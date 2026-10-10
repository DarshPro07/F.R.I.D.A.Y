"""ULTRA-003: every ultracode dispatch is held to the run budget and the governor.

The real Store, the real ContinuousTaskExecutor, the real objective_budget and
a real Governor reading a pinned healthy sample. Only the agent and the token
meter are fakes: the meter stands in for the gateway ledger, which is what
`objective_budget.measure` reads spend from.
"""
from __future__ import annotations

import asyncio
import time
from collections import Counter
from types import SimpleNamespace

import pytest

from friday import governor as G
from friday import objective_budget as B
from friday import objectives as O
from friday.continuous import ContinuousTaskExecutor
from friday.contracts import PERSON
from friday.store import Store
from friday.ultracode import runner as R
from friday.ultracode import spec as S

SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}


def _spec(stages: int = 3, *, budget: int = 100000, cap: int = 1000) -> S.Spec:
    return S.parse_spec({
        "id": "demo", "budget_tokens": budget,
        "stages": [{"id": f"s{n}", "template": "lookup", "barrier": False,
                    "schema": SCHEMA, "tier": "economy", "cap": cap}
                   for n in range(1, stages + 1)]})


def _model(stage: S.Stage) -> str:
    return f"model-{stage.tier}"


class Meter:
    """The gateway ledger: one row per metered call."""

    def __init__(self, spent: int = 0) -> None:
        self.rows: list[dict] = [{"input_tokens": spent, "output_tokens": 0}] if spent else []

    def for_objective(self, run_id: str) -> list[dict]:
        return list(self.rows)


class NoWorkLog:
    def for_friday_run(self, run_id: str) -> list[dict]:
        return []


class Agent:
    """Counts calls and meters `per_call` tokens for each; a gate parks calls."""

    def __init__(self, meter: Meter | None = None, per_call: int = 0) -> None:
        self.meter, self.per_call = meter, per_call
        self.calls: Counter = Counter()
        self.inflight = self.peak = 0
        self.release = asyncio.Event()
        self.park = False

    async def __call__(self, stage: S.Stage, inputs: list, model: str) -> dict:
        first = inputs[0]
        item = first if isinstance(first, str) else str(first.get("item", "?"))
        self.calls[(stage.id, item)] += 1
        self.inflight += 1
        self.peak = max(self.peak, self.inflight)
        try:
            if self.park:
                await self.release.wait()
        finally:
            self.inflight -= 1
        if self.meter is not None and self.per_call:
            self.meter.rows.append({"input_tokens": self.per_call, "output_tokens": 0})
        return {"ok": True, "stage": stage.id, "item": item}


class StubGovernor:
    def worker_capacity(self, objective_id: str = "") -> int:
        return 4

    def active(self, kind=None) -> int:
        return 0

    def justify_parallel(self, objective_id: str) -> None:
        pass


def _healthy_governor(max_workers: int) -> G.Governor:
    sample = G.Sample(at=time.time(), cpu_percent=5.0, ram_percent=30.0,
                      ram_available_gb=16.0, disk_free_gb=100.0,
                      browser_processes=0, friday_rss_mb=100.0)
    return G.Governor(thresholds=G.Thresholds(max_workers=max_workers),
                      sampler=lambda: sample, sample_ttl_s=60.0)


def _gate(store: Store, meter: Meter, governor: G.Governor | None = None) -> R.DispatchGate:
    chosen = governor or _healthy_governor(4)
    return R.DispatchGate(store, governor=lambda: chosen,
                          telemetry=meter, work_log=NoWorkLog())


def _events(store: Store, run_id: str, name: str) -> list[dict]:
    return store.objective_events_named(run_id, (name,))


# --- the budget recorded at admission --------------------------------------


def test_the_spec_budget_is_the_run_budget_recorded_at_admission():
    store = Store(":memory:")
    run = R.start_run(store, _spec(budget=777), ["a"], provenance=PERSON, attended=True)
    assert run["cost_budget_tokens"] == 777
    assert store.objective_run(run["run_id"])["cost_budget_tokens"] == 777


# --- a cap logs what it dropped --------------------------------------------


def test_a_cap_of_three_over_five_items_logs_two_dropped():
    store = Store(":memory:")
    items = ["i1", "i2", "i3", "i4", "i5"]
    run = R.start_run(store, _spec(2, cap=3), items, provenance=PERSON, attended=True)
    tasks = store.objective_tasks(run["run_id"])
    assert [t["arguments"]["inputs"] for t in tasks] == [["i1"], ["i2"], ["i3"]]
    logged = _events(store, run["run_id"], R.EVENT_ITEMS_DROPPED)
    assert len(logged) == 1
    assert logged[0]["detail"] == {"stage": "s1", "cap": 3, "offered": 5,
                                   "dropped": ["i4", "i5"]}


