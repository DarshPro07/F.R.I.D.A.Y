"""Phase 1F manual-promotion gates F1-F5, driven through the production
Friday path: real FridayAgent, real LLM, real MCP over SSE (server.py),
real Hermes gateway (friday profile), real WorkRun log.

Each gate is a separate function; state (timings, ids) is printed as the
promotion record. DO NOTHING between send and result - no Continue, no
status queries.
"""
import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent if '__file__' in dir() else Path('E:/friday-tony-stark-demo-main')
ROOT = Path('E:/friday-tony-stark-demo-main')
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
from dotenv import load_dotenv
load_dotenv()
os.environ.setdefault('HERMES_PYTHON', 'D:\\hermes\\hermes-agent\\venv\\Scripts\\python.exe')
os.environ.setdefault('HERMES_DIR', 'D:\\hermes\\hermes-agent')
import agent_friday
from friday import providers
from friday import hermes_bridge as hb


def now():
    return time.strftime('%H:%M:%S')


async def make_session():
    from livekit.agents.voice import AgentSession
    config = agent_friday.session_config()
    session = AgentSession(turn_handling=config['turn_handling'])
    agent = agent_friday.FridayAgent(stt=providers.build_stt(config['stt_provider']), llm=providers.build_resilient_llm(config['llm_backend'], config['llm_role']), tts=providers.build_tts(config['tts_provider'], speed=config['tts_speed']))
    await session.start(agent)
    return session


def turn_events(result):
    calls, outputs, messages = [], [], []
    delegations = 0
    for event in result.events:
        kind = type(event).__name__
        if kind == 'FunctionCallEvent':
            name = event.item.name
            calls.append(name)
            args = event.item.arguments
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            if name == 'hermes_delegate' or name == 'use_capability' and isinstance(args, dict) and args.get('capability') == 'hermes_delegate':
                delegations += 1
            continue
        if kind == 'FunctionCallOutputEvent':
            outputs.append(str(event.item.output))
            continue
        if kind == 'ChatMessageEvent':
            messages.append((event.item.role, event.item.text_content or ''))
    reply = '\n'.join((t for r, t in messages if r == 'assistant'))
    return (calls, outputs, reply, delegations)


async def wait_terminal(log, run_id, budget=900):
    deadline = time.time() + budget
    while time.time() < deadline:
        rec = log.get(run_id) or {}
        if rec.get('status') in ('COMPLETE', 'PARTIAL', 'FAILED'):
            return rec
        await asyncio.sleep(5)
    return log.get(run_id) or {}


class SaySpy:
    def __init__(self):
        self.spoken = []

    async def say(self, text, **kw):
        self.spoken.append(text)


async def f1(session, log):
    print(f"\n=== F1 normal sanity  [{now()}]")
    before = len(log.recent(50))
    t0 = time.time()
    result = await session.run(user_input='Friday, tell me in one sentence what you are currently able to do. Do not delegate this to Hermes.')
    calls, outputs, reply, dels = turn_events(result)
    after = len(log.recent(50))
    ok = dels == 0 and after == before and bool(reply.strip())
    print(f"reply: {reply[:220]}")
    print(f"tools: {calls}  delegations: {dels}  new workruns: {after - before}  {time.time() - t0:.0f}s")
    print(f"F1: {'PASS' if ok else 'FAIL'}")
    return ok


async def f2(session, log):
    print(f"\n=== F2 delegation golden  [{now()}]")
    t_send = time.time()
    result = await session.run(user_input='Friday, have Hermes inspect this project and tell me the three most important architectural problems. Rank them by impact and support every finding with actual file and line evidence. Do not modify anything.')
    calls, outputs, reply, dels = turn_events(result)
    t_ack = time.time()
    print(f"ack ({t_ack - t_send:.0f}s after send): {reply[:200]}")
    runs = [r for r in log.recent(5) if r.get('started_at', 0) > t_send - 5]
    run = runs[0] if runs else None
    if not run:
        print('F2: FAIL - no WorkRun created')
        return (False, {})
    print(f"WorkRun: {run['work_run_id']}  session: {run['hermes_session_id']}")
    rec = await wait_terminal(log, run['work_run_id'])
    t_done = time.time()
    print(f"terminal: {rec.get('status')} at +{t_done - t_send:.0f}s")
    spy = SaySpy()
    delivered = await agent_friday.drain_hermes_deliveries(spy, log)
    text = '\n'.join(spy.spoken)
    again = await agent_friday.drain_hermes_deliveries(SaySpy(), log)
    usage = {}
    try:
        usage = json.loads(rec.get('usage_json') or '{}').get('usage', {})
    except Exception:
        pass
    findings = len(re.findall('(?m)^\\s*(?:#{1,4}\\s*)?(?:\\*\\*)?\\d+[\\.\\)]', text))
    file_line = len(re.findall('\\.\\w{1,4}:\\d+|`[^`]+:\\d+', text))
    ok = dels == 1 and rec.get('status') == 'COMPLETE' and delivered == 1 and again == 0 and findings >= 3 and file_line >= 3
    print(f"delegations={dels} delivered={delivered} second_drain={again}")
    print(f"findings={findings} file:line refs={file_line}")
    print(f"model calls={usage.get('calls')} aggregate={usage.get('prompt')}")
    print('------------------------------------------------------------')
    print(text[:900])
    print('------------------------------------------------------------')
    print(f"F2: {'PASS' if ok else 'FAIL'}")
    return (ok, {'run': run['work_run_id'], 'usage': usage, 'ack_s': round(t_ack - t_send), 'total_s': round(t_done - t_send)})


