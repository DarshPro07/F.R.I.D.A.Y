#!/usr/bin/env python3
"""
Phase 1B golden journeys (§27): web + browser.

    python scripts/golden_1b.py                # search, fetch, news, browser
    python scripts/golden_1b.py --automate     # also run browser.automate

Exit 0 only if every journey produced a verified result.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()  # browser.automate needs GOOGLE_API_KEY; toolsets never load .env themselves

from friday import contracts as c  # noqa: E402
from friday.policy import PolicyEngine  # noqa: E402
from friday.toolsets import web as W  # noqa: E402
from friday.toolsets.system import needs_approval  # noqa: E402

NOISY = {"results", "articles", "text"}


def show(label: str, result: c.ActionResult) -> bool:
    ok = result.may_claim_completion
    mark = "PASS" if ok else ("ASK " if needs_approval(result) else "FAIL")
    print(f"[{mark}] {label}")
    print(f"       status={result.status}  may_claim_completion={ok}")
    if result.verification:
        print(f"       verify: {result.verification.method}")
        print(f"               {result.verification.evidence}")
    if result.error:
        print(f"       error: {result.error[:200]}")
    if isinstance(result.output, dict):
        trimmed = {k: v for k, v in result.output.items() if k not in NOISY}
        print(f"       output: {json.dumps(trimmed, default=str)[:220]}")
    print()
    return ok


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--automate", action="store_true")
    args = parser.parse_args()
    results: list[bool] = []

    print("=" * 66)
    print('JOURNEY: "Search the web for current LiveKit Agents updates."')
    print("=" * 66)
    run = c.Run.create("Search the web for LiveKit Agents updates.", capability="web")
    result = await W.web_search(run, "LiveKit Agents python framework updates", limit=6)
    results.append(show("web.search", result))
    if isinstance(result.output, dict):
        for hit in result.output.get("results", [])[:5]:
            print(f"         - {hit['title'][:62]}")
            print(f"           {hit['url'][:78]}")
        print()

    print("=" * 66)
    print('JOURNEY: "Read that page."')
    print("=" * 66)
    run = c.Run.create("Read the LiveKit docs page.", capability="web")
    fetched = await W.web_fetch(run, "https://docs.livekit.io/agents/")
    results.append(show("web.fetch", fetched))
    if isinstance(fetched.output, dict):
        print(f"         first 160 chars: {fetched.output.get('text','')[:160]!r}\n")

    print("=" * 66)
    print('JOURNEY: "What is in the news?"')
    print("=" * 66)
    run = c.Run.create("What is in the news?", capability="web")
    news = await W.web_news(run, "world", limit=5)
    results.append(show("web.news", news))
    if isinstance(news.output, dict):
        for article in news.output.get("articles", [])[:4]:
            print(f"         [{article['source']}] {article['title'][:60]}")
        print()

    print("=" * 66)
    print('JOURNEY: "Open GitHub and show me livekit/agents."')
    print("=" * 66)
    run = c.Run.create("Open GitHub livekit/agents.", capability="web")
    results.append(show("browser.open",
                        await W.browser_open(run, "https://github.com/livekit/agents",
                                             headless=True)))
    results.append(show("browser.inspect", await W.browser_inspect(run, max_chars=500)))
    results.append(show("browser.navigate",
                        await W.browser_navigate(run, "https://docs.livekit.io/agents/")))

    print("=" * 66)
    print("JOURNEY: automation is ASK-gated, and a bad URL must FAIL")
    print("=" * 66)
    engine = PolicyEngine()
    gated = await W.browser_automate(run, "search for something", engine=engine)
    print(f"[GATE] browser.automate without approval -> {gated.status} "
          f"(needs_approval={needs_approval(gated)})\n")
    results.append(needs_approval(gated))

    bad = await W.browser_open(run, "ftp://example.com/file")
    print(f"[{'PASS' if bad.status == 'failed' else 'FAIL'}] non-http URL -> "
          f"status={bad.status}, may_claim_completion={bad.may_claim_completion}")
    print(f"       error: {bad.error}\n")
    results.append(bad.status == "failed")

    if args.automate:
        print("=" * 66)
        print('JOURNEY: browser.automate - "search GitHub for livekit agents"')
        print("=" * 66)
        engine.approve_for_session("browser.automate")
        auto = await W.browser_automate(
            run, "Go to github.com and search for 'livekit agents'. Stop once "
                 "the search results page is showing.",
            start_url="https://github.com", max_turns=8,
            engine=engine, headless=True,
        )
        results.append(show("browser.automate (approved)", auto))

    print("=" * 66)
    print("JOURNEY: browser must not be left running (§26)")
    print("=" * 66)
    results.append(show("browser.close", await W.browser_close(run)))
    print(f"       session.running is now {W.session.running}\n")
    results.append(W.session.running is False)

    passed = sum(1 for r in results if r)
    print("=" * 66)
    print(f"RESULT: {passed}/{len(results)} journeys produced a verified result")
    print("=" * 66)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
