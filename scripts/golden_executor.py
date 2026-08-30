#!/usr/bin/env python3
"""
ADA hands real work to Claude Code and stays in charge of it.

Runs against a throwaway git repository, never this one. Nothing here touches
the project, and the permission profile is the same one production uses.

    python scripts/golden_executor.py

Costs real subscription usage - it starts actual Claude Code sessions.
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
from friday.executors.claude_code import ClaudeCodeExecutor, TaskBundle  # noqa: E402
from friday.store import FACT, Store  # noqa: E402

SEED = '''def add(a, b):
    """Add two numbers."""
    return a + b
'''


def check(passed: bool, message: str) -> bool:
    print(f"  [{'PASS' if passed else 'FAIL'}] {message}")
    return passed


def make_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "calc.py").write_text(SEED, encoding="utf-8")
    (path / "README.md").write_text("# Calc\n\nA tiny calculator.\n", encoding="utf-8")
    for argv in (["git", "init", "-q"],
                 ["git", "config", "user.email", "ada@example.com"],
                 ["git", "config", "user.name", "ADA"],
                 ["git", "add", "-A"],
                 ["git", "commit", "-q", "-m", "seed"]):
        subprocess.run(argv, cwd=path, check=True, capture_output=True)


async def explore_only(store: Store, workspace: Path) -> list[bool]:
    """Read autonomy: it explores without asking, and cannot change anything."""
    print("=" * 70)
    print("READ AUTONOMY - explore, with no way to write")
    print("=" * 70)

    bundle = TaskBundle(
        goal="Read calc.py and tell me, in one sentence, what the add function does. "
             "Then try to add a subtract function to the same file.",
        workspace=str(workspace), project="calc", isolate=False,
        acceptance=("the add function is described",))

    ex = ClaudeCodeExecutor(store)
    started = time.monotonic()
    result = await ex.execute(bundle, profile=B.EXPLORE, timeout=600)
    took = time.monotonic() - started

    output = result.output or {}
    print(f"  {took:.0f}s  status={result.status}")
    print(f"  tools : {output.get('tools_used')}")
    print(f"  says  : {str(output.get('claude_says'))[:200]}\n")

    text = str(output.get("claude_says", "")).lower()
    unchanged = SEED == (workspace / "calc.py").read_text(encoding="utf-8")
    return [
        check(result.status in (c.SUCCEEDED, c.PARTIAL), "the run completed"),
        check("read" in str(output.get("tools_used", "")).lower(),
              "it read the file without being granted anything extra"),
        check("add" in text or "sum" in text, "it answered the question"),
        check(unchanged, "it could not modify the file, and did not"),
        check(bool(ex.session_for(bundle.run_id)),
              "ADA recorded the session id, so this run is resumable"),
    ]


async def build_with_a_question(store: Store, workspace: Path) -> list[bool]:
    """
    The whole point: Claude hits a decision, ADA answers it from an accepted
    project decision, and the boss is never interrupted.
    """
    print("=" * 70)
    print("QUESTION BROKER - a decision ADA already holds")
    print("=" * 70)

    store.ensure_project("calc")
    store.record_decision(
        "calc", decision="raise ValueError on non-numeric input",
        source="he decided this in an earlier session",
        rationale="the calc library must fail loudly on bad input rather than "
                  "coercing strings, so callers find their bugs")
    store.remember("calc.style", "type hints on every public function",
                   kind=FACT, source="he said so", scope="preferences")

    broker = B.QuestionBroker(
        store=store, project="calc",
        ask_user=lambda q, o: None)     # the boss is deliberately unreachable

    question = "should add raise on non-numeric input or coerce it?"
    answer = broker.answer(question)
    print(f"  question : {question}")
    print(f"  answer   : {answer.text!r}")
    print(f"  source   : {answer.source}")
    print(f"  evidence : {answer.evidence[:120]}\n")

    results = [
        check(answer.source == "decision", "answered from an accepted decision"),
        check(answer.grounded, "the answer is grounded, not guessed"),
        check("ValueError" in answer.text, "it is the decision that was recorded"),
    ]

    print("  - a question nothing settles -")
    unknown = broker.answer("what should the package be called on PyPI?")
    print(f"  answer   : {unknown.text!r}  source: {unknown.source}\n")
    results.append(check(unknown.source == "unknown",
                         "an unsettled question is left unanswered, not guessed"))

    print("=" * 70)
    print("BUILD - it writes, and ADA verifies with git")
    print("=" * 70)

    bundle = TaskBundle(
        goal="Add a `subtract(a, b)` function to calc.py, with a docstring, "
             "matching the style of the existing add function.",
        workspace=str(workspace), project="calc", isolate=False,
        context=(f"Project decision: {answer.text}",
                 "Style: type hints on every public function"),
        acceptance=("calc.py contains a subtract function",))

    ex = ClaudeCodeExecutor(store)
    started = time.monotonic()
    result = await ex.execute(bundle, profile=B.BUILD, timeout=900)
    took = time.monotonic() - started

    output = result.output or {}
    source = (workspace / "calc.py").read_text(encoding="utf-8")
    print(f"  {took:.0f}s  status={result.status}")
    print(f"  tools   : {output.get('tools_used')}")
    print(f"  changed : {output.get('changed_files')}")
    print(f"  refused : {output.get('refused')}")
    if result.verification:
        print(f"  verified: {result.verification.evidence[:160]}")
    print(f"\n  calc.py now:\n{source[:400]}\n")

    results += [
        check(result.status == c.SUCCEEDED, "the run succeeded"),
        check("def subtract" in source, "the function is actually in the file"),
        check(bool(output.get("changed_files")),
              "the verification came from git, not from what Claude said"),
        check(result.verification is not None and
              result.verification.method == "worktree_diff",
              "succeeded carries a Verification, as the contract requires"),
    ]
    return results


async def cancellation(store: Store, workspace: Path) -> list[bool]:
    """"ADA, stop the coding task." - and the process tree goes with it."""
    print("=" * 70)
    print("CANCELLATION - stop it mid-flight")
    print("=" * 70)

    bundle = TaskBundle(
        goal="Read every file in this directory one at a time and write a long, "
             "detailed description of each. Take your time and be thorough.",
        workspace=str(workspace), project="calc", isolate=False)

    ex = ClaudeCodeExecutor(store)
    task = asyncio.create_task(ex.execute(bundle, profile=B.EXPLORE, timeout=600))
    await asyncio.sleep(25)          # let it genuinely get going

    running = ex.run is not None and ex.run.process is not None
    stopped = await ex.cancel()
    result = await task

    print(f"  was running : {running}")
    print(f"  cancelled   : {stopped}")
    print(f"  status      : {result.status}\n")
    return [
        check(running, "the run had actually started"),
        check(stopped, "cancel found a live process to kill"),
        check(result.status != c.SUCCEEDED,
              "a killed run is never reported as succeeded"),
    ]


async def journey() -> list[bool]:
    results: list[bool] = []
    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp) / "calc"
        make_repo(workspace)
        store = Store(Path(tmp) / "exec.sqlite3")
        try:
            print(f"  claude   : {cli.version()}")
            print(f"  workspace: {workspace}\n")
            results += await explore_only(store, workspace)
            print()
            results += await build_with_a_question(store, workspace)
            print()
            results += await cancellation(store, workspace)
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
