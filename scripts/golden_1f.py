#!/usr/bin/env python3
"""
Phase 1F golden journeys (§27, §"FIRST RELEASE ACCEPTANCE"): Spotify.

    python scripts/golden_1f.py              # includes real playback
    python scripts/golden_1f.py --read-only  # no transport commands

Playback state is restored at the end: if Spotify was paused when this
started, it is paused again when it finishes.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from friday import contracts as c  # noqa: E402
from friday.toolsets import media as M  # noqa: E402
from friday.toolsets.system import needs_approval  # noqa: E402


def show(label: str, result) -> bool:
    ok = result.may_claim_completion
    mark = "PASS" if ok else ("ASK " if needs_approval(result) else "HEDGE")
    print(f"[{mark}] {label}")
    print(f"       status={result.status}  may_claim_completion={ok}")
    if result.verification:
        print(f"       verify: {result.verification.evidence}")
    if result.error:
        print(f"       note : {result.error[:200]}")
    print()
    return result.status in ("succeeded", "partial")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--read-only", action="store_true")
    args = parser.parse_args()
    results: list[bool] = []

    print("=" * 70)
    print("JOURNEY: title parsing — the only now-playing source without the API")
    print("=" * 70)
    cases = [
        ("Spotify Free", False, "", ""),
        ("Spotify Premium", False, "", ""),
        ("Spotify", False, "", ""),
        ("Hans Zimmer - Cornfield Chase", True, "Hans Zimmer", "Cornfield Chase"),
        ("Advertisement", True, "", ""),
    ]
    parsed_ok = True
    for title, playing, artist, track in cases:
        got = M.parse_title(title)
        good = (got["playing"] == playing and got["artist"] == artist
                and got["track"] == track)
        parsed_ok &= good
        print(f"  {'ok ' if good else 'BAD'} {title!r:<34} playing={got['playing']} "
              f"artist={got['artist']!r} track={got['track']!r}")
    print()
    results.append(parsed_ok)

    print("=" * 70)
    print("JOURNEY: is Spotify running, and what is it doing?")
    print("=" * 70)
    window = M.spotify_window()
    if window is None:
        print("[FAIL] Spotify is not running — open it and re-run\n")
        return 1
    hwnd, title = window
    print(f"       window hwnd={hwnd} title={title!r}")
    print(f"       spotify pids: {sorted(M.spotify_pids())[:5]}\n")

    run = c.Run.create("What is playing?", capability="media")
    current = M.spotify_current(run)
    results.append(show("spotify.current", current))
    started_playing = current.output["playing"] if current.output else False

    if args.read_only:
        print("(--read-only: skipping transport commands)\n")
    else:
        print("=" * 70)
        print("JOURNEY: play — verified by Spotify's own state changing")
        print("=" * 70)
        resumed = M.spotify_resume(run)
        results.append(show("spotify.resume", resumed))
        time.sleep(1.5)
        results.append(show("spotify.current (after resume)", M.spotify_current(run)))

        print("=" * 70)
        print("JOURNEY: skip — the track must actually become a different one")
        print("=" * 70)
        results.append(show("spotify.next", M.spotify_next(run)))
        time.sleep(1.0)
        results.append(show("spotify.previous", M.spotify_previous(run)))

        print("=" * 70)
        print("JOURNEY: pause — verified by the title going idle")
        print("=" * 70)
        results.append(show("spotify.pause", M.spotify_pause(run)))

    print("=" * 70)
    print('JOURNEY: "Play Interstellar on Spotify" — the honest limit')
    print("=" * 70)
    run = c.Run.create("Play Interstellar on Spotify.", capability="media")
    played = M.spotify_play(run, "Interstellar soundtrack")
    ok = played.status == "partial" and not played.may_claim_completion
    print(f"[{'PASS' if ok else 'FAIL'}] spotify.play with a query is PARTIAL, "
          f"so the agent cannot claim it started that track")
    print(f"       status={played.status}")
    print(f"       says : {played.error[:180]}")
    if played.output:
        print(f"       opened: {played.output.get('uri')}")
        print(f"       playback_started: {played.output.get('playback_started')}")
    print()
    results.append(ok)

    print("=" * 70)
    print("JOURNEY: refusals")
    print("=" * 70)
    empty = M.spotify_search(run, "   ")
    ok = empty.status == "failed"
    print(f"[{'PASS' if ok else 'FAIL'}] empty query -> {empty.status}: {empty.error}\n")
    results.append(ok)

    if not args.read_only:
        print("=" * 70)
        print("RESTORING: leaving playback as it was found")
        print("=" * 70)
        now = M.current_state()
        if now and now["playing"] != started_playing:
            M.spotify_pause(run) if now["playing"] else M.spotify_resume(run)
            time.sleep(1.0)
        final = M.current_state()
        print(f"       started playing={started_playing}, "
              f"now playing={final['playing'] if final else '?'}\n")

    passed = sum(1 for r in results if r)
    print("=" * 70)
    print(f"RESULT: {passed}/{len(results)} checks behaved correctly")
    print("=" * 70)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
