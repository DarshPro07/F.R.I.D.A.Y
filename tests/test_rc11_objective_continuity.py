"""RC1.1 P0 regressions from the REAL failed production objective.

Evidence row RUN-7361928cb223:
- files_read got a malformed invented path and failed STRUCTURAL;
- hermes_delegate was adapter-only inside the durable runtime and failed
  NOT_CONFIGURED;
- dependent hermes_status skipped;
- run settled PARTIAL, so the user's later `continue` was a new model
  turn and manual_continue_count stayed falsely 0.

These tests are RED before the repair. They assert product behavior,
not implementation details.
"""
from __future__ import annotations
import asyncio
from datetime import datetime, timedelta, timezone
import pytest
import agent_friday
from friday import capabilities, ownership
from friday.continuous import ContinuousTaskExecutor
from friday.objectives import CompileError, compile_objective
from friday.store import Store
CONTROL_CAPABILITIES = {'objective_cancel', 'objective_history', 'objective_resume', 'objective_start', 'objective_pause', 'objective_status', 'objective_list'}


def test_planner_never_places_objective_control_inside_its_own_graph():
    """A project request repeatedly compiled objective_start as task t1,
    then failed because the planner omitted its `objective` argument."""
    text = 'I want to build a small desktop game where I control a combat drone. Research whether Godot is the right choice, then build it.'
    from friday import planner_model
    plan = planner_model.plan_objective(text)
    planned = {goal.capability for goal in plan.goals if goal.capability}
    assert not planned & CONTROL_CAPABILITIES, f"control-plane capabilities leaked into work graph: {planned & CONTROL_CAPABILITIES}"


def test_compiler_refuses_control_plane_tasks_even_if_planner_regresses():
    store = Store(':memory:')
    with pytest.raises(CompileError, match='control-plane'):
        compile_objective(store, request='recursive', objective_summary='recursive', tasks=[{'capability': 'objective_start', 'arguments': {}}], manifest=capabilities.as_dicts())


@pytest.mark.asyncio
async def test_submit_first_worker_is_reconciled_without_duplicate_delegation():
    """A working Hermes result is a WAIT, not success. The next wake asks
    status using the same WorkRun id; it must never call delegate twice."""
    store = Store(':memory:')
    calls = []
    statuses = iter(({'status': 'working', 'work_run_id': 'hermes-live1'}, {'status': 'complete', 'work_run_id': 'hermes-live1', 'result': 'finished evidence'}))

    async def port(capability, arguments):
        calls.append((capability, dict(arguments)))
        if capability == 'hermes_delegate':
            return next(statuses)
        if capability == 'hermes_status':
            return next(statuses)
        raise AssertionError(capability)
    run = compile_objective(store, request='inspect', objective_summary='inspect', tasks=[{'capability': 'hermes_delegate', 'arguments': {'goal': 'inspect safely'}}], manifest=capabilities.as_dicts())
    executor = ContinuousTaskExecutor(store, port, executor_id='rc11')
    executor.portion_budget = 1
    try:
        await executor.start(run['run_id'])
        deadline = asyncio.get_running_loop().time() + 3
        while asyncio.get_running_loop().time() < deadline:
            if store.objective_run(run['run_id'])['status'] == 'COMPLETED':
                break
            await asyncio.sleep(0.05)
        final = store.objective_run(run['run_id'])
        task = store.objective_tasks(run['run_id'])[0]
        assert final['status'] == 'COMPLETED', final
        assert task['attempts'] == 1, 'polling must not count as a re-dispatch'
        assert [c[0] for c in calls] == ['hermes_delegate', 'hermes_status']
        assert calls[1][1]['work_run_id'] == 'hermes-live1'
    finally:
        executor.stop()


def test_active_objective_claim_does_not_expire_by_wall_clock(monkeypatch):
    """Long objectives lost duplicate protection after CLAIM_SECONDS even
    though their durable run was still RUNNING."""
    store = Store(':memory:')
    run = compile_objective(store, request='long', objective_summary='long', tasks=[{'capability': 'files_create', 'arguments': {'path': 'x', 'content': 'x'}}], manifest=capabilities.as_dicts())
    old = (datetime.now(timezone.utc) - timedelta(hours=4)).isoformat()
    store._conn.execute('UPDATE objective_runs SET created_at = ?, updated_at = ? WHERE run_id = ?', (old, old, run['run_id']))
    store._conn.commit()
    assert ownership.claimed_by('files_create', arguments={'path': 'x', 'content': 'x'}, db=store) == run['run_id']
    assert ownership.claimed_by('files_create', arguments={'path': 'other', 'content': 'x'}, db=store) is None


