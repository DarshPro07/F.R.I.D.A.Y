"""Safe phase telemetry for the live voice runtime."""
from __future__ import annotations
import asyncio
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable
MEASUREMENT_NAMES = ('end_of_utterance_delay', 'stt_final_latency', 'llm_first_token_latency', 'ack_first_audio_latency', 'tts_first_audio_latency', 'tool_start_latency', 'event_loop_max_stall')
SAFE_FIELDS = frozenset({'missing_outputs', 'model', 'prompt_cached_tokens', 'phase', 'request_fingerprint', 'source', 'count', 'status', 'prompt_tokens', 'retry_index', 'tool_name', 'duration', 'function_calls', 'input_tokens', 'completion_tokens', 'cancelled', 'failure_class', 'total_tokens', 'chars', 'request_id', 'user_initiated', 'orphan_outputs', 'label', 'output_tokens', 'dispatched', 'audio_duration', 'provider', 'missing_signatures'})


@dataclass(frozen=True)
class PhaseEvent:
    phase: str
    at: float
    run_id: str = ''
    portion_id: str = ''
    speech_id: str = ''
    fields: dict[str, Any] = field(default_factory=dict)


class RuntimeTelemetry:
    """Collect measurements without retaining prompts, transcripts, or secrets."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._events = []
        self._correlation = None
        self._heartbeat_task = None
        self._measurements = {name: None for name in MEASUREMENT_NAMES}
        self._counters = {'duplicate_owners_refused': 0, 'duplicate_calls_prevented': 0, 'duplicate_narrations_prevented': 0, 'structural_errors': 0, 'transient_retries': 0, 'queue_overflows': 0, 'dropped_output_frames': 0, 'provider_calls': 0, 'tool_calls_settled': 0, 'successful_tool_calls': 0, 'stale_owner_rejections': 0, 'muted_audio_turns_ignored': 0}
        self._counters['queue_overflows'] = None
        self._counters['dropped_output_frames'] = None

    def set_correlation(self, correlation: Callable[[], object | None]) -> None:
        self._correlation = correlation

    def attach(self, session) -> None:
        session.on('metrics_collected', self.on_metrics_collected)
        session.on('user_input_transcribed', self.on_user_input_transcribed)
        session.on('speech_created', self.on_speech_created)

    def mark(self, phase: str, *, run_id: str = '', portion_id: str = '', speech_id: str = '', at: float | None = None, **fields: Any) -> None:
        if not run_id and self._correlation is not None:
            correlated = self._correlation()
            if correlated is not None:
                run_id = str(getattr(correlated, 'run_id', '') or '')
                portion_id = str(getattr(correlated, 'portion_id', '') or '')
        safe = {key: value for key, value in fields.items() if key in SAFE_FIELDS if isinstance(value, (str, int, float, bool, type(None)))}
        self._events.append(PhaseEvent(phase=phase, at=self._clock() if at is None else at, run_id=run_id, portion_id=portion_id, speech_id=speech_id, fields=safe))
        if phase == 'tool_started' and speech_id:
            created = next((event for event in reversed(self._events[:-1]) 
if event.phase == 'speech_created' 
if event.speech_id == speech_id), None)
            if created is not None:
                self.measure('tool_start_latency', self._events[-1].at - created.at)

    def increment(self, name: str, count: int = 1) -> None:
        if name not in self._counters:
            raise KeyError(f"unknown runtime counter {name!r}")
        self._counters[name] = int(self._counters[name] or 0) + count

    def measure(self, name: str, value: float) -> None:
        if name not in self._measurements:
            raise KeyError(f"unknown runtime measurement {name!r}")
        self._measurements[name] = round(max(0.0, float(value)), 6)

    def heartbeat_tick(self, *, expected_at: float, observed_at: float | None = None) -> None:
        observed = self._clock() if observed_at is None else observed_at
        lag = round(max(0.0, observed - expected_at), 6)
        current = self._measurements['event_loop_max_stall']
        if current is None or lag > current:
            self._measurements['event_loop_max_stall'] = lag

    def on_user_input_transcribed(self, event) -> None:
        if not getattr(event, 'is_final', False):
            return
        transcript = str(getattr(event, 'transcript', '') or '')
        self.mark('transcript_final', chars=len(transcript))

    def on_speech_created(self, event) -> None:
        handle = getattr(event, 'speech_handle', None)
        self.mark('speech_created', speech_id=str(getattr(handle, 'id', '') or ''), source=str(getattr(event, 'source', '') or ''), user_initiated=bool(getattr(event, 'user_initiated', False)))

    def on_metrics_collected(self, event) -> None:
        metric = getattr(event, 'metrics', None)
        kind = str(getattr(metric, 'type', '') or '')
        speech_id = str(getattr(metric, 'speech_id', '') or '')
        if kind == 'eou_metrics':
            self.measure('end_of_utterance_delay', getattr(metric, 'end_of_utterance_delay', 0.0))
            self.measure('stt_final_latency', getattr(metric, 'transcription_delay', 0.0))
        elif kind == 'llm_metrics':
            self.measure('llm_first_token_latency', getattr(metric, 'ttft', 0.0))
        elif kind == 'tts_metrics':
            self.measure('tts_first_audio_latency', getattr(metric, 'ttfb', 0.0))
            if any((event.phase == 'ack_started' and event.speech_id == speech_id for event in self._events)):
                self.measure('ack_first_audio_latency', getattr(metric, 'ttfb', 0.0))
        fields = {}
        for name in SAFE_FIELDS:
            if hasattr(metric, name):
                fields[name] = getattr(metric, name)
        self.mark(kind or 'metrics', speech_id=speech_id, **fields)

    def start_heartbeat(self, *, interval: float = 0.25) -> None:
        if self._heartbeat_task is not None:
            return

        async def heartbeat() -> None:
            expected = self._clock() + interval
            while True:
                await asyncio.sleep(interval)
                observed = self._clock()
                self.heartbeat_tick(expected_at=expected, observed_at=observed)
                expected = observed + interval
        self._heartbeat_task = asyncio.create_task(heartbeat(), name='friday-runtime-heartbeat')

    async def aclose(self) -> None:
        if self._heartbeat_task is None:
            return
        self._heartbeat_task.cancel()
        try:
            await self._heartbeat_task
        except asyncio.CancelledError:
            pass
        self._heartbeat_task = None

    def _objective_aggregates(self) -> dict[str, dict[str, int | float | None]]:
        aggregates = {}
        for event in self._events:
            if not event.run_id:
                continue
            row = aggregates.setdefault(event.run_id, {'provider_calls': 0, 'tool_calls': 0, 'successful_tool_calls': 0, 'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0, 'transient_failures': 0, 'structural_failures': 0})
            if event.phase == 'provider_request' and event.fields.get('failure_class') != 'none':
                failure = event.fields.get('failure_class')
                if failure == 'transient':
                    row['transient_failures'] += 1
                elif failure == 'structural':
                    row['structural_failures'] += 1
            if event.phase == 'provider_request' and event.fields.get('dispatched') is True:
                row['provider_calls'] += 1
            if event.phase == 'tool_settled':
                row['tool_calls'] += 1
                if event.fields.get('status') == 'succeeded':
                    row['successful_tool_calls'] += 1
            for token_field in ('prompt_tokens', 'completion_tokens', 'total_tokens'):
                value = event.fields.get(token_field)
                if isinstance(value, int):
                    row[token_field] += value
        for row in aggregates.values():
            successes = int(row['successful_tool_calls'] or 0)
            row['provider_calls_per_successful_tool'] = round(int(row['provider_calls'] or 0) / successes, 4) if successes else None
            row['tokens_per_successful_tool'] = round(int(row['total_tokens'] or 0) / successes, 4) if successes else None
            row['retry_tokens'] = None
            row['structural_error_tokens'] = None
        return aggregates

    def snapshot(self) -> dict:
        return {'measurements': dict(self._measurements), 'counters': dict(self._counters), 'objectives': self._objective_aggregates(), 'events': [asdict(event) for event in self._events]}

    def comparison(self, baseline: dict | None = None) -> dict:
        return {'baseline': baseline, 'repaired': self.snapshot(), 'baseline_status': 'MEASURED' if baseline is not None else 'UNMEASURED'}