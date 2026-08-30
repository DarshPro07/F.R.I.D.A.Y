"""The completion-delivery broker's durable half.

Every guarantee the delivery loop relies on is a database property, so
every test here runs against the real sqlite layer: exactly-once creation,
atomic claims, release-on-failure, startup sweep, and old-run backfill.
"""
import time
import pytest
from friday import hermes_bridge as hb


@pytest.fixture()
def log(tmp_path):
    return hb.WorkRunLog(tmp_path / 'runs.sqlite3')


def finished_run(log, *, status=hb.COMPLETE, result='three findings...', age_s=0.0):
    run_id = log.create(task='inspect the project')
    log.update(run_id, status=status, result=result)
    if age_s:
        with log._connect() as db:
            db.execute('UPDATE hermes_work_runs SET last_event_at = ? WHERE work_run_id = ?', (time.time() - age_s, run_id))
    return run_id


def test_delivery_created_exactly_once(log):
    run_id = finished_run(log)
    first = log.create_delivery(run_id, goal='g', status=hb.COMPLETE, message='m')
    second = log.create_delivery(run_id, goal='g', status=hb.COMPLETE, message='m')
    assert first is not None
    assert second is None
    assert len(log.pending_deliveries()) == 1


def test_claim_is_atomic_and_single_winner(log):
    run_id = finished_run(log)
    delivery_id = log.create_delivery(run_id, goal='g', status=hb.COMPLETE, message='m')
    assert log.claim_delivery(delivery_id) is True
    assert log.claim_delivery(delivery_id) is False
    assert log.pending_deliveries() == []


def test_release_puts_a_failed_attempt_back(log):
    run_id = finished_run(log)
    delivery_id = log.create_delivery(run_id, goal='g', status=hb.COMPLETE, message='m')
    log.claim_delivery(delivery_id)
    log.release_delivery(delivery_id)
    assert [d['delivery_id'] for d in log.pending_deliveries()] == [delivery_id]


def test_mark_delivered_is_terminal(log):
    run_id = finished_run(log)
    delivery_id = log.create_delivery(run_id, goal='g', status=hb.COMPLETE, message='m')
    log.claim_delivery(delivery_id)
    log.mark_delivered(delivery_id, via='session.say')
    assert log.pending_deliveries() == []
    log.release_delivery(delivery_id)
    assert log.pending_deliveries() == []


def test_sweep_creates_deliveries_for_orphan_terminal_runs(log):
    """The crash window: run finished, delivery insert never happened."""
    finished_run(log)
    finished_run(log, status=hb.FAILED, result='repo unavailable')
    assert log.sweep_undelivered() == 2
    assert log.sweep_undelivered() == 0
    states = [d['status'] for d in log.pending_deliveries()]
    assert sorted(states) == [hb.COMPLETE, hb.FAILED]


def test_sweep_backfills_ancient_runs_silently(log):
    """First boot after the feature ships must not recite history."""
    finished_run(log, age_s=25200)
    assert log.sweep_undelivered() == 0
    assert log.pending_deliveries() == []


def test_working_runs_are_not_swept(log):
    run_id = log.create(task='still going')
    log.update(run_id, status=hb.WORKING)
    assert log.sweep_undelivered() == 0


def test_render_completion_success_carries_findings():
    message = hb.render_completion({'task': 'inspect the project', 'status': hb.COMPLETE, 'result': '1. islands...\n2. admission...\n3. prompt...'})
    assert 'Hermes finished' in message
    assert 'islands' in message


def test_render_completion_failure_names_the_failure():
    message = hb.render_completion({'task': 'review', 'status': hb.FAILED, 'result': 'repository became unavailable', 'work_run_id': 'hermes-abc'})
    assert "couldn't finish" in message
    assert 'repository became unavailable' in message
    assert 'hermes-abc' in message


def test_supervisor_terminal_event_creates_delivery(tmp_path, monkeypatch):
    """End-to-end through the event handler: message.complete lands ->
    run goes COMPLETE -> a PENDING delivery exists, in one motion."""
    import sys
    from pathlib import Path
    FAKE = str(Path(__file__).parent / 'fake_hermes_gateway.py')
    log = hb.WorkRunLog(tmp_path / 'e2e.sqlite3')
    supervisor = hb.HermesSupervisor(log=log, command=[sys.executable, FAKE], profile='')
    supervisor.READY_TIMEOUT = 20
    try:
        out = supervisor.delegate(hb.TaskBundle(goal='say hello'), wait=True, turn_timeout=30)
        assert out['result']['status'] == hb.COMPLETE
        pending = log.pending_deliveries()
        assert len(pending) == 1
        assert pending[0]['work_run_id'] == out['work_run_id']
        assert 'Hermes finished' in pending[0]['message']
    finally:
        supervisor.stop()