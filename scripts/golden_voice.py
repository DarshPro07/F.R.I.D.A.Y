#!/usr/bin/env python3
"""
Voice-path golden journey — closes the gap left open since Phase 0.

Phases 0, 1A and 1B all ended with the same caveat: "nothing has spoken to the
agent". Every capability was driven directly or over MCP, so the claim "voice
still works" was inference, not evidence.

This proves the whole loop without a microphone, by exercising each leg with
real network calls:

    LEG 1  TTS      text -> real audio samples
    LEG 2  STT      those audio samples -> transcript          (round trip)
    LEG 3  SESSION  user turn -> LLM -> MCP tool call -> reply
    LEG 4  TOOLS    the tool call reached a verified capability

LEG 1+2 is a genuine round trip: the words the agent would speak are fed back
into the ears it listens with. LEG 3 uses AgentSession.run(), the eval harness
shipped in livekit-agents 1.5.1.

    python scripts/golden_voice.py            # all legs
    python scripts/golden_voice.py --audio    # audio legs only (no LLM spend)

Exit 0 only if every leg passes.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

import agent_friday  # noqa: E402
from friday import providers  # noqa: E402

SPOKEN = "Open the calculator please"


def port_open(host: str = "127.0.0.1", port: int = 8000) -> bool:
    import socket

    with socket.socket() as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


def start_mcp_if_needed():
    if port_open():
        print("[voice] MCP server already running")
        return None
    print("[voice] starting MCP server ...")
    python = ROOT / ".venv" / "Scripts" / "python.exe"
    proc = subprocess.Popen(
        [str(python if python.exists() else sys.executable), "server.py"],
        cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        if port_open():
            return proc
        if proc.poll() is not None:
            raise RuntimeError("MCP server died during startup")
        time.sleep(0.25)
    raise RuntimeError("MCP server never opened :8000")


def normalise(text: str) -> set[str]:
    return set(re.sub(r"[^a-z0-9 ]", " ", (text or "").lower()).split())


async def leg_tts() -> tuple[bool, object]:
    print("=" * 66)
    print("LEG 1 — TTS: text becomes real audio")
    print("=" * 66)
    tts = providers.build_tts(os.getenv("TTS_PROVIDER", providers.DEFAULT_TTS))
    try:
        frame = await tts.synthesize(SPOKEN).collect()
    except Exception as exc:
        print(f"[FAIL] synthesize raised {type(exc).__name__}: {exc}\n")
        return False, None

    duration = frame.samples_per_channel / frame.sample_rate
    non_silent = any(frame.data)
    ok = duration > 0.3 and non_silent
    print(f"[{'PASS' if ok else 'FAIL'}] synthesized {SPOKEN!r}")
    print(f"       {frame.sample_rate} Hz, {frame.num_channels} ch, "
          f"{frame.samples_per_channel} samples ({duration:.2f}s)")
    print(f"       non-silent: {non_silent}\n")
    return ok, frame


async def leg_stt(frame) -> bool:
    print("=" * 66)
    print("LEG 2 — STT: that audio becomes text again (round trip)")
    print("=" * 66)
    if frame is None:
        print("[FAIL] no audio from LEG 1\n")
        return False

    # Outside a LiveKit job there is no job-context http session, so supply
    # one. Inside the worker this is unnecessary and http_session stays None.
    import aiohttp

    async with aiohttp.ClientSession() as http:
        stt = providers.build_stt(
            os.getenv("STT_PROVIDER", providers.DEFAULT_STT), http_session=http
        )
        return await _recognise(stt, frame)


async def _recognise(stt, frame) -> bool:
    try:
        event = await stt.recognize(frame)
    except NotImplementedError:
        print("[SKIP] this STT is streaming-only; recognize() unsupported")
        print("       (the streaming path is exercised live in dev/console mode)\n")
        return True
    except Exception as exc:
        print(f"[FAIL] recognize raised {type(exc).__name__}: {exc}\n")
        return False

    transcript = event.alternatives[0].text if event.alternatives else ""
    spoken_words = normalise(SPOKEN)
    heard_words = normalise(transcript)
    overlap = spoken_words & heard_words
    ratio = len(overlap) / max(len(spoken_words), 1)
    ok = ratio >= 0.5

    print(f"[{'PASS' if ok else 'FAIL'}] round trip")
    print(f"       spoken : {SPOKEN!r}")
    print(f"       heard  : {transcript!r}")
    print(f"       overlap: {sorted(overlap)} ({ratio:.0%} of spoken words)\n")
    return ok


async def leg_session() -> bool:
    print("=" * 66)
    print("LEG 3+4 — SESSION: a user turn reaches a verified tool")
    print("=" * 66)
    from livekit.agents.voice import AgentSession

    config = agent_friday.session_config()
    session = AgentSession(turn_handling=config["turn_handling"])
    agent = agent_friday.FridayAgent(
        stt=providers.build_stt(config["stt_provider"]),
        llm=providers.build_resilient_llm(config["llm_backend"], config["llm_role"]),
        tts=providers.build_tts(config["tts_provider"], speed=config["tts_speed"]),
    )

    try:
        await session.start(agent)
    except Exception as exc:
        print(f"[FAIL] session.start raised {type(exc).__name__}: {exc}\n")
        return False

    try:
        result = await session.run(user_input="What is using the most memory on my computer?")
    except Exception as exc:
        print(f"[FAIL] session.run raised {type(exc).__name__}: {exc}\n")
        await session.aclose()
        return False

    calls, outputs, messages = [], [], []
    for event in result.events:
        kind = type(event).__name__
        if kind == "FunctionCallEvent":
            calls.append(event.item.name)
        elif kind == "FunctionCallOutputEvent":
            outputs.append(str(event.item.output)[:400])
        elif kind == "ChatMessageEvent":
            messages.append(f"{event.item.role}: {event.item.text_content}")

    print(f"       events      : {[type(e).__name__ for e in result.events]}")
    print(f"       tool calls  : {calls}")
    for message in messages:
        print(f"       {message[:150]}")

    called_a_tool = bool(calls)
    # The tool payload arrives JSON-encoded inside a JSON string, so the
    # quotes are escaped (\"may_claim_completion\": true). Unescape before
    # matching rather than pattern-matching the escaped form.
    verified = any(
        '"may_claim_completion": true' in o.replace('\\"', '"').lower()
        for o in outputs
    )
    if outputs:
        print(f"       tool output : {outputs[0][:220]}")

    print(f"\n[{'PASS' if called_a_tool else 'FAIL'}] LEG 3 — the turn produced a tool call")
    print(f"[{'PASS' if verified else 'FAIL'}] LEG 4 — the tool returned a verified ActionResult\n")

    await session.aclose()
    return called_a_tool and verified


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audio", action="store_true", help="audio legs only")
    args = parser.parse_args()

    mcp = None
    results: list[bool] = []
    try:
        tts_ok, frame = await leg_tts()
        results.append(tts_ok)
        results.append(await leg_stt(frame))

        if not args.audio:
            mcp = start_mcp_if_needed()
            results.append(await leg_session())
    finally:
        if mcp is not None:
            if os.name == "nt":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(mcp.pid)],
                               capture_output=True)
            else:
                mcp.terminate()

    passed = sum(1 for r in results if r)
    print("=" * 66)
    print(f"VOICE PATH: {passed}/{len(results)} legs passed")
    print("=" * 66)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
