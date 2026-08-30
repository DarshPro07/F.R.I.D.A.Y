#!/usr/bin/env python3
"""
Window operations against a real desktop, on a window Friday opened itself.

The unit tests use a fake window on purpose: the suite runs constantly, and a
test run that minimises the boss's Chrome window every time is a test suite
that gets disabled. This is the other half - real Windows, real window
manager, real read-back - and it touches nothing it did not create.

Every check is the same shape, because it is the point of the whole toolset:
do the thing, then ask the window what it became. The donor sends Win+Left and
hopes.

    python scripts/golden_windows.py
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from friday import contracts as c  # noqa: E402
from friday.toolsets import windows as W  # noqa: E402

#: A window this script creates, owns, and is the only possible match for.
#:
#: The first version used Notepad, and it moved, maximised and minimised a
#: window holding somebody else's file. Modern Notepad opens files as tabs in
#: an existing window OR in new windows depending on a setting, so
#: `Popen(["notepad.exe"])` may hand off to a process already running; the
#: title then reflects whichever tab is active, and `terminate()` kills a
#: launcher that has already exited. The "is one already open?" guard did not
#: catch it, because the existing window was not visible to `getAllWindows()`
#: at the moment it looked.
#:
#: "Notepad is single-instance" is the wrong lesson - it is configurable. The
#: durable one is: launching an application does not prove that the returned
#: pid, or any window matching its name, is a resource this run owns.
#:
#: A tkinter window has none of those properties: one process, one window, a
#: title nothing else on earth shares, and closing the process closes it.
TITLE = "friday-window-gate-7f3a1c"

#: The whole application. It exists to be moved around.
WINDOW_SOURCE = f"""
import tkinter
root = tkinter.Tk()
root.title({TITLE!r})
root.geometry("500x360+120+120")
tkinter.Label(root, text="Friday window gate").pack(expand=True)
root.mainloop()
"""


def check(passed: bool, message: str, detail: str = "") -> bool:
    print(f"  [{'PASS' if passed else 'FAIL'}] {message}")
    if detail:
        print(f"         {detail}")
    return bool(passed)


def run_for(label: str) -> c.Run:
    return c.Run.create(label, capability="system")


def wait_for_window(seconds: float = 8.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        found = W.find(TITLE)
        if found:
            return found[0]
        time.sleep(0.25)
    return None


def main() -> int:
    results: list[bool] = []
    print("=" * 70)
    print("A window Friday opened, on the real desktop")
    print("=" * 70)

    if W.find(TITLE):
        print(f"  a window called {TITLE!r} already exists, which should be "
              f"impossible - refusing rather than acting on it")
        return 2

    process = subprocess.Popen([sys.executable, "-c", WINDOW_SOURCE])
    try:
        window = wait_for_window()
        results.append(check(window is not None, "the window opened"))
        if window is None:
            return 1
        print(f"  window: {window.title!r}\n")

        # --- arrange, which is where a keystroke cannot say what happened ---
        for side, describe_side in (("left", "left half"),
                                    ("right", "right half"),
                                    ("full", "whole screen")):
            result = W.windows_arrange(run_for("arrange"), TITLE, side)
            after = (result.output or {}).get("window", {})
            asked = (result.output or {}).get("asked_for", {})
            results.append(check(
                result.status == c.SUCCEEDED,
                f"snapped to the {describe_side}, and the window agrees",
                f"asked {asked.get('width')}x{asked.get('height')} at "
                f"{asked.get('left')},{asked.get('top')}; got "
                f"{after.get('width')}x{after.get('height')} at "
                f"{after.get('left')},{after.get('top')}"))

        # --- minimise and restore ------------------------------------------
        minimized = W.windows_minimize(run_for("minimize"), TITLE)
        results.append(check(
            minimized.status == c.SUCCEEDED
            and minimized.output["window"]["minimized"] is True,
            "minimised, read back as minimised"))

        restored = W.windows_restore(run_for("restore"), TITLE)
        results.append(check(
            restored.status == c.SUCCEEDED
            and restored.output["window"]["minimized"] is False,
            "restored, read back as not minimised"))

        # --- maximise -------------------------------------------------------
        maximized = W.windows_maximize(run_for("maximize"), TITLE)
        results.append(check(
            maximized.status == c.SUCCEEDED
            and maximized.output["window"]["maximized"] is True,
            "maximised, read back as maximised"))

        # --- focus ----------------------------------------------------------
        focused = W.windows_focus(run_for("focus"), TITLE)
        results.append(check(
            focused.status in (c.SUCCEEDED, c.PARTIAL),
            "focus was attempted and reported honestly",
            focused.verification.evidence if focused.verification
            else (focused.error or "")))

        # --- it is in the list, with real geometry --------------------------
        listing = W.windows_list(run_for("list"), TITLE)
        ours = (listing.output or {}).get("windows", [])
        results.append(check(
            len(ours) == 1 and ours[0]["width"] > 0,
            "it appears in the listing with real geometry",
            str(ours[0]) if ours else "not listed"))

        # --- and a name that matches nothing is refused ---------------------
        missing = W.windows_focus(run_for("focus"), "a window that is not open")
        results.append(check(
            missing.status == c.FAILED and "no open window" in missing.error,
            "a name matching nothing is refused, not guessed at"))
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
        print("\n  (the window Friday opened has been closed again)")

    passed = sum(1 for r in results if r)
    print("\n" + "=" * 70)
    print(f"RESULT: {passed}/{len(results)} behaved correctly")
    print("=" * 70)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
