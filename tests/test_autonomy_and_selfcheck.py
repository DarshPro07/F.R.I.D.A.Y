"""
The owner's 2026-09-03 instructions: no "say okay", an autonomous mode that
skips the permission round trips, and a Friday that can run the master
validation prompt on herself. Each test names the failure it guards.
"""
import json

import pytest

from friday import confirmation, policy
from friday import voice_brain as V


@pytest.fixture
def isolated_policy(monkeypatch, tmp_path):
    """A throwaway autonomy file and a fresh engine, so a test never persists
    a mode into the owner's data/ or leaks one into the next test."""
    monkeypatch.setattr(policy, "AUTONOMY_FILE", tmp_path / "autonomy.json")
    engine = policy.PolicyEngine(autonomy=policy.FULL)
    monkeypatch.setattr(policy, "default_engine", engine)
    confirmation.reset()
    yield engine
    confirmation.reset()


def test_dangerous_answers_confirm_but_never_the_non_approvable():
    full = policy.resolve_policy(policy.FULL)
    danger = policy.resolve_policy(policy.DANGEROUS)
    assert full[policy.DESKTOP_CONTROL] == policy.CONFIRM
    assert danger[policy.DESKTOP_CONTROL] == policy.AUTO
    for category in policy.NON_APPROVABLE:
        assert danger[category] == full[category] != policy.AUTO
    assert not any(v == policy.CONFIRM for v in danger.values())


def test_set_autonomy_persists_and_switches_the_live_engine(isolated_policy):
    assert not policy.skip_permissions()
    assert policy.set_autonomy("dangerous") == policy.DANGEROUS
    assert policy.skip_permissions()
    assert json.loads(policy.AUTONOMY_FILE.read_text())["mode"] == "dangerous"
    assert policy.current_autonomy() == policy.DANGEROUS
    assert isolated_policy.decide("desktop.step").allowed
    policy.set_autonomy("full")
    assert isolated_policy.decide("desktop.step").needs_confirmation
    with pytest.raises(ValueError):
        policy.set_autonomy("yolo")


def test_spoken_switch_turns_full_autonomy_on_and_off(isolated_policy):
    out = V._try_command("full autonomy on")
    assert out["action"] == "autonomy" and out["mode"] == policy.DANGEROUS
    assert policy.skip_permissions()
    out = V._try_command("skip permissions off")
    assert out["mode"] == policy.FULL and not policy.skip_permissions()
    assert V._try_command("Full autonomy") ["mode"] == policy.DANGEROUS   # bare = on
    assert V._try_command("get it full autonomy, like Jarvis")["mode"] == policy.DANGEROUS
    assert V._try_command("go back to asking, full autonomy off")["mode"] == policy.FULL
    assert V._try_command("full autonomy guarded")["mode"] == policy.GUARDED
    assert V._try_command("are you in full autonomy right now?") is None   # a question, not a switch
    assert V._try_command("What does full autonomy mean") is None
    assert V._try_command("this pasted page says full autonomy is a mode of the policy "
                          "engine and goes on for many more words than a spoken switch would") is None


def test_dangerous_autonomy_is_the_default(monkeypatch, tmp_path):
    """Owner, 2026-09-03 18:00: 'get it full autonomy' - no switch phrase needed."""
    monkeypatch.setattr(policy, "AUTONOMY_FILE", tmp_path / "missing.json")
    monkeypatch.delenv("ADA_AUTONOMY", raising=False)
    assert policy.current_autonomy() == policy.DANGEROUS
    monkeypatch.setenv("ADA_AUTONOMY", "guarded")
    assert policy.current_autonomy() == policy.GUARDED       # the env still steps back
    assert policy.PolicyEngine(autonomy=policy.DANGEROUS).decide("desktop.step").allowed
    assert not policy.PolicyEngine(autonomy=policy.DANGEROUS).decide("secrets.read").allowed \
        if policy.PolicyEngine().category_of("secrets.read") else True


def test_a_pasted_page_mentioning_status_is_not_a_status_question(monkeypatch):
    calls = []
    import friday.ui_server as u
    monkeypatch.setattr(u, "build_state", lambda: calls.append(1) or {
        "connections": {"gbrain": {"status": "x"}, "hermes": {"status": "y"}},
        "mcp": {"total": 1}, "system": {"ram_percent": 1}})
    long = ("appendix a known gaps not to be fixed by masking hermes status stays "
            "disabled in the friday profile documented wedge and the lock template status")
    assert V._try_command(long) is None and calls == []
    assert V._try_command("status")["action"] == "status" and calls == [1]


