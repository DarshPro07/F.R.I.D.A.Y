"""
The durable-objective engine must work on a *fresh* database.

Regression for a real bug found 2026-08-29 while running the autonomous test:
`continuity.start_run` INSERTs into `runs` (columns attended, provenance) and
into run_controls / run_tasks, but the reconstructed schema created only the
`runs` table and omitted attended/provenance and all eight run_* tables. The
live database had them from an older schema it was migrated across, so the bug
was invisible in production and total on any clean install - objective
admission failed with:

    sqlite3.OperationalError: table runs has no column named attended
    sqlite3.OperationalError: no such table: run_controls

Which means the whole autonomous (Cowork-like) capability was broken for
anyone starting from an empty database. These tests would have caught it, and
now guard it.
"""

from __future__ import annotations

import datetime as dt

import pytest

from friday import contracts as c
from friday import continuity as C
from friday.store import Store


def _clock():
    return dt.datetime.now(dt.timezone.utc)


@pytest.fixture()
def fresh_manager(tmp_path):
    """A ContinuityManager over a brand-new database - the broken scenario."""
    store = Store(tmp_path / "fresh.sqlite3")
    yield C.ContinuityManager(store, clock=_clock), store, tmp_path / "fresh.sqlite3"
    store.close()


def test_the_runs_table_has_the_columns_continuity_writes():
    """attended and provenance must exist, or start_run fails on a fresh DB."""
    import sqlite3
    import tempfile
    import pathlib

    db = pathlib.Path(tempfile.mkdtemp()) / "s.sqlite3"
    store = Store(db)
    cols = {r[1] for r in store._conn.execute("PRAGMA table_info(runs)")}
    store.close()
    assert {"attended", "provenance"} <= cols, (
        f"runs is missing {{'attended','provenance'}} - {sorted(cols)}")


def test_all_continuity_tables_exist_on_a_fresh_db():
    """The eight run_* tables continuity.py writes must be created by the schema."""
    import tempfile
    import pathlib

    db = pathlib.Path(tempfile.mkdtemp()) / "s.sqlite3"
    store = Store(db)
    tables = {r[0] for r in store._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    store.close()
    required = {"runs", "run_controls", "run_tasks", "run_task_attempts",
                "run_portions", "run_wakes", "run_checkpoints", "run_events",
                "run_narrations"}
    assert required <= tables, f"missing continuity tables: {required - tables}"


def test_an_objective_is_admitted_on_a_fresh_database(fresh_manager):
    manager, _store, _db = fresh_manager
    snap = manager.start_run("autonomous readiness check",
                             initial_task="inspect system",
                             provenance=c.PERSON, attended=True)
    assert snap.run_id
    assert snap.state == "working"
    status = manager.status(snap.run_id)
    assert status.state == "working"
    assert len(status.tasks) == 1


def test_the_objective_survives_a_restart(tmp_path):
    """
    The Cowork-like property: a durable objective persists across a process
    restart, modelled here as a second Store over the same file.
    """
    db = tmp_path / "restart.sqlite3"
    store = Store(db)
    manager = C.ContinuityManager(store, clock=_clock)
    snap = manager.start_run("survive a restart", initial_task="step one",
                             provenance=c.PERSON, attended=True)
    store.close()

    store2 = Store(db)
    manager2 = C.ContinuityManager(store2, clock=_clock)
    recovered = manager2.status(snap.run_id)
    store2.close()
    assert recovered.run_id == snap.run_id
    assert recovered.state == "working"


def test_provenance_is_validated(fresh_manager):
    """The column exists AND the value is checked - a bad provenance is refused."""
    manager, _store, _db = fresh_manager
    with pytest.raises(C.ContinuityInvariantError):
        manager.start_run("bad provenance", initial_task="x",
                          provenance="nonsense", attended=True)
