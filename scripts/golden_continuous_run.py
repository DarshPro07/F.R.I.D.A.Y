"""Safe deterministic journey: one objective continues without another prompt."""
from __future__ import annotations
import asyncio
import json
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from friday import continuity as C
from friday.continuity_livekit import LiveKitContinuity
from friday.store import Store


class Session:
    def __init__(self) -> None:
        self.handlers = {}
        self.generated = []
        self.said = []

    def on(self, name, callback) -> None:
        self.handlers.setdefault(name, []).append(callback)

    def emit(self, name, event) -> None:
        for callback in self.handlers.get(name, []):
            callback(event)

    def generate_reply(self, *, user_input):
        self.generated.append(user_input)
        return Speech()

    def say(self, text):
        self.said.append(text)
        return Speech()


class Speech:
    _counter = 0

    def __init__(self) -> None:
        # runtime_control.bind_speech requires a non-empty id for a
        # generate_reply speech (a side effect must bind to an identifiable
        # utterance). A real LiveKit SpeechHandle has one; the stub must too,
        # or the ownership guard rejects it. This skew is why the journey
        # stopped before its checks.
        Speech._counter += 1
        self.id = f"speech-{Speech._counter}"
        self.callbacks = []

    def add_done_callback(self, callback) -> None:
        self.callbacks.append(callback)

    def finish(self) -> None:
        for callback in list(self.callbacks):
            callback(self)


@dataclass
class Call:
    name: str


@dataclass
class Output:
    output: str


class Tools:
    def __init__(self, *run_ids: str) -> None:
        self.pairs = [(Call(f"owned_tool_{index}"), Output(json.dumps({'run_id': run_id, 'status': 'succeeded', 'may_claim_completion': True, 'verification': {'method': 'test_owned_fixture', 'evidence': f"{run_id} settled in this journey"}}))) for index, run_id in enumerate(run_ids)]

    def zipped(self):
        return self.pairs


async def journey(db: Path) -> list[tuple[str, bool, str]]:
    now = [datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)]
    user_messages = ['audit all test-owned areas, recover, and verify']
    first_store = Store(db)
    first_manager = C.ContinuityManager(first_store, clock=lambda: now[0])
    first_session = Session()
    first_adapter = LiveKitContinuity(first_manager, worker_id='voice-before-restart')
    first_adapter.attach(first_session)
    started = first_adapter.begin_user_objective(user_messages[0], initial_task='inspect every test-owned area', portion_budget=C.PortionBudget(max_actions=2))
    first_speech = Speech()
    first_session.emit('speech_created', SimpleNamespace(speech_handle=first_speech, source='generate_reply'))
    first_session.emit('function_tools_executed', Tools('CHILD-A', 'CHILD-B'))
    first_speech.finish()
    await first_adapter.wait_idle()
    side_effect = first_manager.reserve_attempt(first_adapter.active_claim, task_id=first_adapter.active_claim.task_id, idempotency_key='owned-side-effect:audit-output:v1')
    first_manager.settle_attempt(first_adapter.active_claim, side_effect.attempt_id, status='succeeded', result_ref='RESULT-owned-audit-output')
    duplicate = first_manager.reserve_attempt(first_adapter.active_claim, task_id=first_adapter.active_claim.task_id, idempotency_key='owned-side-effect:audit-output:v1')
    first_session.emit('error', SimpleNamespace(error=SimpleNamespace(error=RuntimeError('injected provider outage'), recoverable=False)))
    retry_snapshot = first_manager.status(started.run_id)
    first_store.close()
    now[0] += timedelta(seconds=2)
    second_store = Store(db)
    second_manager = C.ContinuityManager(second_store, clock=lambda: now[0])
    second_session = Session()
    second_adapter = LiveKitContinuity(second_manager, worker_id='voice-after-restart')
    second_adapter.attach(second_session)
    recovered = await second_adapter.pump_once()
    verification_speech = Speech()
    second_session.emit('speech_created', SimpleNamespace(speech_handle=verification_speech, source='generate_reply'))
    next_step = second_adapter.update_active(action='continue', next_task='verify the complete audit from durable evidence', verification_required=True)
    verification_speech.finish()
    await second_adapter.wait_idle()
    second_session.emit('function_tools_executed', Tools('VERIFY-AUDIT'))
    completed = second_adapter.update_active(action='complete', verification_task_id=next_step['task_id'])
    second_adapter.announce_progress(started.run_id)
    final = second_manager.status(started.run_id)
    attempts = second_store._conn.execute('SELECT attempt_id, status FROM run_task_attempts WHERE run_id=?', (started.run_id,)).fetchall()
    verification_task = next((task for task in final.tasks if task['task_id'] == next_step['task_id']))
    events = second_manager.events(started.run_id)
    checks = [('same run survives restart', recovered.run_id == started.run_id, recovered.run_id if recovered else 'no recovery claim'), ('no user continue message', len(user_messages) == 1, f"user messages={len(user_messages)}"), ('multiple bounded portions', final.counters['portions'] >= 3, f"portions={final.counters['portions']}"), ('provider retry persisted', retry_snapshot.state == 'retrying' and retry_snapshot.counters['scheduler_retries'] == 1, f"state={retry_snapshot.state} retries={retry_snapshot.counters['scheduler_retries']}"), ('successful attempt not re-executed', not duplicate.execute and len(attempts) == 1 and attempts[0]['status'] == 'succeeded', f"attempts={len(attempts)} duplicate_execute={duplicate.execute}"), ('objective completed', completed['state'] == 'completed' and final.outcome == 'succeeded', f"state={final.state} outcome={final.outcome}"), ('verification task succeeded', verification_task['status'] == 'succeeded' and 'VERIFY-AUDIT' in verification_task['evidence_refs'], f"verification={verification_task['status']}"), ('terminal run has no wake', final.wake is None, f"wake={final.wake}"), ('checkpoint history survived', final.checkpoint_version >= 2, f"checkpoints={final.checkpoint_version}"), ('progress is event-backed', bool(second_adapter.announced_progress) and all((event_id in {event['event_id'] for event in events} for event_id, _line in second_adapter.announced_progress)), f"events={len(events)} spoken={len(second_adapter.announced_progress)}"), ('continuation envelope preserves objective', any((started.objective in text for text in first_session.generated + second_session.generated)), 'objective found in internal continuation'), ('continuation envelope preserves authority', all(('must not gain new authority' in text for text in first_session.generated + second_session.generated)), 'authority guard present')]
    second_store.close()
    return checks


def main() -> int:
    with tempfile.TemporaryDirectory(prefix='friday-continuity-') as tmp:
        checks = asyncio.run(journey(Path(tmp) / 'continuity.sqlite3'))
    for name, passed, detail in checks:
        print(f"[{'PASS' if passed else 'FAIL'}] {name}: {detail}")
    passed = sum((1 for _name, ok, _detail in checks if ok))
    print(f"\n{passed}/{len(checks)} continuous-run checks passed")
    return 0 if passed == len(checks) else 1
if __name__ == '__main__':
    raise SystemExit(main())