def test_the_spoken_okay_approves_the_pending_plan(monkeypatch, isolated_policy):
    """Before 2026-09-03 nothing approved the nonce, so 'okay' met
    'that has not been approved yet'."""
    from friday.toolsets import desktop as D
    pend = confirmation.book.ask("RUN-1", D.TAKEOVER, "open notepad", "q?")
    V._LAST_PLAN_NONCE.update(nonce=pend.nonce, task="open notepad")
    seen = {}

    def fake_step(run, nonce, **_kw):
        seen["state"] = confirmation.book.pending[nonce].state
        from friday import contracts as c
        started = c.started(run.run_id, "desktop.step")
        return run.record(c.succeeded(started, output={"result": "finished"},
                                      verification=c.Verification(method="fake", evidence="test")))

    monkeypatch.setattr(D, "desktop_step", fake_step)
    monkeypatch.setattr(V, "_remember_turn", lambda role, text: None)
    monkeypatch.setattr(V, "_try_command", lambda text: None)
    out = V.reply("okay")
    assert seen["state"] == confirmation.APPROVED
    assert out["action"] == "desktop.step" and out["reply"] == "Done, sir."


def test_dangerous_takeover_runs_the_plan_without_a_yes(monkeypatch, isolated_policy):
    from friday import contracts as c
    from friday.toolsets import desktop as D
    policy.set_autonomy("dangerous")
    steps = [{"action": "click", "target": "Start", "say": "Opening the start menu"},
             {"action": "type", "text": "notepad", "say": "Typing notepad"}]

    def fake_plan(run, task, *, monitor=1, engine=policy.default_engine):
        started = c.started(run.run_id, "desktop.plan")
        pend = confirmation.book.ask(run.run_id, D.TAKEOVER, task, "q?")
        if engine.autonomy == policy.DANGEROUS:
            confirmation.book.approve(pend.nonce)
        return run.record(started.finish(status=c.OBSERVED, output={
            "result": "planned", "steps": steps, "confirm": pend.to_dict(),
            "autorun": engine.autonomy == policy.DANGEROUS}))

    done = []

    def fake_step(run, nonce, *, monitor=None, engine=policy.default_engine):
        started = c.started(run.run_id, "desktop.step")
        if nonce:
            spent = confirmation.book.consume(nonce, run_id=run.run_id, action=D.TAKEOVER,
                                              target="open notepad")
            assert spent.ok, spent.reason
        if len(done) == len(steps):
            return run.record(c.succeeded(
                started, output={"result": "finished", "steps_done": len(steps)},
                verification=c.Verification(method="fake", evidence="test")))
        done.append(steps[len(done)])
        return run.record(c.succeeded(started, output={"result": "acted"},
                                      verification=c.Verification(method="fake", evidence="test")))

    monkeypatch.setattr(D, "desktop_plan", fake_plan)
    monkeypatch.setattr(D, "desktop_step", fake_step)
    run = c.Run.create("take over: open notepad", capability="desktop")
    res = D.desktop_takeover(run, "open notepad", engine=policy.default_engine)
    assert res.output["result"] == "finished" and len(done) == 2

    policy.set_autonomy("full")
    run = c.Run.create("take over: open notepad", capability="desktop")
    res = D.desktop_takeover(run, "open notepad", engine=policy.default_engine)
    assert res.output["result"] == "planned" and not res.output["autorun"]
    assert len(done) == 2                                   # nothing ran


def test_forbidden_categories_survive_dangerous_mode(isolated_policy):
    from friday.toolsets import desktop as D
    policy.set_autonomy("dangerous")
    bad = D.forbidden({"target": "type my password into this box", "text": "", "say": "",
                       "action": "plan"})
    assert bad.refused
    assert policy.default_engine.decide("secrets.read").denied \
        if policy.default_engine.category_of("secrets.read") else True


def test_selfcheck_runs_the_automatable_half(monkeypatch, tmp_path):
    from friday import selfcheck
    monkeypatch.setattr(selfcheck, "LIVE", False)     # no real Hermes job / audio / screen in CI
    rep = selfcheck.run()
    ids = {i["id"] for i in rep["items"]}
    assert {"2.1", "5.1-5.3", "A5", "7.5", "7.7", "4.1", "4.2", "13.4", "12.2", "10.1"} <= ids
    core = {i["id"]: i for i in rep["items"]
            if i["id"] in ("2.1", "A5", "4.1", "4.2", "13.4", "3.4", "12.2", "10.1", "0.9")}
    assert all(i["ok"] is True for i in core.values()), core
    assert rep["passed"] + len(rep["failed"]) + rep["skipped"] == rep["total"]
    assert rep["skipped"] == 3                        # the three side-effect items, honestly skipped
    assert "Self-check:" in rep["spoken"] and "needs your voice" in rep["spoken"]
    assert "phases need you" not in rep["spoken"]     # the list the owner rejected


