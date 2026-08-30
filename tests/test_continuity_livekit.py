from __future__ import annotations
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace
from friday import contracts as c
from friday import continuity as C
from friday.continuity_livekit import LiveKitContinuity
from friday.provider_fallback import ProviderRequestRejected, STRUCTURAL
from friday.store import Store
NOW = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)


class FakeSession:
    def __init__(self):
        self.handlers = {}
        self.generated = []
        self.generated_chat_contexts = []
        self.said = []

    def on(self, name, callback):
        self.handlers.setdefault(name, []).append(callback)

    def emit(self, name, event):
        for callback in self.handlers.get(name, []):
            callback(event)

    def generate_reply(self, *, user_input, chat_ctx=None):
        self.generated.append(user_input)
        self.generated_chat_contexts.append(chat_ctx)
        return FakeSpeech()

    def say(self, text):
        self.said.append(text)
        return FakeSpeech()


class FakeSpeech:
    def __init__(self):
        self.id = f"speech-{id(self)}"
        self.callbacks = []
        self._done = False

    def interrupt(self, *, force=False):
        self.interrupted = force

    def add_done_callback(self, callback):
        if self._done:
            asyncio.get_running_loop().call_soon(callback, self)
            return
        self.callbacks.append(callback)

    def finish(self):
        self._done = True
        for callback in list(self.callbacks):
            callback(self)

    def done(self):
        return self._done


@dataclass
class FakeCall:
    name: str


@dataclass
class FakeOutput:
    output: str


class FakeToolsEvent:
    def __init__(self, count):
        self._pairs = [(FakeCall(f"tool-{index}"), FakeOutput('{"run_id":"CHILD-%d","status":"succeeded","may_claim_completion":true,"verification":{"method":"fixture","evidence":"owned"}}' % index)) for index in range(count)]

    def zipped(self):
        return self._pairs


def test_tool_budget_starts_a_second_generation_without_user_input(tmp_path):
    async def scenario():
        store = Store(tmp_path / 'livekit.sqlite3')
        manager = C.ContinuityManager(store, clock=lambda: NOW)
        session = FakeSession()
        adapter = LiveKitContinuity(manager, worker_id='voice-1')
        adapter.attach(session)
        started = adapter.begin_user_objective('audit all test-owned areas', portion_budget=C.PortionBudget(max_actions=2))
        speech = FakeSpeech()
        session.emit('speech_created', SimpleNamespace(speech_handle=speech, source='generate_reply'))
        session.emit('function_tools_executed', FakeToolsEvent(2))
        for index in range(2):
            evidence = (f"CHILD-{index}",)
            manager.record_actions(adapter.active_claim, count=1, evidence_refs=evidence)
            adapter.record_owned_tool_result(adapter.active_claim, count=1, evidence_refs=evidence)
        speech.finish()
        speech.finish()
        await adapter.wait_idle()
        snapshot = manager.status(started.run_id)
        assert len(session.generated) == 1
        assert snapshot.run_id == started.run_id
        assert snapshot.counters['portions'] == 2
        assert 'audit all test-owned areas' in session.generated[0]
        assert 'manual continue' not in session.generated[0].lower()
        store.close()
    asyncio.run(scenario())


def test_unrecoverable_model_error_becomes_a_durable_retry(tmp_path):
    store = Store(tmp_path / 'retry.sqlite3')
    manager = C.ContinuityManager(store, clock=lambda: NOW)
    session = FakeSession()
    adapter = LiveKitContinuity(manager, worker_id='voice-1')
    adapter.attach(session)
    started = adapter.begin_user_objective('survive Gemini', initial_task='inspect')
    session.emit('error', SimpleNamespace(error=SimpleNamespace(error=RuntimeError('fallback exhausted'), recoverable=False)))
    snapshot = manager.status(started.run_id)
    assert snapshot.state == 'retrying'
    assert snapshot.wake['kind'] == C.SCHEDULED_RETRY
    assert snapshot.counters['scheduler_retries'] == 1
    store.close()


def test_structural_history_failure_uses_one_transcript_free_continuation(tmp_path):
    async def scenario():
        store = Store(tmp_path / 'structural.sqlite3')
        manager = C.ContinuityManager(store, clock=lambda: NOW)
        session = FakeSession()
        adapter = LiveKitContinuity(manager, worker_id='voice-1')
        adapter.attach(session)
        started = adapter.begin_user_objective('finish without malformed replay', initial_task='inspect')
        speech = FakeSpeech()
        session.emit('speech_created', SimpleNamespace(speech_handle=speech, source='generate_reply'))
        session.emit('error', SimpleNamespace(error=SimpleNamespace(error=ProviderRequestRejected('native history incomplete', failure_class=STRUCTURAL, request_fingerprint='aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', local=True), recoverable=False)))
        speech.finish()
        await adapter.wait_idle()
        snapshot = manager.status(started.run_id)
        assert snapshot.run_id == started.run_id
        assert snapshot.counters['portions'] == 2
        assert snapshot.counters['scheduler_retries'] == 0
        assert len(session.generated) == 1
        assert session.generated_chat_contexts[0].items == []
        store.close()
    asyncio.run(scenario())


