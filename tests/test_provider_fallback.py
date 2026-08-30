from __future__ import annotations
import asyncio
import pytest
from livekit.agents import APIConnectionError, APIStatusError
from livekit.agents.llm import LLM
from livekit.agents.llm.chat_context import ChatContext, FunctionCall, FunctionCallOutput
from friday.provider_fallback import CONTENT, STRUCTURAL, TRANSIENT, UNKNOWN, HistoryAwareFallback, ProviderRequestRejected, classify_provider_error, inspect_native_history, provider_request_fingerprint


def history() -> tuple[ChatContext, str]:
    chat_ctx = ChatContext.empty()
    chat_ctx.add_message(role='user', content='private user content')
    call_id = 'call-private-1'
    chat_ctx.items.extend([FunctionCall(call_id=call_id, name='probe_value', arguments='{"label":"secret"}'), FunctionCallOutput(call_id=call_id, name='probe_value', output='private result', is_error=False)])
    return (chat_ctx, call_id)


def test_provider_error_classes_are_objective():
    assert classify_provider_error(APIStatusError('function call is missing a thought_signature', status_code=400, retryable=False)) == STRUCTURAL
    assert classify_provider_error(APIConnectionError('connection reset')) == TRANSIENT
    assert classify_provider_error(APIStatusError('blocked for safety', status_code=400, retryable=False)) == CONTENT
    assert classify_provider_error(ValueError('unrecognized failure')) == UNKNOWN


def test_request_fingerprint_contains_no_content_or_signature_bytes():
    chat_ctx, call_id = history()
    signature_store = {call_id: b'opaque-provider-signature'}
    fingerprint = provider_request_fingerprint(chat_ctx, 'google', 'gemini-test', signature_store=signature_store)
    assert len(fingerprint) == 64
    assert 'private' not in fingerprint
    assert 'opaque' not in fingerprint


def test_native_history_requires_original_signature_and_matching_output():
    chat_ctx, call_id = history()
    incomplete = inspect_native_history(chat_ctx, 'google', 'gemini-test', signature_store={}, require_signatures=True)
    complete = inspect_native_history(chat_ctx, 'google', 'gemini-test', signature_store={call_id: b'opaque'}, require_signatures=True)
    assert incomplete.complete is False
    assert incomplete.missing_signatures == 1
    assert complete.complete is True


class StubStream:
    def __init__(self, owner, chat_ctx, tools, error):
        self.owner = owner
        self.chat_ctx = chat_ctx
        self.tools = tools
        self.error = error
        self.finished = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        pass

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.finished:
            raise StopAsyncIteration
        self.finished = True
        if self.error is not None:
            raise self.error
        raise StopAsyncIteration


class StubLLM(LLM):
    def __init__(self, *, error=None, model='gemini-test'):
        super().__init__()
        self.error = error
        self.calls = 0
        self._model = model
        self._thought_signatures = {}

    @property
    def provider(self):
        return 'google'

    @property
    def model(self):
        return self._model

    def chat(self, *, chat_ctx, tools=None, **kwargs):
        self.calls += 1
        return StubStream(self, chat_ctx, tools or [], self.error)

    async def aclose(self):
        pass


async def consume(stream):
    async with stream:
        return [chunk async for chunk in stream]


async def consume_chat(adapter, chat_ctx):
    return await consume(adapter.chat(chat_ctx=chat_ctx))


def test_incomplete_history_is_rejected_before_provider_dispatch():
    chat_ctx, _call_id = history()
    primary, fallback = StubLLM(), StubLLM(model='gemini-fallback')
    adapter = HistoryAwareFallback([primary, fallback], signature_store={}, require_signatures=True)
    with pytest.raises(ProviderRequestRejected) as rejected:
        asyncio.run(consume_chat(adapter, chat_ctx))
    assert rejected.value.failure_class == STRUCTURAL
    assert rejected.value.local is True
    assert rejected.value.history_counts == {'function_calls': 1, 'missing_signatures': 1, 'missing_outputs': 0, 'orphan_outputs': 0}
    assert 'private' not in str(rejected.value)
    assert 'opaque' not in str(rejected.value)
    assert primary.calls == fallback.calls == 0


def test_complete_history_can_fallback_unchanged_after_transient_failure():
    chat_ctx, call_id = history()
    signatures = {call_id: b'opaque'}
    primary = StubLLM(error=APIConnectionError('temporary outage'))
    fallback = StubLLM(model='gemini-fallback')
    adapter = HistoryAwareFallback([primary, fallback], signature_store=signatures, require_signatures=True)
    assert asyncio.run(consume_chat(adapter, chat_ctx)) == []
    assert primary.calls >= 1
    assert fallback.calls == 1


def test_structural_provider_response_is_not_sent_to_fallback_or_replayed():
    chat_ctx, call_id = history()
    primary = StubLLM(error=APIStatusError('function call is missing a thought_signature', status_code=400, retryable=False))
    fallback = StubLLM(model='gemini-fallback')
    adapter = HistoryAwareFallback([primary, fallback], signature_store={call_id: b'opaque'}, require_signatures=True)
    with pytest.raises(ProviderRequestRejected):
        asyncio.run(consume_chat(adapter, chat_ctx))
    with pytest.raises(ProviderRequestRejected):
        asyncio.run(consume_chat(adapter, chat_ctx))
    assert primary.calls == 1
    assert fallback.calls == 0
    assert len(adapter.structural_fingerprints) == 1