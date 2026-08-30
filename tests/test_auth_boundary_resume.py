"""Auth boundary park-and-resume — the connector repair is a recoverable
sub-objective of the parent run.

Boss's rule: after the user completes auth, Friday resumes the original
task automatically. The user must never repeat the request that hit the
auth wall. Mechanically: a PROVIDER_AUTH (USER_REQUIRED with auth wording)
failure parks the RUN as WAITING_PERMISSION with the failed task kept
open, and `resume_after_auth` - the connector_verify success hook - puts
the task back on the scheduler.
"""
from __future__ import annotations
import asyncio
import time
import pytest
from friday import capabilities
from friday import objectives as O
from friday.continuous import ContinuousTaskExecutor
from friday.objectives import compile_objective
from friday.store import Store
AUTH_ERROR = "agent init failed: No Anthropic credentials found. authenticate with 'claude /login'."


async def _drive_until(store, run_id, statuses, seconds=20.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        row = store.objective_run(run_id)
        if row['status'] in statuses:
            return row
        await asyncio.sleep(0.1)
    return store.objective_run(run_id)


@pytest.mark.asyncio
async def test_provider_auth_parks_the_run_instead_of_failing_it(tmp_path):
    store = Store(str(tmp_path / 'park.db'))
    authed = {'ok': False}
    calls = []

    async def port(capability, arguments):
        if capability == 'hermes_delegate':
            calls.append(capability)
            if not authed['ok']:
                return {'status': 'FAILED', 'error': AUTH_ERROR}
            return {'status': 'COMPLETE', 'result': 'one line, boss'}
        return {'ok': True}
    run = compile_objective(store, request='delegate work that needs claude', objective_summary='delegate work that needs claude', tasks=[{'capability': 'hermes_delegate', 'arguments': {'goal': 'answer'}}], manifest=capabilities.as_dicts())
    executor = ContinuousTaskExecutor(store, port, executor_id='park')
    try:
        await executor.start(run['run_id'])
        parked = await _drive_until(store, run['run_id'], (O.RUN_WAITING_PERMISSION, 'PARTIAL', 'FAILED', 'COMPLETED'))
        assert parked['status'] == O.RUN_WAITING_PERMISSION, parked['status']
        task = store.objective_tasks(run['run_id'])[0]
        assert task['status'] == O.TaskStatus.WAITING
        assert task.get('failure_kind') == O.FailureKind.USER_REQUIRED
        assert len(calls) == 1, 'an auth wall must not be retried blindly'
        assert len(store.pending_objective_deliveries()) == 1
        authed['ok'] = True
        from friday.continuous import resume_after_auth
        resumed = resume_after_auth(store, reason='anthropic verified')
        assert run['run_id'] in resumed
        final = await _drive_until(store, run['run_id'], ('COMPLETED', 'PARTIAL', 'FAILED'), seconds=25.0)
        assert final['status'] == 'COMPLETED', final['status']
        assert len(calls) == 2, 'the original task resumed automatically'
        assert int(final.get('manual_continue_count') or 0) == 0
    finally:
        executor.stop()


@pytest.mark.asyncio
async def test_genuine_user_cancellation_still_fails_terminally(tmp_path):
    """Only PROVIDER-AUTH parks. A user's own cancel stays terminal."""
    store = Store(str(tmp_path / 'cancel.db'))

    async def port(capability, arguments):
        return {'status': 'cancelled', 'error': 'the boss declined the confirmation'}
    run = compile_objective(store, request='do a thing', objective_summary='do a thing', tasks=[{'capability': 'system_get_info', 'arguments': {}}], manifest=capabilities.as_dicts())
    executor = ContinuousTaskExecutor(store, port, executor_id='cancel')
    try:
        await executor.start(run['run_id'])
        final = await _drive_until(store, run['run_id'], ('PARTIAL', 'FAILED', 'COMPLETED'))
        assert final['status'] in ('PARTIAL', 'FAILED')
    finally:
        executor.stop()