def test_conversational_continue_after_terminal_partial_is_counted():
    """The old metric counted only objective_resume. The real regression was
    a plain `continue` after the objective had prematurely gone PARTIAL."""
    store = Store(':memory:')
    run = compile_objective(store, request='failed objective', objective_summary='failed objective', tasks=[{'capability': 'objective.unmapped', 'arguments': {'clause': 'something impossible'}}], manifest=capabilities.as_dicts())
    store.finish_objective_run(run['run_id'], status='PARTIAL', summary={'failed': 1})
    counted = agent_friday.record_manual_continue_if_continuity_failed('continue', db=store)
    assert counted is True
    assert store.objective_run(run['run_id'])['manual_continue_count'] == 1


@pytest.mark.asyncio
async def test_live_agent_objective_engine_uses_its_connected_mcp_port(monkeypatch):
    """Production used objective_cli.build_dispatch (non-MCP), making
    hermes_delegate NOT_CONFIGURED inside objectives despite working in chat."""
    captured = {}

    class FakeExecutor:
        def __init__(self, store, call_capability,
                     executor_id, health_probe=None, health_recover=None):
            self.executor_id = executor_id
            captured.update(store=store, port=call_capability, executor_id=executor_id, health_probe=health_probe, health_recover=health_recover)
    fake_store = object()
    monkeypatch.setattr(agent_friday, 'ContinuousTaskExecutor', FakeExecutor)
    monkeypatch.setattr(agent_friday.objective_cli, '_db', lambda: fake_store)

    async def local_dispatch(capability, arguments):
        return {'status': 'NOT_CONFIGURED', 'via': 'local'}
    monkeypatch.setattr(agent_friday.objective_cli, 'build_dispatch', lambda: local_dispatch)

    class FakeRouter:
        def invocable(self, name):
            return object() if name == 'hermes_delegate' else None

    class FakeAgent:
        _objective_engine = None
        _router = FakeRouter()

        async def _call_capability(self, capability, arguments):
            return {'capability': capability, 'via': 'mcp'}
    agent = FakeAgent()
    agent_friday.FridayAgent.start_objective_engine(agent)
    assert captured['store'] is fake_store
    port = captured['port']
    assert (await port('hermes_delegate', {}))['via'] == 'mcp'
    assert (await port('system_battery', {}))['via'] == 'local'
    assert captured['health_probe'] is not None
    assert captured['health_recover'] is not None


@pytest.mark.asyncio
async def test_objective_completion_creates_exactly_once_delivery():
    store = Store(':memory:')

    async def port(capability, arguments):
        return {'ok': True, 'step': capability}
    run = compile_objective(store, request='five safe steps', objective_summary='five safe steps', tasks=[{'capability': 'system_battery', 'arguments': {}}], manifest=capabilities.as_dicts())
    executor = ContinuousTaskExecutor(store, port, executor_id='deliver')
    try:
        await executor.start(run['run_id'])
        pending = store.pending_objective_deliveries()
        assert len(pending) == 1
        delivery = pending[0]
        assert delivery['run_id'] == run['run_id']
        assert 'finished' in delivery['message'].lower()
        assert store.claim_objective_delivery(delivery['delivery_id'])
        assert not store.claim_objective_delivery(delivery['delivery_id'])
        store.mark_objective_delivered(delivery['delivery_id'], via='test')
        assert store.pending_objective_deliveries() == []
    finally:
        executor.stop()


def test_continuity_state_projects_required_parent_cursor_fields():
    from friday.objectives import continuity_state
    store = Store(':memory:')
    run = compile_objective(store, request='inspect then report', objective_summary='inspect then report', tasks=[{'capability': 'hermes_delegate', 'arguments': {'goal': 'inspect'}}, {'capability': 'system_battery', 'arguments': {}, 'dependencies': ['t1']}], manifest=capabilities.as_dicts())
    first = store.objective_tasks(run['run_id'])[0]
    store.update_objective_task(first['task_id'], status='WAITING', attempts=1, result={'work_run_id': 'hermes-cursor1'})
    state = continuity_state(store, run['run_id'])
    required = {'current_step', 'state', 'objective', 'completed_workrun_ids', 'resume_policy', 'updated_at', 'objective_id', 'last_verified_step', 'blocker_class', 'stopped_workrun_ids', 'next_action', 'current_phase', 'retry_count', 'blocker', 'active_workrun_ids'}
    assert required <= set(state)
    assert state['active_workrun_ids'] == ['hermes-cursor1']
    assert state['current_step'] == first['task_id']
    assert state['state'] == 'WAITING_WORKER'


