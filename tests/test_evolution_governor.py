"""
EVO-006: evolution work runs below the person's work and under the budgets.

Real Store, real Governor (a dialled sampler), real objective budget and
trajectory detector. The owner's ruling 2026-10-08c (docs/plans/build-harness/
00-owner-directive-2026-10-08c.md) lets evolution start on a trigger, in an
overnight mode, or automatically when idle; these tests hold the seam all
three go through.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from friday import evolution_governor as E
from friday import governor as G
from friday import objectives as O
from friday.contracts import PERSON
from friday.model_gateway import GatewayTelemetry
from friday.objectives import compile_objective
from friday.store import Store
from tests.test_governor import make

MANIFEST = [{"id": "system_get_info", "description": "cpu"}]
NOW = datetime(2026, 10, 10, 3, 0, tzinfo=timezone.utc)      # inside the overnight window
DAY = datetime(2026, 10, 10, 14, 0, tzinfo=timezone.utc)


def _gov():
    gov, _ = make()
    gov._context = lambda: G.Context(user_idle_s=3600.0, on_battery=False)
    return gov


def _live(store, run_id, *, status="RUNNING", alive=True):
    expiry = datetime(2100, 1, 1, tzinfo=timezone.utc) if alive else datetime(2000, 1, 1, tzinfo=timezone.utc)
    store.open_objective_run(run_id, request="r", objective_summary="s", status=status,
                             lease_executor_id="x", lease_expiry=expiry.isoformat())


def _experiment(store, tasks=1):
    return compile_objective(
        store, request="evolve", objective_summary="evolve", manifest=MANIFEST,
        tasks=[{"capability": "system_get_info", "arguments": {}} for _ in range(tasks)],
        provenance=PERSON, attended=True)["run_id"]


def _admit(store, gov, run_id, **kw):
    kw.setdefault("klass", E.EXPLORATION)
    kw.setdefault("mode", "idle")
    kw.setdefault("now", NOW)
    return E.admit(store, gov, run_id, **kw)


def test_no_evolution_worker_starts_while_a_user_task_is_active(tmp_path):
    store, gov = Store(tmp_path / "e.sqlite3"), _gov()
    _live(store, "user-1")
    run_id = _experiment(store)
    for klass in E.EVOLUTION_CLASSES:
        got = _admit(store, gov, run_id, klass=klass)
        assert not got.admitted and got.blocked_by == E.ACTIVE_TASK
    assert gov.active() == 0                                   # no lease was ever taken
    assert store.objective_events_named(run_id, (E.EVENT_STARTED,)) == []
    store.close()


@pytest.mark.parametrize("status,alive", [("PAUSED", True), ("RUNNING", False)])
def test_an_unfinished_objective_also_outranks_evolution(tmp_path, status, alive):
    store, gov = Store(tmp_path / "e.sqlite3"), _gov()
    _live(store, "user-1", status=status, alive=alive)
    got = _admit(store, gov, _experiment(store), klass=E.RELIABILITY_REPAIR)
    assert not got.admitted and got.blocked_by == E.UNFINISHED_OBJECTIVE
    store.close()


def test_a_run_waiting_on_the_person_does_not_hold_evolution_back(tmp_path):
    store, gov = Store(tmp_path / "e.sqlite3"), _gov()
    _live(store, "user-1", status="WAITING_QUESTION")
    assert _admit(store, gov, _experiment(store)).admitted
    store.close()


def test_an_idle_machine_admits_each_class_and_marks_the_run_as_evolution(tmp_path):
    store, gov = Store(tmp_path / "e.sqlite3"), _gov()
    for klass in E.EVOLUTION_CLASSES:
        run_id = _experiment(store)
        got = _admit(store, gov, run_id, klass=klass)
        assert got.admitted and got.lease
        started = store.objective_events_named(run_id, (E.EVENT_STARTED,))
        assert started and started[0]["detail"]["class"] == klass
        gov.release(got.lease)
    store.close()


def test_an_evolution_run_is_not_the_persons_work(tmp_path):
    store, gov = Store(tmp_path / "e.sqlite3"), _gov()
    run_id = _experiment(store)
    assert _admit(store, gov, run_id).admitted
    store.open_objective_run(run_id, request="r", objective_summary="s", status="RUNNING",
                             lease_executor_id="x", lease_expiry=datetime(2100, 1, 1, tzinfo=timezone.utc).isoformat())
    assert E.user_work(store, now=NOW) == {"active": [], "unfinished": []}
    store.close()


def test_a_lower_class_waits_for_a_pending_higher_one(tmp_path):
    store, gov = Store(tmp_path / "e.sqlite3"), _gov()
    run_id = _experiment(store)
    got = _admit(store, gov, run_id, klass=E.EXPLORATION, waiting={E.RELIABILITY_REPAIR: 1})
    assert not got.admitted and got.blocked_by == E.RELIABILITY_REPAIR
    assert _admit(store, gov, run_id, klass=E.EXPLORATION,
                  waiting={E.RELIABILITY_REPAIR: 0}).admitted
    store.close()


def test_the_three_modes_of_the_owner_ruling(tmp_path):
    store, gov = Store(tmp_path / "e.sqlite3"), _gov()
    run_id = _experiment(store)
    no_trigger = _admit(store, gov, run_id, mode="trigger")
    assert not no_trigger.admitted and "trigger" in no_trigger.reason
    day = _admit(store, gov, run_id, mode="overnight", now=DAY)
    assert not day.admitted and "overnight" in day.reason
    assert gov.active() == 0
    for kw in ({"mode": "trigger", "trigger": "capability gap: pdf_merge"},
               {"mode": "overnight"}, {"mode": "idle"}):
        got = _admit(store, gov, run_id, **kw)
        assert got.admitted, got.reason
        gov.release(got.lease)
    assert not _admit(store, gov, run_id, mode="whenever").admitted
    store.close()


def test_the_person_being_at_the_machine_defers_idle_evolution(tmp_path):
    store, gov = Store(tmp_path / "e.sqlite3"), _gov()
    gov._context = lambda: G.Context(user_idle_s=5.0, on_battery=False)
    got = _admit(store, gov, _experiment(store))
    assert not got.admitted and "person is active" in got.reason
    store.close()


def test_experiment_capacity_is_one_at_a_time(tmp_path):
    store, gov = Store(tmp_path / "e.sqlite3"), _gov()
    first = _experiment(store)
    held = _admit(store, gov, first)
    assert held.admitted
    second = _experiment(store)
    got = _admit(store, gov, second)
    assert not got.admitted and got.blocked_by == "capacity"
    gov.release(held.lease)
    assert _admit(store, gov, second).admitted
    store.close()


def _burn(store, tmp_path, run_id, factor=1):
    tel = GatewayTelemetry(tmp_path / "gw.sqlite3")
    ceiling = int(store.objective_run(run_id)["cost_budget_tokens"])
    tel.record(objective_id=run_id, worker="friday", task_class="SIMPLE", provider="p",
               model="m", status="ok", latency_ms=1, input_tokens=ceiling * factor,
               output_tokens=0)
    return tel


def test_an_experiment_that_exhausts_its_budget_stops_with_its_evidence_archived(tmp_path):
    store, gov = Store(tmp_path / "e.sqlite3"), _gov()
    run_id = _experiment(store)
    got = _admit(store, gov, run_id)
    assert E.check(store, gov, run_id, got.lease, telemetry=_burn(store, tmp_path, run_id, 0)).stopped is False                     # nothing spent: carries on
    stop = E.check(store, gov, run_id, got.lease, telemetry=_burn(store, tmp_path, run_id))
    assert stop.stopped and stop.reason == "budget_exhausted" and stop.dimension == "tokens"
    assert gov.active() == 0                                     # lease released
    row = store.objective_run(run_id)
    assert row["status"] == O.RUN_CANCELLED
    archived = store.objective_events_named(run_id, (E.EVENT_STOPPED,))
    assert len(archived) == 1
    evidence = archived[0]["detail"]
    assert evidence["reason"] == "budget_exhausted" and evidence["dimension"] == "tokens"
    assert evidence["spend"]["tokens"] >= int(row["cost_budget_tokens"])
    assert row["summary"]["evolution_stop"]["reason"] == "budget_exhausted"
    store.close()


def test_an_experiment_that_makes_no_progress_stops_with_its_evidence_archived(tmp_path):
    store, gov = Store(tmp_path / "e.sqlite3"), _gov()
    run_id = _experiment(store, tasks=3)
    got = _admit(store, gov, run_id)
    tasks = [t["task_id"] for t in store.objective_tasks(run_id)]
    for task in tasks * 2:
        store.append_objective_event(run_id, "task.failed", task_id=task,
                                     detail={"reason": "boom", "kind": "STRUCTURAL"})
    stop = E.check(store, gov, run_id, got.lease)
    assert stop.stopped and stop.reason == "no_progress"
    assert gov.active() == 0
    evidence = store.objective_events_named(run_id, (E.EVENT_STOPPED,))[0]["detail"]
    assert evidence["reason"] == "no_progress" and evidence["verdict"] == "NO_PROGRESS"
    assert evidence["signals"]["attempts_since_success"] >= 6
    store.close()


def test_a_healthy_experiment_is_never_stopped(tmp_path):
    store, gov = Store(tmp_path / "e.sqlite3"), _gov()
    run_id = _experiment(store)
    got = _admit(store, gov, run_id)
    assert E.check(store, gov, run_id, got.lease).stopped is False
    assert gov.active() == 1
    assert store.objective_events_named(run_id, (E.EVENT_STOPPED,)) == []
    store.close()


def test_the_person_arriving_preempts_a_running_experiment(tmp_path):
    store, gov = Store(tmp_path / "e.sqlite3"), _gov()
    run_id = _experiment(store)
    got = _admit(store, gov, run_id)
    _live(store, "user-1")
    stop = E.check(store, gov, run_id, got.lease)
    assert stop.stopped and stop.reason == "preempted"
    assert gov.active() == 0
    assert store.objective_events_named(run_id, (E.EVENT_STOPPED,))[0]["detail"]["blocked_by"] == E.ACTIVE_TASK
    store.close()
