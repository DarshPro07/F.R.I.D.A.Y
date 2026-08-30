"""
Automatic learning: the user model fills itself in, without being asked.

The gap this closes was invisible for a week - profile.learn_from_turn was
built, tested and correct, and the profile stayed empty because the only thing
that could call it was a model with other things on its mind.

Two properties matter more than any of the detail here:

  * nothing in the hook may block the reply. Extraction is a Gemini call.
  * nothing in the hook may break the reply. A dead learner costs a memory,
    never a conversation.
"""
from __future__ import annotations
import asyncio
import json
import pytest
from friday import autolearn


def test_payload_reads_a_plain_dict():
    assert autolearn.payload({"status": "succeeded"}) == {"status": "succeeded"}


def test_payload_reads_a_json_string():
    assert autolearn.payload('{"status": "succeeded"}') == {"status": "succeeded"}


def test_payload_unwraps_livekits_text_content():
    """
    The default resolver returns the serialised MCP TextContent, so the tool's
    own result is one layer down (llm/mcp.py:61).
    """
    inner = {"status": "succeeded", "brief": "he uses Linux"}
    wrapped = json.dumps({"type": "text", "text": json.dumps(inner)})
    assert autolearn.payload(wrapped) == inner


def test_payload_on_nonsense_is_empty_not_an_exception():
    assert autolearn.payload("not json at all") == {}
    assert autolearn.payload(None) == {}
    assert autolearn.payload("[1, 2, 3]") == {}


class FakeMCP:
    """Records calls and returns canned tool results, TextContent-wrapped."""

    def __init__(self, *, learned=None, conflicts=None, brief="", fail=False):
        self.calls: list[tuple[str, dict]] = []
        self.learned = learned or []
        self.conflicts = conflicts or []
        self.brief = brief
        self.fail = fail

    async def __call__(self, name: str, arguments: dict):
        self.calls.append((name, arguments))
        if self.fail:
            raise RuntimeError("MCP is down")
        if name == "profile_learn_from_turn":
            body = {"status": "succeeded", "learned": self.learned,
                    "reinforced": [], "rejected": [], "conflicts": self.conflicts}
        elif name == "profile_get":
            body = {"status": "succeeded", "brief": self.brief}
        else:
            body = {"status": "failed", "error": f"unexpected tool {name}"}
        return json.dumps({"type": "text", "text": json.dumps(body)})

    def names(self) -> list[str]:
        return [name for name, _ in self.calls]


async def drain(learner: autolearn.AutoLearner, timeout: float = 2.0) -> None:
    """Wait for the background worker to finish what is queued."""
    await asyncio.wait_for(learner._queue.join(), timeout)


@pytest.fixture
def learner_factory():
    made: list[autolearn.AutoLearner] = []

    def make(mcp, **kwargs):
        learner = autolearn.AutoLearner(mcp, **kwargs)
        made.append(learner)
        return learner

    yield make


def test_short_turns_are_not_learned_from(learner_factory):
    """"yes" and "go on" cost a model call and teach nothing."""
    learner = learner_factory(FakeMCP())
    assert learner.observe("yes") is False
    assert learner.observe("go on") is False
    assert learner.observe("  ") is False
    assert learner.observe("I run everything on Linux") is True


def test_the_same_sentence_twice_is_only_learned_once(learner_factory):
    learner = learner_factory(FakeMCP())
    assert learner.observe("I use a Windows laptop") is True
    assert learner.observe("I use a Windows laptop") is False


def test_a_full_queue_drops_the_oldest_and_never_blocks(learner_factory):
    """Learning may fall behind. Speech may not wait for it."""
    learner = learner_factory(FakeMCP(), max_pending=2)
    assert learner.observe("first thing I said") is True
    assert learner.observe("second thing I said") is True
    assert learner.observe("third thing I said") is True  # accepted, not blocked
    assert learner._queue.qsize() == 2
    queued = [learner._queue.get_nowait()[0] for _ in range(2)]
    assert queued == ["second thing I said", "third thing I said"]


def test_observe_never_raises(learner_factory):
    learner = learner_factory(FakeMCP())
    assert learner.observe(None) is False  # type: ignore[arg-type]


def test_a_turn_is_learned_from_without_the_model_asking(learner_factory):
    mcp = FakeMCP(learned=[{"subject": "hardware.os", "value": "Linux"}],
                  brief="OS: Linux")

    async def go():
        learner = learner_factory(mcp)
        await learner.start()
        learner.observe("I switched the whole rig over to Linux",
                        assistant_text="Noted, boss.")
        await drain(learner)
        await learner.aclose()
        return learner

    learner = asyncio.run(go())
    assert ("profile_learn_from_turn", {
        "user_said": "I switched the whole rig over to Linux",
        "you_replied": "Noted, boss.",
    }) in mcp.calls
    assert learner.learned == 1
    assert "OS: Linux" in learner.brief


