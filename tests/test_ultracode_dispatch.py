"""ULTRA-004: how a stage reaches a model.

A stage that needs tools or code is one bounded Hermes delegation; a tool-less
stage is one gateway inference whose answer is checked against the stage's
schema (one retry, then a classified failure); a provider that is not there
marks that stage UNAVAILABLE and the run goes on. Hermes and the gateway are
fakes injected into the port, so nothing here starts a real Hermes or calls a
real model. The engine is the real ContinuousTaskExecutor over a real Store.
"""
from __future__ import annotations

import asyncio
import json
import threading
from types import SimpleNamespace

import pytest

from friday import contracts as c
from friday import hermes_bridge as hb
from friday import objectives as O
from friday.continuous import ContinuousTaskExecutor, FailureClassifier
from friday.contracts import PERSON
from friday.model_gateway import ModelGatewayResult
from friday.store import Store
from friday.ultracode import runner as R
from friday.ultracode import spec as S

SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}


def _spec(*stages: tuple[str, str, str]) -> S.Spec:
    return S.parse_spec({
        "id": "demo", "budget_tokens": 100000,
        "stages": [{"id": name, "template": template, "schema": SCHEMA,
                    "tier": tier, "cap": 1000} for name, template, tier in stages]})


class OpenGate:
    def admit(self, run, stage, arguments) -> str:
        return ""

    def release(self, lease: str) -> None:
        pass

    def settle(self, run, stage, arguments) -> None:
        pass


OPEN = OpenGate()


def _model(stage: S.Stage) -> str:
    return f"model-{stage.tier}"


class FakeGateway:
    """`answers` is consumed in order, per item; each is a reply text or a
    ModelGatewayResult. Records the thread every call ran on."""

    def __init__(self, answers=None, *, by_item=None) -> None:
        self.answers = list(answers or [])
        self.by_item = {k: list(v) for k, v in (by_item or {}).items()}
        self.requests: list = []
        self.threads: list[int] = []

    def infer(self, request):
        self.requests.append(request)
        self.threads.append(threading.get_ident())
        text = request.context_package[1]["content"]  # the inputs, on a retry too
        queue = self.answers
        for item, replies in self.by_item.items():
            if item in text:
                queue = replies
        reply = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(reply, ModelGatewayResult):
            return reply
        return ModelGatewayResult(status="ok", response=reply)


class FakeSupervisor:
    def __init__(self, record=None, error: Exception | None = None) -> None:
        self.record = record if record is not None else {
            "status": hb.COMPLETE, "result": "built it"}
        self.error = error
        self.calls: list[tuple] = []
        self.threads: list[int] = []

    def delegate(self, bundle, **kwargs):
        self.calls.append((bundle, kwargs))
        self.threads.append(threading.get_ident())
        if self.error is not None:
            raise self.error
        return {"work_run_id": "w1", "result": dict(self.record)}


def _run(tmp_path):
    root = str(tmp_path)
    envelope = c.Envelope(run_id="r1", objective_id="o1", cwd=root,
                          read_roots=(root,), write_roots=(root,),
                          allowed_capabilities=(c.ANY_CAPABILITY,))
    return SimpleNamespace(envelope=envelope)


def _port(spec, *, gateway=None, supervisor=None, store=None, gate=OPEN) -> R.StagePort:
    cache = R.ResultCache(store or Store(":memory:"), "ws")
    return R.StagePort(spec, None, cache, _model, gate, gateway=gateway,
                       supervisor=supervisor)


def _args(spec, item="a", stage_ids=None):
    return {"spec_id": spec.id, "stage_ids": stage_ids or [s.id for s in spec.stages],
            "inputs": [item], "from_keys": False}


# --- routing ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_code_stage_is_one_bounded_hermes_delegation_and_never_an_inference(tmp_path):
    spec = _spec(("build", "code", "standard"))
    gateway, supervisor = FakeGateway(['{"ok": true}']), FakeSupervisor()
    port = _port(spec, gateway=gateway, supervisor=supervisor)
    out = await port(R.CAPABILITY, _args(spec, "fix the parser"), _run(tmp_path))
    assert gateway.requests == [], "a code stage must never go through infer"
    assert len(supervisor.calls) == 1
    bundle, kwargs = supervisor.calls[0]
    assert isinstance(bundle, hb.TaskBundle)
    assert bundle.allowed_paths == (str(tmp_path),), "the bundle is held to the envelope"
    assert any("fix the parser" in fact for fact in bundle.known_facts)
    assert kwargs["scope"].run_id == "r1"
    assert kwargs["wait"] is True and kwargs["model"] == "model-standard"
    assert kwargs["friday_run_id"] == "o1" and kwargs["workspace"] == str(tmp_path)
    assert out["stages"] == 1 and out["hits"] == 0


