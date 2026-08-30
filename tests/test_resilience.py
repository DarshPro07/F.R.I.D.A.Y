"""
When the model returns nothing.

Gemini answers `finish_reason=STOP` with no content, LiveKit retries, and
usually the second attempt is fine. The failure that matters is the other one:
the tool already ran, the retries were spent, and the boss is left in silence
after his music started playing.

The rule underneath every test here: a rescue may never claim something an
ActionResult does not support. Silence is better than a lie.
"""

from __future__ import annotations

import json

import pytest

from friday import resilience as R


class FakeAPIError(Exception):
    def __init__(self, message, body=""):
        super().__init__(message)
        self.body = body


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def test_the_empty_completion_is_recognised():
    error = FakeAPIError("no response generated", body="finish reason: FinishReason.STOP")
    assert R.is_empty_completion(error)


def test_the_exhausted_form_is_recognised_too():
    """The wrapper LiveKit raises once the retries are gone."""
    assert R.is_empty_completion(
        FakeAPIError("failed to generate LLM completion after 4 attempts"))


def test_a_blocked_generation_is_not_an_empty_completion():
    """
    SAFETY or RECITATION is a different problem with a different answer, and
    must not be papered over as a hiccup.
    """
    assert not R.is_empty_completion(
        FakeAPIError("no response generated", body="finish reason: FinishReason.SAFETY"))


def test_a_real_api_failure_is_not_an_empty_completion():
    assert not R.is_empty_completion(FakeAPIError("gemini llm: server error", body="503"))
    assert not R.is_empty_completion(TimeoutError("timed out"))


# ---------------------------------------------------------------------------
# Reading what happened
# ---------------------------------------------------------------------------


def mcp_result(**body) -> str:
    """A tool result in the shape LiveKit's resolver actually produces."""
    return json.dumps({"type": "text", "text": json.dumps(body)})


def test_a_succeeded_tool_is_read_as_succeeded():
    outcome = R.read_outcome("music_play", mcp_result(
        status="succeeded", may_claim_completion=True))
    assert outcome.succeeded


def test_succeeded_without_permission_to_claim_is_not_claimable():
    """
    may_claim_completion is the proof layer's answer, and it outranks the
    status string. If it says no, nothing here says "done".
    """
    outcome = R.read_outcome("music_play", mcp_result(
        status="succeeded", may_claim_completion=False))
    assert not outcome.succeeded


def test_partial_is_not_success():
    assert not R.read_outcome("x", mcp_result(status="partial",
                                              may_claim_completion=False)).succeeded


def test_an_unreadable_result_is_unproven_not_assumed():
    assert not R.read_outcome("x", "who knows").succeeded
    assert not R.read_outcome("x", None).succeeded
    assert R.read_outcome("x", None).status == "unknown"


def test_a_plain_json_result_is_read_too():
    """Not every tool result arrives wrapped in TextContent."""
    outcome = R.read_outcome("x", json.dumps(
        {"status": "succeeded", "may_claim_completion": True}))
    assert outcome.succeeded


# ---------------------------------------------------------------------------
# The line
# ---------------------------------------------------------------------------


def ok(name="t"):
    return R.ToolOutcome(name, "succeeded", True)


def bad(name="t"):
    return R.ToolOutcome(name, "failed", False)


def test_everything_worked_says_so():
    assert R.narrate([ok(), ok()]) in R.DONE


def test_some_of_it_worked_says_that_instead():
    line = R.narrate([ok(), bad()])
    assert line in R.PARTLY
    assert line not in R.DONE


def test_nothing_worked_never_says_done():
    assert R.narrate([bad(), bad()]) in R.BROKEN


def test_no_tools_at_all_is_treated_as_a_broken_turn():
    assert R.narrate([]) in R.BROKEN


def test_no_line_ever_names_a_tool():
    """CRITICAL RULE 1 of the prompt: she never says a tool name. Ever."""
    for line in R.DONE + R.PARTLY + R.BROKEN:
        assert "_" not in line, f"{line!r} looks like it contains a tool name"


def test_the_line_varies_so_it_does_not_sound_like_a_recording():
    lines = {R.narrate([ok()], turn=i) for i in range(len(R.DONE))}
    assert len(lines) == len(R.DONE)