def test_the_briefing_is_loaded_before_the_first_turn(learner_factory):
    """A restart must not start blank; the profile is already on disk."""
    mcp = FakeMCP(brief="GOALS:\n  - ship ADA")

    async def go():
        learner = learner_factory(mcp)
        await learner.start()
        await learner.aclose()
        return learner

    learner = asyncio.run(go())
    assert "ship ADA" in learner.brief
    assert mcp.names() == ["profile_get"]


def test_a_turn_that_taught_nothing_does_not_re_read_the_profile(learner_factory):
    """Most turns contain nothing. They should cost one call, not two."""
    mcp = FakeMCP(learned=[], brief="")

    async def go():
        learner = learner_factory(mcp)
        await learner.start()
        learner.observe("what time is it right now")
        await drain(learner)
        await learner.aclose()

    asyncio.run(go())
    assert mcp.names() == ["profile_get", "profile_learn_from_turn"]


def test_a_conflict_becomes_something_to_ask_about(learner_factory):
    mcp = FakeMCP(conflicts=[{"subject": "hardware.os",
                              "reason": "you said Windows, now Linux"}])

    async def go():
        learner = learner_factory(mcp)
        await learner.start()
        learner.observe("actually I am on Linux now")
        await drain(learner)
        await learner.aclose()
        return learner

    learner = asyncio.run(go())
    assert "hardware.os" in learner.brief
    assert "which is right" in learner.brief


def test_a_missing_profile_tool_is_a_note_not_a_stack_trace(learner_factory, caplog):
    """
    The MCP server may not be up. That is expected in a bare harness, and a
    traceback for it at every session start hides the failures that matter.
    """
    async def absent(name, arguments):
        raise LookupError(f"no capability called {name!r}")

    async def go():
        learner = learner_factory(absent)
        await learner.start()
        await learner.aclose()
        return learner

    with caplog.at_level("INFO"):
        learner = asyncio.run(go())
    assert learner.brief == ""
    assert not any(record.exc_info for record in caplog.records), \
        "a missing tool was logged as an exception"


def test_a_failing_tool_does_not_kill_the_learner(learner_factory):
    """One bad call must not end learning for the rest of the session."""
    mcp = FakeMCP(fail=True)

    async def go():
        learner = learner_factory(mcp)
        await learner.start()
        learner.observe("something worth remembering here")
        await drain(learner)
        mcp.fail = False
        mcp.learned = [{"subject": "user.role", "value": "builder"}]
        mcp.brief = "IDENTITY:\n  - user.role: builder"
        learner.observe("a different thing worth remembering")
        await drain(learner)
        await learner.aclose()
        return learner

    learner = asyncio.run(go())
    assert "builder" in learner.brief


def chat_ctx():
    from livekit.agents import llm

    return llm.ChatContext.empty()


def test_the_briefing_lands_in_the_turn_context(learner_factory):
    learner = learner_factory(FakeMCP())
    learner._brief = "POSSESSIONS:\n  - hardware.laptop: a 16-core Windows machine"

    ctx = chat_ctx()
    assert learner.inject(ctx) is True
    assert ctx.items[-1].role == "system"
    assert "16-core" in ctx.items[-1].text_content


def test_nothing_is_injected_when_nothing_is_known(learner_factory):
    learner = learner_factory(FakeMCP())
    ctx = chat_ctx()
    assert learner.inject(ctx) is False
    assert ctx.items == []


def test_last_assistant_text_finds_the_most_recent_reply():
    ctx = chat_ctx()
    ctx.add_message(role="assistant", content="first reply")
    ctx.add_message(role="user", content="something")
    ctx.add_message(role="assistant", content="second reply")
    ctx.add_message(role="user", content="a follow-up")
    assert autolearn.last_assistant_text(ctx) == "second reply"


def test_last_assistant_text_on_an_empty_conversation():
    assert autolearn.last_assistant_text(chat_ctx()) == ""


class Stub:
    """Just enough of FridayAgent for the unbound hooks to run."""

    prepare_turn = None  # replaced below with the real, unbound method

    def __init__(self, learner):
        self._learner = learner
        self._chat_ctx = chat_ctx()

    @property
    def chat_ctx(self):
        return self._chat_ctx


class Message:
    def __init__(self, text):
        self.text_content = text


@pytest.fixture(autouse=True)
def _borrow_prepare_turn():
    import agent_friday as A
    Stub.prepare_turn = A.FridayAgent.prepare_turn
    Stub.read_before_answering = A.FridayAgent.read_before_answering


def test_a_broken_learner_never_costs_a_reply():
    import agent_friday as A

    class Broken:
        def inject(self, ctx):
            raise RuntimeError("the user model exploded")

        def observe(self, *args, **kwargs):
            raise RuntimeError("and again")

    # No exception: the turn goes on without a briefing.
    asyncio.run(A.FridayAgent.on_user_turn_completed(
        Stub(Broken()), chat_ctx(), Message("hello")))


