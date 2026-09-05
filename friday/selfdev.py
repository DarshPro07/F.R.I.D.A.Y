"""
Controlled self-development (PRD v3.1 FR-047, FR-048, FR-049, FR-050, FR-051).

The loop the PRD names, as a state machine whose order is enforced by
code rather than by discipline:

    OBSERVE -> PROPOSE -> SANDBOX -> IMPLEMENT -> TEST -> REVIEW ->
    REGRESSION -> BENCHMARK -> PROMOTE -> MONITOR -> [ROLLBACK]

  * SANDBOX (FR-048): the change is made in a git worktree on its own
    branch (`friday.executors.worktrees`), never in the live checkout.
    The main runtime's files do not move until PROMOTE merges.
  * TEST + REGRESSION (FR-049): the touched subsystem's tests and a
    regression baseline run INSIDE the sandbox; a failure is a rejection,
    and `promote()` cannot be reached without a passing gate (the state
    machine refuses).
  * REVIEW (FR-012): an independent reviewer reads the sandbox diff.
  * BENCHMARK (FR-050): optional before/after measurement; a regression
    past the tolerance rejects.
  * PROMOTE: `WorktreeManager.promote` - a --no-ff merge whose commit is
    the rollback handle.
  * ROLLBACK (FR-051): `git revert -m 1 <merge>` - deterministic, leaves
    history, restores the prior known-good tree. `monitor()` runs a
    health probe after promotion and rolls back automatically on failure.
  * Kernel surfaces (`self_upgrade.KERNEL_PATHS`: policy, netguard,
    sensitive domains, this loop's own guards) are refused at PROPOSE, so
    security boundaries cannot be self-modified.
  * Every transition is an audit row (FR-065) and a journal line.
"""
from __future__ import annotations

import json
import logging
import subprocess
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Callable

from friday import self_upgrade as SU
from friday.executors import worktrees as WT

logger = logging.getLogger("friday.selfdev")

OBSERVED = "OBSERVED"
PROPOSED = "PROPOSED"
SANDBOXED = "SANDBOXED"
IMPLEMENTED = "IMPLEMENTED"
TESTED = "TESTED"
REVIEWED = "REVIEWED"
REGRESSION_PASSED = "REGRESSION_PASSED"
BENCHMARKED = "BENCHMARKED"
PROMOTED = "PROMOTED"
MONITORED = "MONITORED"
ROLLED_BACK = "ROLLED_BACK"
REJECTED = "REJECTED"
STATES = (OBSERVED, PROPOSED, SANDBOXED, IMPLEMENTED, TESTED, REVIEWED,
          REGRESSION_PASSED, BENCHMARKED, PROMOTED, MONITORED, ROLLED_BACK, REJECTED)

#: The only legal predecessor of each transition. `promote()` requires
#: BENCHMARKED, which requires REGRESSION_PASSED, which requires REVIEWED,
#: which requires TESTED: there is no path to PROMOTED around the gates.
_REQUIRES = {
    PROPOSED: (OBSERVED,),
    SANDBOXED: (PROPOSED,),
    IMPLEMENTED: (SANDBOXED,),
    TESTED: (IMPLEMENTED,),
    REVIEWED: (TESTED,),
    REGRESSION_PASSED: (REVIEWED,),
    BENCHMARKED: (REGRESSION_PASSED,),
    PROMOTED: (BENCHMARKED,),
    MONITORED: (PROMOTED,),
    ROLLED_BACK: (PROMOTED, MONITORED),
}

#: Benchmarks may regress by at most this fraction before promotion is refused.
BENCHMARK_TOLERANCE = 0.10

PYTEST_BASE = ["-m", "pytest", "-q", "-p", "no:cacheprovider", "-m", "not live and not slow"]


class GateRefused(RuntimeError):
    """A transition was attempted out of order or past a failed gate."""


