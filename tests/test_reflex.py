"""
The local fast path, and the gate that decides it is allowed to act.

Needle 2 is a 45M-parameter tool-calling model that runs on this machine in
about 28MB of RAM. The case for it is real - "pause the music" should not cost
a cloud reasoning turn - and so is the case against trusting it, which was
measured rather than assumed. Asked five questions with four tools declared,
the base model answered:

    "restart the song"  ->  power_restart   confidence 0.197
    "pause the music"   ->  no call at all  confidence 0.016

It proposed rebooting the machine for a media command, and scored that higher
than the trivially correct answer it missed. That is the whole reason this
file exists: **the model proposes, Friday's own semantics decide.**

Nothing here needs the model. The gate is pure, which is deliberate - it is
the half that has to be right, and a safety argument that can only be checked
by downloading a binary is not much of a safety argument.
"""
from __future__ import annotations
import pytest
from friday import capabilities as C
from friday import reflex as X


@pytest.fixture(autouse=True)
def _flag_off(monkeypatch):
    """The flag is off unless a test says otherwise, whatever the shell says."""
    monkeypatch.delenv(X.ENV_FLAG, raising=False)
    X.reset()
    yield
    X.reset()


class Proposer:
    """A stand-in model that says exactly what the test tells it to."""

    def __init__(self, name="", arguments=None, confidence=0.9,
                 kind="call", raises=None):
        self.reply = {
            "type": kind,
            "function_calls": ([{"name": name, "arguments": arguments or {}}]
                               if name else []),
            "confidence": confidence,
        }
        self.raises = raises
        self.completed: list[str] = []
        self.ran: list[str] = []

    def complete(self, text, max_new_tokens=256):
        self.completed.append(text)
        if self.raises:
            raise self.raises
        return dict(self.reply)

    def run(self, query, **kwargs):                      # pragma: no cover
        self.ran.append(query)
        raise AssertionError("route() called run(), which executes tools")


def test_the_fast_path_is_off_by_default():
    """
    The single most important line in this file. It ships disabled and stays
    disabled until `scripts/benchmark_reflex.py` reports a zero false-action
    rate on a corpus it was not tuned against.
    """
    assert not X.enabled()
    assert X.route("open Paint").escalated == X.NOT_ENABLED


def test_turning_it_on_takes_a_deliberate_act(monkeypatch):
    monkeypatch.setenv(X.ENV_FLAG, "1")
    assert X.enabled()
    monkeypatch.setenv(X.ENV_FLAG, "0")
    assert not X.enabled()


def test_a_missing_model_escalates_rather_than_failing(monkeypatch):
    monkeypatch.setenv(X.ENV_FLAG, "1")
    monkeypatch.setattr(X, "_agent", lambda: None)

    assert X.route("open Paint").escalated == X.NO_MODEL


def test_the_reboot_it_proposed_for_a_media_command_is_refused():
    """Measured, not imagined. This exact proposal came out of the model."""
    outcome = X.route("restart the song",
                      agent=Proposer("power_restart", confidence=0.197))

    assert not outcome.acts
    assert outcome.escalated == X.NEEDS_APPROVAL
    assert outcome.proposed == "power_restart", \
        "what it wanted is kept, so the refusal can be read"


def test_the_same_mistake_is_caught_where_policy_cannot_help():
    """
    `power_restart` is stopped by policy before the semantics are consulted.
    The rule that matters is the one for capabilities policy has no reason to
    stop: "restart the song" is about MEDIA, and a SYSTEM capability is not.

    The operation filter cannot do this. `for_request` reads no operation from
    "restart the song" at all - restart is one of the verbs whose operation
    lives in its object - so only the target separates them.
    """
    outcome = X.route("restart the song",
                      agent=Proposer("system_get_info", confidence=0.9))

    assert outcome.escalated == X.WRONG_TARGET


def test_a_capability_for_the_wrong_kind_of_request_is_refused():
    outcome = X.route("pause the music",
                      agent=Proposer("windows_list", confidence=0.9))
    assert outcome.escalated == X.WRONG_OPERATION


def test_nothing_a_person_would_be_asked_about_is_a_reflex():
    """
    It skipped the reasoning model entirely. It may not satisfy an ASK and
    must never satisfy a CONFIRM.
    """
    for capability in C._ALL:
        if not capability.requires_approval:
            continue
        outcome = X.admit(capability.id, {}, "do that thing now", 0.99)
        assert outcome in (X.NEEDS_APPROVAL, X.TOO_RISKY), \
            f"{capability.id} was admitted as a reflex"


def test_nothing_risky_is_a_reflex():
    for capability in C._ALL:
        if capability.risk == X.ALLOWED_RISK:
            continue
        assert X.admit(capability.id, {}, "just do it", 0.99), \
            f"{capability.id} is {capability.risk} and was admitted"


def test_the_model_is_only_offered_capabilities_it_could_use():
    """
    Declaring the gated ones would let the retrieval head spend its five
    slots on tools this router may never call - a way of making the model
    look worse than it is.
    """
    offered = {schema["name"] for schema in X.tool_schemas()}
    assert offered, "nothing was offered"
    for name in offered:
        capability = C.by_id(name)
        assert capability is not None
        assert not capability.requires_approval, name
        assert capability.risk == X.ALLOWED_RISK, name


