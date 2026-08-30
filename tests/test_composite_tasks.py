"""
A group of steps the boss reads as one line and the executor runs as many.

An audit of everything Friday can do is 125 checks. As 125 top-level tasks
it is a correct graph and an unreadable answer - the thing the audit planner
was written to stop producing. A composite is the shape that fixes it:

    the boss sees        "files: 11 capabilities"          one row
    the executor sees    files_read, files_info, ...       eleven rows
    the status says      what happened, not what was tried

What is proved here: a group is never dispatched, it settles only when every
child is done, its status is derived by the run's own PARTIAL rule one level
down, and nothing - not the compiler, not a plan - may declare a child
SUCCEEDED without it running.
"""
from __future__ import annotations
import asyncio
from friday import objectives as O
from friday.continuous import ContinuousTaskExecutor, speak
from friday.objectives import COMPOSITE, TaskStatus, compile_objective
from friday.store import Store
MANIFEST = [{'id': name} for name in ('system_get_info', 'apps_open', 'files_read', 'files_info')]


def a_store(tmp_path) -> Store:
    return Store(tmp_path / "composite.sqlite3")


def a_group(*children, **rest) -> dict:
    spec = {"capability": COMPOSITE, "children": list(children)}
    spec.update(rest)
    return spec


def compiled(store, tasks, *, request="audit the files") -> str:
    run = compile_objective(store, request=request, tasks=tasks,
                            manifest=MANIFEST, objective_summary=request)
    return run["run_id"]


def rows(store, run_id):
    return {t["task_id"]: t for t in store.objective_tasks(run_id)}


def an_executor(store, registry) -> ContinuousTaskExecutor:
    """Settlement only - no dispatch, so no driver loop is wanted.

    The loop is cancelled rather than left pending on purpose: a stray
    "coroutine was never awaited" here would be indistinguishable from the
    real one, and a real one is how every async capability broke silently
    once already.
    """

    async def call(capability, arguments):
        return registry[capability](arguments)
    asyncio.set_event_loop(None)
    executor = ContinuousTaskExecutor(store, call)
    executor.stop()
    return executor


def drive(store, registry, run_id) -> list[str]:
    """Run the graph to a standstill and report what was dispatched.

    Built inside the loop it runs on: an executor constructed in a sync body
    schedules its driver on whatever loop `get_event_loop` hands back, which
    is not the one `asyncio.run` then creates.
    """
    called = []

    async def go():
        async def call(capability, arguments):
            called.append(capability)
            return registry[capability](arguments)
        executor = ContinuousTaskExecutor(store, call)
        executor.stop()
        await executor._drive_until_done(run_id)
    asyncio.run(go())
    return called


def _split(store, run_id):
    tasks = store.objective_tasks(run_id)
    group = [t for t in tasks if not t.get("parent_id")][0]["task_id"]
    kids = [t["task_id"] for t in tasks if t.get("parent_id")]
    return group, kids


def test_a_group_is_one_row_and_its_children_are_rows_underneath(tmp_path):
    store = a_store(tmp_path)
    run_id = compiled(store, [a_group(
        {"capability": "files_read", "arguments": {"path": "a"}},
        {"capability": "files_info", "arguments": {"path": "a"}},
    )])

    tasks = store.objective_tasks(run_id)
    top = [t for t in tasks if not t.get("parent_id")]
    kids = [t for t in tasks if t.get("parent_id")]

    assert len(top) == 1 and top[0]["capability"] == COMPOSITE
    assert len(kids) == 2
    assert {kid["parent_id"] for kid in kids} == {top[0]["task_id"]}
    assert {kid["capability"] for kid in kids} == {"files_read", "files_info"}


def test_a_group_is_never_dispatched(tmp_path):
    """It is a view over children, not a step. `composite` is not a tool."""
    store = a_store(tmp_path)
    run_id = compiled(store, [a_group({'capability': 'files_read', 'arguments': {'path': 'a'}})])
    called = drive(store, {'files_read': lambda _a: {'ok': True}}, run_id)
    assert COMPOSITE not in called, 'the group itself was sent to a tool'
    assert called == ['files_read']


def test_a_group_waits_for_every_child(tmp_path):
    store = a_store(tmp_path)
    run_id = compiled(store, [a_group(
        {"capability": "files_read", "arguments": {}},
        {"capability": "files_info", "arguments": {}},
    )])
    group, kids = _split(store, run_id)
    executor = an_executor(store, {})

    store.update_objective_task(kids[0], status=TaskStatus.SUCCEEDED)
    executor._settle_composites(run_id)
    assert rows(store, run_id)[group]["status"] not in O.TASK_TERMINAL

    store.update_objective_task(kids[1], status=TaskStatus.SUCCEEDED)
    executor._settle_composites(run_id)
    assert rows(store, run_id)[group]["status"] == TaskStatus.SUCCEEDED