class FakeSession:
    """Enough AgentSession to prove delivery without a model call."""

    def __init__(self):
        self.said = []
        self.generated = []

    async def say(self, text, allow_interruptions=True):
        self.said.append(text)

    async def generate_reply(self, *a, **kw):
        self.generated.append('generate_reply')


@pytest.mark.asyncio
async def test_c1_finished_objective_reaches_the_user_with_no_model_call(monkeypatch, tmp_path):
    """C1: five safe steps, the user says nothing, the result arrives.

    The production entrypoint drains objective completions the same way it
    drains Hermes ones. This runs the module-level drain, not a copy.
    """
    db = tmp_path / 'c1.db'
    store = Store(str(db))
    monkeypatch.setattr(agent_friday.objective_cli, '_db', lambda: Store(str(db)))

    async def port(capability, arguments):
        return {'ok': True, 'capability': capability}
    run = compile_objective(store, request='run the five step preflight', objective_summary='run the five step preflight', tasks=[{'capability': name, 'arguments': {}} for name in ('system_get_info', 'system_battery', 'system_disks', 'get_current_time', 'system_network')], manifest=capabilities.as_dicts())
    executor = ContinuousTaskExecutor(store, port, executor_id='c1')
    try:
        await executor.start(run['run_id'])
    finally:
        executor.stop()
    assert store.objective_run(run['run_id'])['status'] == 'COMPLETED'
    session = FakeSession()
    delivered = await agent_friday.drain_objective_deliveries(session)
    assert delivered == 1, 'the finished objective never reached the user'
    assert session.generated == [], 'delivery must not cost a model call'
    assert session.said, 'nothing was said to the user'
    assert store.pending_objective_deliveries() == []
    assert await agent_friday.drain_objective_deliveries(session) == 0
    assert len(session.said) == 1
    assert store.objective_run(run['run_id'])['manual_continue_count'] == 0


@pytest.mark.asyncio
async def test_c3_a_recoverable_connectivity_fault_does_not_end_the_run(tmp_path):
    """C3: a stale bridge must be recovered from, not fatal.

    In the real failed row, hermes_delegate came back NOT_CONFIGURED - a kind
    outside RETRYABLE_KINDS - so the objective marked it permanently failed,
    SKIPPED everything downstream, and settled PARTIAL. That is what turned a
    reconnectable internal fault into "should I continue?".

    A connectivity fault is recoverable by definition: it is a statement
    about a link, not about the work.
    """
    store = Store(str(tmp_path / 'c3.db'))
    attempts = []

    async def port(capability, arguments):
        if capability == 'hermes_delegate':
            attempts.append(capability)
            if len(attempts) == 1:
                return {'status': 'not_configured', 'error': 'hermes bridge is not ready'}
            return {'ok': True, 'work': 'done after reconnect'}
        return {'ok': True, 'capability': capability}
    run = compile_objective(store, request='delegate then report', objective_summary='delegate then report', tasks=[{'capability': 'hermes_delegate', 'arguments': {'goal': 'probe'}}, {'capability': 'system_battery', 'arguments': {}, 'dependencies': ['t1']}], manifest=capabilities.as_dicts())
    executor = ContinuousTaskExecutor(store, port, executor_id='c3')
    try:
        await executor.start(run['run_id'])
        deadline = asyncio.get_event_loop().time() + 20.0
        while asyncio.get_event_loop().time() < deadline:
            row = store.objective_run(run['run_id'])
            if row['status'] in ('COMPLETED', 'PARTIAL', 'FAILED', 'CANCELLED'):
                break
            await asyncio.sleep(0.1)
    finally:
        executor.stop()
    final = store.objective_run(run['run_id'])
    tasks = {t['capability']: t for t in store.objective_tasks(run['run_id'])}
    assert len(attempts) >= 2, 'the bridge fault was never retried'
    assert final['status'] == 'COMPLETED', 'a reconnectable bridge fault ended the objective'
    assert tasks['system_battery']['status'] != 'SKIPPED', 'downstream work was skipped for a recoverable link fault'