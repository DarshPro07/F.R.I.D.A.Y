"""
ORCH-013: a finished task whose result invalidates an assumption cancels the
still-running tasks built on it, records why, and touches nothing else.
"""

from __future__ import annotations

import pytest

from friday import capabilities
from friday import contracts as c
from friday import objectives as O
from friday import requirements as R
from friday.store import Store

KEY_STATEMENT = "Save format? -> markdown"


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "ada.sqlite3")


def _entry(statement=KEY_STATEMENT) -> R.Assumption:
    return R.Assumption(statement=statement, origin="understand:save format",
                        confidence=R.HIGH, consequence=R.MEDIUM,
                        validation="", state=R.UNVALIDATED)


def _plan(store, assumes_by_index):
    """Six independent tasks; index -> the assumption keys that task rests on."""
    tasks = []
    for index in range(6):
        spec = {"capability": "files_read", "arguments": {"path": f"f{index}.md"}}
        if index in assumes_by_index:
            spec["assumes"] = list(assumes_by_index[index])
        tasks.append(spec)
    run = O.compile_objective(
        store, request="read the notes", tasks=tasks,
        manifest=capabilities.as_dicts(), objective_summary="read the notes",
        provenance=c.PERSON, attended=True,
        assumptions=(_entry(), _entry("Other premise")))
    return run["run_id"], [f"{run['run_id']}-t{i}" for i in range(1, 7)]


def _set(store, task_id, status):
    store.update_objective_task(task_id, status=status)


def _status(store, task_id):
    return store.objective_task(task_id)["status"]


def _setup(store):
    key, other = _entry().key, _entry("Other premise").key
    run_id, ids = _plan(store, {0: [key], 1: [key], 2: [other], 4: [key], 5: [key]})
    t1, t2, t3, t4, t5, t6 = ids
    _set(store, t1, O.TaskStatus.SUCCEEDED)      # the finished task that refutes
    _set(store, t2, O.TaskStatus.RUNNING)        # built on it, running: cancelled
    _set(store, t3, O.TaskStatus.RUNNING)        # built on another premise
    _set(store, t4, O.TaskStatus.RUNNING)        # assumes nothing
    _set(store, t5, O.TaskStatus.READY)          # built on it, not started
    _set(store, t6, O.TaskStatus.SUCCEEDED)      # built on it, already done
    return key, run_id, ids


def test_a_task_records_which_assumptions_it_rests_on(store):
    key, run_id, ids = _setup(store)

    assert store.objective_task(ids[1])["detail"]["assumes"] == [key]
    assert "assumes" not in (store.objective_task(ids[3])["detail"] or {})


def test_only_running_tasks_built_on_the_refuted_assumption_are_cancelled(store):
    key, run_id, ids = _setup(store)
    t1, t2, t3, t4, t5, t6 = ids

    cancelled = O.invalidate_assumption(
        store, run_id, key, by_task=t1, reason="the reader opens html, not markdown",
        executor_id="test")

    assert cancelled == [t2]
    assert _status(store, t2) == O.TaskStatus.INTERRUPTED
    assert [_status(store, t) for t in (t1, t3, t4, t5, t6)] == [
        O.TaskStatus.SUCCEEDED, O.TaskStatus.RUNNING, O.TaskStatus.RUNNING,
        O.TaskStatus.READY, O.TaskStatus.SUCCEEDED]
    assert store.objective_run(run_id)["status"] == O.RUN_RUNNING


def test_the_cancellation_records_the_reason_the_task_and_the_assumption(store):
    key, run_id, ids = _setup(store)
    t1, t2 = ids[0], ids[1]

    O.invalidate_assumption(store, run_id, key, by_task=t1,
                            reason="the reader opens html", executor_id="test")

    row = store.objective_task(t2)
    assert "the reader opens html" in row["evidence"]
    assert row["finished_at"]
    events = store.recent_objective_events(run_id)
    interrupted = [e for e in events if e["event"] == O.EVENT_TASK_INTERRUPTED]
    assert [e["task_id"] for e in interrupted] == [t2]
    assert interrupted[0]["detail"]["assumption"] == key
    assert interrupted[0]["detail"]["invalidated_by"] == t1
    (invalidated,) = [e for e in events if e["event"] == O.EVENT_ASSUMPTION_INVALIDATED]
    assert invalidated["detail"]["cancelled"] == [t2]
    assert invalidated["detail"]["replan"] is True
    (state,) = [r["state"] for r in store.list_assumptions(run_id) if r["key"] == key]
    assert state == R.REFUTED


def test_the_cancellation_is_marked_so_the_scheduler_does_not_rerun_it(store):
    key, run_id, ids = _setup(store)

    O.invalidate_assumption(store, run_id, key, by_task=ids[0], reason="r",
                            executor_id="test")

    row = store.objective_task(ids[1])
    assert O.invalidated_assumption(row) == key
    assert row["detail"]["invalidated_by"] == ids[0]
    assert row["detail"]["assumption"] == key
    # a watchdog interruption carries no such marker
    _set(store, ids[2], O.TaskStatus.INTERRUPTED)
    assert O.invalidated_assumption(store.objective_task(ids[2])) == ""


def test_a_task_waiting_on_a_worker_it_started_is_cancelled_too(store):
    key = _entry().key
    run_id, ids = _plan(store, {1: [key], 2: [key], 3: [key]})
    t1, t2, t3, t4 = ids[:4]
    _set(store, t1, O.TaskStatus.SUCCEEDED)
    store.update_objective_task(
        t2, status=O.TaskStatus.WAITING, result={"work_run_id": "w-1"})
    _set(store, t3, O.TaskStatus.WAITING)        # waiting, but no worker yet
    store.update_objective_task(
        t4, status=O.TaskStatus.WAITING, result={"work_run_id": "w-2"})

    cancelled = O.invalidate_assumption(store, run_id, key, by_task=t1,
                                        reason="r", executor_id="test")

    assert sorted(cancelled) == sorted([t2, t4])
    assert _status(store, t2) == O.TaskStatus.INTERRUPTED
    assert _status(store, t4) == O.TaskStatus.INTERRUPTED
    assert _status(store, t3) == O.TaskStatus.WAITING


def test_the_task_that_found_the_problem_is_never_cancelled_by_it(store):
    key, run_id, ids = _setup(store)
    _set(store, ids[0], O.TaskStatus.RUNNING)    # t1 itself assumes the key

    cancelled = O.invalidate_assumption(store, run_id, key, by_task=ids[0],
                                        reason="r", executor_id="test")

    assert ids[0] not in cancelled
    assert _status(store, ids[0]) == O.TaskStatus.RUNNING


def test_invalidating_twice_cancels_nothing_the_second_time(store):
    key, run_id, ids = _setup(store)
    O.invalidate_assumption(store, run_id, key, by_task=ids[0], reason="r",
                            executor_id="test")
    before = store.recent_objective_events(run_id)

    again = O.invalidate_assumption(store, run_id, key, by_task=ids[0],
                                    reason="r", executor_id="test")

    assert again == []
    assert store.recent_objective_events(run_id) == before


def test_an_assumption_the_run_never_registered_changes_nothing(store):
    key, run_id, ids = _setup(store)

    cancelled = O.invalidate_assumption(store, run_id, "no-such-key",
                                        by_task=ids[0], reason="r",
                                        executor_id="test")

    assert cancelled == []
    assert _status(store, ids[1]) == O.TaskStatus.RUNNING
    assert not [e for e in store.recent_objective_events(run_id)
                if e["event"] == O.EVENT_ASSUMPTION_INVALIDATED]
