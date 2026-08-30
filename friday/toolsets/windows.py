"""
Windows, as things with observable state.

Batch 2B. The donor's version of this is `snap_left()` sending Win+Left and
`maximize_window()` sending Win+Up - keystrokes that cannot report what
happened, and so cannot satisfy the proof-of-work contract. Every operation
here reads the window back afterwards and reports what it actually became.

The distinction that makes this batch possible at all:

    Win+Up                     something probably maximised, probably that one
    window.maximize()          then isMaximized is True, and it is this window

`apps_*` operates on applications - open Spotify, close it, bring it forward.
This operates on individual windows, because an application is often several
and "the other Chrome window" is a real thing to ask for.

Everything here is reversible by design. Closing a window is not: unsaved work
disappears and no read-back brings it back, so it is not in this batch and
belongs with the destructive ones.
"""

from __future__ import annotations

import re

import pygetwindow

from friday import contracts as c
from friday.policy import PolicyEngine, default_engine
from friday.toolsets.system import APPROVAL_PREFIX

EXECUTION_SCOPE = "local_machine"

#: Enough windows to answer "which one" without becoming a wall of text in a
#: voice reply.
MAX_WINDOWS = 40

#: Windows that exist but are not things a person would point at.
INVISIBLE_TITLES = frozenset({"Program Manager", "Windows Input Experience",
                              "Settings", "Microsoft Text Input Application"})


class WindowError(RuntimeError):
    """No window matched, or more than one did."""


def _gate(run: c.Run, tool_id: str, engine: PolicyEngine) -> c.ActionResult | None:
    verdict = engine.decide(tool_id)
    if verdict.allowed:
        return None
    return run.record(c.started(run.run_id, tool_id).finish(
        status=c.CANCELLED,
        error=f"{APPROVAL_PREFIX}: {verdict.reason} [{verdict.decision}]",
    ))


def _scoped(payload: dict) -> dict:
    return {"execution_scope": EXECUTION_SCOPE, **payload}


def describe(window) -> dict:
    """
    A window as data.

    Titles are arbitrary Unicode - one on this machine contains U+25D0 - and
    anything that writes them to a cp1252 console dies. They are returned as
    text and never formatted into a byte stream here; the encoding belongs to
    whatever displays them.
    """
    return {
        "title": window.title,
        "left": window.left, "top": window.top,
        "width": window.width, "height": window.height,
        "minimized": bool(window.isMinimized),
        "maximized": bool(window.isMaximized),
        "active": bool(window.isActive),
        "visible": bool(window.visible),
    }


def _all_windows() -> list:
    return [w for w in pygetwindow.getAllWindows()
            if w.title.strip() and w.title not in INVISIBLE_TITLES]


def find(pattern: str) -> list:
    """
    Windows whose title matches, case-insensitively, as a substring.

    Deliberately not a regex from the caller: the caller is a language model
    relaying what a person said, and `.*` in a window title is a literal
    character far more often than it is an intention.
    """
    needle = (pattern or "").strip().lower()
    if not needle:
        return []
    return [w for w in _all_windows() if needle in w.title.lower()]


def _one(pattern: str):
    """The single window meant, or an error naming the alternatives."""
    matches = find(pattern)
    if not matches:
        open_now = [w.title for w in _all_windows()][:8]
        raise WindowError(
            f"no open window matches {pattern!r}. Open: {open_now}")
    if len(matches) > 1:
        # Never act on the first of several. "Bring Chrome forward" with four
        # Chrome windows open is a question, not an instruction.
        raise WindowError(
            f"{len(matches)} windows match {pattern!r} - say which: "
            + "; ".join(w.title[:60] for w in matches[:5]))
    return matches[0]


def _verified(run: c.Run, started, window, action: str, expected: dict):
    """
    Do the read-back, and report what the window actually became.

    `expected` is what should now be true. A window manager can refuse - a
    modal dialog will not minimise, a maximised window will not move - and the
    honest result then is PARTIAL with what it is instead, not a success
    because the call did not raise.
    """
    after = describe(window)
    wrong = {key: after[key] for key, value in expected.items()
             if after[key] != value}
    if wrong:
        return run.record(c.partial(
            started,
            f"{action} was accepted but the window is {wrong} - the window "
            f"manager may have refused it",
            output=_scoped({"window": after, "expected": expected})))
    return run.record(c.succeeded(
        started,
        output=_scoped({"window": after}),
        side_effects=(f"{action} {after['title'][:60]!r}",),
        verification=c.Verification(
            method="window_state_read_back",
            evidence=f"{after['title'][:50]!r}: "
                     + ", ".join(f"{k}={after[k]}" for k in expected)
                     + f", now {after['width']}x{after['height']} "
                       f"at {after['left']},{after['top']}"),
    ))


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


