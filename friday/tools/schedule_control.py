"""
MCP faces for scheduled objectives and conditional monitoring
(PRD v3.1 FR-041, FR-042). Logic lives in friday/toolsets/schedules.py.
"""
from __future__ import annotations

import asyncio

from friday import contracts as c
from friday.tools.automation_control import _get_engine
from friday.toolsets import schedules as S


def _execute(request: str, fn, *args, **kwargs) -> dict:
    run = c.Run.create(request, capability="schedules")
    result = fn(run, *args, engine=_get_engine(), **kwargs)
    if asyncio.iscoroutine(result):
        result = asyncio.run(result)
    run.transition("completed" if run.all_succeeded else "partial",
                   None if run.all_succeeded else (result.error or "not verified"))
    try:
        S.store().save_run(run)
    except Exception:  # persistence must never turn a good action into a failure
        pass
    return result.to_dict()


def register(mcp):

    @mcp.tool()
    def schedules_create(name: str, objective: str, trigger: str, tasks: str = "",
                         budgets: str = "", permissions: str = "",
                         delivery: str = "session", condition: str = "",
                         description: str = "") -> dict:
        """
        Schedule an OBJECTIVE to run on its own - once, daily, or every N
        minutes - with the same ledger, evidence gate and policy as one
        asked for out loud.

        `trigger` JSON: {"kind":"once","at":"2026-09-06T08:00"} |
        {"kind":"daily","at":"08:00"} | {"kind":"interval","minutes":30} |
        {"kind":"manual"}. `budgets` JSON: retry_budget, cost_budget_tokens,
        time_budget_s. `permissions`: comma-separated tool ids the run may
        use without asking (ASK-tier only; anything needing a confirmation
        is refused here). `delivery`: session | toast | none.
        `condition` JSON (FR-042 monitoring, notify only when met):
        {"kind":"task_output","task":"t1","path":"hits","op":">","value":0}
        | {"kind":"task_status","task":"t1","op":"==","value":"SUCCEEDED"}
        | {"kind":"any_failed"}. Registered with Windows Task Scheduler and
        queried back - it survives restart and reboot.
        """
        return _execute(f"schedule {name}", S.schedules_create, name, objective, trigger,
                        tasks=tasks, budgets=budgets, permissions=permissions,
                        delivery=delivery, condition=condition, description=description)

    @mcp.tool()
    def schedules_list() -> dict:
        """Every scheduled objective, with whether its OS task is really registered."""
        return _execute("list schedules", S.schedules_list)

    @mcp.tool()
    def schedules_run(name: str) -> dict:
        """Fire a schedule now, through the real objective engine; the
        condition is evaluated and delivery suppressed if it is not met."""
        return _execute(f"run schedule {name}", S.schedules_run, name)

    @mcp.tool()
    def schedules_history(name: str = "", limit: int = 20) -> dict:
        """Every firing: objective run id, outcome, whether the condition was
        met, and whether anything was delivered or suppressed."""
        return _execute("schedule history", S.schedules_history, name=name, limit=limit)

    @mcp.tool()
    def schedules_delete(name: str) -> dict:
        """Remove a schedule and its OS task; verified by querying the OS."""
        return _execute(f"delete schedule {name}", S.schedules_delete, name)
