from __future__ import annotations
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
import pytest
from friday import contracts as c
from friday import continuity as C
from friday.store import Store
NOW = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def store(tmp_path):
    opened = Store(tmp_path / 'continuity.sqlite3')
    yield opened
    opened.close()


@pytest.fixture
def manager(store):
    return C.ContinuityManager(store, clock=lambda: NOW)


def test_start_run_persists_authority_task_and_one_immediate_wake(manager):
    snapshot = manager.start_run('audit the test-owned workspace', provenance=c.PERSON, attended=True, initial_task='inspect the workspace')
    assert snapshot.state == 'working'
    assert snapshot.provenance == c.PERSON
    assert snapshot.attended is True
    assert len(snapshot.tasks) == 1
    assert snapshot.tasks[0]['status'] == 'runnable'
    assert snapshot.wake['kind'] == C.IMMEDIATE
    assert snapshot.wake['task_id'] == snapshot.tasks[0]['task_id']


def test_claim_checkpoint_reopen_and_reclaim_same_run(manager, store):
    started = manager.start_run('finish a long audit', initial_task='inspect the first area')
    first = manager.claim_run(started.run_id, 'worker-a')
    assert first is not None
    checkpointed = manager.checkpoint(first, summary='first bounded portion inspected one area', wake=C.WakeCondition.immediate(detail='continue the audit'))
    assert checkpointed.run_id == started.run_id
    assert checkpointed.wake['generation'] > first.wake_generation
    path = store.path
    store.close()
    reopened = Store(path)
    try:
        recovered = C.ContinuityManager(reopened, clock=lambda: NOW)
        second = recovered.claim_run(started.run_id, 'worker-b')
        assert second is not None
        assert second.run_id == first.run_id
        assert second.portion_id != first.portion_id
    finally:
        reopened.close()


def test_duplicate_wake_claim_creates_only_one_portion(manager):
    started = manager.start_run('continue once', initial_task='do one portion')
    claimed = manager.claim_run(started.run_id, 'worker-a')
    duplicate = manager.claim_run(started.run_id, 'worker-b')
    assert claimed is not None
    assert duplicate is None
    assert manager.status(started.run_id).counters['portions'] == 1


def test_narration_reservation_is_atomic_for_one_semantic_milestone(manager):
    started = manager.start_run('open one app', initial_task='open Calculator')
    claim = manager.claim_run(started.run_id, 'worker-a')

    def reserve(index):
        return manager.reserve_narration(claim, milestone_key=f"app:opened:calculator:{claim.task_id}", speech_id=f"speech-{index}")
    with ThreadPoolExecutor(max_workers=4) as pool:
        outcomes = list(pool.map(reserve, range(4)))
    assert outcomes.count(True) == 1
    assert outcomes.count(False) == 3


def test_narration_reservation_survives_store_reopen(tmp_path):
    path = tmp_path / 'narration.sqlite3'
    store = Store(path)
    manager = C.ContinuityManager(store, clock=lambda: NOW)
    started = manager.start_run('play once', initial_task='play jazz')
    claim = manager.claim_run(started.run_id, 'worker-a')
    key = f"music:playing:jazz:{claim.task_id}"
    assert manager.reserve_narration(claim, milestone_key=key, speech_id='speech-a')
    store.close()
    reopened = Store(path)
    try:
        recovered = C.ContinuityManager(reopened, clock=lambda: NOW)
        assert not recovered.reserve_narration(claim, milestone_key=key, speech_id='speech-b')
    finally:
        reopened.close()


def test_non_terminal_checkpoint_requires_a_wake(manager):
    started = manager.start_run('stay schedulable', initial_task='work')
    claim = manager.claim_run(started.run_id, 'worker-a')
    with pytest.raises(C.ContinuityInvariantError, match='wake'):
        manager.checkpoint(claim, summary='not enough', wake=None)
    assert manager.status(started.run_id).state == 'working'


def test_checkpoint_refuses_a_task_reference_missing_from_this_run(manager):
    started = manager.start_run('keep references sound', initial_task='work')
    claim = manager.claim_run(started.run_id, 'worker-a')
    with pytest.raises(C.ContinuityInvariantError, match='task'):
        manager.checkpoint(claim, summary='bad reference', wake=C.WakeCondition.immediate().with_task('TASK-missing'))
    assert manager.status(started.run_id).wake['kind'] == C.TOOL_COMPLETION


