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

`assess` reads failures. The stall layer (COG-006) reads progress: a `Step`
record per action, a `progress_score` over the five signs a run is going
somewhere (state changed, tool executed, artifact produced, test improved,
requirement moved), and a `StallMeter` that says STALL_DETECTED when tokens pass
a threshold with no real progress, or the same action or the same failure comes
round again. The critic is injected and runs at most once per meter, so a
healthy run never pays for it. This is the detector only: wiring it into the
product loop and the build harness is later slices and is not done yet.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from typing import Any, Callable

ON_TRACK = "ON_TRACK"
NO_PROGRESS = "NO_PROGRESS"
DRIFTING = "DRIFTING"
STALL_DETECTED = "STALL_DETECTED"

#: The event the executor records when it pauses a flagged run.
EVENT_FLAGGED = "trajectory.flagged"
#: The once-marker: the stall critic has run for this run, never again.
EVENT_STALL_CRITIC = "stall.critic"

#: Tokens spent since the run last made real progress. A threshold, not a
#: budget: the budget stops a run, this says it is spending without going
#: anywhere.
STALL_TOKENS = 100_000
#: The same (tool, arguments), or the same failure of one tool, this many times
#: since the last real progress.
STALL_REPEATS = 3
#: Reasoning steps in a row that did not act.
STALL_IDLE_STEPS = 4

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


# -- the stall layer (COG-006) -------------------------------------------------------

OK = "ok"
FAILED = "failed"


def args_hash(arguments: Any) -> str:
    """A short stable fingerprint of a call's arguments, for "the same action"."""
    text = json.dumps(arguments, sort_keys=True, default=str)
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True)
class Step:
    """One thing a run did. `tool` is empty for a step that reasoned and did not
    act. The four flags are the outcome signs a consumer can observe."""

    tokens: int = 0
    tool: str = ""
    args: str = ""
    outcome: str = OK
    failure: str = ""
    state_changed: bool = False
    artifact: bool = False
    test_improved: bool = False
    requirement_moved: bool = False

    @property
    def tool_executed(self) -> bool:
        return bool(self.tool) and self.outcome == OK

    @property
    def real_progress(self) -> bool:
        """A tool that ran and changed nothing is activity, not progress."""
        return (self.state_changed or self.artifact or self.test_improved
                or self.requirement_moved)


def progress_score(step: Step) -> int:
    """How many of the five signs of progress this step shows."""
    return sum((step.state_changed, step.tool_executed, step.artifact,
                step.test_improved, step.requirement_moved))


class StallMeter:
    """
    Progress per token for one run. Feed it each step; it says STALL_DETECTED
    when tokens since the last real progress reach `token_limit`, when the same
    action or the same failure has come round `repeat_limit` times since then,
    or when `idle_limit` steps in a row reasoned without acting. Real progress
    clears all of that. A verdict, once reached, stays: the run was stalled.
    """

    def __init__(self, *, token_limit: int = STALL_TOKENS,
                 repeat_limit: int = STALL_REPEATS, idle_limit: int = STALL_IDLE_STEPS):
        self.token_limit = token_limit
        self.repeat_limit = repeat_limit
        self.idle_limit = idle_limit
        self.steps = 0
        self.tokens = 0
        self.score = 0
        self.critic_ran = False
        self._since = 0
        self._idle = 0
        self._actions: Counter = Counter()
        self._failures: Counter = Counter()
        self._found: Assessment | None = None

    def feed(self, step: Step) -> Assessment:
        self.steps += 1
        self.tokens += step.tokens
        self.score += progress_score(step)
        if step.real_progress:
            self._since = 0
            self._idle = 0
            self._actions.clear()
            self._failures.clear()
        else:
            self._since += step.tokens
            self._idle = 0 if step.tool else self._idle + 1
            if step.tool:
                self._actions[(step.tool, step.args)] += 1
            if step.tool and step.outcome == FAILED and step.failure:
                self._failures[(step.tool, step.failure)] += 1
        return self.verdict()

    def verdict(self) -> Assessment:
        if self._found is None:
            reasons = self._reasons()
            if reasons:
                self._found = Assessment(STALL_DETECTED, tuple(reasons), self.signals())
        return self._found or Assessment(ON_TRACK, (), self.signals())

    def flag(self, reason: str) -> Assessment:
        """Latch a stall the caller found itself (an action it refused to run a
        third time), so it goes through the same once-only critic."""
        if self._found is None:
            self._found = Assessment(STALL_DETECTED, (reason,), self.signals())
        return self._found

    def signals(self) -> dict:
        return {"steps": self.steps, "tokens": self.tokens, "progress_score": self.score,
                "tokens_since_progress": self._since}

    def _reasons(self) -> list[str]:
        found = []
        if self._since >= self.token_limit:
            found.append(f"{self._since} tokens spent since the last real progress "
                         f"(limit {self.token_limit})")
        for (tool, _), n in sorted(self._actions.items()):
            if n >= self.repeat_limit:
                found.append(f"{tool} ran {n} times with the same arguments and "
                             "nothing advanced")
        for (tool, failure), n in sorted(self._failures.items()):
            if n >= self.repeat_limit:
                found.append(f"{tool} failed the same way ({failure}) {n} times")
        if self._idle >= self.idle_limit:
            found.append(f"{self._idle} reasoning steps in a row without an action")
        return found

    def claim_critic(self) -> bool:
        """True exactly once, and only for a stalled meter: the caller then runs
        its critic. A healthy run is never handed the claim."""
        if self.critic_ran or self.verdict().verdict != STALL_DETECTED:
            return False
        self.critic_ran = True
        return True

    def critique(self, critic: Callable[[Assessment], Any]) -> Any:
        """Run `critic` on a stalled run, at most once; None otherwise."""
        return critic(self.verdict()) if self.claim_critic() else None


