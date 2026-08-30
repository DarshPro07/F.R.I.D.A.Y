from __future__ import annotations
import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace
import pytest
from livekit.agents import llm
from friday.continuity import ContinuityManager, WakeCondition
from friday.runtime_control import ACK_AND_ACT, ACT_THEN_CONFIRM, ANNOUNCE_THEN_ACT, ASSOCIATED, BOUND, DUPLICATE, GuardedMCPServerHTTP, SILENT_BACKGROUND, ActionPresentationPolicy, RunExecutionArbiter, StaleExecutionOwner, canonical_execution_key, narration_milestone
from friday.runtime_metrics import RuntimeTelemetry
from friday.store import Store
NOW = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)


class Speech:
    def __init__(self, speech_id: str):
        self.id = speech_id
        self.interrupted = False

    def interrupt(self, *, force: bool = False):
        assert force is True
        self.interrupted = True


def controlled(tmp_path):
    store = Store(tmp_path / 'runtime.sqlite3')
    manager = ContinuityManager(store, clock=lambda: NOW)
    snapshot = manager.start_run('open Calculator once', initial_task='open Calculator')
    claim = manager.claim_run(snapshot.run_id, 'voice-1')
    assert claim is not None
    return (store, manager, claim)


def test_only_one_generated_speech_binds_to_a_claim(tmp_path):
    store, manager, claim = controlled(tmp_path)
    arbiter = RunExecutionArbiter(manager, active_claim=lambda: claim)
    first = Speech('speech-a')
    second = Speech('speech-b')
    assert arbiter.bind_speech(claim, first, source='generate_reply').state == BOUND
    decision = arbiter.bind_speech(claim, second, source='generate_reply')
    assert decision.state == DUPLICATE
    assert second.interrupted is True
    assert first.interrupted is False
    assert arbiter.owner.speech_id == 'speech-a'
    store.close()


def test_runtime_say_is_presentation_not_a_competing_owner(tmp_path):
    store, manager, claim = controlled(tmp_path)
    arbiter = RunExecutionArbiter(manager, active_claim=lambda: claim)
    generated = Speech('speech-a')
    acknowledgement = Speech('speech-ack')
    arbiter.bind_speech(claim, generated, source='generate_reply')
    decision = arbiter.bind_speech(claim, acknowledgement, source='say')
    assert decision.state == ASSOCIATED
    assert acknowledgement.interrupted is False
    assert arbiter.owner.speech_id == 'speech-a'
    store.close()


def test_stale_speech_cannot_reserve_a_side_effect(tmp_path):
    store, manager, claim = controlled(tmp_path)
    telemetry = RuntimeTelemetry()
    arbiter = RunExecutionArbiter(manager, active_claim=lambda: claim, telemetry=telemetry)
    arbiter.bind_speech(claim, Speech('speech-a'), source='generate_reply')
    stale_context = SimpleNamespace(speech_handle=Speech('speech-b'))
    with pytest.raises(StaleExecutionOwner):
        arbiter.authorize_tool(stale_context, 'apps_open', {'name': 'calculator'})
    rows = store._conn.execute('SELECT * FROM run_task_attempts').fetchall()
    assert rows == []
    assert telemetry.snapshot()['counters']['stale_owner_rejections'] == 1
    store.close()


def test_revoked_speech_context_cannot_bind_itself_to_the_next_portion(tmp_path):
    store, manager, first_claim = controlled(tmp_path)
    active = first_claim
    arbiter = RunExecutionArbiter(manager, active_claim=lambda: active)
    old_speech = Speech('speech-old')
    old_context = SimpleNamespace(speech_handle=old_speech, function_call=SimpleNamespace(call_id='call-old', name='apps_open'))
    arbiter.authorize_owner(old_context, expected_tool='apps_open')
    manager.checkpoint(first_claim, summary='next portion', wake=WakeCondition.immediate('continue'))
    arbiter.revoke(first_claim)
    active = manager.claim_run(first_claim.run_id, 'voice-2')
    assert active is not None
    with pytest.raises(StaleExecutionOwner):
        arbiter.authorize_tool(old_context, 'apps_open', {'name': 'calculator'})
    rows = store._conn.execute('SELECT * FROM run_task_attempts').fetchall()
    assert rows == []
    store.close()


def test_identical_argument_objects_have_one_execution_key():
    left = canonical_execution_key('TASK-1', 'apps_open', {'name': 'calculator', 'options': {'b': 2, 'a': 1}})
    right = canonical_execution_key('TASK-1', 'apps_open', {'options': {'a': 1, 'b': 2}, 'name': 'calculator'})
    assert left == right
    assert left != canonical_execution_key('TASK-2', 'apps_open', {'name': 'calculator'})


