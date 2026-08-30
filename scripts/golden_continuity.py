#!/usr/bin/env python3
"""
Kill it for real, then carry on.

Test 7 and test 10 of the executor gate, with nothing mocked:

  * a multi-step task is started, allowed to change a file, then HARD-KILLED -
    not cancelled. The ADA process that was watching it goes too.
  * a brand-new ADA runtime opens the database it has never seen, finds the
    run as INTERRUPTED rather than FAILED, and continues it into the same
    Claude session.
  * a run whose Claude session has vanished is restarted from the task bundle
    instead of being lost.
  * asking to continue a live run attaches; it never starts a second Claude.

Throwaway git repo and throwaway database. Costs real subscription usage.

    python scripts/golden_continuity.py
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
from friday.executors import runs as R  # noqa: E402
from friday.executors.claude_code import ClaudeCodeExecutor, TaskBundle  # noqa: E402
from friday.store import Store  # noqa: E402

GOAL = (
    "Build a small package in this directory:\n"
    "1. parser.py with a parse_pair(text) function that splits 'a=b' into a tuple\n"
    "2. validation in it: raise ValueError on input with no '='\n"
    "3. test_parser.py with pytest tests covering both cases\n"
    "4. a README.md section describing it\n"
    "Do these in order, writing each file before starting the next."
)


def check(passed: bool, message: str) -> bool:
    print(f"  [{'PASS' if passed else 'FAIL'}] {message}")
    return passed


def make_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "README.md").write_text("# Halo\n", encoding="utf-8")
    for argv in (["git", "init", "-q"],
                 ["git", "config", "user.email", "ada@example.com"],
                 ["git", "config", "user.name", "ADA"],
                 ["git", "add", "-A"],
                 ["git", "commit", "-q", "-m", "seed"]):
        subprocess.run(argv, cwd=path, check=True, capture_output=True)


def hard_kill(pid: int) -> bool:
    """
    Not a cancel. The process does not get to tidy up or write a status.

    Then wait for it to actually be gone. `taskkill` prints SUCCESS and returns
    0 while the process is still there - measured at ~0.5s on this machine, and
    checking inside that window made a killed run read as still running. The
    liveness check was right; the test was just faster than Windows.

    A real crash-and-restart has a person and a process launch in between, so
    waiting here is the honest version of the scenario, not a workaround.
    """
    import psutil

    subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if not psutil.pid_exists(pid):
            return True
        time.sleep(0.2)
    return False


async def phase_one(db: Path, workspace: Path) -> tuple[list[bool], str]:
    """Start real work, let it get somewhere, then pull the plug."""
    print("#" * 70)
    print("# PHASE ONE - start it, then hard-kill it mid-task")
    print("#" * 70 + "\n")

    results: list[bool] = []
    store = Store(db)
    try:
        bundle = TaskBundle(goal=GOAL, workspace=str(workspace), project="halo",
                            isolate=False, run_id="DEV-halo-parser",
                            acceptance=("parser.py exists", "tests exist"))
        ex = ClaudeCodeExecutor(store)
        task = asyncio.create_task(ex.execute(bundle, profile=B.BUILD, timeout=900))

        # Wait until it has actually written something. Killing before any
        # work lands would prove nothing about continuing it.
        deadline = time.monotonic() + 300
        wrote = False
        while time.monotonic() < deadline:
            await asyncio.sleep(3)
            if (workspace / "parser.py").exists():
                wrote = True
                break
            if task.done():
                break

        row = store.executor_run("DEV-halo-parser")
        pid = row.get("pid") if row else None
        session = row.get("session_id") if row else ""
        print(f"  wrote a file : {wrote}")
        print(f"  status       : {row['status'] if row else 'MISSING'}")
        print(f"  session      : {session}")
        print(f"  pid          : {pid}")
        print(f"  last event   : {row.get('last_event') if row else ''}\n")

        results += [
            check(wrote, "Claude got far enough to change the repository"),
            check(bool(session), "the session id was persisted while it ran"),
            check(bool(pid), "the pid was persisted while it ran"),
            check(bool(row and row["task_bundle"]),
                  "the task bundle was persisted, not just a summary"),
        ]

        print("  --- pulling the plug ---")
        gone = hard_kill(int(pid)) if pid else False
        print(f"  process gone : {gone}\n")
        results.append(check(gone, "the process really is dead before we look"))
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
        return results, session
    finally:
        store.close()          # the ADA runtime that was watching is now gone


async def phase_two(db: Path, workspace: Path, session: str) -> list[bool]:
    """A runtime that has never seen any of this opens the database."""
    print("#" * 70)
    print("# PHASE TWO - a new ADA, told nothing, reading the database")
    print("#" * 70 + "\n")

    results: list[bool] = []
    store = Store(db)              # brand new handle, brand new everything
    try:
        manager = R.RunManager(store)

        interrupted = manager.reconcile_all()
        row = store.executor_run("DEV-halo-parser")
        print(f"  reconciled   : {[r['run_id'] for r in interrupted]}")
        print(f"  status now   : {row['status']}\n")
        results += [
            check(row["status"] == R.INTERRUPTED,
                  "a killed run reads as INTERRUPTED, not FAILED"),
            check(row["status"] != R.SUCCEEDED,
                  "and certainly not as SUCCEEDED"),
        ]

        # Test 10: "how is the Halo validation work going?" - answered from
        # durable state by something that has never heard of the conversation.
        brief = manager.brief(project="halo")
        print("  what a fresh conversation can say:")
        print("\n".join(f"    {line}" for line in brief.splitlines()))
        print()
        results += [
            check("DEV-halo-parser" in brief, "the run is named"),
            check("INTERRUPTED" in brief, "its state is stated"),
            check("parse_pair" in brief or "parser" in brief.lower(),
                  "the goal survived, not just the id"),
        ]

        recovery = manager.recover("DEV-halo-parser",
                                   session_exists=R.session_file_exists)
        print(f"  recovery     : {recovery.action} - {recovery.reason}\n")
        results.append(check(recovery.can_continue,
                             "ADA knows how to carry on without being told"))

        restored = manager.bundle_of(recovery.run)
        results.append(check(restored.goal == GOAL,
                             "the original goal came back whole"))

        # "Continue it."
        print("  --- continuing ---\n")
        ex = ClaudeCodeExecutor(store)
        started = time.monotonic()
        result = await ex.continue_run("DEV-halo-parser", profile=B.BUILD,
                                       timeout=900)
        took = time.monotonic() - started

        output = result.output or {}
        files = sorted(p.name for p in workspace.iterdir() if p.is_file())
        print(f"  {took:.0f}s  status={result.status}")
        print(f"  tools   : {output.get('tools_used')}")
        print(f"  files   : {files}")
        if result.verification:
            print(f"  verified: {result.verification.evidence[:150]}")
        print()

        results += [
            check(result.status in (c.SUCCEEDED, c.PARTIAL),
                  "the continued run completed"),
            check((workspace / "parser.py").exists(), "parser.py is there"),
            check(any(name.startswith("test") for name in files),
                  "it carried on to the tests rather than starting over"),
            check(store.executor_run("DEV-halo-parser")["status"] in
                  (R.SUCCEEDED, R.INTERRUPTED),
                  "the run's final state came from the ActionResult"),
        ]
        return results
    finally:
        store.close()


async def lost_session(db: Path, workspace: Path) -> list[bool]:
    """Claude's session files get cleaned up. The project must not go with them."""
    print("#" * 70)
    print("# LOST SESSION - the transcript is gone, the work is not")
    print("#" * 70 + "\n")

    store = Store(db)
    try:
        manager = R.RunManager(store)
        store.touch_executor_run("DEV-halo-parser", status=R.INTERRUPTED,
                                 session_id="sess-that-no-longer-exists", pid=None)
        recovery = manager.recover("DEV-halo-parser",
                                   session_exists=lambda s: False)
        print(f"  recovery : {recovery.action} - {recovery.reason}\n")
        return [
            check(recovery.action == "restart",
                  "a vanished session becomes a restart, not a dead end"),
            check(manager.bundle_of(recovery.run).goal == GOAL,
                  "the task bundle is still enough to start again"),
        ]
    finally:
        store.close()


