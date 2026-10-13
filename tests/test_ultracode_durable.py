"""UCW-002 gap 1: a definition runs as a DAG on the durable objective engine.

One durable task per step; the engine's task dependencies are the step's
depends_on, so independent steps start without a barrier, a dependent step
starts only when its handoff validates, a deterministic step goes to its
executor (never a model), and the role resolution (pin and reason) is
recorded on the run. Driven through the real ContinuousTaskExecutor."""
from __future__ import annotations

import asyncio
import copy

import pytest

from friday import objectives as O
from friday.continuous import ContinuousTaskExecutor
from friday.contracts import PERSON
from friday.store import Store
from friday.ultracode import definition as D
from friday.ultracode import durable as U
from friday.ultracode.cache import ResultCache
from tests.test_ultracode_definition import (  # noqa: F401  (_empty_profile_store: autouse)
    _AVAILABLE, _GOOD, _ROUTES, _empty_profile_store, build_capability)


class OpenGate:
    """Permissive: the budget gate is ULTRA-003's (tests/test_ultracode_budget.py)."""

    def __init__(self) -> None:
        self.admitted: list[str] = []

    def admit(self, run, stage, arguments) -> str:
        self.admitted.append(stage.id)
        return ""

    def release(self, lease: str) -> None:
        pass

    def settle(self, run, stage, arguments) -> None:
        pass


class StubGovernor:
    def worker_capacity(self, objective_id: str = "") -> int:
        return 4

    def active(self, kind=None) -> int:
        return 0

    def justify_parallel(self, objective_id: str) -> None:
        pass


def _compiled():
    return D.compile_definition(D.parse_definition(build_capability()))


def _resolutions(directives=None, available=_AVAILABLE):
    compiled = _compiled()
    return {r.step: r for r in D.resolve_roles(compiled.definition, directives or {},
                                               routes=_ROUTES, available=available)}


class Runtime:
    """A model agent and a deterministic executor that log every call. The two
    roots each wait until both have started: only possible without a barrier."""

    def __init__(self, outputs: dict) -> None:
        self.outputs = outputs
        self.log: list = []
        self.started: set[str] = set()

    async def agent(self, step, inputs, model):
        self.log.append(("agent", step.id, model, inputs))
        self.started.add(step.id)
        if step.id in ("scan", "review"):
            async def both():
                while not {"scan", "review"} <= self.started:
                    await asyncio.sleep(0.005)
            await asyncio.wait_for(both(), timeout=5)
        return copy.deepcopy(self.outputs[step.id])

    async def run_tests(self, step, inputs):
        self.log.append(("executor", step.id, None, inputs))
        return copy.deepcopy(self.outputs[step.id])

    def called(self, step_id: str) -> list:
        return [e for e in self.log if e[1] == step_id]


async def _drive(store, compiled, runtime, resolutions, gate=None):
    run = U.start_definition_run(store, compiled, resolutions=resolutions,
                                 provenance=PERSON, attended=True)
    port = U.DefinitionPort(compiled, agent=runtime.agent,
                            executors={"pytest.run": runtime.run_tests},
                            resolutions=resolutions, cache=ResultCache(store, "ws"),
                            gate=gate or OpenGate())
    executor = ContinuousTaskExecutor(store, port, executor_id="dag",
                                      governor=lambda: StubGovernor())
    driving = asyncio.ensure_future(executor.start(run["run_id"]))
    deadline = asyncio.get_event_loop().time() + 10
    while store.objective_run(run["run_id"])["status"] not in O.RUN_TERMINAL:
        assert asyncio.get_event_loop().time() < deadline, "timed out waiting for the run"
        await asyncio.sleep(0.01)
    await asyncio.wait_for(driving, 5)
    executor.stop()
    tasks = {t["arguments"]["stage_ids"][0]: t for t in store.objective_tasks(run["run_id"])}
    return run["run_id"], tasks


def test_the_plan_is_one_task_per_step_and_dependencies_are_depends_on():
    tasks = U.plan_definition_tasks(_compiled())
    by_step = {t["arguments"]["stage_ids"][0]: t for t in tasks}
    task_of = {step: f"t{i + 1}" for i, step in enumerate(_compiled().order)}
    assert set(by_step) == {"scan", "review", "build", "test", "verdict"}
    assert by_step["scan"]["dependencies"] == [] and by_step["review"]["dependencies"] == []
    assert by_step["build"]["dependencies"] == [task_of["scan"], task_of["review"]]
    assert by_step["verdict"]["dependencies"] == [task_of["build"], task_of["test"]]
    assert by_step["build"]["arguments"]["inputs"] == [
        f"{{{{tasks.{task_of['scan']}.key}}}}", f"{{{{tasks.{task_of['review']}.key}}}}"]
    assert len({tuple(t["surface"]) for t in tasks}) == 5, "each step has its own surface"
    assert {t["capability"] for t in tasks} == {U.CAPABILITY}


