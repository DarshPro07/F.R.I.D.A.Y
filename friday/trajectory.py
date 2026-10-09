"""
The run-level trajectory check (PRD v7 TRAJ-001, §12).

The budget stops a run at a hard ceiling, and the task-level stuck detector
stops one task that keeps failing the same way. This reads a run as a whole,
from what it has durably recorded since the person last started or resumed
it, and says whether it is still going somewhere:

- no progress: attempts failing across several tasks with nothing succeeding;
- one cause failing task after task (the network, a provider, a credential);
- policy refusals piling up (the plan keeps asking for what it may not do);
- spend running far ahead of progress;
- a step with side effects being run again and again;
- the run leaving its objective contract: a forbidden action class started,
  more side-effecting steps than allowed, or acting while questions are open;
- the plan itself changing again and again (the person keeps amending it);
- the run losing the worker driving it again and again (worker conflict).

`assess` is pure, so the thresholds can be benchmarked on synthetic traces;
`for_run` gathers the inputs from the store. The executor pauses a flagged
run and asks the person (continuous.py); resuming starts a new window. Acting
on an open question is caught before the step starts (`upcoming`), and a person
who has said carry on is not asked about the same questions again.
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

ON_TRACK = "ON_TRACK"
NO_PROGRESS = "NO_PROGRESS"
DRIFTING = "DRIFTING"

#: The event the executor records when it pauses a flagged run.
EVENT_FLAGGED = "trajectory.flagged"

#: Failed or retried attempts since the last success, across at least
#: `NO_PROGRESS_TASKS` tasks. One task alone is the task-level detector's.
NO_PROGRESS_ATTEMPTS = 6
NO_PROGRESS_TASKS = 2
#: One failure kind across this many tasks since the last success, each
#: failing it at least `SAME_CAUSE_REPEATS` times: it keeps happening, it is
#: not one blip that hit every task running at that moment.
SAME_CAUSE_TASKS = 3
SAME_CAUSE_REPEATS = 2
#: Policy refusals in the window.
NEAR_MISSES = 3
#: Spend at or above this fraction of a budget ...
BURN_SPEND = 0.75
#: ... with at most this fraction of the tasks done.
BURN_PROGRESS = 0.25
#: Starts of one task whose capability writes or acts outside FRIDAY.
SIDE_EFFECT_STARTS = 3
#: Plan amendments (a skip, amend or append, PRD §12 "user amendments") in the
#: window. Fewer is a person steering the plan; this many is a plan that is not
#: settling, and the trajectory the person approved may no longer be the one
#: running.
AMENDMENTS = 4
#: Takeovers of the run (the watchdog seizing it from a worker that stopped
#: driving it, PRD §12 "worker conflict") in the window. One is ordinary crash
#: recovery; this many is the run thrashing between workers, not advancing.
TAKEOVERS = 3

_STARTED = "task.started"
_SUCCEEDED = "task.succeeded"
_FAILED = "task.failed"
_RETRY = "task.retry"
_BLOCKED = "task.blocked"
_RESUMED = "run.resumed"
#: modification.GRAPH_EDIT: a skip, amend or append to the live plan.
_AMENDED = "graph_edited"
#: objectives.EVENT_WATCHDOG_ORPHANED: the watchdog took the run over. Not
#: lease.acquired, which every healthy wake records.
_ORPHANED = "watchdog.orphaned"
_REFUSAL_MARKS = ("ENVELOPE:", "APPROVAL_REQUIRED:", "BLOCKED:")
_REFUSAL = re.compile(r"\b(?:" + "|".join(_REFUSAL_MARKS) + ")")


@dataclass(frozen=True)
class Assessment:
    verdict: str
    reasons: tuple[str, ...] = ()
    signals: dict = field(default_factory=dict)


def _detail(event: dict) -> dict:
    detail = event.get("detail")
    return detail if isinstance(detail, dict) else {}


def _refused(event: dict) -> bool:
    """A failed, retried or blocked attempt whose reason is a policy refusal."""
    return (event.get("event") in (_FAILED, _RETRY, _BLOCKED)
            and bool(_REFUSAL.search(str(_detail(event).get("reason") or ""))))


def _side_effecting(capability: str) -> bool:
    from friday import capabilities as C

    found = C.by_id(capability or "")
    return found is not None and found.side_effect in ("write", "external_action")


def _outside_contract(governance: dict, started: list[str],
                      capability_of: dict) -> list[str]:
    """Started steps the contract's governance rules out (scope creep): an
    action class it forbids, or more side-effecting steps than it allows."""
    from friday import contracts as K

    forbidden = set(governance.get("forbidden") or ())
    found = []
    effects = []
    for task in started:
        capability = str(capability_of.get(task) or "")
        kind = K.action_class(capability)
        if kind and kind in forbidden:
            found.append(f"{capability} ({kind} was forbidden)")
        elif _side_effecting(capability):
            effects.append(task)
    ceiling = K.write_ceiling(governance)
    if ceiling and len(effects) > ceiling:
        found.append(f"{len(effects)} steps with side effects against a limit of {ceiling}")
    return found


def _acting_on_questions(open_questions: tuple, steps: list[str],
                         capability_of: dict) -> list[str]:
    """The side-effecting steps, started or about to start, while the contract
    still holds unanswered questions: an assumption nobody verified."""
    if not open_questions:
        return []
    return [str(capability_of.get(task)) for task in steps
            if _side_effecting(str(capability_of.get(task) or ""))]


def _questions_waved_through(earlier: list[dict], open_questions: tuple) -> bool:
    """Whether the person was already asked about these open questions and
    said carry on: a flag that named them, before the resume that began this
    window. Asking again after every step would make "carry on" mean one step."""
    asked = 0
    for e in earlier:
        signals = _detail(e).get("signals")
        count = signals.get("unanswered_questions") if isinstance(signals, dict) else 0
        if e.get("event") == EVENT_FLAGGED and isinstance(count, int):
            asked = max(asked, count)
    return bool(open_questions) and asked >= len(open_questions)


def assess(events: list[dict], tasks: list[dict], *, spend: float = 0.0,
           governance: dict | None = None, open_questions: tuple = (),
           upcoming: tuple = (), refusals: int | None = None,
           starts: dict | None = None, since: list[dict] | None = None) -> Assessment:
    """
    Where the run is going, from its events since the person last resumed it
    (all of them if never), its task rows, and the largest fraction of any
    budget it has spent. With the objective contract's governance and open
    questions it also says whether the run has left what was asked. `upcoming`
    is the ids of the tasks the executor is about to dispatch: a side-effecting
    one under an open question is flagged before it starts, not after.
    `refusals`, `starts` (task id -> times started) and `since` (the failed,
    retried or blocked attempts since the run's last success) are those counts
    over the whole window when `events` holds only the newest of a long run;
    left out, they are counted from `events`.
    """
    governance = governance or {}
    start = max((i for i, e in enumerate(events) if e.get("event") == _RESUMED), default=-1)
    window = events[start + 1:]

    if since is None:
        last_success = max((i for i, e in enumerate(window) if e.get("event") == _SUCCEEDED),
                           default=-1)
        since = [e for e in window[last_success + 1:]
                 if e.get("event") in (_FAILED, _RETRY, _BLOCKED)]
    failing = {e.get("task_id") for e in since if e.get("task_id")}
    per_cause: Counter = Counter()
    for e in since:
        # A refusal is the plan asking for what it may not do, counted below;
        # it is not the world failing the same way everywhere.
        if _REFUSAL.search(str(_detail(e).get("reason") or "")):
            continue
        kind = str(_detail(e).get("kind") or "")
        if kind and e.get("task_id"):
            per_cause[(kind, e["task_id"])] += 1
    causes: dict[str, set] = defaultdict(set)
    for (kind, task), n in per_cause.items():
        if n >= SAME_CAUSE_REPEATS:
            causes[kind].add(task)
    if refusals is None:
        refusals = sum(1 for e in window if _refused(e))
    amendments = sum(1 for e in window if e.get("event") == _AMENDED)
    takeovers = sum(1 for e in window if e.get("event") == _ORPHANED)
    capability_of = {t.get("task_id"): t.get("capability") for t in tasks}
    status_of = {t.get("task_id"): t.get("status") for t in tasks}
    if starts is None:
        starts = Counter(e.get("task_id") for e in window if e.get("event") == _STARTED)
    # A step that has succeeded is done and will not run again; only one still
    # being retried can repeat its side effect.
    repeated_effects = sorted(task for task, n in starts.items()
                              if n >= SIDE_EFFECT_STARTS
                              and status_of.get(task) != "SUCCEEDED"
                              and _side_effecting(capability_of.get(task, "")))
    done = sum(1 for t in tasks if t.get("status") == "SUCCEEDED")
    progress = done / len(tasks) if tasks else 1.0
    started = sorted(task for task in starts if task)
    out_of_scope = _outside_contract(governance, started, capability_of)
    waved_through = start >= 0 and _questions_waved_through(events[:start], open_questions)
    unanswered = [] if waved_through else _acting_on_questions(open_questions, started,
                                                               capability_of)
    pending = [] if waved_through else _acting_on_questions(
        open_questions, [task for task in upcoming if task not in starts], capability_of)

    signals = {"attempts_since_success": len(since), "failing_tasks": len(failing),
               "refusals": refusals, "amendments": amendments, "takeovers": takeovers,
               "spend": round(spend, 3), "progress": round(progress, 3),
               "repeated_side_effects": repeated_effects, "out_of_scope": out_of_scope,
               "unanswered_questions": len(open_questions) if unanswered or pending else 0,
               "pending_side_effects": pending}
    stuck: list[str] = []
    drifting: list[str] = []
    if len(since) >= NO_PROGRESS_ATTEMPTS and len(failing) >= NO_PROGRESS_TASKS:
        stuck.append(f"{len(since)} attempts across {len(failing)} tasks and nothing has "
                     "succeeded since")
    for kind, affected in sorted(causes.items()):
        if len(affected) >= SAME_CAUSE_TASKS:
            stuck.append(f"the same failure ({kind}) across {len(affected)} tasks")
    if refusals >= NEAR_MISSES:
        drifting.append(f"{refusals} policy refusals: the plan keeps asking for what it may not do")
    if amendments >= AMENDMENTS:
        drifting.append(f"{amendments} plan amendments since the run started: the plan keeps changing")
    if takeovers >= TAKEOVERS:
        drifting.append(f"{takeovers} worker takeovers since the run started: "
                        "the run keeps losing the worker driving it")
    # An early warning below the ceilings; a budget already spent is the
    # budget's to stop, with its own exact reason (objective_budget.check).
    if BURN_SPEND <= spend < 1.0 and progress <= BURN_PROGRESS:
        drifting.append(f"{spend:.0%} of a budget spent with {progress:.0%} of the tasks done")
    if repeated_effects:
        drifting.append("a step with side effects has run again and again: "
                        + ", ".join(repeated_effects))
    if out_of_scope:
        drifting.append("work has gone beyond the objective's contract: "
                        + "; ".join(out_of_scope))
    if unanswered:
        drifting.append(f"a step with side effects has started while {len(open_questions)} "
                        "question(s) in the contract are still open: " + "; ".join(unanswered))
    if pending:
        drifting.append(f"a step with side effects is about to start while {len(open_questions)} "
                        "question(s) in the contract are still open: " + "; ".join(pending))
    if stuck:
        return Assessment(NO_PROGRESS, tuple(stuck + drifting), signals)
    if drifting:
        return Assessment(DRIFTING, tuple(drifting), signals)
    return Assessment(ON_TRACK, (), signals)


def spend_fraction(store, run_id: str) -> float:
    """The largest fraction of any of the run's budgets spent so far."""
    from friday import objective_budget as B

    run = store.objective_run(run_id) or {}
    limits = B.CLASS_LIMITS.get(str(run.get("task_class") or ""), B._DEFAULT_LIMITS)
    spend = B.measure(store, run_id)
    fractions = [spend.tool_calls / limits["max_tool_calls"]] if limits["max_tool_calls"] else []
    if run.get("cost_budget_tokens"):
        fractions.append(spend.tokens / int(run["cost_budget_tokens"]))
    if run.get("time_budget_s"):
        fractions.append(spend.wall_s / int(run["time_budget_s"]))
    return max(fractions, default=0.0)


#: The events that outlive the recent window, each rare per run: a flag and the
#: resume after it, and the amendments and takeovers a window is judged on.
_KEPT_WHATEVER_THEIR_AGE = (EVENT_FLAGGED, _RESUMED, _AMENDED, _ORPHANED)


def _events_for(store, run_id: str) -> list[dict]:
    """The run's newest events, plus every flag, resume, amendment and takeover
    however old. A long run pushes the flag the person answered, and the resume
    after it, out of the recent window, and without them a "carry on" would be
    forgotten and the same questions asked again; it pushes the early amendments
    and takeovers of the current window out too, and without them a plan that
    keeps changing, or a run that keeps losing its worker, would read as
    steady for as long as it ran."""
    merged = {e["id"]: e for e in store.recent_objective_events(run_id)}
    for e in store.objective_events_named(run_id, _KEPT_WHATEVER_THEIR_AGE):
        merged.setdefault(e["id"], e)
    return [merged[i] for i in sorted(merged)]


def for_run(store, run_id: str, upcoming: tuple = ()) -> Assessment:
    """`assess` over what the store holds for this run, and the ids of the
    tasks about to be dispatched."""
    raw = (store.objective_run(run_id) or {}).get("contract")
    try:
        contract = json.loads(raw) if raw else {}
    except (ValueError, TypeError):
        # An unreadable contract leaves the other signals; the executor logs it.
        contract = {}
    contract = contract if isinstance(contract, dict) else {}
    events = _events_for(store, run_id)
    # Refusals, starts and the failures since the last success are common per
    # run, so they cannot be fetched by name like the rest: the store counts
    # them over the whole window, which opens at the last resume, rather than
    # over the newest events only.
    opened = max((e["id"] for e in events if e.get("event") == _RESUMED), default=0)
    refusals = sum(1 for e in store.objective_events_named(
        run_id, (_FAILED, _RETRY, _BLOCKED), after_id=opened, mentioning=_REFUSAL_MARKS)
        if _refused(e))
    # Bounded: at most one success per task, and failures after the last
    # success are capped by the per-task retry limit (continuous.py's
    # DEFAULT_MAX_ATTEMPTS, 3) before NO_PROGRESS pauses the run at 6.
    succeeded = store.objective_events_named(run_id, (_SUCCEEDED,), after_id=opened)
    since = store.objective_events_named(
        run_id, (_FAILED, _RETRY, _BLOCKED),
        after_id=max((e["id"] for e in succeeded), default=opened))
    return assess(events, store.objective_tasks(run_id),
                  spend=spend_fraction(store, run_id),
                  governance=dict(contract.get("governance") or {}),
                  open_questions=tuple(contract.get("open_questions") or ()),
                  upcoming=tuple(upcoming), refusals=refusals, since=since,
                  starts=store.objective_event_counts(run_id, _STARTED, after_id=opened))
