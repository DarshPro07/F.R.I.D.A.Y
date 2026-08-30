from __future__ import annotations
import asyncio
import json
from mcp.server.fastmcp import FastMCP
from friday import continuity as C
from friday.store import Store
from friday.tools import run_control


def call(name: str, arguments: dict | None = None):
    server = FastMCP(name='test')
    run_control.register(server)
    result = asyncio.run(server.call_tool(name, arguments or {}))
    if isinstance(result, tuple) and len(result) == 2 and isinstance(result[1], dict):
        return result[1]
    if isinstance(result, list) and len(result) == 1 and hasattr(result[0], 'text'):
        return json.loads(result[0].text)
    return result


def test_status_reads_durable_state_without_chat_history(tmp_path):
    store = Store(tmp_path / 'runs.sqlite3')
    manager = C.ContinuityManager(store)
    started = manager.start_run('audit alpha', initial_task='inspect alpha')
    run_control.reset_store(store)
    result = call('run_status', {'run_id': started.run_id})
    assert result['run_id'] == started.run_id
    assert result['objective'] == 'audit alpha'
    assert result['next_wake']['kind'] == C.IMMEDIATE
    assert result['current_task']['description'] == 'inspect alpha'
    store.close()


def test_mutation_refuses_to_guess_between_multiple_runs(tmp_path):
    store = Store(tmp_path / 'runs.sqlite3')
    manager = C.ContinuityManager(store)
    manager.start_run('audit alpha', initial_task='inspect alpha')
    manager.start_run('audit beta', initial_task='inspect beta')
    run_control.reset_store(store)
    result = call('run_pause')
    assert result['status'] == 'ambiguous'
    assert result['may_claim_completion'] is False
    assert len(result['candidates']) == 2
    store.close()


def test_exact_run_control_never_changes_a_different_run(tmp_path):
    store = Store(tmp_path / 'runs.sqlite3')
    manager = C.ContinuityManager(store)
    alpha = manager.start_run('audit alpha', initial_task='inspect alpha')
    beta = manager.start_run('audit beta', initial_task='inspect beta')
    run_control.reset_store(store)
    paused = call('run_pause', {'run_id': alpha.run_id})
    assert paused['run_id'] == alpha.run_id
    assert paused['state'] == 'paused'
    assert manager.status(beta.run_id).state == 'working'
    store.close()