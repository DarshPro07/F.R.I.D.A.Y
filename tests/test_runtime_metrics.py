from __future__ import annotations
import asyncio
from types import SimpleNamespace
from friday.runtime_metrics import RuntimeTelemetry


class Session:
    def __init__(self):
        self.handlers = {}

    def on(self, name, callback):
        self.handlers.setdefault(name, []).append(callback)

    def emit(self, name, event):
        for callback in self.handlers.get(name, []):
            callback(event)


def test_snapshot_marks_missing_realtime_measurements_unmeasured():
    telemetry = RuntimeTelemetry(clock=lambda: 10.0)
    snapshot = telemetry.snapshot()
    assert snapshot['measurements']['stt_final_latency'] is None
    assert snapshot['measurements']['ack_first_audio_latency'] is None
    assert snapshot['measurements']['tts_first_audio_latency'] is None
    assert snapshot['measurements']['tool_start_latency'] is None
    assert snapshot['measurements']['event_loop_max_stall'] is None
    assert snapshot['counters']['queue_overflows'] is None
    assert snapshot['counters']['dropped_output_frames'] is None


def test_transcript_body_and_unsafe_fields_are_not_recorded():
    telemetry = RuntimeTelemetry(clock=lambda: 10.0)
    telemetry.mark('transcript_final', run_id='RUN-1', speech_id='speech-1', transcript='do not log this', token='secret', chars=15)
    event = telemetry.snapshot()['events'][0]
    assert event['run_id'] == 'RUN-1'
    assert event['speech_id'] == 'speech-1'
    assert event['fields'] == {'chars': 15}


def test_provider_history_shape_counts_are_safe_without_history_content():
    telemetry = RuntimeTelemetry(clock=lambda: 10.0)
    telemetry.mark('provider_request', function_calls=3, missing_signatures=1, missing_outputs=1, orphan_outputs=0, prompt='do not record', signature=b'do not record')
    assert telemetry.snapshot()['events'][0]['fields'] == {'function_calls': 3, 'missing_signatures': 1, 'missing_outputs': 1, 'orphan_outputs': 0}


def test_livekit_metrics_populate_phase_measurements_without_content():
    telemetry = RuntimeTelemetry(clock=lambda: 10.0)
    session = Session()
    telemetry.attach(session)
    session.emit('metrics_collected', SimpleNamespace(metrics=SimpleNamespace(type='eou_metrics', speech_id='speech-1', end_of_utterance_delay=0.2, transcription_delay=0.4, on_user_turn_completed_delay=0.1)))
    session.emit('metrics_collected', SimpleNamespace(metrics=SimpleNamespace(type='tts_metrics', speech_id='speech-1', ttfb=0.3, duration=1.2, audio_duration=1.0, characters_count=20, cancelled=False)))
    measurements = telemetry.snapshot()['measurements']
    assert measurements['stt_final_latency'] == 0.4
    assert measurements['tts_first_audio_latency'] == 0.3


def test_heartbeat_records_event_loop_stall():
    async def scenario():
        now = [0.0]
        telemetry = RuntimeTelemetry(clock=lambda: now[0])
        telemetry.heartbeat_tick(expected_at=1.0, observed_at=1.35)
        assert telemetry.snapshot()['measurements']['event_loop_max_stall'] == 0.35
    asyncio.run(scenario())


def test_live_heartbeat_starts_and_closes_without_a_background_leak():
    async def scenario():
        telemetry = RuntimeTelemetry()
        telemetry.start_heartbeat(interval=0.01)
        await asyncio.sleep(0.05)
        assert telemetry.snapshot()['measurements']['event_loop_max_stall'] is not None
        await telemetry.aclose()
        assert telemetry._heartbeat_task is None
    asyncio.run(scenario())


def test_objective_aggregation_preserves_tokens_and_success_ratios():
    claim = SimpleNamespace(run_id='RUN-1', portion_id='PORTION-1')
    telemetry = RuntimeTelemetry(clock=lambda: 10.0)
    telemetry.set_correlation(lambda: claim)
    telemetry.mark('provider_request', provider='google', model='gemini-test', request_fingerprint='aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', failure_class='none', retry_index=0, dispatched=True)
    telemetry.mark('tool_settled', tool_name='apps_open', status='succeeded')
    telemetry.mark('llm_metrics', prompt_tokens=50000, completion_tokens=500, total_tokens=50500)
    row = telemetry.snapshot()['objectives']['RUN-1']
    assert row['provider_calls'] == 1
    assert row['successful_tool_calls'] == 1
    assert row['total_tokens'] == 50500
    assert row['provider_calls_per_successful_tool'] == 1.0
    assert row['tokens_per_successful_tool'] == 50500.0
    assert row['retry_tokens'] is None


def test_comparison_does_not_invent_an_unavailable_baseline():
    report = RuntimeTelemetry().comparison()
    assert report['baseline'] is None
    assert report['baseline_status'] == 'UNMEASURED'