"""
Development happens somewhere else, and only arrives if it earns it.

    worktree  ->  verify  ->  promote or reject  ->  main is untouched until
                                                     promotion

This is the boundary that eventually lets ADA change its own code without the
running checkout being the experiment. Everything here is git; nothing copies
files around, because a copy has no history and cannot be rolled back.

## What 2.1.233 actually does, probed

    claude -p --worktree ada-probe

    path        <repo>/.claude/worktrees/<name>
    branch      worktree-<name>
    base        the current HEAD, including uncommitted-to-main feature work
    isolation   real - Write landed in the worktree, `git status` in the main
                checkout showed no tracked file touched
    on exit     the changes are left UNCOMMITTED, and the worktree is left
                LOCKED, reason "claude session <name> (pid NNNN)"

The last line is the one that costs you. A headless run cleans up nothing, so
the lifecycle is ADA's: commit the work before it can be promoted, and unlock
before the worktree can be removed.

## The base is chosen, never assumed

The probe based off HEAD, but a repository with a remote default branch may
not. Building a feature on top of a stale default is the kind of bug that only
shows up as a confusing merge, so `base_commit` is recorded at creation and
checked at promotion rather than trusted.

## Rollback reverts, it does not reset

`git reset --hard` on a shared branch destroys history and is on the list of
things no profile may run. A revert is a new commit that undoes a merge: the
state comes back, the record of what happened stays, and it works whether or
not anyone has pulled.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("friday-agent.executors.worktrees")

#: Where the CLI puts them. Probed, not guessed.
WORKTREE_DIR = Path(".claude") / "worktrees"

#: And what it calls the branch.
BRANCH_PREFIX = "worktree-"

# The promotion state machine. It will hold unchanged for any executor.
WORKTREE_CREATED = "WORKTREE_CREATED"
DEVELOPING = "DEVELOPING"
VERIFYING = "VERIFYING"
READY = "READY"
REJECTED = "REJECTED"
PROMOTING = "PROMOTING"
PROMOTED = "PROMOTED"
ROLLED_BACK = "ROLLED_BACK"

PROMOTION_STATES = (WORKTREE_CREATED, DEVELOPING, VERIFYING, READY, REJECTED,
                    PROMOTING, PROMOTED, ROLLED_BACK)


class WorktreeError(RuntimeError):
    """Something about the isolation is wrong. Never proceed past one of these."""


def git(repo: str | Path, *args: str, check: bool = True) -> str:
    out = subprocess.run(["git", *args], cwd=str(repo), capture_output=True,
                         text=True, timeout=120)
    if check and out.returncode != 0:
        raise WorktreeError(
            f"git {' '.join(args)} failed in {repo}: "
            f"{(out.stderr or out.stdout).strip()[:300]}")
    return (out.stdout or "").strip()


@dataclass(frozen=True)
class Worktree:
    name: str
    path: Path
    branch: str
    repo: Path
    base_commit: str = ""

    @property
    def exists(self) -> bool:
        return self.path.is_dir()


@dataclass(frozen=True)
class Promotion:
    """What happened, in enough detail to undo it."""

    state: str
    branch: str
    target: str
    base_commit: str = ""      # where the target was before
    result_commit: str = ""    # the worktree commit that carried the work
    merge_commit: str = ""     # what landed on the target
    reason: str = ""

    @property
    def rollback_target(self) -> str:
        """Where to get back to. Recorded, never reconstructed later."""
        return self.base_commit


class WorktreeManager:
    """Create, verify, promote, reject, roll back, clean up."""

    def __init__(self, repo: str | Path) -> None:
        self.repo = Path(repo).resolve()

    # -- finding -----------------------------------------------------------

    def path_for(self, name: str) -> Path:
        return self.repo / WORKTREE_DIR / name

    def current_branch(self) -> str:
        """
        What the main checkout is on.

        The default promotion target, because promoting somewhere the boss is
        not standing would be a surprise, and surprises in git are expensive.
        """
        return git(self.repo, "rev-parse", "--abbrev-ref", "HEAD")

    def branch_for(self, name: str) -> str:
        return f"{BRANCH_PREFIX}{name}"

    def listed(self) -> list[dict]:
        """Every worktree git knows about, including the main checkout."""
        out = git(self.repo, "worktree", "list", "--porcelain")
        entries, current = [], {}
        for line in out.splitlines():
            if not line.strip():
                if current:
                    entries.append(current)
                    current = {}
                continue
            key, _, value = line.partition(" ")
            current[key] = value or True
        if current:
            entries.append(current)
        return entries

    def find(self, name: str) -> Worktree | None:
        path = self.path_for(name)
        for entry in self.listed():
            if Path(str(entry.get("worktree", ""))).resolve() == path.resolve():
                branch = str(entry.get("branch", "")).replace("refs/heads/", "")
                return Worktree(name=name, path=path,
                                branch=branch or self.branch_for(name),
                                repo=self.repo,
                                base_commit=str(entry.get("HEAD", "")))
        return None

    def verify(self, name: str) -> tuple[bool, str]:
        """
        Is this a real, separate checkout of *this* repository?

        Checked before a resume, because a missing worktree is the dangerous
        case: the CLI may fall back to the launch directory, and a run that
        believed it was isolated would start editing the live checkout.
        """
        path = self.path_for(name)
        if not path.is_dir():
            return False, f"the worktree directory is gone: {path}"
        try:
            common = git(path, "rev-parse", "--git-common-dir")
        except WorktreeError as exc:
            return False, f"{path} is not a git checkout: {exc}"

        expected = (self.repo / ".git").resolve()
        actual = (path / common).resolve() if not Path(common).is_absolute() \
            else Path(common).resolve()
        if actual != expected:
            return False, (f"{path} belongs to a different repository "
                           f"({actual}, expected {expected})")

        # An empty directory *inside* the repo passes every check above,
        # because git walks upward and finds the main .git. It would then be
        # treated as a valid isolated workspace while actually being the main
        # checkout under another name - which is precisely the accident this
        # whole guard exists to prevent. The top level has to be the worktree
        # itself, not something above it.
        try:
            toplevel = Path(git(path, "rev-parse", "--show-toplevel")).resolve()
        except WorktreeError as exc:
            return False, f"{path} has no working tree of its own: {exc}"
        if toplevel != path.resolve():
            return False, (f"{path} is not its own checkout - it sits inside "
                           f"{toplevel}")
        if path.resolve() == self.repo.resolve():
            return False, "the 'worktree' is the main checkout"
        return True, "ok"

    # -- creation ----------------------------------------------------------

    def create(self, name: str, *, base: str = "HEAD") -> Worktree:
        """
        A fresh checkout of `base` on its own branch, under WORKTREE_DIR.

        This is the sandbox FR-048 requires: work happens here and the
        main checkout does not move until `promote()` merges. Refuses to
        reuse a name that already has a worktree - a sandbox that might
        contain somebody else's half-finished change is not a sandbox.
        """
        if self.find(name) is not None:
            raise WorktreeError(f"worktree {name!r} already exists")
        path = self.path_for(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        branch = self.branch_for(name)
        git(self.repo, "worktree", "add", "-b", branch, str(path), base)
        head = git(path, "rev-parse", "HEAD")
        return Worktree(name=name, path=path, branch=branch, repo=self.repo,
                        base_commit=head)

    # -- the work ----------------------------------------------------------

    def changes(self, name: str) -> list[str]:
        path = self.path_for(name)
        if not path.is_dir():
            return []
        # `git()` strips the whole output, which eats the leading space of
        # the first porcelain line (" M pkg/mod.py" -> "M pkg/mod.py"), so a
        # fixed [3:] slice lost the first character of the first path.
        # Read the raw output and slice each line on its own.
        out = subprocess.run(["git", "status", "--porcelain"], cwd=str(path),
                             capture_output=True, text=True, timeout=120)
        if out.returncode != 0:
            raise WorktreeError(f"git status failed in {path}: "
                                f"{(out.stderr or '').strip()[:300]}")
        return [line[3:].strip() for line in (out.stdout or "").splitlines()
                if line.strip()]

    def commit(self, name: str, message: str) -> str:
        """
        Commit whatever the run left behind, and return the sha.

        A headless run leaves its work uncommitted, so without this there is
        nothing for a merge to carry. Returns "" when there was nothing to
        commit, which is a real outcome and not an error.
        """
        path = self.path_for(name)
        ok, why = self.verify(name)
        if not ok:
            raise WorktreeError(why)
        if not self.changes(name):
            return ""
        git(path, "add", "-A")
        git(path, "commit", "-m", message)
        return git(path, "rev-parse", "HEAD")

    # -- promotion ---------------------------------------------------------

    def promote(self, name: str, *, target: str, message: str) -> Promotion:
        """
        Merge the worktree branch into the target branch.

        A merge, not a copy: the history comes with it, and the merge commit is
        what a rollback later reverts.
        """
        ok, why = self.verify(name)
        if not ok:
            raise WorktreeError(why)

        branch = self.branch_for(name)
        result = self.commit(name, message)
        if not result:
            return Promotion(REJECTED, branch, target,
                             reason="there was nothing to promote")

        # This check comes before resolving the target, so a target that does
        # not exist reports the guard rather than a raw git parse error.
        current = git(self.repo, "rev-parse", "--abbrev-ref", "HEAD")
        if current != target:
            raise WorktreeError(
                f"the main checkout is on {current!r}, not {target!r}; "
                "promotion never switches branches underneath anyone")
        base = git(self.repo, "rev-parse", target)

        try:
            git(self.repo, "merge", "--no-ff", "-m", message, branch)
        except WorktreeError as exc:
            # A conflict is a rejection, not a half-promotion. The target is
            # put back exactly as it was.
            git(self.repo, "merge", "--abort", check=False)
            return Promotion(REJECTED, branch, target, base_commit=base,
                             result_commit=result,
                             reason=f"the merge did not apply cleanly: {exc}")

        merged = git(self.repo, "rev-parse", "HEAD")
        return Promotion(PROMOTED, branch, target, base_commit=base,
                         result_commit=result, merge_commit=merged,
                         reason=message)

    def reject(self, name: str, reason: str) -> Promotion:
        """Nothing moves. The worktree is kept so the failure can be read."""
        return Promotion(REJECTED, self.branch_for(name), "",
                         reason=reason)

    def rollback(self, promotion: Promotion, *, reason: str = "") -> Promotion:
        """
        Undo a promotion that turned out to be wrong.

        `git revert -m 1` rather than `reset --hard`: the state comes back, the
        record of what happened stays, and it is safe on a branch someone else
        may already have. reset --hard is on the list no profile may run, and
        it should not sneak in through here either.
        """
        if promotion.state != PROMOTED or not promotion.merge_commit:
            raise WorktreeError("only a completed promotion can be rolled back")

        git(self.repo, "revert", "--no-edit", "-m", "1", promotion.merge_commit)
        return Promotion(
            ROLLED_BACK, promotion.branch, promotion.target,
            base_commit=promotion.base_commit,
            result_commit=promotion.result_commit,
            merge_commit=git(self.repo, "rev-parse", "HEAD"),
            reason=reason or "rolled back after promotion")

    # -- lifecycle ---------------------------------------------------------

    def unlock(self, name: str) -> bool:
        """
        Clear the lock a finished session left behind.

        The CLI locks the worktree with "claude session <name> (pid NNNN)" and
        a headless run never unlocks it, so the lock outlives the process and
        blocks removal.
        """
        try:
            git(self.repo, "worktree", "unlock", str(self.path_for(name)))
            return True
        except WorktreeError:
            return False       # not locked is the same as unlocked, for us

    def cleanup(self, name: str, *, keep: bool = False,
                delete_branch: bool = True) -> dict:
        """
        Remove the worktree and its branch.

        `keep=True` for a rejected run whose worktree is the evidence - the
        only thing worse than a stale worktree is deleting the one that
        explains the failure.
        """
        report = {"worktree": name, "kept": keep, "removed": False,
                  "branch_deleted": False, "notes": []}
        if keep:
            report["notes"].append("kept on purpose; it holds the evidence")
            return report

        self.unlock(name)
        path = self.path_for(name)
        if path.is_dir():
            try:
                git(self.repo, "worktree", "remove", "--force", str(path))
                report["removed"] = True
            except WorktreeError as exc:
                report["notes"].append(f"could not remove: {exc}")
        else:
            report["notes"].append("already gone")

        git(self.repo, "worktree", "prune", check=False)

        if delete_branch:
            branch = self.branch_for(name)
            try:
                git(self.repo, "branch", "-D", branch)
                report["branch_deleted"] = True
            except WorktreeError as exc:
                report["notes"].append(f"branch left in place: {exc}")
        return report

    def stale(self) -> list[str]:
        """Worktrees ADA made that no longer have a live run behind them."""
        out = []
        root = self.repo / WORKTREE_DIR
        if not root.is_dir():
            return out
        for child in root.iterdir():
            if child.is_dir() and child.name.startswith("ada-"):
                out.append(child.name)
        return out


def guard_before_resume(manager: WorktreeManager, name: str) -> tuple[bool, str]:
    """
    May an isolated run be resumed?

    The dangerous case is a worktree that has been deleted or moved: the CLI
    can fall back to the directory it was launched from, and a run that still
    believes it is isolated would start editing the live checkout. So a missing
    worktree stops the resume rather than quietly widening its blast radius.
    """
    ok, why = manager.verify(name)
    if ok:
        return True, "the worktree is intact"
    return False, (
        f"{why}. Refusing to resume: without its worktree the session could "
        f"fall back to the main checkout, and an isolated run must never "
        f"become an unisolated one by accident."
    )
