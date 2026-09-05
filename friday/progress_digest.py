"""Spoken progress digests, composed from Hermes work-run events.

Owner's ask (2026-09-04): milestones as they happen, plus a digest every
~3 minutes while work is in flight, silent otherwise. `compose()` is pure -
it never touches the bridge, the store or the network - so the room loop,
the UI endpoint and the tests all read the exact same composition logic.

STATICALLY_CONFIRMED shapes it reads (see hermes_bridge.py):
- `HermesSupervisor.progress(work_run_id)` -> {"work_run_id", "status",
  "line", "seq", "tools", "current", "elapsed_s", "result"}.
- `hermes_work_runs` rows (via `WorkRunLog.active()`/`.get()`) carry
  `provider`, `model`, `route_reason`, `result`, `task`, `status`.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

_logger = logging.getLogger("friday.progress")


from friday.hermes_bridge import TERMINAL  # one definition, never two

MAX_DIGEST_RUNS = 3


@dataclass
class Digest:
    milestones: list = field(default_factory=list)
    digest: str | None = None
    next_at: float = 0.0


def _model_reason(run: dict) -> str:
    model = run.get("model") or "an unnamed model"
    reason = run.get("route_reason") or "default route"
    return f"{model} ({reason})"


def _milestone_line(run: dict) -> str:
    summary = (run.get("result") or run.get("line") or
               "finished with no summary").strip().replace("\n", " ")[:200]
    return f"{summary} - on {_model_reason(run)}"


def _digest_line(run: dict) -> str:
    last = (run.get("line") or "starting").strip()
    current = run.get("current") or (
        "first tool" if not run.get("tools") else "wrapping up")
    return (f"did {run.get('tools', 0)} tools, last: {last}, "
            f"on {_model_reason(run)}, next: {current}")


def outcome_line(record: dict, *, now: float | None = None) -> str:
    """One spoken sentence for a finished run, from its durable record only.

    A run closed by the orphan sweep (failure_kind LOST) is said as lost in
    a restart, not as a plain failure: the two call for different actions
    from the owner (re-send it, versus read why it failed). The handoff
    summary is preferred over the raw result; the model and route reason
    close the sentence because "why that model" is half of the question.
    """
    import json as _json
    import time as _time

    status = record.get("status", "")
    lost = record.get("failure_kind") == "LOST"
    if lost:
        head = "was lost in a restart before it finished"
    else:
        head = {"COMPLETE": "finished", "PARTIAL": "stopped part way",
                "FAILED": "failed"}.get(status, status.lower() or "ended")
    task = " ".join((record.get("task") or "").split())[:140] or "a task with no title"
    summary = ""
    if not lost:
        raw = record.get("handoff") or ""
        if raw:
            try:
                summary = str(_json.loads(raw).get("summary") or "").strip()
            except (TypeError, ValueError, AttributeError):
                summary = ""
        summary = summary or " ".join((record.get("result") or "").split())[:200]
    when = ""
    at = record.get("last_event_at") or 0
    if at:
        mins = int(((now if now is not None else _time.time()) - at) // 60)
        if mins < 1:
            when = " just now"
        elif mins < 120:
            when = f" {mins} minutes ago"
        else:
            when = f" {mins // 60} hours ago"
    line = f"Hermes {head}{when}: {task}."
    if summary:
        line += f" Outcome: {summary}" + ("" if summary.endswith(".") else ".")
    return line + f" It ran on {_model_reason(record)}."


def compose(runs, objectives=None, *, now, last_digest_at=0.0,
            cadence=180) -> Digest:
    """
    `runs`: flat dicts, each a work run merged with its live progress (see
    `gather()`). `objectives` is accepted for continuity-snapshot milestones
    later; today it carries nothing events-bearing, so it is ignored.
    # ponytail: objectives unused until continuity emits its own events.
    Dedupes by (work_run_id, seq) inside this one call. Never raises - a
    malformed run is skipped, not a crash.
    """
    seen = set()
    milestones: list[str] = []
    lines: list[str] = []
    for run in (runs or []):
        try:
            wid = run.get("work_run_id")
            if not wid:
                continue
            key = (wid, run.get("seq", 0))
            if key in seen:
                continue
            seen.add(key)
            status = run.get("status", "")
            if status in TERMINAL:
                milestones.append(_milestone_line(run))
            else:
                lines.append(_digest_line(run))
        except Exception:  # noqa: BLE001 - a bad row narrates nothing, ever
            continue

    if len(milestones) > MAX_DIGEST_RUNS:
        # A restart inside the terminal window can re-surface a dozen
        # just-closed runs at once; the room hears three and a count.
        extra = len(milestones) - MAX_DIGEST_RUNS
        milestones = milestones[:MAX_DIGEST_RUNS] + [f"and {extra} more finished"]
    digest = None
    next_at = last_digest_at + cadence
    if lines and now - last_digest_at >= cadence:
        # Newest runs first (active() orders by started_at DESC); the rest
        # are a count. A spoken digest is three breaths, never twenty.
        shown = lines[:MAX_DIGEST_RUNS]
        if len(lines) > MAX_DIGEST_RUNS:
            shown.append(f"and {len(lines) - MAX_DIGEST_RUNS} more in flight")
        digest = "; ".join(shown)
        next_at = now + cadence
    return Digest(milestones=milestones, digest=digest, next_at=next_at)


def gather(sup, *, terminal_window_s: float = 600.0) -> list:
    """Live + just-finished Hermes runs from a `HermesSupervisor`, flattened
    for `compose()`. Not pure - reads the live log and progress ledger -
    kept here so `ui_server` and the room loop share one merge instead of
    two.

    `active()` alone excludes terminal runs, so a completion would never
    reach `compose()`'s milestone branch; `recent()` adds back any run that
    went terminal within `terminal_window_s`, once, so the milestone fires
    and then falls out of the window on its own - no separate "already
    spoken" bookkeeping belongs in this pure-data layer.
    """
    import time as _time

    try:
        # Runs whose owning process died are closed first, so a restart
        # never leaves the digest narrating ghosts (WorkRunLog.sweep_orphans).
        sup.log.sweep_orphans()
    except Exception as exc:  # noqa: BLE001 - narrate the old way, never crash
        _logger.warning("progress: orphan sweep failed: %s", exc)
    try:
        rows = list(sup.log.active())
    except Exception:  # noqa: BLE001
        rows = []
    seen_ids = {r.get("work_run_id") for r in rows}
    try:
        recent = sup.log.recent(limit=12)
    except Exception:  # noqa: BLE001
        recent = []
    now = _time.time()
    for row in recent:
        wid = row.get("work_run_id")
        if wid in seen_ids:
            continue
        if row.get("status") in TERMINAL and \
                now - row.get("last_event_at", 0) <= terminal_window_s:
            rows.append(row)
            seen_ids.add(wid)

    out = []
    for row in rows:
        wid = row.get("work_run_id")
        try:
            prog = sup.progress(wid) if wid else {}
        except Exception:  # noqa: BLE001
            prog = {}
        merged = dict(row)
        merged.update(prog)
        out.append(merged)
    return out
