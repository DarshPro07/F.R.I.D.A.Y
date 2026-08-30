"""
A provider outage may end an attempt. It must never end the objective.

`all LLMs are unavailable, retrying...` is a symptom, and on its own it says
nothing about what to do next. What matters is the error immediately before
it:

    429 RESOURCE_EXHAUSTED      rate or quota pressure     wait, then ask again
    503 UNAVAILABLE, timeout    the service is struggling  wait, then ask again
    400 missing thought_signature   the request is malformed   never blindly
    UNEXPECTED_TOOL_CALL        tool protocol state        never blindly

Every one of those used to classify as STRUCTURAL, which is not retryable. So
a Google capacity blip lasting seconds during one task marked that task
permanently failed and skipped everything downstream of it - measured, all
four conditions, retryable=False.

These gates hold two things apart: an outage is waited out, and a malformed
request is not sent twice.
"""

from __future__ import annotations

import asyncio

import pytest
from livekit.agents._exceptions import APIConnectionError, APIStatusError

from friday import objectives as O
from friday.continuous import ContinuousTaskExecutor, FailureClassifier
from friday.objectives import FailureKind, TaskStatus, compile_objective
from friday.store import Store


class Registry:
    """A registry whose behaviour is scripted per capability."""

    def __init__(self) -> None:
        self.behaviour: dict[str, list] = {}
        self.calls: list[str] = []

    def script(self, capability: str, *outcomes) -> None:
        self.behaviour[capability] = list(outcomes)

    async def call(self, capability: str, arguments: dict) -> dict:
        self.calls.append(capability)
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
def registry() -> Registry:
    return Registry()


@pytest.fixture
def executor(store, registry) -> ContinuousTaskExecutor:
    return ContinuousTaskExecutor(store, registry.call, executor_id="outage")


def compile_graph(store, tasks: list[dict]) -> dict:
    return compile_objective(
        store, request="provider outage probe", tasks=tasks,
        manifest=[{"id": f"cap{i}", "description": f"cap {i}"}
                  for i in range(1, len(tasks) + 1)],
        objective_summary="probe")


def by_plan_id(store, run_id: str) -> dict:
    return {row["task_id"].rsplit("-", 1)[-1]: row
            for row in store.objective_tasks(run_id)}


# ---------------------------------------------------------------------------
# Telling an outage from a bug
# ---------------------------------------------------------------------------

OUTAGES = [
    ("503 the service is struggling",
     APIStatusError("gemini llm: server error", status_code=503,
                    body="UNAVAILABLE")),
    ("429 rate or quota pressure",
     APIStatusError("gemini llm: client error", status_code=429,
                    body="RESOURCE_EXHAUSTED")),
    ("every configured model unavailable",
     APIConnectionError("all LLMs failed after 4.8 seconds")),
]

BUGS = [
    ("a function call with no thought signature",
     APIStatusError("gemini llm: client error", status_code=400,
                    body="Function call is missing a thought_signature "
                         "INVALID_ARGUMENT")),
    ("a tool call where no tool was declared",
     APIStatusError("no response generated", status_code=-1,
                    body="finish reason: FinishReason.UNEXPECTED_TOOL_CALL")),
]


@pytest.mark.parametrize("name, error", OUTAGES,
                         ids=[case[0].replace(" ", "-") for case in OUTAGES])
def test_an_outage_is_worth_waiting_for(name, error):
    kind = FailureClassifier.classify(error)
    assert kind == FailureKind.PROVIDER_DOWN, f"{name} -> {kind}"
    assert kind in O.RETRYABLE_KINDS


@pytest.mark.parametrize("name, error", BUGS,
                         ids=[case[0].replace(" ", "-") for case in BUGS])
def test_a_malformed_request_is_not_sent_twice(name, error):
    """
    The second attempt sends the same impossible request. Retrying it costs a
    round trip, tokens and a second of silence to be told the same thing.
    """
    kind = FailureClassifier.classify(error)
    assert kind == FailureKind.STRUCTURAL, f"{name} -> {kind}"
    assert kind not in O.RETRYABLE_KINDS


def test_an_ordinary_bug_is_still_structural():
    """The classifier must not read every exception as the provider's fault."""
    assert FailureClassifier.classify(
        AttributeError("'NoneType' object has no attribute 'x'")
    ) == FailureKind.STRUCTURAL


