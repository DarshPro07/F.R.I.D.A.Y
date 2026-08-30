"""
MCP adapter for individual windows.

`apps_*` operates on applications. This operates on windows, because an
application is often several of them and "the other Chrome window" is a real
thing to ask for.

There is no close. Closing is not reversible - unsaved work disappears, and no
read-back brings it back - so it does not belong in a batch defined by being
undoable.
"""

from __future__ import annotations

import os
from typing import TypedDict

from friday import contracts as c
from friday.policy import PolicyEngine, PolicyError
from friday.toolsets import windows as W

_engine: PolicyEngine | None = None


def _get_engine() -> PolicyEngine:
    global _engine
    if _engine is None:
        _engine = PolicyEngine()
        for tool_id in (t.strip() for t in
                        os.getenv("ADA_PREAPPROVED_TOOLS", "").split(",") if t.strip()):
            try:
                _engine.approve_for_session(tool_id)
            except PolicyError:
                continue
    return _engine


class WindowList(TypedDict):
    windows: list
    count: int
    shown: int
    truncated: bool
    active: str
    error: str


class WindowChanged(TypedDict):
    #: The window as it is NOW, read back after the operation. The point of
    #: the whole toolset: a keystroke cannot report this.
    window: dict
    #: True only when the read-back matched what was asked for. A window
    #: manager can refuse - a modal will not minimise - and that is a PARTIAL
    #: result rather than a failure or a success.
    as_asked: bool
    error: str


def _run(label: str):
    return c.Run.create(label, capability="system")


def _changed(result) -> WindowChanged:
    if result.status == c.SUCCEEDED and result.output:
        return {"window": result.output["window"], "as_asked": True,
                "error": ""}
    if result.status == c.PARTIAL and result.output:
        return {"window": result.output["window"], "as_asked": False,
                "error": result.error or ""}
    return {"window": {}, "as_asked": False,
            "error": result.error or "the window operation failed"}


def register(mcp):

    @mcp.tool()
    def windows_list(pattern: str = "") -> WindowList:
        """
        What windows are open, where they are, and which one is in front.

        `pattern` narrows by title, case-insensitively, as a substring. Use it
        before any of the others when the boss said something like "the other
        Chrome window" - they take the same pattern, and they refuse rather
        than guessing when it matches more than one.
        """
        result = W.windows_list(_run("list windows"), pattern,
                               engine=_get_engine())
        if result.status != c.SUCCEEDED or result.output is None:
            return {"windows": [], "count": 0, "shown": 0, "truncated": False,
                    "active": "", "error": result.error or "failed"}
        output = result.output
        return {"windows": output["windows"], "count": output["count"],
                "shown": output["shown"], "truncated": output["truncated"],
                "active": output["active"], "error": ""}

    @mcp.tool()
    def windows_focus(pattern: str) -> WindowChanged:
        """Bring one window to the front. Restores it first if minimized."""
        return _changed(W.windows_focus(_run("focus window"), pattern,
                                        engine=_get_engine()))

    @mcp.tool()
    def windows_minimize(pattern: str) -> WindowChanged:
        """Put a window out of the way. Undo with windows_restore."""
        return _changed(W.windows_minimize(_run("minimize window"), pattern,
                                           engine=_get_engine()))

    @mcp.tool()
    def windows_restore(pattern: str) -> WindowChanged:
        """Bring a minimized or maximized window back to its ordinary size."""
        return _changed(W.windows_restore(_run("restore window"), pattern,
                                          engine=_get_engine()))

    @mcp.tool()
    def windows_maximize(pattern: str) -> WindowChanged:
        """Fill the screen with one window. Undo with windows_restore."""
        return _changed(W.windows_maximize(_run("maximize window"), pattern,
                                           engine=_get_engine()))

    @mcp.tool()
    def windows_arrange(pattern: str, side: str = "left") -> WindowChanged:
        """
        Snap a window to half the screen: left, right, top, bottom or full.

        The rectangle is computed from the real display size and the window is
        read back afterwards, so "it is on the left half" is measured rather
        than assumed.
        """
        return _changed(W.windows_arrange(_run("arrange window"), pattern,
                                          side, engine=_get_engine()))