def test_invariant_detector_rejects_a_manually_deleted_wake(manager, store):
    started = manager.start_run('detect mutation', initial_task='work')
    with pytest.raises(C.ContinuityInvariantError, match='requires 1 wake'):
        with store._tx() as conn:
            conn.execute('DELETE FROM run_wakes WHERE run_id=?', (started.run_id,))
            manager._validate(conn, started.run_id)
    assert manager.status(started.run_id).wake['kind'] == C.IMMEDIATE


def test_only_the_matching_external_signal_makes_a_run_runnable(manager):
    started = manager.start_run('wait for a job', initial_task='collect its result')
    claim = manager.claim_run(started.run_id, 'worker-a')
    waiting = manager.checkpoint(claim, summary='job submitted', wake=C.WakeCondition(C.EXTERNAL_STATE, detail='job job-7 completes', signal_key='job-7'))
    stale = manager.signal_wake(started.run_id, kind=C.EXTERNAL_STATE, signal_key='job-old')
    assert stale.wake['kind'] == C.EXTERNAL_STATE
    signalled = manager.signal_wake(started.run_id, kind=C.EXTERNAL_STATE, signal_key='job-7')
    assert signalled.wake['kind'] == C.IMMEDIATE
    assert signalled.wake['generation'] > waiting.wake['generation']


def test_provider_exhaustion_schedules_a_bounded_future_retry(store):
    now = [NOW]
    manager = C.ContinuityManager(store, clock=lambda: now[0])
    started = manager.start_run('survive the provider', initial_task='inspect')
    claim = manager.claim_run(started.run_id, 'worker-a')
    retrying = manager.provider_failed(claim, error='503 unavailable', retryable=True)
    assert retrying.state == 'retrying'
    assert retrying.wake['kind'] == C.SCHEDULED_RETRY
    assert retrying.counters['scheduler_retries'] == 1
    assert manager.claim_run(started.run_id, 'too-early') is None
    now[0] += timedelta(seconds=2)
    assert manager.claim_run(started.run_id, 'after-backoff') is not None


def test_a_successful_attempt_is_never_reserved_for_execution_twice(manager):
    started = manager.start_run('write once', initial_task='write the result')
    claim = manager.claim_run(started.run_id, 'worker-a')
    first = manager.reserve_attempt(claim, task_id=claim.task_id, idempotency_key='write:result.txt:v1')
    duplicate = manager.reserve_attempt(claim, task_id=claim.task_id, idempotency_key='write:result.txt:v1')
    assert first.execute is True
    assert duplicate.attempt_id == first.attempt_id
    assert duplicate.execute is False
    manager.settle_attempt(claim, first.attempt_id, status='succeeded', result_ref='RESULT-1')
    after_success = manager.reserve_attempt(claim, task_id=claim.task_id, idempotency_key='write:result.txt:v1')
    assert after_success.execute is False
    assert after_success.status == 'succeeded'


def test_pause_invalidates_a_claim_and_cancel_defeats_stale_wakes(manager):
    started = manager.start_run('remain controllable', initial_task='work')
    stale_claim = manager.claim_run(started.run_id, 'worker-a')
    paused = manager.pause_run(started.run_id, 'person paused it')
    assert paused.state == 'paused'
    assert paused.wake['kind'] == C.USER_INPUT
    with pytest.raises(C.StaleClaim):
        manager.checkpoint(stale_claim, summary='late callback', wake=C.WakeCondition.immediate())
    resumed = manager.resume_run(started.run_id)
    assert resumed.state == 'working'
    assert resumed.wake['kind'] == C.IMMEDIATE
    cancelled = manager.cancel_run(started.run_id, 'person cancelled')
    assert cancelled.state == 'cancelled'
    assert cancelled.wake is None
    assert manager.claim_run(started.run_id, 'late-worker') is None


def test_a_total_portion_budget_stops_without_claiming_success(store):
    manager = C.ContinuityManager(store, clock=lambda: NOW)
    started = manager.start_run('do not loop', initial_task='non-converging task', total_budget=C.RunBudget(max_portions=1))
    claim = manager.claim_run(started.run_id, 'worker-a')
    manager.checkpoint(claim, summary='still not done', wake=C.WakeCondition.immediate())
    assert manager.claim_run(started.run_id, 'worker-b') is None
    stopped = manager.status(started.run_id)
    assert stopped.state == 'partial'
    assert stopped.outcome == 'budget_exhausted:portions'
    assert stopped.wake is None


