"""
Provider failures, told apart.

Every case here is a shape actually observed against the installed stack
(google-genai 1.70.0, livekit-plugins-google 1.5.1) during the provider
root-cause investigation, not an invented one. The distinction that matters is
whether retrying could possibly help:

    a 504 DEADLINE_EXCEEDED          retry; it was a bad moment
    a missing thought_signature      do not; the request is missing it on the
                                     second attempt too

Both used to arrive as "failed to generate llm completion" and be retried the
same number of times.
"""

from __future__ import annotations

import pytest

from friday import provider_diagnostics as D


class FakeAPIError(Exception):
    """The shape livekit-plugins-google raises."""

    def __init__(self, message, *, body="", status_code=None):
        super().__init__(message)
        self.body = body
        self.status_code = status_code


# ---------------------------------------------------------------------------
# Structural: retrying cannot help
# ---------------------------------------------------------------------------


def test_a_missing_thought_signature_is_structural():
    """
    Observed verbatim from gemini-3-flash-preview when history is rebuilt
    without the signature Gemini returned. gemini-2.5-flash accepts the same
    history, which is why this only bites on the fallback model.
    """
    error = FakeAPIError(
        "gemini llm: client error", status_code=400,
        body="Function call is missing a thought_signature in functionCall "
             "parts. This is required for tools to work correctly INVALID_ARGUMENT")

    found = D.diagnose(error)
    assert found.kind == D.STRUCTURAL
    assert not found.worth_retrying, \
        "a request missing a signature is missing it on the retry too"
    assert "thought signature" in found.detail


def test_an_unexpected_tool_call_is_structural():
    error = FakeAPIError("no response generated",
                         body="finish reason: FinishReason.UNEXPECTED_TOOL_CALL")

    found = D.diagnose(error)
    assert found.kind == D.STRUCTURAL
    assert found.finish_reason == "UNEXPECTED_TOOL_CALL"
    assert not found.worth_retrying


def test_a_malformed_function_call_is_structural():
    error = FakeAPIError("no response generated",
                         body="finish reason: MALFORMED_FUNCTION_CALL")
    assert D.diagnose(error).kind == D.STRUCTURAL


# ---------------------------------------------------------------------------
# Transient: retrying is the right answer
# ---------------------------------------------------------------------------


def test_a_deadline_exceeded_is_transient():
    """Observed once during the LiveKit probes; the retry succeeded."""
    error = FakeAPIError(
        "gemini llm: server error", status_code=504,
        body="Deadline expired before operation could complete. DEADLINE_EXCEEDED")

    found = D.diagnose(error)
    assert found.kind == D.TRANSIENT
    assert found.worth_retrying


@pytest.mark.parametrize("status", [429, 500, 503, 504])
def test_server_and_rate_limit_codes_are_transient(status):
    assert D.diagnose(
        FakeAPIError("gemini llm: server error", status_code=status)
    ).kind == D.TRANSIENT


# ---------------------------------------------------------------------------
# No content: a candidate arrived carrying nothing
# ---------------------------------------------------------------------------


def test_an_empty_stop_is_no_content_not_structural():
    """
    The original symptom. A normal STOP with no parts is the empty completion
    `friday/resilience.py` already handles - worth retrying, and NOT the same
    thing as a tool-protocol failure.
    """
    error = FakeAPIError("no response generated",
                         body="finish reason: FinishReason.STOP")

    found = D.diagnose(error)
    assert found.kind == D.NO_CONTENT
    assert found.finish_reason == "STOP"
    assert found.worth_retrying


def test_a_truncated_answer_says_it_was_truncated():
    found = D.diagnose(FakeAPIError("no response generated",
                                    body="finish reason: MAX_TOKENS"))
    assert found.kind == D.NO_CONTENT
    assert "cut off" in found.detail


# ---------------------------------------------------------------------------
# Safety
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("reason", ["SAFETY", "RECITATION", "PROHIBITED_CONTENT"])
def test_a_blocked_generation_is_not_a_hiccup(reason):
    found = D.diagnose(FakeAPIError("generation blocked by gemini",
                                    body=f"finish reason: {reason}"))
    assert found.kind == D.SAFETY
    assert not found.worth_retrying, "a blocked generation must not be retried into"


# ---------------------------------------------------------------------------
# The point of the whole module
# ---------------------------------------------------------------------------


def test_the_same_sentence_produces_different_diagnoses():
    """
    "no response generated" is the plugin's one sentence for four unrelated
    failures. If this test ever passes trivially - every case landing in the
    same bucket - the classification has stopped doing anything.
    """
    same_sentence = [
        FakeAPIError("no response generated", body="finish reason: STOP"),
        FakeAPIError("no response generated",
                     body="finish reason: UNEXPECTED_TOOL_CALL"),
        FakeAPIError("no response generated", body="finish reason: SAFETY"),
        FakeAPIError("no response generated", body="finish reason: MAX_TOKENS"),
    ]
    kinds = {D.diagnose(e).kind for e in same_sentence}
    assert len(kinds) >= 3, \
        f"four different failures collapsed into {kinds}"


def test_the_description_names_a_cause_not_a_symptom():
    line = D.describe_failure(
        FakeAPIError("no response generated",
                     body="finish reason: UNEXPECTED_TOOL_CALL"))
    assert "UNEXPECTED_TOOL_CALL" in line
    assert line != "no response generated"


def test_nothing_secret_reaches_the_description():
    """Signature presence is a diagnostic; signature bytes are not."""
    error = FakeAPIError("gemini llm: client error", status_code=400,
                         body="thought_signature ABCDEFhiddenbytes INVALID_ARGUMENT")
    line = D.describe_failure(error)
    assert "ABCDEFhiddenbytes" not in line


# ---------------------------------------------------------------------------
# Not asking the same impossible question twice
# ---------------------------------------------------------------------------


def test_the_same_request_shape_fingerprints_the_same():
    shape = dict(model="gemini-2.5-flash", tool_count=125,
                 tool_choice="auto", history_length=4, last_role="user")
    assert D.request_fingerprint(**shape) == D.request_fingerprint(**shape)


def test_a_different_tool_configuration_is_a_different_request():
    base = dict(model="gemini-2.5-flash", tool_count=125,
                tool_choice="auto", history_length=4)
    assert (D.request_fingerprint(**base)
            != D.request_fingerprint(**{**base, "tool_choice": "none"})), \
        "the max_tool_steps final request must not look like the one before it"
    assert (D.request_fingerprint(**base)
            != D.request_fingerprint(**{**base, "tool_count": 0}))


def test_the_fingerprint_carries_no_prompt():
    """
    Deliberately structural. Two turns of one conversation differ in content
    and share a shape, and shape is what a structural failure is about.
    """
    import inspect

    source = inspect.getsource(D.request_fingerprint)
    assert "prompt" not in source.lower().split("deliberately")[0]
