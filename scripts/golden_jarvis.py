#!/usr/bin/env python3
"""
The local Jarvis journey, end to end.

Every other golden script proves one capability. This one proves they hold
together in a single conversation: open something, look at the machine, read a
file, look at the screen, be told something worth keeping - then throw the
whole runtime away and ask for it back.

The restart is the point. Between the two phases the agent, the session, the
router, the learner and the MCP server process are all destroyed and rebuilt.
Only the database survives, which is exactly the claim being tested: the
conversation is disposable, the memory is not.

Runs against a throwaway database, so the real profile is untouched.

    python scripts/golden_jarvis.py
"""

from __future__ import annotations

import asyncio
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Windows consoles default to cp1252, so an em-dash in a README prints here as
# a replacement character and reads like a decoding bug in files_read. It is
# not: files_read returns U+2014 correctly (checked). Only the display was
# wrong, and a golden script that appears to show corruption is worse than
# useless.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

#: Specific and arbitrary: the model cannot answer this from world knowledge.
SECRET = ("remember that the controller for Project Halo is a Nucleo F446RE "
          "board, and it talks over CAN not I2C")
RECALL = "what controller am I using for Project Halo, and how does it talk?"


class Typed:
    """What the room hands the text callback."""

    def __init__(self, text: str):
        self.text = text
        self.info = None
        self.participant = None


class Step:
    def __init__(self, ask, *, expect_tool=True, must_match=None, why="", label=""):
        self.ask = ask
        self.expect_tool = expect_tool
        self.must_match = must_match      # a regex the answer has to satisfy
        self.why = why
        self.label = label or ask[:40]


#: A real reading has a number and a unit in it.
#:
#: Pinning exact words was wrong twice. First it demanded "memory" and "GB"
#: and got "605.8 MB" of "RAM"; then it demanded "MB" and got "587 megabytes".
#: The second one is not a near miss - she is a voice, and spelling the unit
#: out is what a voice should do. The check has to accept how she speaks.
A_REAL_MEASUREMENT = r"\d[\d.,]*\s*(mb|gb|kb|%|mega|giga|kilo)"

JOURNEY = [
    Step("open youtube in a browser", label="open a page"),
    Step("what's using my RAM right now?",
         must_match=A_REAL_MEASUREMENT,
         why="quotes a real figure rather than describing the idea of memory",
         label="look at the machine"),
    Step("read README.md in this project folder and tell me its first heading",
         label="read a file"),
    Step("look at my screen and tell me what application is in front",
         label="look at the screen"),
    Step(SECRET, label="be told something worth keeping"),
]


def port_open(port: int = 8000) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def start_mcp(db_path: str):
    """A fresh MCP server process, pointed at the throwaway database."""
    if port_open():
        print("! port 8000 is already in use - stop the running server first, "
              "or this will test the wrong database\n")
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


def stop_mcp(proc) -> None:
    if proc is None:
        return
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                   capture_output=True)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and port_open():
        time.sleep(0.25)


async def new_agent():
    """A complete runtime: session, agent, router, learner, all brand new."""
    from livekit.agents.voice import AgentSession

    import agent_friday
    from friday import providers, resilience

    config = agent_friday.session_config()
    session = AgentSession(turn_handling=config["turn_handling"])
    agent = agent_friday.FridayAgent(
        stt=providers.build_stt(config["stt_provider"]),
        llm=providers.build_resilient_llm(config["llm_backend"], config["llm_role"]),
        tts=providers.build_tts(config["tts_provider"]),
    )
    await session.start(agent)
    guard = resilience.TurnGuard()
    guard.attach(session)
    await asyncio.sleep(2.0)  # on_enter: narrow the tools, load the briefing
    return session, agent, guard, agent_friday.text_input_callback(agent)


def reply_text(handle) -> str:
    if handle is None:
        return ""
    return " ".join(
        (getattr(item, "text_content", "") or "")
        for item in (handle.chat_items or [])
        if getattr(item, "role", None) == "assistant"
    ).strip()


def tools_used(handle) -> list[str]:
    if handle is None:
        return []
    return [getattr(item, "name", "?") for item in (handle.chat_items or [])
            if type(item).__name__ == "FunctionCall"]