def test_an_item_list_inside_the_cap_logs_nothing():
    store = Store(":memory:")
    run = R.start_run(store, _spec(1, cap=3), ["a", "b", "c"], provenance=PERSON, attended=True)
    assert _events(store, run["run_id"], R.EVENT_ITEMS_DROPPED) == []


# --- at the budget the run stops, naming the stages it dropped ------------


def _shared_meter(monkeypatch, meter: Meter) -> None:
    """The engine's own budget check reads the same ledger the gate reads."""
    monkeypatch.setattr("friday.model_gateway.GatewayTelemetry", lambda *a, **k: meter)
    monkeypatch.setattr("friday.hermes_bridge.WorkRunLog", lambda *a, **k: NoWorkLog())


async def _run_engine(store: Store, port, run_id: str) -> None:
    executor = ContinuousTaskExecutor(store, port, executor_id="budget",
                                      governor=lambda: StubGovernor())
    await executor.start(run_id)
    executor.stop()


def _unfinished_pairs(store: Store, run_id: str, agent: Agent) -> set[tuple[str, str]]:
    """Every (task, stage) of a task that did not finish and whose stage never ran."""
    pairs = set()
    for task in store.objective_tasks(run_id):
        if task["status"] in O.TASK_TERMINAL:
            continue
        item = task["arguments"]["inputs"][0]
        pairs |= {(task["task_id"], sid) for sid in task["arguments"]["stage_ids"]
                  if (sid, item) not in agent.calls}
    return pairs


@pytest.mark.asyncio
async def test_a_tiny_budget_stops_the_run_with_the_dropped_stages_listed(monkeypatch):
    store = Store(":memory:")
    spec = _spec(3, budget=50)
    meter = Meter()
    _shared_meter(monkeypatch, meter)
    agent = Agent(meter, 40)
    run = R.start_run(store, spec, ["a"], provenance=PERSON, attended=True)
    port = R.StagePort(spec, agent, R.ResultCache(store, "ws"), _model,
                       gate=_gate(store, meter))
    await _run_engine(store, port, run["run_id"])

    assert sorted(stage for stage, _ in agent.calls) == ["s1", "s2"]
    row = store.objective_run(run["run_id"])
    assert row["status"] == O.RunStatus.PAUSED
    assert "budget exhausted (tokens)" in row["blocker"]
    listed = _events(store, run["run_id"], R.EVENT_STAGES_DROPPED)
    assert len(listed) == 1
    assert [d["stage"] for d in listed[0]["detail"]["dropped"]] == ["s3"]
    assert listed[0]["detail"]["dimension"] == "tokens"
    assert all(d["stage"] in row["blocker"] for d in listed[0]["detail"]["dropped"])
    # A stop is not a failure: the stopped task waits, it is not marked failed.
    assert O.TaskStatus.FAILED not in [t["status"] for t in store.objective_tasks(run["run_id"])]


@pytest.mark.asyncio
async def test_a_budget_crossed_at_a_task_boundary_lists_every_unfinished_stage(monkeypatch):
    """One meter for the gate and the engine. The first task's call crosses the
    budget on its last stage, so the next dispatch's gate never runs and the
    engine's boundary check would pause with nothing listed."""
    store = Store(":memory:")
    spec = _spec(1, budget=50)
    meter = Meter()
    _shared_meter(monkeypatch, meter)
    agent = Agent(meter, 60)
    run = R.start_run(store, spec, ["a", "b", "c"], provenance=PERSON, attended=True)
    port = R.StagePort(spec, agent, R.ResultCache(store, "ws"), _model,
                       gate=_gate(store, meter))
    await _run_engine(store, port, run["run_id"])

    row = store.objective_run(run["run_id"])
    assert row["status"] == O.RunStatus.PAUSED
    assert "budget exhausted (tokens)" in row["blocker"]
    listed = _events(store, run["run_id"], R.EVENT_STAGES_DROPPED)
    assert len(listed) == 1, "the unfinished stages are listed once"
    pairs = {(d["task"], d["stage"]) for d in listed[0]["detail"]["dropped"]}
    assert pairs and pairs == _unfinished_pairs(store, run["run_id"], agent)
    assert {task for task, _ in pairs} == {
        t["task_id"] for t in store.objective_tasks(run["run_id"])
        if t["status"] not in O.TASK_TERMINAL}, "no unfinished task is left unlisted"
    assert sum(agent.calls.values()) < 3, "the dropped stages did not run"
    assert O.TaskStatus.FAILED not in [t["status"] for t in store.objective_tasks(run["run_id"])]


