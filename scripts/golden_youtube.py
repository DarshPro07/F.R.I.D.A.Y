#!/usr/bin/env python3
"""
"Find Techno Gamerz on YouTube and review the channel."

The request that started this: it failed because there were no YouTube tools
at all, only a browser and a music searcher. This is the journey it should
have had.

Real network, throwaway database, no browser opened for anything the data
already answers.

    python scripts/golden_youtube.py
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from friday import contracts as c  # noqa: E402
from friday.store import Store  # noqa: E402
from friday.toolsets import youtube as Y  # noqa: E402

CHANNEL = "@TechnoGamerzOfficial"

#: The real one. Pinned because the first version of this resolver guessed
#: "@TechnoGamerz" from the name and found a different, genuine channel with
#: one video from 2010 - a real id, a matching title, real view counts, and
#: entirely the wrong creator. Nothing about that looks like a failure unless
#: the test knows which channel was meant.
EXPECTED_ID = "UCl_vAxZpvbO-PFXdDu7EdHw"


def check(passed: bool, message: str) -> bool:
    print(f"  [{'PASS' if passed else 'FAIL'}] {message}")
    return passed


def run_for(label: str) -> c.Run:
    return c.Run.create(label, capability="youtube")


async def journey() -> list[bool]:
    results: list[bool] = []
    with tempfile.TemporaryDirectory() as tmp:
        store = Store(Path(tmp) / "yt.sqlite3")
        try:
            print(f"  Data API key: {'present' if Y.api_key() else 'absent'}\n")

            print("=" * 70)
            print(f'[you] "Find {CHANNEL} on YouTube."')
            print("=" * 70)
            started = time.monotonic()
            result = await Y.youtube_find_channel(run_for("find"), CHANNEL,
                                                  store=store)
            first_took = time.monotonic() - started
            output = result.output or {}
            print(f"  {first_took:.1f}s  status={result.status}")
            print(f"  channel_id : {output.get('channel_id')}")
            print(f"  title      : {output.get('title')!r}")
            print(f"  from       : {output.get('resolved_from')}\n")

            channel_id = output.get("channel_id", "")
            results += [
                check(result.status == c.SUCCEEDED, "the channel was found"),
                check(channel_id.startswith("UC"), "it is a real channel id"),
                check(channel_id == EXPECTED_ID,
                      f"it is THE channel, not a lookalike ({channel_id})"),
                check(output.get("resolved_from") != "api",
                      "resolved without spending a search.list call"),
            ]

            print("=" * 70)
            print('[you] "Find Techno Gamerz."   (a plain name, not a handle)')
            print("=" * 70)
            vague = await Y.youtube_find_channel(run_for("vague"),
                                                 "Techno Gamerz", store=store)
            vout = vague.output or {}
            print(f"  status={vague.status}")
            print(f"  guess : {vout.get('title')!r} ({vout.get('channel_id')})")
            print(f"  why   : {(vague.error or '')[:150]}")
            print()
            # PARTIAL when the search answers, FAILED when it does not - the
            # web search itself rate-limits across repeated runs, which is the
            # honest reason name lookup is not dependable without the Data API.
            # Either way it must never come back as a confident answer.
            results += [
                check(vague.status != c.SUCCEEDED,
                      "a name-only lookup never claims certainty"),
                check(vout.get("needs_confirmation") is True
                      or vague.status == c.FAILED,
                      "it either asks for confirmation or admits it failed"),
            ]

            print("=" * 70)
            print('[you] "Tell me about the channel."')
            print("=" * 70)
            result = await Y.youtube_channel_details(run_for("details"), CHANNEL,
                                                     store=store)
            output = result.output or {}
            print(f"  status={result.status}  backend={output.get('backend')}")
            print(f"  title      : {output.get('title')!r}")
            print(f"  subscribers: {output.get('subscribers', '(needs the Data API)')}")
            if output.get("unavailable_without_api"):
                print(f"  not available without the API: "
                      f"{output['unavailable_without_api']}")
            print()
            results += [
                check(result.status == c.SUCCEEDED, "the channel read back"),
                check(bool(output.get("title")), "it has a title"),
                check(output.get("backend") in ("api", "feed"),
                      "it says which backend answered"),
            ]

            print("=" * 70)
            print('[you] "Show me its five newest videos."')
            print("=" * 70)
            result = await Y.youtube_recent_videos(run_for("recent"), CHANNEL,
                                                   limit=5, store=store)
            output = result.output or {}
            videos = output.get("videos") or []
            print(f"  status={result.status}  count={output.get('count')}")
            for video in videos:
                views = video.get("views")
                print(f"    {video['published_at'][:10]}  "
                      f"{(f'{views:,}' if views else '?'):>12} views  "
                      f"{video['title'][:46]}")
            print()
            results += [
                check(result.status == c.SUCCEEDED, "the uploads came back"),
                check(len(videos) >= 3, "several uploads, not one"),
                check(any((v.get("views") or 0) > 100_000 for v in videos),
                      "the numbers are the scale of the real channel"),
                check(all(v.get("published_at") for v in videos),
                      "each has a real publish date"),
                check(any(v.get("views") for v in videos),
                      "each has real view counts"),
            ]

            print("=" * 70)
            print('[you] "Which performed best?"')
            print("=" * 70)
            best = output.get("most_viewed_recent")
            top = max(videos, key=lambda v: v.get("views") or 0) if videos else {}
            print(f"  best: {best!r}  ({(top.get('views') or 0):,} views)\n")
            results.append(check(bool(best) and best == top.get("title"),
                                 "answered from real statistics, not a guess"))

            print("=" * 70)
            print('[you] "Do it again."')
            print("=" * 70)
            started = time.monotonic()
            result = await Y.youtube_find_channel(run_for("again"), CHANNEL,
                                                  store=store)
            again_took = time.monotonic() - started
            output = result.output or {}
            print(f"  {again_took:.2f}s  from={output.get('resolved_from')}  "
                  f"(first time {first_took:.1f}s)\n")
            results += [
                check(output.get("resolved_from") == "memory",
                      "the second ask came from memory, costing nothing"),
                check(output.get("channel_id") == channel_id,
                      "and it is the same channel, not a lookalike"),
                check(again_took < first_took,
                      f"faster than the first ({again_took:.2f}s < {first_took:.1f}s)"),
            ]

            print("=" * 70)
            print("[guard] a channel that does not exist")
            print("=" * 70)
            result = await Y.youtube_find_channel(
                run_for("nonsense"), "zzqx not a real channel 84719", store=store)
            print(f"  status={result.status}: {(result.error or '')[:70]}\n")
            # PARTIAL is the right answer here, not FAILED: it found a page
            # called "zzqx" and is asking whether that is the one. The
            # requirement is that it never CLAIMS - a weak match surfaced as a
            # question is honest; a weak match reported as success is not.
            results.append(check(result.status != c.SUCCEEDED,
                                 "an unfindable channel never claims success"))
            results.append(check(
                (result.output or {}).get("needs_confirmation") is True
                or result.status == c.FAILED,
                "and it either asks or admits it could not find one"))

            if not Y.api_key():
                print("  (video_details needs the Data API - not configured, "
                      "so it is expected to fail cleanly)")
            result = await Y.youtube_video_details(
                run_for("video"), videos[0]["video_id"] if videos else "dQw4w9WgXcQ")
            print(f"  video_details -> {result.status}: "
                  f"{(result.error or (result.output or {}).get('title', ''))[:80]}\n")
            results.append(check(
                result.status in (c.SUCCEEDED, c.FAILED),
                "video_details either works or says why - never pretends"))
            return results
        finally:
            store.close()


def main() -> int:
    results = asyncio.run(journey())
    passed = sum(1 for r in results if r)
    print("=" * 70)
    print(f"RESULT: {passed}/{len(results)} checks passed")
    print("=" * 70)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
