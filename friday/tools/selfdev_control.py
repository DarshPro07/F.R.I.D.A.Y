"""
Controlled self-development tools (PRD v3.1 FR-047..FR-051).

    selfdev_run       observe -> propose -> sandbox -> implement -> test ->
                      independent review -> regression -> benchmark; stops
                      at BENCHMARKED (never touches the live checkout)
    selfdev_promote   merge into the live branch on an approval, then a
                      health probe that rolls back automatically on failure
    selfdev_rollback  deterministic git revert of a promoted change
    selfdev_status    where each candidate stands and why

Kernel surfaces (policy, netguard, sensitive domains, the self-upgrade
guards) are refused at propose time: Friday cannot loosen its own
security boundaries through this path.
"""
from __future__ import annotations

from friday import contracts as c
from friday.toolsets import selfdev as ST


def register(mcp):

    @mcp.tool()
    def selfdev_run(candidate_id: str, weakness: str, evidence: dict, proposal: str,
                    files: list[str], patch: str, tests: list[str],
                    regression: list[str] | None = None) -> dict:
        """
        Take one self-improvement candidate through every gate short of
        promotion: measured evidence required; proposal names the files and
        the touched subsystem's tests; the change (a unified diff) is
        applied in an isolated git worktree; subsystem tests, an
        independent review, the regression baseline and a benchmark all run
        there. The live checkout is untouched. Ends BENCHMARKED (ready for
        `selfdev_promote` with approval) or REJECTED with the reason and the
        sandbox kept as evidence.
        """
        run = c.Run.create(f"selfdev {candidate_id}", capability="selfdev_run")
        result = ST.selfdev_run(run, candidate_id, weakness, evidence or {}, proposal,
                                files, patch, tests, regression)
        if result.status == c.FAILED:
            return {"status": "rejected", "candidate": candidate_id, "reason": result.error,
                    "say": f"{candidate_id} was rejected: {result.error}"}
        cand = result.output["candidate"]
        return {"status": "ready", "candidate": candidate_id, "state": cand["state"],
                "sandbox": cand["sandbox_path"], "review": cand["review"],
                "benchmark": cand["benchmark"], "evidence": result.verification.evidence,
                "say": f"{candidate_id} passed every gate in its sandbox; promotion needs your yes"}

    @mcp.tool()
    def selfdev_promote(candidate_id: str, approved: bool = False) -> dict:
        """
        Merge a BENCHMARKED self-change into the live branch. Requires an
        explicit approval; runs a post-promotion health probe and rolls the
        merge back automatically (git revert) if the probe fails.
        """
        run = c.Run.create(f"promote {candidate_id}", capability="selfdev_promote")
        result = ST.selfdev_promote(run, candidate_id, approved=approved)
        if result.status == c.FAILED:
            return {"status": "not_promoted", "candidate": candidate_id, "reason": result.error,
                    "say": f"{candidate_id} was not promoted: {result.error}"}
        cand = result.output["candidate"]
        return {"status": "promoted", "candidate": candidate_id,
                "merge_commit": cand["promotion"]["merge_commit"],
                "rollback_target": cand["promotion"]["base_commit"],
                "say": f"{candidate_id} is live; rollback target recorded"}

    @mcp.tool()
    def selfdev_rollback(candidate_id: str, reason: str = "") -> dict:
        """Deterministically undo a promoted self-change (git revert of the
        merge); the prior known-good version is restored and history kept."""
        run = c.Run.create(f"rollback {candidate_id}", capability="selfdev_rollback")
        result = ST.selfdev_rollback(run, candidate_id, reason)
        if result.status == c.FAILED:
            return {"status": "failed", "candidate": candidate_id, "reason": result.error}
        return {"status": "rolled_back", "candidate": candidate_id,
                "evidence": result.verification.evidence,
                "say": f"{candidate_id} rolled back"}

    @mcp.tool()
    def selfdev_status(candidate_id: str = "") -> dict:
        """Where each self-development candidate stands (state, weakness,
        rejection reason), or one candidate's full record."""
        run = c.Run.create("selfdev status", capability="selfdev_status")
        result = ST.selfdev_status(run, candidate_id)
        if result.status == c.FAILED:
            return {"found": False, "candidate": candidate_id, "say": result.error}
        return {"found": True, "candidates" if not candidate_id else "candidate": result.output}
