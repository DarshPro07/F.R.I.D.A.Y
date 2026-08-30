"""
A finished delegation is news for a while and then it is history.

Measured on 2026-08-27: four smoke-test completions were recorded PENDING at
18:13-18:22 while no LiveKit session was connected. At 22:43 a fresh probe room
opened - a conversation that had asked for none of them - and Friday recited all
four, cold, four and a half hours late.

`hermes_deliveries` has no room or session column, so "deliver what is PENDING"
means "deliver into whoever turns up". That is right for the reconnect case the
feature exists for and wrong past the point where the answer still means
anything. These tests hold the line at the age guard.
"""
from __future__ import annotations
import time
import pytest
from friday import hermes_bridge as hb


@pytest.fixture
def log(tmp_path):
    """A WorkRunLog on its own database. Never the boss's real store."""
    return hb.WorkRunLog(db_path=tmp_path / "deliveries.sqlite3")


def _delivery(log, *, age_s: float, message: str = "Hermes finished: x") -> str:
    run_id = log.create(task="a bounded task")
    log.update(run_id, status=hb.COMPLETE, result="done")
    delivery_id = log.create_delivery(run_id, goal="a bounded task",
                                      status=hb.COMPLETE, message=message)
    assert delivery_id, "the fixture must actually create a delivery"
    with log._connect() as db:
        db.execute("UPDATE hermes_deliveries SET created_at = ?"
                   " WHERE delivery_id = ?",
                   (time.time() - age_s, delivery_id))
    return delivery_id


def test_a_fresh_delivery_is_still_spoken(log):
    """The reconnect case the feature exists for must keep working."""
    delivery_id = _delivery(log, age_s=60)
    pending = log.pending_deliveries()
    assert [d["delivery_id"] for d in pending] == [delivery_id]


def test_a_delivery_older_than_the_ttl_is_not_spoken(log):
    """
    The regression. Without the age guard in `pending_deliveries` this row is
    returned and recited into whatever session is live.
    """
    _delivery(log, age_s=hb.DELIVERY_TTL_S + 60,
              message="Hermes finished: reply with exactly: CONNECTOR SMOKE OK")
    assert log.pending_deliveries() == []


def test_an_expired_delivery_records_why_rather_than_vanishing(log):
    """
    A filtered row is invisible; an EXPIRED row is evidence. The difference
    matters when somebody asks why they were never told.
    """
    delivery_id = _delivery(log, age_s=hb.DELIVERY_TTL_S + 60)
    log.pending_deliveries()
    with log._connect() as db:
        state = db.execute(
            "SELECT delivery_state FROM hermes_deliveries WHERE delivery_id = ?",
            (delivery_id,)).fetchone()[0]
    assert state == "EXPIRED"


def test_expiring_is_idempotent_and_does_not_touch_fresh_rows(log):
    fresh = _delivery(log, age_s=10)
    _delivery(log, age_s=hb.DELIVERY_TTL_S + 60)
    assert [d["delivery_id"] for d in log.pending_deliveries()] == [fresh]
    assert [d["delivery_id"] for d in log.pending_deliveries()] == [fresh]


def test_an_expired_delivery_is_never_resurrected_by_a_later_sweep(log):
    """
    `sweep_undelivered` backfills runs with no delivery row. An EXPIRED row
    still is a row, so the UNIQUE constraint must stop the sweep re-creating it
    - otherwise expiry would only postpone the recital until the next restart.
    """
    _delivery(log, age_s=hb.DELIVERY_TTL_S + 60)
    log.pending_deliveries()
    log.sweep_undelivered()
    assert log.pending_deliveries() == []


def test_the_ttl_is_the_same_number_the_sweep_uses():
    """
    Two age windows for the same question - is this news or history - would
    drift. One constant, asserted so a future edit to one is a test failure.
    """
    import inspect

    for method in (hb.WorkRunLog.pending_deliveries,
                   hb.WorkRunLog.sweep_undelivered):
        default = inspect.signature(method).parameters["max_age_s"].default
        assert default == hb.DELIVERY_TTL_S


def test_a_non_production_origin_is_still_refused_regardless_of_age(log):
    """
    Age is a second guard, not a replacement for origin isolation. A gate run
    must not become deliverable by being recent.
    """
    run_id = log.create(task="gate probe")
    with log._connect() as db:
        db.execute("UPDATE hermes_work_runs SET origin = 'golden_gate'"
                   " WHERE work_run_id = ?", (run_id,))
    log.update(run_id, status=hb.COMPLETE, result="done")
    assert log.create_delivery(run_id, goal="gate probe",
                              status=hb.COMPLETE, message="NEVER SPEAK") is None
    assert log.pending_deliveries() == []


def _objective_delivery(store, *, age_s: float, run_id: str) -> int:
    import datetime as _dt
    store.create_objective_delivery(run_id, 'The objective finished.')
    created = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(seconds=age_s)).isoformat()
    with store._tx() as conn:
        conn.execute('UPDATE objective_deliveries SET created_at = ? WHERE run_id = ?', (created, run_id))
        return conn.execute('SELECT delivery_id FROM objective_deliveries WHERE run_id = ?', (run_id,)).fetchone()[0]


@pytest.fixture
def store(tmp_path):
    from friday import store as store_module
    return store_module.Store(tmp_path / 'objectives.sqlite3')


def test_a_fresh_objective_completion_is_still_announced(store):
    delivery_id = _objective_delivery(store, age_s=60, run_id='run-fresh')
    assert [d['delivery_id'] for d in store.pending_objective_deliveries()] == [delivery_id]


def test_an_objective_completion_older_than_the_ttl_is_not_announced(store):
    """
    The second regression, measured the same evening as the first: fourteen
    objective completions written at 11:09 UTC were recited into a fresh probe
    room at 17:21 UTC.
    """
    from friday import store as store_module
    _objective_delivery(store, age_s=store_module.OBJECTIVE_DELIVERY_TTL_S + 60, run_id='run-stale')
    assert store.pending_objective_deliveries() == []


def test_an_expired_objective_delivery_records_why(store):
    from friday import store as store_module
    _objective_delivery(store, age_s=store_module.OBJECTIVE_DELIVERY_TTL_S + 60, run_id='run-stale-2')
    store.pending_objective_deliveries()
    with store._tx() as conn:
        state = conn.execute('SELECT delivery_state FROM objective_deliveries WHERE run_id = ?', ('run-stale-2',)).fetchone()[0]
    assert state == 'EXPIRED'


def test_both_halves_of_the_seam_use_the_same_window():
    from friday import store as store_module
    assert store_module.OBJECTIVE_DELIVERY_TTL_S == hb.DELIVERY_TTL_S
