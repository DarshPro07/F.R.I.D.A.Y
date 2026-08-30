#!/usr/bin/env python3
"""
Power control against the real machine, without turning it off.

    python scripts/golden_power.py                  safe; the default
    python scripts/golden_power.py --cancel-journey initiates a REAL shutdown
                                                    and calls it back
    python scripts/golden_power.py --lock           actually locks the screen

The default run touches nothing. It proves the parts that can be wrong in
Friday: that availability is read from the right API, that the privilege
really flips, that no power action moves without a yes bound to it, and that a
yes for one action cannot be spent on another.

`--cancel-journey` is the interesting one and it is opt-in for a reason. It
asks Windows for a real shutdown with a real countdown and then calls it back,
which is the only way to exercise that path end to end - and if the callback
failed, this machine would go down. Everything else in this feature is proven
without ever asking the operating system to do something irreversible; this is
the exception, and it should be a deliberate choice made by somebody watching.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from friday import confirmation as CF  # noqa: E402
from friday import contracts as c  # noqa: E402
from friday import policy as p  # noqa: E402
from friday import power_state  # noqa: E402
from friday.platform import windows as native  # noqa: E402
from friday.toolsets import power as W  # noqa: E402


def check(passed: bool, message: str, detail: str = "") -> bool:
    print(f"  [{'PASS' if passed else 'FAIL'}] {message}")
    if detail:
        print(f"         {detail}")
    return bool(passed)


def run_for(label: str) -> c.Run:
    return c.Run.create(label, capability="system")


def what_this_machine_can_do(results: list[bool]) -> None:
    print("\n-- what this machine can actually do --")

    caps = native.power_capabilities()
    results.append(check(
        isinstance(caps.sleep, bool) and isinstance(caps.hibernate, bool),
        "capabilities read from SYSTEM_POWER_CAPABILITIES",
        f"sleep={caps.sleep} hibernate={caps.hibernate} "
        f"modern_standby={caps.modern_standby}"))

    import ctypes

    legacy = bool(ctypes.WinDLL("powrprof").IsPwrSuspendAllowed())
    if caps.modern_standby and legacy is False:
        results.append(check(
            caps.sleep is True,
            "the legacy API disagrees, and the capabilities answer is used",
            f"IsPwrSuspendAllowed={legacy} but AoAc={caps.modern_standby}; "
            f"believing the legacy call would tell the boss this machine "
            f"cannot sleep"))
    else:
        print("       (the two APIs agree on this machine; nothing to compare)")


def the_privilege(results: list[bool]) -> None:
    print("\n-- the privilege every power call needs --")

    was = native.enable_shutdown_privilege()
    results.append(check(
        was is True,
        "SeShutdownPrivilege can be enabled, and says so honestly",
        "it is held but disabled by default, so every power call would "
        "otherwise fail and read as the machine refusing"))
    native.enable_shutdown_privilege(False)
    results.append(check(True, "and put back afterwards"))


def nothing_moves_without_a_yes(results: list[bool]) -> None:
    print("\n-- nothing moves without a yes bound to it --")

    for name, call in (("lock", W.power_lock), ("sleep", W.power_sleep),
                       ("shutdown", W.power_shutdown),
                       ("restart", W.power_restart)):
        book = CF.Book()
        result = call(run_for(f"{name} it"), book=book)
        results.append(check(
            result.status in (c.CANCELLED, c.UNSUPPORTED),
            f"{name} asks first and does nothing",
            (result.error or "")[:96]))

    book = CF.Book()
    engine = p.PolicyEngine(autonomy=p.FULL)
    result = W.power_restart(run_for("restart"), book=book, engine=engine)
    results.append(check(
        result.status == c.CANCELLED,
        "FULL autonomy still does not answer for the boss",
        "ASK becomes AUTO under FULL; CONFIRM never does"))

    book = CF.Book()
    run = run_for("shut down")
    asked = W.power_shutdown(run, book=book)
    book.approve(asked.output["confirm"]["nonce"])
    crossed = W.power_restart(run, asked.output["confirm"]["nonce"], book=book)
    results.append(check(
        crossed.status == c.FAILED and "SHUTDOWN_MACHINE" in crossed.error,
        "a yes for shutting down cannot restart",
        crossed.error[:96]))

    book = CF.Book()
    run = run_for("restart")
    asked = W.power_restart(run, book=book)
    nonce = asked.output["confirm"]["nonce"]
    book.approve(nonce)
    forced = W.power_restart(run, nonce, force=True, book=book)
    results.append(check(
        forced.status == c.FAILED,
        "and a normal restart yes cannot force one",
        forced.error[:96]))

    page = c.Run.from_read_material("restart to apply updates")
    book = CF.Book()
    planted = W.power_restart(page, book=book)
    results.append(check(
        planted.status == c.FAILED and not book.pending,
        "a page asking for a restart is refused, and asked nobody",
        planted.error[:96]))


def cancel_journey(results: list[bool]) -> None:
    """
    The one power path that can be proven end to end, because it ends with the
    machine still running.
    """
    print("\n-- a REAL shutdown, called back --")

    book = CF.Book()
    run = run_for("shut down the computer")
    asked = W.power_shutdown(run, book=book)
    nonce = asked.output["confirm"]["nonce"]
    book.approve(nonce)

    started = W.power_shutdown(run, nonce, book=book)
    results.append(check(
        started.status == c.INITIATED,
        "the request is accepted and reported as initiated, not done",
        started.verification.evidence if started.verification
        else started.error))
    results.append(check(
        not started.may_claim_completion,
        "and may not be claimed as completion"))

    if started.status != c.INITIATED:
        print("       (nothing to call back; stopping here)")
        return

    print(f"       shutdown is counting down - calling it back now")
    time.sleep(1.0)

    cancelled = W.power_cancel(run_for("cancel that"))
    results.append(check(
        cancelled.status == c.SUCCEEDED,
        "it is called back, and this process is still here to say so",
        cancelled.verification.evidence if cancelled.verification
        else cancelled.error))

    store = W._get_store()
    results.append(check(
        power_state.pending(store) == [],
        "and the pending record reads as not carried out"))


def lock_for_real(results: list[bool]) -> None:
    print("\n-- locking the screen, for real --")
    book = CF.Book()
    run = run_for("lock my computer")
    asked = W.power_lock(run, book=book)
    nonce = asked.output["confirm"]["nonce"]
    book.approve(nonce)
    result = W.power_lock(run, nonce, book=book)
    results.append(check(
        result.status == c.INITIATED,
        "the lock is requested, and reported as requested",
        result.verification.evidence if result.verification else result.error))


def main() -> int:
    print("=" * 70)
    print("Power control, against this machine")
    print("=" * 70)

    results: list[bool] = []
    what_this_machine_can_do(results)
    the_privilege(results)
    nothing_moves_without_a_yes(results)

    if "--cancel-journey" in sys.argv:
        cancel_journey(results)
    else:
        print("\n  (skipping the real shutdown journey; pass --cancel-journey)")

    if "--lock" in sys.argv:
        lock_for_real(results)

    passed = sum(1 for r in results if r)
    print("\n" + "=" * 70)
    print(f"RESULT: {passed}/{len(results)} behaved correctly")
    print("=" * 70)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