def test_structural_recovery_pumps_when_tool_context_bound_speech_already_finished(tmp_path):
    async def scenario():
        store = Store(tmp_path / 'structural-after-speech.sqlite3')
        manager = C.ContinuityManager(store, clock=lambda: NOW)
        session = FakeSession()
        adapter = LiveKitContinuity(manager, worker_id='voice-1')
        adapter.attach(session)
        started = adapter.begin_user_objective('finish without manual continue', initial_task='inspect')
        speech = FakeSpeech()
        adapter.arbiter.authorize_owner(SimpleNamespace(speech_handle=speech, function_call=SimpleNamespace(call_id='call-live-structural', name='get_current_time')), expected_tool='get_current_time')
        speech.finish()
        session.emit('error', SimpleNamespace(error=SimpleNamespace(error=ProviderRequestRejected('native history incomplete', failure_class=STRUCTURAL, request_fingerprint='bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb', local=True), recoverable=False)))
        await adapter.wait_idle()
        snapshot = manager.status(started.run_id)
        assert snapshot.counters['portions'] == 2
        assert len(session.generated) == 1
        assert session.generated_chat_contexts[0].items == []
        store.close()
    asyncio.run(scenario())


def test_fresh_adapter_reconstructs_due_work_without_chat_history(tmp_path):
    path = tmp_path / 'restart.sqlite3'
    first_store = Store(path)
    first = C.ContinuityManager(first_store, clock=lambda: NOW)
    started = first.start_run('summarize only what the page says', provenance=c.READ_MATERIAL, attended=False, initial_task='continue the bounded summary')
    first_store.close()

    async def scenario():
        reopened = Store(path)
        manager = C.ContinuityManager(reopened, clock=lambda: NOW)
        session = FakeSession()
        adapter = LiveKitContinuity(manager, worker_id='voice-after-restart')
        adapter.attach(session)
        claim = await adapter.pump_once()
        assert claim is not None
        assert claim.run_id == started.run_id
        assert len(session.generated) == 1
        envelope = session.generated[0]
        assert 'READ_MATERIAL' in envelope
        assert 'summarize only what the page says' in envelope
        assert 'must not gain new authority' in envelope
        reopened.close()
    asyncio.run(scenario())


def test_run_update_persists_the_next_task_before_starting_it(tmp_path):
    async def scenario():
        store = Store(tmp_path / 'update.sqlite3')
        manager = C.ContinuityManager(store, clock=lambda: NOW)
        session = FakeSession()
        adapter = LiveKitContinuity(manager, worker_id='voice-1')
        adapter.attach(session)
        started = adapter.begin_user_objective('build then verify', initial_task='build')
        speech = FakeSpeech()
        session.emit('speech_created', SimpleNamespace(speech_handle=speech, source='generate_reply'))
        result = adapter.update_active(action='continue', next_task='verify the result', verification_required=True)
        before_reply = manager.status(started.run_id)
        assert before_reply.wake['kind'] == C.IMMEDIATE
        assert before_reply.wake['task_id'] == result['task_id']
        assert session.generated == []
        speech.finish()
        await adapter.wait_idle()
        assert len(session.generated) == 1
        assert manager.status(started.run_id).counters['portions'] == 2
        store.close()
    asyncio.run(scenario())


def test_late_speech_callback_cannot_checkpoint_the_new_portion(tmp_path):
    async def scenario():
        store = Store(tmp_path / 'late-speech.sqlite3')
        manager = C.ContinuityManager(store, clock=lambda: NOW)
        session = FakeSession()
        adapter = LiveKitContinuity(manager, worker_id='voice-1')
        adapter.attach(session)
        started = adapter.begin_user_objective('work across portions', initial_task='first')
        old_speech = FakeSpeech()
        session.emit('speech_created', SimpleNamespace(speech_handle=old_speech, source='generate_reply'))
        adapter.update_active(action='continue', next_task='second')
        new_claim = await adapter.pump_once()
        assert new_claim is not None
        old_speech.finish()
        await adapter.wait_idle()
        assert adapter.active_claim.portion_id == new_claim.portion_id
        assert manager.status(started.run_id).state == 'working'
        assert manager.status(started.run_id).counters['portions'] == 2
        store.close()
    asyncio.run(scenario())


