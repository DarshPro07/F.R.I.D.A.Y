"""ACC-001 slice 1: the worker-lifecycle adapter behind the command center.

Every agent's Queued / Running / Verified / Blocked / Failed / Cancelled state
is derived here, and only from durable worker records: the AGENT-001 ledger
(injected, duck-typed), the Hermes WorkRun log, run_tasks, the governor queue
and pending approval gates. A model or objective that merely *claims* work has
no row, so it never appears as running.

Pure functions, no I/O of their own: the caller passes already-read rows. That
is also why two derivations from one sqlite file are equal - there is no hidden
in-memory state to lose in a restart.

Ledger rows are AgentLedger.view() rows (agent_ledger.py: its own status
vocabulary, role_id/model_route columns, ISO started_at, evidence_refs from the
finishing event). A status this module does not know is Blocked with the status
named, never Running.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Callable, Iterable

from friday import agent_ledger as al
from friday import hermes_bridge as hb

QUEUED = "queued"
RUNNING = "running"
VERIFIED = "verified"
BLOCKED = "blocked"
FAILED = "failed"
CANCELLED = "cancelled"

STATES = (QUEUED, RUNNING, VERIFIED, BLOCKED, FAILED, CANCELLED)

#: The AG-UI event types this module emits.
AGUI_TYPES = ("RUN_STARTED", "RUN_FINISHED", "RUN_ERROR", "STEP_STARTED",
              "STEP_FINISHED", "STATE_SNAPSHOT", "STATE_DELTA")

_WORKRUN_STATE = {
    hb.WORKING: RUNNING, hb.STEERED: RUNNING,
    hb.STARTING: QUEUED, hb.CONNECTED: QUEUED, hb.SESSION_READY: QUEUED,
    hb.WAIT_FRIDAY: BLOCKED, hb.WAIT_USER: BLOCKED, hb.PARTIAL: BLOCKED,
    hb.DISCONNECTED: BLOCKED,
    hb.CANCELLING: CANCELLED, hb.FAILED: FAILED, hb.COMPLETE: VERIFIED,
}

#: agent_runs.status. STARTING is a claim, not a worker; INTERRUPTED (no live
#: process behind a RUNNING row) is handled in _from_ledger as blocked.
_LEDGER_STATE = {
    al.STARTING: QUEUED, al.RUNNING: RUNNING, al.INTERRUPTED: BLOCKED,
    al.SUCCEEDED: VERIFIED, al.FAILED: FAILED, al.CANCELLED: CANCELLED,
}

#: run_tasks.status as written by friday/continuity.py.
_TASK_STATE = {"runnable": QUEUED, "running": RUNNING, "succeeded": VERIFIED,
               "unknown": BLOCKED}

_UNVERIFIED = "completion unverified"


@dataclass(frozen=True)
class AgentState:
    run_id: str
    parent_run_id: str
    role: str
    model: str
    state: str
    reason: str
    position: int | None


@dataclass(frozen=True)
class Frame:
    """One AG-UI event, before it is wrapped in ui_server._event_frame."""
    type: str
    run_id: str
    payload: dict


def _get(row, name, default=""):
    value = row.get(name, default) if isinstance(row, dict) else \
        getattr(row, name, default)
    return default if value is None else value


def _has_evidence(refs) -> bool:
    if isinstance(refs, str):
        try:
            refs = json.loads(refs or "[]")
        except ValueError:
            return False
    return bool(refs)


def _settle(state, reason, refs):
    """COMPLETE/succeeded is verified only with evidence refs."""
    if state == VERIFIED and not _has_evidence(refs):
        return BLOCKED, _UNVERIFIED
    return state, reason


def _epoch(value) -> float:
    """Seconds since the epoch from a number (WorkRun) or ISO text (ledger)."""
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(
            str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _from_ledger(status, refs):
    state = _LEDGER_STATE.get(status)
    if state is None:
        return BLOCKED, "unknown status %r" % (status,)
    if status == al.INTERRUPTED:
        return BLOCKED, "interrupted: no live worker process"
    return _settle(state, "", refs)


def _from_workrun(status, question, refs):
    state = _WORKRUN_STATE.get(status)
    if state is None:
        return BLOCKED, "unknown status %r" % (status,)
    if state == BLOCKED:
        reason = question or ("worker disconnected"
                              if status == hb.DISCONNECTED else status)
        return BLOCKED, reason
    return _settle(state, "", refs)


def derive(ledger: Iterable = (), runs: Iterable = (), tasks: Iterable = (),
           queue: Iterable = (), gates: Iterable = ()) -> list[AgentState]:
    """The state of every agent that has a durable record, ordered by run_id.

    A run_id seen in more than one source keeps the first (ledger, then runs,
    then tasks). Queued entries are numbered 1.. by when they began waiting.
    """
    found: dict[str, tuple] = {}   # run_id -> (parent, role, model, state, reason, at)

    for row in ledger:
        state, reason = _from_ledger(_get(row, "status"),
                                     _get(row, "evidence_refs", []))
        found.setdefault(_get(row, "run_id"), (
            _get(row, "parent_run_id"), _get(row, "role_id"),
            _get(row, "model_route"), state, reason,
            _epoch(_get(row, "started_at", 0))))

    for row in runs:
        state, reason = _from_workrun(_get(row, "status"),
                                      _get(row, "pending_question"),
                                      _get(row, "evidence_refs", []))
        found.setdefault(_get(row, "work_run_id"), (
            _get(row, "friday_run_id"), "hermes", _get(row, "model"),
            state, reason, _epoch(_get(row, "started_at", 0))))

    for row in tasks:
        status = _get(row, "status")
        state = _TASK_STATE.get(status)
        if state is None:
            state, reason = BLOCKED, "task status %r" % (status,)
        else:
            state, reason = _settle(state, "", _get(row, "evidence_refs", []))
        found.setdefault(_get(row, "task_id"), (
            _get(row, "run_id"), "task", "", state, reason, 0.0))

    for kind, label, at in queue:
        # governor entries have no run id of their own; the name is positional
        found.setdefault("queue:%s:%s:%s" % (kind, at, label), (
            "", kind, "", QUEUED, label, float(at)))

    for gate in gates:
        found.setdefault("gate:%s" % _get(gate, "nonce"), (
            "", "approval", "", BLOCKED,
            _get(gate, "question") or _get(gate, "action"), 0.0))

    waiting = sorted((at, rid) for rid, (_, _, _, st, _, at) in found.items()
                     if st == QUEUED)
    position = {rid: n for n, (_, rid) in enumerate(waiting, 1)}
    return [AgentState(rid, parent, role, model, state, reason,
                       position.get(rid))
            for rid, (parent, role, model, state, reason, _)
            in sorted(found.items())]


def snapshot(sources: dict[str, Callable[[], Iterable]]) -> dict:
    """Read each named source (ledger, runs, tasks, queue, gates) and derive.

    A source that raises contributes nothing and is named in ``reason``; it
    never propagates, so a down database gives an empty list and a reason.
    """
    got: dict[str, list] = {}
    down: list[str] = []
    for name, read in sources.items():
        try:
            got[name] = list(read())
        except Exception as exc:  # noqa: BLE001 - reported, not swallowed
            down.append("%s: %s" % (name, exc))
    agents = derive(**got)
    return {"agents": [asdict(a) for a in agents], "reason": "; ".join(down)}


def _delta_op(op, a: AgentState):
    return {"op": op, "path": "/agents/%s" % a.run_id, "value": asdict(a)}


def to_agui(prev: list[AgentState] | None, now: list[AgentState]) -> list[Frame]:
    """AG-UI frames for the change from ``prev`` to ``now``.

    ``prev=None`` is a first look: one STATE_SNAPSHOT. Otherwise, per changed
    agent: running -> RUN_STARTED, verified -> RUN_FINISHED, failed/cancelled
    -> RUN_ERROR, entering/leaving blocked -> STEP_STARTED/STEP_FINISHED, plus
    one STATE_DELTA (JSON Patch) carrying every change.
    """
    if prev is None:
        return [Frame("STATE_SNAPSHOT", "", {
            "agents": [asdict(a) for a in now]})]
    before = {a.run_id: a for a in prev}
    frames: list[Frame] = []
    ops: list[dict] = []
    for a in now:
        old = before.get(a.run_id)
        if old == a:
            continue
        ops.append(_delta_op("add" if old is None else "replace", a))
        was = old.state if old else None
        if a.state == RUNNING and was != RUNNING:
            frames.append(Frame("RUN_STARTED", a.run_id, {
                "threadId": a.parent_run_id or a.run_id, "runId": a.run_id}))
        elif a.state == VERIFIED and was != VERIFIED:
            frames.append(Frame("RUN_FINISHED", a.run_id, {
                "threadId": a.parent_run_id or a.run_id, "runId": a.run_id}))
        elif a.state in (FAILED, CANCELLED) and was != a.state:
            frames.append(Frame("RUN_ERROR", a.run_id, {
                "message": a.reason or a.state, "code": a.state}))
        if a.state == BLOCKED and was != BLOCKED:
            frames.append(Frame("STEP_STARTED", a.run_id, {
                "stepName": a.reason}))
        elif was == BLOCKED and a.state != BLOCKED:
            frames.append(Frame("STEP_FINISHED", a.run_id, {
                "stepName": old.reason}))
    for old in prev:
        if old.run_id not in {a.run_id for a in now}:
            ops.append({"op": "remove", "path": "/agents/%s" % old.run_id})
    if ops:
        frames.append(Frame("STATE_DELTA", "", {"delta": ops}))
    return frames
