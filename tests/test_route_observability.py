"""H0 route observability: every run carries provider/model/route_reason/
fallback fields durably, and old databases migrate additively.

The spec's gate: "If you cannot observe this, H cannot pass yet."
These tests make the observability itself regression-proof.
"""
import sqlite3
import sys
from pathlib import Path
import pytest
from friday import hermes_bridge as hb
FAKE = str(Path(__file__).parent / 'fake_hermes_gateway.py')


def make_supervisor(tmp_path, name):
    log = hb.WorkRunLog(tmp_path / name)
    supervisor = hb.HermesSupervisor(log=log, command=[sys.executable, FAKE], profile='')
    supervisor.READY_TIMEOUT = 20
    return supervisor


@pytest.fixture()
def log(tmp_path):
    return hb.WorkRunLog(tmp_path / 'runs.sqlite3')


def test_new_database_has_route_columns(log):
    columns = {r[1] for r in sqlite3.connect(log._path).execute('PRAGMA table_info(hermes_work_runs)')}
    assert {'fallback_from', 'fallback_to', 'route_reason', 'fallback_reason'} <= columns


def test_old_database_migrates_additively(tmp_path):
    """A pre-H0 database (no route columns) gains them on open, keeping
    its rows: the migration is ALTER-only, never a rewrite."""
    path = tmp_path / 'old.sqlite3'
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE hermes_work_runs ( work_run_id TEXT PRIMARY KEY, friday_run_id TEXT NOT NULL DEFAULT '', hermes_session_id TEXT NOT NULL DEFAULT '', hermes_stored_session_id TEXT NOT NULL DEFAULT '', hermes_version TEXT NOT NULL DEFAULT '', provider TEXT NOT NULL DEFAULT '', model TEXT NOT NULL DEFAULT '', task TEXT NOT NULL DEFAULT '', bundle_chars INTEGER NOT NULL DEFAULT 0, token_budget TEXT NOT NULL DEFAULT 'NORMAL', status TEXT NOT NULL DEFAULT 'DISCONNECTED', pending_question TEXT NOT NULL DEFAULT '', result TEXT NOT NULL DEFAULT '', events_seen INTEGER NOT NULL DEFAULT 0, usage_json TEXT NOT NULL DEFAULT '', started_at REAL NOT NULL, last_event_at REAL NOT NULL)")
    db.execute("INSERT INTO hermes_work_runs (work_run_id, task, status, started_at, last_event_at) VALUES ('hermes-old1', 'old task', 'COMPLETE', 1.0, 2.0)")
    db.commit()
    db.close()
    migrated = hb.WorkRunLog(path)
    record = migrated.get('hermes-old1')
    assert record['task'] == 'old task'
    assert record['route_reason'] == ''


def test_update_accepts_route_fields(log):
    run = log.create(task='t')
    log.update(run, route_reason='hard reasoning', fallback_from='a', fallback_to='b', fallback_reason='a unavailable')
    record = log.get(run)
    assert record['route_reason'] == 'hard reasoning'
    assert record['fallback_from'] == 'a'
    assert record['fallback_to'] == 'b'
    assert record['fallback_reason'] == 'a unavailable'


def test_delegate_records_route(tmp_path):
    """Through a live (fake) gateway: the durable record carries the
    effective provider/model and the caller's route_reason."""
    sup = make_supervisor(tmp_path, 'd.sqlite3')
    sup.start()
    try:
        out = sup.delegate(hb.TaskBundle(goal='g'), route_reason='test route', wait=True, turn_timeout=30)
        record = sup.log.get(out['work_run_id'])
        assert record['model'] == 'fake-model'
        assert record['provider'] == 'fake'
        assert record['route_reason'] == 'test route'
        assert record['fallback_from'] == ''
    finally:
        sup.stop()


def test_delegate_records_fallback_on_model_mismatch(tmp_path):
    """Requesting a model the gateway does not honour records the
    mismatch as an explicit fallback, never a silent normalization."""
    sup = make_supervisor(tmp_path, 'f.sqlite3')
    sup.start()
    try:
        out = sup.delegate(hb.TaskBundle(goal='g'), model='requested-model', wait=True, turn_timeout=30)
        record = sup.log.get(out['work_run_id'])
        assert record['model'] == 'fake-model'
        assert record['fallback_from'] == 'requested-model'
        assert record['fallback_to'] == 'fake-model'
        assert record['fallback_reason']
    finally:
        sup.stop()