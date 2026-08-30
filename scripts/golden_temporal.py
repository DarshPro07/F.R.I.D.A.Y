#!/usr/bin/env python3
"""
Does Friday know what year it is, and does it research instead of projecting?

Asks the exact question that exposed the bug and checks the reply for
forecast-hedging language and for an actual web search.

    python scripts/golden_temporal.py
"""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

import agent_friday  # noqa: E402
from friday import providers  # noqa: E402

QUESTION = ("My idea is to start a print on demand or a dropshipping business. "
            "Can you research about it? Is it profitable in 2026?")

# Phrases that mean it answered from training data instead of looking.
HEDGES = [
    "the future", "future viability", "projection", "project into",
    "as of my last update", "my training", "i don't have data",
    "hard to predict", "upcoming year", "yet to happen", "will be in 2026",
]


def port_open(port: int = 8000) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def start_mcp():
    if port_open():
        return None
    python = ROOT / ".venv" / "Scripts" / "python.exe"
    proc = subprocess.Popen(
        [str(python if python.exists() else sys.executable), "server.py"],
        cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline and not port_open():
        time.sleep(0.25)
    return proc


async def ask() -> tuple[list[str], str]:
    from livekit.agents.voice import AgentSession

    config = agent_friday.session_config()
    session = AgentSession(turn_handling=config["turn_handling"])
    agent = agent_friday.FridayAgent(
        stt=providers.build_stt(config["stt_provider"]),
        llm=providers.build_resilient_llm(config["llm_backend"], config["llm_role"]),
        tts=providers.build_tts(config["tts_provider"]),
    )
    await session.start(agent)
    try:
        result = await session.run(user_input=QUESTION)
        calls, replies = [], []
        for event in result.events:
            kind = type(event).__name__
            if kind == "FunctionCallEvent":
                calls.append(event.item.name)
            elif kind == "ChatMessageEvent" and event.item.role == "assistant":
                replies.append(event.item.text_content or "")
        return calls, " ".join(replies)
    finally:
        await session.aclose()


def main() -> int:
    now = datetime.now().astimezone()
    print("=" * 70)
    print(f"Real clock: {now.strftime('%A %d %B %Y, %H:%M %Z')}")
    print("=" * 70)
    print(agent_friday.temporal_context())
    print("=" * 70)
    print(f"[you] {QUESTION}\n")

    mcp = start_mcp()
    try:
        calls, answer = asyncio.run(ask())
    finally:
        if mcp is not None:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(mcp.pid)],
                           capture_output=True)

    print(f"[tool calls] {calls}\n")
    print(f"[friday] {answer[:900]}\n")

    lowered = answer.lower()
    found = [h for h in HEDGES if h in lowered]
    searched = any("search" in c or "fetch" in c or "news" in c for c in calls)

    print("=" * 70)
    print(f"[{'PASS' if not found else 'FAIL'}] no forecast hedging"
          + (f" -> found: {found}" if found else ""))
    print(f"[{'PASS' if searched else 'FAIL'}] it actually looked something up"
          f" (calls: {calls or 'none'})")
    print("=" * 70)
    return 0 if (not found and searched) else 1


if __name__ == "__main__":
    sys.exit(main())