@pytest.mark.asyncio
async def test_a_stop_lists_the_stages_of_every_task_that_did_not_finish():
    store = Store(":memory:")
    spec = _spec(2, budget=1)
    run = R.start_run(store, spec, ["a", "b"], provenance=PERSON, attended=True)
    gate = _gate(store, Meter(spent=1))
    port = R.StagePort(spec, Agent(), R.ResultCache(store, "ws"), _model, gate=gate)
    first = store.objective_tasks(run["run_id"])[0]
    with pytest.raises(R.BudgetStop):
        await port(R.CAPABILITY, first["arguments"],
                   run=SimpleNamespace(envelope=SimpleNamespace(objective_id=run["run_id"])))
    detail = _events(store, run["run_id"], R.EVENT_STAGES_DROPPED)[0]["detail"]
    assert sorted((d["task"], d["stage"]) for d in detail["dropped"]) == sorted(
        (t["task_id"], s) for t in store.objective_tasks(run["run_id"]) for s in ("s1", "s2"))


# --- governor admission: concurrent agents never exceed its cap -----------


@pytest.mark.asyncio
async def test_concurrent_agents_never_exceed_the_governors_worker_cap():
    store = Store(":memory:")
    spec = _spec(1)
    items = [f"i{n}" for n in range(5)]
    run = R.start_run(store, spec, items, provenance=PERSON, attended=True)
    governor = _healthy_governor(max_workers=2)
    agent = Agent()
    agent.park = True
    port = R.StagePort(spec, agent, R.ResultCache(store, "ws"), _model,
                       gate=_gate(store, Meter(), governor))
    as_run = SimpleNamespace(envelope=SimpleNamespace(objective_id=run["run_id"]))
    calls = [asyncio.ensure_future(port(R.CAPABILITY, t["arguments"], run=as_run))
             for t in store.objective_tasks(run["run_id"])]
    await asyncio.sleep(0.05)
    assert governor.active(G.WORKER) == 2, "only the governor's cap of agents holds a lease"
    agent.release.set()
    outcomes = await asyncio.gather(*calls, return_exceptions=True)

    refused = [o for o in outcomes if isinstance(o, G.Refused)]
    assert len(refused) == 3, "the others were refused, so the engine parks them"
    assert agent.peak == 2
    assert governor.active(G.WORKER) == 0, "every lease is released"


@pytest.mark.asyncio
async def test_a_gated_port_without_a_run_is_refused_not_run_ungated():
    store = Store(":memory:")
    spec = _spec(1)
    run = R.start_run(store, spec, ["a"], provenance=PERSON, attended=True)
    agent = Agent()
    port = R.StagePort(spec, agent, R.ResultCache(store, "ws"), _model,
                       gate=_gate(store, Meter()))
    with pytest.raises(ValueError, match="run"):
        await port(R.CAPABILITY, store.objective_tasks(run["run_id"])[0]["arguments"])
    assert not agent.calls



@pytest.mark.asyncio
async def test_tasks_that_reach_a_spent_budget_together_list_the_drops_once():
    store = Store(":memory:")
    spec = _spec(2, budget=1)
    run = R.start_run(store, spec, ["a", "b"], provenance=PERSON, attended=True)
    port = R.StagePort(spec, Agent(), R.ResultCache(store, "ws"), _model,
                       gate=_gate(store, Meter(spent=1)))
    as_run = SimpleNamespace(envelope=SimpleNamespace(objective_id=run["run_id"]))
    for task in store.objective_tasks(run["run_id"]):
        with pytest.raises(R.BudgetStop):
            await port(R.CAPABILITY, task["arguments"], run=as_run)
    assert len(_events(store, run["run_id"], R.EVENT_STAGES_DROPPED)) == 1
    assert len(_events(store, run["run_id"], B.EVENT_BUDGET_EXHAUSTED)) == 1


@pytest.mark.asyncio
async def test_a_call_that_crosses_the_budget_on_the_last_stage_drops_nothing():
    store = Store(":memory:")
    spec = _spec(1, budget=50)
    run = R.start_run(store, spec, ["a"], provenance=PERSON, attended=True)
    meter = Meter()
    port = R.StagePort(spec, Agent(meter, 60), R.ResultCache(store, "ws"), _model,
                       gate=_gate(store, meter))
    as_run = SimpleNamespace(envelope=SimpleNamespace(objective_id=run["run_id"]))
    task = store.objective_tasks(run["run_id"])[0]
    store.update_objective_task(task["task_id"], status=O.TaskStatus.RUNNING)  # as the engine
    result = await port(R.CAPABILITY, task["arguments"], run=as_run)
    assert result["stages"] == 1, "the finished result is returned, not raised"
    assert _events(store, run["run_id"], R.EVENT_STAGES_DROPPED) == []


def test_a_port_cannot_be_built_without_a_gate():
    spec = _spec(1)
    with pytest.raises(TypeError):
        R.StagePort(spec, Agent(), R.ResultCache(Store(":memory:"), "ws"), _model)
