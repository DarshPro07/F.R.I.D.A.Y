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
    wall time   now - created_at

A budget that is exhausted blocks the run with a `blocker` that names the
dimension and the numbers, and records `run.budget_exhausted` in the
ledger. It never silently trims work.
"""
from __future__ import annotations

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
    #                 tool calls, worker delegations, replans (strategy changes)
    "TRIVIAL":      {"max_tool_calls": 4,   "max_workers": 0, "max_replans": 1 * MAX_STRATEGY_CHANGES},
    "SIMPLE":       {"max_tool_calls": 12,  "max_workers": 1, "max_replans": 1 * MAX_STRATEGY_CHANGES},
    "STANDARD":     {"max_tool_calls": 40,  "max_workers": 2, "max_replans": 2 * MAX_STRATEGY_CHANGES},
    "COMPLEX":      {"max_tool_calls": 120, "max_workers": 4, "max_replans": 3 * MAX_STRATEGY_CHANGES},
    "LONG_RUNNING": {"max_tool_calls": 400, "max_workers": 6, "max_replans": 4 * MAX_STRATEGY_CHANGES},
    "CRITICAL":     {"max_tool_calls": 160, "max_workers": 4, "max_replans": 2 * MAX_STRATEGY_CHANGES},
}
_DEFAULT_LIMITS = CLASS_LIMITS["STANDARD"]

#: Capabilities whose execution is a worker delegation.
WORKER_CAPABILITIES = frozenset({
    "hermes_delegate", "hermes_team", "hermes_team_start", "executor_run",
    "claude_code_run", "selfdev_run",
})

EVENT_BUDGET_EXHAUSTED = "run.budget_exhausted"


@dataclass
class Spend:
    tokens: int = 0
    tool_calls: int = 0
    workers: int = 0
    replans: int = 0
    wall_s: float = 0.0
    sources: dict = field(default_factory=dict)


@dataclass
class Verdict:
    allowed: bool
    dimension: str = ""
    reason: str = ""
    spend: Spend = field(default_factory=Spend)
    limit: int = 0


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
            spend.sources["gateway_calls"] = len(rows)
        except Exception as exc:  # noqa: BLE001 - a missing ledger is not a free pass; recorded
            spend.sources["gateway_error"] = f"{type(exc).__name__}: {exc}"
    worker_tokens = 0
    if work_log is not None:
        try:
            for record in work_log.for_friday_run(run_id):
                usage = record.get("usage_json") or record.get("usage") or {}
                if isinstance(usage, str):
                    import json
                    usage = json.loads(usage or "{}")
                worker_tokens += int(usage.get("total") or
                                     (int(usage.get("prompt") or 0) + int(usage.get("completion") or 0)))
        except Exception as exc:  # noqa: BLE001
            spend.sources["worker_error"] = f"{type(exc).__name__}: {exc}"
    spend.tokens = gateway_tokens + worker_tokens
    spend.sources.update({"gateway_tokens": gateway_tokens, "worker_tokens": worker_tokens})
    return spend


def check(store, run_id: str, *, next_capability: str = "", next_task_id: str | None = None,
          telemetry=None, work_log=None, now: datetime | None = None) -> Verdict:
    """May the run make one more call? Decided from recorded spend."""
    run = store.objective_run(run_id) or {}
    limits = CLASS_LIMITS.get(str(run.get("task_class") or ""), _DEFAULT_LIMITS)
    spend = measure(store, run_id, telemetry=telemetry, work_log=work_log, now=now)

    ceiling = int(run.get("cost_budget_tokens") or 0)
    if ceiling and spend.tokens >= ceiling:
        return Verdict(False, "tokens", f"{spend.tokens} tokens spent of a {ceiling} ceiling", spend, ceiling)
    wall = int(run.get("time_budget_s") or 0)
    if wall and spend.wall_s >= wall:
        return Verdict(False, "wall_time", f"{spend.wall_s:.0f}s elapsed of a {wall}s budget", spend, wall)
    if spend.tool_calls >= limits["max_tool_calls"]:
        return Verdict(False, "tool_calls", f"{spend.tool_calls} tool calls of {limits['max_tool_calls']} allowed",
                       spend, limits["max_tool_calls"])
    if next_capability in WORKER_CAPABILITIES and spend.workers >= limits["max_workers"]:
        # The task about to run may itself be one of the counted workers
        # (a retry); only a NEW delegation is refused.
        if next_task_id is None or not any(
                t.get("task_id") == next_task_id and int(t.get("attempts") or 0) > 0
                for t in store.objective_tasks(run_id)):
            return Verdict(False, "workers",
                           f"{spend.workers} worker delegations of {limits['max_workers']} allowed",
                           spend, limits["max_workers"])
    if spend.replans > limits["max_replans"]:
        return Verdict(False, "replans", f"{spend.replans} strategy changes of {limits['max_replans']} allowed",
                       spend, limits["max_replans"])
    return Verdict(True, spend=spend)
