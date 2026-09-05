"""The voice pipeline changes: TTS chunking and the acknowledgement rule.

These are the two things the boss reported as broken in a live session:
  - "excessive pause before the next sentence"  -> tts_node flush boundary
  - simple requests got no "I'm working on it"  -> read-only ack exemption
"""

from __future__ import annotations

import asyncio
import json
import re

import pytest

import agent_friday as af


# ---------------------------------------------------------------------------
# Acknowledgement: speak first when the boss would otherwise wait in silence
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("capability", [
    "vision_inspect_screen",     # ~15.7s measured, touches the device
    "vision_inspect_camera",
    "web_deep_research",         # network, slowest of all
    "web_search",
])
def test_slow_reads_are_acknowledged(capability):
    """A read that leaves this process must not answer with silence."""
    assert af._should_acknowledge(capability) is True


@pytest.mark.parametrize("capability", ["get_current_time"])
def test_instant_reads_stay_silent(capability):
    """'What time is it' should not be preceded by 'give me a sec'."""
    assert af._should_acknowledge(capability) is False


@pytest.mark.parametrize("capability", ["files_write", "music_play", "apps_open"])
def test_writes_are_always_acknowledged(capability):
    """The original rule, unchanged: anything that mutates speaks first."""
    assert af._should_acknowledge(capability) is True


def test_the_acknowledgement_matches_the_work():
    assert "look" in af._ack_line("vision_inspect_screen").lower()
    assert "look" in af._ack_line("web_search").lower()   # "Looking that up"
    assert af._ack_line("files_write") == "On it, boss."


def test_an_unknown_capability_still_gets_a_line():
    """No KeyError on a tool this build has never heard of."""
    assert af._ack_line("totally_made_up_tool")


# ---------------------------------------------------------------------------
# TTS chunking: fewer synthesis round trips, without dropping or reordering
# ---------------------------------------------------------------------------


class _FakeAgentDefault:
    """Captures what tts_node hands downstream, in order."""

    def __init__(self):
        self.chunks: list[str] = []

    async def tts_node(self, agent, text, model_settings):
        async for chunk in text:
            self.chunks.append(chunk)
            yield chunk


async def _run_tts(sentences: list[str], monkeypatch) -> list[str]:
    fake = _FakeAgentDefault()
    monkeypatch.setattr(af.Agent, "default", fake)

    async def source():
        for s in sentences:
            yield s

    agent = af.FridayAgent.__new__(af.FridayAgent)   # no LiveKit session needed
    out = [c async for c in af.FridayAgent.tts_node(agent, source(), None)]
    assert out == fake.chunks
    return fake.chunks


def test_the_first_sentence_is_flushed_immediately(monkeypatch):
    """Time-to-first-word must not regress: she starts talking at once."""
    chunks = asyncio.run(_run_tts(
        ["All systems nominal, boss. ", "The core is stable. ",
         "Nothing needs your attention. "], monkeypatch))
    assert chunks, "nothing was spoken"
    assert chunks[0].strip() == "All systems nominal, boss."


def test_later_sentences_are_batched_into_fewer_requests(monkeypatch):
    """The pause the boss heard was one synthesis round trip per sentence."""
    sentences = [f"This is sentence number {i}. " for i in range(1, 11)]
    chunks = asyncio.run(_run_tts(sentences, monkeypatch))

    # Old behaviour: one chunk per sentence (10). New: first one, then batches.
    assert len(chunks) < len(sentences), (
        f"expected batching, got {len(chunks)} chunks for {len(sentences)} "
        "sentences - each chunk is a TTS round trip the boss hears as a gap"
    )


def test_no_word_is_lost_or_reordered(monkeypatch):
    """Batching must change only the flush boundary, never the content."""
    sentences = [
        "First thing. ", "Second thing. ", "Third thing. ",
        "Fourth thing. ", "Fifth thing, and that is all. ",
    ]
    chunks = asyncio.run(_run_tts(sentences, monkeypatch))

    def words(text: str) -> list[str]:
        return re.findall(r"[a-z]+", text.lower())

    assert words(" ".join(chunks)) == words("".join(sentences))


def test_a_trailing_fragment_is_still_spoken(monkeypatch):
    """A final clause with no full stop must not be swallowed."""
    chunks = asyncio.run(_run_tts(
        ["Done, boss. ", "One more thing without punctuation"], monkeypatch))
    assert "one more thing" in " ".join(chunks).lower()


def test_a_url_split_across_chunks_is_still_scrubbed(monkeypatch):
    """The reason the buffer exists at all - this must not regress."""
    chunks = asyncio.run(_run_tts(
        ["Here is the link: https://exa", "mple.com/page and that is it. "],
        monkeypatch))
    spoken = " ".join(chunks)
    assert "example.com" not in spoken
    assert "http" not in spoken


def test_a_single_short_answer_is_not_held_back(monkeypatch):
    """A one-line reply must be spoken, not buffered waiting for more."""
    chunks = asyncio.run(_run_tts(["Yes, boss. "], monkeypatch))
    assert "yes, boss." in " ".join(chunks).lower()


def test_a_plain_search_does_not_route_to_the_research_skill_family():
    """Live defect: "search for me foreign market Trend" hit the research
    family, which only returns SKILL NAMES, and Friday reported she could not
    execute it. A question about the world belongs to web_search/web_answer."""
    for question in [
        "search for me foreign market trends",
        "search for the latest news",
        "what is happening with the market",
        "look up the price of gold",
    ]:
        assert "research" not in af._family_hints(question), (
            f"{question!r} routed to the research skill family, which answers "
            "with skill names rather than results - the exact dead end the "
            "boss hit"
        )