async def f3(session):
    print(f"\n=== F3 ack-before-action  [{now()}]")
    result = await session.run(user_input='Friday, create a temporary text file called friday_ack_test.txt in the project root containing: ACK TEST')
    order = []
    for event in result.events:
        kind = type(event).__name__
        if kind == 'ChatMessageEvent' and event.item.role == 'assistant':
            order.append(('say', (event.item.text_content or '')[:60]))
            continue
        if kind == 'FunctionCallEvent':
            order.append(('tool', event.item.name))
    first_say = next((i for i, (k, _) in enumerate(order) if k == 'say'), None)
    first_tool = next((i for i, (k, _) in enumerate(order) if k == 'tool'), None)
    made = (Path(ROOT) / 'friday_ack_test.txt').exists()
    ack_first = first_say is not None and (first_tool is None or first_say < first_tool)
    print(f"order: {order[:6]}")
    print(f"file created: {made}  ack before action: {ack_first}")
    try:
        (Path(ROOT) / 'friday_ack_test.txt').unlink(missing_ok=True)
    except OSError:
        pass
    ok = made and ack_first
    print(f"F3: {'PASS' if ok else 'FAIL'}")
    return ok


async def f4(session, log):
    print(f"\n=== F4 steering  [{now()}]")
    t0 = time.time()
    result = await session.run(user_input='Friday, have Hermes inspect the runtime architecture and identify the three largest reliability risks. Read only.')
    _, _, reply, dels = turn_events(result)
    runs = [r for r in log.recent(5) if r.get('started_at', 0) > t0 - 5]
    if not runs:
        print('F4: FAIL - no run')
        return False
    run_id = runs[0]['work_run_id']
    print(f"run: {run_id}  status: {runs[0]['status']}")
    steer_result = await session.run(user_input='Focus only on the Friday-to-Hermes runtime and delivery path now. Ignore unrelated UI architecture.')
    _, _, steer_reply, steer_dels = turn_events(steer_result)
    print(f"steer reply: {steer_reply[:180]}")
    runs_after = [r for r in log.recent(8) if r.get('started_at', 0) > t0 - 5]
    rec = await wait_terminal(log, run_id)
    text = rec.get('result') or ''
    scoped = any((w in text.lower() for w in ('delivery', 'bridge', 'workrun', 'gateway', 'delegat')))
    ok = dels == 1 and len(runs_after) == 1 and steer_dels == 0 and rec.get('status') in ('COMPLETE', 'PARTIAL') and scoped
    print(f"runs after steer: {len(runs_after)} (must be 1)  new delegations from steer turn: {steer_dels}")
    print(f"final: {rec.get('status')}  scope-reflects-steer: {scoped}")
    print(f"F4: {'PASS' if ok else 'FAIL'}")
    await agent_friday.drain_hermes_deliveries(SaySpy(), log)
    return ok


async def f5(session, log):
    print(f"\n=== F5 stop  [{now()}]")
    t0 = time.time()
    await session.run(user_input='Friday, have Hermes perform a broad read-only architecture review of this project.')
    runs = [r for r in log.recent(5) if r.get('started_at', 0) > t0 - 5]
    if not runs:
        print('F5: FAIL - no run')
        return False
    run_id = runs[0]['work_run_id']
    await asyncio.sleep(8)
    t_stop = time.time()
    stop_result = await session.run(user_input='Stop.')
    _, _, stop_reply, _ = turn_events(stop_result)
    t_ack = time.time()
    print(f"stop ack ({t_ack - t_stop:.1f}s): {stop_reply[:140]}")
    await asyncio.sleep(10)
    rec = log.get(run_id) or {}
    state = rec.get('status')
    stopped = state in ('PARTIAL', 'FAILED', 'CANCELLING')
    print(f"run state after stop: {state}")
    spy = SaySpy()
    delivered = await agent_friday.drain_hermes_deliveries(spy, log)
    ghost = any(('architecture review' in s.lower() and 'finished' in s.lower() for s in spy.spoken))
    ok = bool(stop_reply.strip()) and stopped and not ghost
    print(f"stop latency: {t_ack - t_stop:.1f}s  pretend-completions: {ghost}")
    print(f"F5: {'PASS' if ok else 'FAIL'}")
    return ok


async def main():
    log = hb.WorkRunLog()
    log.sweep_undelivered(max_age_s=0)
    for stale in log.pending_deliveries():
        if log.claim_delivery(stale['delivery_id']):
            log.mark_delivered(stale['delivery_id'], via='gate-pre-clear')
    session = await make_session()
    results = {}
    try:
        results['F1'] = await f1(session, log)
        f2_ok, f2_data = await f2(session, log)
        results['F2'] = f2_ok
        results['F3'] = await f3(session)
        results['F4'] = await f4(session, log)
        results['F5'] = await f5(session, log)
    finally:
        await session.aclose()
    print('\n============================================================')
    for gate, ok in results.items():
        print(f"{gate}: {'PASS' if ok else 'FAIL'}")
    if 'F2' in results and f2_data:
        print(f"F2 data: {f2_data}")
    return 0 if all(results.values()) else 1
if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))