REFUSALS = ("i don't have", "i can't", "not able to", "unable to",
            "i do not have", "don't have access")


def check(passed: bool, message: str) -> bool:
    print(f"    [{'PASS' if passed else 'FAIL'}] {message}")
    return passed


async def phase_one(results: list[bool]) -> None:
    session, agent, guard, say = await new_agent()
    try:
        print(f"  runtime up: {agent._router.describe()}\n")
        for index, step in enumerate(JOURNEY, 1):
            print("=" * 70)
            print(f"[{index}/{len(JOURNEY)}] {step.label}")
            print(f"[you] {step.ask}")
            print("=" * 70)

            started = time.monotonic()
            handle = await say(session, Typed(step.ask))
            await handle.wait_for_playout()
            took = time.monotonic() - started

            spoken = reply_text(handle)
            calls = tools_used(handle)
            print(f"  {took:.1f}s  tools: {calls or 'none'}")
            print(f"  reply: {spoken[:200]}\n")

            results.append(check(bool(spoken.strip()), "answered at all"))
            results.append(check(
                not any(phrase in spoken.lower() for phrase in REFUSALS),
                "did not claim it lacked the ability"))
            if step.expect_tool:
                results.append(check(bool(calls), "reached for a real tool"))
            if step.must_match:
                results.append(check(
                    bool(re.search(step.must_match, spoken.lower())), step.why))

        print("  waiting for the background learner...")
        try:
            await asyncio.wait_for(agent._learner._queue.join(), 120)
        except asyncio.TimeoutError:
            results.append(check(False, "the learner drained before shutdown"))
        print(f"  learned {agent._learner.learned} item(s)")
        print(f"  resilience: {guard.describe()}\n")
        results.append(check(guard.rescued == 0 or guard.empty_completions > 0,
                             "no turn ended in silence"))
    finally:
        await session.aclose()


async def phase_two(results: list[bool]) -> None:
    session, agent, guard, say = await new_agent()
    try:
        print("=" * 70)
        print(f"[you] {RECALL}")
        print("=" * 70)
        started = time.monotonic()
        handle = await say(session, Typed(RECALL))
        await handle.wait_for_playout()
        spoken = reply_text(handle)
        print(f"  {time.monotonic() - started:.1f}s  "
              f"tools: {tools_used(handle) or 'none'}")
        print(f"  reply: {spoken[:260]}\n")

        lowered = spoken.lower()
        results.append(check("nucleo" in lowered or "f446" in lowered,
                             "remembered the board across the restart"))
        # \bcan\b, not lowered.split(): it answered "using CAN." and the
        # trailing full stop made the token "can." - the memory was fine, the
        # check was not.
        results.append(check(bool(re.search(r"\bcan\b", lowered)),
                             "remembered how it talks"))
        results.append(check("i2c" not in lowered.replace("not i2c", ""),
                             "did not repeat the thing it was told it is NOT"))
        results.append(check(
            not any(phrase in lowered for phrase in REFUSALS),
            "did not claim it had forgotten"))
    finally:
        await session.aclose()


def main() -> int:
    results: list[bool] = []
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "jarvis.sqlite3")
        os.environ["ADA_DB"] = db_path

        print("\n" + "#" * 70)
        print("# PHASE ONE - one conversation, five real things")
        print("#" * 70 + "\n")
        mcp = start_mcp(db_path)
        try:
            asyncio.run(phase_one(results))
        finally:
            stop_mcp(mcp)

        print("\n" + "#" * 70)
        print("# RESTART - agent, session, router, learner and MCP server all gone")
        print("#" * 70 + "\n")

        print("#" * 70)
        print("# PHASE TWO - a runtime that has never met him, asking the database")
        print("#" * 70 + "\n")
        mcp = start_mcp(db_path)
        try:
            asyncio.run(phase_two(results))
        finally:
            stop_mcp(mcp)

    passed = sum(1 for r in results if r)
    print("\n" + "=" * 70)
    print(f"RESULT: {passed}/{len(results)} checks passed")
    print("=" * 70)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
