"""
The objective budget, enforced (audit A-022).

`compile_objective` records `cost_budget_tokens` and `time_budget_s` on the
run at admission, and the model gateway's `GrowthGuard` stops runaway
context per call - but nothing stopped a run that had *already* spent its
ceiling from dispatching the next task, and nothing counted tool calls,
worker delegations or replans at all. This module is the one check the
engine makes before every capability call, against numbers that were
actually recorded, never estimated:

    tokens      GatewayTelemetry rows for the objective (input + output,
                actual provider usage) plus Hermes WorkRunLog usage for
                delegated tasks
    tool calls  task attempts recorded in objective_tasks
    workers     delegations recorded as hermes_delegate / hermes_team tasks
    replans     strategy_changes across the run's tasks
    turns       GatewayTelemetry rows for the objective (one per model call)
                plus the model calls a Hermes worker made itself (`calls`)
    research    attempts of the research capabilities (ORCH-003)
    wall time   now - created_at

A budget that is exhausted blocks the run with a `blocker` that names the
dimension and the numbers, and records `run.budget_exhausted` in the
ledger. It never silently trims work.

ORCH-003: every ceiling also has a soft limit at SOFT_FRACTION of it. A run
that crosses one is still allowed; the engine records `run.budget_soft_limit`
once per dimension with a progress / blocker / why-more-budget report built
from recorded task state, so no model has to notice or be asked. The hard
limit is the ceiling above, enforced by `check` in code; raising the ceiling
on the run row is the only way past it. A worker already running is held to
the same ceilings on each of its usage ticks (`hold_running`), so a runaway
is stopped mid-call rather than at the next dispatch.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone

#: How many times one task may change strategy (re-plan, different role,
#: reduce scope) on the same failure fingerprint before it stops asking and
#: goes BLOCKED. Owned here, not in `continuous`, because the run-level
#: `max_replans` below must be reconciled with it: a class cap smaller than
#: this makes the per-task loop-breaker unreachable - the run parks PAUSED
#: on "budget exhausted (replans)" two failures before the task would have
#: concluded BLOCKED, and a stuck fingerprint never reaches a terminal state
#: (test_failure_fingerprint, first CI run after A-022 landed).
MAX_STRATEGY_CHANGES = 3

#: Per-class limits for the dimensions that are not tokens. Tokens and
#: wall time come from the run row (set at admission from the task class);
#: these come from the same class so one table decides everything.
#:
#: `max_replans` is expressed in whole tasks' worth of strategy changes so
#: the invariant above holds by construction: every class allows at least
#: one task to exhaust its own strategy budget and conclude, and the run
#: cap governs replanning ACROSS tasks, which is the runaway the audit
#: (A-022) was about.
CLASS_LIMITS: dict[str, dict[str, int]] = {
    #                 tool calls, worker delegations, replans (strategy changes),
    #                 turns (model calls; ~4 per tool call), research calls
    "TRIVIAL":      {"max_tool_calls": 4,   "max_workers": 0, "max_replans": 1 * MAX_STRATEGY_CHANGES, "max_turns": 16, "max_research_calls": 1},
    "SIMPLE":       {"max_tool_calls": 12,  "max_workers": 1, "max_replans": 1 * MAX_STRATEGY_CHANGES, "max_turns": 48, "max_research_calls": 2},
    "STANDARD":     {"max_tool_calls": 40,  "max_workers": 2, "max_replans": 2 * MAX_STRATEGY_CHANGES, "max_turns": 160, "max_research_calls": 6},
    "COMPLEX":      {"max_tool_calls": 120, "max_workers": 4, "max_replans": 3 * MAX_STRATEGY_CHANGES, "max_turns": 480, "max_research_calls": 16},
    "LONG_RUNNING": {"max_tool_calls": 400, "max_workers": 6, "max_replans": 4 * MAX_STRATEGY_CHANGES, "max_turns": 1600, "max_research_calls": 40},
    "CRITICAL":     {"max_tool_calls": 160, "max_workers": 4, "max_replans": 2 * MAX_STRATEGY_CHANGES, "max_turns": 640, "max_research_calls": 16},
}
#: ORCH-003: the fraction of a ceiling at which a run is asked to report.
SOFT_FRACTION = 0.8

#: Research capabilities: minutes and money per call (friday/audit_fixtures.py),
#: so they get a call budget of their own.
RESEARCH_CAPABILITIES = frozenset({"web_crawl", "web_deep_research"})

_DEFAULT_LIMITS = CLASS_LIMITS["STANDARD"]

#: Capabilities whose execution is a worker delegation.
WORKER_CAPABILITIES = frozenset({
    "hermes_delegate", "hermes_team", "hermes_team_start", "executor_run",
    "claude_code_run", "selfdev_run", "development_run",
})

EVENT_BUDGET_EXHAUSTED = "run.budget_exhausted"
EVENT_BUDGET_SOFT = "run.budget_soft_limit"


@dataclass
class Spend:
    tokens: int = 0
    tool_calls: int = 0
    workers: int = 0
    replans: int = 0
    wall_s: float = 0.0
    turns: int = 0
    research_calls: int = 0
    sources: dict = field(default_factory=dict)


@dataclass
class Verdict:
    allowed: bool
    dimension: str = ""
    reason: str = ""
    spend: Spend = field(default_factory=Spend)
    limit: int = 0
    #: Soft limits crossed while still allowed: {dimension, spent, limit}.
    soft: tuple = ()


def _parse(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def measure(store, run_id: str, *, telemetry=None, work_log=None,
            now: datetime | None = None) -> Spend:
    """What this run has actually consumed, from durable records."""
    run = store.objective_run(run_id) or {}
    tasks = store.objective_tasks(run_id)
    spend = Spend()
    spend.tool_calls = sum(int(t.get("attempts") or 0) for t in tasks)
    # A retried delegation is the same worker slot again, not a new
    # worker: count delegated TASKS that have started, not attempts
    # (test_rc11 C3: a connectivity retry of hermes_delegate must not be
    # refused as a second delegation).
    spend.workers = sum(1 for t in tasks
                        if t.get("capability") in WORKER_CAPABILITIES
                        and int(t.get("attempts") or 0) > 0)
    spend.research_calls = sum(int(t.get("attempts") or 0) for t in tasks
                               if t.get("capability") in RESEARCH_CAPABILITIES)
    spend.replans = sum(int(((t.get("detail") or {}).get("strategy_changes")) or 0)
                        for t in tasks)
    created = _parse(run.get("created_at") or "")
    if created:
        spend.wall_s = max(0.0, ((now or datetime.now(timezone.utc)) - created).total_seconds())

    gateway_tokens = 0
    if telemetry is not None:
        try:
            rows = telemetry.for_objective(run_id)
            gateway_tokens = sum(int(r.get("input_tokens") or 0) + int(r.get("output_tokens") or 0)
                                 for r in rows)
            spend.turns = len(rows)
            spend.sources["gateway_calls"] = len(rows)
        except Exception as exc:  # noqa: BLE001 - a missing ledger is not a free pass; recorded
            spend.sources["gateway_error"] = f"{type(exc).__name__}: {exc}"
    worker_tokens = worker_calls = 0
    if work_log is not None:
        try:
            for record in work_log.for_friday_run(run_id):
                usage = record.get("usage_json") or record.get("usage") or {}
                if isinstance(usage, str):
                    import json
                    usage = json.loads(usage or "{}")
                # Hermes' `session.usage` event carries the counters under
                # `usage`; reading the top level counted a worker as free.
                if isinstance(usage.get("usage"), dict):
                    usage = usage["usage"]
                worker_tokens += int(usage.get("total") or
                                     (int(usage.get("prompt") or 0) + int(usage.get("completion") or 0)))
                worker_calls += int(usage.get("calls") or 0)
        except Exception as exc:  # noqa: BLE001
            spend.sources["worker_error"] = f"{type(exc).__name__}: {exc}"
    spend.tokens = gateway_tokens + worker_tokens
    spend.turns += worker_calls
    spend.sources.update({"gateway_tokens": gateway_tokens, "worker_tokens": worker_tokens,
                          "worker_calls": worker_calls})
    return spend


def _envelope_of(run: dict):
    """The run's execution envelope (PRD v7 section 43), None when it has
    none, and why it cannot be read when it is there but unreadable."""
    raw = run.get("envelope")
    if not raw:
        return None, ""
    from friday import contracts

    try:
        return contracts.Envelope.from_dict(json.loads(raw)), ""
    except (ValueError, KeyError, TypeError) as exc:
        return None, f"the run's execution envelope cannot be read ({type(exc).__name__})"


def check(store, run_id: str, *, next_capability: str = "", next_task_id: str | None = None,
          telemetry=None, work_log=None, now: datetime | None = None,
          in_flight: bool = False) -> Verdict:
    """May the run make one more call? Decided from recorded spend.

    The run's execution envelope only tightens this: its token and tool-call
    limits lower the ceilings, never raise them, and once it has expired
    nothing more runs until the person re-issues it.

    `in_flight` asks instead whether a call that is already running may go
    on (ORCH-003): only what that call can still spend - tokens, wall time,
    turns - is judged. Its tool call, its worker slot and the replans were
    charged when it was dispatched, and the envelope's end is the
    supervisor's own timer, so none of those stop a worker half way."""
    run = store.objective_run(run_id) or {}
    limits = CLASS_LIMITS.get(str(run.get("task_class") or ""), _DEFAULT_LIMITS)
    spend = measure(store, run_id, telemetry=telemetry, work_log=work_log, now=now)

    envelope, unreadable = _envelope_of(run)
    if unreadable and not in_flight:
        return Verdict(False, "envelope", unreadable, spend, 0)
    if envelope is not None and not in_flight and envelope.expired(now):
        return Verdict(False, "envelope_expiry",
                       f"the execution envelope expired at {envelope.expires_at}; "
                       f"the person re-issues it", spend, 0)
    ceiling = int(run.get("cost_budget_tokens") or 0)
    if envelope is not None and envelope.token_limit:
        ceiling = min(ceiling, envelope.token_limit) if ceiling else envelope.token_limit
    if ceiling and spend.tokens >= ceiling:
        return Verdict(False, "tokens", f"{spend.tokens} tokens spent of a {ceiling} ceiling", spend, ceiling)
    wall = int(run.get("time_budget_s") or 0)
    if wall and spend.wall_s >= wall:
        return Verdict(False, "wall_time", f"{spend.wall_s:.0f}s elapsed of a {wall}s budget", spend, wall)
    max_calls = limits["max_tool_calls"]
    if envelope is not None and envelope.tool_call_limit:
        max_calls = min(max_calls, envelope.tool_call_limit)
    if spend.tool_calls >= max_calls and not in_flight:
        return Verdict(False, "tool_calls", f"{spend.tool_calls} tool calls of {max_calls} allowed",
                       spend, max_calls)
    if spend.turns >= limits["max_turns"]:
        return Verdict(False, "turns", f"{spend.turns} turns of {limits['max_turns']} allowed",
                       spend, limits["max_turns"])
    if (next_capability in RESEARCH_CAPABILITIES
            and spend.research_calls >= limits["max_research_calls"]):
        return Verdict(False, "research_calls",
                       f"{spend.research_calls} research calls of {limits['max_research_calls']} allowed",
                       spend, limits["max_research_calls"])
    if next_capability in WORKER_CAPABILITIES and spend.workers >= limits["max_workers"]:
        # The task about to run may itself be one of the counted workers
        # (a retry); only a NEW delegation is refused.
        if next_task_id is None or not any(
                t.get("task_id") == next_task_id and int(t.get("attempts") or 0) > 0
                for t in store.objective_tasks(run_id)):
            return Verdict(False, "workers",
                           f"{spend.workers} worker delegations of {limits['max_workers']} allowed",
                           spend, limits["max_workers"])
    if spend.replans > limits["max_replans"] and not in_flight:
        return Verdict(False, "replans", f"{spend.replans} strategy changes of {limits['max_replans']} allowed",
                       spend, limits["max_replans"])
    soft = tuple(
        {"dimension": name, "spent": spent, "limit": limit}
        for name, spent, limit in (
            ("tokens", spend.tokens, ceiling), ("wall_time", spend.wall_s, wall),
            ("tool_calls", spend.tool_calls, max_calls), ("turns", spend.turns, limits["max_turns"]),
            ("research_calls", spend.research_calls, limits["max_research_calls"]),
            ("workers", spend.workers, limits["max_workers"]),
            ("replans", spend.replans, limits["max_replans"]))
        if limit and spent >= limit * SOFT_FRACTION)
    return Verdict(True, spend=spend, soft=soft)