@dataclass
class Candidate:
    """FR-047: an improvement candidate, linked to measured evidence."""

    id: str
    weakness: str
    evidence: dict                      # measured, e.g. {"failure_rate": 0.4, "samples": 10}
    proposal: str = ""
    files: tuple[str, ...] = ()
    tests: tuple[str, ...] = ()         # the touched subsystem's tests
    regression: tuple[str, ...] = ()    # the regression baseline
    state: str = OBSERVED
    worktree: str = ""
    sandbox_path: str = ""
    base_commit: str = ""
    diff_summary: str = ""
    review: dict | None = None
    test_log: str = ""
    regression_log: str = ""
    benchmark: dict | None = None
    promotion: dict | None = None
    history: list[dict] = field(default_factory=list)
    rejected_because: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class SelfDevelopment:
    """One candidate through the loop. `runner` executes a test command in a
    working directory and returns (passed, tail) - injectable so the gate
    logic is tested without spending minutes of pytest per case."""

    def __init__(self, repo: str | Path, *, python: str | Path | None = None,
                 journal: str | Path | None = None,
                 runner: Callable[[list[str], str], tuple[bool, str]] | None = None) -> None:
        self.repo = Path(repo).resolve()
        self.python = str(python or (self.repo / ".venv-verify" / "Scripts" / "python.exe"))
        self.journal_path = Path(journal) if journal else self.repo / "data" / "selfdev_journal.jsonl"
        self.manager = WT.WorktreeManager(self.repo)
        self._runner = runner or self._pytest

    # -- bookkeeping -------------------------------------------------------

    def _transition(self, cand: Candidate, to: str, **detail) -> None:
        allowed = _REQUIRES.get(to)
        if allowed is not None and cand.state not in allowed:
            raise GateRefused(f"{cand.id}: cannot go {cand.state} -> {to}; "
                              f"requires {' or '.join(allowed)}")
        cand.history.append({"at": time.time(), "from": cand.state, "to": to, **detail})
        cand.state = to
        self._journal(cand, to, **detail)
        self._audit(cand, to, detail)

    def _reject(self, cand: Candidate, why: str, **detail) -> Candidate:
        cand.rejected_because = why
        cand.history.append({"at": time.time(), "from": cand.state, "to": REJECTED,
                             "why": why, **detail})
        cand.state = REJECTED
        self._journal(cand, REJECTED, why=why, **detail)
        self._audit(cand, REJECTED, {"why": why, **detail})
        return cand

    def _journal(self, cand: Candidate, step: str, **detail) -> None:
        try:
            self.journal_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.journal_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"at": time.time(), "candidate": cand.id, "step": step,
                                     **{k: str(v)[:500] for k, v in detail.items()}}) + "\n")
        except OSError:
            logger.exception("selfdev journal write failed")

    def _audit(self, cand: Candidate, step: str, detail: dict) -> None:
        try:
            from friday import trust as T
            T.audit().record(actor="friday:selfdev", action=f"selfdev.{step.lower()}",
                             target=cand.worktree or cand.id, tier=T.R3,
                             decision="TRANSITION" if step != REJECTED else "REJECT",
                             result=str(detail.get("why", ""))[:200],
                             objective_id=cand.id,
                             detail={k: str(v)[:300] for k, v in detail.items()})
        except Exception:  # noqa: BLE001 - audit never blocks the loop
            logger.exception("selfdev audit write failed")

    def _pytest(self, tests: list[str], cwd: str) -> tuple[bool, str]:
        result = subprocess.run([self.python, *PYTEST_BASE, *tests], cwd=cwd,
                                capture_output=True, text=True, timeout=1800,
                                encoding="utf-8", errors="replace")
        tail = "\n".join((result.stdout or "").splitlines()[-3:])
        return result.returncode == 0, tail

    # -- the loop ----------------------------------------------------------

    def observe(self, candidate_id: str, weakness: str, evidence: dict) -> Candidate:
        """FR-047: a candidate exists only with measured evidence attached."""
        if not evidence or not any(isinstance(v, (int, float)) for v in evidence.values()):
            raise GateRefused("a candidate needs at least one measured number as evidence, "
                              "not a feeling that something could be better")
        cand = Candidate(id=candidate_id, weakness=weakness, evidence=dict(evidence))
        self._journal(cand, OBSERVED, weakness=weakness, evidence=evidence)
        return cand

    def propose(self, cand: Candidate, proposal: str, files: list[str], *,
                tests: list[str], regression: list[str]) -> Candidate:
        """Kernel surfaces are refused here, before a sandbox exists."""
        try:
            SU.SelfUpgrade(self.repo, journal=self.journal_path).guard_kernel(list(files))
        except SU.UpgradeRefused as exc:
            return self._reject(cand, f"kernel surface: {exc}")
        if not tests:
            return self._reject(cand, "a proposal must name the touched subsystem's tests")
        cand.proposal, cand.files = proposal, tuple(files)
        cand.tests, cand.regression = tuple(tests), tuple(regression)
        self._transition(cand, PROPOSED, files=list(files), tests=list(tests))
        return cand

    def sandbox(self, cand: Candidate) -> Candidate:
        """FR-048: a worktree on its own branch. The live checkout is untouched."""
        name = f"selfdev-{cand.id}"
        try:
            tree = self.manager.create(name)
        except WT.WorktreeError as exc:
            return self._reject(cand, f"could not create sandbox: {exc}")
        cand.worktree, cand.sandbox_path, cand.base_commit = name, str(tree.path), tree.base_commit
        self._transition(cand, SANDBOXED, path=str(tree.path), base=tree.base_commit)
        return cand

    def implement(self, cand: Candidate, apply_change: Callable[[Path], None]) -> Candidate:
        """`apply_change(sandbox_root)` edits files INSIDE the sandbox. A
        change that touches the live checkout, or a file outside the
        proposal, is rejected by the diff check."""
        if cand.state != SANDBOXED:
            raise GateRefused(f"{cand.id}: implement needs SANDBOXED, is {cand.state}")
        root = Path(cand.sandbox_path)
        try:
            apply_change(root)
        except Exception as exc:  # noqa: BLE001
            return self._reject(cand, f"apply_change raised: {exc}")
        changed = self.manager.changes(cand.worktree)
        if not changed:
            return self._reject(cand, "the change touched no files in the sandbox")
        outside = [f for f in changed if f.replace("\\", "/") not in
                   {p.replace("\\", "/") for p in cand.files}]
        if outside:
            return self._reject(cand, f"changed files outside the proposal: {outside}")
        live = [f for f in cand.files if (self.repo / f).exists()
                and (root / f).exists()
                and (self.repo / f).read_bytes() != (root / f).read_bytes()
                and _live_differs_from_base(self.repo, f)]
        if live:
            return self._reject(cand, f"the live checkout moved during implementation: {live}")
        cand.diff_summary = ", ".join(changed)
        self._transition(cand, IMPLEMENTED, changed=changed)
        return cand

    def test(self, cand: Candidate) -> Candidate:
        """FR-049 first half: the touched subsystem's tests, in the sandbox."""
        if cand.state != IMPLEMENTED:
            raise GateRefused(f"{cand.id}: test needs IMPLEMENTED, is {cand.state}")
        passed, tail = self._runner(list(cand.tests), cand.sandbox_path)
        cand.test_log = tail
        if not passed:
            return self._reject(cand, "subsystem tests failed", tail=tail)
        self._transition(cand, TESTED, tail=tail)
        return cand

    def review(self, cand: Candidate, *, infer=None) -> Candidate:
        """FR-012: an independent reviewer over the sandbox diff."""
        if cand.state != TESTED:
            raise GateRefused(f"{cand.id}: review needs TESTED, is {cand.state}")
        from friday import adversarial as A
        diff = WT.git(Path(cand.sandbox_path), "diff", "--no-color", check=False)
        change = A.ChangeUnderReview(
            goal=cand.proposal, claim=f"{cand.proposal} (addresses: {cand.weakness})",
            diff=diff, verifier_result=cand.test_log, implemented_by="friday:selfdev",
            changed_files=cand.files)
        kwargs = {"objective_id": cand.id, "reviewer": "reviewer:model_gateway"}
        if infer is not None:
            kwargs["infer"] = infer
        evidence = A.independent_review(change, **kwargs)
        cand.review = evidence.to_dict()
        if evidence.verdict == A.DISPUTED:
            return self._reject(cand, "independent review disputed the change",
                                findings=list(evidence.findings))
        if evidence.verdict == A.INCONCLUSIVE:
            return self._reject(cand, "independent review was inconclusive",
                                error=evidence.error)
        self._transition(cand, REVIEWED, verdict=evidence.verdict)
        return cand

    def regression(self, cand: Candidate) -> Candidate:
        """FR-049 second half: the regression baseline, in the sandbox."""
        if cand.state != REVIEWED:
            raise GateRefused(f"{cand.id}: regression needs REVIEWED, is {cand.state}")
        if cand.regression:
            passed, tail = self._runner(list(cand.regression), cand.sandbox_path)
            cand.regression_log = tail
            if not passed:
                return self._reject(cand, "regression baseline failed", tail=tail)
        else:
            tail = "no regression baseline named; subsystem tests stand alone"
        self._transition(cand, REGRESSION_PASSED, tail=tail)
        return cand

    def benchmark(self, cand: Candidate, *, before: dict | None = None,
                  measure: Callable[[Path], dict] | None = None,
                  lower_is_better: tuple[str, ...] = ()) -> Candidate:
        """FR-050: compare `measure(sandbox)` against `before`. Any metric
        that regresses past BENCHMARK_TOLERANCE rejects. With no measure
        (a change that has no performance claim) the step records that."""
        if cand.state != REGRESSION_PASSED:
            raise GateRefused(f"{cand.id}: benchmark needs REGRESSION_PASSED, is {cand.state}")
        if measure is None:
            cand.benchmark = {"skipped": "no performance claim"}
            self._transition(cand, BENCHMARKED, skipped=True)
            return cand
        after = measure(Path(cand.sandbox_path))
        before = before or {}
        regressions = []
        for key, new in after.items():
            old = before.get(key)
            if not isinstance(old, (int, float)) or not isinstance(new, (int, float)) or old == 0:
                continue
            worse = (new > old * (1 + BENCHMARK_TOLERANCE)) if key in lower_is_better \
                else (new < old * (1 - BENCHMARK_TOLERANCE))
            if worse:
                regressions.append(f"{key}: {old} -> {new}")
        cand.benchmark = {"before": before, "after": after, "regressions": regressions}
        if regressions:
            return self._reject(cand, "benchmark regressed", regressions=regressions)
        self._transition(cand, BENCHMARKED, before=before, after=after)
        return cand

    def promote(self, cand: Candidate, *, approved: bool) -> Candidate:
        """The only way into the live checkout. Requires every gate above
        (enforced by `_REQUIRES`) and a person's yes."""
        if cand.state != BENCHMARKED:
            raise GateRefused(f"{cand.id}: promote needs BENCHMARKED, is {cand.state}; "
                              f"there is no path around the gates")
        if not approved:
            return self._reject(cand, "not approved: a self-change lands only on a yes")
        target = self.manager.current_branch()
        try:
            promotion = self.manager.promote(cand.worktree, target=target,
                                             message=f"selfdev {cand.id}: {cand.proposal}")
        except WT.WorktreeError as exc:
            return self._reject(cand, f"promotion failed: {exc}")
        cand.promotion = asdict(promotion)
        if promotion.state != WT.PROMOTED:
            return self._reject(cand, f"promotion rejected: {promotion.reason}")
        self._transition(cand, PROMOTED, merge=promotion.merge_commit,
                         rollback_target=promotion.rollback_target)
        return cand

    def monitor(self, cand: Candidate, health: Callable[[], bool]) -> Candidate:
        """Post-promotion health probe. Unhealthy -> automatic rollback."""
        if cand.state != PROMOTED:
            raise GateRefused(f"{cand.id}: monitor needs PROMOTED, is {cand.state}")
        try:
            healthy = bool(health())
        except Exception as exc:  # noqa: BLE001
            healthy = False
            cand.history.append({"at": time.time(), "monitor_error": str(exc)[:300]})
        if not healthy:
            return self.rollback(cand, reason="post-promotion health probe failed")
        self._transition(cand, MONITORED, healthy=True)
        return cand

    def rollback(self, cand: Candidate, *, reason: str) -> Candidate:
        """FR-051: deterministic. `git revert -m 1 <merge>`; the prior
        known-good tree is restored and the history says why."""
        if cand.state not in (PROMOTED, MONITORED) or not cand.promotion:
            raise GateRefused(f"{cand.id}: nothing promoted to roll back (state {cand.state})")
        promotion = WT.Promotion(**cand.promotion)
        undone = self.manager.rollback(promotion, reason=reason)
        cand.promotion = asdict(undone)
        self._transition(cand, ROLLED_BACK, reason=reason, revert=undone.merge_commit,
                         restored=promotion.rollback_target)
        return cand

    def cleanup(self, cand: Candidate) -> dict:
        """Remove the sandbox. Rejected candidates keep theirs: the
        worktree is the evidence."""
        if not cand.worktree:
            return {"worktree": "", "kept": False, "removed": False}
        return self.manager.cleanup(cand.worktree, keep=(cand.state == REJECTED))


def _live_differs_from_base(repo: Path, relpath: str) -> bool:
    """Whether the live checkout's copy of `relpath` differs from HEAD -
    i.e. somebody edited the live file while the sandbox was open."""
    out = subprocess.run(["git", "-C", str(repo), "status", "--porcelain", "--", relpath],
                         capture_output=True, text=True, timeout=30)
    return bool(out.stdout.strip())
