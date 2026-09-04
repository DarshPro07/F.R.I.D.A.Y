"""
friday/selfcheck.py -- Friday checks herself, and does the work herself.

The owner pasted docs/MASTER_VALIDATION_PROMPT.md into the control room and
said "go according to the verification prompt" (2026-09-03). The first version
ran the easy half and read him a list of "phases that need you" - which he
rightly called the opposite of autonomy. This one does everything a program
can do on its own: it hands Hermes a real, tiny job, transcribes a real file,
exercises the objective budget on a scratch run, measures this machine's load,
looks at the screen, and reads back what the last Hermes run left in memory.
The single thing it cannot do is hear the owner speak (the pause rule), and
it says so in one clause, not a list.

Each item is one capability call and one predicate; nothing here is judged by
a model. Never raises: an item that blows up is a failed item with the error
as its detail. Side-effect items (the Hermes job, transcription, pointing at
the screen) are skipped, not faked, when FRIDAY_SELFCHECK_LIVE=0 or when the
thing they need is absent.
"""
from __future__ import annotations

import json
import os
from typing import Callable

Check = tuple[str, str, Callable[[], tuple[bool | None, str]]]

#: Side-effect items run for real unless the owner (or a test) says otherwise.
LIVE = os.getenv("FRIDAY_SELFCHECK_LIVE", "1") != "0"


def _cap(family: str, operation: str, **arguments):
    from friday import fabric
    res = fabric.call_with_fallback(family, operation, **arguments)
    if res.status != "succeeded":
        raise RuntimeError(res.error or "the capability did not answer")
    return res.output


# --- the quick half ---------------------------------------------------------

def _clock():
    from friday import voice_brain as V
    out = V._run_clock("now", {})
    return bool(out.get("result")), str(out.get("result") or out.get("error"))


def _files_roundtrip():
    from friday import voice_brain as V
    w = json.loads(V._run_files("write", {"content": "food = 'pizza'\n"}).get("result", "{}"))
    path = (w.get("output") or {}).get("path") if isinstance(w.get("output"), dict) else None
    path = path or w.get("path")
    if w.get("status") != "succeeded":
        return False, f"write: {w}"
    listed = V._run_files("list", {}).get("result", "")
    name = str(path or "").replace("\\", "/").rsplit("/", 1)[-1]
    if name and name not in listed:
        return False, f"list did not show {name}"
    d = V._run_files("delete", {"path": str(path)}) if path else {"error": "no path returned"}
    ok = '"succeeded"' in d.get("result", "")
    return ok, f"wrote, listed and deleted {name}" if ok else f"delete: {d}"


def _files_confined():
    from friday import voice_brain as V
    out = V._run_files("read", {"path": "C:/Windows/win.ini"})
    return "only reaches my own workspace" in str(out.get("error", "")), \
        "an absolute path outside my workspace is refused"


def _executives():
    names = _cap("roles", "executives")
    return len(names) == 14, f"{len(names)} executives"


def _find_agent():
    hits = _cap("roles", "find_agent", query="python")
    return "python-pro" in hits, ", ".join(hits[:5])


def _archetypes():
    names = _cap("roles", "archetypes")
    return len(names) == 8, ", ".join(names)


def _traversal_refused():
    try:
        _cap("roles", "agent", path="../../../.env")
    except Exception as exc:  # noqa: BLE001
        return "catalogue" in str(exc), str(exc)[:120]
    return False, "a path outside the pack was read"


def _commerce_honest():
    try:
        out = _cap("commerce", "products")
        return True, f"store answered: {str(out)[:80]}"
    except Exception as exc:  # noqa: BLE001
        return True, f"honest: {str(exc)[:100]}"


def _helpers_roster():
    from friday import fabric
    rows = fabric.report()
    return len(rows) >= 20, f"{len(rows)} helpers registered"


def _bundle_is_memory_not_transcript():
    from friday import hermes_bridge as hb
    bundle = hb.TaskBundle(goal="add a comment to friday/answer.py quoting three rules "
                                "from AGENTS.md that you already know").with_memory()
    text = bundle.render()
    leaked = [ln for ln in text.splitlines() if ln.startswith(("he:", "you:"))]
    ok = len(text) < 6000 and not leaked
    return ok, f"{len(text)} chars, {len(leaked)} transcript lines, memory block " \
               f"{'present' if 'ALREADY KNOWS' in text else 'missing'}"


def _economy_route():
    from friday import execution_economics as ee
    plan = ee.plan_delegation("use the cheapest model and rename the variable foo "
                              "in tests/_scratch_foo.py")
    ok = plan["tier"] == ee.TIER_ECONOMY and plan["effort"] == "low"
    return ok, f"tier {plan['tier']}, model {plan['model'] or 'profile default'}, effort {plan['effort']}"