def test_successful_side_effect_reservation_cannot_execute_twice(tmp_path):
    store, manager, claim = controlled(tmp_path)
    arbiter = RunExecutionArbiter(manager, active_claim=lambda: claim)
    context = SimpleNamespace(speech_handle=Speech('speech-a'), session=SimpleNamespace(say=lambda *args, **kwargs: SimpleNamespace(id='ack')))
    arbiter.bind_speech(claim, context.speech_handle, source='generate_reply')
    first = arbiter.authorize_tool(context, 'apps_open', {'name': 'calculator'})
    assert first.execute is True
    arbiter.settle_tool(first, '{"run_id":"ACTION-1","status":"succeeded","may_claim_completion":true,"verification":{"method":"window","evidence":"open"}}')
    second = arbiter.authorize_tool(context, 'apps_open', {'name': 'calculator'})
    assert second.execute is False
    assert second.status == 'succeeded'
    store.close()


def test_running_and_unknown_attempts_are_not_dispatched_again(tmp_path):
    store, manager, claim = controlled(tmp_path)
    arbiter = RunExecutionArbiter(manager, active_claim=lambda: claim)
    context = SimpleNamespace(speech_handle=Speech('speech-a'))
    arbiter.bind_speech(claim, context.speech_handle, source='generate_reply')
    running = arbiter.authorize_tool(context, 'apps_open', {'name': 'calculator'})
    duplicate_running = arbiter.authorize_tool(context, 'apps_open', {'name': 'calculator'})
    assert running.execute is True
    assert duplicate_running.execute is False
    assert duplicate_running.status == 'running'
    arbiter.settle_exception(running, TimeoutError('outcome uncertain'), dispatched=True)
    duplicate_unknown = arbiter.authorize_tool(context, 'apps_open', {'name': 'calculator'})
    assert duplicate_unknown.execute is False
    assert duplicate_unknown.status == 'unknown'
    store.close()


def test_late_tool_result_cannot_settle_after_claim_checkpoint(tmp_path):
    store, manager, claim = controlled(tmp_path)
    active = claim
    arbiter = RunExecutionArbiter(manager, active_claim=lambda: active)
    context = SimpleNamespace(speech_handle=Speech('speech-a'))
    arbiter.bind_speech(claim, context.speech_handle, source='generate_reply')
    authorization = arbiter.authorize_tool(context, 'apps_open', {'name': 'calculator'})
    manager.checkpoint(claim, summary='continue elsewhere', wake=WakeCondition.immediate())
    active = None
    with pytest.raises(StaleExecutionOwner):
        arbiter.settle_tool(authorization, '{"run_id":"ACTION-LATE","status":"succeeded","may_claim_completion":true,"verification":{"evidence":"late"}}')
    attempt = store._conn.execute('SELECT status FROM run_task_attempts WHERE attempt_id=?', (authorization.reservation.attempt_id,)).fetchone()
    assert attempt['status'] == 'unknown'
    assert manager.status(claim.run_id).counters['actions'] == 0
    store.close()


def test_guarded_mcp_tool_checks_owner_before_network_dispatch(tmp_path):
    store, manager, claim = controlled(tmp_path)
    arbiter = RunExecutionArbiter(manager, active_claim=lambda: claim)
    context = SimpleNamespace(speech_handle=Speech('speech-a'), session=SimpleNamespace(say=lambda *args, **kwargs: SimpleNamespace(id='ack')))
    arbiter.bind_speech(claim, context.speech_handle, source='generate_reply')

    class Client:
        calls = 0

        async def call_tool(self, name, arguments):
            self.calls += 1
            return SimpleNamespace(isError=False, output='{"run_id":"ACTION-1","status":"succeeded","may_claim_completion":true,"verification":{"method":"window","evidence":"open"}}')
    server = GuardedMCPServerHTTP(url='http://127.0.0.1:8000/sse', arbiter=arbiter, tool_result_resolver=lambda result: result.result.output)
    server._client = Client()
    tool = server._make_function_tool('apps_open', 'open an app', {'type': 'object'}, None)
    first = asyncio.run(tool({'name': 'calculator'}, context))
    duplicate = asyncio.run(tool({'name': 'calculator'}, context))
    assert '"status":"succeeded"' in first
    assert json.loads(duplicate)['duplicate_prevented'] is True
    assert server._client.calls == 1
    store.close()


