"""Phase 14 devops: production-health proofs.

- Migration: a REAL pre-H0 database (production schema from before this
  build) opens cleanly, keeps its rows, gains the new columns/tables.
- Crash recovery: a WorkRun left WORKING by a dead process is visible;
  the delivery sweep backfills old runs without announcing history.
- Missing optional backends (Paperclip) degrade honestly (tested in
  test_orgplane, asserted here end-to-end via control_plane()).
- Stale scratch: an abandoned secret-entry scratch file is inert.
"""
import sqlite3
import time
from friday import hermes_bridge as hb
from friday import orgplane
from friday import secret_broker as sb


def build_pre_vnext_db(path):
    """The production schema as it shipped BEFORE this build (no route
    columns, no route_outcomes, no user_policy/org/skill tables)."""
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE hermes_work_runs ( work_run_id TEXT PRIMARY KEY, friday_run_id TEXT NOT NULL DEFAULT '', hermes_session_id TEXT NOT NULL DEFAULT '', hermes_stored_session_id TEXT NOT NULL DEFAULT '', hermes_version TEXT NOT NULL DEFAULT '', provider TEXT NOT NULL DEFAULT '', model TEXT NOT NULL DEFAULT '', task TEXT NOT NULL DEFAULT '', bundle_chars INTEGER NOT NULL DEFAULT 0, token_budget TEXT NOT NULL DEFAULT 'NORMAL', status TEXT NOT NULL DEFAULT 'DISCONNECTED', pending_question TEXT NOT NULL DEFAULT '', result TEXT NOT NULL DEFAULT '', events_seen INTEGER NOT NULL DEFAULT 0, usage_json TEXT NOT NULL DEFAULT '', started_at REAL NOT NULL, last_event_at REAL NOT NULL)")
    now = time.time()
    db.execute("INSERT INTO hermes_work_runs (work_run_id, task, status, result, started_at, last_event_at) VALUES ('hermes-oldcomplete', 'finished work', 'COMPLETE', 'done', ?, ?)", (now - 7200, now - 7100))
    db.execute("INSERT INTO hermes_work_runs (work_run_id, task, status, started_at, last_event_at) VALUES ('hermes-crashed', 'interrupted work', 'WORKING', ?, ?)", (now - 600, now - 500))
    db.commit()
    db.close()


def test_pre_vnext_database_migrates_and_keeps_rows(tmp_path):
    path = tmp_path / 'prod.sqlite3'
    build_pre_vnext_db(path)
    log = hb.WorkRunLog(path)
    old = log.get('hermes-oldcomplete')
    assert old['result'] == 'done'
    assert old['route_reason'] == ''
    from friday.execution_economics import RouteOutcomes
    from friday.user_policy import UserPolicy
    RouteOutcomes(path).record('hermes-oldcomplete', task_class='x', route_level='l', tier='t', status='COMPLETE')
    assert UserPolicy(path).state_of('research') == 'AUTO'


def test_crashed_working_run_is_visible_not_lost(tmp_path):
    path = tmp_path / 'prod.sqlite3'
    build_pre_vnext_db(path)
    log = hb.WorkRunLog(path)
    active = log.active()
    assert any((r['work_run_id'] == 'hermes-crashed' for r in active))


def test_sweep_backfills_old_completions_silently(tmp_path):
    """A terminal run from hours ago must NOT be announced on boot -
    swept as already-delivered instead (no history recitation)."""
    path = tmp_path / 'prod.sqlite3'
    build_pre_vnext_db(path)
    log = hb.WorkRunLog(path)
    log.sweep_undelivered(max_age_s=3600)
    pending = log.pending_deliveries()
    assert all((d['work_run_id'] != 'hermes-oldcomplete' for d in pending))


def test_paperclip_absent_is_honest_end_to_end():
    plane = orgplane.control_plane()
    assert plane.name == 'local'


def test_abandoned_scratch_is_inert(tmp_path):
    broker = sb.SecretBroker(tmp_path / 'vault')
    scratch = broker.scratch_file('abandoned')
    scratch.write_text('sk-ant-' 'api03-ABANDONEDKEY123456789012345', encoding='utf-8')
    assert broker.list_aliases() == []
    broker.ingest_scratch('abandoned')
    assert not scratch.exists()