#!/usr/bin/env python3
"""
Phase 1E golden journeys (§27): vision, on demand, without inventing anything.

    python scripts/golden_1e.py              # screen + camera
    python scripts/golden_1e.py --no-camera  # screen only

Every capture is written to data/vision/ so any claim about what was seen has
a retrievable image behind it. The camera is opened per call and released
immediately.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from friday import contracts as c  # noqa: E402
from friday.toolsets import vision as V  # noqa: E402
from friday.toolsets.system import needs_approval  # noqa: E402


def show(label: str, result) -> bool:
    ok = result.may_claim_completion
    mark = "PASS" if ok else ("ASK " if needs_approval(result) else "HEDGE")
    print(f"[{mark}] {label}")
    print(f"       status={result.status}  may_claim_completion={ok}")
    if result.verification:
        print(f"       verify: {result.verification.evidence}")
    if result.error:
        print(f"       note : {result.error[:160]}")
    for artifact in result.artifacts:
        print(f"       artifact: {artifact.path_or_uri}")
    print()
    return result.status in ("succeeded", "partial")


def report(result) -> None:
    out = result.output or {}
    if "confidence" not in out:
        return
    print(f"       question   : {out['question']}")
    print(f"       observation: {out['observation'][:150]}")
    print(f"       identified : {out['identification'] or '(none offered)'}")
    print(f"       confidence : {out['confidence']:.0%}  [{out['confidence_band']}]")
    if out.get("uncertain_because"):
        print(f"       unsure why : {out['uncertain_because'][:120]}")
    if out.get("suggested_better_view"):
        print(f"       better view: {out['suggested_better_view'][:120]}")
    if out.get("text_found"):
        print(f"       text found : {out['text_found'][:120]}")
    print(f"       ADA SAYS   : {out['spoken_form']}")
    print(f"       (answered about frame sha256:{out['answered_about_frame']}, "
          f"{out['frame_age_seconds']}s old)")
    print()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-camera", action="store_true")
    args = parser.parse_args()
    results: list[bool] = []

    print("=" * 70)
    print("JOURNEY: confidence decides what may be asserted")
    print("=" * 70)
    for confidence, expect_start in (
        (0.92, "I believe that's"),
        (0.60, "It looks like"),
        (0.20, "I'm not confident enough"),
    ):
        said = V.spoken_form(
            {"confidence": confidence, "identification": "a Raspberry Pi 5",
             "observation": "a small green circuit board",
             "suggested_better_view": "Turn it so the label faces me."},
            "camera",
        )
        band = V.confidence_band(confidence)
        good = said.startswith(expect_start)
        print(f"  {'ok ' if good else 'BAD'} {confidence:.0%} [{band:6}] {said}")
        results.append(good)
    print()

    print("=" * 70)
    print('JOURNEY: "Look at my screen."')
    print("=" * 70)
    run = c.Run.create("Look at my screen.", capability="vision")
    captured = V.screen_capture(run)
    results.append(show("vision.screen_capture", captured))

    inspected = V.inspect_screen(
        run, "What application is in the foreground, and what is it showing?"
    )
    results.append(show("vision.inspect_screen", inspected))
    report(inspected)

    print("=" * 70)
    print('JOURNEY: "Read this screen." (OCR-style)')
    print("=" * 70)
    read = V.inspect_screen(run, "Read any text you can see, verbatim.")
    results.append(show("vision.inspect_screen (read text)", read))
    report(read)

    if not args.no_camera:
        print("=" * 70)
        print('JOURNEY: "What am I holding?"  (§27 — real camera frame)')
        print("=" * 70)
        run = c.Run.create("What am I holding?", capability="vision")

        t0 = time.monotonic()
        frame = V.camera_frame(run)
        elapsed = time.monotonic() - t0
        results.append(show(f"vision.camera_frame ({elapsed:.1f}s)", frame))

        held = V.inspect_camera(run, "What am I holding? Identify the object.")
        results.append(show("vision.inspect_camera", held))
        report(held)

        print("=" * 70)
        print("JOURNEY: the camera must not be left open (§26)")
        print("=" * 70)
        second = V.camera_frame(run)
        reopened = second.may_claim_completion
        print(f"[{'PASS' if reopened else 'FAIL'}] the camera reopened cleanly, "
              f"so it was released after the first grab")
        if second.output:
            print(f"       second frame sha256:{second.output['sha256']} "
                  f"(different from first: "
                  f"{second.output['sha256'] != frame.output['sha256']})")
        print()
        results.append(reopened)

    print("=" * 70)
    print("JOURNEY: a frame that cannot be captured must FAIL, not be guessed")
    print("=" * 70)
    run = c.Run.create("Look at monitor 9.", capability="vision")
    missing = V.screen_capture(run, monitor=99)
    ok = missing.status == "failed" and not missing.may_claim_completion
    print(f"[{'PASS' if ok else 'FAIL'}] nonexistent monitor -> {missing.status}")
    print(f"       error: {missing.error}\n")
    results.append(ok)

    captures = sorted(V.captures_dir().glob("*.png"))
    print("=" * 70)
    print(f"ARTIFACTS: {len(captures)} image(s) in {V.captures_dir()}")
    for path in captures[-6:]:
        print(f"  {path.stat().st_size:>9,} bytes  {path.name}")
    print("=" * 70)

    passed = sum(1 for r in results if r)
    print(f"RESULT: {passed}/{len(results)} checks behaved correctly")
    print("=" * 70)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
