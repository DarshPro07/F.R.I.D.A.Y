"""
Watchdog + invariant tests.

The invariant NON_TERMINAL_RUN_HAS_FUTURE must hold at every instant: a
non-terminal run has an active executor, a scheduled next_wake, or a
legitimate WAITING_* state. An orphaned run (stale lease) is reconciled
exactly once per outage, with a generation bump preventing double
continuation.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta

import pytest

from friday.continuous import ContinuousTaskExecutor, RunWatchdog
from friday.objectives import RUN_TERMINAL, RunStatus, compile_objective
from friday.store import Store


class SimpleRegistry:
    async def call(self, capability: str, arguments: dict) -> dict:
        return {"ok": True, "capability": capability}


@pytest.fixture
def store() -> Store:
    return Store(":memory:")


@pytest.fixture
def registry() -> SimpleRegistry:
    return SimpleRegistry()


@pytest.fixture
def executor(store, registry) -> ContinuousTaskExecutor:
    return ContinuousTaskExecutor(store, registry.call, executor_id="watch-exec")


def compile_small(store) -> dict:
    return compile_objective(
        store,
        request="watchdog probe",
        tasks=[
            {"capability": "a", "arguments": {}},
            {"capability": "b", "arguments": {}, "dependencies": ["t1"]},
            {"capability": "c", "arguments": {}},
        ],
        manifest=[
            {"id": "a", "description": "a"},
            {"id": "b", "description": "b"},
            {"id": "c", "description": "c"},
        ],
        objective_summary="probe",
    )


def invariant_holds(store, run_id: str) -> bool:
    """The invariant, probed from pure store state."""
    row = store.objective_run(run_id)
    if row is None or row["status"] in RUN_TERMINAL:
        return True
    if row["status"] in ("WAITING_QUESTION", "WAITING_PERMISSION"):
        return True
    # Non-terminal: needs a lease or a scheduled wake.
    if row["next_wake"]:
        return True
    if row["lease_executor_id"] and row["lease_expiry"]:
        expiry = datetime.fromisoformat(row["lease_expiry"])
        return expiry > datetime.now()
    return False


@pytest.mark.asyncio
async def test_watchdog_reconciles_orphan_once(store, executor):
    """A run whose owner vanished is picked up exactly once per outage."""
    run = compile_small(store)
    run_id = run["run_id"]

    # Pretend another executor started it and died mid-wake: stale lease,
    # one in-flight task.
    from friday.contracts import now_iso

    store.open_objective_run(
        run_id, request=run["request"],
        objective_summary=run["objective_summary"],
        lease_executor_id="dead-exec", lease_generation=1,
        lease_expiry=(datetime.now() - timedelta(seconds=5)).isoformat(),
        next_wake=None, status="RUNNING",
    )
    store.save_objective_task(
        task_id="t1", run_id=run_id, capability="a", arguments="{}",
        dependencies="[]", status="RUNNING", attempts=1,
    )

    watchdog = RunWatchdog(executor, lease_timeout=90.0)
    orphaned = await watchdog.sweep_once()

    assert run_id in orphaned
    row = store.objective_run(run_id)
    assert row["lease_executor_id"] == executor.executor_id
    assert row["lease_generation"] == 2, "generation must bump on reacquire"
    assert row["status"] == "RUNNING"

    # The in-flight task was interrupted with evidence, then re-driven.
    tasks = {t["task_id"]: t for t in store.objective_tasks(run_id)}
    assert tasks["t1"]["status"] in ("SUCCEEDED", "INTERRUPTED")

    events = [e["event"] for e in store.objective_events(run_id)]
    assert "watchdog.orphaned" in events
    assert events.count("watchdog.orphaned") == 1
    assert events.count("continuation.scheduled") == 1

    # No double continuation: a second sweep finds nothing to do.
    again = await watchdog.sweep_once()
    assert run_id not in again
    assert store.objective_run(run_id)["status"] == "COMPLETED"


@pytest.mark.asyncio
async def test_watchdog_does_not_steal_a_live_lease(store, executor):
    """A fresh lease is not an orphan, even though the owner is unknown."""
    run = compile_small(store)
    run_id = run["run_id"]
    store.open_objective_run(
        run_id, request=run["request"],
        objective_summary=run["objective_summary"],
        lease_executor_id="other-exec", lease_generation=3,
        lease_expiry=(datetime.now() + timedelta(seconds=120)).isoformat(),
        next_wake=(datetime.now() + timedelta(seconds=1)).isoformat(),
        status="RUNNING",
    )

    watchdog = RunWatchdog(executor, lease_timeout=90.0)
    orphaned = await watchdog.sweep_once()
    assert run_id not in orphaned
    row = store.objective_run(run_id)
    assert row["lease_generation"] == 3, "live lease must not be bumped"


@pytest.mark.asyncio
async def test_invariant_holds_throughout(store, executor):
    """Probe the invariant after every mutation the engine makes."""
    run = compile_small(store)
    run_id = run["run_id"]
    assert invariant_holds(store, run_id), "compile leaves a non-terminal run"

    deadline = asyncio.get_event_loop().time() + 2.0
    while asyncio.get_event_loop().time() < deadline:
        assert invariant_holds(store, run_id), (
            f"invariant broken: {json.dumps(store.objective_run(run_id), default=str)}"
        )
        await asyncio.sleep(0.01)
        if store.objective_run(run_id)["status"] in RUN_TERMINAL:
            break

    row = store.objective_run(run_id)
    assert row["status"] == RunStatus.COMPLETED


@pytest.mark.asyncio
async def test_waiting_states_are_legitimate_no_wake(store, executor):
    """WAITING_QUESTION / WAITING_PERMISSION are valid without a wake."""
    run = compile_small(store)
    run_id = run["run_id"]
    store.open_objective_run(
        run_id, request=run["request"],
        objective_summary=run["objective_summary"],
        status="WAITING_QUESTION",
        lease_executor_id=None, lease_generation=0,
        lease_expiry=None, next_wake=None,
    )
    assert invariant_holds(store, run_id)
    watchdog = RunWatchdog(executor, lease_timeout=90.0)
    orphaned = await watchdog.sweep_once()
    assert run_id not in orphaned, "a user-waiting run is not an orphan"