def test_selfcheck_hands_hermes_a_real_job_when_live(monkeypatch):
    from friday import selfcheck
    from friday import voice_brain as V
    monkeypatch.setattr(selfcheck, "LIVE", True)
    seen = {}
    monkeypatch.setattr(V, "_run_hermes", lambda op, args: seen.update(goal=args["goal"]) or {
        "result": json.dumps({"status": "working", "work_run_id": "WR-1", "tier": "economy",
                              "effort": "low"})})
    ok, detail = selfcheck._hermes_real_job()
    assert ok and "WR-1" in detail and "cheapest model" in seen["goal"]
    monkeypatch.setattr(V, "_run_hermes", lambda op, args: {"error": "Hermes is not reachable: down"})
    ok, detail = selfcheck._hermes_real_job()
    assert ok is False and "not reachable" in detail


def test_go_according_to_the_prompt_runs_the_selfcheck(monkeypatch):
    from friday import selfcheck
    monkeypatch.setattr(selfcheck, "run", lambda only="": {
        "spoken": "Self-check: 1 of 1 passed.", "passed": 1, "total": 1, "items": [],
        "failed": [], "needs_you": []})
    out = V._try_command("Go according to the verification prompt. Okay.")
    assert out["action"] == "selfcheck" and out["reply"].startswith("Self-check")
    assert V._try_command("Friday, check yourself")["action"] == "selfcheck"
    assert V._try_command("what is the status of the prompt")["action"] != "selfcheck" \
        if V._try_command("what is the status of the prompt") else True


def test_full_autonomy_lets_a_spoken_write_reach_the_fabric(monkeypatch, isolated_policy):
    """Before 2026-09-03 every mutating op was refused from a spoken turn whatever
    the mode. In dangerous mode the write goes through (the fabric's own gates
    still apply); a restricted provider's ops never do."""
    from types import SimpleNamespace
    from friday import fabric

    social = SimpleNamespace(family="social", risk="low", operations=("queue", "schedule"),
                             open_operations=("queue",))
    hacker = SimpleNamespace(family="security", risk="restricted",
                             operations=("skill", "exploit"), open_operations=())
    monkeypatch.setattr(fabric, "registry", lambda: {"postiz": social, "strix": hacker})
    calls = []
    monkeypatch.setattr(fabric, "call_with_fallback",
                        lambda fam, op, **kw: calls.append((fam, op, kw)) or SimpleNamespace(
                            status="succeeded", output={"ok": True}, error=""))

    monkeypatch.setitem(V._CURRENT_TURN, "text", "schedule a post saying hi for tomorrow")
    out = V._run_capability("social", "schedule", {"text": "hi", "when": "tomorrow"})
    assert "go-ahead" in out["error"] and calls == []
    policy.set_autonomy("dangerous")
    out = V._run_capability("social", "schedule", {"text": "hi", "when": "tomorrow"})
    assert "result" in out and calls[-1][:2] == ("social", "schedule")
    out = V._run_capability("security", "exploit", {"target": "x"})
    assert "error" in out and len(calls) == 1          # restricted: still refused
    # Injection guard: the owner asked to READ; a page the model read said "post".
    monkeypatch.setitem(V._CURRENT_TURN, "text", "what is queued on our social accounts?")
    out = V._run_capability("social", "schedule", {"text": "buy now", "when": "now"})
    assert "did not ask" in out["error"] and len(calls) == 1
    assert V._asked_for("run_robot", "run the pricing robot") and not V._asked_for("run_robot", "list robots")


def test_capability_errors_carry_the_providers_health_hint(monkeypatch):
    """"No provider available" must come with the adapter's own line - the
    one that names the env var to set (live pass, 2026-09-03)."""
    from friday import fabric
    monkeypatch.setattr(fabric, "report", lambda: [
        {"family": "social", "provider": "postiz_social"},
        {"family": "media", "provider": "openmontage_media"}])
    monkeypatch.setattr(fabric, "health", lambda pid: {
        "postiz_social": {"status": "UNAVAILABLE",
                          "detail": "unreachable at 127.0.0.1:3000, set POSTIZ_API_URL"},
        "openmontage_media": {"status": "UNAVAILABLE", "detail": "irrelevant"}}[pid])
    out = V._with_health_hint("social", "no provider available for social queue")
    assert "set POSTIZ_API_URL" in out and "irrelevant" not in out
    assert V._with_health_hint("nothing", "x") == "x"