@pytest.mark.asyncio
async def test_a_code_stage_that_hermes_reports_failed_fails_the_task(tmp_path):
    spec = _spec(("build", "code", "standard"))
    supervisor = FakeSupervisor({"status": hb.FAILED, "result": "tests red"})
    port = _port(spec, gateway=FakeGateway(['{"ok": true}']), supervisor=supervisor)
    with pytest.raises(R.StageFailed, match="tests red"):
        await port(R.CAPABILITY, _args(spec), _run(tmp_path))


@pytest.mark.asyncio
async def test_a_tool_less_stage_is_one_inference_and_never_a_delegation(tmp_path):
    spec = _spec(("look", "lookup", "economy"))
    gateway, supervisor = FakeGateway(['{"ok": true}']), FakeSupervisor()
    port = _port(spec, gateway=gateway, supervisor=supervisor)
    await port(R.CAPABILITY, _args(spec, "item-1"), _run(tmp_path))
    assert supervisor.calls == []
    assert len(gateway.requests) == 1
    request = gateway.requests[0]
    assert request.objective_id == "o1"
    assert "item-1" in request.context_package[-1]["content"]
    assert json.dumps(SCHEMA) in request.context_package[0]["content"]


@pytest.mark.asyncio
async def test_hermes_and_the_gateway_are_called_off_the_event_loop(tmp_path):
    spec = _spec(("look", "lookup", "economy"), ("build", "code", "standard"))
    gateway, supervisor = FakeGateway(['{"ok": true}']), FakeSupervisor()
    port = _port(spec, gateway=gateway, supervisor=supervisor)
    await port(R.CAPABILITY, _args(spec), _run(tmp_path))
    here = threading.get_ident()
    assert gateway.threads and supervisor.threads
    assert here not in gateway.threads + supervisor.threads


def test_a_port_that_cannot_reach_a_stage_s_provider_is_refused_when_built():
    spec = _spec(("build", "code", "standard"))
    with pytest.raises(ValueError, match="supervisor"):
        _port(spec, gateway=FakeGateway(['{"ok": true}']))
    with pytest.raises(ValueError, match="gateway"):
        _port(_spec(("look", "lookup", "economy")), supervisor=FakeSupervisor())


# --- schema: one retry, then a classified failure --------------------------


@pytest.mark.asyncio
async def test_a_schema_violating_answer_is_retried_once_and_the_second_answer_is_used(tmp_path):
    spec = _spec(("look", "lookup", "economy"))
    gateway = FakeGateway(['{"ok": "yes"}', '{"ok": true}'])
    port = _port(spec, gateway=gateway)
    out = await port(R.CAPABILITY, _args(spec), _run(tmp_path))
    assert len(gateway.requests) == 2
    retry = gateway.requests[1].context_package[-1]["content"]
    assert "ok" in retry and "boolean" in retry, "the retry says what was wrong"
    assert out["stages"] == 1


@pytest.mark.asyncio
async def test_the_retry_is_a_dispatch_of_its_own_and_a_spent_budget_stops_it(tmp_path):
    class SpentAfterOne(OpenGate):
        def __init__(self) -> None:
            self.admits, self.open = 0, 0

        def admit(self, run, stage, arguments) -> str:
            self.admits += 1
            if self.admits > 1:
                raise R.BudgetStop("the run's token budget is spent")
            self.open += 1
            return "lease"

        def release(self, lease: str) -> None:
            self.open -= 1

    spec = _spec(("look", "lookup", "economy"))
    gateway, gate = FakeGateway(['{"ok": "yes"}', '{"ok": true}']), SpentAfterOne()
    port = _port(spec, gateway=gateway, gate=gate)
    with pytest.raises(R.BudgetStop):
        await port(R.CAPABILITY, _args(spec), _run(tmp_path))
    assert len(gateway.requests) == 1, "the retry was not sent past the budget"
    assert gate.open == 0, "the first attempt's lease was released before the retry asked"


@pytest.mark.asyncio
async def test_a_second_violation_fails_the_task_with_a_classified_error(tmp_path):
    spec = _spec(("look", "lookup", "economy"))
    gateway = FakeGateway(["not json at all", '{"ok": 3}'])
    port = _port(spec, gateway=gateway)
    with pytest.raises(R.SchemaViolation) as caught:
        await port(R.CAPABILITY, _args(spec), _run(tmp_path))
    assert len(gateway.requests) == 2, "exactly one retry"
    assert caught.value.stage == "look"
    assert FailureClassifier.classify(caught.value) == O.FailureKind.INVALID_ARGUMENT


