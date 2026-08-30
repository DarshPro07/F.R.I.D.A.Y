"""
InputArbiter classification: new input while a run is active must be routed,
never destructive. A status question must not cancel the run; a side
conversation must not touch it; only an explicit stop cancels it.
"""
from __future__ import annotations
import pytest
from friday.arbiter import INTENTS, classify_input


@pytest.mark.parametrize("text", [
    "stop",
    "cancel",
    "cancel that",
    "stop it now",
    "abort",
    "forget it",
    "never mind",
    "enough of that",
    "stop the run please",
])
def test_cancel_phrases(text: str):
    assert classify_input(text) == "CANCEL"


@pytest.mark.parametrize("text", [
    "what's the status",
    "how's it going",
    "how is it going",
    "how far along are you",
    "where are you up to",
    "what's happening with that run",
    "progress report",
    "how's the objective going",
])
def test_query_phrases(text: str):
    assert classify_input(text) == "QUERY_ABOUT_RUN"


@pytest.mark.parametrize("text", [
    "what time is it",
    "tell me a joke",
    "open spotify",
    "who won the match",
    "remind me to buy milk tomorrow",
])
def test_side_conversation(text: str):
    assert classify_input(text) == "SIDE_CONVERSATION"


@pytest.mark.parametrize("text", [
    "change the report to use PDF",
    "instead of files, use a spreadsheet",
    "skip the world monitor step",
    "don't open Paint, open Notepad",
    "redo the summary with more detail",
])
def test_modification_phrases(text: str):
    assert classify_input(text) == "MODIFICATION"


@pytest.mark.parametrize("text", [
    "check my system health and give me a report",
    "research quantum computing for me",
    "run the demo objective",
    "set up a new workspace with two files",
])
def test_new_objective_phrases(text: str):
    assert classify_input(text) == "NEW_OBJECTIVE"


def test_intent_vocabulary():
    assert set(INTENTS) == {"CANCEL", "QUERY_ABOUT_RUN", "SIDE_CONVERSATION",
                            "MODIFICATION", "NEW_OBJECTIVE"}


def test_cancel_wins_over_query():
    """Ambiguity must favour stopping, never accidentally cancelling."""
    assert classify_input("cancel and what's the status") == "CANCEL"
LONG_REQUEST = 'Friday, perform a complete real capability audit of yourself from start to finish. Treat this as one durable audit objective and do not wait for me to say continue. First, read your live capability registry and build the audit from what is actually registered right now. Also test the control plane itself. During this same audit, query your own status, modify one not-yet-started audit task, skip another harmless pending audit task if appropriate, and prove the graph version changed. Do not stop because a model turn ended. Do not ask me whether to continue.'


def test_a_long_request_is_not_a_cancellation():
    """
    The failure this guards, exactly as it happened.

    Every control pattern matches somewhere in this text - "stop", "status",
    "modify", "skip" - and CANCEL matched first, so a request to audit the
    entire system was read as an instruction to stop. There was no run to
    stop, so it became a side conversation: nothing admitted, nothing claimed,
    and the whole audit ran in the conversational loop until the provider
    gave out.
    """
    assert len(LONG_REQUEST.split()) > 80, 'the excerpt shrank below the length bound it is testing'
    assert classify_input(LONG_REQUEST) == 'NEW_OBJECTIVE', 'a long request was read as a control utterance because it mentions one'


@pytest.mark.parametrize('phrase', ['do not stop because a model turn ended', "don't skip anything", 'never cancel the run', 'finish it without stopping'])
def test_a_negated_control_word_is_not_that_control(phrase):
    """"Do not stop" is not "stop", and reading it as one inverts the order."""
    assert classify_input(phrase) != 'CANCEL', f"{phrase!r} was read as a stop"


@pytest.mark.parametrize('phrase, expected', [('stop', 'CANCEL'), ('cancel that', 'CANCEL'), ('never mind', 'CANCEL'), ('how far are you', 'QUERY_ABOUT_RUN'), ('what is the status', 'QUERY_ABOUT_RUN'), ('skip the world monitor part', 'MODIFICATION'), ('use BBC instead for the research', 'MODIFICATION')])
def test_short_control_utterances_still_work(phrase, expected):
    """
    The bound must not cost the thing it protects. These are what a person
    actually says while work is running, and every one is well inside it.
    """
    assert classify_input(phrase) == expected


def test_the_length_bounds_leave_room_for_real_speech():
    """
    Sized against real utterances rather than picked round. A spoken
    modification carries the thing it modifies - "use the BBC feed instead of
    Reuters for the research part" - and has to fit.
    """
    from friday import arbiter
    spoken = 'use the BBC feed instead of Reuters for the research part please'
    assert len(spoken.split()) < arbiter._MODIFICATION_WORDS
    assert classify_input(spoken) == 'MODIFICATION'
