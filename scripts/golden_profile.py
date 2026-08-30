#!/usr/bin/env python3
"""
User-model golden journey: learn from how someone talks, then be corrected.

Feeds real turns through the pipeline and shows what Friday concludes, what it
refuses to conclude, and what happens when a later statement contradicts an
earlier one.

    python scripts/golden_profile.py
    python scripts/golden_profile.py --offline   # reconciliation only, no model
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from friday import profile as P  # noqa: E402
from friday.store import FACT, INFERENCE, PREFERENCE, Store  # noqa: E402

# Real turns, in the user's own voice.
TURNS = [
    "See I want a system where my assistant Friday knows everything about my "
    "core base: how I think, what things I have with myself, what things I want, "
    "what my goal is, what my preference is.",

    "I'm building ADA on a Windows laptop with 16 cores. I care a lot about "
    "evidence — I don't want it claiming things it didn't do.",

    "Also I want Spotify connected so that it knows all my playlists. If I say "
    "play this song, it should play. I'd prefer local-first tools over cloud "
    "where I can.",

    "ok cool thanks",
]

# Two corrections, on purpose. The first is NOT a contradiction even though it
# sounds like one - "the build machine is Windows but the target is Linux" is
# two different subjects, and treating it as a conflict would be wrong. The
# second contradicts a stored fact outright, and must be caught.
CORRECTIONS = [
    ("Actually I'm not on a Windows laptop for this — the build machine is "
     "Windows but I want ADA to end up running on Linux.",
     "sounds like a correction, but is really about two different things"),
    ("Wait, I got that wrong earlier — my laptop has 8 cores, not 16.",
     "contradicts a stored fact outright"),
]


def show_outcomes(outcomes) -> None:
    for outcome in outcomes:
        symbol = {"stored": "+", "reinforced": "^", "rejected": "-",
                  "conflict": "!"}.get(outcome.action, "?")
        print(f"     {symbol} {outcome.action:<11} {outcome.subject}")
        print(f"       value : {outcome.value}")
        print(f"       why   : {outcome.reason}")


def show_profile(store) -> None:
    data = P.profile(store)
    for dimension in P.DIMENSIONS:
        rows = data.get(dimension) or []
        if not rows:
            continue
        print(f"  {dimension.upper()}")
        for row in rows:
            mark = "" if row["kind"] == FACT else f"  [{row['kind'].lower()} {row['confidence']:.0%}]"
            print(f"    - {row['subject']}: {row['value']}{mark}")
    print()


def offline_demo(store) -> int:
    """The reconciliation table, without needing the extractor."""
    print("=" * 72)
    print("OFFLINE: the reconciliation rules")
    print("=" * 72)

    print("\n1. Friday guesses your timezone, then you tell it.")
    store.remember("user.timezone", "PST", kind=INFERENCE,
                   source="guessed from message times", confidence=0.6)
    print(f"   before : {store.recall('user.timezone')[0]['value']} (INFERENCE)")
    show_outcomes(P.learn(store, [P.Candidate(
        dimension=P.IDENTITY, subject="user.timezone", value="IST", kind=FACT,
        confidence=1.0, evidence="I'm in India")]))
    print(f"   after  : {store.recall('user.timezone')[0]['value']} (FACT)")

    print("\n2. Friday then guesses something that contradicts what you said.")
    show_outcomes(P.learn(store, [P.Candidate(
        dimension=P.IDENTITY, subject="user.timezone", value="GMT",
        kind=INFERENCE, confidence=0.9,
        evidence="they mentioned a London meeting")]))
    print(f"   still  : {store.recall('user.timezone')[0]['value']}  <- a guess "
          f"did not overwrite what you said")

    print("\n3. You say something that contradicts an earlier statement.")
    store.remember("project.db", "Postgres", kind=FACT, source="user said so")
    show_outcomes(P.learn(store, [P.Candidate(
        dimension=P.GOALS, subject="project.db", value="SQLite", kind=FACT,
        confidence=1.0, evidence="we're using SQLite for now")]))
    conflict = store.contradictions(resolution="pending")[0]
    print(f"   held   : {store.recall('project.db')[0]['value']} "
          f"(unchanged until you settle it)")
    print(f"   asks   : you previously said {conflict['existing_value']!r}, "
          f"now {conflict['new_value']!r} — which is right?")
    P.resolve(store, conflict["id"], keep="new", rationale="user confirmed the switch")
    print(f"   settled: {store.recall('project.db')[0]['value']}")

    print("\n4. Repetition builds confidence.")
    for day in range(1, 5):
        P.learn(store, [P.Candidate(
            dimension=P.PREFERENCES, subject="user.tooling",
            value="local-first tools", kind=PREFERENCE, confidence=0.5,
            evidence=f"day {day}: prefers running things locally")])
        row = store.recall("user.tooling")[0]
        print(f"   day {day}: {row['confidence']:.0%}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        store = Store(Path(tmp) / "profile.sqlite3")
        try:
            if args.offline:
                return offline_demo(store)

            print("=" * 72)
            print("LEARNING FROM HOW YOU TALK")
            print("=" * 72)
            for i, turn in enumerate(TURNS, 1):
                print(f"\n[turn {i}] {turn[:96]}{'...' if len(turn) > 96 else ''}")
                try:
                    candidates = P.extract_candidates(
                        turn, existing_subjects=P.known_subjects(store))
                except P.ExtractionError as exc:
                    print(f"   extraction unavailable: {exc}")
                    return 1
                if not candidates:
                    print("     (nothing worth remembering — correct for small talk)")
                    continue
                for item in candidates:
                    print(f"     ~ {item.dimension}/{item.subject} = {item.value!r} "
                          f"[{item.kind} {item.confidence:.0%}]")
                    print(f"       quoted: {item.evidence[:88]!r}")
                show_outcomes(P.learn(store, candidates))

            print("\n" + "=" * 72)
            print("WHAT FRIDAY NOW KNOWS")
            print("=" * 72)
            show_profile(store)

            print("=" * 72)
            print("BEING CORRECTED")
            print("=" * 72)
            for text, note in CORRECTIONS:
                print(f"[you] {text}")
                print(f"      ({note})\n")
                candidates = P.extract_candidates(
                    text, existing_subjects=P.known_subjects(store))
                for item in candidates:
                    print(f"     ~ {item.subject} = {item.value!r} [{item.kind}]")
                show_outcomes(P.learn(store, candidates))
                print()

            pending = store.contradictions(resolution="pending")
            if pending:
                print(f"  {len(pending)} conflict(s) raised rather than "
                      f"silently overwritten:")
                for row in pending:
                    print(f"    {row['subject']}: {row['existing_value']!r} "
                          f"vs {row['new_value']!r}")
                    print(f"      held : {store.recall(row['subject'])[0]['value']!r} "
                          f"until you settle it")
                    P.resolve(store, row["id"], keep="new",
                              rationale="user corrected themselves")
                    print(f"      after you confirm: "
                          f"{store.recall(row['subject'])[0]['value']!r}")
            else:
                print("  (no conflict raised)")

            print("\n" + "=" * 72)
            print("THE BRIEFING PUT IN FRONT OF THE NEXT TURN")
            print("=" * 72)
            brief = P.brief(store)
            print(brief if brief else "(nothing learned yet)")

            subjects = [row["subject"] for rows in P.profile(store).values()
                        for row in rows]
            if subjects:
                print("\n" + "=" * 72)
                print(f"WHY DOES FRIDAY THINK THIS?  ({subjects[0]})")
                print("=" * 72)
                explanation = P.explain(store, subjects[0])
                current = explanation["current"]
                print(f"  belief    : {current['value']} [{current['kind']}]")
                print(f"  confidence: {current['confidence']:.0%}")
                for observation in explanation["observations"][:3]:
                    print(f"  because   : {observation['evidence'][:100]!r}")
            return 0
        finally:
            store.close()


if __name__ == "__main__":
    sys.exit(main())