def stall_steps(events: list[dict], tasks: list[dict], *,
                since_resume: bool = True) -> list[Step]:
    """The steps a stored run has recorded, by default since the person last
    resumed it: a task that succeeded moved the requirement, one that failed
    carries its kind."""
    start = -1
    if since_resume:
        start = max((i for i, e in enumerate(events) if e.get("event") == _RESUMED), default=-1)
    capability_of = {t.get("task_id"): str(t.get("capability") or "") for t in tasks}
    steps = []
    for e in events[start + 1:]:
        kind, task = e.get("event"), e.get("task_id")
        capability = capability_of.get(task) or ""
        if kind == _SUCCEEDED:
            steps.append(Step(tool=capability, args=args_hash(task), requirement_moved=True,
                              state_changed=_side_effecting(capability)))
        elif kind in (_FAILED, _RETRY, _BLOCKED):
            failure = str(_detail(e).get("kind") or _detail(e).get("reason") or "error")[:60]
            steps.append(Step(tool=capability or "task", args=args_hash(task),
                              outcome=FAILED, failure=failure))
    return steps


def stall_for_events(events: list[dict], tasks: list[dict], *, tokens: int = 0,
                     meter: StallMeter | None = None,
                     step_count: int | None = None) -> StallMeter:
    """A meter fed the stored run's steps. The store does not attribute tokens
    to events, so the run's measured `tokens` are spread evenly over ALL of its
    steps: an estimate of progress per token, not a measurement. Only the steps
    since the last resume are fed, each at that share, so a resume does not
    hand the whole run's spend to its first step. `step_count` is the number of
    steps the whole run has taken when `events` is only its recent window (the
    store's counts); without it the window is taken to be the whole run."""
    meter = meter or StallMeter()
    every = step_count if step_count is not None else len(
        stall_steps(events, tasks, since_resume=False))
    share = tokens // every if every else 0
    for step in stall_steps(events, tasks):
        meter.feed(replace(step, tokens=share))
    return meter


def critic_already_ran(events: list[dict]) -> bool:
    return any(e.get("event") == EVENT_STALL_CRITIC for e in events)


def stall_for_run(store, run_id: str, *, telemetry=None, work_log=None) -> StallMeter:
    """The stall meter over what the store holds for this run. Tokens come only
    from the gateway ledger and the Hermes work log (`telemetry`, `work_log`,
    as for `objective_budget.measure`); without them the run's spend reads 0.

    Only the newest events are fed (a resume older than that window is outside
    it, so every fed event is after it), but the per-step token share divides by
    the run's whole step count and the once-only critic marker is looked up by
    name, so neither depends on how long the run is.

    Repeated failure is `assess`'s ground and the task-level detector's (a task
    gets `continuous.DEFAULT_MAX_ATTEMPTS` tries): the store path therefore only
    calls the same failure a stall once it has outlasted `NO_PROGRESS_ATTEMPTS`,
    so it never pre-empts them. What it adds is spend with nothing advancing."""
    from friday import objective_budget as B

    step_count = sum(sum(store.objective_event_counts(run_id, kind).values())
                     for kind in (_SUCCEEDED, _FAILED, _RETRY, _BLOCKED))
    meter = stall_for_events(store.recent_objective_events(run_id),
                             store.objective_tasks(run_id),
                             tokens=B.measure(store, run_id, telemetry=telemetry,
                                              work_log=work_log).tokens,
                             meter=StallMeter(repeat_limit=NO_PROGRESS_ATTEMPTS),
                             step_count=step_count)
    meter.critic_ran = critic_already_ran(
        store.objective_events_named(run_id, (EVENT_STALL_CRITIC,)))
    return meter