def test_friday_records_the_objective_before_learning_the_turn(monkeypatch):
    import agent_friday as A
    seen = []

    class FakeContinuity:
        def accept_user_turn(self, text):
            seen.append(('objective', text))

    class FakeLearner:
        def observe(self, text, assistant_text):
            seen.append(('learn', text))
    monkeypatch.setattr(A.autolearn, 'last_assistant_text', lambda ctx: '')
    agent = SimpleNamespace(_continuity=FakeContinuity(), _learner=FakeLearner())
    A.FridayAgent.prepare_turn(agent, object(), 'audit the workspace')
    assert seen == [('objective', 'audit the workspace'), ('learn', 'audit the workspace')]


def test_session_usage_records_only_the_new_cumulative_tokens(tmp_path):
    store = Store(tmp_path / 'usage.sqlite3')
    manager = C.ContinuityManager(store, clock=lambda: NOW)
    session = FakeSession()
    adapter = LiveKitContinuity(manager, worker_id='voice-1')
    adapter.attach(session)
    started = adapter.begin_user_objective('count the budget', initial_task='work')
    session.emit('session_usage_updated', SimpleNamespace(usage=SimpleNamespace(model_usage=[SimpleNamespace(type='llm_usage', input_tokens=10, output_tokens=5)])))
    session.emit('session_usage_updated', SimpleNamespace(usage=SimpleNamespace(model_usage=[SimpleNamespace(type='llm_usage', input_tokens=18, output_tokens=7)])))
    assert manager.status(started.run_id).counters['model_tokens'] == 25
    store.close()


def test_progress_speech_requires_a_new_event_and_respects_cooldown(tmp_path):
    monotonic = [100.0]
    store = Store(tmp_path / 'progress.sqlite3')
    manager = C.ContinuityManager(store, clock=lambda: NOW)
    session = FakeSession()
    adapter = LiveKitContinuity(manager, worker_id='voice-1', monotonic=lambda: monotonic[0], progress_cooldown=10.0)
    adapter.attach(session)
    started = adapter.begin_user_objective('report real milestones', initial_task='work')
    first = adapter.announce_progress(started.run_id)
    assert first is not None
    assert session.said == [first]
    manager.record_actions(adapter.active_claim, count=1, evidence_refs=('RESULT-1',))
    monotonic[0] += 2
    assert adapter.announce_progress(started.run_id) is None
    assert len(session.said) == 1
    monotonic[0] += 10
    second = adapter.announce_progress(started.run_id)
    assert second is not None
    assert len(session.said) == 2
    assert adapter.announce_progress(started.run_id) is None
    event_ids = {event['event_id'] for event in manager.events(started.run_id)}
    assert {event_id for event_id, _line in adapter.announced_progress} <= event_ids
    store.close()


def test_session_close_releases_the_lease_to_a_durable_restart_wake(tmp_path):
    store = Store(tmp_path / 'close.sqlite3')
    manager = C.ContinuityManager(store, clock=lambda: NOW)
    session = FakeSession()
    adapter = LiveKitContinuity(manager, worker_id='voice-1')
    adapter.attach(session)
    started = adapter.begin_user_objective('survive session close', initial_task='work')
    session.emit('close', SimpleNamespace(reason='job_shutdown', error=None))
    snapshot = manager.status(started.run_id)
    assert snapshot.state == 'working'
    assert snapshot.wake['kind'] == C.IMMEDIATE
    assert adapter.active_claim is None
    store.close()


def test_external_pause_invalidates_late_speech_and_next_turn_resumes(tmp_path):
    async def scenario():
        store = Store(tmp_path / 'external-pause.sqlite3')
        manager = C.ContinuityManager(store, clock=lambda: NOW)
        session = FakeSession()
        adapter = LiveKitContinuity(manager, worker_id='voice-1')
        adapter.attach(session)
        started = adapter.begin_user_objective('pause safely', initial_task='work')
        speech = FakeSpeech()
        session.emit('speech_created', SimpleNamespace(speech_handle=speech, source='generate_reply'))
        manager.pause_run(started.run_id, 'external control paused it')
        speech.finish()
        await adapter.wait_idle()
        assert manager.status(started.run_id).state == 'paused'
        resumed = adapter.accept_user_turn('resume that task')
        assert resumed.run_id == started.run_id
        assert resumed.state == 'working'
        assert adapter.active_claim is not None
        store.close()
    asyncio.run(scenario())