@pytest.mark.asyncio
async def test_a_task_whose_answer_violates_the_schema_fails_while_its_sibling_completes():
    spec = _spec(("look", "lookup", "economy"))
    store = Store(":memory:")
    gateway = FakeGateway(by_item={"bad": ["nope"], "good": ['{"ok": true}']},
                          answers=['{"ok": true}'])
    port = _port(spec, gateway=gateway, store=store)
    run = R.start_run(store, spec, ["bad", "good"], provenance=PERSON, attended=True)
    executor = ContinuousTaskExecutor(store, port, executor_id="sib",
                                      governor=lambda: SimpleNamespace(
                                          worker_capacity=lambda objective_id="": 4,
                                          active=lambda kind=None: 0,
                                          justify_parallel=lambda objective_id: None))
    await executor.start(run["run_id"])
    executor.stop()
    tasks = store.objective_tasks(run["run_id"])
    by_item = {t["arguments"]["inputs"][0]: t["status"] for t in tasks}
    assert by_item["good"] == O.TaskStatus.SUCCEEDED
    assert by_item["bad"] == O.TaskStatus.FAILED


# --- an unavailable provider ----------------------------------------------


@pytest.mark.asyncio
async def test_a_gateway_with_no_route_marks_that_stage_unavailable_and_skips_what_needs_it(tmp_path):
    spec = _spec(("look", "lookup", "economy"), ("check", "check", "economy"))
    down = ModelGatewayResult(status="failed", dispatch="BLOCKED",
                              warnings=["no route"])
    gateway = FakeGateway([down])
    port = _port(spec, gateway=gateway)
    out = await port(R.CAPABILITY, _args(spec), _run(tmp_path))
    assert len(gateway.requests) == 1, "the next stage had nothing to work on"
    assert out["unavailable"] == ["look", "check"]
    final = port._cache.get(out["key"])
    assert final["state"] == R.UNAVAILABLE and final["stage"] == "check"


@pytest.mark.asyncio
async def test_hermes_being_unavailable_marks_the_code_stage_unavailable(tmp_path):
    spec = _spec(("build", "code", "standard"))
    supervisor = FakeSupervisor(error=hb.HermesUnavailable("gateway is not running"))
    port = _port(spec, gateway=FakeGateway(['{"ok": true}']), supervisor=supervisor)
    out = await port(R.CAPABILITY, _args(spec), _run(tmp_path))
    assert out["unavailable"] == ["build"]
    assert "not running" in port._cache.get(out["key"])["reason"]


@pytest.mark.asyncio
async def test_a_run_with_an_unavailable_stage_still_completes():
    spec = _spec(("look", "lookup", "economy"))
    store = Store(":memory:")
    down = ModelGatewayResult(status="failed", dispatch="BLOCKED")
    port = _port(spec, gateway=FakeGateway([down]), store=store)
    run = R.start_run(store, spec, ["a", "b"], provenance=PERSON, attended=True)
    executor = ContinuousTaskExecutor(store, port, executor_id="down",
                                      governor=lambda: SimpleNamespace(
                                          worker_capacity=lambda objective_id="": 4,
                                          active=lambda kind=None: 0,
                                          justify_parallel=lambda objective_id: None))
    await executor.start(run["run_id"])
    executor.stop()
    assert store.objective_run(run["run_id"])["status"] == O.RunStatus.COMPLETED


@pytest.mark.asyncio
async def test_a_gateway_refusal_is_a_policy_failure_not_an_unavailable_stage(tmp_path):
    spec = _spec(("look", "lookup", "economy"))
    refused = ModelGatewayResult(status="refused", dispatch="ASK_USER")
    port = _port(spec, gateway=FakeGateway([refused]))
    with pytest.raises(PermissionError):
        await port(R.CAPABILITY, _args(spec), _run(tmp_path))


@pytest.mark.asyncio
async def test_an_unavailable_marker_from_an_earlier_run_is_retried_when_the_provider_is_back(tmp_path):
    spec = _spec(("look", "lookup", "economy"))
    store = Store(":memory:")
    down = ModelGatewayResult(status="failed", dispatch="BLOCKED", warnings=["no route"])
    first = await _port(spec, gateway=FakeGateway([down]), store=store)(
        R.CAPABILITY, _args(spec), _run(tmp_path))
    assert first["unavailable"] == ["look"]
    healthy = FakeGateway(['{"ok": true}'])
    port = _port(spec, gateway=healthy, store=store)
    second = await port(R.CAPABILITY, _args(spec), _run(tmp_path))
    assert len(healthy.requests) == 1, "the stage was served from the poisoned cache"
    assert "unavailable" not in second
    assert second["hits"] == 0
    assert port._cache.get(second["key"]) == {"ok": True}
    third = await port(R.CAPABILITY, _args(spec), _run(tmp_path))
    assert third["hits"] == 1 and len(healthy.requests) == 1, "a real result is still a hit"
