"""
TRAJ-002: a trajectory debugger over the black-box trace (BBOX-001).

A run that fails at step 25 was often already broken at step 5. This module
reads the run's event trace, derives the invariants that trace is subject to,
checks every step against them in order, and names the FIRST violation, which
is the root-cause candidate, not the step where the run finally fell over.

The trace is the objective event log (`Store.objective_events`): what was
observable, never model thoughts. Event `detail` is untrusted data; it is only
ever quoted (truncated) as evidence and never decides a verdict.

The report feeds the failure genome (`failure_record`, a ready
`failure_genome.FailureRecord`) and a regression test (`regression_test`, the
trace up to the violation plus the verdict a fixed engine must stop giving).
Nothing here writes: capturing the record is the caller's decision.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Callable

from friday import objectives as O
from friday.failure_genome import FailureRecord

_EVIDENCE_CAP = 400

_RUN_EVENTS = (O.EVENT_RUN_STARTED, O.EVENT_RUN_RESUMED, O.EVENT_RUN_PAUSED,
               O.EVENT_RUN_COMPLETED, O.EVENT_RUN_PARTIAL, O.EVENT_RUN_FAILED,
               O.EVENT_RUN_CANCELLED)
_RUN_ENDS = (O.EVENT_RUN_COMPLETED, O.EVENT_RUN_PARTIAL, O.EVENT_RUN_FAILED,
             O.EVENT_RUN_CANCELLED)
#: An outcome is something a task can only reach after it has begun.
_OUTCOMES = (O.EVENT_TASK_SUCCEEDED, O.EVENT_SIDE_EFFECT_COMMITTED)
_REOPENS = (O.EVENT_TASK_STARTED, O.EVENT_TASK_RETRY)


@dataclass
class _State:
    """What the checks have seen so far; one instance per trace."""
    last_at: float | None = None
    run_ended_at: int | None = None
    seen: set = field(default_factory=set)        # task ids with any earlier event
    succeeded: set = field(default_factory=set)   # task ids with a recorded success


@dataclass(frozen=True)
class Invariant:
    name: str
    why: str
    #: (state, step) -> the way this step breaks it, or None.
    check: Callable[[_State, dict], str | None]


def _num(value) -> float | None:
    """A timestamp as epoch seconds. The store writes `at` as an ISO-8601
    string (`now_iso`); synthetic traces may carry a float. Anything else is
    None, so the rule simply does not apply to that step."""
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        pass
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:       # naive stamps are read as UTC, consistently
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _time_moves_forward(state: _State, step: dict) -> str | None:
    at = _num(step.get("at"))
    if at is not None and state.last_at is not None and at < state.last_at:
        return f"timestamp {at} is before the previous step's {state.last_at}"
    return None


def _nothing_after_run_end(state: _State, step: dict) -> str | None:
    if state.run_ended_at is not None and step.get("event") != O.EVENT_RUN_RESUMED:
        return f"{step.get('event')} after the run ended at step {state.run_ended_at}"
    return None


def _outcome_follows_start(state: _State, step: dict) -> str | None:
    task = step.get("task_id")
    if step.get("event") in _OUTCOMES and task and task not in state.seen:
        return f"task {task} reports {step.get('event')} but no earlier step touched it"
    return None


def _no_restart_after_success(state: _State, step: dict) -> str | None:
    task = step.get("task_id")
    if step.get("event") in _REOPENS and task in state.succeeded:
        return f"task {task} is {step.get('event')} again after it already succeeded"
    return None


_ALL = (
    Invariant("time-moves-forward", "events are appended in time order", _time_moves_forward),
    Invariant("nothing-after-the-run-ends",
              "a run that reached a terminal state records nothing more until it is resumed",
              _nothing_after_run_end),
    Invariant("outcome-follows-start",
              "a task cannot succeed or commit a side effect before it has begun",
              _outcome_follows_start),
    Invariant("no-restart-after-success",
              "a task that succeeded is never started or retried again",
              _no_restart_after_success),
)


def derive_invariants(steps: list[dict]) -> list[Invariant]:
    """The invariants this trace is subject to: a rule applies only if the
    trace carries the events it is about (timestamps, run events, task events)."""
    has_time = any(_num(s.get("at")) is not None for s in steps)
    has_run = any(s.get("event") in _RUN_EVENTS for s in steps)
    has_task = any(s.get("task_id") for s in steps)
    applies = {
        "time-moves-forward": has_time,
        "nothing-after-the-run-ends": has_run,
        "outcome-follows-start": has_task,
        "no-restart-after-success": has_task,
    }
    return [i for i in _ALL if applies[i.name]]


def _evidence(step: dict) -> str:
    """One bounded line quoting the step; detail is data, shown, never obeyed."""
    detail = step.get("detail")
    try:
        text = json.dumps(detail, default=str, sort_keys=True) if detail else ""
    except (TypeError, ValueError):
        text = str(detail)
    return f"{step.get('event')} task={step.get('task_id') or '-'} {text}".rstrip()[:_EVIDENCE_CAP]


def _advance(state: _State, step: dict, number: int) -> None:
    at = _num(step.get("at"))
    if at is not None:
        state.last_at = at
    event, task = step.get("event"), step.get("task_id")
    if event in _RUN_ENDS and state.run_ended_at is None:
        state.run_ended_at = number
    elif event == O.EVENT_RUN_RESUMED:
        state.run_ended_at = None
    if task:
        state.seen.add(task)
        if event in _OUTCOMES or event == O.EVENT_TASK_RECOVERED:
            state.succeeded.add(task)


def _failure_step(steps: list[dict]) -> int | None:
    """The step the run visibly fails at: its run.failed event, else the first
    task failure that nothing later in that task's history undid."""
    for number, step in enumerate(steps, 1):
        if step.get("event") == O.EVENT_RUN_FAILED:
            return number
    latest: dict[str, tuple[int, str]] = {}
    for number, step in enumerate(steps, 1):
        task, event = step.get("task_id"), step.get("event")
        if task and (event in O._EVENT_TASK_STATUS or event == O.EVENT_SIDE_EFFECT_COMMITTED):
            latest[task] = (number, event)
    open_failures = [n for n, event in latest.values() if event == O.EVENT_TASK_FAILED]
    return min(open_failures) if open_failures else None


