"""
Adversarial reasoning as capability-runtime implementations (FR-008,
FR-012). MCP faces live in `friday/tools/adversarial_control.py`.
"""
from __future__ import annotations

from friday import adversarial as A
from friday import contracts as c
from friday.policy import PolicyEngine, default_engine


def decision_deliberate(run: c.Run, question: str, evidence: list[str] | None = None,
                        options: list[str] | None = None, *,
                        engine: PolicyEngine = default_engine,
                        infer=None) -> c.ActionResult:
    """FR-008: five-role panel over one high-impact question. The output
    preserves disagreements, evidence and uncertainty (never a fabricated
    consensus)."""
    started = c.started(run.run_id, "decision.deliberate")
    if not (question or "").strip():
        return run.record(c.failed(started, "a question is required"))
    kwargs = {"objective_id": run.run_id}
    if infer is not None:
        kwargs["infer"] = infer
    decision = A.deliberate(question.strip(), list(evidence or []),
                            options=list(options or []) or None, **kwargs)
    if decision.verdict == "UNAVAILABLE":
        return run.record(c.failed(
            started, "the judge role was unavailable: " + decision.positions[A.JUDGE].error))
    unavailable = ", ".join(decision.roles_unavailable) or "none"
    return run.record(c.succeeded(
        started, output=decision.to_dict(),
        verification=c.Verification(
            method="adversarial_panel",
            evidence=(f"{len(A.ROLES) - len(decision.roles_unavailable)} of {len(A.ROLES)} "
                      f"roles answered (unavailable: {unavailable}); "
                      f"{len(decision.disagreements)} disagreement(s) kept; "
                      f"judge {decision.verdict} at confidence {decision.confidence}"))))


def change_review(run: c.Run, goal: str, claim: str, diff: str,
                  verifier_result: str = "", implemented_by: str = "worker",
                  changed_files: list[str] | None = None, *,
                  engine: PolicyEngine = default_engine,
                  infer=None) -> c.ActionResult:
    """FR-012: an independent reviewer over a diff and the worker's claim."""
    started = c.started(run.run_id, "change.review")
    change = A.ChangeUnderReview(
        goal=goal, claim=claim, diff=diff, verifier_result=verifier_result,
        implemented_by=implemented_by, changed_files=tuple(changed_files or ()))
    kwargs = {"objective_id": run.run_id}
    if infer is not None:
        kwargs["infer"] = infer
    review = A.independent_review(change, **kwargs)
    if review.error:
        return run.record(c.failed(started, f"reviewer unavailable: {review.error}"))
    return run.record(c.succeeded(
        started, output=review.to_dict(),
        verification=c.Verification(
            method="independent_review",
            evidence=(f"{review.verdict} by {review.reviewed_by}, independent of "
                      f"{review.independent_of}: {len(review.findings)} finding(s)"))))