def test_completion_requires_a_succeeded_verification_task(manager):
    started = manager.start_run('finish honestly', initial_task='do the work')
    work = manager.claim_run(started.run_id, 'worker-a')
    verify_id = manager.add_task(work, description='verify the work', dependencies=(work.task_id,), verification_required=True)
    manager.checkpoint(work, summary='work complete; verification remains', completed_task_ids=(work.task_id,), evidence_refs=('RESULT-work',), wake=C.WakeCondition.immediate().with_task(verify_id))
    verification = manager.claim_run(started.run_id, 'worker-b')
    with pytest.raises(C.ContinuityInvariantError, match='verification'):
        manager.complete_run(verification, verification_task_id=verify_id, evidence_refs=())
    with pytest.raises(C.ContinuityInvariantError, match='current run'):
        manager.complete_run(verification, verification_task_id=verify_id, evidence_refs=('VERIFY-not-observed',))
    manager.record_actions(verification, count=1, evidence_refs=('VERIFY-tests-passed',))
    completed = manager.complete_run(verification, verification_task_id=verify_id, evidence_refs=('VERIFY-tests-passed',))
    assert completed.state == 'completed'
    assert completed.outcome == 'succeeded'
    assert completed.wake is None


def test_an_expired_active_lease_recovers_to_a_new_portion(store):
    now = [NOW]
    first = C.ContinuityManager(store, clock=lambda: now[0], lease_seconds=30)
    started = first.start_run('survive a crash', initial_task='continue safely')
    abandoned = first.claim_run(started.run_id, 'dead-worker')
    now[0] += timedelta(seconds=31)
    recovered = C.ContinuityManager(store, clock=lambda: now[0], lease_seconds=30)
    next_claim = recovered.claim_next_due('fresh-worker')
    assert next_claim is not None
    assert next_claim.run_id == abandoned.run_id
    assert next_claim.portion_id != abandoned.portion_id
    portions = store._conn.execute('SELECT status FROM run_portions WHERE run_id=? ORDER BY started_at, portion_id', (started.run_id,)).fetchall()
    assert {row['status'] for row in portions} == {'interrupted', 'running'}


def test_an_expired_attempt_with_unknown_side_effect_cannot_be_retried(store):
    now = [NOW]
    manager = C.ContinuityManager(store, clock=lambda: now[0], lease_seconds=30)
    started = manager.start_run('do not duplicate', initial_task='send once')
    claim = manager.claim_run(started.run_id, 'dead-worker')
    manager.reserve_attempt(claim, task_id=claim.task_id, idempotency_key='send:message-1')
    now[0] += timedelta(seconds=31)
    manager.recover_expired_leases()
    waiting = manager.status(started.run_id)
    assert waiting.state == 'waiting_external'
    assert waiting.tasks[0]['status'] == 'unknown'
    assert manager.claim_next_due('fresh-worker') is None
    still_waiting = manager.signal_wake(started.run_id, kind=C.EXTERNAL_STATE, signal_key=f"reconcile:{claim.portion_id}")
    assert still_waiting.state == 'waiting_external'
    assert still_waiting.wake['kind'] == C.EXTERNAL_STATE


@pytest.mark.parametrize(('wake', 'state'), [(C.WakeCondition.immediate('continue'), 'working'), (C.WakeCondition(C.SCHEDULED_RETRY, detail='retry'), 'retrying'), (C.WakeCondition(C.TOOL_COMPLETION, detail='tool', signal_key='tool-1'), 'waiting_tool'), (C.WakeCondition(C.PROVIDER_AVAILABLE, detail='provider', signal_key='p-1'), 'waiting_external'), (C.WakeCondition(C.EXTERNAL_STATE, detail='external', signal_key='e-1'), 'waiting_external'), (C.WakeCondition(C.USER_INPUT, detail='which target'), 'waiting_user'), (C.WakeCondition(C.USER_SECRET, detail='sign in directly'), 'waiting_secret'), (C.WakeCondition(C.PERMISSION, detail='confirm exact shutdown'), 'waiting_permission')])
def test_each_wake_kind_maps_to_one_truthful_nonterminal_state(manager, wake, state):
    started = manager.start_run('map the wake', initial_task='work')
    claim = manager.claim_run(started.run_id, 'worker-a')
    snapshot = manager.checkpoint(claim, summary='bounded', wake=wake)
    assert snapshot.state == state
    assert snapshot.wake['kind'] == wake.kind


