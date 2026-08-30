"""V3 Phase 1A/1B — hermes_health.Report wired into executor recovery.

The RC1.1 handoff left the health module decided-but-unconsulted: a
CONNECTIVITY refusal was retried on a blind timer without ever asking WHICH
layer failed. These gates prove the executor consults the report, records
the diagnosis durably, drives the layer-appropriate repair, and refuses to
restart anything while live work exists.
"""
from __future__ import annotations
import asyncio
import pytest
from friday import capabilities, hermes_health as H
from friday.continuous import ContinuousTaskExecutor
from friday.objectives import compile_objective
from friday.store import Store


def _signals(**down):
    signals = {name: True for name in H.LAYERS}
    for name in down:
        signals[name] = False
    return signals


async def _drive(store, run_id, executor, seconds=20.0):
    try:
        await executor.start(run_id)
        deadline = asyncio.get_event_loop().time() + seconds
        while asyncio.get_event_loop().time() < deadline:
            row = store.objective_run(run_id)
            if row['status'] in ('COMPLETED', 'PARTIAL', 'FAILED', 'CANCELLED'):
                return row
            await asyncio.sleep(0.1)
        return store.objective_run(run_id)
    finally:
        executor.stop()


@pytest.mark.asyncio
async def test_connectivity_failure_consults_the_report_and_repairs_the_layer(tmp_path):
    """The live shape: stale bridge, healthy gateway. The executor must ask
    the report which layer failed, run THAT repair, and continue."""
    store = Store(str(tmp_path / 'wire.db'))
    calls = []
    repairs = []
    bridge_ok = {'up': False}

    async def port(capability, arguments):
        if capability == 'hermes_delegate':
            calls.append(capability)
            if not bridge_ok['up']:
                return {'status': 'not_configured', 'error': 'hermes bridge is not ready'}
            return {'ok': True, 'answer': 'delegated fine'}
        return {'ok': True}

    def probe():
        return H.Report(_signals(**{} if bridge_ok['up'] else {'hermes_bridge_ready': False, 'active_workrun_reachable': False}))

    async def recover(report):
        repairs.append(report.recovery)
        if report.recovery is H.Recovery.RECONNECT_BRIDGE:
            bridge_ok['up'] = True
            return True
        return False
    run = compile_objective(store, request='delegate through the bridge', objective_summary='delegate through the bridge', tasks=[{'capability': 'hermes_delegate', 'arguments': {'goal': 'probe'}}], manifest=capabilities.as_dicts())
    executor = ContinuousTaskExecutor(store, port, executor_id='wire', health_probe=probe, health_recover=recover)
    final = await _drive(store, run['run_id'], executor)
    assert repairs == [H.Recovery.RECONNECT_BRIDGE], 'the layer-specific repair was not driven from the report'
    assert final['status'] == 'COMPLETED'
    assert len(calls) >= 2, 'work never continued after the repair'
    events = store.objective_events(run['run_id'])
    diagnosed = [e for e in events if e['event'] == 'connectivity.diagnosed']
    assert diagnosed, 'the diagnosis was not durably recorded'
    detail = diagnosed[0]['detail']
    assert detail['failed_layer'] == 'hermes_bridge_ready'
    assert detail['recovery'] == 'reconnect_bridge'


@pytest.mark.asyncio
async def test_dead_gateway_with_live_work_reconciles_before_restart(tmp_path):
    """C4 precondition: the report must be given the live WorkRun ids, so a
    dead-looking gateway yields RECONCILE_THEN_RESTART, never a blind
    restart that would orphan the child."""
    store = Store(str(tmp_path / 'dead.db'))
    seen_reports = []

    async def port(capability, arguments):
        if capability == 'hermes_delegate':
            return {'status': 'working', 'work_run_id': 'hermes-live-1'}
        if capability == 'hermes_status':
            return {'status': 'not_configured', 'error': 'gateway connection refused'}
        return {'ok': True}

    def probe(active_workruns=()):
        report = H.Report(_signals(gateway_process_alive=False, gateway_http_reachable=False, friday_profile_registered=False, friday_to_gateway_connected=False, mcp_server_alive=False, mcp_sse_connected=False, hermes_bridge_ready=False, active_workrun_reachable=False), active_workruns=tuple(active_workruns))
        seen_reports.append(report)
        return report

    async def recover(report):
        return False
    run = compile_objective(store, request='delegate', objective_summary='delegate', tasks=[{'capability': 'hermes_delegate', 'arguments': {'goal': 'long job'}}], manifest=capabilities.as_dicts())
    executor = ContinuousTaskExecutor(store, port, executor_id='dead', health_probe=probe, health_recover=recover)
    try:
        await executor.start(run['run_id'])
        await asyncio.sleep(2.0)
    finally:
        executor.stop()
    assert seen_reports, 'the report was never consulted'
    assert any((r.active_workruns == ('hermes-live-1',) for r in seen_reports)), 'the live WorkRun was not handed to the report, so a blind restart was possible'
    assert all((r.recovery is H.Recovery.RECONCILE_THEN_RESTART for r in seen_reports if r.active_workruns)), 'a dead gateway with live work must reconcile before restart'


@pytest.mark.asyncio
async def test_failed_recovery_still_retries_and_never_asks_the_user(tmp_path):
    """Recovery that does not work degrades to the bounded retry path -
    the run fails honestly after max attempts; no user question shape."""
    store = Store(str(tmp_path / 'hard.db'))

    async def port(capability, arguments):
        return {'status': 'not_configured', 'error': 'hermes bridge is not ready'}

    def probe():
        return H.Report(_signals(hermes_bridge_ready=False, active_workrun_reachable=False))

    async def recover(report):
        return False
    run = compile_objective(store, request='delegate', objective_summary='delegate', tasks=[{'capability': 'hermes_delegate', 'arguments': {'goal': 'probe'}, 'max_attempts': 2}], manifest=capabilities.as_dicts())
    executor = ContinuousTaskExecutor(store, port, executor_id='hard', health_probe=probe, health_recover=recover)
    final = await _drive(store, run['run_id'], executor)
    assert final['status'] in ('PARTIAL', 'FAILED')
    task = store.objective_tasks(run['run_id'])[0]
    assert task['status'] == 'FAILED'
    assert 'hermes_bridge_ready' in (task.get('evidence') or '')
    assert '?' not in (task.get('evidence') or '')


@pytest.mark.asyncio
async def test_executor_without_probe_keeps_the_plain_retry_contract(tmp_path):
    """No probe configured (tests, CLI) - the CONNECTIVITY retry behaves
    exactly as before. The wiring must be additive."""
    store = Store(str(tmp_path / 'plain.db'))
    attempts = []

    async def port(capability, arguments):
        attempts.append(1)
        if len(attempts) == 1:
            return {'status': 'not_configured', 'error': 'hermes bridge is not ready'}
        return {'ok': True}
    run = compile_objective(store, request='delegate', objective_summary='delegate', tasks=[{'capability': 'hermes_delegate', 'arguments': {'goal': 'probe'}}], manifest=capabilities.as_dicts())
    executor = ContinuousTaskExecutor(store, port, executor_id='plain')
    final = await _drive(store, run['run_id'], executor)
    assert final['status'] == 'COMPLETED'
    assert len(attempts) == 2