"""MCP adapter for the automation engine (§14)."""

from __future__ import annotations

import asyncio
import os

from friday import contracts as c
from friday.policy import PolicyEngine, PolicyError
from friday.toolsets import automations as A

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
    run = c.Run.create(request, capability="automations")
    result = fn(run, *args, engine=_get_engine(), **kwargs)
    if asyncio.iscoroutine(result):
        result = asyncio.run(result)
    run.transition("completed" if run.all_succeeded else "partial",
                   None if run.all_succeeded else (result.error or "not verified"))
    try:
        A.store().save_run(run)
    except Exception:
        pass
    return result.to_dict()


def register(mcp):

    @mcp.tool()
    def automations_create(name: str, trigger: str, steps: str,
                           description: str = "") -> dict:
        """
        Define a repeating job: a trigger, and a graph of steps.

        `trigger` is JSON, one of:
            {"kind": "daily", "at": "08:00"}      24-hour local time
            {"kind": "interval", "minutes": 30}   5 to 1440
            {"kind": "manual"}                    only runs when asked

        `steps` is a JSON list. Each step is
            {"id": "news", "tool": "web.news", "args": {"topic": "world"},
             "needs": ["earlier_step"], "retries": 1}

        `needs` is what makes the steps a graph: one whose dependency did not
        succeed is passed over rather than run into the same failure. A step
        can use an earlier step's output with {{steps.<id>.<key>}}, and a
        value supplied when it runs with {{vars.<name>}}.

        The set of capabilities a step may name is fixed, and every one of
        them only reads or plays - nothing that writes files, closes apps or
        runs commands is automatable. Naming anything outside the set is
        refused when the automation is created, and the refusal lists exactly
        what is allowed, so try it and read the error rather than guessing.

        A daily or interval automation registers a task with Windows' own
        scheduler, so it survives you restarting me and it survives a reboot.
        Success here means the task was queried back and found - not that a
        command exited zero.
        """
        return _execute(f"create automation {name}", A.automations_create,
                        name, trigger, steps, description=description)

    @mcp.tool()
    def automations_list() -> dict:
        """
        Every automation, with `armed` read back from the OS scheduler rather
        than from our own record. `orphaned` means we think it is scheduled
        and Windows disagrees - say so plainly if you see it.
        """
        return _execute("list automations", A.automations_list)

    @mcp.tool()
    def automations_run(name: str, variables: str = "") -> dict:
        """
        Run an automation now, without waiting for its trigger.

        `variables` is a JSON object filling any {{vars.x}} placeholders.
        Returns every step's status, attempts and evidence. A result of
        `partial` means some steps did not succeed - report which ones.
        """
        return _execute(f"run automation {name}", A.automations_run,
                        name, variables=variables)

    @mcp.tool()
    def automations_history(name: str = "", limit: int = 10) -> dict:
        """
        What the automations actually did, per step, including the ones that
        ran while nobody was watching. This is how "did my morning briefing
        run?" is answered with evidence instead of a guess.
        """
        return _execute("automation history", A.automations_history,
                        name=name, limit=limit)

    @mcp.tool()
    def automations_delete(name: str) -> dict:
        """
        Delete an automation and disarm its scheduled task. Fails rather than
        reporting success if the task is still registered afterwards.
        """
        return _execute(f"delete automation {name}", A.automations_delete, name)