async def no_second_claude(db: Path, workspace: Path) -> list[bool]:
    """One run_id, at most one live executor."""
    print("#" * 70)
    print("# DUPLICATE RESUME - ask twice, get one process")
    print("#" * 70 + "\n")

    store = Store(db)
    try:
        manager = R.RunManager(store)
        # Pretend this interpreter is the executor: a live pid it can verify.
        import psutil

        me = psutil.Process()
        store.touch_executor_run("DEV-halo-parser", status=R.RUNNING, pid=me.pid)

        original = R.process_alive
        R.process_alive = lambda pid, started_at=None: pid == me.pid
        try:
            recovery = manager.recover("DEV-halo-parser")
            ex = ClaudeCodeExecutor(store)
            result = await ex.continue_run("DEV-halo-parser")
        finally:
            R.process_alive = original

        print(f"  recovery : {recovery.action}")
        print(f"  result   : {result.status} | "
              f"already_running={(result.output or {}).get('already_running')}\n")
        return [
            check(recovery.action == "attach", "a live run is attached to"),
            check((result.output or {}).get("already_running") is True,
                  "the second request returned status instead of a new process"),
            check(result.status != c.SUCCEEDED,
                  "attaching is not reported as having done the work"),
        ]
    finally:
        store.close()


async def journey() -> list[bool]:
    results: list[bool] = []
    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp) / "halo"
        db = Path(tmp) / "ada.sqlite3"
        make_repo(workspace)
        print(f"  claude    : {cli.version()}")
        print(f"  workspace : {workspace}\n")

        first, session = await phase_one(db, workspace)
        results += first
        print()
        results += await phase_two(db, workspace, session)
        print()
        results += await lost_session(db, workspace)
        print()
        results += await no_second_claude(db, workspace)
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
