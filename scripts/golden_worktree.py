#!/usr/bin/env python3
"""
Isolate, verify, promote or reject, roll back, clean up.

The boundary that eventually lets ADA change its own code without the running
checkout being the experiment. Real Claude Code runs, real git, throwaway repo.

    python scripts/golden_worktree.py

Costs real subscription usage.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from friday import contracts as c  # noqa: E402
from friday.executors import brokers as B  # noqa: E402
from friday.executors import cli  # noqa: E402
from friday.executors import worktrees as W  # noqa: E402
from friday.executors.claude_code import ClaudeCodeExecutor, TaskBundle  # noqa: E402
from friday.store import Store  # noqa: E402


def check(passed: bool, message: str) -> bool:
    print(f"  [{'PASS' if passed else 'FAIL'}] {message}")
    return passed


def git(cwd, *args) -> str:
    out = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                         text=True)
    return (out.stdout or "").strip()


def tracked_changes(repo: Path) -> list[str]:
    """Untracked .claude/ is the worktree itself; it is not a change to main."""
    return [line for line in git(repo, "status", "--porcelain").splitlines()
            if line.strip() and not line.strip().startswith("??")]


def make_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "calc.py").write_text(
        "def add(a, b):\n    return a + b\n", encoding="utf-8")
    (path / "README.md").write_text("# Calc\n", encoding="utf-8")
    for argv in (["git", "init", "-q"],
                 ["git", "config", "user.email", "ada@example.com"],
                 ["git", "config", "user.name", "ADA"],
                 ["git", "add", "-A"],
                 ["git", "commit", "-q", "-m", "seed"]):
        subprocess.run(argv, cwd=path, check=True, capture_output=True)


async def isolate_and_promote(store: Store, repo: Path) -> list[bool]:
    print("#" * 70)
    print("# ISOLATION -> VERIFY -> PROMOTE")
    print("#" * 70 + "\n")

    results: list[bool] = []
    bundle = TaskBundle(
        goal="Add a multiply(a, b) function to calc.py with a docstring, and a "
             "test_calc.py with a pytest test for it.",
        workspace=str(repo), project="calc", isolate=True,
        run_id="DEV-promote", acceptance=("multiply exists", "a test exists"))

    manager = W.WorktreeManager(repo)
    ex = ClaudeCodeExecutor(store)
    before = git(repo, "rev-parse", "HEAD")

    started = time.monotonic()
    result = await ex.execute(bundle, profile=B.BUILD, timeout=900)
    took = time.monotonic() - started

    name = bundle.worktree_name()
    output = result.output or {}
    print(f"  {took:.0f}s  status={result.status}")
    print(f"  worktree : {manager.path_for(name)}")
    print(f"  changed  : {output.get('changed_files')}")
    print(f"  in       : {output.get('changed_in')}\n")

    results += [
        check(result.status == c.SUCCEEDED, "the isolated run succeeded"),
        check(manager.verify(name)[0], "the worktree is a real separate checkout"),
        check((manager.path_for(name) / "calc.py").exists(),
              "the work is in the worktree"),
        check(not tracked_changes(repo),
              "the MAIN checkout has no modified tracked files"),
        check("multiply" not in (repo / "calc.py").read_text(encoding="utf-8"),
              "main's calc.py does not have the change yet"),
        check(git(repo, "rev-parse", "HEAD") == before,
              "main's HEAD has not moved"),
    ]

    # Verification is ADA's, from the worktree, not from what Claude said.
    print("  --- verifying in the worktree ---")
    tests = subprocess.run(
        [str(ROOT / ".venv" / "Scripts" / "python.exe"), "-m", "pytest", "-q"],
        cwd=str(manager.path_for(name)), capture_output=True, text=True,
        timeout=300)
    passed = tests.returncode == 0
    print(f"  pytest rc={tests.returncode}: "
          f"{(tests.stdout or '').strip().splitlines()[-1] if tests.stdout else ''}\n")
    results.append(check(passed, "the worktree's own tests pass"))

    if not passed:
        print("  tests failed - rejecting instead of promoting\n")
        ex.reject(bundle, "the worktree's tests did not pass")
        return results

    print("  --- promoting ---")
    promotion = ex.promote(bundle, message="ADA DEV-promote: add multiply")
    print(f"  state    : {promotion.state}")
    print(f"  target   : {promotion.target}")
    print(f"  base     : {promotion.base_commit[:12]}")
    print(f"  merge    : {promotion.merge_commit[:12]}\n")

    row = store.latest_promotion("DEV-promote")
    results += [
        check(promotion.state == W.PROMOTED, "the promotion completed"),
        check("multiply" in (repo / "calc.py").read_text(encoding="utf-8"),
              "MAIN now has the change"),
        check(git(repo, "rev-parse", "HEAD") != before, "main's HEAD moved"),
        check(row is not None and row["rollback_target"] == before,
              "the rollback point was persisted, not left to be worked out"),
    ]

    print("  --- rolling back ---")
    rolled = ex.rollback(bundle, reason="pretend it broke in production")
    print(f"  state    : {rolled.state}\n")
    results += [
        check(rolled.state == W.ROLLED_BACK, "the rollback completed"),
        check("multiply" not in (repo / "calc.py").read_text(encoding="utf-8"),
              "main is back to how it was"),
        check(promotion.merge_commit in git(repo, "log", "--format=%H"),
              "history was reverted, not rewritten"),
    ]

    print("  --- cleanup ---")
    report = ex.cleanup(bundle)
    print(f"  {report}\n")
    results += [
        check(report["removed"], "the worktree was removed"),
        check(report["branch_deleted"], "its branch was removed"),
        check(name not in manager.stale(), "nothing stale is left behind"),
    ]
    return results


async def reject_on_failure(store: Store, repo: Path) -> list[bool]:
    """A run whose work does not pass must move nothing."""
    print("#" * 70)
    print("# REJECT - acceptance fails, main is untouched")
    print("#" * 70 + "\n")

    bundle = TaskBundle(
        goal="Add a function divide(a, b) to calc.py that returns a / b. Do NOT "
             "guard against division by zero - leave that case unhandled.",
        workspace=str(repo), project="calc", isolate=True, run_id="DEV-reject")

    manager = W.WorktreeManager(repo)
    ex = ClaudeCodeExecutor(store)
    before = git(repo, "rev-parse", "HEAD")
    result = await ex.execute(bundle, profile=B.BUILD, timeout=900)
    name = bundle.worktree_name()

    print(f"  status   : {result.status}")
    print(f"  worktree : {manager.path_for(name).exists()}\n")

    # ADA's acceptance test, not Claude's opinion of its own work.
    source = ""
    target = manager.path_for(name) / "calc.py"
    if target.exists():
        source = target.read_text(encoding="utf-8")
    acceptable = "ZeroDivisionError" in source or "if b == 0" in source
    print(f"  acceptance (guards divide-by-zero): {acceptable}\n")

    promotion = (ex.promote(bundle, message="ADA DEV-reject")
                 if acceptable else
                 ex.reject(bundle, "divide has no guard for b == 0"))
    print(f"  decision : {promotion.state} - {promotion.reason[:80]}\n")

    results = [
        check(promotion.state == W.REJECTED, "a failing acceptance rejects"),
        check(git(repo, "rev-parse", "HEAD") == before, "main's HEAD did not move"),
        check("divide" not in (repo / "calc.py").read_text(encoding="utf-8"),
              "main never received the change"),
        check(not tracked_changes(repo), "no partial promotion was left behind"),
    ]

    report = ex.cleanup(bundle, keep=True)
    results.append(check(manager.path_for(name).is_dir(),
                         "the rejected worktree is kept as evidence"))
    print(f"  cleanup  : {report}\n")

    manager.cleanup(name)      # tidy up after the test itself
    return results


async def missing_worktree(store: Store, repo: Path) -> list[bool]:
    """
    The dangerous case: resume a run whose worktree has been deleted.

    The CLI can fall back to the launch directory, so an isolated run would
    quietly start editing the live checkout.
    """
    print("#" * 70)
    print("# MISSING WORKTREE - refuse to resume into the main checkout")
    print("#" * 70 + "\n")

    import shutil

    from friday.executors import runs as R

    bundle = TaskBundle(goal="add a subtract function", workspace=str(repo),
                        project="calc", isolate=True, run_id="DEV-missing")
    manager = W.WorktreeManager(repo)
    ex = ClaudeCodeExecutor(store)

    ex.runs.open(bundle)
    ex.runs.running("DEV-missing", pid=None, session_id="sess-old")
    ex.runs.store.touch_executor_run("DEV-missing", status=R.INTERRUPTED)

    path = manager.path_for(bundle.worktree_name())
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "worktree", "add", "-q", "-b",
                    manager.branch_for(bundle.worktree_name()), str(path)],
                   cwd=str(repo), capture_output=True)
    intact = W.guard_before_resume(manager, bundle.worktree_name())

    shutil.rmtree(path)
    gone = W.guard_before_resume(manager, bundle.worktree_name())
    print(f"  intact -> {intact[0]}")
    print(f"  deleted -> {gone[0]}: {gone[1][:110]}\n")

    results = [
        check(intact[0], "an intact worktree may be resumed"),
        check(not gone[0], "a deleted worktree refuses the resume"),
        check("main checkout" in gone[1],
              "and says why - it would fall back to the live checkout"),
    ]

    # And a fake directory in its place must not fool the check.
    path.mkdir(parents=True, exist_ok=True)
    faked = W.guard_before_resume(manager, bundle.worktree_name())
    print(f"  empty dir in its place -> {faked[0]}: {faked[1][:110]}\n")
    results.append(check(not faked[0],
                         "an empty directory in its place is not a worktree"))

    shutil.rmtree(path, ignore_errors=True)
    manager.cleanup(bundle.worktree_name())
    return results


async def journey() -> list[bool]:
    results: list[bool] = []
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "calc"
        make_repo(repo)
        store = Store(Path(tmp) / "wt.sqlite3")
        try:
            print(f"  claude : {cli.version()}")
            print(f"  repo   : {repo}\n")
            results += await isolate_and_promote(store, repo)
            print()
            results += await reject_on_failure(store, repo)
            print()
            results += await missing_worktree(store, repo)
        finally:
            store.close()
    return results


def main() -> int:
    if not cli.available():
        print("claude CLI is not installed; nothing to prove")
        return 1
    results = asyncio.run(journey())
    passed = sum(1 for r in results if r)
    print("\n" + "=" * 70)
    print(f"RESULT: {passed}/{len(results)} checks passed")
    print("=" * 70)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
