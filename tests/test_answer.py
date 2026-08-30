"""
Deciding how hard to think about a question, before answering it.

The thing Friday is actually for. Every other subsystem exists so the boss can
stop opening ChatGPT, and none of them help with the commonest request of all,
which is a question.

Until this, every question took one path: a single Gemini turn with seventeen
tools attached. "Use search when you need to" was in the master prompt the
whole time and did not work, because a model that believes it knows the answer
does not feel a need - and `web_deep_research` sat outside `CORE_TOOLS`, in a
group of two, behind a discovery step. A research question reached
`web_search` at best, and a search is not research.

What is proved here: the mode is chosen without a model, a question with a
date on it gets sources, an ordinary one does not pay for them, and an answer
built on sources carries an instruction to disagree with a wrong premise.
"""
from __future__ import annotations
import pytest
from friday import answer as A


@pytest.mark.parametrize("question", [
    "what is CQRS",
    "explain how a hash map works",
    "what is the difference between TCP and UDP",
    "tell me about the actor model",
    "define idempotency",
])
def test_stable_knowledge_needs_no_sources(question):
    """
    Reading four articles to answer "what is CQRS" is two seconds spent to
    reach the same answer. FAST is not a fallback, it is the right mode for
    most of what anybody asks.
    """
    plan = A.plan(question)
    assert plan.mode == A.FAST, (question, plan.because)
    assert not plan.needs_sources
    assert not plan.capability


def test_an_unclassifiable_question_defaults_to_fast():
    """
    The honest default: it is what Friday does today, it costs nothing, and
    the model can still reach for search itself. Guessing RESEARCH would put
    two seconds on every sentence nobody could categorise.
    """
    plan = A.plan("hmm, interesting")
    assert plan.mode == A.FAST


def test_nothing_asked_is_not_an_error():
    assert A.plan("").mode == A.FAST


@pytest.mark.parametrize("question", [
    "what is the latest version of Python",
    "what happened with OpenAI this week",
    "what is the current pricing for Gemini",
    "has anything been announced about it recently",
    "what are the headlines today",
])
def test_a_question_about_now_gets_sources(question):
    plan = A.plan(question)
    assert plan.mode == A.RESEARCH, (question, plan.because)
    assert plan.capability == "web_deep_research"
    assert plan.arguments["question"] == question


def test_a_version_makes_a_definition_into_a_fact_with_a_date():
    """
    "What is the latest version" opens like a definition and is not one. The
    order of the tests is the argument.
    """
    assert A.plan("what is the newest version of Node").mode == A.RESEARCH
    assert A.plan("what is Node").mode == A.FAST


@pytest.mark.parametrize("question", [
    "should I use Godot or Unreal for this game",
    "compare React Native and Flutter",
    "is Rust worth learning",
    "what do you recommend for a small 2D game",
    "what are the trade-offs between Postgres and SQLite here",
])
def test_weighing_options_is_deep(question):
    plan = A.plan(question)
    assert plan.mode == A.DEEP, (question, plan.because)
    assert plan.arguments["sources"] > A.SOURCES[A.RESEARCH], \
        "a recommendation from two sources is an opinion with extra steps"


@pytest.mark.parametrize("question", [
    "why does this architecture keep failing when the provider goes down",
    "why do microservices make this harder",
    "best way to structure a plugin system",
    "how should I design the retry logic",
])
def test_architecture_questions_are_worth_arguing_with(question):
    assert A.plan(question).mode == A.DEEP, question


@pytest.mark.parametrize("question", [
    "why is my computer slow",
    "why is my laptop running hot",
    "what is wrong with my disk",
])
def test_a_question_about_this_machine_is_not_architecture(question):
    """
    Friday should look at the processes, not read seven articles. This
    replaced a word count, which got "why does this architecture keep failing
    when the provider goes down" wrong at eleven words.
    """
    assert A.plan(question).mode == A.FAST, question


def test_the_machine_pattern_survived_its_own_escaping():
    r"""
    `\b` became a literal backspace byte here - byte 0x08 at the head of the
    pattern - and the rule silently matched nothing, which is exactly the
    incident `friday/request_shape.py` documents about the negation regex.
    Same failure, same afternoon, different file.

    An inert filter looks exactly like a filter with nothing to do, so this
    asserts the pattern is intact rather than only that the behaviour looks
    right.
    """
    assert "\x08" not in A._ABOUT_THIS_MACHINE.pattern
    assert A._ABOUT_THIS_MACHINE.search("why is my computer slow")
    assert not A._ABOUT_THIS_MACHINE.search("why does this architecture fail")


def test_an_answer_built_on_sources_is_told_to_disagree():
    """
    A question containing a false premise is the case that matters most and
    the one an agreeable assistant handles worst.
    """
    for question in ("should I use Godot or Unreal",
                     "what is the latest version of Python"):
        instruction = A.plan(question).instruction
        assert "premise" in instruction.lower()
        assert "infer" in instruction.lower()