def test_a_capability_that_does_not_exist_is_refused():
    outcome = X.route("do the thing",
                      agent=Proposer("magic_super_tool", confidence=0.99))
    assert outcome.escalated == X.UNKNOWN_CAPABILITY


def test_a_tuned_model_reporting_no_confidence_escalates():
    """
    `needle/__init__.py` sets `confidence = None` whenever tuned weights are
    loaded, because LoRA does not update the confidence head. So the moment
    anybody fine-tunes this on Friday's own speech - the obvious next step -
    the number stops existing, and absence must not read as certainty.
    """
    outcome = X.route("open Paint",
                      agent=Proposer("apps_open", {"name": "Paint"},
                                     confidence=None))
    assert outcome.escalated == X.NO_CONFIDENCE


def test_a_score_below_the_floor_escalates():
    outcome = X.route("open Paint",
                      agent=Proposer("apps_open", {"name": "Paint"},
                                     confidence=0.01))
    assert outcome.escalated == X.LOW_CONFIDENCE


def test_confidence_alone_does_not_admit_anything():
    """A perfect score on a wrong capability is still a wrong capability."""
    assert X.admit("system_get_info", {}, "restart the song", 1.0) \
        == X.WRONG_TARGET


def test_a_call_missing_its_arguments_escalates():
    outcome = X.route("open Paint", agent=Proposer("apps_open", {},
                                                   confidence=0.9))
    assert outcome.escalated == X.MISSING_ARGUMENTS


def test_an_empty_argument_is_a_missing_one():
    assert X.admit("apps_open", {"name": ""}, "open Paint", 0.9) \
        == X.MISSING_ARGUMENTS


def test_a_good_proposal_is_admitted():
    """The gate has to let the right answer through, or it is just an off switch."""
    outcome = X.route("open Paint",
                      agent=Proposer("apps_open", {"name": "Paint"},
                                     confidence=0.9))

    assert outcome.acts
    assert outcome.capability == "apps_open"
    assert outcome.arguments == {"name": "Paint"}


def test_it_asks_the_model_and_never_lets_the_model_act():
    """
    Needle's own `run()` executes the tools itself. Nothing outside
    `CapabilityRuntime` may execute anything - that is where policy,
    provenance and the evidence trail live.
    """
    proposer = Proposer("apps_open", {"name": "Paint"}, confidence=0.9)
    X.route("open Paint", agent=proposer)

    assert proposer.completed == ["open Paint"]
    assert proposer.ran == [], "the model was allowed to execute"


def test_no_call_is_the_escalation_signal_not_a_failure():
    """Needle's designed way of saying "this is not a local reflex"."""
    outcome = X.route("research whether godot suits my game",
                      agent=Proposer("", kind="respond", confidence=0.07))
    assert outcome.escalated == X.NO_CALL


def test_a_model_that_throws_costs_nobody_their_reply():
    outcome = X.route("open Paint",
                      agent=Proposer("apps_open", raises=RuntimeError("boom")))
    assert outcome.escalated == X.NO_MODEL


def test_it_is_not_a_planner():
    """
    256 tokens of context and no free-text answer. A compound request is not
    one tool call, and reading it as one is how "check my computer and open
    Paint, then tell me if it looks slow" becomes a single wrong action.
    """
    compound = ("check my computer and open Paint, then tell me whether the "
                "storage situation needs attention any time soon")
    outcome = X.route(compound,
                      agent=Proposer("system_get_info", confidence=0.9))

    assert not outcome.acts, "a multi-step request was handled as one reflex"


def test_the_corpus_comes_from_the_registry():
    """
    A hand-kept benchmark would be wrong the first time somebody added a
    capability and forgot. This one cannot drift - it is the same data the
    semantic router is built from.
    """
    from scripts.benchmark_reflex import corpus

    positives, negatives, escalations = corpus()
    assert len(positives) > 200
    assert len(negatives) > 200, "the negatives are what measure false actions"
    assert escalations
    for _text, capability in positives:
        assert C.by_id(capability) is not None


def test_a_model_that_proposes_nothing_scores_zero_false_actions(monkeypatch):
    """
    The baseline, and the bar. Escalating everything costs exactly what Friday
    costs today; a local router only earns its place by beating that without
    spending the zero.
    """
    from scripts.benchmark_reflex import _Deaf, measure

    monkeypatch.setenv(X.ENV_FLAG, "1")
    report = measure(_Deaf(), gate_only=True)

    assert report["false_actions"] == 0
    assert report["handled_locally"] == 0


def test_the_deterministic_arm_needs_no_model():
    """
    Friday's own semantic router, offered as a reflex backend. Not a
    reimplementation - `planner.interpret` and `planner.resolve`, the same
    pass production uses - so what is measured is the router that ships.
    """
    outcome = X.route('pause the music', agent=X.Deterministic())
    assert outcome.acts
    assert outcome.capability == 'music_pause'