def windows_list(
    run: c.Run, pattern: str = "", *, engine: PolicyEngine = default_engine
) -> c.ActionResult:
    """What is open, where it is, and what state it is in."""
    tool_id = "windows.list"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    try:
        found = find(pattern) if pattern.strip() else _all_windows()
    except Exception as exc:                            # noqa: BLE001
        return run.record(c.failed(started, f"could not list windows: {exc}"))

    windows = [describe(w) for w in found[:MAX_WINDOWS]]
    active = next((w["title"] for w in windows if w["active"]), "")
    return run.record(c.succeeded(
        started,
        output=_scoped({"windows": windows, "count": len(found),
                        "shown": len(windows),
                        "truncated": len(found) > MAX_WINDOWS,
                        "active": active}),
        verification=c.Verification(
            method="enumerate_windows",
            evidence=f"{len(found)} window(s)"
                     + (f" matching {pattern!r}" if pattern.strip() else "")
                     + (f"; active: {active[:40]!r}" if active else "")),
    ))


# ---------------------------------------------------------------------------
# Reversible operations, each verified by reading the window back
# ---------------------------------------------------------------------------


def windows_focus(
    run: c.Run, pattern: str, *, engine: PolicyEngine = default_engine
) -> c.ActionResult:
    """
    Bring one window to the front, and report whether Windows allowed it.

    Windows deliberately restricts `SetForegroundWindow`: a process that is
    not already in the foreground may not steal focus, and the OS can refuse
    even when the documented conditions are met. pygetwindow raises when the
    call returns 0, which is that refusal arriving as an exception.

    That is not a failure of this tool, and it must not be reported as one -
    nor as a success. The window was found, it was asked for, and Windows
    said no. `GetForegroundWindow` (which is what `isActive` reads) is the
    authority on what actually happened, so it is consulted afterwards rather
    than assumed either way.

    What this deliberately does NOT do is work around the policy: no synthetic
    ALT presses, no AttachThreadInput, no repeated stealing. Those defeat a
    protection that exists so that a background process cannot take the
    keyboard out from under someone mid-sentence.
    """
    tool_id = "windows.focus"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    try:
        window = _one(pattern)
    except WindowError as exc:
        return run.record(c.failed(started, str(exc)))

    denied = ""
    try:
        if window.isMinimized:
            window.restore()
        window.activate()
    except WindowError as exc:
        return run.record(c.failed(started, str(exc)))
    except Exception as exc:                            # noqa: BLE001
        # Keep going: the OS refusing is a result, and the read-back below is
        # what decides. Some refusals still leave the window foreground.
        denied = f"{type(exc).__name__}: {exc}"

    after = describe(window)
    if after["active"]:
        return run.record(c.succeeded(
            started,
            output=_scoped({"window": after, "focus_granted": True}),
            side_effects=(f"focused {after['title'][:60]!r}",),
            verification=c.Verification(
                method="get_foreground_window",
                evidence=f"{after['title'][:50]!r} is the foreground window"),
        ))
    return run.record(c.partial(
        started,
        f"the window is there and Windows would not give it focus"
        + (f" ({denied})" if denied else "")
        + " - it restricts which processes may take the keyboard",
        output=_scoped({"window": after, "focus_granted": False,
                        "os_refusal": denied}),
    ))


def windows_minimize(
    run: c.Run, pattern: str, *, engine: PolicyEngine = default_engine
) -> c.ActionResult:
    """Put a window out of the way. Reversible with windows_restore."""
    tool_id = "windows.minimize"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    try:
        window = _one(pattern)
        window.minimize()
    except WindowError as exc:
        return run.record(c.failed(started, str(exc)))
    except Exception as exc:                            # noqa: BLE001
        return run.record(c.failed(started, f"could not minimize: {exc}"))
    return _verified(run, started, window, "minimized", {"minimized": True})