def soft_report(store, run_id: str, entry: dict) -> dict:
    """What a run at a soft limit would say: progress, blocker, why more
    budget helps. Built from recorded task state, not asked of a model."""
    from friday import objectives as O

    tasks = store.objective_tasks(run_id)
    done = sum(1 for t in tasks if t["status"] == O.TaskStatus.SUCCEEDED)
    open_states = (O.TaskStatus.QUEUED, O.TaskStatus.READY, O.TaskStatus.RUNNING,
                   O.TaskStatus.WAITING, O.TaskStatus.INTERRUPTED)
    remaining = sum(1 for t in tasks if t["status"] in open_states)
    stuck = [t for t in tasks if t["status"] in (O.TaskStatus.FAILED, O.TaskStatus.BLOCKED)]
    if stuck:
        last = stuck[-1]
        blocker = (f"{last['capability']} {str(last['status']).lower()} "
                   f"({last.get('failure_kind') or 'no failure kind recorded'})")
    else:
        blocker = (store.objective_run(run_id) or {}).get("blocker") or "none"
    left = max(entry["limit"] - entry["spent"], 0)
    why = (f"{remaining} task{'s' if remaining != 1 else ''} still open; "
           f"{left:.0f} of the {entry['limit']} {entry['dimension']} ceiling is left for them"
           if remaining else "no tasks remain open, so more budget would not help")
    return {**entry, "progress": f"{done} of {len(tasks)} tasks done",
            "blocker": blocker, "why_more_budget": why}