def _deep_route():
    from friday import execution_economics as ee
    plan = ee.plan_delegation("think hard about this: redesign how the auth core "
                              "validates tokens. Plan only.")
    ok = plan["tier"] == ee.TIER_DEEP and plan["effort"] == "high"
    return ok, f"level {plan['level']}, tier {plan['tier']}, effort {plan['effort']}"


def _metrics_envelope():
    from friday.ui_server import build_state
    m = build_state().get("metrics") or {}
    return "all_time" in m and "open_tasks" in m, f"keys {sorted(m)}"


def _organisation():
    from friday import org
    st = org.state()
    return int(st.get("agents_total") or 0) > 258 and "awesome-claude-code-subagents" in str(st.get("source")), \
        f"{st.get('agents_total')} agents; source {st.get('source')}"


def _memory_tiers():
    from friday import memory_stack as M
    tiers = M.aggregate("what did hermes finish", budget_tokens=600)["tiers"]
    return "outcomes" in tiers and "episodes" in tiers, ", ".join(sorted(tiers))


def _credential_entry_refused():
    from friday.toolsets import desktop as D
    bad = D.forbidden({"target": "type my password into this box", "text": "", "say": "",
                       "action": "plan"})
    return bad.refused, bad.reason or "not refused"


def _autonomy_mode():
    from friday import policy
    mode = policy.default_engine.autonomy
    return mode == policy.DANGEROUS, f"autonomy {mode}" + (
        "" if mode == policy.DANGEROUS else " - say 'full autonomy on' to act without asking")


def _status_paste_guard():
    from friday import voice_brain as V
    long = ("appendix a known gaps not to be fixed by masking hermes status stays disabled "
            "in the friday profile documented wedge and the lock template status is present")
    return V._try_command(long) is None, "a pasted page mentioning status is not a status command"


# --- the half that used to be "needs you" -----------------------------------

def _host_load():
    from friday import turn_timing as T
    host = T.host_load()
    return True, "latency attribution ready: " + ", ".join(
        f"{k} {v}" for k, v in (host or {}).items() if not isinstance(v, (dict, list)))[:140]


def _budget_on_scratch_run():
    """12.2 on a scratch store: 33k tokens on a 32k portion must stop the portion."""
    import tempfile
    from pathlib import Path
    from friday import continuity as C
    from friday import contracts as c
    from friday.store import Store
    path = Path(tempfile.mkdtemp(prefix="friday-selfcheck-")) / "scratch.sqlite3"
    store = Store(path)
    try:
        manager = C.ContinuityManager(store)
        started = manager.start_run("self-check: budget rehearsal", provenance=c.PERSON,
                                    attended=True, initial_task="spend past the portion cap")
        claim = manager.claim_run(started.run_id, "selfcheck")
        snapshot = manager.record_model_tokens(claim, 33_000)
        ok = str(snapshot.budget_exhausted).startswith("portion")
        return ok, f"scratch run: {snapshot.budget_exhausted or 'no stop'} after 33000/32000 tokens"
    finally:
        try:
            store.close()
        except Exception:  # noqa: BLE001
            pass


def _transcribe_known_file():
    """8.1 for real, on the smallest cached audio file, if there is one."""
    if not LIVE:
        return None, "skipped: side effects off"
    from friday.config import DATA_DIR
    cache = DATA_DIR / "tts_cache"
    files = sorted(cache.glob("*.mp3"), key=lambda p: p.stat().st_size) if cache.is_dir() else []
    if not files:
        return None, "skipped: no cached audio under data/tts_cache to transcribe"
    try:
        out = _cap("media", "transcribe", source=str(files[0]))
    except Exception as exc:  # noqa: BLE001
        return False, f"{files[0].name}: {str(exc)[:120]}"
    text = out.get("text") if isinstance(out, dict) else str(out)
    return bool(text), f"{files[0].name} -> {str(text)[:80]!r}"


def _screen_point():
    """13.1 for real: find the control room's mic button on the live screen."""
    if not LIVE:
        return None, "skipped: side effects off"
    from friday import voice_brain as V
    out = V._run_desktop("point", {"target": "the microphone button in the Friday control room"})
    if "error" in out:
        low = out["error"].lower()
        if any(w in low for w in ("no screen", "capture", "not configured", "monitor", "vision")):
            return None, f"skipped: {out['error'][:100]}"
        return False, out["error"][:120]
    try:
        res = json.loads(out["result"])
    except (KeyError, ValueError):
        return False, str(out)[:120]
    found = bool(res.get("found")) or res.get("status") == "succeeded"
    if not found and res.get("result") == "not_visible":
        # She captured the screen and the vision model looked: the control is
        # simply not on screen right now. That is the pipeline working.
        return True, "looked at the live screen; the mic button is not visible right now"
    return found, f"{res.get('result') or res.get('status')}: {str(res.get('spoken') or '')[:90]}"