def test_genuine_methodology_requests_still_reach_the_research_family():
    """The family is still right for what it is actually for."""
    for question in [
        "do a deep dive on this",
        "compare sources on this claim",
        "write a literature review",
    ]:
        assert "research" in af._family_hints(question), question


def test_research_skill_without_a_name_explains_instead_of_quoting_empty():
    """Live defect: Friday read "'' is not an offered skill" aloud and concluded
    the research capability was broken. An empty name is the model's mistake
    and the error must say what to do instead."""
    from friday import fabric
    from friday.fabric_adapters import science_skills

    with pytest.raises(fabric.FabricError) as exc:
        science_skills.call("skill", None, name="")
    msg = str(exc.value)
    assert "'' is not" not in msg
    assert "name" in msg and "web_search" in msg


# ---------------------------------------------------------------------------
# MCP outage: report the outage, never invent a missing capability
# ---------------------------------------------------------------------------
#
# Measured in a live session: with MCP down, the boss asked for the screen, a
# capability list, and market data, and got three DIFFERENT invented reasons
# ("vision is not currently available", "a temporary issue with the research
# skill"). Nothing was missing - the tool server was unreachable. These pin the
# honest behaviour.


def _offline_agent():
    agent = af.FridayAgent.__new__(af.FridayAgent)
    agent._router = af.capability_router.Router()   # no tools loaded
    agent._tools_offline = True
    return agent


def test_search_reports_the_outage_rather_than_a_missing_capability():
    agent = _offline_agent()
    out = json.loads(asyncio.run(
        af.FridayAgent.search_capabilities.__wrapped__(agent, "look at my screen")))

    assert out["error"] == "mcp_unavailable"
    assert out["found"] == 0
    spoken = out["say_this"].lower()
    assert "offline" in spoken or "isn't answering" in spoken
    # The specific failure the boss saw must be named as forbidden.
    assert "missing" in out["do_not"].lower()


def test_listing_areas_while_offline_does_not_answer_from_memory():
    agent = _offline_agent()
    out = json.loads(asyncio.run(
        af.FridayAgent.list_capability_areas.__wrapped__(agent)))

    assert out["error"] == "mcp_unavailable"
    assert "areas" not in out, "must not list capabilities it cannot verify"


def test_listing_areas_reports_real_inventory_when_tools_are_present():
    """The positive case: with tools loaded, vision is present and reachable."""
    class _Info:
        def __init__(self, name):
            self.name = name
            self.raw_schema = {}

    class _Tool:
        def __init__(self, name):
            self.info = _Info(name)

    agent = af.FridayAgent.__new__(af.FridayAgent)
    agent._router = af.capability_router.Router()
    agent._tools_offline = False
    names = list(af.capability_router.GROUPS["vision"]) + ["get_current_time"]
    agent._router.load([_Tool(n) for n in names])

    out = json.loads(asyncio.run(
        af.FridayAgent.list_capability_areas.__wrapped__(agent)))

    assert "error" not in out
    areas = {a["area"] for a in out["areas"]}
    assert "vision" in areas, (
        "vision must appear in the capability inventory - reporting it as "
        "unavailable while vision_inspect_screen works is the original bug"
    )


# ---------------------------------------------------------------------------
# Room-path progress narration: milestones + a cadence digest, S3
# ---------------------------------------------------------------------------


class _FakeWorkLog:
    def __init__(self, rows):
        self._rows = rows

    def active(self):
        return list(self._rows)

    def recent(self, limit=12):
        return list(self._rows)


class _FakeSupervisor:
    def __init__(self, rows, progress_by_id):
        self.log = _FakeWorkLog(rows)
        self._progress = progress_by_id

    def progress(self, work_run_id):
        return self._progress[work_run_id]


class _FakeSession:
    def __init__(self):
        self.said = []
        self.output = None
        self.user_state = "listening"
        self.current_speech = None

    async def say(self, text, allow_interruptions=True, add_to_chat_ctx=None):
        self.said.append(text)


def test_room_path_speaks_a_milestone_and_a_timed_digest():
    """A fake run's events must produce one milestone (spoken at once) and,
    once cadence elapses, one digest - the narration LiveKit never had
    before S3 (only completions were delivered, never progress)."""
    row = {"work_run_id": "wr-1", "status": "WORKING", "model": "claude",
           "route_reason": "capacity", "last_event_at": 0}
    prog = {"work_run_id": "wr-1", "status": "WORKING", "seq": 1, "tools": 2,
            "line": "read policy.py", "current": "editing x.py"}
    sup = _FakeSupervisor([row], {"wr-1": prog})
    session = _FakeSession()

    result = asyncio.run(af.speak_progress_digests(session, sup=sup))
    assert result["digest"] is False   # first pass: cadence has not elapsed
    assert session.said == []          # nothing terminal yet, no milestone

    # Advance the fake clock past the cadence by back-dating last_digest_at
    # through the state dict the caller owns across polls.
    state = {"last_digest_at": 0.0, "spoken": set()}
    prog["seq"] = 2
    import time as _time
    real_time = _time.time
    try:
        _time.time = lambda: real_time() + 200
        result = asyncio.run(af.speak_progress_digests(session, sup=sup, state=state))
    finally:
        _time.time = real_time
    assert result["digest"] is True
    assert any("claude" in s and "capacity" in s for s in session.said)
