"""
EVOLAB-002: self-evolution has its own money, RAM and time allowance.

Real Store, real Governor (a dialled sampler), real objective budget and a real
Evolution Archive on a tmp directory. The money, RAM and time measurements are
injected as numbers; nothing reads the machine's clock for the period.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest

from friday import evolution_allowance as W
from friday import evolution_archive as A
from friday import evolution_governor as E
from friday import governor as G
from friday import objective_budget as B
from friday.contracts import PERSON
from friday.objectives import compile_objective
from friday.store import Store
from tests.test_governor import make

MANIFEST = [{"id": "system_get_info", "description": "cpu"}]
NOW = datetime(2026, 10, 10, 3, 0, tzinfo=timezone.utc)
LIMITS = W.AllowanceLimits(usd=2.0, ram_gb_s=100.0, wall_s=60.0)


def _gov():
    gov, _ = make()
    gov._context = lambda: G.Context(user_idle_s=3600.0, on_battery=False)
    return gov


def _run(store, request="evolve"):
    return compile_objective(
        store, request=request, objective_summary=request, manifest=MANIFEST,
        tasks=[{"capability": "system_get_info", "arguments": {}}],
        provenance=PERSON, attended=True)["run_id"]


def _running_experiment(store, gov):
    run_id = _run(store)
    got = E.admit(store, gov, run_id, klass=E.EXPLORATION, mode=E.TRIGGER,
                  trigger="slow path", now=NOW)
    assert got.admitted, got.reason
    return run_id, got.lease


def _allowance(tmp_path, limits=LIMITS):
    return W.EvolutionAllowance(tmp_path / "allowance", limits), A.EvolutionArchive(tmp_path / "arc")


def _step(allowance, archive, run_id="exp-1", trigger="slow path", **over):
    kw = dict(objective_id=run_id, trigger=trigger, parent_version="router-v4",
              candidate_version="router-v5", hypothesis="Cache the manifest",
              scope=["router"], now=NOW)
    kw.update(over)
    return allowance.authorize_step(archive, **kw)


@pytest.mark.parametrize("spend,dimension", [({"money_usd": 2.0}, W.MONEY),
                                             ({"ram_gb_s": 100.0}, W.RAM),
                                             ({"wall_s": 60.0}, W.TIME)],
                         ids=["money", "ram", "time"])
def test_an_exhausted_dimension_refuses_the_next_step_and_archives_it(tmp_path, spend, dimension):
    store, gov = Store(tmp_path / "e.sqlite3"), _gov()
    allowance, archive = _allowance(tmp_path)
    run_id, _ = _running_experiment(store, gov)
    owner_run = _run(store, "the person's own task")      # after admission: it would outrank evolution
    owner_before = B.check(store, owner_run, now=NOW)
    assert _step(allowance, archive, run_id).allowed
    allowance.charge(gov, run_id, now=NOW, **spend)

    got = _step(allowance, archive, run_id)

    assert not got.allowed and got.reason == W.REASON_EXHAUSTED and got.dimension == dimension
    refused = archive.get(got.archived)
    assert refused["decision"] == A.REFUSED and refused["objective"] == run_id
    assert refused["evaluation"]["dimension"] == dimension
    assert refused["evaluation"]["limit"] == LIMITS.of(dimension)
    assert B.check(store, owner_run, now=NOW) == owner_before          # the person's budget is untouched
    store.close()


def test_a_refusal_is_not_a_lost_experiment(tmp_path):
    store, gov = Store(tmp_path / "e.sqlite3"), _gov()
    allowance, archive = _allowance(tmp_path)
    run_id, _ = _running_experiment(store, gov)
    allowance.charge(gov, run_id, money_usd=5.0, now=NOW)
    assert not _step(allowance, archive, run_id).allowed

    assert archive.find_rejected(parent_version="router-v4", hypothesis="Cache the manifest",
                                 scope=["router"]) is None
    archive.require_untried(parent_version="router-v4", hypothesis="Cache the manifest",
                            scope=["router"])
    # a real loss before the refusal still stands after it
    archive.record(objective="o", parent_version="p", candidate_version="c", hypothesis="h",
                   scope=["s"], baseline={"a": 1}, candidate={"a": 2}, evaluation={"v": "FAIL"},
                   decision=A.REJECT, rollback_version="p")
    archive.record(objective="o", parent_version="p", candidate_version="c2", hypothesis="h",
                   scope=["s"], baseline={"a": 1}, candidate={"steps_run": 0},
                   evaluation={"refused": "x"}, decision=A.REFUSED, rollback_version="p")
    assert archive.find_rejected(parent_version="p", hypothesis="h", scope=["s"]) is not None
    store.close()


def test_a_running_evolution_lease_never_makes_a_foreground_task_wait(tmp_path):
    store, gov = Store(tmp_path / "e.sqlite3"), _gov()
    stopped_runs = []
    run_id, lease = _running_experiment(store, gov)

    asked = gov.admit(G.WORKER, label="user task", spawn_reason=G.SPECIALISATION,
                      priority=G.URGENT)
    assert asked.decision == G.ADMIT                  # admitted with evolution still running
    assert any(item["lease"] == lease for item in gov.active_leases())
    gov.release(asked.lease)

    started = time.monotonic()
    stopped = W.yield_to_foreground(store, gov, stop=stopped_runs.append)

    assert time.monotonic() - started < 2.0
    assert [s["run_id"] for s in stopped] == [run_id] and stopped_runs == [run_id]
    assert all(item["lease"] != lease for item in gov.active_leases())
    assert store.objective_run(run_id)["status"] == "CANCELLED"
    assert store.objective_events_named(run_id, (E.EVENT_STOPPED,))
    assert W.yield_to_foreground(store, gov) == []        # nothing left to stop
    store.close()


def test_the_lease_is_released_even_when_the_stop_callback_fails(tmp_path):
    store, gov = Store(tmp_path / "e.sqlite3"), _gov()
    run_id, lease = _running_experiment(store, gov)

    def boom(_):
        raise RuntimeError("worker would not stop")

    with pytest.raises(RuntimeError, match="would not stop"):
        W.yield_to_foreground(store, gov, stop=boom)
    assert all(item["lease"] != lease for item in gov.active_leases())
    assert store.objective_run(run_id)["status"] == "CANCELLED"
    store.close()


def test_idle_research_with_no_trigger_spends_nothing_and_leaves_no_trace(tmp_path):
    store, gov = Store(tmp_path / "e.sqlite3"), _gov()
    allowance, archive = _allowance(tmp_path)
    for trigger in ("", "   "):
        got = _step(allowance, archive, trigger=trigger)
        assert not got.allowed and got.reason == W.REASON_NO_TRIGGER
    assert allowance.records() == [] and archive.query() == []
    assert allowance.report(W.period_of(NOW))["spent"] == {W.MONEY: 0.0, W.RAM: 0.0, W.TIME: 0.0}
    # and there is nothing to draw on without an admitted lease
    with pytest.raises(W.AllowanceError, match="no admitted evolution lease"):
        allowance.charge(gov, "exp-1", money_usd=1.0, now=NOW)
    assert allowance.records() == []
    store.close()


def test_every_spend_is_a_labelled_record_and_the_total_is_their_sum(tmp_path):
    store, gov = Store(tmp_path / "e.sqlite3"), _gov()
    allowance, _ = _allowance(tmp_path, W.AllowanceLimits(usd=100, ram_gb_s=1e6, wall_s=1e6))
    first, _ = _running_experiment(store, gov)
    allowance.charge(gov, first, money_usd=0.10, ram_gb_s=12.5, wall_s=3.0, now=NOW)
    allowance.charge(gov, first, money_usd=0.20, now=NOW)
    gov.release(next(i["lease"] for i in gov.active_leases() if i["label"] == f"evolution:{first}"))
    second, _ = _running_experiment(store, gov)
    allowance.charge(gov, second, money_usd=0.05, wall_s=4.0, now=NOW)

    report = allowance.report(W.period_of(NOW))

    assert len(report["records"]) == 6
    for row in report["records"]:
        assert row["objective_id"] in (first, second) and row["dimension"] in W.DIMENSIONS
        assert row["amount"] > 0 and row["id"].startswith("SPEND-")
    for dim in W.DIMENSIONS:
        assert report["spent"][dim] == pytest.approx(
            sum(r["amount"] for r in report["records"] if r["dimension"] == dim))
    assert report["spent"][W.MONEY] == pytest.approx(0.35)
    assert report["spent"][W.TIME] == pytest.approx(7.0)
    assert [r["amount"] for r in allowance.records(objective_id=second)] == [0.05, 4.0]
    # a record survives a new instance: the ledger is durable
    again = W.EvolutionAllowance(tmp_path / "allowance", LIMITS)
    assert again.spent(W.period_of(NOW), W.MONEY) == pytest.approx(0.35)
    store.close()


def test_an_exhausted_allowance_never_borrows_from_another_dimension_or_period(tmp_path):
    store, gov = Store(tmp_path / "e.sqlite3"), _gov()
    allowance, archive = _allowance(tmp_path)
    run_id, _ = _running_experiment(store, gov)
    owner_run = _run(store, "the person's own task")
    owner_before = B.check(store, owner_run, now=NOW)
    allowance.charge(gov, run_id, money_usd=9.0, wall_s=10.0, now=NOW)       # money far past 2.0

    report = allowance.report(W.period_of(NOW))
    assert report["exhausted"] == [W.MONEY]
    assert report["limits"] == {W.MONEY: 2.0, W.RAM: 100.0, W.TIME: 60.0}    # limits never grow
    assert report["spent"][W.TIME] == 10.0 and report["spent"][W.RAM] == 0.0  # others untouched
    assert report["remaining"][W.MONEY] == 0.0
    assert not _step(allowance, archive, run_id).allowed

    tomorrow = NOW + timedelta(days=1)
    assert allowance.spent(W.period_of(tomorrow), W.MONEY) == 0.0            # nothing carried over
    assert _step(allowance, archive, run_id, now=tomorrow).allowed
    assert B.check(store, owner_run, now=NOW) == owner_before                # the person's budget is untouched
    store.close()


@pytest.mark.parametrize("bad", [-1.0, float("nan"), float("inf"), True, "3"])
def test_a_malformed_spend_is_refused_not_recorded(tmp_path, bad):
    store, gov = Store(tmp_path / "e.sqlite3"), _gov()
    allowance, _ = _allowance(tmp_path)
    run_id, _ = _running_experiment(store, gov)
    with pytest.raises(W.AllowanceError):
        allowance.charge(gov, run_id, money_usd=bad, now=NOW)
    assert allowance.records() == []
    store.close()


def test_a_damaged_ledger_fails_closed(tmp_path):
    store, gov = Store(tmp_path / "e.sqlite3"), _gov()
    allowance, _ = _allowance(tmp_path)
    run_id, _ = _running_experiment(store, gov)
    allowance.charge(gov, run_id, money_usd=0.5, now=NOW)
    with open(allowance.path, "ab") as handle:
        handle.write(b"{torn\n")
    with pytest.raises(W.AllowanceError, match="line 2"):
        allowance.exhausted(W.period_of(NOW))
    with pytest.raises(W.AllowanceError, match="line 2"):
        allowance.charge(gov, run_id, money_usd=0.5, now=NOW)
    store.close()


def _row(**over):
    row = {"id": "SPEND-x1", "at": NOW.isoformat(), "period": W.period_of(NOW),
           "objective_id": "exp-1", "dimension": W.MONEY, "amount": 1.0, "unit": "usd"}
    row.update(over)
    return row


def _raw(row):
    import json
    return json.dumps(row, sort_keys=True)          # json.dumps writes NaN / Infinity bare


@pytest.mark.parametrize("damage", [
    {"amount": float("nan")}, {"amount": float("inf")}, {"amount": -4.0}, {"amount": True},
    {"period": None}, {"objective_id": ""}, {"id": ""}, {"unit": "eur"}, {"unit": None},
], ids=["nan", "inf", "negative", "bool", "no-period", "no-objective", "no-id", "wrong-unit",
        "no-unit"])
def test_a_damaged_row_fails_closed_instead_of_lifting_the_allowance(tmp_path, damage):
    store, gov = Store(tmp_path / "e.sqlite3"), _gov()
    allowance, _ = _allowance(tmp_path)
    run_id, _ = _running_experiment(store, gov)
    allowance.charge(gov, run_id, money_usd=5.0, now=NOW)              # over the 2.0 limit
    with open(allowance.path, "ab") as handle:
        handle.write((_raw(_row(**damage)) + "\n").encode("utf-8"))
    with pytest.raises(W.AllowanceError, match="line 2"):
        allowance.exhausted(W.period_of(NOW))
    with pytest.raises(W.AllowanceError, match="line 2"):
        allowance.charge(gov, run_id, money_usd=0.5, now=NOW)
    store.close()


@pytest.mark.parametrize("missing", ["period", "objective_id", "id", "unit", "amount"])
def test_a_row_missing_a_field_is_an_allowance_error_not_a_keyerror(tmp_path, missing):
    allowance, _ = _allowance(tmp_path)
    allowance.root.mkdir(parents=True)
    row = _row()
    del row[missing]
    allowance.path.write_text(_raw(row) + "\n", encoding="utf-8")
    with pytest.raises(W.AllowanceError, match="line 1"):
        allowance.records(W.period_of(NOW))


def test_a_failing_stop_does_not_leave_another_evolution_lease_running(tmp_path):
    store, gov = Store(tmp_path / "e.sqlite3"), _gov()
    first, _ = _running_experiment(store, gov)
    second = _run(store, "second experiment")
    got = gov.admit(G.OPTIONAL, label=f"{E._LABEL}{second}", objective_id=second,
                    owner="evolution", priority=G.BACKGROUND)
    assert got.admitted, got.reason            # a raised cap must not strand the second lease
    calls = []

    def stop(run_id):
        calls.append(run_id)
        raise RuntimeError("stop failed")

    with pytest.raises(RuntimeError, match="stop failed"):
        W.yield_to_foreground(store, gov, stop=stop)
    assert len(calls) == 2
    assert not [l for l in gov.active_leases() if l["label"].startswith(E._LABEL)]
    store.close()