def test_tool_run_context_binds_owner_before_speech_observer(tmp_path):
    store, manager, claim = controlled(tmp_path)
    arbiter = RunExecutionArbiter(manager, active_claim=lambda: claim)
    speech = Speech('speech-live')
    context = SimpleNamespace(speech_handle=speech, function_call=SimpleNamespace(call_id='call-live-1', name='apps_open', arguments='{"name":"calculator"}'), session=SimpleNamespace(say=lambda *args, **kwargs: SimpleNamespace(id='ack')))

    class Client:
        calls = 0

        async def call_tool(self, name, arguments):
            self.calls += 1
            return SimpleNamespace(isError=False, output='{"run_id":"ACTION-LIVE","status":"succeeded","may_claim_completion":true,"verification":{"method":"window","evidence":"open"}}')
    server = GuardedMCPServerHTTP(url='http://127.0.0.1:8000/sse', arbiter=arbiter, tool_result_resolver=lambda result: result.result.output)
    server._client = Client()
    tool = server._make_function_tool('apps_open', 'open an app', {'type': 'object'}, None)
    result = asyncio.run(tool({'name': 'calculator'}, context))
    assert '"status":"succeeded"' in result
    assert arbiter.owner.speech_id == 'speech-live'
    assert server._client.calls == 1
    store.close()


def test_livekit_injects_run_context_into_guarded_mcp_tool(tmp_path):
    store, manager, claim = controlled(tmp_path)
    arbiter = RunExecutionArbiter(manager, active_claim=lambda: claim)
    server = GuardedMCPServerHTTP(url='http://127.0.0.1:8000/sse', arbiter=arbiter)
    tool = server._make_function_tool('apps_open', 'open an app', {'type': 'object'}, None)
    context = SimpleNamespace(speech_handle=Speech('speech-livekit'), function_call=SimpleNamespace(call_id='call-livekit', name='apps_open'))
    args, kwargs = llm.utils.prepare_function_arguments(fnc=tool, json_arguments='{"name":"calculator"}', call_ctx=context)
    assert context in (*args, *kwargs.values())
    store.close()


def test_three_step_live_invocations_share_one_owner_without_observer_ordering(tmp_path):
    store, manager, claim = controlled(tmp_path)
    arbiter = RunExecutionArbiter(manager, active_claim=lambda: claim)
    speech = Speech('speech-three-step')

    class Client:
        calls = []

        async def call_tool(self, name, arguments):
            self.calls.append((name, arguments))
            return SimpleNamespace(isError=False, output=json.dumps({'run_id': f"ACTION-{len(self.calls)}", 'status': 'succeeded', 'may_claim_completion': True, 'verification': {'method': 'fixture', 'evidence': name}}))
    server = GuardedMCPServerHTTP(url='http://127.0.0.1:8000/sse', arbiter=arbiter, tool_result_resolver=lambda result: result.result.output)
    server._client = Client()
    wanted = [('get_current_time', {}), ('system_get_info', {}), ('apps_open', {'name': 'calculator'})]
    for index, (name, arguments) in enumerate(wanted, start=1):
        context = SimpleNamespace(speech_handle=speech, function_call=SimpleNamespace(call_id=f"call-{index}", name=name, arguments=json.dumps(arguments)), session=SimpleNamespace(say=lambda *args, **kwargs: SimpleNamespace(id='ack')))
        tool = server._make_function_tool(name, f"run {name}", {'type': 'object'}, None)
        result = json.loads(asyncio.run(tool(arguments, context)))
        assert result['status'] == 'succeeded'
    assert server._client.calls == wanted
    assert arbiter.owner.speech_id == 'speech-three-step'
    assert manager.status(claim.run_id).counters['actions'] == 3
    store.close()


def test_startup_profile_read_is_the_only_additional_ownerless_tool(tmp_path):
    store, manager, claim = controlled(tmp_path)
    arbiter = RunExecutionArbiter(manager, active_claim=lambda: claim)

    class Client:
        calls = []

        async def call_tool(self, name, arguments):
            self.calls.append(name)
            return SimpleNamespace(isError=False, output='{"status":"succeeded"}')
    server = GuardedMCPServerHTTP(url='http://127.0.0.1:8000/sse', arbiter=arbiter, tool_result_resolver=lambda result: result.result.output)
    server._client = Client()
    profile_get = server._make_function_tool('profile_get', 'read the profile', {'type': 'object'}, None)
    apps_open = server._make_function_tool('apps_open', 'open an app', {'type': 'object'}, None)
    assert asyncio.run(profile_get({})) == '{"status":"succeeded"}'
    with pytest.raises(StaleExecutionOwner):
        asyncio.run(apps_open({'name': 'calculator'}))
    assert server._client.calls == ['profile_get']
    store.close()


