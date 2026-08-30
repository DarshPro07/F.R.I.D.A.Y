"""History-aware provider fallback without metadata reconstruction."""
from __future__ import annotations
import asyncio
import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any, Literal
from livekit.agents import APIConnectionError, APIStatusError
from livekit.agents.llm import FallbackAdapter
from livekit.agents.llm.chat_context import ChatContext, FunctionCall, FunctionCallOutput
from livekit.agents.llm.fallback_adapter import AvailabilityChangedEvent, DEFAULT_FALLBACK_API_CONNECT_OPTIONS, FallbackLLMStream
from livekit.agents.llm.tool_context import Tool, ToolChoice
from livekit.agents.types import NOT_GIVEN, APIConnectOptions, NotGivenOr
from friday.runtime_metrics import RuntimeTelemetry
TRANSIENT = 'transient'
STRUCTURAL = 'structural'
CONTENT = 'content'
UNKNOWN = 'unknown'
FailureClass = Literal[('transient', 'structural', 'content', 'unknown')]
_STRUCTURAL_MARKERS = ('thought_signature', 'thought signature', 'malformed history', 'malformed function', 'unexpected tool', 'unexpected function', 'invalid schema', 'function call is missing')
_CONTENT_MARKERS = ('content filter', 'content policy', 'safety policy', 'blocked for safety', 'prohibited content')
_TRANSIENT_MARKERS = ('no response generated', 'failed to generate llm completion', 'fallback exhausted', 'rate limit', 'temporarily unavailable', 'connection reset', 'connection error', 'timed out', 'timeout')


@dataclass(frozen=True)
class HistoryInspection:
    request_fingerprint: str
    complete: bool
    function_calls: int
    missing_signatures: int
    missing_outputs: int
    orphan_outputs: int


class ProviderRequestRejected(RuntimeError):
    """A request must not be replayed through another provider attempt."""

    def __init__(self, message: str, *, failure_class: FailureClass, request_fingerprint: str, local: bool = False, history_counts: dict[str, int] | None = None) -> None:
        super().__init__(message)
        self.failure_class = failure_class
        self.request_fingerprint = request_fingerprint
        self.local = local
        self.history_counts = dict(history_counts or {})


def classify_provider_error(error: object) -> FailureClass:
    explicit = getattr(error, 'failure_class', None)
    if explicit in {TRANSIENT, STRUCTURAL, CONTENT, UNKNOWN}:
        return explicit
    text = f"{type(error).__name__}: {error}".lower()
    body = str(getattr(error, 'body', '') or '').lower()
    combined = f"{text} {body}"
    if any((marker in combined for marker in _STRUCTURAL_MARKERS)):
        return STRUCTURAL
    if any((marker in combined for marker in _CONTENT_MARKERS)):
        return CONTENT
    status_code = int(getattr(error, 'status_code', -1) or -1)
    if isinstance(error, (asyncio.TimeoutError, TimeoutError, APIConnectionError)):
        return TRANSIENT
    if status_code == 429 or status_code >= 500:
        return TRANSIENT
    if any((marker in combined for marker in _TRANSIENT_MARKERS)):
        return TRANSIENT
    if getattr(error, 'retryable', False) is True:
        return TRANSIENT
    return UNKNOWN


def _tool_name(tool: object) -> str:
    return str(getattr(tool, 'id', '') or getattr(getattr(tool, 'info', None), 'name', '') or type(tool).__name__)


def provider_request_fingerprint(chat_ctx: ChatContext, provider: str, model: str, *, tools: list[object] | None = None, signature_store: dict[str, bytes] | None = None) -> str:
    """Hash request structure and identities without content or opaque bytes."""
    items = []
    for item in chat_ctx.items:
        identity = {'type': str(getattr(item, 'type', type(item).__name__)), 'id': str(getattr(item, 'id', ''))}
        if isinstance(item, FunctionCall):
            identity.update({'name': item.name, 'call_id': hashlib.sha256(item.call_id.encode()).hexdigest(), 'native_signature': bool(signature_store is not None and item.call_id in signature_store)})
        elif isinstance(item, FunctionCallOutput):
            identity.update({'name': item.name, 'call_id': hashlib.sha256(item.call_id.encode()).hexdigest(), 'is_error': item.is_error})
        else:
            identity['role'] = str(getattr(item, 'role', ''))
        items.append(identity)
    shape = {'provider': provider, 'model': model, 'items': items, 'tools': sorted((_tool_name(tool) for tool in tools or []))}
    encoded = json.dumps(shape, sort_keys=True, separators=(',', ':')).encode()
    return hashlib.sha256(encoded).hexdigest()


def inspect_native_history(chat_ctx: ChatContext, provider: str, model: str, *, tools: list[object] | None = None, signature_store: dict[str, bytes] | None = None, require_signatures: bool = False) -> HistoryInspection:
    calls = [item for item in chat_ctx.items if isinstance(item, FunctionCall)]
    outputs = [item for item in chat_ctx.items if isinstance(item, FunctionCallOutput)]
    call_ids = {item.call_id for item in calls}
    output_ids = {item.call_id for item in outputs}
    missing_signatures = sum((1 for item in calls 
if require_signatures and (signature_store is None or item.call_id not in signature_store)))
    missing_outputs = len(call_ids - output_ids)
    orphan_outputs = len(output_ids - call_ids)
    return HistoryInspection(request_fingerprint=provider_request_fingerprint(chat_ctx, provider, model, tools=tools, signature_store=signature_store), complete=not (missing_signatures or missing_outputs or orphan_outputs), function_calls=len(calls), missing_signatures=missing_signatures, missing_outputs=missing_outputs, orphan_outputs=orphan_outputs)


