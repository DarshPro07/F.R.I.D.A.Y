"""
Voice path: the loop from audio in to a verified tool call.

Phases 0, 1A and 1B all shipped with "nothing has spoken to the agent". These
tests close that. They are marked `live` because they make real provider calls
(TTS, STT, LLM) - run the suite with -m "not live" to skip them.

The round trip is the interesting part: the words the agent would *speak* are
fed back into the ears it *listens* with, so both audio legs are exercised
against real services without a microphone.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re

import pytest
from dotenv import load_dotenv

load_dotenv()

import agent_friday
from friday import providers

live = pytest.mark.live

SPOKEN = "Open the calculator please"


def words(text: str) -> set[str]:
    return set(re.sub(r"[^a-z0-9 ]", " ", (text or "").lower()).split())


@live
def test_tts_produces_real_audio():
    async def synth():
        tts = providers.build_tts()
        return await tts.synthesize(SPOKEN).collect()

    frame = asyncio.run(synth())
    duration = frame.samples_per_channel / frame.sample_rate
    assert duration > 0.3, f"suspiciously short audio: {duration}s"
    assert any(frame.data), "audio frame is silent"


@live
def test_tts_stt_round_trip():
    """Synthesize speech, transcribe it back, and compare the words."""

    async def round_trip():
        import aiohttp

        tts = providers.build_tts()
        frame = await tts.synthesize(SPOKEN).collect()
        # No job context here, so the plugin needs its own http session.
        async with aiohttp.ClientSession() as http:
            stt = providers.build_stt(http_session=http)
            try:
                event = await stt.recognize(frame)
            except NotImplementedError:
                pytest.skip("configured STT is streaming-only")
        return event.alternatives[0].text if event.alternatives else ""

    heard = asyncio.run(round_trip())
    overlap = words(SPOKEN) & words(heard)
    ratio = len(overlap) / len(words(SPOKEN))
    assert ratio >= 0.5, f"heard {heard!r}, only {ratio:.0%} of spoken words"


@live
def test_stt_needs_an_http_session_outside_a_job(caplog):
    """
    Documents the constraint that broke the first attempt: LiveKit plugins
    take their aiohttp session from the job context, so a standalone caller
    must supply one.

    The reason is asserted against the logged cause, not against the raised
    exception's message. LiveKit retries recognition four times and then raises
    its own APIConnectionError - "failed to recognize speech after 4 attempts" -
    which says nothing about why. Matching on that string tested the wrapper's
    prose, so it broke the day the wrapper reworded itself while the constraint
    it was guarding had not moved at all.
    """

    async def without_session():
        tts = providers.build_tts()
        frame = await tts.synthesize("hello").collect()
        stt = providers.build_stt()  # no http_session
        return await stt.recognize(frame)

    with caplog.at_level(logging.WARNING), pytest.raises(Exception):
        asyncio.run(without_session())
    assert "http session outside of a job context" in caplog.text.lower(), \
        f"failed for some other reason: {caplog.text[-400:]}"


@live
def test_a_user_turn_reaches_a_verified_tool():
    """
    The whole point: a spoken-style request must produce a real tool call
    whose ActionResult is verified, not a plausible sentence.

    Requires the MCP server on :8000 (scripts/golden_voice.py starts one).
    """
    import socket

    with socket.socket() as sock:
        sock.settimeout(0.5)
        if sock.connect_ex(("127.0.0.1", 8000)) != 0:
            pytest.skip("MCP server not running on :8000")

    async def turn():
        from livekit.agents.voice import AgentSession

        config = agent_friday.session_config()
        session = AgentSession(turn_handling=config["turn_handling"])
        agent = agent_friday.FridayAgent(
            stt=providers.build_stt(config["stt_provider"]),
            llm=providers.build_llm(config["llm_backend"], config["llm_role"]),
            tts=providers.build_tts(config["tts_provider"]),
        )
        await session.start(agent)
        try:
            result = await session.run(
                user_input="What is using the most memory on my computer?"
            )
            calls, outputs = [], []
            for event in result.events:
                kind = type(event).__name__
                if kind == "FunctionCallEvent":
                    calls.append(event.item.name)
                elif kind == "FunctionCallOutputEvent":
                    outputs.append(str(event.item.output))
            return calls, outputs
        finally:
            await session.aclose()

    calls, outputs = asyncio.run(turn())
    assert calls, "the turn produced no tool call"
    unescaped = " ".join(o.replace('\\"', '"') for o in outputs).lower()
    assert '"may_claim_completion": true' in unescaped, (
        "a tool ran but returned no verified result"
    )
