"""
A router that would rather say nothing than say the wrong thing.

The deterministic router handled 23.7% of requests in 16ms and got 13.6% of
them wrong, two dangerously. Those are not the numbers of a router that needs
tuning; they are the numbers of a router that was never allowed to decline.

What is proved here is the declining. Every rule below exists because a
measured request got past its absence:

    "should I restart my PC?"        a question that names an action
    "what if I shut down?"           a hypothetical that names an action
    "don't close Chrome"             a sentence whose only content is a refusal
    "minimise the browser"           `browser_close` won by being alone
    "restart the computer"           a read won on a shared domain word
    "it is frozen, end it"           end what?
    "open Chrome and find the news"  two outcomes answered with one

Nothing here needs a model, a corpus or a network. The gate is pure, because
the gate is the part that has to be right.
"""
from __future__ import annotations
import pytest
from friday import request_shape as RS
from friday import selective as SEL


def decide(text, **active):
    return SEL.decide(text, context=SEL.Context(active=dict(active)))


def test_a_decision_routes_or_abstains_never_both():
    for text in ("pause the music", "why is my computer slow", "restart it"):
        decision = decide(text)
        assert decision.routes != bool(decision.abstained), text


def test_every_abstention_names_a_reason_and_a_place_to_look():
    """
    §25: failures are classified so fixes stay mechanical. An abstention with
    no blame is a dead end for whoever tries to improve coverage later.
    """
    for text in ('why is my computer slow', 'restart it', 'should I restart my PC', 'open Chrome and find the news', 'how do I close a window'):
        decision = decide(text)
        assert decision.abstained in SEL.ABSTENTIONS, text
        assert decision.blame in SEL.TAXONOMY, (text, decision.abstained)


def test_uncertainty_is_kept_per_facet():
    """
    §10. A strong operation with an unreadable target is not a middling
    score, it is certainty about the verb and ignorance of the object.
    """
    decision = decide("pause the music")
    assert decision.evidence.operation == "CONTROL"
    assert decision.evidence.target == "MEDIA"
    assert decision.evidence.positive, "nothing pointed at the winner"


@pytest.mark.parametrize("text", [
    "should I restart my PC",
    "should I close Chrome",
    "could stopping Chrome fix this",
    "would closing the browser help",
    "what if I shut down the computer",
    "suppose I deleted that file",
    "is it worth restarting the computer",
    "what would happen if the music stopped",
])
def test_a_question_about_an_action_never_becomes_one(text):
    decision = decide(text)
    assert not decision.routes, f"{text!r} -> {decision.capability}"
    assert decision.abstained in (SEL.REASONING_REQUIRED, SEL.OUT_OF_DOMAIN)


@pytest.mark.parametrize('text', ['how do I close a window', 'can you delete files', 'are you able to close Chrome', 'is it safe to delete that file'])
def test_asking_about_an_action_is_not_asking_for_it(text):
    """
    Narrowed deliberately. "What is playing" and "what windows are open" used
    to be here, because every question abstained - and that threw away the
    whole class of questions that are plain requests for state. They route
    now; see `test_a_question_asking_for_state_is_answered`.
    """
    assert not decide(text).routes, text


@pytest.mark.parametrize("text", [
    "why is my computer slow",
    "research OpenAI",
    "compare Godot and Unreal",
    "design a game for me",
    "explain this error",
    "what should I do here",
    "recommend a database",
])
def test_thinking_is_never_a_reflex(text):
    decision = decide(text)
    assert not decision.routes, text
    assert decision.abstained in (SEL.REASONING_REQUIRED, SEL.OUT_OF_DOMAIN)


@pytest.mark.parametrize("text", [
    "don't close Chrome",
    "do not stop the music",
    "never restart my computer",
    "don't delete anything",
    "no need to open Paint",
    "don't pause it",
])
def test_a_sentence_that_only_forbids_produces_nothing(text):
    decision = decide(text)
    assert not decision.routes, f"{text!r} -> {decision.capability}"


def test_the_cue_is_found():
    assert RS.forbidden_spans("don't close Chrome")
    assert RS.forbidden_spans("do not stop the music")
    assert RS.forbidden_spans("anything except restart")
    assert not RS.forbidden_spans("close Chrome")


