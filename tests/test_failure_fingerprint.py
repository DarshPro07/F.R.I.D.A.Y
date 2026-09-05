"""
S4a: failure fingerprint + strategy-change guard.

A retryable failure that keeps landing on the exact same fingerprint with no
new hypothesis is not "still transient" - it is a loop. These tests drive the
same engine used by the P0 gate (tests/test_objective_continuity.py) through
that loop and assert it changes strategy, then gives up cleanly at BLOCKED
instead of retrying forever.
"""

from __future__ import annotations

import asyncio

import pytest

from friday.continuous import (
    ContinuousTaskExecutor,
    MAX_STRATEGY_CHANGES,
    STRATEGY_HINTS,
    failure_fingerprint,
)
from friday.objectives import TaskStatus, compile_objective
from friday.store import Store


def manifest() -> list[dict]:
    return [{"id": "flaky", "description": "a capability that fails"}]


@pytest.fixture
def store() -> Store:
    return Store(":memory:")


async def _run_to_terminal(store, executor, run_id, timeout=5.0):
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        row = store.objective_run(run_id)
        if row["status"] in ("COMPLETED", "PARTIAL", "FAILED", "CANCELLED"):
            return row
        await asyncio.sleep(0.02)
    raise AssertionError("run never reached a terminal state")


def _compile(store, *, arguments=None):
    # ponytail: objective_tasks has no max_attempts column today (pre-existing
    # gap, not S4a's) - the attempt budget is executor.max_attempts, set
    # per-test below, not the compiled task's requested value.
    tasks = [{"capability": "flaky", "arguments": arguments or {}}]
    return compile_objective(store, request="flaky test", tasks=tasks,
                             manifest=manifest(), objective_summary="flaky")


@pytest.mark.asyncio
async def test_same_fingerprint_without_new_evidence_changes_strategy():
    """Blind retry today: three identical failures used to just retry a
    fourth time with attempts left; the task must instead go BLOCKED."""
    store = Store(":memory:")

    async def always_times_out(_cap, _args):
        raise TimeoutError("op timed out at line 12 in /tmp/run-8f21/a.py")

    executor = ContinuousTaskExecutor(store, always_times_out,
                                      executor_id="fp-1")
    executor.max_attempts = 10
    run = _compile(store)
    await executor.start(run["run_id"])
    row = await _run_to_terminal(store, executor, run["run_id"])

    tasks = store.objective_tasks(run["run_id"])
    task = tasks[0]
    assert task["status"] == TaskStatus.BLOCKED, task
    detail = task["detail"]
    assert detail["strategy_changes"] == MAX_STRATEGY_CHANGES + 1
    assert len(detail["fingerprint_history"]) >= MAX_STRATEGY_CHANGES + 1
    assert row["status"] == "FAILED"
    assert row["summary"]["outcome"].startswith("blocked:")


@pytest.mark.asyncio
async def test_new_evidence_allows_a_bounded_retry():
    """A hypothesis that changes each attempt is new evidence: it keeps
    retrying (no strategy change) instead of jumping straight to BLOCKED."""
    store = Store(":memory:")
    calls = {"n": 0}

    words = ["alpha", "beta", "gamma"]

    async def different_reason_each_time(_cap, _args):
        word = words[calls["n"]]
        calls["n"] += 1
        raise TimeoutError(f"attempt reason {word}")

    executor = ContinuousTaskExecutor(store, different_reason_each_time,
                                      executor_id="fp-2")
    executor.max_attempts = 3
    run = _compile(store)
    await executor.start(run["run_id"])
    await _run_to_terminal(store, executor, run["run_id"])

    task = store.objective_tasks(run["run_id"])[0]
    assert task["status"] == TaskStatus.FAILED
    assert task["detail"]["strategy_changes"] == 0
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_strategy_budget_ends_in_blocked_not_a_loop():
    """A huge attempt budget must not turn a stuck fingerprint into an
    infinite retry loop - it stops at MAX_STRATEGY_CHANGES, well under it."""
    store = Store(":memory:")

    async def always_fails(_cap, _args):
        raise TimeoutError("stuck")

    executor = ContinuousTaskExecutor(store, always_fails, executor_id="fp-3")
    executor.max_attempts = 1000
    run = _compile(store)
    await executor.start(run["run_id"])
    await _run_to_terminal(store, executor, run["run_id"])

    task = store.objective_tasks(run["run_id"])[0]
    assert task["status"] == TaskStatus.BLOCKED
    assert task["attempts"] < 20, "strategy budget must cut this off early"
    hints = task["detail"].get("strategy_hint_history") or STRATEGY_HINTS
    assert set(hints) <= set(STRATEGY_HINTS) | {None}


def test_fingerprint_ignores_numbers_and_paths():
    a = failure_fingerprint("TRANSIENT",
                            "TimeoutError: op timed out at line 12 in "
                            "/tmp/run-8f21/a.py")
    b = failure_fingerprint("TRANSIENT",
                            "TimeoutError: op timed out at line 99 in "
                            "/tmp/run-ffee/a.py")
    assert a == b


@pytest.mark.asyncio
async def test_iteration_budget_caps_attempts():
    """An `iteration_budget` in the task arguments caps max_attempts even
    when the compiled task allows far more."""
    store = Store(":memory:")
    calls = {"n": 0}

    async def always_fails(_cap, _args):
        calls["n"] += 1
        raise TimeoutError("stuck")

    executor = ContinuousTaskExecutor(store, always_fails, executor_id="fp-4")
    executor.max_attempts = 50
    run = _compile(store, arguments={"iteration_budget": 2})
    await executor.start(run["run_id"])
    await _run_to_terminal(store, executor, run["run_id"])

    task = store.objective_tasks(run["run_id"])[0]
    assert task["attempts"] == 2
    assert calls["n"] == 2
