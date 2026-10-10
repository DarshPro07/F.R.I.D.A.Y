"""
One bus for human signals (PRD v7 section 96, SIGNAL-001).

The person is an asynchronous API: they answer from the desktop, say stop on
voice, change their mind from a phone while a worker on another machine is
still mid-call. Every one of those arrives here as a signal with a kind and a
surface, is written to `objective_events` before anything moves, and then does
what its kind means to the objective. Durable rows only - no module state - so
a signal sent to a freshly started process finds a waiting run exactly as the
dead one left it, and the MCP server and the voice agent can each send one.

    USER_CORRECTION   raises the amendment barrier (below); an `amend` in the
                      payload is applied through `modification.amend_task`
                      and lifts it again
    USER_APPROVAL     `continuous.resume_after_approval`, bound to the named
                      operation and target
    USER_STOP         the run is paused
    USER_BACK         the barrier is lifted and a paused run resumes
    USER_DENIAL, USER_ANSWER, USER_PRIORITY_CHANGE, USER_POLICY_CHANGE
                      recorded on the objective; no behaviour hangs off them
                      yet

The amendment barrier: while it is up the executor starts nothing new for the
run (`continuous._execute_task` asks `barrier_up`). A call already running is
never rewritten halfway - `modification` refuses that - but its result cannot
start the old version's dependents; they wait for the amendment to be applied
or for the person to say go on. The barrier is two events in the trace, so it
survives a restart and holds for whichever process or device drives the run.
"""

from __future__ import annotations

from dataclasses import dataclass

from friday import modification
from friday import objectives as O
from friday.store import Store

KINDS = (
    "USER_CORRECTION", "USER_APPROVAL", "USER_DENIAL", "USER_ANSWER",
    "USER_STOP", "USER_PRIORITY_CHANGE", "USER_POLICY_CHANGE", "USER_BACK",
)
SURFACES = ("desktop", "voice", "telegram", "web", "mobile")

EVENT_RECEIVED = "signal.received"
EVENT_APPLIED = "signal.applied"
EVENT_BARRIER_RAISED = "signal.barrier_raised"
EVENT_BARRIER_RELEASED = "signal.barrier_released"

#: What a signal did to the objective.
BARRIER_RAISED = "barrier_raised"
BARRIER_RELEASED = "barrier_released"
PAUSED = "paused"
RESUMED = "resumed"
RECORDED = "recorded"        # written down; nothing acts on this kind yet
IGNORED = "ignored"          # the run was not in a state the signal applies to

_ACTED = (BARRIER_RAISED, BARRIER_RELEASED, PAUSED, RESUMED)


@dataclass(frozen=True)
class Delivery:
    """What was received and what it did, in enough detail to say it back."""

    signal_id: int
    kind: str
    surface: str
    effect: str
    reason: str = ""

    @property
    def applied(self) -> bool:
        return self.effect in _ACTED


def barrier_up(store: Store, run_id: str) -> dict | None:
    """The raise event of the amendment barrier if it is up, else None."""
    marks = store.objective_events_named(
        run_id, (EVENT_BARRIER_RAISED, EVENT_BARRIER_RELEASED))
    if marks and marks[-1]["event"] == EVENT_BARRIER_RAISED:
        return marks[-1]
    return None


def _raise_barrier(store: Store, run_id: str, signal_id: int,
                   surface: str) -> None:
    if barrier_up(store, run_id) is None:
        store.append_objective_event(
            run_id, EVENT_BARRIER_RAISED,
            detail={"signal_id": signal_id, "surface": surface,
                    "graph_version": modification.graph_version(store, run_id)})


def _release_barrier(store: Store, run_id: str, signal_id: int,
                     surface: str) -> bool:
    if barrier_up(store, run_id) is None:
        return False
    store.append_objective_event(
        run_id, EVENT_BARRIER_RELEASED,
        detail={"signal_id": signal_id, "surface": surface,
                "graph_version": modification.graph_version(store, run_id)})
    return True


def _correct(store: Store, run_id: str, signal_id: int, surface: str,
             payload: dict) -> tuple[str, str]:
    _raise_barrier(store, run_id, signal_id, surface)
    amend = payload.get("amend")
    if not isinstance(amend, dict):
        return BARRIER_RAISED, "waiting for the amendment to be applied"
    changes = amend.get("changes")
    edit = modification.amend_task(
        store, run_id, str(amend.get("description") or ""),
        changes if isinstance(changes, dict) else {},
        said=str(payload.get("said") or ""))
    if not edit.applied:
        return BARRIER_RAISED, edit.reason
    _release_barrier(store, run_id, signal_id, surface)
    return BARRIER_RELEASED, edit.reason


def _approve(store: Store, run_id: str, surface: str,
             payload: dict) -> tuple[str, str]:
    from friday import continuous

    if continuous.resume_after_approval(
            store, run_id, decided_by=surface,
            operation=str(payload.get("operation") or ""),
            target=str(payload.get("target") or "")):
        return RESUMED, ""
    return IGNORED, "nothing on this run is waiting for that approval"


def send(store: Store, run_id: str, kind: str, *, surface: str,
         payload: dict | None = None) -> Delivery:
    """Deliver one signal to an objective. Raises ValueError for an unknown
    kind, surface or run, or a payload that is not a dict - nothing is written
    for those. Otherwise the signal is recorded first, then acted on."""
    surface = str(surface).lower()
    if kind not in KINDS:
        raise ValueError(f"unknown signal kind {kind!r}")
    if surface not in SURFACES:
        raise ValueError(f"unknown surface {surface!r}")
    if payload is not None and not isinstance(payload, dict):
        raise ValueError("a signal payload is a dict")
    payload = payload or {}
    run = store.objective_run(run_id)
    if run is None:
        raise ValueError(f"no such objective run {run_id!r}")

    signal_id = store.append_objective_event(
        run_id, EVENT_RECEIVED,
        detail={"kind": kind, "surface": surface, "payload": payload})

    effect, reason = RECORDED, ""
    if run["status"] in O.RUN_TERMINAL:
        effect, reason = IGNORED, f"the run is already {run['status']}"
    elif kind == "USER_CORRECTION":
        effect, reason = _correct(store, run_id, signal_id, surface, payload)
    elif kind == "USER_APPROVAL":
        effect, reason = _approve(store, run_id, surface, payload)
    elif kind == "USER_STOP":
        if O.pause_run(store, run_id=run_id,
                       reason=f"stop signal from {surface}", executor_id=surface):
            effect = PAUSED
        else:
            effect, reason = IGNORED, "the run is already paused"
    elif kind == "USER_BACK":
        released = _release_barrier(store, run_id, signal_id, surface)
        if O.resume_run(store, run_id=run_id,
                        reason=f"back signal from {surface}", executor_id=surface):
            effect = RESUMED
        elif released:
            effect = BARRIER_RELEASED
        else:
            effect, reason = IGNORED, "the run was not paused"

    store.append_objective_event(
        run_id, EVENT_APPLIED,
        detail={"signal_id": signal_id, "kind": kind, "surface": surface,
                "effect": effect, "reason": reason[:300]})
    return Delivery(signal_id, kind, surface, effect, reason)
