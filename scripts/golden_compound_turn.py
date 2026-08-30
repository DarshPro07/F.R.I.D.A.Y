#!/usr/bin/env python3
"""
The compound request, through the production seam, without a microphone.

Reconstructed from a failed voice run. The boss asked for four things in one
breath and the reply executed all four itself:

    system_resource_usage -> system_list_processes -> apps_open -> web_search

four tool calls against LiveKit's budget of three, so LiveKit issued its final
tools-disabled request, Gemini answered UNEXPECTED_TOOL_CALL, the fallback
inherited the history and died on a missing thought signature. The provider
failure was real; it was reached by a request that should never have been in
the conversational loop.

This gate asserts the two things that have to be true, in order:

    admitted    the turn becomes a durable run with a task graph
    owned       the reply does not do that run's work

It drives `prepare_turn` directly, which is exactly what the production text
callback does (`text_input_callback` in agent_friday.py) - `session.run()`
alone would skip the seam and measure nothing.

    python scripts/golden_compound_turn.py
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SPOKEN = ("Friday, check my computer, open Paint, find one current technology "
          "story, and finish the complete job without me saying continue.")

#: What the reply did in the failed run. None of these may happen here.
FORBIDDEN = {"system_resource_usage", "system_list_processes", "apps_open",
             "web_search", "web_deep_research", "files_write"}

#: LiveKit's per-turn tool budget. Reaching it is what entered the broken path.
TOOL_BUDGET = 3


def start_mcp():
    from friday import health

    if health.serving():
        return None
    python = ROOT / ".venv" / "Scripts" / "python.exe"
    proc = subprocess.Popen(
        [str(python if python.exists() else sys.executable), "server.py"],
        cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not health.wait_until_serving():
        raise SystemExit("the MCP server never started serving; nothing to gate")
    return proc


async def main() -> int:
    from livekit.agents.voice import AgentSession

    import agent_friday
    from friday import objectives as O
    from friday import providers
    from friday.toolsets import objectives as OT

    store = OT.store()
    before = {run["run_id"] for run in store.objective_runs(limit=50)}

    config = agent_friday.session_config()
    session = AgentSession(turn_handling=config["turn_handling"])
    agent = agent_friday.FridayAgent(
        stt=providers.build_stt(config["stt_provider"]),
        llm=providers.build_resilient_llm(config["llm_backend"],
                                          config["llm_role"]),
        tts=providers.build_tts(config["tts_provider"]),
    )
    await session.start(agent)
    await asyncio.sleep(2.0)

    if not agent._router.all_tools:
        print("[SKIP] the MCP toolset is empty - nothing below measures anything")
        return 2
    print(f"tool surface: {len(agent._router.all_tools)} tools\n")
    print(f"> {SPOKEN}\n")

    # --- 1. admission ------------------------------------------------------
    turn_ctx = agent.chat_ctx.copy()
    agent.prepare_turn(turn_ctx, SPOKEN)

    after = {run["run_id"] for run in store.objective_runs(limit=50)}
    admitted = sorted(after - before)
    failures = []

    if len(admitted) != 1:
        print(f"  [FAIL] admission created {len(admitted)} durable run(s); "
              f"expected exactly one")
        failures.append("admission")
        run_id = ""
    else:
        run_id = admitted[0]
        tasks = store.objective_tasks(run_id)
        print(f"  [PASS] admitted {run_id} with {len(tasks)} task(s): "
              f"{[t['capability'] for t in tasks]}")
        if len(tasks) < 2:
            print("  [FAIL] a compound request compiled to fewer than 2 tasks")
            failures.append("plan")

    # --- 2. ownership ------------------------------------------------------
    from friday import ownership

    refused = [name for name in sorted(FORBIDDEN)
               if ownership.claimed_by(name)]
    allowed = sorted(FORBIDDEN - set(refused))
    print(f"  claimed from the reply: {refused}")
    if allowed:
        print(f"  [WARN] still available to the reply: {allowed}")
    if len(allowed) >= TOOL_BUDGET:
        print(f"  [FAIL] {len(allowed)} of the objective's capabilities are "
              f"still callable - enough to reach the {TOOL_BUDGET}-step ceiling")
        failures.append("ownership")
    else:
        print(f"  [PASS] at most {len(allowed)} conversational tool call(s) "
              f"possible, under the budget of {TOOL_BUDGET}")

    # --- 3. the reply itself ----------------------------------------------
    started = time.monotonic()
    try:
        outcome = await session.run(user_input=SPOKEN)
    except Exception as exc:                                # noqa: BLE001
        from friday.provider_diagnostics import describe_failure
        print(f"  [FAIL] the reply raised: {describe_failure(exc)}")
        return 1

    took = time.monotonic() - started
    calls, said = [], []
    for event in outcome.events:
        item = getattr(event, "item", None)
        if item is None:
            continue
        if getattr(item, "type", "") == "function_call":
            calls.append(item.name)
        text = getattr(item, "text_content", None)
        if text:
            said.append(text)

    spoke = " ".join(said).strip()
    print(f"\n  reply: {took:4.1f}s calls={calls}")
    print(f"  said : {spoke[:200]!r}")

    duplicated = [name for name in calls if name in FORBIDDEN]
    if duplicated:
        print(f"  [FAIL] the reply did the objective's work: {duplicated}")
        failures.append("duplication")
    if len(calls) > TOOL_BUDGET:
        print(f"  [FAIL] {len(calls)} tool calls in one turn - the ceiling that "
              f"entered the broken provider path")
        failures.append("ceiling")
    if not spoke:
        print("  [FAIL] the reply said nothing")
        failures.append("silent")

    if run_id:
        run = store.objective_run(run_id)
        print(f"\n  objective {run_id}: {run['status'] if run else 'missing'}")

    print("\n" + "=" * 70)
    print("COMPOUND TURN GATE:",
          "PASS" if not failures else f"FAIL ({', '.join(failures)})")
    return 1 if failures else 0


if __name__ == "__main__":
    mcp = start_mcp()
    try:
        sys.exit(asyncio.run(main()))
    finally:
        if mcp is not None:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(mcp.pid)],
                           capture_output=True)
