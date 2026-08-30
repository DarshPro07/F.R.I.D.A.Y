#!/usr/bin/env python3
"""
The automation engine against the real Windows scheduler.

Unit tests can prove the graph, the retries and the boundary. They cannot
prove the one thing that separates this from the donor design: that the
trigger actually fires. Only `schtasks` can settle that, so this registers a
real task, waits for the operating system to run it, and then reads the run
out of the database from the *other* side - written by a different process
than the one that created the automation.

    python scripts/golden_automations.py

It cleans up after itself, including on failure.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from friday import contracts as c  # noqa: E402
from friday.toolsets import automations as A  # noqa: E402

NAME = "golden-gate-probe"
#: Long enough that the task is registered before its first tick, short enough
#: that nobody watches a progress bar for it.
WAIT_SECONDS = 210


def run_for(label: str) -> c.Run:
    return c.Run.create(label, capability="automation")


def check(passed: bool, message: str, detail: str = "") -> bool:
    print(f"  [{'PASS' if passed else 'FAIL'}] {message}")
    if detail:
        print(f"         {detail}")
    return passed


def cleanup() -> None:
    try:
        A.automations_delete(run_for("cleanup"), NAME)
    except Exception:
        pass
    subprocess.run(["schtasks", "/Delete", "/TN", A.task_name_for(NAME), "/F"],
                   capture_output=True, text=True)


def main() -> int:
    if sys.platform != "win32":
        print("This gate needs the Windows scheduler.")
        return 1

    results: list[bool] = []
    cleanup()
    try:
        print("=" * 70)
        print("REGISTER - a real task, confirmed by the OS and not by us")
        print("=" * 70)
        steps = [
            {"id": "info", "tool": "system.get_info", "args": {}},
            {"id": "note", "tool": "memory.remember", "needs": ["info"],
             "args": {"subject": "automation gate",
                      "value": "the scheduled task fired and ran its graph",
                      "source": "golden_automations.py"}},
        ]
        created = A.automations_create(
            run_for("create"), NAME, json.dumps({"kind": "interval", "minutes": 5}),
            json.dumps(steps), description="ADA automation gate probe")
        print(f"  status={created.status}")
        print(f"  evidence: {created.verification.evidence if created.verification else created.error}")
        results.append(check(created.status == c.SUCCEEDED, "automation created"))
        if created.status != c.SUCCEEDED:
            return 1

        task = created.output["task_name"]
        query = subprocess.run(["schtasks", "/Query", "/TN", task, "/FO", "LIST"],
                               capture_output=True, text=True)
        results.append(check(query.returncode == 0,
                             "the OS agrees the task exists",
                             query.stdout.strip().splitlines()[0] if query.stdout else ""))

        listed = A.automations_list(run_for("list"))
        row = next(r for r in listed.output["automations"] if r["name"] == NAME)
        results.append(check(row["armed"] and not row["orphaned"],
                             "reported as armed, checked against schtasks"))

        print("\n" + "=" * 70)
        print("FIRE BY HAND - the graph runs and every step is recorded")
        print("=" * 70)
        by_hand = asyncio.run(A.automations_run(run_for("run"), NAME))
        for step in by_hand.output["steps"]:
            print(f"    {step['id']:<6} {step['status']:<10} "
                  f"{step['attempts']} attempt(s)  {step['took_ms']:>5}ms")
        results.append(check(by_hand.status == c.SUCCEEDED, "ran to completion"))
        results.append(check(
            all(s["status"] == c.SUCCEEDED for s in by_hand.output["steps"]),
            "every step verified"))

        print("\n" + "=" * 70)
        print(f"FIRE BY SCHEDULE - waiting up to {WAIT_SECONDS}s for the OS")
        print("=" * 70)
        print("  (nothing below is written by this process)")
        # Only a run that did not exist before this gate started counts.
        #
        # This previously accepted any row with fired_by='schedule', and a run
        # left behind by the *previous* gate satisfied it instantly - so the
        # check passed without the scheduler doing anything at all. A harness
        # that reports a pass from evidence produced by an earlier run is
        # worse than no harness, because it looks like proof.
        already = {h["run_id"] for h in A.store().automation_history(NAME)}
        print(f"  ignoring {len(already)} run(s) that already existed")
        deadline = time.monotonic() + WAIT_SECONDS
        fired = None
        while time.monotonic() < deadline:
            time.sleep(5)
            history = A.store().automation_history(NAME)
            fresh = [h for h in history
                     if h["fired_by"] == "schedule" and h["run_id"] not in already]
            if fresh:
                fired = fresh[0]
                break
            print(f"    ... {int(deadline - time.monotonic())}s left, "
                  f"{len(history) - len(already)} new run(s)", end="\r")

        print()
        if fired is None:
            results.append(check(False, "the scheduled task fired",
                                 "no run with fired_by='schedule' appeared"))
        else:
            print(f"    run_id   : {fired['run_id']}")
            print(f"    fired_by : {fired['fired_by']}")
            print(f"    status   : {fired['status']}")
            for step in fired["steps"]:
                print(f"    {step['id']:<6} {step['status']}")
            results.append(check(True, "the scheduled task fired on its own"))
            results.append(check(fired["fired_by"] == "schedule",
                                 "recorded as scheduler-fired, not hand-fired"))
            results.append(check(fired["status"] == c.SUCCEEDED,
                                 "the scheduled run succeeded"))
            results.append(check(bool(fired["finished_at"]),
                                 "it finished rather than being abandoned"))
            results.append(check(len(fired["steps"]) == len(steps),
                                 "every step of the graph is recorded"))

            # The invariant, checked where it actually broke. A scheduled
            # process is the one that starts somewhere else, so this is the
            # only place the claim can be settled rather than asserted.
            runtime = fired.get("runtime") or {}
            print(f"    cwd      : {runtime.get('cwd')}")
            print(f"    root     : {runtime.get('project_root')}")
            print(f"    database : {runtime.get('database')}")
            results.append(check(bool(runtime),
                                 "the scheduled run recorded where it resolved"))
            results.append(check(
                runtime.get("project_root") == str(ROOT),
                "the scheduled process used the same project root"))
            results.append(check(
                Path(runtime.get("database", "")).parent == ROOT / "data",
                "and wrote to the database under it, not beside its cwd"))

        print("\n" + "=" * 70)
        print("DISARM - and prove the task is gone from the OS")
        print("=" * 70)
        deleted = A.automations_delete(run_for("delete"), NAME)
        results.append(check(deleted.status == c.SUCCEEDED, "deleted"))
        results.append(check(not A.task_exists(task),
                             "schtasks no longer knows the task"))
    finally:
        cleanup()

    print("\n" + "=" * 70)
    passed = sum(1 for r in results if r)
    print(f"RESULT: {passed}/{len(results)} checks behaved correctly")
    print("=" * 70)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
