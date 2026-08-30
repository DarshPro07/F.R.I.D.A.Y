#!/usr/bin/env python3
"""
Music golden journey: "play this song" — and prove it actually played.

    python scripts/golden_music.py
    python scripts/golden_music.py --song "Mann Mera Gajendra Verma"

Audio will play out loud. Everything is stopped at the end.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from friday import contracts as c  # noqa: E402
from friday.toolsets import music as M  # noqa: E402
from friday.toolsets.system import needs_approval  # noqa: E402


def show(label: str, result) -> bool:
    ok = result.may_claim_completion
    mark = "PASS" if ok else ("ASK " if needs_approval(result) else "FAIL")
    print(f"[{mark}] {label}")
    print(f"       status={result.status}")
    if result.verification:
        print(f"       verify: {result.verification.evidence}")
    if result.error:
        print(f"       error : {result.error[:180]}")
    print()
    return ok


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--song", default="Interstellar Hans Zimmer Cornfield Chase")
    args = parser.parse_args()
    results: list[bool] = []
    run = c.Run.create("play music", capability="music")

    try:
        print("=" * 70)
        print(f'JOURNEY: search for "{args.song}"')
        print("=" * 70)
        t0 = time.monotonic()
        found = M.music_search(run, args.song, limit=5)
        results.append(show(f"music.search ({time.monotonic() - t0:.1f}s)", found))
        if found.output:
            for hit in found.output["results"]:
                print(f"         {hit['duration']:>6}  {hit['title'][:58]}")
            print()

        print("=" * 70)
        print(f'JOURNEY: "Play {args.song}" — verified by a live player')
        print("=" * 70)
        t0 = time.monotonic()
        played = M.music_play(run, args.song)
        results.append(show(f"music.play ({time.monotonic() - t0:.1f}s to audio)", played))
        if played.output:
            track = played.output["now_playing"]
            print(f"       NOW PLAYING: {track['title']}")
            print(f"       channel    : {track['channel']}")
            print(f"       method     : {track['method']}")
            print(f"       queued next: {played.output['queued']}\n")

        print("       ...letting it play for 6s so you can hear it...\n")
        time.sleep(6)

        print("=" * 70)
        print("JOURNEY: pause, resume, current")
        print("=" * 70)
        results.append(show("music.pause", M.music_pause(run)))
        time.sleep(2.5)
        results.append(show("music.current (while paused)", M.music_current(run)))
        results.append(show("music.resume", M.music_resume(run)))
        time.sleep(3)

        print("=" * 70)
        print("JOURNEY: next — a different track must actually start")
        print("=" * 70)
        before = dict(M.player.track or {})
        skipped = M.music_next(run)
        results.append(show("music.next", skipped))
        if skipped.output:
            print(f"       was: {before.get('title', '?')[:60]}")
            print(f"       now: {skipped.output['now_playing']['title'][:60]}")
            changed = (skipped.output["now_playing"]["video_id"]
                       != before.get("video_id"))
            print(f"       actually a different track: {changed}\n")
            results.append(changed)
        time.sleep(4)

        print("=" * 70)
        print("JOURNEY: mood selection")
        print("=" * 70)
        mood = M.music_play_mood(run, "relaxing")
        results.append(show("music.play_mood('relaxing')", mood))
        if mood.output:
            print(f"       picked : {mood.output['now_playing']['title'][:60]}")
            print(f"       how    : {mood.output.get('selected_by')}\n")
        time.sleep(4)

        print("=" * 70)
        print("JOURNEY: refusals")
        print("=" * 70)
        bad = M.music_play_mood(run, "existential dread")
        ok = bad.status == "failed"
        print(f"[{'PASS' if ok else 'FAIL'}] unknown mood -> {bad.status}: "
              f"{(bad.error or '')[:90]}")
        results.append(ok)
        empty = M.music_play(run, "   ")
        ok = empty.status == "failed"
        print(f"[{'PASS' if ok else 'FAIL'}] empty query -> {empty.status}\n")
        results.append(ok)

        print("=" * 70)
        print("JOURNEY: stop, and prove nothing is left running")
        print("=" * 70)
        results.append(show("music.stop", M.music_stop(run)))
        after = M.music_current(run)
        stopped = after.output["playing"] is False
        print(f"[{'PASS' if stopped else 'FAIL'}] player is gone: "
              f"alive={M.player.alive}\n")
        results.append(stopped)
    finally:
        M.player.stop()

    passed = sum(1 for r in results if r)
    print("=" * 70)
    print(f"RESULT: {passed}/{len(results)} checks behaved correctly")
    print("=" * 70)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