def test_typed_input_gets_the_same_treatment_as_spoken(learner_factory):
    """
    Text from the room goes straight to generate_reply and never reaches
    on_user_turn_completed (room_io/types.py:55), so it needs its own wiring -
    typing to Friday must teach it just as much as talking to it.
    """
    import agent_friday as A

    learner = learner_factory(FakeMCP())
    learner._brief = "PREFERENCES:\n  - style: short answers"
    agent = Stub(learner)
    agent._chat_ctx.add_message(role="assistant", content="Standing by.")

    seen = {}

    class FakeSession:
        async def interrupt(self):
            seen["interrupted"] = True

        def generate_reply(self, *, user_input, chat_ctx):
            seen["user_input"] = user_input
            seen["chat_ctx"] = chat_ctx
            return "handle"

    event = type("E", (), {"text": "always keep it under three sentences"})()
    handle = asyncio.run(A.text_input_callback(agent)(FakeSession(), event))

    assert handle == "handle"
    assert seen["interrupted"] is True
    # The briefing is in the instructions, not stapled onto each turn - see
    # test_the_turn_hook_observes_without_touching_the_context.
    assert learner._queue.qsize() == 1
    assert learner._queue.get_nowait() == (
        "always keep it under three sentences", "Standing by.")


def test_typed_input_does_not_pollute_the_stored_history(learner_factory):
    """The briefing informs one reply; it must not accumulate in history."""
    import agent_friday as A

    learner = learner_factory(FakeMCP())
    learner._brief = "IDENTITY:\n  - user.role: builder"
    agent = Stub(learner)

    class FakeSession:
        async def interrupt(self):
            pass

        def generate_reply(self, *, user_input, chat_ctx):
            return None

    event = type("E", (), {"text": "something worth saying here"})()
    asyncio.run(A.text_input_callback(agent)(FakeSession(), event))

    assert agent.chat_ctx.items == []


def test_the_agent_can_call_its_own_capabilities_without_the_model():
    """
    The learner reaches profile_learn_from_turn through the router, not
    through use_capability - there is no model in that loop.
    """
    import agent_friday as A
    from friday import capability_router as R

    class FakeTool:
        def __init__(self, name):
            self.info = type("I", (), {"name": name})()
            self.seen = None

        async def __call__(self, arguments):
            self.seen = arguments
            return "ok"

    tool = FakeTool("profile_learn_from_turn")
    router = R.Router()
    router.load([tool])

    stub = Stub(None)
    stub._router = router
    result = asyncio.run(A.FridayAgent._call_capability(
        stub, "profile_learn_from_turn", {"user_said": "hi"}))

    assert result == "ok"
    assert tool.seen == {"user_said": "hi"}

    with pytest.raises(LookupError):
        asyncio.run(A.FridayAgent._call_capability(stub, "no_such_tool", {}))


def test_the_briefing_is_folded_into_the_instructions(learner_factory):
    """
    Where it costs nothing per turn, instead of rewriting the chat context
    every time and losing the head start LiveKit had already taken.
    """
    import agent_friday as A

    learner = learner_factory(FakeMCP())
    learner._brief = "POSSESSIONS:\n  - hardware.laptop: 16 cores"

    updated = {}

    class Agentish:
        _learner = learner
        _briefing = ""

        async def update_instructions(self, text):
            updated["text"] = text

    agent = Agentish()
    changed = asyncio.run(A.FridayAgent.refresh_briefing(agent))
    assert changed is True
    assert "16 cores" in updated["text"]
    assert "F.R.I.D.A.Y" in updated["text"], "the base prompt must survive"


def test_an_unchanged_briefing_does_not_rewrite_anything(learner_factory):
    """
    The whole point of moving it: no change, no context rewrite, no lost
    preemptive generation.
    """
    import agent_friday as A

    learner = learner_factory(FakeMCP())
    learner._brief = "same"
    calls = []

    class Agentish:
        _learner = learner
        _briefing = ""

        async def update_instructions(self, text):
            calls.append(text)

    agent = Agentish()
    asyncio.run(A.FridayAgent.refresh_briefing(agent))
    asyncio.run(A.FridayAgent.refresh_briefing(agent))
    assert len(calls) == 1, "it rewrote the instructions for no change"


def test_the_learner_reports_only_real_changes(learner_factory):
    """The callback is what triggers a refresh, so it must not cry wolf."""
    fired = []
    mcp = FakeMCP(learned=[{"subject": "x", "value": "y"}], brief="A")

    async def go():
        learner = autolearn.AutoLearner(mcp, on_learned=lambda: fired.append(1))
        await learner.start()
        learner.observe("something worth remembering here")
        await drain(learner)
        # Same brief again: no callback.
        learner.observe("a different thing worth remembering")
        await drain(learner)
        await learner.aclose()

    asyncio.run(go())
    assert len(fired) == 1, f"fired {len(fired)} times for one real change"