def record_soft_limits(store, run_id: str, verdict: Verdict, *, task_id: str | None = None) -> None:
    """Record `run.budget_soft_limit` once per dimension the run has crossed."""
    if not verdict.soft:
        return
    reported = {(e.get("detail") or {}).get("dimension")
                for e in store.objective_events_named(run_id, (EVENT_BUDGET_SOFT,))}
    for entry in verdict.soft:
        if entry["dimension"] not in reported:
            store.append_objective_event(run_id, EVENT_BUDGET_SOFT, task_id=task_id,
                                         detail=soft_report(store, run_id, entry))


def record_exhausted(store, run_id: str, verdict: Verdict, *, task_id: str | None = None,
                     work_run_id: str = "") -> None:
    """Record `run.budget_exhausted`: the dimension, the reason and the numbers."""
    detail = {"dimension": verdict.dimension, "reason": verdict.reason,
              "limit": verdict.limit, "spend": vars(verdict.spend)}
    if work_run_id:
        detail["work_run_id"] = work_run_id
    store.append_objective_event(run_id, EVENT_BUDGET_EXHAUSTED, task_id=task_id, detail=detail)


def hold_running(store, run_id: str, work_run_id: str, *, telemetry=None, work_log=None,
                 now: datetime | None = None) -> Verdict | None:
    """Hold a worker that is already running to its run's budget (ORCH-003).

    `check` runs before a dispatch and before a gateway call, but one
    delegation is a single long call: without this a runaway worker spends
    past the ceiling until it ends on its own. The caller runs it on each of
    the worker's usage ticks. A soft limit crossed is reported once, as at
    dispatch. A hard limit crossed is recorded as `run.budget_exhausted` and
    the refusing verdict is returned for the caller to stop the worker with -
    in code, no model asked. None when the worker may go on."""
    verdict = check(store, run_id, telemetry=telemetry, work_log=work_log, now=now,
                    in_flight=True)
    if verdict.allowed:
        record_soft_limits(store, run_id, verdict)
        return None
    record_exhausted(store, run_id, verdict, work_run_id=work_run_id)
    return verdict