# ---------------------------------------------------------------------------
# The guard
# ---------------------------------------------------------------------------


class FakeCall:
    def __init__(self, name):
        self.name = name


class FakeOutput:
    def __init__(self, output):
        self.output = output


class FakeToolsExecuted:
    def __init__(self, pairs):
        self._pairs = pairs

    def zipped(self):
        return self._pairs


class FakeLLMError:
    def __init__(self, error, recoverable):
        self.error = error
        self.recoverable = recoverable


class FakeErrorEvent:
    def __init__(self, error, recoverable=False):
        self.error = FakeLLMError(error, recoverable)


class FakeItem:
    def __init__(self, role, text):
        self.role = role
        self.text_content = text


class FakeItemAdded:
    def __init__(self, item):
        self.item = item


class FakeSession:
    def __init__(self):
        self.said = []
        self.handlers = {}

    def on(self, name, handler):
        self.handlers[name] = handler

    def say(self, text):
        self.said.append(text)


@pytest.fixture
def attached():
    session = FakeSession()
    guard = R.TurnGuard()
    guard.attach(session)
    return guard, session


def tools_ran(guard, *outputs):
    guard.on_tools_executed(FakeToolsExecuted([
        (FakeCall(f"tool_{i}"), FakeOutput(out)) for i, out in enumerate(outputs)
    ]))


def empty(recoverable=False):
    return FakeErrorEvent(
        FakeAPIError("no response generated", body="finish reason: FinishReason.STOP"),
        recoverable=recoverable)


def test_the_boss_is_not_left_in_silence_after_something_worked(attached):
    guard, session = attached
    tools_ran(guard, mcp_result(status="succeeded", may_claim_completion=True))
    guard.on_error(empty())
    assert session.said and session.said[0] in R.DONE
    assert guard.rescued == 1


def test_a_recoverable_failure_is_counted_but_not_spoken_over(attached):
    """LiveKit is about to try again. Talking over it would double the reply."""
    guard, session = attached
    tools_ran(guard, mcp_result(status="succeeded", may_claim_completion=True))
    guard.on_error(empty(recoverable=True))
    assert session.said == []
    assert guard.empty_completions == 1
    assert guard.recovered == 1


def test_a_turn_that_spoke_for_itself_is_never_rescued(attached):
    guard, session = attached
    tools_ran(guard, mcp_result(status="succeeded", may_claim_completion=True))
    guard.on_item_added(FakeItemAdded(FakeItem("assistant", "Music's on, boss.")))
    guard.on_error(empty())
    assert session.said == []


def test_an_empty_turn_with_no_tool_behind_it_stays_quiet(attached):
    """
    A hiccup with nothing riding on it is something he can simply repeat. A
    canned apology for every blip is its own kind of noise.
    """
    guard, session = attached
    guard.on_error(empty())
    assert session.said == []
    assert guard.empty_completions == 1
    assert guard.rescued == 0


def test_a_failed_tool_is_never_narrated_as_done(attached):
    guard, session = attached
    tools_ran(guard, mcp_result(status="failed", may_claim_completion=False))
    guard.on_error(empty())
    assert session.said[0] in R.BROKEN
    assert session.said[0] not in R.DONE


def test_a_real_api_error_is_not_rescued(attached):
    """A 503 is not a hiccup, and pretending it went fine would be a lie."""
    guard, session = attached
    tools_ran(guard, mcp_result(status="succeeded", may_claim_completion=True))
    guard.on_error(FakeErrorEvent(FakeAPIError("gemini llm: server error", body="503")))
    assert session.said == []


def test_one_failure_is_rescued_once(attached):
    guard, session = attached
    tools_ran(guard, mcp_result(status="succeeded", may_claim_completion=True))
    guard.on_error(empty())
    guard.on_error(empty())
    assert len(session.said) == 1


def test_the_guard_counts_what_it_saw(attached):
    guard, _ = attached
    tools_ran(guard, mcp_result(status="succeeded", may_claim_completion=True))
    guard.on_error(empty(recoverable=True))
    guard.on_error(empty())
    assert guard.describe() == {
        "empty_completions": 2, "recovered_by_retry": 1,
        "rescued_by_narration": 1, "pending_outcomes": 0,
    }


