"""ULTRA-002: an ultracode run is durable objective tasks, not a loop of its own.

The real Store (a file, so a "restart" is a new Store over the same database)
and the real ContinuousTaskExecutor; only the agent is a fake that counts its
calls per stage. Nothing sleeps for time: a parked agent waits on an event the
test opens, so what runs at once is observed, not hoped.
"""
from __future__ import annotations

import ast
import asyncio
from collections import Counter
from pathlib import Path

import pytest

from friday import objectives as O
from friday.continuous import ContinuousTaskExecutor
from friday.contracts import PERSON
from friday.store import Store
from friday.ultracode import runner as R
from friday.ultracode import spec as S

SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}


def _spec(*stages: tuple[str, bool]) -> S.Spec:
    return S.parse_spec({
        "id": "demo", "budget_tokens": 100000,
        "stages": [{"id": name, "template": "lookup", "barrier": barrier,
                    "schema": SCHEMA, "tier": "economy", "cap": 1000}
                   for name, barrier in stages]})


class Killed(BaseException):
    """The process dies here: not an Exception, so nothing handles it."""


def _item(inputs: list) -> str:
    first = inputs[0]
    return first if isinstance(first, str) else str(first.get("item", "?"))


class FakeAgent:
    """Counts calls per (stage, item); a gate parks one call."""

    def __init__(self) -> None:
        self.calls: Counter = Counter()
        self.started: list[tuple[str, str]] = []
        self.kill_at: tuple[str, str] | None = None
        self.drop_at: tuple[str, str] | None = None
        self.gates: dict[tuple[str, str], asyncio.Event] = {}

    def gate(self, stage: str, item: str) -> asyncio.Event:
        return self.gates.setdefault((stage, item), asyncio.Event())

    async def __call__(self, stage: S.Stage, inputs: list, model: str) -> dict:
        item = _item(inputs)
        self.calls[(stage.id, item)] += 1
        self.started.append((stage.id, item))
        if self.kill_at == (stage.id, item):
            self.kill_at = None
            raise Killed(stage.id)
        if self.drop_at == (stage.id, item):
            self.drop_at = None
            raise ConnectionResetError(f"link dropped at {stage.id}")
        if (stage.id, item) in self.gates:
            await self.gates[(stage.id, item)].wait()
        return {"ok": True, "stage": stage.id, "item": item}


def _model(stage: S.Stage) -> str:
    return f"model-{stage.tier}"


class StubGovernor:
    def __init__(self, capacity: int = 4) -> None:
        self.capacity = capacity

    def worker_capacity(self, objective_id: str = "") -> int:
        return self.capacity

    def active(self, kind=None) -> int:
        return 0

    def justify_parallel(self, objective_id: str) -> None:
        pass


def _executor(store: Store, port, name: str) -> ContinuousTaskExecutor:
    return ContinuousTaskExecutor(store, port, executor_id=name,
                                  governor=lambda: StubGovernor())


async def _until(condition, timeout: float = 3.0) -> None:
    deadline = asyncio.get_event_loop().time() + timeout
    while not condition():
        assert asyncio.get_event_loop().time() < deadline, "timed out waiting"
        await asyncio.sleep(0.01)


# --- the plan the runner emits ---------------------------------------------


def test_a_barrier_starts_a_new_task_that_waits_for_every_item():
    spec = _spec(("scan", False), ("extract", False), ("verify", True), ("report", False))
    tasks = R.plan_tasks(spec, ["a", "b"])
    assert [t["capability"] for t in tasks] == [R.CAPABILITY] * 3
    assert [t["arguments"]["stage_ids"] for t in tasks] == [
        ["scan", "extract"], ["scan", "extract"], ["verify", "report"]]
    assert not tasks[0].get("dependencies") and not tasks[1].get("dependencies")
    assert tasks[2]["dependencies"] == ["t1", "t2"]
    assert tasks[2]["arguments"]["inputs"] == ["{{tasks.t1.key}}", "{{tasks.t2.key}}"]
    surfaces = [tuple(t["surface"]) for t in tasks]
    assert len(set(surfaces)) == 3, "each task declares its own surface so siblings run together"


def test_a_spec_without_a_barrier_is_one_task_per_item():
    tasks = R.plan_tasks(_spec(("scan", False), ("extract", False)), ["a", "b", "c"])
    assert len(tasks) == 3 and all(not t.get("dependencies") for t in tasks)


def test_the_runner_owns_no_scheduling_loop():
    tree = ast.parse(Path(R.__file__).read_text(encoding="utf-8"))
    banned_calls = {"create_task", "ensure_future", "gather", "wait", "sleep",
                    "run_until_complete", "Thread"}
    loops = [n for n in ast.walk(tree) if isinstance(n, ast.While)]
    calls = [n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", "")
             for n in ast.walk(tree) if isinstance(n, ast.Call)]
    assert not loops, "a while loop is a scheduler"
    assert not banned_calls & set(calls), banned_calls & set(calls)


# --- durable stages --------------------------------------------------------