def test_the_cue_reaches_only_to_the_clause_boundary():
    """
    The half a single regex cannot do. "Don't close Chrome, open Paint" is
    one prohibition and one request, and treating it as a blanket refusal
    would be as wrong as ignoring the negation entirely.
    """
    spans = RS.forbidden_spans("don't close Chrome, open Paint instead")
    assert spans == ("close Chrome",)
    assert RS.is_forbidden("don't close Chrome, open Paint instead", ["close"])
    assert not RS.is_forbidden("don't close Chrome, open Paint instead",
                               ["open"])


def test_a_broken_cue_would_fail_a_scoping_test_not_go_quiet():
    """
    The regression guard for the incident above. If the cue pattern stops
    matching, this fails loudly rather than the suite passing with a filter
    that does nothing.
    """
    assert RS._NEGATION_CUE.search("do not")
    assert RS._NEGATION_CUE.search("don't")
    assert RS._NEGATION_CUE.search("never")
    assert not RS._NEGATION_CUE.search("do")


def test_a_pronoun_with_nothing_behind_it_abstains():
    for text in ("restart it", "close it", "delete it", "stop it"):
        decision = decide(text)
        assert not decision.routes, f"{text!r} -> {decision.capability}"


def test_a_referent_this_conversation_established_grounds_it():
    """§4. "Pause it" after "play some music" means the music."""
    decision = decide("pause it", MEDIA="daft punk")
    assert decision.routes
    assert decision.capability == "music_pause"
    assert decision.evidence.referent_grounded


def test_two_live_things_make_the_referent_ambiguous_again():
    decision = decide("pause it", MEDIA="daft punk", APPLICATION="Paint")
    assert not decision.routes or decision.capability.startswith("music_")


def test_a_destructive_referent_is_never_guessed():
    """
    The rule with no exception. `files_recycle` may be perfectly identified
    for "delete it" and it is still not run, because "it" was never said.
    """
    for text in ("delete it", "get rid of that", "close it"):
        assert not decide(text).routes, text


def test_being_the_only_candidate_left_is_not_evidence():
    """
    `browser_close` won "minimise the browser" by being the only BROWSER
    capability in MOVE's neighbourhood. Nothing in the sentence pointed at
    closing anything.
    """
    decision = decide("minimise the browser")
    assert not decision.routes, f"-> {decision.capability}"


def test_matching_the_domain_is_not_matching_the_request():
    """
    Measured: "restart the computer" reached `system_resource_usage`. SYSTEM
    was the right domain; nothing in the sentence pointed at reading resource
    usage, and it won the domain by ranking.
    """
    decision = decide("restart the computer")
    assert not decision.routes, f"-> {decision.capability}"


def test_an_unread_verb_is_never_answered_by_a_read():
    """
    An imperative Friday could not parse must not become an observation.
    A read is not a wrong answer to "restart the computer", it is a
    non-answer performed anyway.
    """
    decision = decide("restart the computer")
    assert decision.abstained == SEL.CONFLICTING_OPERATION


def test_a_clear_command_still_gets_through():
    """The gate has to let the right answer past, or it is just an off switch."""
    for text, wanted in (("pause the music", "music_pause"),
                         ("open Paint", "apps_open"),
                         ("show my windows", "windows_list"),
                         ("search the web for cats", "web_search")):
        decision = decide(text)
        assert decision.routes, f"{text!r} -> ABSTAIN({decision.abstained})"
        assert decision.capability == wanted, (text, decision.capability)


def test_identifying_a_capability_is_not_permission_to_run_it():
    """
    §11 and §13. The reflex may name `power_shutdown` perfectly well. It
    never executes it.
    """
    from friday import capabilities as C

    for capability in C._ALL:
        if capability.risk in ("HIGH", "IRREVERSIBLE") or \
                capability.requires_approval:
            assert not SEL.reflex_direct_allowed(capability.id), capability.id


def test_nothing_that_ends_something_is_reflex_executable():
    from friday import reflex as X

    for name in ("browser_close", "music_stop", "power_cancel",
                 "objective_cancel"):
        assert X.is_dangerous(name)
        assert not SEL.reflex_direct_allowed(name), name


