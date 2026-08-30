"""
Restart recovery: a run in progress survives a process restart.

The store is the only thing shared across restarts. A fresh executor (new
process identity) reacquires the lease, marks the dead portion interrupted
with evidence, and finishes the run - without any user message.
"""
from __future__ import annotations


def by_plan_id(store, run_id: str) -> dict:
    """
    Tasks indexed by their plan-facing name - t1, t2 - not the stored id.

    Stored ids are scoped to their run now (`RUN-abc123-t1`), because
    `objective_tasks.task_id` is the table's PRIMARY KEY and per-run numbering
    collided across runs: the second objective ever compiled overwrote the
    first one's rows instead of inserting its own. The plan-facing names these
    tests are written against are unchanged.
    """
    return {row['task_id'].rsplit('-', 1)[-1]: row for row in store.objective_tasks(run_id)}


def plan_id(value: str) -> str:
    """The plan-facing half of a stored task id, for comparing to `blocked_by`."""
    return (value or '').rsplit('-', 1)[-1]
import asyncio
from datetime import datetime, timedelta
import pytest
from friday.continuous import ContinuousTaskExecutor, RunWatchdog
from friday.objectives import RunStatus, TaskStatus, compile_objective
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


def compile_restartable(store) -> dict:
    return compile_objective(
        store,
        request="restart probe",
        tasks=[
            {"capability": "a", "arguments": {}},
            {"capability": "b", "arguments": {}, "dependencies": ["t1"]},
            {"capability": "c", "arguments": {}, "dependencies": ["t1"]},
        ],
        manifest=[
            {"id": "a", "description": "a"},
            {"id": "b", "description": "b"},
            {"id": "c", "description": "c"},
        ],
        objective_summary="restart probe",
    )


@pytest.mark.asyncio
async def test_fresh_executor_resumes_same_run(store, registry):
    """A new process identity picks up where the old one stopped."""
    run = compile_restartable(store)
    run_id = run['run_id']
    store.open_objective_run(run_id, request=run['request'], objective_summary=run['objective_summary'], lease_executor_id='old-executor-pid-9001', lease_generation=1, lease_expiry=(datetime.now() - timedelta(seconds=30)).isoformat(), next_wake=None, status='RUNNING')
    store.save_objective_task(task_id='t1', run_id=run_id, capability='a', arguments='{}', dependencies='[]', status='RUNNING', attempts=1)
    fresh = ContinuousTaskExecutor(store, registry.call, executor_id='fresh-executor-pid-9002')
    watchdog = RunWatchdog(fresh, lease_timeout=90.0)
    orphaned = await watchdog.sweep_once()
    assert run_id in orphaned
    row = store.objective_run(run_id)
    assert row['lease_executor_id'] == 'fresh-executor-pid-9002'
    assert row['lease_generation'] == 2
    deadline = asyncio.get_event_loop().time() + 2.0
    while asyncio.get_event_loop().time() < deadline:
        row = store.objective_run(run_id)
        if row['status'] in ('COMPLETED', 'PARTIAL', 'FAILED', 'CANCELLED'):
            break
        await asyncio.sleep(0.05)
    assert row['status'] == RunStatus.COMPLETED
    tasks = by_plan_id(store, run_id)
    assert tasks['t1']['status'] == TaskStatus.SUCCEEDED
    assert tasks['t2']['status'] == TaskStatus.SUCCEEDED
    assert tasks['t3']['status'] == TaskStatus.SUCCEEDED
    events = [e['event'] for e in store.objective_events(run_id)]
    assert 'watchdog.orphaned' in events
    assert 'run.completed' in events
    assert any((e['event'] == 'task.interrupted' for e in store.objective_events(run_id))) or any((e['event'] == 'task.started' and e['task_id'] == 't1' for e in store.objective_events(run_id)))


class CountingRegistry:
    def __init__(self) -> None:
        self.calls = []

    async def call(self, capability: str, arguments: dict) -> dict:
        self.calls.append(capability)
        return {'ok': True, 'capability': capability}


@pytest.mark.asyncio
async def test_a_finished_task_is_never_dispatched_twice(store):
    """The side effect happened once and the row says so. That is the record."""
    registry = CountingRegistry()
    run = compile_restartable(store)
    run_id = run['run_id']
    first = ContinuousTaskExecutor(store, registry.call, executor_id='pid-1')
    first.stop()
    await first._drive_until_done(run_id)
    assert sorted(registry.calls) == ['a', 'b', 'c']
    registry.calls.clear()
    second = ContinuousTaskExecutor(store, registry.call, executor_id='pid-2')
    second.stop()
    await second._drive_until_done(run_id)
    assert registry.calls == [], f"replayed {registry.calls}"


@pytest.mark.asyncio
async def test_an_interrupted_task_is_re_dispatched_and_a_finished_one_is_not(store):
    """
    The honest distinction. An INTERRUPTED row means the process vanished
    mid-call and nothing observed the outcome, so it is tried again - the
    alternative is abandoning work on the strength of a guess. A SUCCEEDED row
    means the outcome *was* observed, and re-running it would be doing the
    thing twice on the strength of no evidence at all.
    """
    registry = CountingRegistry()
    run = compile_restartable(store)
    run_id = run['run_id']
    tasks = by_plan_id(store, run_id)
    store.update_objective_task(tasks['t1']['task_id'], status=TaskStatus.SUCCEEDED)
    store.update_objective_task(tasks['t2']['task_id'], status=TaskStatus.INTERRUPTED, attempts=1)
    executor = ContinuousTaskExecutor(store, registry.call, executor_id='pid-3')
    executor.stop()
    await executor._drive_until_done(run_id)
    assert 'a' not in registry.calls, 'a finished task was run again'
    assert registry.calls.count('b') == 1, registry.calls
    assert registry.calls.count('c') == 1, registry.calls