def test_a_deep_answer_is_asked_for_an_opinion_and_its_counterargument():
    instruction = A.plan("should I use Godot or Unreal").instruction
    assert "recommendation" in instruction.lower()
    assert "against your own" in instruction.lower()


def test_a_fast_answer_carries_no_instruction():
    """Nothing was researched, so there is nothing to be honest about."""
    assert A.plan("what is CQRS").instruction == ""


def test_the_findings_reach_the_model_without_the_boss_seeing_them():
    """
    The brief is context, not a chat message. What he hears is Friday's
    answer, not a research dump.
    """
    plan = A.plan("what is the latest version of Python")
    text = A.brief(plan, {"output": {"sources": [{"url": "https://example.com"}]}})

    assert "example.com" in text
    assert plan.instruction in text
    assert len(text) < 20000, "the brief must not swamp the turn"


@pytest.mark.asyncio
async def test_research_runs_before_the_model_answers(monkeypatch):
    """
    Sources before the answer, not after it. Afterwards is a correction, and
    a correction the boss did not ask for is just a longer wrong answer.
    """
    import agent_friday
    from livekit.agents import llm as lkllm

    called = {}

    class Result:
        status = "succeeded"
        may_claim_completion = True

        def to_dict(self):
            return {"output": {"sources": [{"url": "https://example.com"}]}}

    class Runtime:
        def __init__(self, **_):
            pass

        def execute(self, capability, arguments):
            called["capability"] = capability
            return Result()

    monkeypatch.setattr("friday.capability_runtime.CapabilityRuntime", Runtime)
    context = lkllm.ChatContext.empty()
    await agent_friday.research_first(context, "what is the latest version of Python")

    assert called["capability"] == "web_deep_research"
    said = "\n".join(str(getattr(item, "content", "")) for item in context.items)
    assert "example.com" in said


@pytest.mark.asyncio
async def test_an_ordinary_question_is_not_researched(monkeypatch):
    import agent_friday
    from livekit.agents import llm as lkllm

    def explode(**_):
        raise AssertionError("a FAST question paid for sources")

    monkeypatch.setattr("friday.capability_runtime.CapabilityRuntime", explode)
    context = lkllm.ChatContext.empty()
    await agent_friday.research_first(context, "what is CQRS")

    assert not context.items


@pytest.mark.asyncio
async def test_research_that_fails_costs_nobody_their_answer(monkeypatch):
    """The turn goes on. A missing source is not a missing reply."""
    import agent_friday
    from livekit.agents import llm as lkllm

    class Broken:
        def __init__(self, **_):
            pass

        def execute(self, *_a, **_k):
            raise RuntimeError("network on fire")

    monkeypatch.setattr("friday.capability_runtime.CapabilityRuntime", Broken)
    await agent_friday.research_first(lkllm.ChatContext.empty(),
                                      "what is the latest version of Python")


@pytest.mark.asyncio
async def test_a_coroutine_capability_is_awaited(monkeypatch):
    """
    `web_deep_research` is one, and the first version of this guarded against
    awaitables by *returning* - which would have made the whole research path
    a silent no-op. That shape of bug has already broken every async
    capability in this codebase once.
    """
    import agent_friday
    from livekit.agents import llm as lkllm

    class Result:
        status = "succeeded"
        may_claim_completion = True

        def to_dict(self):
            return {"output": {"sources": ["https://example.com"]}}

    class Async:
        def __init__(self, **_):
            pass

        def execute(self, *_a, **_k):
            async def later():
                return Result()

            return later()

    monkeypatch.setattr("friday.capability_runtime.CapabilityRuntime", Async)
    context = lkllm.ChatContext.empty()
    await agent_friday.research_first(context, "what is the latest version of Python")

    assert context.items, "the awaited result never reached the turn"
CODEBASE_QUESTIONS = ['verify against the exact current source and give me the source locations', 'which module in this codebase currently owns delivery', 'what does hermes_bridge.py do right now', 'use code-intelligence to find the latest call chain into the planner', 'where in the repository is the current origin filter']


@pytest.mark.parametrize('question', CODEBASE_QUESTIONS)
def test_a_question_about_our_own_source_does_not_go_to_the_web(question):
    """
    Measured live: "...verify against the exact current source" classified as
    RESEARCH on the word `current`, went to web search, found no sources and
    returned partial - while Friday held a structural graph of its own code and
    never reached it.

    Searching the web for the contents of a private repository cannot succeed,
    so this is not a preference between two workable routes.
    """
    mode, why = A.classify(question)
    assert mode != A.RESEARCH, f"{question!r} routed to the web: {why}"
STILL_NEEDS_SOURCES = ['what is the latest version of python', 'what is the current price of bitcoin', 'any news today about the outage', 'what changed in the 3.12 release']


@pytest.mark.parametrize('question', STILL_NEEDS_SOURCES)
def test_the_codebase_guard_does_not_swallow_real_research(question):
    """The negative control: a temporal question with no code in it still goes
    to sources. A guard that catches everything is not a guard."""
    mode, _ = A.classify(question)
    assert mode == A.RESEARCH, question