def test_one_child_succeeding_means_the_group_did_something(tmp_path):
    """
    The run's own PARTIAL rule, one level down. An audit group where three of
    eleven capabilities are broken audited eleven capabilities - the three
    are its finding, not its status.
    """
    store = a_store(tmp_path)
    run_id = compiled(store, [a_group(
        {"capability": "files_read", "arguments": {}},
        {"capability": "files_info", "arguments": {}},
    )])
    group, kids = _split(store, run_id)

    store.update_objective_task(kids[0], status=TaskStatus.SUCCEEDED)
    store.update_objective_task(kids[1], status=TaskStatus.FAILED,
                                failure_kind=O.FailureKind.STRUCTURAL,
                                evidence="no such file")
    an_executor(store, {})._settle_composites(run_id)

    settled = rows(store, run_id)[group]
    assert settled["status"] == TaskStatus.SUCCEEDED
    assert settled["result"]["tally"] == {TaskStatus.SUCCEEDED: 1,
                                          TaskStatus.FAILED: 1}
    assert settled["result"]["failures"][0]["capability"] == "files_info"


def test_a_group_where_nothing_succeeded_failed(tmp_path):
    store = a_store(tmp_path)
    run_id = compiled(store, [a_group(
        {"capability": "files_read", "arguments": {}},
        {"capability": "files_info", "arguments": {}},
    )])
    group, kids = _split(store, run_id)
    for kid in kids:
        store.update_objective_task(kid, status=TaskStatus.FAILED,
                                    failure_kind=O.FailureKind.STRUCTURAL)
    an_executor(store, {})._settle_composites(run_id)

    assert rows(store, run_id)[group]["status"] == TaskStatus.FAILED


def test_a_group_nothing_could_run_is_skipped_not_failed(tmp_path):
    """
    Every capability in it needs a person to say yes. That is an answer with
    a name, and calling it a failure would report a gap that is not there.
    """
    store = a_store(tmp_path)
    run_id = compiled(store, [a_group(
        {"capability": "files_read", "skipped_because": "needs approval"},
        {"capability": "files_info", "skipped_because": "needs approval"},
    )])
    group, _kids = _split(store, run_id)
    an_executor(store, {})._settle_composites(run_id)

    assert rows(store, run_id)[group]["status"] == TaskStatus.SKIPPED


def test_a_child_may_be_skipped_with_a_reason_before_anything_runs(tmp_path):
    store = a_store(tmp_path)
    run_id = compiled(store, [a_group(
        {"capability": "files_read", "arguments": {}},
        {"capability": "files_info",
         "skipped_because": "a person has to say yes"},
    )])

    skipped = [t for t in store.objective_tasks(run_id)
               if t["capability"] == "files_info"][0]
    assert skipped["status"] == TaskStatus.SKIPPED
    assert skipped["evidence"] == "a person has to say yes"
    assert skipped["failure_kind"] is None, "a named reason is not a failure"


def test_a_plan_cannot_declare_a_child_already_succeeded(tmp_path):
    """
    The one thing pre-settlement must never reach. A task marked SUCCEEDED
    without running is evidence for work nobody did, which is the failure
    this whole codebase is built against.
    """
    store = a_store(tmp_path)
    run_id = compiled(store, [a_group(
        {"capability": "files_read", "status": "SUCCEEDED",
         "result": {"ok": True}},
    )])

    kid = [t for t in store.objective_tasks(run_id) if t.get("parent_id")][0]
    assert kid["status"] != TaskStatus.SUCCEEDED
    assert kid["result"] in (None, {}, "")


def test_a_hallucinated_child_fails_at_compile_like_any_other(tmp_path):
    store = a_store(tmp_path)
    run_id = compiled(store, [a_group(
        {"capability": "magic_super_tool", "arguments": {}},
        {"capability": "files_read", "arguments": {}},
    )])

    kid = [t for t in store.objective_tasks(run_id)
           if t["capability"] == "magic_super_tool"][0]
    assert kid["status"] == TaskStatus.FAILED
    assert kid["failure_kind"] == O.FailureKind.CAPABILITY_MISSING


def test_the_headline_counts_groups_and_the_failures_are_still_named(tmp_path):
    store = a_store(tmp_path)
    run_id = compiled(store, [a_group({'capability': 'files_read', 'arguments': {}}, {'capability': 'files_info', 'arguments': {}})])

    def broken(_args):
        raise ValueError('no such file')
    drive(store, {'files_read': lambda _a: {'ok': True}, 'files_info': broken}, run_id)
    summary = store.objective_run(run_id)['summary']
    assert summary['succeeded'] == 1, 'the two children were counted as tasks'
    assert summary['checks'] == 2
    assert summary['checks_failed'] == 1
    assert any((f['capability'] == 'files_info' for f in summary['failures']))
    said = speak(store, run_id)
    assert '1 task succeeded' in said, said
    assert '1 of 2 checks' in said, said
    assert 'files_info' in said, said


def test_progress_is_spoken_in_groups_not_children(tmp_path):
    store = a_store(tmp_path)
    run_id = compiled(store, [
        a_group({"capability": "files_read", "arguments": {}},
                {"capability": "files_info", "arguments": {}}),
        a_group({"capability": "apps_open", "arguments": {"name": "Paint"}}),
    ])

    said = speak(store, run_id)
    assert "of 2 steps" in said, said
