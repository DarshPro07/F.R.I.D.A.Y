#!/usr/bin/env python3
"""
Dynamic toolsets end to end: does the agent load what it needs, unprompted?

Starts a real session, checks the tool surface narrowed, then asks for things
that need groups the model cannot see and watches whether it enables them.

    python scripts/golden_router.py
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

import agent_friday  # noqa: E402
from friday import capability_router as R  # noqa: E402
from friday import providers  # noqa: E402

ASKS = [
    ("look at my screen and tell me what application is in front", "vision"),
    ("what files are in my workspace", "files"),
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


async def journey() -> list[bool]:
    from livekit.agents.voice import AgentSession

    results: list[bool] = []
    config = agent_friday.session_config()
    session = AgentSession(turn_handling=config["turn_handling"])
    agent = agent_friday.FridayAgent(
        stt=providers.build_stt(config["stt_provider"]),
        llm=providers.build_resilient_llm(config["llm_backend"], config["llm_role"]),
        tts=providers.build_tts(config["tts_provider"]),
    )
    await session.start(agent)
    try:
        await asyncio.sleep(2.0)  # let on_enter narrow the surface
        described = agent._router.describe()
        print("=" * 70)
        print("AFTER SESSION START")
        print("=" * 70)
        print(f"  tools known   : {described['total_tools']}")
        print(f"  tools active  : {described['active_tools']}")
        print(f"  groups on     : {described['enabled_groups'] or 'none'}")
        print(f"  groups waiting: {described['available_groups']}\n")

        narrowed = 0 < described["active_tools"] < described["total_tools"]
        print(f"[{'PASS' if narrowed else 'FAIL'}] the surface narrowed\n")
        results.append(narrowed)

        for ask, expected_group in ASKS:
            print("=" * 70)
            print(f'[you] {ask}')
            print("=" * 70)
            result = await session.run(user_input=ask)

            calls, replies = [], []
            for event in result.events:
                kind = type(event).__name__
                if kind == "FunctionCallEvent":
                    calls.append(event.item.name)
                elif kind == "ChatMessageEvent" and event.item.role == "assistant":
                    replies.append(event.item.text_content or "")

            print(f"  tool calls : {calls}")
            print(f"  reply      : {' '.join(replies)[:220]}\n")

            searched = "search_capabilities" in calls
            used = "use_capability" in calls
            refused = any(p in " ".join(replies).lower() for p in
                          ("i don't have", "i can't", "not able to", "unable to"))

            print(f"  [{'PASS' if searched else 'FAIL'}] searched for the "
                  f"{expected_group} capability")
            print(f"  [{'PASS' if used else 'FAIL'}] then invoked it via "
                  f"use_capability")
            print(f"  [{'PASS' if not refused else 'FAIL'}] did not claim it "
                  f"lacked the ability\n")
            results += [searched, used, not refused]
        return results
    finally:
        await session.aclose()


def main() -> int:
    mcp = start_mcp()
    try:
        results = asyncio.run(journey())
    finally:
        if mcp is not None:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(mcp.pid)],
                           capture_output=True)

    passed = sum(1 for r in results if r)
    print("=" * 70)
    print(f"RESULT: {passed}/{len(results)} checks passed")
    print("=" * 70)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
