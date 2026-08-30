#!/usr/bin/env python3
"""
Phase 1D golden journey (§13): memory must survive a full restart.

The point of this script is that the phases run in **separate operating system
processes**. A new Store object in the same process proves nothing about
durability; a second `python.exe` reading what the first one wrote does.

    python scripts/golden_1d.py           # write -> restart -> read -> agent
    python scripts/golden_1d.py --no-agent   # skip the LLM turn (no spend)

Phases, each its own process:
    1  write   store facts, a preference, an inference and a decision
    2  read    fresh process opens the same database and recalls them
    3  agent   fresh process asks the LLM "what language...?" over MCP
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
PYTHON = str(PYTHON if PYTHON.exists() else sys.executable)

SUBJECT = "Project Arc Reactor.language"


def show(label, result) -> bool:
    ok = result.may_claim_completion
    print(f"[{'PASS' if ok else 'FAIL'}] {label}")
    print(f"       status={result.status}")
    if result.verification:
        print(f"       verify: {result.verification.evidence}")
    if result.error:
        print(f"       error: {result.error[:160]}")
    print()
    return ok


# ---------------------------------------------------------------------------
# Phase 1 — write (own process)
# ---------------------------------------------------------------------------


def phase_write() -> int:
    from friday import contracts as c
    from friday.store import FACT, INFERENCE, PREFERENCE
    from friday.toolsets import memory as M

    print(f"[write] pid={os.getpid()} db={os.environ['ADA_DB']}")
    run = c.Run.create("Remember the Arc Reactor project details.", capability="memory")
    ok = []

    ok.append(show("remember FACT (language)", M.memory_remember(
        run, SUBJECT, "Python", kind=FACT, source="user stated it in conversation")))
    ok.append(show("remember FACT (database)", M.memory_remember(
        run, "Project Arc Reactor.database", "SQLite", kind=FACT,
        source="user stated it in conversation")))
    ok.append(show("remember PREFERENCE", M.memory_remember(
        run, "user.tooling", "local-first tools", kind=PREFERENCE,
        source="stated repeatedly")))
    ok.append(show("remember INFERENCE (low confidence)", M.memory_remember(
        run, "user.timezone", "IST", kind=INFERENCE,
        source="guessed from message timestamps", confidence=0.6)))
    ok.append(show("record a project decision", M.project_record_decision(
        run, "Arc Reactor", "Use SQLite rather than JSON files",
        rationale="JSON memory silently trimmed old entries")))

    M.store().add_message("conv-golden-1d", "user", "Remember the Arc Reactor details.")
    M.store().close_conversation("conv-golden-1d", "Set up Arc Reactor project memory.")
    M.reset_store(None)
    return 0 if all(ok) else 1


# ---------------------------------------------------------------------------
# Phase 2 — read after restart (own process)
# ---------------------------------------------------------------------------


def phase_read() -> int:
    from friday import contracts as c
    from friday.store import FACT, INFERENCE
    from friday.toolsets import memory as M

    print(f"[read ] pid={os.getpid()} db={os.environ['ADA_DB']}")
    run = c.Run.create("What language is the Arc Reactor project?", capability="memory")
    ok = []

    recalled = M.memory_recall(run, SUBJECT)
    ok.append(show("recall after restart", recalled))
    if recalled.may_claim_completion:
        entry = recalled.output["memories"][0]
        print(f"       value       : {entry['value']}")
        print(f"       kind        : {entry['kind']}")
        print(f"       source      : {entry['source']}")
        print(f"       recorded_at : {entry['recorded_at']}")
        print(f"       spoken_form : {entry['spoken_form']}\n")
        ok.append(entry["value"] == "Python")
        ok.append(entry["kind"] == FACT and bool(entry["source"]))

    inferred = M.memory_recall(run, "user.timezone")
    ok.append(show("recall an INFERENCE", inferred))
    if inferred.may_claim_completion:
        spoken = inferred.output["memories"][0]["spoken_form"]
        print(f"       spoken_form : {spoken}")
        hedged = "inferred" in spoken.lower()
        print(f"[{'PASS' if hedged else 'FAIL'}] an inference is not spoken as a fact\n")
        ok.append(hedged)

    ok.append(show("project context", M.project_context(run, "Arc Reactor")))
    recap = M.session_recap(run)
    ok.append(show("session recap ('where were we')", recap))

    again = M.session_recap(run)
    same = (again.may_claim_completion
            and len(again.output["conversations"])
            == len(recap.output["conversations"]))
    print(f"[{'PASS' if same else 'FAIL'}] recap is non-destructive "
          f"(Mark-L's pop_last_session consumed it)\n")
    ok.append(same)

    M.reset_store(None)
    return 0 if all(ok) else 1


# ---------------------------------------------------------------------------
# Phase 3 — the agent answers from memory (own process)
# ---------------------------------------------------------------------------


def phase_agent() -> int:
    import asyncio

    from dotenv import load_dotenv

    load_dotenv()
    import agent_friday
    from friday import providers

    print(f"[agent] pid={os.getpid()} db={os.environ['ADA_DB']}")

    async def turn():
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
            result = await session.run(
                user_input="What language are we using for the Arc Reactor project?"
            )
            calls, replies = [], []
            for event in result.events:
                kind = type(event).__name__
                if kind == "FunctionCallEvent":
                    calls.append(event.item.name)
                elif kind == "ChatMessageEvent" and event.item.role == "assistant":
                    replies.append(event.item.text_content or "")
            return calls, replies
        finally:
            await session.aclose()

    calls, replies = asyncio.run(turn())
    answer = " ".join(replies)
    print(f"       tool calls : {calls}")
    print(f"       answer     : {answer[:220]}\n")

    used_memory = any("memory" in name for name in calls)
    said_python = "python" in answer.lower()
    print(f"[{'PASS' if used_memory else 'FAIL'}] the agent consulted memory")
    print(f"[{'PASS' if said_python else 'FAIL'}] the agent answered 'Python' "
          f"from the persisted fact\n")
    return 0 if (used_memory and said_python) else 1


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def port_open(port: int = 8000) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def run_phase(name: str, db: str, env_extra: dict | None = None) -> bool:
    env = dict(os.environ, ADA_DB=db, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8",
               **(env_extra or {}))
    print("=" * 66)
    print(f"PHASE: {name}  (separate process)")
    print("=" * 66)
    proc = subprocess.run([PYTHON, __file__, "--phase", name], cwd=str(ROOT), env=env)
    return proc.returncode == 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=["write", "read", "agent"])
    parser.add_argument("--no-agent", action="store_true")
    args = parser.parse_args()

    if args.phase == "write":
        return phase_write()
    if args.phase == "read":
        return phase_read()
    if args.phase == "agent":
        return phase_agent()

    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "golden_1d.sqlite3")
        results = [run_phase("write", db)]

        exists = Path(db).exists()
        size = Path(db).stat().st_size if exists else 0
        print("=" * 66)
        print("RESTART: the writing process has exited completely")
        print(f"         database on disk: {exists}, {size} bytes")
        print("=" * 66 + "\n")
        results.append(exists and size > 0)

        results.append(run_phase("read", db))

        if not args.no_agent:
            mcp = None
            if not port_open():
                print("[main] starting MCP server for the agent phase ...")
                mcp = subprocess.Popen(
                    [PYTHON, "server.py"], cwd=str(ROOT),
                    env=dict(os.environ, ADA_DB=db),
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                deadline = time.monotonic() + 45
                while time.monotonic() < deadline and not port_open():
                    time.sleep(0.25)
            try:
                results.append(run_phase("agent", db))
            finally:
                if mcp is not None:
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(mcp.pid)],
                                   capture_output=True) if os.name == "nt" else mcp.terminate()

    passed = sum(1 for r in results if r)
    print("=" * 66)
    print(f"RESULT: {passed}/{len(results)} phases passed")
    print("=" * 66)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