def test_guarded_mcp_tool_rejects_ownerless_model_calls(tmp_path):
    store, manager, claim = controlled(tmp_path)
    arbiter = RunExecutionArbiter(manager, active_claim=lambda: claim)
    server = GuardedMCPServerHTTP(url='http://127.0.0.1:8000/sse', arbiter=arbiter)
    tool = server._make_function_tool('apps_open', 'open an app', {'type': 'object'}, None)
    with pytest.raises(StaleExecutionOwner):
        asyncio.run(tool({'name': 'calculator'}))
    store.close()


def test_presentation_policy_classifies_known_and_unknown_tools():
    policy = ActionPresentationPolicy()
    assert policy.plan('music_play', {'query': 'jazz'}).mode == ANNOUNCE_THEN_ACT
    assert policy.plan('apps_open', {'name': 'calculator'}).mode == ACK_AND_ACT
    assert policy.plan('memory_recall', {'query': 'name'}).mode == SILENT_BACKGROUND
    assert policy.plan('new_side_effect', {}).mode == ACT_THEN_CONFIRM


def test_music_acknowledgement_plays_out_before_network_dispatch(tmp_path):
    store, manager, claim = controlled(tmp_path)
    arbiter = RunExecutionArbiter(manager, active_claim=lambda: claim)
    events = []

    class Ack:
        id = 'ack-a'

        async def wait_for_playout(self):
            events.append('playout')

    class Session:
        def say(self, text, **kwargs):
            events.append(('say', text, kwargs))
            return Ack()

    class Client:
        async def call_tool(self, name, arguments):
            events.append('dispatch')
            return SimpleNamespace(isError=False, output='{"run_id":"ACTION-MUSIC","status":"succeeded","may_claim_completion":true,"verification":{"method":"process","evidence":"playing"}}')
    speech = Speech('speech-a')
    context = SimpleNamespace(speech_handle=speech, session=Session())
    arbiter.bind_speech(claim, speech, source='generate_reply')
    server = GuardedMCPServerHTTP(url='http://127.0.0.1:8000/sse', arbiter=arbiter, tool_result_resolver=lambda result: result.result.output)
    server._client = Client()
    tool = server._make_function_tool('music_play', 'play music', {'type': 'object'}, None)
    asyncio.run(tool({'query': 'jazz'}, context))
    assert events[0][0] == 'say'
    assert events[1:] == ['playout', 'dispatch']
    store.close()


def test_semantic_milestones_ignore_phrasing_but_allow_a_new_task():
    result = '{"status":"succeeded","may_claim_completion":true,"output":{"app":"Calculator"},"verification":{"method":"window","evidence":"visible"}}'
    first = narration_milestone('apps_open', result, task_id='TASK-1')
    repeated = narration_milestone('apps_open', result, task_id='TASK-1')
    intentional_repeat = narration_milestone('apps_open', result, task_id='TASK-2')
    assert first == repeated
    assert first != intentional_repeat


def test_repeated_verified_app_state_is_returned_without_repeat_narration(tmp_path):
    store, manager, claim = controlled(tmp_path)
    arbiter = RunExecutionArbiter(manager, active_claim=lambda: claim)
    context = SimpleNamespace(speech_handle=Speech('speech-a'), session=SimpleNamespace(say=lambda *args, **kwargs: SimpleNamespace(id='ack')))
    arbiter.bind_speech(claim, context.speech_handle, source='generate_reply')

    class Client:
        async def call_tool(self, name, arguments):
            return SimpleNamespace(isError=False, output='{"status":"succeeded","may_claim_completion":true,"output":{"app":"Calculator"},"verification":{"method":"window","evidence":"visible"}}')
    server = GuardedMCPServerHTTP(url='http://127.0.0.1:8000/sse', arbiter=arbiter, tool_result_resolver=lambda result: result.result.output)
    server._client = Client()
    tool = server._make_function_tool('apps_open', 'open an app', {'type': 'object'}, None)
    first = json.loads(asyncio.run(tool({'name': 'Calculator'}, context)))
    repeated = json.loads(asyncio.run(tool({'name': 'calc'}, context)))
    first_body = json.loads(first['text']) if 'text' in first else first
    repeated_body = json.loads(repeated['text']) if 'text' in repeated else repeated
    assert first_body['narration_allowed'] is True
    assert repeated_body['narration_allowed'] is False
    assert repeated_body['may_claim_completion'] is True
    store.close()