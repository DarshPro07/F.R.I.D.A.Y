#!/usr/bin/env python3
"""
Three web modes, live: fast, research, deep - and the agent choosing between
them.

The point of the split is latency. A question with a short factual answer must
not pay for six page loads, and a research question must not be answered from
a search snippet. This measures both and prints the numbers.

    python scripts/golden_research.py
"""

from __future__ import annotations

import asyncio
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from friday import contracts as c  # noqa: E402
from friday.toolsets import research as R  # noqa: E402

FAST_QUESTION = "What is the latest stable version of livekit-agents for python?"
CRAWL_URLS = ("https://en.wikipedia.org/wiki/Web_crawler "
              "https://en.wikipedia.org/wiki/Web_scraping")
DEEP_QUESTION = "long term memory architectures for LLM agents"
AGENT_ASK = "research how people build long term memory for AI agents"

#: The fast path is the one the boss hears. Beyond this it is not a fast path.
FAST_BUDGET_SECONDS = 12.0


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


def run_for(label: str) -> c.Run:
    return c.Run.create(label, capability="web")


def check(passed: bool, message: str) -> bool:
    print(f"  [{'PASS' if passed else 'FAIL'}] {message}")
    return passed


async def modes() -> list[bool]:
    results: list[bool] = []

    print("=" * 70)
    print("FAST - one grounded call, no browser")
    print("=" * 70)
    started = time.monotonic()
    result = await R.web_answer(run_for("fast"), FAST_QUESTION)
    took = time.monotonic() - started
    output = result.output or {}
    print(f"  {took:.1f}s  status={result.status}")
    print(f"  answer     : {(output.get('answer') or result.error or '')[:180]}")
    print(f"  citations  : {len(output.get('citations') or [])}")
    print(f"  searches   : {output.get('searches')}")
    results.append(check(result.status == c.SUCCEEDED, "answered"))
    results.append(check(bool(output.get("citations")),
                         "grounded in real sources, not memory"))
    results.append(check(took < FAST_BUDGET_SECONDS,
                         f"stayed under {FAST_BUDGET_SECONDS:.0f}s ({took:.1f}s)"))

    print("\n" + "=" * 70)
    print("RESEARCH - read the pages properly")
    print("=" * 70)
    started = time.monotonic()
    result = await R.web_crawl(run_for("crawl"), CRAWL_URLS, max_chars=4000)
    took = time.monotonic() - started
    output = result.output or {}
    pages = output.get("pages") or []
    print(f"  {took:.1f}s  status={result.status}  engine={output.get('engine')}")
    for page in pages:
        print(f"    [{page['engine']:<9}] {page['chars']:>7} chars  "
              f"{page['title'][:40]!r}")
    for bad in output.get("unreadable") or []:
        print(f"    FAILED {bad}")
    results.append(check(len(pages) >= 1, "read at least one page"))

    furniture = ("cookie", "skip to content", "privacy policy", "subscribe now")
    clean = all(
        sum(word in (page.get("markdown") or "").lower() for word in furniture) <= 1
        for page in pages
    )
    results.append(check(clean, "main content only - no nav, banners or footers"))
    results.append(check(all(page["markdown"].strip() for page in pages),
                        "every page returned actual text"))

    print("\n" + "=" * 70)
    print("DEEP - search, read, rank, budget")
    print("=" * 70)
    started = time.monotonic()
    result = await R.web_deep_research(run_for("deep"), DEEP_QUESTION, sources=4)
    took = time.monotonic() - started
    output = result.output or {}
    sources = output.get("sources") or []
    print(f"  {took:.1f}s  status={result.status}  "
          f"provider={output.get('search_provider')}")
    print(f"  considered={output.get('considered')} read={output.get('read')} "
          f"corpus={output.get('corpus_chars')} chars")
    for source in sources:
        print(f"    rel={source['relevance']:>4}  "
              f"{len(source['markdown']):>6} chars  {source['url'][:64]}")
    print(f"  dropped for budget : {output.get('dropped_for_budget')}")
    print(f"  unreadable         : {output.get('unreadable')}")

    results.append(check(len(sources) >= 2, "read more than one source"))
    results.append(check(
        (output.get("corpus_chars") or 0) <= R.DEFAULT_CORPUS_CHARS,
        f"corpus stayed inside its budget "
        f"({output.get('corpus_chars')} <= {R.DEFAULT_CORPUS_CHARS})"))
    results.append(check(
        sources == sorted(sources, key=lambda s: s["relevance"], reverse=True),
        "sources ranked by relevance"))
    results.append(check(all(source["url"] for source in sources),
                        "every source is citable"))
    return results


async def agent_journey() -> list[bool]:
    """Does the model reach for research when the question deserves it?"""
    from livekit.agents.voice import AgentSession

    import agent_friday
    from friday import providers

    print("\n" + "=" * 70)
    print(f"[you] {AGENT_ASK}")
    print("=" * 70)

    config = agent_friday.session_config()
    session = AgentSession(turn_handling=config["turn_handling"])
    agent = agent_friday.FridayAgent(
        stt=providers.build_stt(config["stt_provider"]),
        llm=providers.build_resilient_llm(config["llm_backend"], config["llm_role"]),
        tts=providers.build_tts(config["tts_provider"]),
    )
    await session.start(agent)
    try:
        await asyncio.sleep(2.0)
        started = time.monotonic()
        result = await session.run(user_input=AGENT_ASK)
        took = time.monotonic() - started

        calls, replies = [], []
        for event in result.events:
            kind = type(event).__name__
            if kind == "FunctionCallEvent":
                calls.append(event.item.name)
            elif kind == "ChatMessageEvent" and event.item.role == "assistant":
                replies.append(event.item.text_content or "")
        spoken = " ".join(replies)
        used = agent._router.last_used if hasattr(agent._router, "last_used") else []

        print(f"  {took:.1f}s")
        print(f"  tool calls : {calls}")
        print(f"  reply      : {spoken[:260]}\n")

        reached = "search_capabilities" in calls or "web_deep_research" in calls
        answered = len(spoken.strip()) > 60
        refused = any(phrase in spoken.lower() for phrase in
                      ("i don't have", "i can't", "not able to", "unable to"))
        return [
            check(reached, "went looking for a research capability"),
            check(answered, "came back with something substantial"),
            check(not refused, "did not claim it lacked the ability"),
        ]
    finally:
        await session.aclose()


def main() -> int:
    mcp = start_mcp()
    try:
        results = asyncio.run(modes())
        results += asyncio.run(agent_journey())
    finally:
        if mcp is not None:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(mcp.pid)],
                           capture_output=True)

    passed = sum(1 for r in results if r)
    print("\n" + "=" * 70)
    print(f"RESULT: {passed}/{len(results)} checks passed")
    print("=" * 70)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
