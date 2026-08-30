#!/usr/bin/env python3
"""
The Browser Companion against real Chrome. Twenty tests.

Everything up to now proves the implementation and the protocol. It cannot
prove that Chrome suspended the worker, revived it on an alarm, or refused an
origin after it was revoked - Chrome is the runtime under test, and only
Chrome can answer for it.

    python -m friday.companion.provision      # once
    # load the unpacked extension, paste the token
    python scripts/live_companion.py

Groups, and what each has to make real:

    TRANSPORT     real Chrome <-> extension <-> loopback Friday
    PERMISSIONS   attended, granted, revoked, read-only
    LIFECYCLE     sleep, alarm wake, heartbeat, lease expiry
    RECOVERY      Friday crash, Chrome restart, stale run, Friday away

Some steps need a human - clicking the toolbar icon, revoking a permission,
restarting Chrome. Those pause and say exactly what to do. Everything else is
automatic.

  --quick   skip the four tests that need minutes of waiting (14, 15, 17, 20)
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from friday.companion import bridge as B  # noqa: E402
from friday.companion import pairing  # noqa: E402

from scripts.live_page import ORIGIN as TEST_ORIGIN  # noqa: E402
from scripts.live_page import URL as TEST_URL  # noqa: E402
from scripts.live_page import serve as serve_test_page  # noqa: E402

results: list[tuple[int, str, bool, str]] = []


def record(number: int, name: str, passed: bool, detail: str = "") -> bool:
    results.append((number, name, passed, detail))
    mark = "PASS" if passed else "FAIL"
    print(f"  [{mark}] {number:>2}. {name}")
    if detail:
        print(f"         {detail}")
    return passed


def ask(prompt: str) -> str:
    print(f"\n  >>> {prompt}")
    try:
        return input("      press Enter when done (or 's' to skip): ").strip()
    except EOFError:
        return "s"


async def attached(companion: B.Companion, *, timeout: float = 60) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if companion.attached:
            return True
        await asyncio.sleep(0.5)
    return False


async def dump(companion: B.Companion) -> dict:
    return await companion.call("trace.dump")


def show_trace(entries: list[dict], last: int = 12) -> None:
    print("\n      --- trace ---")
    for entry in entries[-last:]:
        detail = {k: v for k, v in entry.items()
                  if k not in ("at", "worker", "event")}
        print(f"      {entry.get('at','')[11:19]} [{entry.get('worker','?')}] "
              f"{entry.get('event','?'):<20} {detail if detail else ''}")


# ---------------------------------------------------------------------------
# TRANSPORT
# ---------------------------------------------------------------------------


async def transport(companion: B.Companion) -> None:
    print("\n" + "=" * 70)
    print("TRANSPORT")
    print("=" * 70)

    hello = await companion.call("hello")
    expected = pairing.allowed_origin().split("//")[-1]
    actual = hello.get("extension_id", "")
    record(13, "Chrome's own id matches the one pinned in the manifest",
           actual == expected,
           f"chrome says {actual!r}, manifest pins {expected!r}")

    tabs = await companion.call("tabs.list")
    record(1, "list_tabs returns the tabs of the browser actually in use",
           tabs.get("ok") and len(tabs.get("tabs", [])) > 0,
           f"{len(tabs.get('tabs', []))} tab(s)")

    current = await companion.call("tabs.current")
    tab = current.get("tab") or {}
    record(2, "current_tab returns the real focused tab",
           bool(tab.get("url")), f"{tab.get('url','')[:70]}")

    opened = await companion.call("nav.open", url=TEST_URL)
    record(5, "open_url opens in the real browser",
           opened.get("ok") is True, str(opened.get("tab", ""))[:70])
    await asyncio.sleep(2)


# ---------------------------------------------------------------------------
# PERMISSIONS
# ---------------------------------------------------------------------------


async def permissions(companion: B.Companion) -> None:
    print("\n" + "=" * 70)
    print("PERMISSIONS")
    print("=" * 70)

    # 8: nothing granted, no gesture -> refused.
    state = await dump(companion)
    granted = state.get("granted_origins", [])
    if any(TEST_ORIGIN in origin for origin in granted):
        ask(f"{TEST_ORIGIN} is already granted. Remove it in the extension "
            f"options so the refusal can be tested, then come back")
        state = await dump(companion)

    read = await companion.call("page.read")
    record(8, "no permission and no gesture -> refused, with a reason",
           read.get("ok") is False and "permission" in str(read.get("error", "")),
           str(read.get("error", ""))[:90])

    ask(f"open {TEST_URL} and CLICK the Friday toolbar icon on that tab")
    read = await companion.call("page.read")
    record(3, "attended mode reads the real DOM after one gesture",
           read.get("ok") is True and len(read.get("text", "")) > 20,
           f"mode={read.get('mode')} title={read.get('title','')[:40]!r}")

    # The chain that actually matters. Without this the gate could pass while
    # proving only Friday <-> socket <-> service worker, and never that a
    # content script ran in a real tab.
    before = await companion.call("page.read")
    record(6, "the page reads back its untouched state",
           "untouched" in before.get("text", ""),
           "status=untouched")

    found = await companion.call("page.find", name="press me")
    record(6.1, "semantic find locates a real element by its accessible name",
           found.get("ok") and len(found.get("elements", [])) > 0,
           f"{len(found.get('elements', []))} element(s)")

    clicked = await companion.call("page.click", name="press me")
    await asyncio.sleep(1)
    after = await companion.call("page.read")
    record(6.2, "CLICK CHANGED THE REAL DOM, not just returned ok",
           clicked.get("ok") is True and "clicked-1" in after.get("text", ""),
           "status: untouched -> clicked-1")

    secret = f"friday-{int(time.time())}"
    typed = await companion.call("page.type", name="type here", text=secret)
    await asyncio.sleep(1)
    echoed = await companion.call("page.read")
    record(7, "TYPE reached the field and the page's own handler saw it",
           typed.get("ok") is True and secret in echoed.get("text", ""),
           f"echo contains {secret!r}")

    ask(f"in the extension options, ALLOW {TEST_ORIGIN} , then reopen {TEST_URL} "
        f"in a NEW tab and do NOT click the Friday icon")
    read = await companion.call("page.read")
    record(9, "granted mode works with no toolbar click",
           read.get("ok") is True and read.get("mode") == "granted",
           f"mode={read.get('mode')}")

    ask(f"now REVOKE {TEST_ORIGIN} in the extension options. Leave everything "
        f"else running")
    read = await companion.call("page.read")
    record(16, "a revoked origin is refused immediately, with no restart",
           read.get("ok") is False,
           "Chrome is asked at execution time, not a cached answer")

    quiet = B.Companion(port=companion.port, token=companion.token,
                        read_only=True)
    quiet._socket = companion._socket
    quiet._pending = companion._pending
    blocked = await quiet.call("page.click", name="anything")
    record(12, "read-only refuses to change anything",
           blocked.get("ok") is False and "read-only" in blocked.get("error", ""),
           blocked.get("error", "")[:70])


# ---------------------------------------------------------------------------
# LIFECYCLE
# ---------------------------------------------------------------------------


async def lifecycle(companion: B.Companion, *, quick: bool) -> None:
    print("\n" + "=" * 70)
    print("LIFECYCLE")
    print("=" * 70)

    before = await dump(companion)
    print(f"      worker now: {before.get('worker')}  "
          f"alarm_exists={before.get('alarm_exists')}")
    record(14.1, "the wake alarm exists", before.get("alarm_exists") is True)

    if quick:
        print("      (14, 15 skipped - they need minutes of real waiting)")
        return

    print("\n      Test 14 needs the service worker to genuinely die.")
    ask("go to chrome://extensions, open the Friday card, and click "
        "'service worker' -> in the devtools that open, use the Application "
        "tab to Stop the worker. Or just leave Chrome untouched for ~5 minutes")

    # Generously bounded on purpose: Chrome may fire an alarm late, and does
    # not wake a sleeping machine at all. "Eventually" is the contract.
    woke = await attached(companion, timeout=240)
    after = await dump(companion) if woke else {}
    new_worker = after.get("worker") != before.get("worker")
    record(14, "the worker died and an alarm brought it back, no reload",
           woke and new_worker,
           f"worker {before.get('worker')} -> {after.get('worker')}")
    if after.get("entries"):
        show_trace(after["entries"])

    print("\n      Test 15: an unattended run across several idle windows.")
    run_id = "LIVE-15"
    keep = {"running": True}
    holder = asyncio.create_task(
        companion.hold(run_id, still_running=lambda: keep["running"]))
    print("      holding a lease for 90s - do not touch Chrome")
    await asyncio.sleep(45)

    # Not "the worker id stayed the same" - that is consistent with a worker
    # that is alive but useless. A real command has to still execute.
    mid_command = await companion.call("tabs.current")
    record(15.2, "a real command still executes mid-run, unattended",
           mid_command.get("ok") is True,
           str((mid_command.get("tab") or {}).get("url", ""))[:60])

    await asyncio.sleep(45)
    mid = await dump(companion)
    keep["running"] = False
    await holder

    lease = mid.get("lease") or {}
    beats = [e for e in mid.get("entries", []) if e.get("event") == "heartbeat"]
    record(15, "an unattended run stayed alive across 3+ idle windows",
           mid.get("heartbeat_running") is True and lease.get("run_id") == run_id,
           f"{len(beats)} heartbeat(s), lease held by {lease.get('run_id')}")

    ended = await dump(companion)
    record(15.1, "and the lease was released when the run finished",
           (ended.get("lease") or {}).get("run_id") != run_id)


# ---------------------------------------------------------------------------
# RECOVERY
# ---------------------------------------------------------------------------


async def recovery(companion: B.Companion, *, quick: bool) -> None:
    print("\n" + "=" * 70)
    print("RECOVERY")
    print("=" * 70)

    # 18: a late renew from a finished run must not resurrect itself.
    await companion.begin_session("RUN-A", lease_ms=60000)
    await companion.end_session("RUN-A")
    await companion.begin_session("RUN-B", lease_ms=60000)
    stale = await companion.renew_session("RUN-A")
    active = await companion.call("session.status")
    record(18, "a stale run cannot renew over the one that replaced it",
           stale.get("ok") is False and active.get("active_run") == "RUN-B",
           f"{stale.get('error','')} | active={active.get('active_run')}")
    await companion.end_session("RUN-B")

    if quick:
        print("      (17, 19, 20 skipped - they need waiting or a restart)")
        return

    # 17: Friday dies without session.end.
    print("\n      Test 17: a lease taken and then abandoned.")
    await companion.begin_session("RUN-ORPHAN", lease_ms=15000)
    print("      lease taken for 15s and deliberately never ended - waiting 25s")
    await asyncio.sleep(25)
    state = await companion.call("session.status")
    after = await dump(companion)
    record(17, "an abandoned lease expires and the keepalive stops",
           state.get("active_run") is None and
           after.get("heartbeat_running") is not True,
           f"active={state.get('active_run')} "
           f"heartbeat={after.get('heartbeat_running')}")

    # 20: Friday unavailable.
    print("\n      Test 20: Friday goes away for a while.")
    entries_before = len((await dump(companion)).get("entries", []))
    await companion.stop()
    print("      Friday stopped. Waiting 90s - Chrome should retry on alarms,")
    print("      not spin, and the worker should be allowed to sleep between.")
    await asyncio.sleep(90)
    await companion.start()
    back = await attached(companion, timeout=180)
    record(20, "Friday returns and the extension reconnects on its own",
           back, "no reinstall, no reload")
    if back:
        state = await dump(companion)
        attempts = [e for e in state.get("entries", [])[entries_before:]
                    if e.get("event") in ("ws.unreachable", "alarm.fired")]
        record(20.1, "and it retried on alarms rather than spinning",
               len(attempts) < 40, f"{len(attempts)} attempt(s) in 90s")
        show_trace(state.get("entries", []))

    # 19: Chrome restart.
    answer = ask("Test 19: fully QUIT Chrome and start it again, then wait for "
                 "the Friday badge. Type 's' to skip")
    if answer.lower() == "s":
        print("      skipped")
        return
    back = await attached(companion, timeout=240)
    state = await dump(companion) if back else {}
    record(19, "after a Chrome restart the extension assumes no running work",
           back and not (state.get("lease") or {}),
           f"lease after restart: {state.get('lease')}")


# ---------------------------------------------------------------------------


async def main_async(quick: bool) -> int:
    origin = pairing.allowed_origin()
    if not origin:
        print("Not provisioned. Run: python -m friday.companion.provision")
        return 1

    page = serve_test_page()
    print(f"Test page served at {TEST_URL}")

    companion = B.Companion()
    await companion.start()
    print(f"Friday is listening on ws://{B.HOST}:{companion.port}")
    print(f"Expecting only: {origin}")
    print("\nWaiting for the extension to attach...")

    if not await attached(companion, timeout=90):
        print("\nNo extension attached. Check that it is loaded, that the id "
              "matches, and that the token is pasted in its options.")
        await companion.stop()
        return 1
    print("Attached.\n")

    try:
        await transport(companion)
        await permissions(companion)
        await lifecycle(companion, quick=quick)
        await recovery(companion, quick=quick)
    finally:
        try:
            await companion.end_session()
        except Exception:
            pass
        await companion.stop()
        page.shutdown()
        page.server_close()

    print("\n" + "=" * 70)
    passed = sum(1 for _, _, ok, _ in results if ok)
    for number, name, ok, _ in sorted(results):
        if not ok:
            print(f"  FAILED {number}: {name}")
    print(f"RESULT: {passed}/{len(results)} checks passed")
    print("=" * 70)
    return 0 if passed == len(results) else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true",
                        help="skip the tests that need minutes of waiting")
    args = parser.parse_args()
    return asyncio.run(main_async(args.quick))


if __name__ == "__main__":
    sys.exit(main())
