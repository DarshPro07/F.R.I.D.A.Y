#!/usr/bin/env python3
"""
Phase 1A golden journeys (§27).

Exercises the system toolset against the real machine and prints the
ActionResult for each step, so success is judged from verification evidence
rather than from the absence of an exception.

    python scripts/golden_1a.py            # read-only checks
    python scripts/golden_1a.py --apps     # also opens and closes Calculator

Exit 0 only if every journey produced a verified result.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from friday import contracts as c  # noqa: E402
from friday.policy import PolicyEngine  # noqa: E402
from friday.toolsets import system as S  # noqa: E402

NOISY_KEYS = {"apps", "processes", "text"}


def show(label: str, result: c.ActionResult) -> bool:
    ok = result.may_claim_completion
    mark = "PASS" if ok else ("ASK " if S.needs_approval(result) else "FAIL")
    print(f"[{mark}] {label}")
    print(f"       status={result.status}  may_claim_completion={ok}")
    if result.verification:
        print(f"       verify: {result.verification.method}")
        print(f"               {result.verification.evidence}")
    if result.error:
        print(f"       error: {result.error}")
    if isinstance(result.output, dict):
        trimmed = {k: v for k, v in result.output.items() if k not in NOISY_KEYS}
        print(f"       output: {json.dumps(trimmed, default=str)[:240]}")
    if result.side_effects:
        print(f"       side effects: {list(result.side_effects)}")
    print()
    return ok


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apps", action="store_true",
                        help="also open and close Calculator (visible side effect)")
    args = parser.parse_args()

    results: list[bool] = []

    print("=" * 66)
    print('JOURNEY: "What is this computer?"')
    print("=" * 66)
    run = c.Run.create("What is this computer?", capability="system")
    results.append(show("system.get_info", S.system_get_info(run)))

    print("=" * 66)
    print('JOURNEY: "What is using the most RAM?"')
    print("=" * 66)
    run = c.Run.create("What is using the most RAM?", capability="system")
    result = S.system_list_processes(run, top=5, sort_by="memory")
    results.append(show("system.list_processes", result))
    if isinstance(result.output, dict):
        for row in result.output.get("processes", [])[:5]:
            print(f"         {row['name']:<28} {row['memory_mb']:>8} MB  pid={row['pid']}")
        print()

    print("=" * 66)
    print('JOURNEY: "How is the machine doing?"')
    print("=" * 66)
    run = c.Run.create("How is the machine doing?", capability="system")
    results.append(show("system.resource_usage", S.system_resource_usage(run)))
    results.append(show("system.wifi_status", S.system_wifi_status(run)))

    print("=" * 66)
    print("JOURNEY: volume + clipboard round-trip")
    print("=" * 66)
    run = c.Run.create("What volume am I at?", capability="system")
    results.append(show("volume.get", S.volume_get(run)))

    engine = PolicyEngine()
    blocked = S.clipboard_write(run, "ada-phase1a-probe", engine=engine)
    print(f"[GATE] clipboard.write without approval -> {blocked.status} "
          f"(needs_approval={S.needs_approval(blocked)})\n")
    engine.approve_for_session("clipboard.write")
    results.append(show("clipboard.write (approved)",
                        S.clipboard_write(run, "ada-phase1a-probe", engine=engine)))
    results.append(show("clipboard.read", S.clipboard_read(run)))

    print("=" * 66)
    print("JOURNEY: app discovery")
    print("=" * 66)
    run = c.Run.create("What apps do I have?", capability="system")
    results.append(show("apps.list_known", S.apps_list_known(run)))

    print("=" * 66)
    print('JOURNEY: unknown app must FAIL, never claim success')
    print("=" * 66)
    run = c.Run.create("Open Flurbomatic 9000.", capability="system")
    ghost = S.apps_open(run, "flurbomatic 9000")
    print(f"[{'PASS' if ghost.status == 'failed' else 'FAIL'}] "
          f"nonexistent app -> status={ghost.status}, "
          f"may_claim_completion={ghost.may_claim_completion}")
    print(f"       error: {ghost.error}\n")
    results.append(ghost.status == "failed" and not ghost.may_claim_completion)

    if args.apps:
        print("=" * 66)
        print('JOURNEY: "Open Calculator."  (§27 - real process verification)')
        print("=" * 66)
        run = c.Run.create("Open Calculator.", capability="system")
        results.append(show("apps.open calculator", S.apps_open(run, "calculator")))

        engine = PolicyEngine()
        gated = S.apps_close(run, "calculator", engine=engine)
        print(f"[GATE] apps.close without approval -> {gated.status} "
              f"(needs_approval={S.needs_approval(gated)})\n")
        engine.approve_for_session("apps.close")
        results.append(show("apps.close calculator (approved)",
                            S.apps_close(run, "calculator", engine=engine)))
    else:
        print("(skipping app open/close - pass --apps to run it)\n")

    passed = sum(1 for r in results if r)
    print("=" * 66)
    print(f"RESULT: {passed}/{len(results)} journeys produced a verified result")
    print("=" * 66)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
