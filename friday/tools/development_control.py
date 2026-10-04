"""
Development-run tools (PRD v7 CTX-003, owner ruling 2026-10-03d).

    development_run      start one development run for an objective, in its own
                         sandbox worktree; the context compiler is the only way
                         to the worker. FILE_WRITE: asks by default.
    development_promote  merge a run's sandbox work into a named live branch.
                         COMMAND_EXECUTION: a yes to start a run never covers it.

A run that needs commands is refused until EXEC-SBX-001 exists.
"""
from __future__ import annotations

from friday import contracts as c
from friday.toolsets import development_runs as DT


def register(mcp):

    @mcp.tool()
    def development_run(work: str, root: str, objective_id: str, project: str = "",
                        strategy_hint: str = "", budget_tokens: int = 0,
                        needs_commands: bool = False) -> dict:
        """
        Start one development run for the durable objective `objective_id`, in
        the repository at `root`. The objective's critical facts, exact
        literals and blockers are compiled into the run's package first; if
        they cannot be kept the run does not start. The run's work stays in its
        own sandbox worktree. Say `needs_commands` when the work must run
        programs: that is refused until the OS sandbox (EXEC-SBX-001) exists.
        """
        run = c.Run.create(f"development run {objective_id}", capability="development_run")
        result = DT.development_run(run, work, root, objective_id, project, strategy_hint,
                                    budget_tokens, needs_commands)
        if result.status != c.SUCCEEDED:
            return {"status": "not_started", "objective_id": objective_id,
                    "reason": result.error,
                    "say": f"The development run did not start: {result.error}"}
        report = result.output["report"]
        return {"status": "started", "objective_id": objective_id,
                "team": (report["team"] or {}).get("roles", []),
                "evidence": result.verification.evidence,
                "say": "The development run is staffed and works in its own sandbox worktree"}

    @mcp.tool()
    def development_promote(root: str, run_id: str, branch: str, message: str = "") -> dict:
        """
        Merge a development run's sandbox worktree into `branch` (named, never
        guessed). Needs its own approval: approval to start the run does not
        cover landing it. Returns the merge commit and the rollback target.
        """
        run = c.Run.create(f"promote development run {run_id}", capability="development_promote")
        result = DT.development_promote(run, root, run_id, branch, message)
        if result.status != c.SUCCEEDED:
            return {"status": "not_promoted", "run_id": run_id, "reason": result.error,
                    "say": f"The development run was not promoted: {result.error}"}
        return {"status": "promoted", "run_id": run_id, **result.output,
                "say": f"The development run is on {branch}; the rollback target is recorded"}
