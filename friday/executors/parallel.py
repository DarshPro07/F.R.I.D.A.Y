"""
friday/executors/parallel.py -- one packet, several runtimes, one integration
(FAB-002).

FRIDAY is the only supervisor. The same `WorkPacket` goes to each runtime in
its own isolated worktree, all at once; each returns a `Receipt`. The manager
then judges every receipt by what it can see itself (what changed on disk, the
packet's own acceptance tests, the budget) rather than by what the runtime
said, and integrates exactly one: the first qualifying receipt in the order
the caller gave the runtimes. The order is the caller's routing decision
(quality first, free first); this module has no opinion about it.

A runtime that is missing or signed out returns an UNAVAILABLE receipt and is
reported as such. Nothing here substitutes another runtime for it.
"""
from __future__ import annotations

import subprocess
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Protocol

from friday.executors.work_packet import (
    DONE, FAILED, OVER_BUDGET, UNAVAILABLE, Receipt, WorkPacket)
from friday.executors.worktrees import PROMOTED, Promotion, WorktreeError, WorktreeManager, git

#: How much of a failing acceptance test's output the receipt keeps.
CHECK_OUTPUT_CHARS = 500


class Runtime(Protocol):
    runtime: str

    def run(self, packet: WorkPacket, workspace: str) -> Receipt: ...


@dataclass
class Outcome:
    """Every receipt, and which one FRIDAY integrated (None when none could be)."""

    receipts: list[Receipt] = field(default_factory=list)
    integrated: Receipt | None = None
    promotion: Promotion | None = None

    def to_dict(self) -> dict:
        return {"integrated": self.integrated.runtime if self.integrated else None,
                "receipts": [r.to_dict() for r in self.receipts]}


def _changed_files(path) -> list[str]:
    """Every file the runtime changed, one path each. Untracked files are
    listed individually: a new directory must not stand in for its contents."""
    tracked = git(path, "diff", "--name-only", "HEAD").splitlines()
    new = git(path, "ls-files", "--others", "--exclude-standard").splitlines()
    return sorted({p.strip() for p in (*tracked, *new) if p.strip()})


def _run_check(argv: tuple[str, ...], cwd: str, timeout_s: float) -> dict:
    from friday import execution

    try:
        proc = subprocess.run(
            list(argv), cwd=cwd, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout_s,
            env=execution.worker_environment("acceptance"),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        passed, output = False, f"timed out after {timeout_s:.0f}s"
    except OSError as exc:
        passed, output = False, f"could not be run: {exc}"
    else:
        passed = proc.returncode == 0
        output = f"{proc.stdout or ''}{proc.stderr or ''}"[-CHECK_OUTPUT_CHARS:]
    return {"command": " ".join(argv), "passed": passed, "output": output}


def _judge(packet: WorkPacket, receipt: Receipt, path) -> None:
    """Hold a runtime's own DONE to what the manager can verify. A receipt can
    only go down from here: nothing a runtime says makes it DONE again."""
    if receipt.status == UNAVAILABLE:
        return
    receipt.files_changed = _changed_files(path)
    if receipt.status != DONE:
        return
    outside = [p for p in receipt.files_changed if not packet.in_scope(p)]
    if not receipt.files_changed:
        receipt.status = FAILED
        receipt.warnings.append("the runtime reported done but nothing changed on disk")
    elif outside:
        receipt.status = FAILED
        receipt.warnings.append("changed files outside the packet's allowed paths: "
                                + ", ".join(outside[:10]))
    elif receipt.cost_usd > packet.budget.usd:
        receipt.status = OVER_BUDGET
        receipt.warnings.append(
            f"cost ${receipt.cost_usd:.4f} exceeds the ${packet.budget.usd:.4f} budget")
    else:
        receipt.acceptance = [_run_check(argv, str(path), packet.budget.seconds)
                              for argv in packet.checks]
        failing = [r["command"] for r in receipt.acceptance if not r["passed"]]
        if failing:
            receipt.status = FAILED
            receipt.warnings.append("acceptance tests failed: " + "; ".join(failing))


def run_parallel(packet: WorkPacket, runtimes: list[Runtime], manager: WorktreeManager,
                 *, target: str = "", run_id: str = "") -> Outcome:
    """Run `packet` on every runtime in its own worktree, judge each receipt,
    and integrate the first qualifying one onto `target` (default: the branch
    the main checkout is on)."""
    names = [rt.runtime for rt in runtimes]
    if not runtimes or len(set(names)) != len(names):
        raise ValueError("run_parallel needs one or more distinct runtimes")
    run_id = run_id or f"fab-{uuid.uuid4().hex[:8]}"
    target = target or manager.current_branch()
    worktree_names = [f"{run_id}-{name}" for name in names]

    created = []
    try:
        for name in worktree_names:
            created.append(manager.create(name))
    except Exception:
        for made in created:
            manager.cleanup(made.name)
        raise

    def work(rt: Runtime, worktree) -> Receipt:
        try:
            receipt = rt.run(packet, str(worktree.path))
        except Exception as exc:  # a runtime that raises is a failed run, with its reason
            receipt = Receipt(rt.runtime, FAILED,
                              warnings=[f"{type(exc).__name__}: {exc}"])
        # A receipt is for the runtime that was asked, whatever it claims.
        receipt.runtime = rt.runtime
        receipt.workspace = str(worktree.path)
        return receipt

    with ThreadPoolExecutor(max_workers=len(runtimes)) as pool:
        receipts = list(pool.map(work, runtimes, created))
    for receipt, worktree in zip(receipts, created):
        _judge(packet, receipt, worktree.path)

    outcome = Outcome(receipts=receipts)
    for receipt, worktree in zip(receipts, created):
        if receipt.status != DONE:
            continue
        message = f"FAB-002: {packet.bundle.goal.strip()[:60]} ({receipt.runtime})"
        try:
            promotion = manager.promote(worktree.name, target=target, message=message)
        except WorktreeError as exc:
            receipt.warnings.append(f"not integrated: {exc}")
            continue
        if promotion.state == PROMOTED:
            receipt.commit = promotion.result_commit
            outcome.integrated, outcome.promotion = receipt, promotion
            break
        receipt.warnings.append(f"not integrated: {promotion.reason}")

    for receipt in receipts:
        if outcome.integrated is not None and receipt is not outcome.integrated \
                and receipt.status == DONE:
            receipt.warnings.append(
                f"not integrated: {outcome.integrated.runtime} was integrated instead")

    # Work that landed, or never happened, has nothing left to keep. Anything
    # else is the evidence for its receipt.
    for receipt, worktree in zip(receipts, created):
        landed = receipt.status == DONE and outcome.integrated is not None
        if receipt.status == UNAVAILABLE or landed:
            manager.cleanup(worktree.name)
            receipt.workspace = ""
    return outcome