def test_action_budget_is_enforced_when_the_spend_lands(store):
    # S3: the total budget used to be checked only at the NEXT claim, which let
    # a run overshoot by a whole portion. Same outcome, recorded immediately.
    manager = C.ContinuityManager(store, clock=lambda: NOW)
    started = manager.start_run('bound actions', initial_task='work', total_budget=C.RunBudget(max_actions=1))
    claim = manager.claim_run(started.run_id, 'worker-a')
    assert manager.record_actions(claim, count=1).outcome == 'budget_exhausted:actions'
    assert manager.claim_run(started.run_id, 'worker-b') is None
    assert manager.status(started.run_id).outcome == 'budget_exhausted:actions'


def test_model_token_budget_is_enforced_when_the_spend_lands(store):
    manager = C.ContinuityManager(store, clock=lambda: NOW)
    started = manager.start_run('bound tokens', initial_task='work', total_budget=C.RunBudget(max_model_tokens=5))
    claim = manager.claim_run(started.run_id, 'worker-a')
    assert manager.record_model_tokens(claim, 5).outcome == 'budget_exhausted:model_tokens'
    assert manager.claim_run(started.run_id, 'worker-b') is None
    assert manager.status(started.run_id).outcome == 'budget_exhausted:model_tokens'


def test_elapsed_budget_is_enforced_at_the_next_claim(store):
    now = [NOW]
    manager = C.ContinuityManager(store, clock=lambda: now[0])
    started = manager.start_run('bound time', initial_task='work', total_budget=C.RunBudget(max_elapsed_seconds=1))
    claim = manager.claim_run(started.run_id, 'worker-a')
    manager.checkpoint(claim, summary='more remains', wake=C.WakeCondition.immediate())
    now[0] += timedelta(seconds=1)
    assert manager.claim_run(started.run_id, 'worker-b') is None
    assert manager.status(started.run_id).outcome == 'budget_exhausted:elapsed_time'


def test_retry_budget_exhaustion_is_an_explicit_failure(store):
    manager = C.ContinuityManager(store, clock=lambda: NOW)
    started = manager.start_run('bound retries', initial_task='work', total_budget=C.RunBudget(max_scheduler_retries=0))
    claim = manager.claim_run(started.run_id, 'worker-a')
    failed = manager.provider_failed(claim, error='503', retryable=True)
    assert failed.state == 'failed'
    assert failed.outcome == 'provider_retries_exhausted'
    assert failed.wake is None

def test_portion_token_cap_marks_claim_exhausted(store):
    manager = C.ContinuityManager(store, clock=lambda: NOW)
    started = manager.start_run('bound one portion', initial_task='work', portion_budget=C.PortionBudget(max_model_tokens=32000))
    claim = manager.claim_run(started.run_id, 'worker-a')
    snapshot = manager.record_model_tokens(claim, 33000)
    assert snapshot.budget_exhausted.startswith('portion')
    assert snapshot.state == 'working'
    assert ('budget_exhausted', 'portion:model_tokens 33000/32000') in [(e['kind'], e['message']) for e in manager.events(started.run_id)]


def test_run_budget_finishes_immediately_not_at_next_claim(store):
    manager = C.ContinuityManager(store, clock=lambda: NOW)
    started = manager.start_run('bound the whole run', initial_task='work', total_budget=C.RunBudget(max_model_tokens=250))
    claim = manager.claim_run(started.run_id, 'worker-a')
    snapshot = manager.record_model_tokens(claim, 300)
    assert snapshot.state == 'partial'
    assert snapshot.outcome == 'budget_exhausted:model_tokens'
    assert snapshot.budget_exhausted == 'run:model_tokens'
    assert snapshot.wake is None


def test_remaining_budget_reports_what_is_left(store):
    manager = C.ContinuityManager(store, clock=lambda: NOW)
    started = manager.start_run('size the next call', initial_task='work', portion_budget=C.PortionBudget(max_actions=3, max_model_tokens=1000), total_budget=C.RunBudget(max_actions=10, max_model_tokens=5000, max_portions=4))
    claim = manager.claim_run(started.run_id, 'worker-a')
    manager.record_model_tokens(claim, 400)
    manager.record_actions(claim, count=1)
    left = manager.remaining_budget(claim)
    assert left['portion'] == {'model_tokens': 600, 'actions': 2}
    assert left['run'] == {'model_tokens': 4600, 'actions': 9, 'portions': 3}
