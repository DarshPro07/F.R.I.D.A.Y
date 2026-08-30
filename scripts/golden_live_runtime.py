"""Deterministic offline acceptance journey for the repaired live runtime."""
from __future__ import annotations
import argparse
import ast
import asyncio
import inspect
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from livekit.agents.llm.chat_context import ChatContext, FunctionCall, FunctionCallOutput
from friday import contracts as c
from friday import continuity as C
from friday.fsjail import FileJail
from friday.policy import FULL, PolicyEngine
from friday.provider_fallback import inspect_native_history
from friday.response_render import render_speech_stream
from friday.runtime_control import DUPLICATE, RunExecutionArbiter
from friday.runtime_metrics import RuntimeTelemetry
from friday.store import Store
from friday.tools import automation_control as AC
from friday.toolsets import automations as AUTO
from friday.toolsets import files as FILES
from friday.world_monitor import build_world_monitor_url, verify_world_monitor_destination
NOW = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)


class Speech:
    def __init__(self, speech_id: str) -> None:
        self.id = speech_id
        self.interrupted = False

    def interrupt(self, *, force=False) -> None:
        self.interrupted = force


async def clean_text() -> str:
    async def source():
        yield 'See **status** at https://exa'
        yield "mple.com.\n```py\nprint('hidden')\n```\nReady."
    return ''.join([part async for part in render_speech_stream(source())])


async def automation_probe() -> dict:
    calls = []

    def fake_search(run, query: str, *, engine):
        calls.append(query)
        return run.record(c.succeeded(c.started(run.run_id, 'web.search'), output={'query': query}, verification=c.Verification(method='fixture', evidence='searched')))

    class FakeMCP:
        def __init__(self) -> None:
            self.tools = {}

        def tool(self):
            def register(function):
                self.tools[function.__name__] = function
                return function
            return register
    original_tools = AUTO.TOOLS
    AUTO.TOOLS = {'web.search': fake_search}
    mcp = FakeMCP()
    AC.register(mcp)
    try:
        created = await mcp.tools['automations_create']('runtime-probe', json.dumps({'kind': 'manual'}), json.dumps([{'id': 'search', 'tool': 'web.search', 'args': {'query': 'owned runtime fixture'}}]))
        executed = await mcp.tools['automations_run']('runtime-probe')
        history = await mcp.tools['automations_history']('runtime-probe', 10)
        deleted = await mcp.tools['automations_delete']('runtime-probe')
    finally:
        AUTO.TOOLS = original_tools
    return {'created': created['status'], 'executed': executed['status'], 'history': history['status'], 'history_count': history['output']['count'], 'deleted': deleted['status'], 'definition_absent': AUTO.store().get_automation('runtime-probe') is None, 'calls': calls}


def no_asyncio_run(module) -> bool:
    tree = ast.parse(inspect.getsource(module))
    return not any((isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and node.func.value.id == 'asyncio' and node.func.attr == 'run' for node in ast.walk(tree)))


