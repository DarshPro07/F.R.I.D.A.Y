"""OBJ-004 acceptance: a three-item batch with one failure retries only that
item, and a restart duplicates nothing.

The real Store (on a file, so a "restart" is a new Store and a new executor
over the same database) and the real ContinuousTaskExecutor; only the tool
port is scripted. The first process is stopped after one portion, between
the failed attempt and its retry - the case an in-process test cannot reach.
"""
from __future__ import annotations

import asyncio
from collections import Counter

import pytest

from friday import objectives as O
from friday.continuous import ContinuousTaskExecutor
from friday.contracts import PERSON, RESUME_FAILED_ITEMS_ONLY, ObjectiveContract
from friday.objectives import FailureKind, TaskStatus, compile_objective
from friday.store import Store

ITEMS = ("alpha", "bravo", "charlie")
FAILING_ITEM = "bravo"


class BatchPort:
    """Records every call per item. `failures` names the items whose next call
    drops the link; each entry is spent by one call."""

    def __init__(self, failures: tuple[str, ...] = ()) -> None:
        self.failures = list(failures)
        self.calls: list[str] = []

    async def __call__(self, capability: str, arguments: dict):
        item = arguments["item"]
        self.calls.append(item)
        if item in self.failures:
            self.failures.remove(item)
            raise ConnectionResetError(f"connection reset while processing {item}")
        return {"ok": True, "capability": capability, "item": item}


def compile_batch(store: Store) -> str:
    """Three independent items: no dependencies, one task each."""
    run = compile_objective(
        store, request="process the batch",
        tasks=[{"capability": "process_item", "arguments": {"item": item}}
               for item in ITEMS],
        manifest=[{"id": "process_item", "description": "process one batch item"}],
        objective_summary="process the batch", provenance=PERSON, attended=True)
    return run["run_id"]


def by_item(store: Store, run_id: str) -> dict:
    return {row["arguments"]["item"]: row for row in store.objective_tasks(run_id)}


def events(store: Store, run_id: str, name: str) -> list[dict]:
    return [e for e in store.objective_events(run_id) if e["event"] == name]


@pytest.mark.asyncio
async def test_a_batch_retries_only_the_failed_item_and_a_restart_duplicates_nothing(tmp_path):
    db = str(tmp_path / "batch.sqlite3")

    # Process one: the whole batch is attempted in one portion. One item
    # drops the link, so it is parked for a retry; the other two succeed.
    before = Store(db)
    run_id = compile_batch(before)
    first_port = BatchPort(failures=(FAILING_ITEM,))
    first = ContinuousTaskExecutor(before, first_port, executor_id="process-1")
    first.portion_budget = len(ITEMS)
    await first.start(run_id)
    first.stop()

    assert Counter(first_port.calls) == {item: 1 for item in ITEMS}
    tasks = by_item(before, run_id)
    assert {item: tasks[item]["status"] for item in ITEMS} == {
        "alpha": TaskStatus.SUCCEEDED,
        FAILING_ITEM: TaskStatus.WAITING,
        "charlie": TaskStatus.SUCCEEDED}
    assert tasks[FAILING_ITEM]["failure_kind"] == FailureKind.CONNECTIVITY
    assert before.objective_run(run_id)["status"] not in O.RUN_TERMINAL
    failed_task_id = tasks[FAILING_ITEM]["task_id"]
    del before                                    # the process dies here

    # Process two: a new Store and a new executor over the same file. It must
    # find the per-item state and run the failed item alone.
    after = Store(db)
    second_port = BatchPort()
    second = ContinuousTaskExecutor(after, second_port, executor_id="process-2")
    await second.start(run_id)
    second.stop()

    assert second_port.calls == [FAILING_ITEM], \
        f"the restart re-ran finished items or skipped the failed one: {second_port.calls}"
    tasks = by_item(after, run_id)
    assert {item: (row["status"], row["attempts"]) for item, row in tasks.items()} == {
        "alpha": (TaskStatus.SUCCEEDED, 1),
        FAILING_ITEM: (TaskStatus.SUCCEEDED, 2),
        "charlie": (TaskStatus.SUCCEEDED, 1)}
    assert after.objective_run(run_id)["status"] == O.RunStatus.COMPLETED
    assert [e["task_id"] for e in events(after, run_id, "task.retry")] == [failed_task_id]
    assert len(events(after, run_id, "run.completed")) == 1
    del after

    # Process three: restarting a finished batch changes nothing.
    again = Store(db)
    third_port = BatchPort()
    third = ContinuousTaskExecutor(again, third_port, executor_id="process-3")
    await third.start(run_id)
    third.stop()

    assert third_port.calls == [], "a finished batch was executed again after a restart"
    assert {row["attempts"] for row in again.objective_tasks(run_id)} == {1, 2}
    assert again.objective_run(run_id)["status"] == O.RunStatus.COMPLETED
    assert len(events(again, run_id, "run.completed")) == 1
    assert len(events(again, run_id, "task.retry")) == 1


@pytest.mark.asyncio
async def test_a_narrowed_batch_retry_is_not_answered_with_the_first_attempts_replayed_result(tmp_path):
    """The live port replays the result recorded under an idempotency key. A
    retry narrowed to the failed item is a different request, so it must not
    share the first attempt's key, or the failed item is never really retried.
    The real objective port and Store; only the capability is scripted."""
    import agent_friday

    db = str(tmp_path / "narrowed.sqlite3")
    calls: list[list[str]] = []

    async def local(capability, arguments, run=None):
        calls.append(list(arguments["items"]))
        # FAILING_ITEM fails the first time it is tried and succeeds the second.
        rows = [{"item": i, "ok": not (i == FAILING_ITEM and len(calls) == 1)}
                for i in arguments["items"]]
        return {"status": "succeeded", "output": {"items": rows},
                "side_effects": ["processed " + ", ".join(arguments["items"])]}

    def executor_on(store: Store, executor_id: str) -> ContinuousTaskExecutor:
        port = agent_friday.objective_port(local, router_of=lambda: None,
                                           call_via_router=None, store=store)
        return ContinuousTaskExecutor(store, call_capability=port, executor_id=executor_id)

    before = Store(db)
    run_id = compile_objective(
        before, request="process the batch, retry only the failed ones",
        tasks=[{"capability": "files_create", "arguments": {"items": list(ITEMS)}}],
        manifest=[{"id": "files_create", "description": "process the batch items"}],
        objective_summary="process the batch", provenance=PERSON, attended=True,
        contract=ObjectiveContract(goal="process the batch",
                                   governance={"recovery": RESUME_FAILED_ITEMS_ONLY}))["run_id"]
    first = executor_on(before, "process-1")
    first.portion_budget = 1
    await first.start(run_id)
    first.stop()
    assert calls == [list(ITEMS)]
    assert before.objective_run(run_id)["status"] not in O.RUN_TERMINAL
    del before                                    # the process dies here

    after = Store(db)
    second = executor_on(after, "process-2")
    try:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 20
        while (loop.time() < deadline
               and after.objective_run(run_id)["status"] not in O.RUN_TERMINAL):
            await second.start(run_id)
            await asyncio.sleep(0.02)
    finally:
        second.stop()

    assert calls == [list(ITEMS), [FAILING_ITEM]], \
        f"the narrowed retry was answered from the first attempt's recorded call: {calls}"
    (task,) = after.objective_tasks(run_id)
    assert task["detail"]["item_outcomes"] == {item: True for item in ITEMS}
    assert after.objective_run(run_id)["status"] == O.RunStatus.COMPLETED
