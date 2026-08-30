#!/usr/bin/env python3
"""
Does Friday learn on its own, and does it remember on the next turn?

The unit tests prove the machinery. This proves the thing that actually failed
before: a real session, with a real model that was never told to file a
memory, ends up knowing something it was not asked to store - and what it
learned is in front of it next time it is asked.

Drives the production text path (`text_input_callback`), not `session.run()`,
because `session.run()` calls `generate_reply` directly and skips both hooks.

Runs against a throwaway database, so the real profile is untouched.

    python scripts/golden_autolearn.py
"""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

#: Specific enough that the model cannot have guessed it back.
CONFESSION = ("I do all my work on a sixteen core Windows laptop, and the "
              "project I'm building right now is called ADA")
QUESTION = "what machine do I work on?"


def port_open(port: int = 8000) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def start_mcp(db_path: str):
    if port_open():
        print("! an MCP server is already running - it owns its own database, "
              "so this run may touch the real profile\n")
        return None
    python = ROOT / ".venv" / "Scripts" / "python.exe"
    proc = subprocess.Popen(
        [str(python if python.exists() else sys.executable), "server.py"],
        cwd=str(ROOT), env={**os.environ, "ADA_DB": db_path},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline and not port_open():
        time.sleep(0.25)
    return proc


class Typed:
    """What the room hands the text callback."""

    def __init__(self, text: str):
        self.text = text
        self.info = None
        self.participant = None


async def journey() -> list[bool]:
    from livekit.agents.voice import AgentSession

    import agent_friday
    from friday import providers

    results: list[bool] = []
    config = agent_friday.session_config()
    session = AgentSession(turn_handling=config["turn_handling"])
    agent = agent_friday.FridayAgent(
        stt=providers.build_stt(config["stt_provider"]),
        llm=providers.build_resilient_llm(config["llm_backend"], config["llm_role"]),
        tts=providers.build_tts(config["tts_provider"]),
    )
    await session.start(agent)
    say = agent_friday.text_input_callback(agent)
    learner = agent._learner
    try:
        await asyncio.sleep(2.0)  # on_enter: narrow the tools, load the briefing

        print("=" * 70)
        print(f"[you] {CONFESSION}")
        print("=" * 70)
        handle = await say(session, Typed(CONFESSION))
        # Sample before the reply plays: the learner may finish first, and it
        # did - the queue was already empty by the time the audio ended.
        queued = learner._queue.qsize() > 0
        await handle.wait_for_playout()
        print(f"  reply      : {reply_text(handle)[:200]}\n")

        print(f"  [{'PASS' if queued else 'FAIL'}] the turn was queued to learn "
              f"from, with nobody asking for it")
        results.append(queued)

        print("  waiting for the background learner...")
        started = time.monotonic()
        try:
            await asyncio.wait_for(learner._queue.join(), 120)
        except asyncio.TimeoutError:
            print("  [FAIL] learning did not finish within 120s\n")
            return results + [False, False, False, False]
        print(f"  learned in {time.monotonic() - started:.1f}s\n")

        stored = learner.learned > 0
        print(f"  [{'PASS' if stored else 'FAIL'}] it stored something "
              f"({learner.learned} item(s))")

        brief = learner.brief
        print(f"\n  briefing now:\n{brief or '    (empty)'}\n")
        knows = any(word in brief.lower() for word in ("windows", "ada", "laptop"))
        print(f"  [{'PASS' if knows else 'FAIL'}] the briefing carries what he "
              f"just said\n")
        results += [stored, knows]

        # The half that matters: is it in front of the model on the next turn?
        print("=" * 70)
        print(f"[you] {QUESTION}")
        print("=" * 70)
        before = learner._queue.qsize()
        handle = await say(session, Typed(QUESTION))
        await handle.wait_for_playout()
        spoken = reply_text(handle)
        print(f"  reply      : {spoken[:240]}\n")

        recalled = any(word in spoken.lower()
                       for word in ("windows", "sixteen", "16-core", "16 core"))
        asked_nothing = learner._queue.qsize() >= before  # still observing turns
        print(f"  [{'PASS' if recalled else 'FAIL'}] it answered from what it "
              f"learned, not from the question")
        print(f"  [{'PASS' if asked_nothing else 'FAIL'}] the second turn was "
              f"observed too\n")
        results += [recalled, asked_nothing]
        return results
    finally:
        await session.aclose()


def reply_text(handle) -> str:
    return " ".join(
        (getattr(item, "text_content", "") or "")
        for item in (handle.chat_items or [])
        if getattr(item, "role", None) == "assistant"
    ).strip()


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "golden.sqlite3")
        os.environ["ADA_DB"] = db_path
        mcp = start_mcp(db_path)
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