def test_a_reversible_low_risk_capability_is():
    for name in ("music_pause", "apps_open", "windows_list"):
        assert SEL.reflex_direct_allowed(name), name


@pytest.mark.parametrize("text", [
    "open Chrome and find today's AI news",
    "check my computer then open Paint",
    "pause the music and tell me the time",
    "close that window, open Notepad, and turn the volume up",
])
def test_more_than_one_outcome_is_not_a_reflex(text):
    decision = decide(text)
    assert not decision.routes, f"{text!r} -> {decision.capability}"
    assert decision.abstained == SEL.COMPOUND_REQUEST


def test_it_stays_inside_the_latency_budget():
    """
    §28: median under 30ms, p95 under 75ms. The whole case for a local path
    is that it is faster than a cloud turn; gating that costs 200ms has
    argued itself out of existence.
    """
    import statistics
    import time

    texts = ["pause the music", "open Paint", "show my windows",
             "why is my computer slow", "restart it", "close the browser",
             "search the web for cats", "what is playing"]
    for text in texts:                       # warm the imports
        decide(text)

    timings = []
    for _ in range(5):
        for text in texts:
            started = time.perf_counter()
            decide(text)
            timings.append((time.perf_counter() - started) * 1000)

    timings.sort()
    median = statistics.median(timings)
    p95 = timings[int(len(timings) * 0.95) - 1]
    assert median < 30, f"median {median:.1f}ms"
    assert p95 < 75, f"p95 {p95:.1f}ms"


@pytest.mark.parametrize('text,wanted', [('can you open Paint', 'apps_open'), ('could you pause the music', 'music_pause'), ('would you pause the music', 'music_pause'), ('can you show me my windows', 'windows_list'), ('could you open Paint please', 'apps_open')])
def test_a_polite_instruction_is_still_an_instruction(text, wanted):
    decision = decide(text)
    assert decision.routes, f"{text!r} -> ABSTAIN({decision.abstained})"
    assert decision.capability == wanted


def test_the_courtesy_is_stripped_before_anything_reads_it():
    """
    Leaving it in made `could` and `you` content words: "can you open Paint"
    segmented into two goals and buried the verb, and "could you bring up
    Paint?" scored against a capability on the strength of "you".
    """
    assert RS.read('could you open Paint?').command == 'open Paint'
    assert RS.read('open Paint').command == 'open Paint'


def test_asking_whether_friday_can_is_not_asking_it_to():
    """
    The other half of the same class, and the dangerous half. "Are you able
    to shift the Notepad window" reached `windows_list` - a question about an
    ability answered by an unrelated listing, because every question infers
    READ and WINDOW was the domain.
    """
    for text in ('are you able to shift the Notepad window', 'do you know how to open Paint', 'is it safe to read the song', 'what happens when I drag my windows', 'how do I close a window', 'should I restart my PC'):
        decision = decide(text)
        assert not decision.routes, f"{text!r} -> {decision.capability}"


@pytest.mark.parametrize('text,wanted', [('what windows do I have open?', 'windows_list'), ('what is playing', 'music_current'), ('which apps are running', 'apps_list_known')])
def test_a_question_asking_for_state_is_answered(text, wanted):
    """
    Abstaining on every question threw this whole class away. Measured live:
    production answered "What windows do I have open?" by listing *processes*
    and the router had no opinion at all.
    """
    decision = decide(text)
    assert decision.routes, f"{text!r} -> ABSTAIN({decision.abstained})"
    assert decision.capability == wanted


def test_a_question_can_never_become_an_action():
    """
    The constraint that makes the above safe: only an informational operation
    may answer a question, and an informational operation cannot close,
    delete or restart anything.
    """
    from friday import semantics as S
    assert SEL.decide('what windows do I have open?').routes
    for capability in ('apps_open', 'music_pause', 'browser_close'):
        assert S.for_capability(capability)[0] not in SEL._INFORMATIONAL


def test_the_shape_gate_still_refuses_what_it_always_did():
    """The fixes widen one door. They must not open any of the others."""
    for text in ('why is my computer slow', 'research OpenAI', 'what if I shut down the computer', "don't close Chrome", 'open Chrome and find the news', 'restart it', 'delete it', 'can you delete files', 'can you close Chrome'):
        assert not decide(text).routes, text