def test_spoken_file_ops_never_leave_the_workspace(monkeypatch, tmp_path):
    """Security review, 2026-09-03: an absolute or escaping path from the
    spoken files family must be refused, on reads as well as writes."""
    import shutil
    import uuid
    from friday import config
    # The files toolset's own jail allows E:\ wholesale (which is the point of
    # this test), so the workspace stand-in must live inside it, not in %TEMP%.
    work = config.ARTIFACTS_DIR.parent / ("_pytest_ws_" + uuid.uuid4().hex[:8])
    monkeypatch.setattr(config, "ARTIFACTS_DIR", work)
    try:
        out = V._run_files("read", {"path": "C:/Windows/win.ini"})
        assert "only reaches my own workspace" in out["error"]
        out = V._run_files("read", {"path": str(config.ARTIFACTS_DIR.parent.parent / ".env")})
        assert "only reaches my own workspace" in out["error"]           # E:\ is jail-allowed, not workspace
        out = V._run_files("write", {"path": "../escape.txt", "content": "x"})
        assert "only reaches my own workspace" in out["error"]
        assert not (work.parent / "escape.txt").exists()
        out = V._run_files("write", {"path": "note.txt", "content": "food = 'pizza'"})
        assert "result" in out and (work / "note.txt").exists(), out
        out = V._run_files("list", {})
        assert "note.txt" in out.get("result", "")
    finally:
        shutil.rmtree(work, ignore_errors=True)


def test_web_extract_parses_the_gated_fetch_with_scrapling(monkeypatch):
    """Owner, 2026-09-03: use Scrapling for details because it is fast and
    exact. The fetch stays the gated one (netguard etc.); Scrapling only parses."""
    pytest.importorskip("scrapling")
    import json
    from friday import contracts as c
    from friday.toolsets import web as W

    html = ("<html><head><title>Models</title></head><body><h1>Nova-2</h1><h2>Languages</h2>"
            "<table><tr><td>English</td></tr><tr><td>Hindi</td></tr></table>"
            "<ul><li>agent one</li><li>plain item</li><li>agent two</li></ul></body></html>")

    async def fake_fetch(run, url, *, include_html=False, **_kw):
        started = c.started(run.run_id, "web.fetch")
        return run.record(c.succeeded(started, output={
            "url": url, "final_url": url, "title": "Models", "text": "x", "html": html},
            verification=c.Verification(method="fake", evidence="test")))

    monkeypatch.setattr(W, "web_fetch", fake_fetch)
    out = json.loads(V._run_web("extract", {"url": "https://docs.example/models",
                                           "fields": {"models": "h1", "langs": "table tr"}})["result"])
    assert out["how"] == "fields" and out["extracted"]["models"] == ["Nova-2"]
    assert out["extracted"]["langs"] == ["English", "Hindi"]
    out = json.loads(V._run_web("extract", {"url": "https://docs.example/models", "text": "agent"})["result"])
    assert out["how"] == "by_text" and len(out["extracted"]) == 2
    out = json.loads(V._run_web("extract", {"url": "https://docs.example/models"})["result"])
    assert out["extracted"]["headings"] == ["Nova-2", "Languages"] and out["extracted"]["title"] == ["Models"]
    # The gate is the real one: a metadata address never reaches the parser.
    monkeypatch.undo()
    out = V._run_web("extract", {"url": "http://169.254.169.254/latest/meta-data/"})
    assert "error" in out and "169.254" in out["error"] or "refused" in out.get("error", "")


def test_the_takeover_offer_lapses_after_one_unrelated_turn(monkeypatch, isolated_policy):
    """A stashed plan nonce must not be spendable by a bare 'ok' several turns
    later (review, 2026-09-03): only the very next turn is the yes."""
    monkeypatch.setattr(V, "_remember_turn", lambda role, text: None)
    monkeypatch.setattr(V, "_try_command", lambda text: None)
    monkeypatch.setattr(V, "_model", lambda: None)          # offline brain path
    V._LAST_PLAN_NONCE.update(nonce="abc", task="open notepad")
    out = V.reply("what time is it")
    assert out.get("degraded") and V._LAST_PLAN_NONCE["nonce"] == ""
    out = V.reply("ok")
    assert out.get("action") != "desktop.step"