@pytest.mark.asyncio
async def test_killed_after_stage_two_and_resumed_no_finished_stage_runs_again(tmp_path):
    db = str(tmp_path / "ultra.sqlite3")
    spec = _spec(("s1", False), ("s2", False), ("s3", False))
    agent = FakeAgent()
    agent.kill_at = ("s3", "a")
    args = R.plan_tasks(spec, ["a"])[0]["arguments"]

    first = R.StagePort(spec, agent, R.ResultCache(Store(db), "ws"), _model)
    with pytest.raises(Killed):
        await first(R.CAPABILITY, args)
    assert agent.calls[("s1", "a")] == 1 and agent.calls[("s2", "a")] == 1

    # The process died: a new Store and port over the same database.
    second = R.StagePort(spec, agent, R.ResultCache(Store(db), "ws"), _model)
    result = await second(R.CAPABILITY, args)
    assert agent.calls[("s1", "a")] == 1, "stage 1 was called again after the restart"
    assert agent.calls[("s2", "a")] == 1, "stage 2 was called again after the restart"
    assert agent.calls[("s3", "a")] == 2, "only the stage that never finished is retried"
    assert result["hits"] == 2 and result["stages"] == 3


@pytest.mark.asyncio
async def test_a_changed_model_is_not_answered_from_the_cache(tmp_path):
    db = str(tmp_path / "model.sqlite3")
    spec = _spec(("s1", False))
    agent = FakeAgent()
    args = R.plan_tasks(spec, ["a"])[0]["arguments"]
    await R.StagePort(spec, agent, R.ResultCache(Store(db), "ws"), _model)(R.CAPABILITY, args)
    await R.StagePort(spec, agent, R.ResultCache(Store(db), "ws"),
                      lambda stage: "another-model")(R.CAPABILITY, args)
    assert agent.calls[("s1", "a")] == 2


@pytest.mark.asyncio
async def test_the_engine_retry_of_a_dropped_stage_does_not_call_the_finished_stages_again():
    store = Store(":memory:")
    spec = _spec(("s1", False), ("s2", False), ("s3", False))
    agent = FakeAgent()
    agent.drop_at = ("s3", "a")
    run = R.start_run(store, spec, ["a"], provenance=PERSON, attended=True)
    port = R.StagePort(spec, agent, R.ResultCache(store, "ws"), _model)
    executor = _executor(store, port, "retry")
    await executor.start(run["run_id"])
    executor.stop()
    assert [agent.calls[(s, "a")] for s in ("s1", "s2", "s3")] == [1, 1, 2]
    assert store.objective_run(run["run_id"])["status"] == O.RunStatus.COMPLETED


@pytest.mark.asyncio
async def test_a_stopped_run_resumes_through_the_engine_at_the_barrier(tmp_path):
    db = str(tmp_path / "engine.sqlite3")
    spec = _spec(("s1", False), ("s2", False), ("s3", True))
    agent = FakeAgent()

    before = Store(db)
    run = R.start_run(before, spec, ["a"], provenance=PERSON, attended=True)
    port = R.StagePort(spec, agent, R.ResultCache(before, "ws"), _model)
    first = _executor(before, port, "process-1")
    first.portion_budget = 1                      # stop after stage 2's task
    await first.start(run["run_id"])
    first.stop()
    assert agent.calls == Counter({("s1", "a"): 1, ("s2", "a"): 1})
    assert before.objective_run(run["run_id"])["status"] not in O.RUN_TERMINAL
    del before

    after = Store(db)
    port = R.StagePort(spec, agent, R.ResultCache(after, "ws"), _model)
    second = _executor(after, port, "process-2")
    await second.start(run["run_id"])
    second.stop()
    assert agent.calls[("s1", "a")] == 1 and agent.calls[("s2", "a")] == 1
    assert sum(n for (stage, _), n in agent.calls.items() if stage == "s3") == 1
    assert after.objective_run(run["run_id"])["status"] == O.RunStatus.COMPLETED


# --- no barrier, no waiting ------------------------------------------------


@pytest.mark.asyncio
async def test_a_stage_without_a_barrier_starts_before_a_slower_sibling_finishes():
    store = Store(":memory:")
    spec = _spec(("scan", False), ("extract", False), ("verify", True))
    agent = FakeAgent()
    agent.gate("scan", "slow")          # the slow sibling parks in its first stage
    run = R.start_run(store, spec, ["slow", "fast"], provenance=PERSON, attended=True)
    port = R.StagePort(spec, agent, R.ResultCache(store, "ws"), _model)
    executor = _executor(store, port, "pipeline")
    driving = asyncio.ensure_future(executor.start(run["run_id"]))

    await _until(lambda: ("extract", "fast") in agent.started)
    assert ("scan", "slow") in agent.started
    assert ("extract", "slow") not in agent.started, "the slow sibling is still in stage 1"
    assert ("verify", "fast") not in agent.started, "a barrier waits for every item"

    agent.gate("scan", "slow").set()
    await _until(lambda: store.objective_run(run["run_id"])["status"] in O.RUN_TERMINAL)
    assert await asyncio.wait_for(driving, 3) is True
    executor.stop()
    assert store.objective_run(run["run_id"])["status"] == O.RunStatus.COMPLETED
    verify_at = next(i for i, s in enumerate(agent.started) if s[0] == "verify")
    assert {("extract", "slow"), ("extract", "fast")} <= set(agent.started[:verify_at])
