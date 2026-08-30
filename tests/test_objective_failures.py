"""
Failure-local transitions: one task failing must not sink the run.

Classification: TRANSIENT retries (bounded); STRUCTURAL / CAPABILITY_MISSING /
POLICY_BLOCK / INVALID_ARGUMENT / NOT_CONFIGURED fail terminally; dependents
of a failed task are skipped with blocked_by; independent tasks complete;
the run ends PARTIAL at worst, and summaries never claim more than happened.
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
import pytest
from friday.continuous import ContinuousTaskExecutor, FailureClassifier
from friday.objectives import FailureKind, TaskStatus, compile_objective
from friday.store import Store


class FlakyRegistry:
    """A registry whose behaviour is scripted per capability."""

    def __init__(self) -> None:
        self.behaviour: dict[str, list[Exception | dict]] = {}
        self.calls: list[tuple[str, dict]] = []
        self.recalled: set[str] = set()

    def script(self, capability: str, *outcomes) -> None:
        self.behaviour[capability] = list(outcomes)

    async def call(self, capability: str, arguments: dict) -> dict:
        self.calls.append((capability, arguments))
        self.recalled.add(capability)
        queue = self.behaviour.get(capability, [{"ok": True}])
        outcome = queue[0] if queue else {"ok": True}
        if len(queue) > 1:
            self.behaviour[capability] = queue[1:]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture
def store() -> Store:
    return Store(":memory:")


@pytest.fixture
def registry() -> FlakyRegistry:
    return FlakyRegistry()


@pytest.fixture
def executor(store, registry) -> ContinuousTaskExecutor:
    return ContinuousTaskExecutor(store, registry.call, executor_id="fail-exec")


def graph(*tasks: dict) -> list[dict]:
    return [
        {"capability": f"cap{i}", "arguments": {}, **task}
        for i, task in enumerate(tasks, start=1)
    ]


def compile_graph(store, registry, tasks: list[dict]) -> dict:
    return compile_objective(
        store,
        request="failure-local probe",
        tasks=tasks,
        manifest=[{"id": f"cap{i}", "description": f"cap {i}"}
                  for i in range(1, len(tasks) + 1)],
        objective_summary="probe",
    )


@pytest.mark.asyncio
async def test_failure_is_local(store, registry, executor):
    """A failed task skips its dependents; independents still complete."""
    registry.script('cap1', RuntimeError('boom'))
    registry.script('cap2', {'ok': True})
    registry.script('cap3', {'ok': True})
    registry.script('cap4', {'ok': True})
    run = compile_graph(store, registry, [{}, {'dependencies': ['t1']}, {}, {}])
    await executor.start(run['run_id'])
    tasks = by_plan_id(store, run['run_id'])
    assert tasks['t1']['status'] == TaskStatus.FAILED
    assert tasks['t1']['failure_kind'] == FailureKind.STRUCTURAL
    assert tasks['t2']['status'] == TaskStatus.SKIPPED
    assert plan_id(tasks['t2']['blocked_by']) == 't1'
    assert tasks['t3']['status'] == TaskStatus.SUCCEEDED
    assert tasks['t4']['status'] == TaskStatus.SUCCEEDED
    final = store.objective_run(run['run_id'])
    assert final['status'] == 'PARTIAL'
    assert final['summary']['failed'] == 1
    assert final['summary']['skipped'] == 1
    assert final['summary']['succeeded'] == 2


@pytest.mark.asyncio
async def test_transient_retry_succeeds(store, registry, executor):
    """TRANSIENT failures retry (bounded); attempts counts them."""
    registry.script("cap1", TimeoutError("network"), TimeoutError("network"),
                    {"ok": True, "finally": True})
    run = compile_graph(store, registry, [{}])

    await executor.start(run["run_id"])
    tasks = store.objective_tasks(run["run_id"])
    task = tasks[0]
    assert task["status"] == TaskStatus.SUCCEEDED
    assert task["attempts"] == 3
    assert task["evidence"] is not None

    final = store.objective_run(run["run_id"])
    assert final["status"] == "COMPLETED"


@pytest.mark.asyncio
async def test_transient_exhausted_is_terminal(store, registry, executor):
    """Exhausted retries terminate the task rather than looping forever."""
    registry.script("cap1", TimeoutError("network"))
    run = compile_graph(store, registry, [{}])

    await executor.start(run["run_id"])
    task = store.objective_tasks(run["run_id"])[0]
    assert task["status"] == TaskStatus.FAILED
    assert task["failure_kind"] == FailureKind.TRANSIENT
    assert task["attempts"] == executor.max_attempts

    final = store.objective_run(run["run_id"])
    assert final["status"] == "FAILED"


@pytest.mark.asyncio
async def test_capability_missing_not_recalled(store, registry, executor):
    """CAPABILITY_MISSING is terminal and never re-dispatched."""
    run = compile_graph(store, registry, [{'capability': 'cap1'}, {'capability': 'ghost_cap'}, {'capability': 'cap3'}])
    await executor.start(run['run_id'])
    tasks = by_plan_id(store, run['run_id'])
    assert tasks['t2']['status'] == TaskStatus.FAILED
    assert tasks['t2']['failure_kind'] == FailureKind.CAPABILITY_MISSING
    assert 'ghost_cap' not in registry.recalled
    assert tasks['t3']['status'] == TaskStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_invalid_arguments_are_terminal(store, registry, executor):
    registry.script("cap1", TypeError("bad argument"))
    run = compile_graph(store, registry, [{}])
    await executor.start(run["run_id"])
    task = store.objective_tasks(run["run_id"])[0]
    assert task["failure_kind"] == FailureKind.INVALID_ARGUMENT
    assert task["status"] == TaskStatus.FAILED


@pytest.mark.asyncio
async def test_policy_block_is_terminal(store, registry, executor):
    from friday.policy import PolicyError

    registry.script("cap1", PolicyError("not approved"))
    run = compile_graph(store, registry, [{}])
    await executor.start(run["run_id"])
    task = store.objective_tasks(run["run_id"])[0]
    assert task["failure_kind"] == FailureKind.POLICY_BLOCK
    assert task["status"] == TaskStatus.FAILED


@pytest.mark.asyncio
async def test_summary_honesty(store, registry, executor):
    """The summary JSON is literal: PARTIAL never claims all succeeded."""
    registry.script("cap1", RuntimeError("boom"))
    run = compile_graph(store, registry, [{}, {}])
    await executor.start(run["run_id"])

    final = store.objective_run(run["run_id"])
    assert final["status"] == "PARTIAL"
    summary = final["summary"]
    assert summary["succeeded"] + summary["failed"] + summary["skipped"] == 2
    assert summary["manual_continue_count"] == 0
    assert summary["duration_seconds"] >= 0
    assert summary["failures"][0]["kind"] == FailureKind.STRUCTURAL
    assert summary["failures"][0]["reason"]

    # Prose contract: a speaker function renders counts + reasons, never JSON.
    spoken = executor.speak_summary(run["run_id"])
    assert "two" in spoken or "1" in spoken
    assert "cap1" in spoken or "first" in spoken
    assert "{" not in spoken


def test_failure_classifier_mapping():
    from friday.policy import PolicyError

    assert FailureClassifier.classify(LookupError("x")) == \
        FailureKind.CAPABILITY_MISSING
    assert FailureClassifier.classify(PolicyError("x")) == \
        FailureKind.POLICY_BLOCK
    assert FailureClassifier.classify(ValueError("x")) == \
        FailureKind.INVALID_ARGUMENT
    assert FailureClassifier.classify(NotImplementedError("x")) == \
        FailureKind.NOT_CONFIGURED
    assert FailureClassifier.classify(TimeoutError("x")) == \
        FailureKind.TRANSIENT
    assert FailureClassifier.classify(RuntimeError("x")) == \
        FailureKind.STRUCTURAL


@pytest.mark.asyncio
async def test_a_refusing_result_is_not_a_success(store, registry, executor):
    registry.script('cap1', {'status': 'failed', 'tool_id': 'files_create', 'error': 'path refused: path is outside the permitted roots', 'may_claim_completion': False})
    run = compile_graph(store, registry, graph({}))
    await executor.start(run['run_id'])
    task = by_plan_id(store, run['run_id'])['t1']
    assert task['status'] == TaskStatus.FAILED, task['status']
    assert 'outside the permitted roots' in (task['evidence'] or '')


@pytest.mark.asyncio
async def test_the_refusal_keeps_the_name_it_came_with(store, registry, executor):
    """
    A policy refusal and a broken tool need different answers, and the
    ActionResult already knows which is which.
    """
    registry.script('cap1', {'status': 'not_permitted', 'error': 'denied'})
    registry.script('cap2', {'status': 'not_configured', 'error': 'no key'})
    run = compile_graph(store, registry, graph({}, {}))
    await executor.start(run['run_id'])
    tasks = by_plan_id(store, run['run_id'])
    assert tasks['t1']['failure_kind'] == FailureKind.POLICY_BLOCK
    assert tasks['t2']['failure_kind'] == FailureKind.NOT_CONFIGURED


@pytest.mark.asyncio
async def test_partial_work_is_not_called_a_failure(store, registry, executor):
    """
    PARTIAL means something happened. It is not claimable and it is not a
    failure either, and collapsing it into one would lose the distinction the
    ActionResult exists to carry.
    """
    registry.script('cap1', {'status': 'partial', 'error': '3 of 5 done'})
    run = compile_graph(store, registry, graph({}))
    await executor.start(run['run_id'])
    assert by_plan_id(store, run['run_id'])['t1']['status'] == TaskStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_a_result_that_says_nothing_still_means_it_worked(store, registry, executor):
    """
    "If it says how it went, believe it" - not "everything must say". A
    dispatcher returning a plain dict is what an MCP tool may do, and turning
    that into a failure would be a contract change wearing a guard's clothes.
    """
    registry.script('cap1', {'ok': True, 'pid': 4242})
    run = compile_graph(store, registry, graph({}))
    await executor.start(run['run_id'])
    assert by_plan_id(store, run['run_id'])['t1']['status'] == TaskStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_an_unplaceable_clause_is_named_not_the_marker(store, registry, executor):
    """
    FRIDAY-CORE-02: `UNMAPPED_CAPABILITY` must surface as a spoken gap, not a
    silent FAILED row.

    "unknown capability 'objective.unmapped'" is a true sentence about the
    marker that tells the boss nothing. What they need is which of the things
    they said went nowhere, and that clause was sitting in the arguments,
    being thrown away in favour of the marker's name.
    """
    from friday.continuous import speak
    from friday.objectives import UNMAPPED_CAPABILITY
    run = compile_objective(store, request='flurb the wibble, then check the computer', objective_summary='flurb the wibble, then check the computer', tasks=[{'capability': UNMAPPED_CAPABILITY, 'arguments': {'clause': 'flurb the wibble'}}, {'capability': 'cap2', 'arguments': {}}], manifest=[{'id': 'cap2'}])
    await executor.start(run['run_id'])
    task = by_plan_id(store, run['run_id'])['t1']
    assert task['status'] == TaskStatus.FAILED
    assert 'flurb the wibble' in task['evidence']
    assert 'objective.unmapped' not in task['evidence']
    said = speak(store, run['run_id'])
    assert 'flurb the wibble' in said, said
    assert 'objective.unmapped' not in said, said


@pytest.mark.asyncio
async def test_a_genuinely_unknown_capability_still_says_so(store, registry, executor):
    """The marker is a special case, not a licence to stop naming tools."""
    run = compile_objective(store, request='probe', objective_summary='probe', tasks=[{'capability': 'magic_super_tool', 'arguments': {}}], manifest=[{'id': 'cap1'}])
    task = by_plan_id(store, run['run_id'])['t1']
    assert 'magic_super_tool' in task['evidence']
