"""MCP adapter for the Phase 1G reminders toolset."""

from __future__ import annotations

import os

from friday import contracts as c
from friday.policy import PolicyEngine, PolicyError
from friday.toolsets import reminders as R

_engine: PolicyEngine | None = None


def _get_engine() -> PolicyEngine:
    global _engine
    if _engine is None:
        _engine = PolicyEngine()
        for tool_id in (t.strip() for t in
                        os.getenv("ADA_PREAPPROVED_TOOLS", "").split(",") if t.strip()):
            try:
                _engine.approve_for_session(tool_id)
            except PolicyError:
                continue
    return _engine


def _execute(request: str, fn, *args, **kwargs) -> dict:
    run = c.Run.create(request, capability="reminders")
    result = fn(run, *args, engine=_get_engine(), **kwargs)
    run.transition("completed" if run.all_succeeded else "partial",
                   None if run.all_succeeded else (result.error or "not verified"))
    try:
        R.store().save_run(run)
    except Exception:
        pass
    return result.to_dict()


def register(mcp):

    @mcp.tool()
    def reminders_create(message: str, when: str) -> dict:
        """
        Set a reminder that survives ADA restarting, because the operating
        system's scheduler owns it rather than a timer in this process.

        `when` accepts "in 20 minutes", "tomorrow morning", "at 15:30", or an
        ISO timestamp. If it cannot be understood the call fails - say so and
        ask, rather than picking a time.
        """
        return _execute(f"remind: {message}", R.reminders_create, message, when)

    @mcp.tool()
    def reminders_list() -> dict:
        """
        Pending reminders. `still_scheduled` says whether the OS scheduler
        still holds each one, so a reminder that was lost is visible rather
        than silently assumed to be waiting.
        """
        return _execute("list reminders", R.reminders_list)

    @mcp.tool()
    def reminders_cancel(reminder_id: int) -> dict:
        """Cancel a pending reminder by its id (from reminders_list)."""
        return _execute(f"cancel reminder {reminder_id}", R.reminders_cancel,
                        reminder_id)