# ---------------------------------------------------------------------------
# The invariant
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_outage_does_not_end_the_run(store, registry, executor):
    """
    The one that matters. A provider outage may stop the current attempt; the
    durable objective must still have a future.
    """
    registry.script("cap1", APIStatusError("gemini llm: server error",
                                           status_code=503, body="UNAVAILABLE"))
    run = compile_graph(store, [{"capability": "cap1", "arguments": {}}])
    run_id = run["run_id"]

    await executor.start(run_id)

    task = by_plan_id(store, run_id)["t1"]
    assert task["status"] != TaskStatus.FAILED, \
        "an outage marked the task permanently failed"
    assert task["failure_kind"] == FailureKind.PROVIDER_DOWN
    assert task["next_wake"], "the task has no future"

    row = store.objective_run(run_id)
    assert row["status"] not in O.RUN_TERMINAL, \
        "a provider outage ended the objective"
    assert row["next_wake"], "the run has no scheduled continuation"


@pytest.mark.asyncio
async def test_the_task_succeeds_once_the_provider_comes_back(store, registry,
                                                              executor):
    """An outage is a delay, not a verdict."""
    registry.script("cap1",
                    APIStatusError("gemini llm: server error",
                                   status_code=503, body="UNAVAILABLE"),
                    {"ok": True})
    run = compile_graph(store, [{"capability": "cap1", "arguments": {}}])
    run_id = run["run_id"]

    await executor.start(run_id)
    # The wake is deliberately in the future, so bring it forward rather than
    # waiting out the backoff in a test.
    for row in store.objective_tasks(run_id):
        store.update_objective_task(row["task_id"], next_wake=None)
    store.touch_objective_run(run_id, next_wake=None)
    await executor.start(run_id)

    assert by_plan_id(store, run_id)["t1"]["status"] == TaskStatus.SUCCEEDED
    assert registry.calls == ["cap1", "cap1"], registry.calls


@pytest.mark.asyncio
async def test_independent_work_continues_through_an_outage(store, registry,
                                                            executor):
    """
    One model-backed task waiting on a provider must not hold up work that
    needs no model at all.
    """
    registry.script("cap1", APIStatusError("gemini llm: server error",
                                           status_code=503, body="UNAVAILABLE"))
    registry.script("cap2", {"ok": True})
    run = compile_graph(store, [
        {"capability": "cap1", "arguments": {}},
        {"capability": "cap2", "arguments": {}},
    ])
    run_id = run["run_id"]

    await executor.start(run_id)

    tasks = by_plan_id(store, run_id)
    assert tasks["t2"]["status"] == TaskStatus.SUCCEEDED, \
        "independent work was blocked by an unrelated provider outage"
    assert tasks["t1"]["status"] != TaskStatus.FAILED


@pytest.mark.asyncio
async def test_an_outage_does_not_skip_dependents(store, registry, executor):
    """
    A dependent of a *failed* task is skipped, and rightly. A dependent of a
    task that is merely waiting for a provider is not - the dependency has not
    failed, it has not happened yet.
    """
    registry.script("cap1", APIStatusError("gemini llm: server error",
                                           status_code=503, body="UNAVAILABLE"))
    run = compile_graph(store, [
        {"capability": "cap1", "arguments": {}},
        {"capability": "cap2", "arguments": {}, "dependencies": ["t1"]},
    ])
    run_id = run["run_id"]

    await executor.start(run_id)

    assert by_plan_id(store, run_id)["t2"]["status"] != TaskStatus.SKIPPED, \
        "a dependent was skipped because its dependency was waiting"


@pytest.mark.asyncio
async def test_a_structural_provider_bug_still_fails_the_task(store, registry,
                                                              executor):
    """
    The other half. A malformed request must not be waited on forever - it
    fails, its dependents skip, and the rest of the run continues.
    """
    registry.script("cap1", APIStatusError(
        "gemini llm: client error", status_code=400,
        body="Function call is missing a thought_signature INVALID_ARGUMENT"))
    run = compile_graph(store, [{"capability": "cap1", "arguments": {}}])
    run_id = run["run_id"]

    await executor.start(run_id)

    task = by_plan_id(store, run_id)["t1"]
    assert task["status"] == TaskStatus.FAILED
    assert task["failure_kind"] == FailureKind.STRUCTURAL
    assert registry.calls == ["cap1"], \
        f"a malformed request was sent {len(registry.calls)} times"


@pytest.mark.asyncio
async def test_an_outage_is_logged_with_what_to_do_about_it(store, registry,
                                                            executor, caplog):
    """
    "all LLMs are unavailable" says nothing actionable. Which provider, which
    code, and whether it is worth asking again is what a reader needs.
    """
    import logging

    registry.script("cap1", APIStatusError("gemini llm: server error",
                                           status_code=503, body="UNAVAILABLE"))
    run = compile_graph(store, [{"capability": "cap1", "arguments": {}}])

    with caplog.at_level(logging.INFO):
        await executor.start(run["run_id"])

    line = " ".join(record.message for record in caplog.records)
    assert "provider.failure" in line, line[:300]
    assert "class=TRANSIENT_PROVIDER" in line
    assert "status_code=503" in line
    assert "retryable=True" in line