def windows_restore(
    run: c.Run, pattern: str, *, engine: PolicyEngine = default_engine
) -> c.ActionResult:
    """Bring a minimized or maximized window back to its ordinary size."""
    tool_id = "windows.restore"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    try:
        window = _one(pattern)
        window.restore()
    except WindowError as exc:
        return run.record(c.failed(started, str(exc)))
    except Exception as exc:                            # noqa: BLE001
        return run.record(c.failed(started, f"could not restore: {exc}"))
    return _verified(run, started, window, "restored",
                     {"minimized": False, "maximized": False})


def windows_maximize(
    run: c.Run, pattern: str, *, engine: PolicyEngine = default_engine
) -> c.ActionResult:
    """Fill the screen with one window. Reversible with windows_restore."""
    tool_id = "windows.maximize"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    try:
        window = _one(pattern)
        if window.isMinimized:
            window.restore()
        window.maximize()
    except WindowError as exc:
        return run.record(c.failed(started, str(exc)))
    except Exception as exc:                            # noqa: BLE001
        return run.record(c.failed(started, f"could not maximize: {exc}"))
    return _verified(run, started, window, "maximized", {"maximized": True})


#: Where a window goes when asked for half the screen. Fractions of the
#: primary display rather than pixels, because the answer differs on every
#: machine and a hard-coded 960 is wrong on all but one of them.
HALVES = {
    "left": (0.0, 0.0, 0.5, 1.0),
    "right": (0.5, 0.0, 0.5, 1.0),
    "top": (0.0, 0.0, 1.0, 0.5),
    "bottom": (0.0, 0.5, 1.0, 0.5),
    "full": (0.0, 0.0, 1.0, 1.0),
}


def windows_arrange(
    run: c.Run, pattern: str, side: str = "left", *,
    engine: PolicyEngine = default_engine
) -> c.ActionResult:
    """
    Snap a window to half the screen, and verify where it landed.

    The donor sends Win+Left and hopes. This computes the rectangle from the
    real display size, moves the window there, and reads back the geometry -
    so "it is on the left half" is a measurement rather than an assumption.
    """
    tool_id = "windows.arrange"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    key = (side or "").strip().lower()
    if key not in HALVES:
        return run.record(c.failed(
            started, f"unknown side {side!r}; use one of {sorted(HALVES)}"))

    from friday.toolsets.hardware import system_displays

    screens = system_displays(c.Run.create("displays", capability="system"),
                              engine=engine)
    if screens.status != c.SUCCEEDED or not (screens.output or {}).get("displays"):
        return run.record(c.failed(
            started, "cannot arrange a window without knowing the screen size"))
    primary = next((s for s in screens.output["displays"] if s["primary"]),
                   screens.output["displays"][0])

    fx, fy, fw, fh = HALVES[key]
    left = primary["left"] + int(primary["width"] * fx)
    top = primary["top"] + int(primary["height"] * fy)
    width = int(primary["width"] * fw)
    height = int(primary["height"] * fh)

    try:
        window = _one(pattern)
        if window.isMinimized:
            window.restore()
        if window.isMaximized:
            # A maximised window ignores moveTo, and the resulting "it did not
            # move" is confusing rather than wrong.
            window.restore()
        window.moveTo(left, top)
        window.resizeTo(width, height)
    except WindowError as exc:
        return run.record(c.failed(started, str(exc)))
    except Exception as exc:                            # noqa: BLE001
        return run.record(c.failed(started, f"could not arrange: {exc}"))

    after = describe(window)
    # Window managers round: a resize lands within a few pixels of what was
    # asked for, and demanding exactness would fail on every real machine.
    close_enough = (abs(after["left"] - left) <= 16
                    and abs(after["top"] - top) <= 16
                    and abs(after["width"] - width) <= 32
                    and abs(after["height"] - height) <= 32)
    payload = _scoped({"window": after, "side": key,
                       "asked_for": {"left": left, "top": top,
                                     "width": width, "height": height}})
    if not close_enough:
        return run.record(c.partial(
            started,
            f"asked for {width}x{height} at {left},{top} and it is "
            f"{after['width']}x{after['height']} at {after['left']},"
            f"{after['top']} - the window manager placed it differently",
            output=payload))
    return run.record(c.succeeded(
        started, output=payload,
        side_effects=(f"arranged {after['title'][:60]!r} {key}",),
        verification=c.Verification(
            method="window_geometry_read_back",
            evidence=f"{after['title'][:40]!r} is {after['width']}x"
                     f"{after['height']} at {after['left']},{after['top']} "
                     f"({key} half of {primary['width']}x{primary['height']})"),
    ))
