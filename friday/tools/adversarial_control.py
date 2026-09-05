"""
Adversarial reasoning tools (PRD v3.1 FR-008, FR-012).

`decision_deliberate` - contrarian decision mode for high-impact choices.
`change_review`       - an independent reviewer over a worker's change.

Both run bounded inferences through the Hermes MODEL_GATEWAY (no agent
loop); the implementations are in `friday/toolsets/adversarial.py`.
"""
from __future__ import annotations

from friday import contracts as c
from friday.toolsets import adversarial as AT


def register(mcp):

    @mcp.tool()
    def decision_deliberate(question: str, evidence: list[str] | None = None,
                            options: list[str] | None = None) -> dict:
        """
        Contrarian decision mode for a high-impact choice: a proposer, a
        contrarian, a failure analyst, an evidence checker and a judge each
        take a position. The result keeps every disagreement, which evidence
        was actually supported, what is missing, and the judge's stated
        uncertainty - it never manufactures consensus.

        Use for strategy decisions, architecture choices and anything
        expensive to reverse. Not for simple questions (five inferences).
        """
        run = c.Run.create(question[:80], capability="decision_deliberate")
        result = AT.decision_deliberate(run, question, evidence, options)
        if result.status == c.FAILED:
            return {"status": "failed", "error": result.error}
        d = result.output
        return {"status": "ok", **d,
                "say": (f"judge says {d['verdict'].lower()} at confidence {d['confidence']}"
                        + (f"; {len(d['disagreements'])} disagreement(s) stand"
                           if d["disagreements"] else "; no standing disagreement"))}

    @mcp.tool()
    def change_review(goal: str, claim: str, diff: str, verifier_result: str = "",
                      implemented_by: str = "worker",
                      changed_files: list[str] | None = None) -> dict:
        """
        Independent review of a change by a reviewer that is not the worker
        who made it: reads the diff, the worker's claim and the objective
        verifier's result, and returns CONFIRMED, DISPUTED or INCONCLUSIVE
        with concrete findings. The promotion gate refuses a disputed or
        unreviewed consequential change.
        """
        run = c.Run.create(goal[:80], capability="change_review")
        result = AT.change_review(run, goal, claim, diff, verifier_result,
                                  implemented_by, changed_files)
        if result.status == c.FAILED:
            return {"status": "failed", "error": result.error}
        r = result.output
        return {"status": "ok", **r,
                "say": f"{r['verdict'].lower()} with {len(r['findings'])} finding(s)"}
