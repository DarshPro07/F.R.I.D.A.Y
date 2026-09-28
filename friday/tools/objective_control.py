"""MCP adapter for the Phase 3 objectives toolset."""

from __future__ import annotations

import os

from friday import contracts as c
from friday.policy import PolicyEngine, PolicyError
from friday.toolsets import objectives as O

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


def _execute(request: str, fn, *args, _run: c.Run | None = None, **kwargs) -> dict:
    run = _run or c.Run.create(request, capability="objectives")
    result = fn(run, *args, engine=_get_engine(), **kwargs)
    run.transition("completed" if run.all_succeeded else "partial",
                   None if run.all_succeeded else (result.error or "not verified"))
    try:
        O.store().save_run(run)
    except Exception:
        pass
    return result.to_dict()


def register(mcp):

    @mcp.tool()
    def objective_start(objective: str, tasks: str = "",
                        replace: bool = False, nonce: str = "") -> dict:
        """
        Start a multi-step objective that Friday runs on its own, no
        "continue" required. The request is compiled into a validated task
        graph and driven to completion by the run driver.

        `objective` is the plain-language request, e.g. "check whether this
        computer looks healthy, open Paint, find me one current technology
        story, create and clean up a temporary note, tell me when the whole
        job is finished". `tasks` is an optional explicit JSON list of
        {"capability", "arguments", "dependencies"} that is used instead of
        the planner when given; capability names come from the capability
        list (dotted ids like "apps.open" are not used - call the
        capability's own name, e.g. "apps_open"). If an objective is already
        active it fails unless `replace` is true, which cancels the old run
        (with a reason trail) before starting the new one.

        When the objective could send what it reads (it reads private data
        and can reach the web, a browser or a terminal), the answer is
        APPROVAL_REQUIRED with a `confirm.nonce`: ask the person, and once
        they have answered call this again with the same `objective`,
        `tasks` and that `nonce`. You cannot approve it yourself - the nonce
        only says which question was answered.
        """
        from friday import confirmation

        # The yes is bound to the run that asked, so a retry carrying its
        # nonce runs as that run - and only for this question.
        pending = confirmation.book.pending.get(nonce) if nonce else None
        run = (c.Run(run_id=pending.run_id, request=f"start objective: {objective}",
                     capability="objectives")
               if pending is not None and pending.action == "objective.composition"
               else None)
        return _execute(f"start objective: {objective}", O.objective_start,
                        objective, tasks=tasks, replace=replace, nonce=nonce, _run=run)

    @mcp.tool()
    def objective_status(run_id: str = "") -> dict:
        """
        Where the objective run stands: status, and the per-task rows with
        evidence. Without `run_id` it reports the most recent active run.
        """
        return _execute("objective status", O.objective_status, run_id)

    @mcp.tool()
    def objective_list(limit: int = 10) -> dict:
        """Recent objective runs, most recent first."""
        return _execute("list objectives", O.objective_list, limit=limit)

    @mcp.tool()
    def objective_pause(run_id: str = "", reason: str = "") -> dict:
        """
        Pause the objective run - the current task finishes, then nothing
        runs until an explicit resume. Say so if you pause it, and resume it
        when asked.
        """
        return _execute(f"pause objective: {reason or run_id or 'run'}",
                        O.objective_pause, run_id, reason=reason)

    @mcp.tool()
    def objective_resume(run_id: str = "", reason: str = "") -> dict:
        """Resume a paused objective run exactly where it paused."""
        return _execute(f"resume objective: {reason or run_id or 'run'}",
                        O.objective_resume, run_id, reason=reason)

    @mcp.tool()
    def objective_cancel(run_id: str = "", reason: str = "") -> dict:
        """
        Stop the objective run: unfinished tasks are interrupted with a
        recorded reason and the run ends CANCELLED. Say what happened.
        """
        return _execute(f"cancel objective: {reason or run_id or 'run'}",
                        O.objective_cancel, run_id, reason=reason)

    @mcp.tool()
    def objective_history(run_id: str = "", limit: int = 25) -> dict:
        """The event ledger of a run: every transition, task and reason."""
        return _execute("objective history", O.objective_history, run_id,
                        limit=limit)

    @mcp.tool()
    def objective_trace(run_id: str = "", as_text: bool = False) -> dict:
        """One trace that reconstructs what happened to an objective (FR-054):
        state transitions, tool calls, workers, model calls, policy
        decisions, latency, retries, errors and verification outcomes -
        from durable state, never from model thoughts."""
        from friday.toolsets import model_gateway as MG
        return _execute("objective trace", MG.objective_trace, run_id,
                        as_text=as_text)