def test_a_broken_tool_reader_never_breaks_the_session(attached):
    guard, session = attached

    class Exploding:
        def zipped(self):
            raise RuntimeError("nope")

    guard.on_tools_executed(Exploding())  # no exception escapes
    assert guard.pending == []


def test_a_session_that_cannot_speak_does_not_crash_the_guard(attached):
    guard, session = attached

    def boom(text):
        raise RuntimeError("no audio output")

    session.say = boom
    tools_ran(guard, mcp_result(status="succeeded", may_claim_completion=True))
    assert guard.rescue() is None


# ---------------------------------------------------------------------------
# The fallback model
# ---------------------------------------------------------------------------


def test_fallback_is_on_by_default(monkeypatch):
    from friday import providers

    monkeypatch.delenv("ADA_LLM_FALLBACK", raising=False)
    assert providers.fallback_role("NORMAL") == "DEEP"


def test_fallback_can_be_turned_off(monkeypatch):
    from friday import providers

    monkeypatch.setenv("ADA_LLM_FALLBACK", "off")
    assert providers.fallback_role("NORMAL") is None


def test_falling_back_to_the_model_that_just_failed_is_pointless(monkeypatch):
    from friday import providers

    monkeypatch.setenv("ADA_LLM_FALLBACK", "DEEP")
    assert providers.fallback_role("DEEP") is None


def test_an_unknown_fallback_role_is_ignored_not_fatal(monkeypatch):
    from friday import providers

    monkeypatch.setenv("ADA_LLM_FALLBACK", "TURBO")
    assert providers.fallback_role("NORMAL") is None


def test_the_attempt_timeout_is_not_the_five_second_default():
    """
    FallbackAdapter defaults to 5s per attempt. A long but perfectly healthy
    generation would be cut off by that - a regression dressed as a fix.
    """
    from friday import providers

    assert providers.FALLBACK_ATTEMPT_TIMEOUT >= 20


def test_retries_stay_with_the_primary_before_falling_back():
    """So this is a net under current behaviour, not a change to it."""
    from friday import providers

    assert providers.FALLBACK_RETRIES_PER_MODEL >= 1


# ---------------------------------------------------------------------------
# The fallback has to be able to finish what the primary started
# ---------------------------------------------------------------------------


class FakeGemini:
    """Only the attribute the plugin actually keeps signatures in."""

    def __init__(self) -> None:
        self._thought_signatures: dict[str, bytes] = {}


def test_both_models_end_up_holding_the_same_signatures():
    """
    Gemini 2.5 and 3 require every function-call part in a multi-turn tool
    conversation to carry back the thought_signature it was produced with. The
    plugin stores those per LLM INSTANCE, and a FallbackAdapter holds two - so
    the moment the second one takes over a conversation the first was having,
    it rebuilds a request whose earlier calls have no signatures and Gemini
    answers 400, retryable=False. Both models exhaust, and the turn dies as
    "failed to generate LLM completion".
    """
    from friday import providers

    primary, secondary = FakeGemini(), FakeGemini()
    assert providers._share_thought_signatures(primary, secondary)

    primary._thought_signatures["call-1"] = b"signature"
    assert secondary._thought_signatures["call-1"] == b"signature", \
        "the fallback cannot finish a conversation the primary started"
    assert primary._thought_signatures is secondary._thought_signatures


def test_a_plugin_that_renames_the_attribute_degrades_rather_than_crashing():
    """This reaches into a private attribute, so it has to fail softly."""
    from friday import providers

    class Renamed:
        pass

    assert not providers._share_thought_signatures(Renamed(), Renamed())
    assert not providers._share_thought_signatures(FakeGemini(), Renamed())


def test_the_models_we_actually_run_all_need_signatures():
    """
    Measured against the installed plugin rather than assumed: if a model in
    our role table stops requiring them - or starts - this is where it shows.
    """
    from livekit.plugins.google.llm import _requires_thought_signatures

    from friday import providers

    for model in providers.LLM_ROLE_MODELS["google"].values():
        assert _requires_thought_signatures(model), model