async def journey(root: Path) -> tuple[list[tuple[str, bool, str]], dict]:
    store = Store(root / 'runtime.sqlite3')
    manager = C.ContinuityManager(store, clock=lambda: NOW)
    started = manager.start_run('open Calculator once', initial_task='open Calculator')
    claim = manager.claim_run(started.run_id, 'golden-voice')
    active = claim
    telemetry = RuntimeTelemetry(clock=lambda: 10.0)
    telemetry.set_correlation(lambda: active)
    arbiter = RunExecutionArbiter(manager, active_claim=lambda: active, telemetry=telemetry)
    first, duplicate = Speech('speech-a'), Speech('speech-b')
    arbiter.bind_speech(claim, first, source='generate_reply')
    owner_decision = arbiter.bind_speech(claim, duplicate, source='generate_reply')
    context = SimpleNamespace(speech_handle=first)
    authorization = arbiter.authorize_tool(context, 'apps_open', {'name': 'calculator'})
    arbiter.settle_tool(authorization, '{"run_id":"ACTION-GOLDEN","status":"succeeded","may_claim_completion":true,"verification":{"method":"fixture","evidence":"one dispatch"}}')
    second_authorization = arbiter.authorize_tool(context, 'apps_open', {'name': 'calculator'})
    history = ChatContext.empty()
    history.items.extend([FunctionCall(call_id='call-1', name='probe', arguments='{}'), FunctionCallOutput(call_id='call-1', name='probe', output='ok', is_error=False)])
    incomplete = inspect_native_history(history, 'google', 'gemini-test', signature_store={}, require_signatures=True)
    canonical = build_world_monitor_url()
    reordered = 'https://worldmonitor.app/dashboard?layers=conflicts,bases,hotspots,nuclear,sanctions,weather,canadaAlerts,economic,waterways,outages,military,natural&timeRange=7d&view=global&zoom=1&lon=-0.18&lat=20'
    workspace = root / 'workspace'
    artifacts = workspace / 'artifacts'
    artifacts.mkdir(parents=True)
    artifact = artifacts / 'temporary.txt'
    artifact.write_text('owned fixture', encoding='utf-8')
    FILES.reset_jail(FileJail(roots=(workspace,)))
    original_artifacts = FILES.ARTIFACTS_DIR
    FILES.ARTIFACTS_DIR = artifacts
    try:
        deleted = FILES.files_delete(c.Run.create('clean fixture', capability='files'), str(artifact), permanent=True, engine=PolicyEngine(autonomy=FULL))
    finally:
        FILES.ARTIFACTS_DIR = original_artifacts
        FILES.reset_jail(None)
    AUTO.reset_store(Store(root / 'automation.sqlite3'))
    AC._engine = PolicyEngine(autonomy=FULL)
    automation_result = await automation_probe()
    AUTO.store().close()
    AUTO.reset_store(None)
    spoken = await clean_text()
    checks = [('one generated owner', owner_decision.state == DUPLICATE and duplicate.interrupted, owner_decision.state), ('one side-effect dispatch', not second_authorization.execute, second_authorization.status), ('incomplete native history detected locally', not incomplete.complete, f"missing_signatures={incomplete.missing_signatures}"), ('canonical dashboard verifies semantically', verify_world_monitor_destination(canonical, reordered).ok, canonical), ('Friday artifact direct cleanup', deleted.status == c.SUCCEEDED and not artifact.exists(), deleted.output['result']), ('automation MCP lifecycle awaits current loop', all((automation_result[key] == c.SUCCEEDED for key in ('created', 'executed', 'history', 'deleted'))) and automation_result['history_count'] == 1 and automation_result['definition_absent'] and automation_result['calls'] == ['owned runtime fixture'] and no_asyncio_run(AC), json.dumps(automation_result, sort_keys=True)), ('speech-only rendering', 'http' not in spoken and 'print' not in spoken, spoken), ('real voice metrics remain unmeasured offline', telemetry.comparison()['baseline_status'] == 'UNMEASURED', telemetry.comparison()['baseline_status'])]
    report = telemetry.comparison()
    store.close()
    return (checks, report)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--offline', action='store_true')
    args = parser.parse_args()
    if not args.offline:
        print('Real LiveKit microphone/provider acceptance is UNMEASURED by this safe script.')
        print('Run with --offline for deterministic checks; use the live agent for the real gate.')
        return 2
    with tempfile.TemporaryDirectory(prefix='friday-live-runtime-') as temporary:
        checks, report = asyncio.run(journey(Path(temporary)))
    for name, passed, detail in checks:
        print(f"[{'PASS' if passed else 'FAIL'}] {name}: {detail}")
    print(json.dumps({'checks_passed': sum((1 for _name, passed, _detail in checks if passed)), 'checks_total': len(checks), 'metrics': report}, indent=2))
    return 0 if all((passed for _name, passed, _detail in checks)) else 1
if __name__ == '__main__':
    raise SystemExit(main())