@pytest.mark.asyncio
async def test_build_capability_runs_durably_as_a_dag_with_a_recorded_pin():
    store = Store(":memory:")
    resolutions = _resolutions({"builder": {"pin": "m-mid-b"}})
    runtime, gate = Runtime(_GOOD), OpenGate()
    run_id, tasks = await _drive(store, _compiled(), runtime, resolutions, gate)

    assert store.objective_run(run_id)["status"] == O.RunStatus.COMPLETED
    order = [e[1] for e in runtime.log]
    # independent roots both started before either finished (no barrier)
    assert set(order[:2]) == {"scan", "review"}
    # the dependent got exactly its typed handoff, from the pinned model
    (_, _, model, handed), = runtime.called("build")
    assert model == "m-mid-b"
    assert handed == {"scan": {"findings": ["a"]}, "review": {"notes": "n"}}
    # the deterministic step went to its executor, never to a model; every
    # dispatch, the executor's too, is held to the run's gate
    assert runtime.called("test") == [("executor", "test", None, {"build": {"patch": "p"}})]
    assert sorted(gate.admitted) == sorted(["scan", "review", "build", "test", "verdict"])
    assert runtime.called("verdict")[0][2] == "m-big"
    # the runtime choice and its reason are recorded on the run
    events = [e for e in store.objective_events(run_id) if e["event"] == U.EVENT_RESOLUTIONS]
    recorded = {r["step"]: r for r in events[0]["detail"]["resolutions"]}
    assert recorded["build"]["status"] == D.STATUS_PINNED
    assert "pins role builder to m-mid-b" in recorded["build"]["reason"]
    assert recorded["test"]["status"] == D.STATUS_DETERMINISTIC


@pytest.mark.asyncio
async def test_a_dependent_does_not_start_when_its_durable_handoff_does_not_validate():
    # scan's schema lets it hand on nothing, so scan is DONE; build takes
    # `findings`, which scan did not produce, so build is refused undispatched.
    raw = build_capability()
    raw["steps"][0]["outputs"]["required"] = []
    compiled = D.compile_definition(D.parse_definition(raw))
    store = Store(":memory:")
    runtime = Runtime({**_GOOD, "scan": {}})
    resolutions = {r.step: r for r in D.resolve_roles(compiled.definition, {}, routes=_ROUTES,
                                                      available=_AVAILABLE)}
    run_id, tasks = await _drive(store, compiled, runtime, resolutions)
    assert tasks["scan"]["status"] == O.TaskStatus.SUCCEEDED
    assert runtime.called("build") == [] and runtime.called("test") == []
    assert tasks["build"]["status"] == O.TaskStatus.FAILED
    assert "steps[2].inputs[0].fields[0]" in tasks["build"]["evidence"]
    assert store.objective_run(run_id)["status"] != O.RunStatus.COMPLETED


@pytest.mark.asyncio
async def test_a_step_result_that_violates_its_schema_fails_and_its_dependents_never_start():
    store = Store(":memory:")
    runtime = Runtime({**_GOOD, "scan": {"findings": "not-a-list"}})
    run_id, tasks = await _drive(store, _compiled(), runtime, _resolutions())
    assert tasks["scan"]["status"] == O.TaskStatus.FAILED
    assert "steps[0].outputs" in tasks["scan"]["evidence"]
    assert runtime.called("build") == [] and runtime.called("test") == []
    assert store.objective_run(run_id)["status"] != O.RunStatus.COMPLETED


@pytest.mark.asyncio
async def test_a_pin_to_an_unavailable_route_is_unavailable_on_the_durable_run_too():
    store = Store(":memory:")
    resolutions = _resolutions({"builder": {"pin": "m-gone"}})
    runtime = Runtime(_GOOD)
    run_id, tasks = await _drive(store, _compiled(), runtime, resolutions)
    assert runtime.called("build") == [], "no substitute model was dispatched"
    assert tasks["build"]["status"] == O.TaskStatus.FAILED
    assert D.STATUS_UNAVAILABLE in tasks["build"]["evidence"]
    assert "m-gone" in tasks["build"]["evidence"]


def test_a_missing_resolution_or_executor_is_refused_before_any_run_exists():
    store = Store(":memory:")
    resolutions = _resolutions()
    del resolutions["verdict"]
    with pytest.raises(D.DefinitionError) as caught:
        U.start_definition_run(store, _compiled(), resolutions=resolutions,
                               provenance=PERSON, attended=True)
    assert caught.value.field == "resolutions.verdict"
    assert store.objective_runs(limit=10) == []
    with pytest.raises(D.DefinitionError) as caught:
        U.DefinitionPort(_compiled(), agent=Runtime(_GOOD).agent, executors={},
                         resolutions=_resolutions(), cache=ResultCache(store, "ws"),
                         gate=OpenGate())
    assert caught.value.field == "steps[3].executor"