class HistoryAwareFallback(FallbackAdapter):
    """LiveKit-compatible fallback that never retries structural requests."""

    def __init__(self, llm: list, *, signature_store: dict[str, bytes] | None = None, require_signatures: bool = False, telemetry: RuntimeTelemetry | None = None, **kwargs) -> None:
        super().__init__(llm, **kwargs)
        self.signature_store = signature_store
        self.require_signatures = require_signatures
        self.telemetry = telemetry
        self.structural_fingerprints = set()

    def set_telemetry(self, telemetry: RuntimeTelemetry) -> None:
        self.telemetry = telemetry

    def chat(self, *, chat_ctx: ChatContext, tools: list[Tool] | None = None, conn_options: APIConnectOptions = DEFAULT_FALLBACK_API_CONNECT_OPTIONS, parallel_tool_calls: NotGivenOr[bool] = NOT_GIVEN, tool_choice: NotGivenOr[ToolChoice] = NOT_GIVEN, extra_kwargs: NotGivenOr[dict[str, Any]] = NOT_GIVEN):
        primary = self._llm_instances[0]
        inspection = inspect_native_history(chat_ctx, str(getattr(primary, 'provider', 'unknown')), str(getattr(primary, 'model', 'unknown')), tools=tools, signature_store=self.signature_store, require_signatures=self.require_signatures)
        call_kwargs = dict(llm=self, chat_ctx=chat_ctx, tools=tools or [], conn_options=conn_options, parallel_tool_calls=parallel_tool_calls, tool_choice=tool_choice, extra_kwargs=extra_kwargs)
        return HistoryAwareFallbackStream(inspection=inspection, **call_kwargs)

    def record_request(self, inspection: HistoryInspection, *, child, retry_index: int, failure_class: FailureClass | None, dispatched: bool = True) -> None:
        if self.telemetry is None:
            return
        if dispatched:
            self.telemetry.increment('provider_calls')
        if failure_class == STRUCTURAL:
            self.telemetry.increment('structural_errors')
        elif failure_class == TRANSIENT:
            self.telemetry.increment('transient_retries')
        self.telemetry.mark('provider_request', provider=str(getattr(child, 'provider', 'unknown')), model=str(getattr(child, 'model', 'unknown')), request_fingerprint=inspection.request_fingerprint, failure_class=failure_class or 'none', retry_index=retry_index, dispatched=dispatched, function_calls=inspection.function_calls, missing_signatures=inspection.missing_signatures, missing_outputs=inspection.missing_outputs, orphan_outputs=inspection.orphan_outputs)


class HistoryAwareFallbackStream(FallbackLLMStream):
    def __init__(self, *, inspection: HistoryInspection, **kwargs) -> None:
        self._inspection = inspection
        super().__init__(**kwargs)

    async def _run(self) -> None:
        adapter = self._fallback_adapter
        fingerprint = self._inspection.request_fingerprint
        if not self._inspection.complete:
            adapter.structural_fingerprints.add(fingerprint)
            adapter.record_request(self._inspection, child=adapter._llm_instances[0], retry_index=0, failure_class=STRUCTURAL, dispatched=False)
            raise ProviderRequestRejected('provider-native tool history is incomplete', failure_class=STRUCTURAL, request_fingerprint=fingerprint, local=True, history_counts={'function_calls': self._inspection.function_calls, 'missing_signatures': self._inspection.missing_signatures, 'missing_outputs': self._inspection.missing_outputs, 'orphan_outputs': self._inspection.orphan_outputs})
        if fingerprint in adapter.structural_fingerprints:
            raise ProviderRequestRejected('structurally failed provider request cannot be replayed', failure_class=STRUCTURAL, request_fingerprint=fingerprint, local=True)
        start_time = time.time()
        all_failed = all((not status.available for status in adapter._status))
        for index, child in enumerate(adapter._llm_instances):
            child_status = adapter._status[index]
            if not child_status.available and not all_failed:
                self._try_recovery(child)
                continue
            text_sent = ''
            tool_calls_sent = []
            try:
                async for result in self._try_generate(llm=child, check_recovery=False):
                    if result.delta:
                        text_sent += result.delta.content or ''
                        tool_calls_sent.extend((call.name for call in result.delta.tool_calls))
                    self._event_ch.send_nowait(result)
                adapter.record_request(self._inspection, child=child, retry_index=index, failure_class=None)
                return
            except Exception as error:
                failure_class = classify_provider_error(error)
                adapter.record_request(self._inspection, child=child, retry_index=index, failure_class=failure_class)
                if failure_class != TRANSIENT:
                    if failure_class == STRUCTURAL:
                        adapter.structural_fingerprints.add(fingerprint)
                    raise ProviderRequestRejected('provider request rejected without fallback replay', failure_class=failure_class, request_fingerprint=fingerprint) from error
                if child_status.available:
                    child_status.available = False
                    adapter.emit('llm_availability_changed', AvailabilityChangedEvent(llm=child, available=False))
                if (text_sent or tool_calls_sent) and not adapter._retry_on_chunk_sent:
                    raise
                self._try_recovery(child)
                continue
        raise APIConnectionError(f"all LLMs failed after {time.time() - start_time:.2f} seconds")