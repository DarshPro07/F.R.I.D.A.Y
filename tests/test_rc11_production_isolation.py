"""RC1.1 production-isolation cleanup — stale gate deliveries + honest
WorkRun refusal classification.

Live production evidence (2026-08-27): after C1-C9, a real Friday startup
spoke two OLD recovery-gate prompts ("bridge recovered"/"gateway recovered",
runs hermes-efff49e33a / hermes-c10474cdf4) into the user's session as
Hermes failures. Two distinct defects:

1. ISOLATION: gate/test WorkRuns carried no origin, so the startup sweep
   and the delivery drain treated them as user-facing. Exactly-once held;
   exactly-to-the-right-context did not exist.
2. CLASSIFICATION: `_refused` keys are lowercase ActionResult statuses; a
   WorkRun record's uppercase FAILED matched nothing, so a FAILED
   delegation was recorded SUCCEEDED - which is how C3/C4 claimed
   "delegation answered" while the model call actually failed at provider
   auth. Provider-auth failure must classify as USER_REQUIRED (an auth
   boundary), never as CONNECTIVITY and never as success.
"""
from __future__ import annotations
import os
import tempfile
import time
from pathlib import Path
import pytest
from friday import continuous as C
from friday import objectives as O


def test_uppercase_workrun_failed_is_a_refusal_not_a_success():
    record = {'work_run_id': 'hermes-x', 'status': 'FAILED', 'error': 'the worker crashed'}
    refusal = C._refused(record)
    assert refusal is not None, 'a FAILED WorkRun record slipped through as success - the C3/C4 false-completion shape'
    assert refusal[0] == O.FailureKind.STRUCTURAL


def test_provider_auth_failure_is_user_required_not_connectivity():
    record = {'status': 'FAILED', 'result': '{"message": "agent init failed: No Anthropic credentials found. Set ANTHROPIC_TOKEN or ANTHROPIC_API_KEY, run \'claude setup-token\', or authenticate with \'claude /login\'."}'}
    refusal = C._refused(record)
    assert refusal is not None
    kind, reason = refusal
    assert kind == O.FailureKind.USER_REQUIRED, f"provider auth must be an auth boundary, got {kind}"
    assert kind != O.FailureKind.CONNECTIVITY
    assert 'credentials' in reason.lower() or 'auth' in reason.lower()


def test_auth_words_veto_connectivity_classification():
    record = {'status': 'not_configured', 'error': 'gateway agent init failed: credentials required, authenticate with /login'}
    kind, _ = C._refused(record)
    assert kind == O.FailureKind.USER_REQUIRED


def test_workrun_complete_and_partial_are_not_failures():
    assert C._refused({'status': 'COMPLETE', 'result': 'fine'}) is None
    assert C._refused({'status': 'PARTIAL', 'result': 'half'}) is None


def test_delegate_envelope_with_nested_failed_record_is_a_refusal():
    """The EXACT durable shape from RUN-fcf041fe742e that was recorded
    SUCCEEDED: supervisor.delegate's envelope with FAILED one level down."""
    envelope = {'work_run_id': 'hermes-53b70854a2', 'session_id': '6c994e1f', 'bundle': {'chars': 715}, 'result': {'work_run_id': 'hermes-53b70854a2', 'status': 'FAILED', 'result': '{"message": "agent init failed: No Anthropic credentials found"}'}}
    refusal = C._refused(envelope)
    assert refusal is not None, 'the envelope shape slipped through again'
    assert refusal[0] == O.FailureKind.USER_REQUIRED


def test_delegate_envelope_with_nested_complete_is_not_a_refusal():
    envelope = {'work_run_id': 'x', 'result': {'status': 'COMPLETE', 'result': 'one line'}}
    assert C._refused(envelope) is None


@pytest.fixture
def log(tmp_path, monkeypatch):
    from friday import hermes_bridge as hb
    monkeypatch.setenv('FRIDAY_HERMES_DB', str(tmp_path / 'wr.db'))
    monkeypatch.delenv('FRIDAY_RUN_ORIGIN', raising=False)
    return hb.WorkRunLog(path=tmp_path / 'wr.db')


def test_gate_workrun_is_never_delivered_to_the_user(log, monkeypatch):
    """Negative control: a golden-gate run, left FAILED, must not surface."""
    monkeypatch.setenv('FRIDAY_RUN_ORIGIN', 'golden_gate')
    work_run_id = log.create(task='Reply with exactly one line: gate probe')
    log.update(work_run_id, status='FAILED', result='{"message": "agent init failed"}')
    monkeypatch.delenv('FRIDAY_RUN_ORIGIN', raising=False)
    log.sweep_undelivered()
    pending = log.pending_deliveries()
    assert pending == [], f"a gate WorkRun reached the user-facing delivery queue: {pending}"


def test_production_workrun_is_still_delivered_exactly_once(log):
    """Positive control: crash recovery for real work is unchanged."""
    work_run_id = log.create(task='real user work')
    log.update(work_run_id, status='COMPLETE', result='{"ok": true}')
    log.sweep_undelivered()
    pending = log.pending_deliveries()
    assert len(pending) == 1
    assert pending[0]['work_run_id'] == work_run_id
    log.sweep_undelivered()
    assert len(log.pending_deliveries()) == 1


def test_event_path_delivery_is_also_origin_filtered(log, monkeypatch):
    """The sweep is not the only writer; the completion event path must
    obey the same isolation choke point."""
    monkeypatch.setenv('FRIDAY_RUN_ORIGIN', 'test')
    work_run_id = log.create(task='harness run')
    monkeypatch.delenv('FRIDAY_RUN_ORIGIN', raising=False)
    log.update(work_run_id, status='COMPLETE', result='{"ok": true}')
    created = log.create_delivery(work_run_id, goal='harness run', status='COMPLETE', message='done')
    assert not log.pending_deliveries(), 'an event-path delivery for a non-production run became PENDING'


def test_origin_defaults_to_production(log):
    work_run_id = log.create(task='ordinary')
    assert log.get(work_run_id).get('origin', 'production') == 'production'