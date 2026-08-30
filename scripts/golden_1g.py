#!/usr/bin/env python3
"""
Phase 1G golden journey (§27): "Remind me in 2 minutes" — and verify it fires.

Scheduling a reminder is easy to fake; the requirement is that it actually
goes off. So this schedules one a short way out and then waits, polling the
database until the fired flag flips. The flag is set by the script the OS
scheduler runs, so seeing it flip means the operating system genuinely
executed our task.

    python scripts/golden_1g.py                # ~90s, waits for a real fire
    python scripts/golden_1g.py --no-wait      # skip the wait
    python scripts/golden_1g.py --delay 120    # further out

A desktop notification will appear when it fires. That is the point.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from friday import contracts as c  # noqa: E402
from friday.store import Store  # noqa: E402
from friday.toolsets import reminders as R  # noqa: E402
from friday.toolsets.reminders import WhenError, parse_when  # noqa: E402


def show(label: str, result) -> bool:
    ok = result.may_claim_completion
    print(f"[{'PASS' if ok else 'FAIL'}] {label}")
    print(f"       status={result.status}")
    if result.verification:
        print(f"       verify: {result.verification.evidence}")
    if result.error:
        print(f"       error: {result.error[:180]}")
    if isinstance(result.output, dict):
        trimmed = {k: v for k, v in result.output.items() if k != "reminders"}
        print(f"       output: {json.dumps(trimmed, default=str)[:200]}")
    print()
    return ok


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-wait", action="store_true")
    parser.add_argument("--delay", type=int, default=75,
                        help="seconds until the reminder fires")
    args = parser.parse_args()
    results: list[bool] = []

    print("=" * 66)
    print("JOURNEY: understanding when")
    print("=" * 66)
    now = datetime(2026, 8, 16, 14, 30, 0)
    cases = [
        ("in 20 minutes", "2026-08-16T14:50:00"),
        ("in 2 hours", "2026-08-16T16:30:00"),
        ("tomorrow morning", "2026-08-17T09:00:00"),
        ("tomorrow", "2026-08-17T09:00:00"),
        ("at 15:45", "2026-08-16T15:45:00"),
        ("at 9am", "2026-08-17T09:00:00"),   # 9am today already passed
        ("tonight", "2026-08-16T21:00:00"),
    ]
    parsed_ok = True
    for text, expected in cases:
        got = parse_when(text, now=now).isoformat(timespec="seconds")
        good = got == expected
        parsed_ok &= good
        print(f"       {'ok ' if good else 'BAD'} {text!r:<20} -> {got}")
    print()
    results.append(parsed_ok)

    for nonsense in ("next fortnight-ish", "", "at 99:99"):
        try:
            parse_when(nonsense, now=now)
            print(f"[FAIL] {nonsense!r} should not have parsed\n")
            results.append(False)
        except WhenError:
            results.append(True)
    print("[PASS] unparseable times are refused rather than guessed\n")

    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "reminders.sqlite3"
        R.reset_store(Store(db))
        run = c.Run.create("Remind me in two minutes.", capability="reminders")

        print("=" * 66)
        print("JOURNEY: a reminder in the past must be refused")
        print("=" * 66)
        past = R.reminders_create(run, "too late", "2020-01-01T00:00:00")
        ok = past.status == "failed" and not past.may_claim_completion
        print(f"[{'PASS' if ok else 'FAIL'}] past time -> {past.status}: "
              f"{(past.error or '')[:80]}\n")
        results.append(ok)

        print("=" * 66)
        print("JOURNEY: cancel removes the OS task")
        print("=" * 66)
        doomed = R.reminders_create(run, "this one gets cancelled", "in 30 minutes")
        results.append(show("create (to be cancelled)", doomed))
        if doomed.may_claim_completion:
            task = doomed.output["task_name"]
            print(f"       scheduler holds it: {R.task_exists(task)}")
            cancelled = R.reminders_cancel(run, doomed.output["id"])
            results.append(show("cancel", cancelled))
            gone = not R.task_exists(task)
            print(f"[{'PASS' if gone else 'FAIL'}] task really gone from the "
                  f"scheduler: {gone}\n")
            results.append(gone)

        print("=" * 66)
        print(f'JOURNEY: "Remind me in {args.delay} seconds" — and verify it FIRES')
        print("=" * 66)
        created = R.reminders_create(run, "ADA Phase 1G golden test reminder",
                                     f"in {args.delay} seconds")
        results.append(show("reminders.create", created))
        if not created.may_claim_completion:
            R.reset_store(None)
            return 1

        reminder_id = created.output["id"]
        listed = R.reminders_list(run)
        results.append(show("reminders.list", listed))
        for row in listed.output["reminders"]:
            print(f"         #{row['id']} due {row['due_at']} "
                  f"still_scheduled={row['still_scheduled']} fired={row['fired']}")
        print()

        if args.no_wait:
            print("(--no-wait: not waiting for the fire; cancelling instead)\n")
            R.reminders_cancel(run, reminder_id)
            R.reset_store(None)
        else:
            deadline = time.monotonic() + args.delay + 90
            print(f"       waiting up to {int(args.delay + 90)}s for the OS to "
                  f"run the task ...")
            fired = False
            while time.monotonic() < deadline:
                row = R.store().get_reminder(reminder_id)
                if row and row["fired"]:
                    fired = True
                    break
                time.sleep(2)
            print()
            print(f"[{'PASS' if fired else 'FAIL'}] the reminder actually fired")
            if fired:
                print("       evidence: the fired flag was set by the script the "
                      "Windows scheduler executed,")
                print("                 not by this process")
            else:
                print("       the fired flag never flipped within the window")
            print()
            results.append(fired)
            if not fired:
                R.reminders_cancel(run, reminder_id)
            R.reset_store(None)

    passed = sum(1 for r in results if r)
    print("=" * 66)
    print(f"RESULT: {passed}/{len(results)} checks passed")
    print("=" * 66)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
