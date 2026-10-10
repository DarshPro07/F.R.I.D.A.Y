"""
The Opportunities Inbox's detectors (PRO-001).

Proactive work in FRIDAY is event- and schedule-driven. This module is the
piece that notices something worth the owner's attention, and its one
discipline is that noticing costs nothing: every detector is plain code over
rows the automation engine already writes. It never calls a model, never
opens a socket, and never loops on its own - a caller (a scheduled task, a
health tick) invokes `scan`, which reads, compares, and records. An idle
system therefore has no inference loop to be unnecessary:
`tests/test_opportunities.py` runs a simulated week of ticks with every model
entry point and the network booby-trapped.

Deciding what to *do* about an opportunity is a separate, later step and the
only one that may involve a model. Nothing here imports one, and the test
suite fails if that changes.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from friday import contracts as c
from friday import workspace

#: Consecutive failed runs of one automation that count as a pattern. One
#: failure is an event; three is something the automation will not fix alone.
FAILURE_STREAK = 3

#: How long a run may stay `running` before it is called stalled. An
#: automation that is killed mid-run (a reboot, a crash) never writes a
#: finished status, so only the clock can say it is gone.
STALL_AFTER = timedelta(minutes=30)

#: How much history a detector reads per automation.
HISTORY = 50

AUTOMATION_FAILING = "automation_failing"
AUTOMATION_STALLED = "automation_stalled"


def _parse(stamp: str) -> datetime:
    got = datetime.fromisoformat(stamp)
    return got if got.tzinfo else got.replace(tzinfo=timezone.utc)


def _failure_streak(runs: list[dict]) -> list[dict]:
    """The newest unbroken run of failures, newest first. `runs` is newest
    first; a run still in flight neither extends nor breaks the streak."""
    streak: list[dict] = []
    for run in runs:
        if run["status"] == "running":
            continue
        if run["status"] != c.FAILED:
            break
        streak.append(run)
    return streak


def _automation_findings(store, now: datetime) -> list[tuple[str, str, str, dict]]:
    """(kind, subject, evidence_key, detail) per finding."""
    found = []
    for automation in store.automations():
        name = automation["name"]
        runs = store.automation_history(name, limit=HISTORY)
        streak = _failure_streak(runs)
        if len(streak) >= FAILURE_STREAK:
            # Keyed by the streak's oldest run, so more failures of the same
            # streak are the same fact and a dismissal sticks until the
            # automation recovers and then fails again.
            found.append((AUTOMATION_FAILING, name, streak[-1]["run_id"], {
                "failures": len(streak),
                "run_ids": [r["run_id"] for r in streak[:FAILURE_STREAK]],
                "last_error": streak[0].get("error") or "",
            }))
        for run in runs:
            if run["status"] == "running" and now - _parse(run["started_at"]) > STALL_AFTER:
                found.append((AUTOMATION_STALLED, name, run["run_id"], {
                    "run_id": run["run_id"], "started_at": run["started_at"]}))
    return found


def scan(store, *, workspace_id: str | None = None,
         now: datetime | None = None) -> list[dict]:
    """Run every detector once and return the opportunities that are new."""
    owner = workspace_id or workspace.current()
    moment = now or datetime.now(timezone.utc)
    raised = []
    for kind, subject, evidence_key, detail in _automation_findings(store, moment):
        row = store.opportunity_raise(owner, kind, subject, evidence_key, detail)
        if row is not None:
            raised.append(row)
    return raised