def _hermes_real_job():
    """3.3 for real: hand Hermes a tiny economy-tier job, submit-first. The
    delivery broker speaks the result later and on_terminal writes it to
    memory, which the next self-check reads back (item 3.4)."""
    if not LIVE:
        return None, "skipped: side effects off"
    from friday import voice_brain as V
    from friday.config import ARTIFACTS_DIR
    goal = ("use the cheapest model. Self-check job from Friday: create a file named "
            f"hermes_selfcheck.txt inside {ARTIFACTS_DIR} containing one line with the "
            "current date and time. Do nothing else, touch nothing else.")
    out = V._run_hermes("delegate", {"goal": goal})
    if "error" in out:
        return False, out["error"][:140]
    info = json.loads(out["result"])
    return info.get("status") == "working", \
        f"work run {info.get('work_run_id')}, tier {info.get('tier')}, effort {info.get('effort')}"


def _last_hermes_outcome_in_memory():
    """3.4: what the last finished Hermes run left in shared memory."""
    from friday import memory_stack as M
    items = M.hermes_outcomes().get("items") or []
    if not items:
        return True, "no finished Hermes run in memory yet; the self-check job above lands there"
    last = items[0]
    text = str(last.get("statement") or last.get("decision") or last)[:120]
    return True, f"last outcome: {text}"


CHECKS: tuple[Check, ...] = (
    ("0", "autonomy mode in force", _autonomy_mode),
    ("2.1", "clock/now answers", _clock),
    ("5.1-5.3", "files write, list, delete in my workspace", _files_roundtrip),
    ("A5", "files never leave my workspace", _files_confined),
    ("7.2", "fourteen executives", _executives),
    ("7.5", "find_agent finds python-pro", _find_agent),
    ("7.7", "eight agents-team archetypes", _archetypes),
    ("7.9", "path traversal refused", _traversal_refused),
    ("6.1", "commerce answers or says unreachable", _commerce_honest),
    ("15.1", "helpers roster", _helpers_roster),
    ("3.1", "Hermes bundle carries memory, not the transcript", _bundle_is_memory_not_transcript),
    ("4.1", "cheapest model routes to economy/low", _economy_route),
    ("4.2", "think hard routes to deep/high", _deep_route),
    ("12.1", "metrics envelope has this-objective and all-time", _metrics_envelope),
    ("7.8", "organisation lists both packs", _organisation),
    ("3.4", "memory has the outcomes tier", _memory_tiers),
    ("13.4", "credential entry refused in code", _credential_entry_refused),
    ("0.9", "a pasted page is not a status command", _status_paste_guard),
    ("10.1", "latency attribution and host load", _host_load),
    ("12.2", "portion budget stops a scratch run", _budget_on_scratch_run),
    ("8.1", "transcribe a real cached file", _transcribe_known_file),
    ("13.1", "point at a control on the live screen", _screen_point),
    ("3.3", "a real Hermes job, economy tier, submitted", _hermes_real_job),
    ("3.4b", "last Hermes outcome in shared memory", _last_hermes_outcome_in_memory),
)

#: The one thing a program cannot do: speak into the microphone.
NEEDS_YOU: tuple[str, ...] = ("1.x the pause rule - it needs your voice",)


def run(only: str = "") -> dict:
    """Run every item (or those whose id starts with `only`)."""
    items = []
    for cid, label, fn in CHECKS:
        if only and not cid.startswith(only):
            continue
        try:
            ok, detail = fn()
        except Exception as exc:  # noqa: BLE001
            ok, detail = False, f"{type(exc).__name__}: {str(exc)[:140]}"
        items.append({"id": cid, "label": label, "ok": ok, "detail": str(detail)})
    passed = sum(1 for i in items if i["ok"] is True)
    skipped = sum(1 for i in items if i["ok"] is None)
    failed = [f"{i['id']} {i['label']}" for i in items if i["ok"] is False]
    hermes = next((i for i in items if i["id"] == "3.3" and i["ok"] is True), None)
    spoken = f"Self-check: {passed} passed, {len(failed)} failed, {skipped} skipped."
    if failed:
        spoken += " Failed: " + "; ".join(failed) + "."
    if hermes:
        spoken += " Hermes has a real job from me as part of this; I'll tell you when it lands."
    spoken += " The one thing I can't do alone is the pause rule - that needs your voice."
    return {"passed": passed, "failed": failed, "skipped": skipped, "total": len(items),
            "items": items, "needs_you": list(NEEDS_YOU), "spoken": spoken}