def debug(trace: list[dict]) -> dict:
    """Name the first invariant violation in `trace` (steps are 1-based)."""
    steps = [dict(s) for s in trace]
    invariants = derive_invariants(steps)
    state = _State()
    violations: list[dict] = []
    for number, step in enumerate(steps, 1):
        for inv in invariants:
            broke = inv.check(state, step)
            if broke:
                violations.append({
                    "step": number, "invariant": inv.name, "why": inv.why,
                    "broke": broke, "event": step.get("event"),
                    "task_id": step.get("task_id"), "evidence": _evidence(step)})
        _advance(state, step, number)

    failure_step = _failure_step(steps)
    first = violations[0] if violations else None
    report = {
        "found": True, "steps": len(steps), "failed": failure_step is not None,
        "failure_step": failure_step, "invariants": [i.name for i in invariants],
        "violations": violations, "first_violation": first,
        "root_cause": None, "failure_record": None, "regression_test": None,
    }
    if failure_step is None:
        report["root_cause"] = "the run did not fail" if first is None else (
            f"the run did not fail, but step {first['step']} breaks {first['invariant']}")
        return report

    symptom = f"run failed at step {failure_step}: {_evidence(steps[failure_step - 1])}"
    if first is None:
        report["root_cause"] = (
            f"no invariant broke before the failure at step {failure_step}; "
            "the cause is in that step's own error, not in the trace's shape")
        report["failure_record"] = FailureRecord(
            symptom=symptom, root_cause=report["root_cause"],
            repair="read the failing step's error; no earlier step is implicated",
            task_class="trajectory")
        return report

    n, name = first["step"], first["invariant"]
    gap = failure_step - n
    report["root_cause"] = (
        f"step {n} ({first['event']}, task {first['task_id'] or '-'}) breaks {name}: "
        f"{first['broke']}; the run only failed at step {failure_step}"
        + (f", {gap} steps later" if gap > 0 else ""))
    report["failure_record"] = FailureRecord(
        symptom=symptom, root_cause=report["root_cause"],
        repair=f"keep the invariant '{name}' ({first['why']}) from breaking at that step",
        task_class="trajectory", wrong_assumption=first["why"],
        regression_test=("tests/test_trace_debugger.py: replay the prefix, expect "
                         f"first violation {name}"))
    report["regression_test"] = {
        "steps": [{k: s.get(k) for k in ("event", "task_id", "at")} for s in steps[:n]],
        "expect_first_violation": {"step": n, "invariant": name},
    }
    return report


def from_run(run_id: str, *, store=None) -> dict:
    """Debug a run straight from its black box (the objective event log)."""
    from friday import observability as OB
    from friday.store import Store

    db = store or Store()
    events = OB._all_events(db, run_id)
    if not events:
        return {"run_id": run_id, "found": False,
                "say": f"no recorded events for objective {run_id}"}
    return {"run_id": run_id, **debug(events)}
