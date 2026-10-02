"""
A side effect that happened is never done again on recovery (PRD v7
DUR-001).

The executor records that a side-effecting call committed as the first
thing it writes after the call returns. If it dies after that and before
recording the task's success, the recovery reads the commit and completes
the task from it; it does not run the call a second time. A call that did
not commit - the executor died while it was running - is run again, as
before.
"""
from __future__ import annotations

import asyncio

import pytest

from friday import contracts as c
from friday import objectives as O
from friday.continuous import ContinuousTaskExecutor, RunWatchdog
from friday.store import Store


class _Died(BaseException):
    """The executor's process ending: not an error it handles."""


def _compile(store):
    created = O.compile_objective(
        store, request="write the note", objective_summary="write the note",
        tasks=[{"capability": "files_create", "arguments": {"path": "note.txt", "content": "x"}}],
        manifest=[{"id": "files_create", "description": "create"}],
        provenance=c.PERSON, attended=True)
    return created["run_id"]


def _expire_lease(store, run_id):
    store._conn.execute("UPDATE objective_runs SET lease_expiry='2000-01-01T00:00:00+00:00', "
                        "next_wake=NULL WHERE run_id=?", (run_id,))


async def _recover(store, run_id, port):
    executor = ContinuousTaskExecutor(store, call_capability=port, executor_id="second")
    executor.stop()
    watchdog = RunWatchdog(executor, lease_timeout=0.1)
    await watchdog.sweep_once()
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 10
    while loop.time() < deadline:
        if store.objective_run(run_id)["status"] in O.RUN_TERMINAL:
            break
        await asyncio.sleep(0.05)


def test_a_committed_side_effect_is_not_done_again_after_a_crash(tmp_path):
    store = Store(tmp_path / "ada.sqlite3")
    run_id = _compile(store)
    effects: list[str] = []

    async def port(capability, arguments):
        effects.append(capability)
        return {"status": "succeeded", "output": {"path": "note.txt"},
                "side_effects": ["wrote note.txt"]}

    real_update = store.update_objective_task

    def dies_while_recording_success(task_id, **fields):
        if fields.get("status") == O.TaskStatus.SUCCEEDED:
            raise _Died()
        return real_update(task_id, **fields)

    store.update_objective_task = dies_while_recording_success
    first = ContinuousTaskExecutor(store, call_capability=port, executor_id="first")
    first.stop()
    with pytest.raises(_Died):
        asyncio.run(first.start(run_id))
    store.update_objective_task = real_update
    assert effects == ["files_create"]
    assert [e for e in store.objective_events(run_id)
            if e["event"] == O.EVENT_SIDE_EFFECT_COMMITTED], "the commit was not recorded first"

    _expire_lease(store, run_id)
    asyncio.run(_recover(store, run_id, port))
    assert effects == ["files_create"], "the side effect was done a second time"
    (task,) = store.objective_tasks(run_id)
    assert task["status"] == O.TaskStatus.SUCCEEDED
    assert store.objective_run(run_id)["status"] == O.RUN_COMPLETED
    assert any(e["event"] == O.EVENT_TASK_RECOVERED for e in store.objective_events(run_id))


def test_a_call_that_did_not_commit_is_run_again(tmp_path):
    store = Store(tmp_path / "ada.sqlite3")
    run_id = _compile(store)
    effects: list[str] = []

    async def dies_during_the_call(capability, arguments):
        effects.append("started")
        raise _Died()

    first = ContinuousTaskExecutor(store, call_capability=dies_during_the_call,
                                   executor_id="first")
    first.stop()
    with pytest.raises(_Died):
        asyncio.run(first.start(run_id))
    assert not [e for e in store.objective_events(run_id)
                if e["event"] == O.EVENT_SIDE_EFFECT_COMMITTED]

    async def port(capability, arguments):
        effects.append("done")
        return {"status": "succeeded", "output": {}, "side_effects": ["wrote note.txt"]}

    _expire_lease(store, run_id)
    asyncio.run(_recover(store, run_id, port))
    assert effects == ["started", "done"]
    assert store.objective_run(run_id)["status"] == O.RUN_COMPLETED


def test_a_side_effecting_call_gets_a_key_that_survives_the_rerun(tmp_path):
    store = Store(tmp_path / "ada.sqlite3")
    run_id = _compile(store)
    keys: list[str] = []

    async def died_before_commit(capability, arguments, idempotency_key=None):
        keys.append(idempotency_key)
        raise _Died()

    first = ContinuousTaskExecutor(store, call_capability=died_before_commit,
                                   executor_id="first")
    first.stop()
    with pytest.raises(_Died):
        asyncio.run(first.start(run_id))

    async def port(capability, arguments, idempotency_key=None):
        keys.append(idempotency_key)
        return {"status": "succeeded", "output": {}, "side_effects": ["wrote note.txt"]}

    _expire_lease(store, run_id)
    asyncio.run(_recover(store, run_id, port))
    (task,) = store.objective_tasks(run_id)
    assert keys == [f"{run_id}:{task['task_id']}"] * 2


def test_the_production_port_does_not_repeat_a_call_that_already_returned(tmp_path):
    """The call returned but the executor died before its commit: the port
    itself recorded the result under the key, so the re-run replays it."""
    import agent_friday

    store = Store(tmp_path / "ada.sqlite3")
    run_id = _compile(store)
    effects: list[str] = []

    async def local(capability, arguments, run=None):
        effects.append(capability)
        return {"status": "succeeded", "output": {"path": "note.txt"},
                "side_effects": ["wrote note.txt"]}

    port = agent_friday.objective_port(local, router_of=lambda: None,
                                       call_via_router=None, store=store)

    real_append = store.append_objective_event

    def dies_before_the_commit(rid, event, **kw):
        if event == O.EVENT_SIDE_EFFECT_COMMITTED:
            raise _Died()
        return real_append(rid, event, **kw)

    store.append_objective_event = dies_before_the_commit
    first = ContinuousTaskExecutor(store, call_capability=port, executor_id="first")
    first.stop()
    with pytest.raises(_Died):
        asyncio.run(first.start(run_id))
    store.append_objective_event = real_append
    assert effects == ["files_create"]
    assert not [e for e in store.objective_events(run_id)
                if e["event"] == O.EVENT_SIDE_EFFECT_COMMITTED]

    _expire_lease(store, run_id)
    asyncio.run(_recover(store, run_id, port))
    assert effects == ["files_create"], "the side effect was done a second time"
    assert store.objective_run(run_id)["status"] == O.RUN_COMPLETED


def test_the_event_log_alone_says_what_each_task_is(tmp_path):
    store = Store(tmp_path / "ada.sqlite3")
    run_id = _compile(store)

    async def port(capability, arguments):
        return {"status": "succeeded", "output": {}, "side_effects": ["wrote note.txt"]}

    real_update = store.update_objective_task

    def dies_while_recording_success(task_id, **fields):
        if fields.get("status") == O.TaskStatus.SUCCEEDED:
            raise _Died()
        return real_update(task_id, **fields)

    store.update_objective_task = dies_while_recording_success
    first = ContinuousTaskExecutor(store, call_capability=port, executor_id="first")
    first.stop()
    with pytest.raises(_Died):
        asyncio.run(first.start(run_id))
    store.update_objective_task = real_update
    (task,) = store.objective_tasks(run_id)
    assert task["status"] != O.TaskStatus.SUCCEEDED  # the row lags the log
    replayed = O.replay_task_statuses(store.objective_events(run_id))
    assert replayed == {task["task_id"]: O.TaskStatus.SUCCEEDED}
