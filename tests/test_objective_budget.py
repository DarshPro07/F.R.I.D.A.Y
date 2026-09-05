"""
The objective budget is enforced from recorded spend (audit A-022).

Every check here drives the real ContinuousTaskExecutor against a real
Store; the "spend" is put on record the way production records it
(GatewayTelemetry rows, task attempts, strategy changes, created_at) and
the engine is expected to PAUSE the run with a blocker naming the
dimension - never to trim work silently and never to guess.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from friday import objective_budget as B
from friday import objectives as O
from friday.continuous import ContinuousTaskExecutor
from friday.model_gateway import GatewayTelemetry
from friday.objectives import compile_objective
from friday.store import Store


class Caps:
    def __init__(self):
        self.calls = []

    async def call(self, capability, arguments):
        self.calls.append(capability)
        return {"ok": True}


MANIFEST = [{"id": "system_get_info", "description": "cpu"},
            {"id": "hermes_delegate", "description": "worker"}]


def _run(store, tasks):
    return compile_objective(store, request="budget probe", tasks=tasks,
                             manifest=MANIFEST, objective_summary="budget probe")["run_id"]


async def _drive(store, caps, run_id, seconds=3.0):
    engine = ContinuousTaskExecutor(store, caps.call, executor_id="budget-test")
    engine.stop()
    await engine.start(run_id)
    deadline = asyncio.get_event_loop().time() + seconds
    while asyncio.get_event_loop().time() < deadline:
        row = store.objective_run(run_id)
        if row["status"] in O.RUN_TERMINAL or row["status"] == O.RUN_PAUSED:
            return row
        await asyncio.sleep(0.05)
    return store.objective_run(run_id)


def test_measure_reads_only_durable_records(tmp_path):
    store = Store(tmp_path / "b.sqlite3")
    tel = GatewayTelemetry(tmp_path / "gw.sqlite3")
    run_id = _run(store, [{"capability": "system_get_info", "arguments": {}}])
    for _ in range(3):
        tel.record(objective_id=run_id, worker="friday", task_class="SIMPLE",
                   provider="p", model="m", status="ok", latency_ms=1,
                   input_tokens=1000, output_tokens=500)
    spend = B.measure(store, run_id, telemetry=tel)
    assert spend.tokens == 4500                      # actual usage rows, summed
    assert spend.sources["gateway_calls"] == 3
    assert spend.tool_calls == 0 and spend.workers == 0 and spend.replans == 0
    store.close()


@pytest.mark.asyncio
async def test_a_run_over_its_token_ceiling_is_paused_before_the_next_call(tmp_path, monkeypatch):
    store = Store(tmp_path / "b.sqlite3")
    tel = GatewayTelemetry(tmp_path / "gw.sqlite3")
    monkeypatch.setattr("friday.model_gateway.GatewayTelemetry", lambda *a, **k: tel)
    caps = Caps()
    run_id = _run(store, [{"capability": "system_get_info", "arguments": {}}])
    ceiling = int(store.objective_run(run_id)["cost_budget_tokens"])
    assert ceiling > 0
    tel.record(objective_id=run_id, worker="friday", task_class="SIMPLE",
               provider="p", model="m", status="ok", latency_ms=1,
               input_tokens=ceiling, output_tokens=1)        # already spent
    row = await _drive(store, caps, run_id)
    assert row["status"] == O.RUN_PAUSED, row
    assert row["blocker"].startswith("budget exhausted (tokens)"), row["blocker"]
    assert caps.calls == [], "no capability may run once the ceiling is spent"
    events = [e["event"] for e in store.objective_events(run_id)]
    assert B.EVENT_BUDGET_EXHAUSTED in events
    task = store.objective_tasks(run_id)[0]
    assert task["status"] == O.TaskStatus.READY and int(task["attempts"] or 0) == 0, \
        "the parked task is not charged an attempt it never made"
    store.close()


@pytest.mark.asyncio
async def test_a_run_past_its_wall_clock_budget_is_paused(tmp_path):
    store = Store(tmp_path / "b.sqlite3")
    caps = Caps()
    run_id = _run(store, [{"capability": "system_get_info", "arguments": {}}])
    stale = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    with store._tx() as conn:
        conn.execute("UPDATE objective_runs SET created_at=? WHERE run_id=?", (stale, run_id))
    assert int(store.objective_run(run_id)["time_budget_s"]) > 0
    row = await _drive(store, caps, run_id)
    assert row["status"] == O.RUN_PAUSED
    assert "wall_time" in row["blocker"]
    assert caps.calls == []
    store.close()


@pytest.mark.asyncio
async def test_worker_delegations_are_capped_per_class(tmp_path):
    store = Store(tmp_path / "b.sqlite3")
    caps = Caps()
    tasks = [{"capability": "hermes_delegate", "arguments": {"n": i}} for i in range(3)]
    run_id = _run(store, tasks)
    klass = store.objective_run(run_id)["task_class"]
    limit = B.CLASS_LIMITS[klass]["max_workers"]
    # Put `limit` delegations on record as already attempted and finished
    # WITH evidence (the completion gate would otherwise mark the run
    # PARTIAL for an evidence gap before the budget is ever consulted).
    for t in store.objective_tasks(run_id)[:limit]:
        store.update_objective_task(t["task_id"], status=O.TaskStatus.SUCCEEDED,
                                    attempts=1, evidence="worker finished: verified by test",
                                    result={"ok": True})
        store.append_objective_evidence(run_id, task_id=t["task_id"], expected="worker done",
                                        actual="worker done", method="test", passed=True, ref="")
    if limit < 3:
        row = await _drive(store, caps, run_id)
        assert row["status"] == O.RUN_PAUSED
        assert "workers" in row["blocker"]
        assert caps.calls == []
    store.close()


@pytest.mark.asyncio
async def test_within_budget_runs_are_untouched(tmp_path, monkeypatch):
    store = Store(tmp_path / "b.sqlite3")
    tel = GatewayTelemetry(tmp_path / "gw.sqlite3")
    monkeypatch.setattr("friday.model_gateway.GatewayTelemetry", lambda *a, **k: tel)
    caps = Caps()
    run_id = _run(store, [{"capability": "system_get_info", "arguments": {}}])
    tel.record(objective_id=run_id, worker="friday", task_class="SIMPLE",
               provider="p", model="m", status="ok", latency_ms=1,
               input_tokens=10, output_tokens=10)
    row = await _drive(store, caps, run_id)
    assert row["status"] == O.RUN_COMPLETED, row
    assert caps.calls == ["system_get_info"]
    assert B.EVENT_BUDGET_EXHAUSTED not in [e["event"] for e in store.objective_events(run_id)]
    store.close()


def test_a_missing_ledger_is_recorded_not_a_free_pass_and_not_a_block(tmp_path):
    class Broken:
        def for_objective(self, run_id):
            raise OSError("ledger gone")
    store = Store(tmp_path / "b.sqlite3")
    run_id = _run(store, [{"capability": "system_get_info", "arguments": {}}])
    v = B.check(store, run_id, telemetry=Broken())
    assert v.allowed
    assert "gateway_error" in v.spend.sources
    store.close()


def test_every_class_lets_one_task_exhaust_its_own_strategy_budget():
    """The replans cap must not undercut the per-task loop-breaker.

    A stuck fingerprint concludes BLOCKED after MAX_STRATEGY_CHANGES changes
    (continuous._requeue_or_block); a class cap below that parks the run
    PAUSED two failures earlier and the task never reaches a terminal state.
    That is what the first CI run after A-022 found in
    test_failure_fingerprint. The cap governs replanning across tasks.
    """
    from friday.continuous import MAX_STRATEGY_CHANGES
    assert MAX_STRATEGY_CHANGES == B.MAX_STRATEGY_CHANGES
    for klass, limits in B.CLASS_LIMITS.items():
        assert limits["max_replans"] >= B.MAX_STRATEGY_CHANGES, \
            f"{klass}: max_replans {limits['max_replans']} < MAX_STRATEGY_CHANGES {B.MAX_STRATEGY_CHANGES}"


@pytest.mark.asyncio
async def test_a_stuck_single_task_concludes_blocked_before_the_replans_cap(tmp_path):
    """End to end through the engine: the same failure every time, a large
    attempt budget, and the run must END (FAILED, outcome blocked:) rather
    than park on the replans dimension."""
    from friday.continuous import ContinuousTaskExecutor as Engine

    async def always_stuck(_cap, _args):
        raise TimeoutError("stuck")

    store = Store(tmp_path / "b.sqlite3")
    run_id = _run(store, [{"capability": "system_get_info", "arguments": {}}])
    engine = Engine(store, always_stuck, executor_id="budget-stuck")
    engine.max_attempts = 50
    await engine.start(run_id)
    deadline = asyncio.get_event_loop().time() + 5.0
    while asyncio.get_event_loop().time() < deadline:
        row = store.objective_run(run_id)
        if row["status"] in O.RUN_TERMINAL or row["status"] == O.RUN_PAUSED:
            break
        await asyncio.sleep(0.05)
    engine.stop()
    row = store.objective_run(run_id)
    assert row["status"] == O.RUN_FAILED, (row["status"], row.get("blocker"))
    assert str(row["summary"]["outcome"]).startswith("blocked:")
    task = store.objective_tasks(run_id)[0]
    assert task["status"] == O.TaskStatus.BLOCKED
    assert task["detail"]["strategy_changes"] == B.MAX_STRATEGY_CHANGES + 1
    store.close()