def test_the_deterministic_arm_escalates_what_it_cannot_read():
    for text in ('it is frozen, end it', 'shut down the computer'):
        assert not X.route(text, agent=X.Deterministic()).acts, text


def test_a_domain_is_read_without_a_model():
    assert X.domain_of('pause the music') == 'MEDIA'
    assert X.domain_of('open Paint') == 'APPLICATION'
    assert X.domain_of('it is frozen, end it') == ''


def test_narrowing_puts_the_model_in_its_own_regime():
    """
    Measured: 95 tools cost a median of 2.2 seconds, against 265-820ms for
    four. Needle bypasses retrieval entirely at five tools or fewer, so the
    size of what is declared is the latency.
    """
    whole = X.tool_schemas()
    assert len(whole) > 80
    for text in ('pause the music', 'open Paint', 'read that file'):
        narrowed = X.tool_schemas(X.domain_of(text))
        assert 0 < len(narrowed) <= 20, (text, len(narrowed))


def test_narrowing_is_the_exact_target_not_the_family():
    """
    The families are where the confusions live: APPLICATION pulls in WINDOW,
    BROWSER and PROCESS, which is the browser_close / process_terminate /
    apps_close set the model already confused with each other.
    """
    from friday import semantics as S
    for schema in X.tool_schemas('MEDIA'):
        assert S.for_capability(schema['name'])[1] == 'MEDIA', schema['name']


def test_an_unreadable_domain_is_not_handed_to_the_model():
    """
    The case the flat catalogue handled worst. A 45M model choosing between
    browser_close, process_terminate, apps_close and music_stop is guessing,
    and the sentence that provokes it names no domain at all.
    """
    gated = X.DomainGated()
    outcome = X.route('it is frozen, end it', agent=gated)
    assert not outcome.acts
    assert gated._by_domain == {}, 'a model was built for a sentence with no domain'


def test_a_bare_pronoun_never_ends_anything():
    for text in ('close it', 'end it', 'delete it', 'stop this', 'it is frozen, end it', 'get rid of that'):
        assert not X.is_grounded(text), text


def test_naming_the_thing_grounds_it():
    for text in ('close the browser', 'stop the music', 'shut down the computer', 'delete that file'):
        assert X.is_grounded(text), text


def test_a_recoverable_verb_is_not_held_to_it():
    """
    Only ending verbs. A mistaken "show me that" is a wasted read and the
    boss asks again; a mistaken "close that" is somebody's unsaved work.
    """
    assert X.is_grounded('pause it')
    assert X.is_grounded('show me that')
    assert X.is_grounded('what is that')


def test_an_ungrounded_ending_escalates_through_the_gate():
    assert X.admit('browser_close', {}, 'it is frozen, end it', 0.99) == X.UNGROUNDED


def test_what_counts_as_dangerous_is_derived_and_pinned():
    """
    Every capability a reflex may reach is LOW risk, so `risk` cannot make
    this distinction - and should not, because LOW is correct for all of
    them. `browser_close` closes Playwright's session, not the boss's Chrome.
    It is still the wrong thing to do to somebody who said "end it".

    Derived from an ending verb plus a side effect outside this process. This
    pins what that currently catches, so a registry change shows up as a test
    rather than as a quiet loss of coverage.
    """
    caught = {name for name in (s['name'] for s in X.tool_schemas()) if X.is_dangerous(name)}
    for name in ('browser_close', 'music_stop', 'power_cancel', 'objective_cancel', 'workbench_stop'):
        assert name in caught, name
    for name in ('music_pause', 'apps_open', 'windows_list', 'files_read'):
        assert name not in caught, name


def test_the_agent_warms_the_fast_path_at_startup():
    """
    `warm()` exists so the model is built "rather than inside somebody's
    sentence", and `_agent()` says "never at request time in a voice turn -
    `warm()` is what the agent calls at startup".

    Nothing called it. The first construction - measured at 29 seconds,
    because it fetches the engine from Hugging Face - would have landed
    inside the first turn that used the fast path, which is the exact stall
    the function was written to avoid.
    """
    import inspect
    import agent_friday as A
    assert hasattr(A.FridayAgent, '_warm_the_fast_path')
    assert '_warm_the_fast_path' in inspect.getsource(A.FridayAgent.on_enter)


def test_warming_costs_nothing_when_the_fast_path_is_off(monkeypatch):
    """
    The default. `enabled()` is checked first and returns before anything is
    imported, so startup pays nothing for a feature nobody turned on.
    """
    monkeypatch.delenv('FRIDAY_REFLEX_MODE', raising=False)
    built = []
    monkeypatch.setattr(X, '_agent', lambda: built.append(1))
    assert X.warm() is False
    assert not built, 'it built the model with the fast path switched off'


def test_a_failure_to_warm_does_not_stop_the_session(monkeypatch):
    """Warming is an optimisation; failing it costs latency and nothing else."""
    import agent_friday as A
    agent = object.__new__(A.FridayAgent)
    monkeypatch.setattr(X, 'warm', lambda: (_ for _ in ()).throw(RuntimeError('no model')))
    agent._warm_the_fast_path()
