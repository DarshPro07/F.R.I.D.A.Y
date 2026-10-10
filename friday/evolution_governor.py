"""
Evolution runs below the person's work, under the existing governors (EVO-006).

Self-evolution is not a second engine. An experiment is an ordinary objective
run, so its money, time, tool-call and turn ceilings are `objective_budget`,
its progress test is `trajectory` (COG-006), and the machine's headroom, the
person being away and the power being on are `governor.admit(OPTIONAL,
BACKGROUND)` (RES-001). This module only adds the order of precedence and the
two stops the requirement names:

    1. the person's active task        (nothing evolves while it runs)
    2. an unfinished objective         (a paused run, or one whose lease lapsed)
    3. reliability repair              \\
    4. approved improvement             > evolution, in this order
    5. exploration                     /

Owner ruling 2026-10-08c (docs/plans/build-harness/00-owner-directive-2026-10-08c.md):
evolution starts on a trigger, in an overnight mode, or automatically when idle.
All three come through `admit`; the mode only decides what must hold first.

Assumptions, stated rather than asked (revisit if wrong):
  * the overnight window is 01:00-06:00 on the clock `now` carries;
  * one experiment runs at a time (experiment capacity), because the
    governor's worker cap is for the person's work;
  * a run waiting on the person (WAITING_QUESTION / WAITING_PERMISSION) cannot
    use the machine, so it does not hold evolution back.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from datetime import datetime, timezone

from friday import governor as G
from friday import objective_budget as B
from friday import objectives as O
from friday import productivity
from friday import trajectory as T

# -- precedence, highest first -------------------------------------------------

ACTIVE_TASK = "active_task"
UNFINISHED_OBJECTIVE = "unfinished_objective"
RELIABILITY_REPAIR = "reliability_repair"
APPROVED_IMPROVEMENT = "approved_improvement"
EXPLORATION = "exploration"
EVOLUTION_CLASSES = (RELIABILITY_REPAIR, APPROVED_IMPROVEMENT, EXPLORATION)

# -- modes (owner ruling 2026-10-08c) ------------------------------------------

TRIGGER = "trigger"
OVERNIGHT = "overnight"
IDLE = "idle"
MODES = (TRIGGER, OVERNIGHT, IDLE)
OVERNIGHT_FROM_HOUR = 1
OVERNIGHT_TO_HOUR = 6

#: Experiments that may run at once.
MAX_EXPERIMENTS = 1
_LABEL = "evolution:"

EVENT_STARTED = "evolution.started"
EVENT_STOPPED = "evolution.stopped"


@dataclass
class Admission:
    admitted: bool
    reason: str
    blocked_by: str = ""
    lease: str = ""


@dataclass
class Stop:
    stopped: bool
    reason: str = ""
    dimension: str = ""
    evidence: dict = field(default_factory=dict)


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(timezone.utc)


def _is_evolution(store, run_id: str) -> bool:
    return bool(store.objective_events_named(run_id, (EVENT_STARTED,)))


def user_work(store, *, now: datetime | None = None, exclude: str = "") -> dict:
    """The person's runs that outrank evolution: `active` ones hold a live
    lease; `unfinished` ones are paused, or running with a lapsed lease and
    waiting to be resumed. Evolution's own runs are not the person's work."""
    now = _now(now)
    active: list[str] = []
    unfinished: list[str] = []
    for status in (O.RUN_RUNNING, O.RUN_PAUSED):
        for run in store.objective_runs_in(status):
            run_id = run["run_id"]
            if run_id == exclude or _is_evolution(store, run_id):
                continue
            expiry = B._parse(run.get("lease_expiry") or "")
            if status == O.RUN_RUNNING and expiry is not None and expiry > now:
                active.append(run_id)
            else:
                unfinished.append(run_id)
    return {"active": active, "unfinished": unfinished}


def admit(store, gov: G.Governor, run_id: str, *, klass: str, mode: str, trigger: str = "",
          waiting: dict | None = None, now: datetime | None = None) -> Admission:
    """May this experiment start now? `waiting` maps an evolution class to how
    many proposals of it are pending: a class starts only when no higher one
    is. On admission the governor's lease is held under `evolution:<run_id>`
    and the run is marked as evolution; release it with `check` or the
    governor when the experiment ends."""
    if klass not in EVOLUTION_CLASSES:
        return Admission(False, f"unknown evolution class {klass!r}; known: {EVOLUTION_CLASSES}")
    if mode not in MODES:
        return Admission(False, f"unknown evolution mode {mode!r}; known: {MODES}")
    now = _now(now)

    work = user_work(store, now=now, exclude=run_id)
    if work["active"]:
        return Admission(False, f"the person's task is active ({', '.join(work['active'])})",
                         ACTIVE_TASK)
    if work["unfinished"]:
        return Admission(False, f"an unfinished objective comes first ({', '.join(work['unfinished'])})",
                         UNFINISHED_OBJECTIVE)
    for higher in EVOLUTION_CLASSES[:EVOLUTION_CLASSES.index(klass)]:
        if (waiting or {}).get(higher):
            return Admission(False, f"{higher} work is pending and comes before {klass}", higher)

    if mode == TRIGGER and not trigger.strip():
        return Admission(False, "trigger mode needs the trigger that caused it")
    if mode == OVERNIGHT and not OVERNIGHT_FROM_HOUR <= now.hour < OVERNIGHT_TO_HOUR:
        return Admission(False, f"overnight mode runs {OVERNIGHT_FROM_HOUR:02d}:00-"
                                f"{OVERNIGHT_TO_HOUR:02d}:00; it is {now:%H:%M}")

    if sum(1 for lease in gov.active_leases()
           if str(lease["label"]).startswith(_LABEL)) >= MAX_EXPERIMENTS:
        return Admission(False, f"{MAX_EXPERIMENTS} experiment(s) already running", "capacity")

    decision = gov.admit(G.OPTIONAL, label=f"{_LABEL}{run_id}", objective_id=run_id,
                         owner="evolution", priority=G.BACKGROUND)
    if not decision.admitted:
        return Admission(False, decision.reason, "governor")
    store.append_objective_event(run_id, EVENT_STARTED, detail={
        "class": klass, "mode": mode, "trigger": trigger, "lease": decision.lease})
    return Admission(True, decision.reason, lease=decision.lease)


def _stop(store, gov: G.Governor, run_id: str, lease: str, evidence: dict) -> Stop:
    """Archive why, end the run, give the lease back."""
    store.append_objective_event(run_id, EVENT_STOPPED, detail=evidence)
    store.finish_objective_run(run_id, status=O.RUN_CANCELLED,
                               summary={"evolution_stop": evidence})
    productivity.report_finished_run(store, run_id)
    gov.release(lease)
    return Stop(True, evidence["reason"], evidence.get("dimension", ""), evidence)


def check(store, gov: G.Governor, run_id: str, lease: str, *, telemetry=None, work_log=None,
          now: datetime | None = None) -> Stop:
    """Called before each step of an experiment. Stops it, with its evidence
    archived on the run, when the person's task has become active, when any
    budget dimension is exhausted, or when the trajectory shows no progress."""
    now = _now(now)
    work = user_work(store, now=now, exclude=run_id)
    if work["active"]:
        return _stop(store, gov, run_id, lease, {
            "reason": "preempted", "blocked_by": ACTIVE_TASK, "runs": work["active"]})
    verdict = B.check(store, run_id, telemetry=telemetry, work_log=work_log, now=now)
    if not verdict.allowed:
        return _stop(store, gov, run_id, lease, {
            "reason": "budget_exhausted", "dimension": verdict.dimension,
            "detail": verdict.reason, "limit": verdict.limit,
            "spend": dataclasses.asdict(verdict.spend)})
    assessment = T.for_run(store, run_id)
    if assessment.verdict == T.NO_PROGRESS:
        return _stop(store, gov, run_id, lease, {
            "reason": "no_progress", "verdict": assessment.verdict,
            "reasons": list(assessment.reasons), "signals": assessment.signals,
            "spend": dataclasses.asdict(verdict.spend)})
    B.record_soft_limits(store, run_id, verdict)
    return Stop(False)
