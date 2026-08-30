"""
MCP adapter for screen brightness.

Two tools, and both can answer "this machine has no such control" without that
being a failure. A desktop with an external monitor usually exposes no WMI
brightness instance at all, and the honest reply is that the monitor has
buttons.
"""

from __future__ import annotations

import os
from typing import TypedDict

from friday import contracts as c
from friday.policy import PolicyEngine, PolicyError
from friday.toolsets import brightness as B

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


class Brightness(TypedDict):
    #: False means this machine exposes no control - not that anything broke.
    supported: bool
    percent: int | None
    error: str


class BrightnessChanged(TypedDict):
    supported: bool
    previous_percent: int | None
    requested_percent: int
    #: Set when a request below the floor was raised to it. A voice assistant
    #: taking the screen to zero leaves somebody unable to see how to undo it.
    floored_to: int | None
    observed_percent: int | None
    reversible: bool
    error: str


def _run(label: str):
    return c.Run.create(label, capability="system")


def register(mcp):

    @mcp.tool()
    def brightness_get() -> Brightness:
        """
        How bright the screen is.

        `supported: false` means this monitor exposes no brightness control -
        common on desktops, where the buttons are on the monitor itself. That
        is an answer, not an error.
        """
        result = B.brightness_get(_run("brightness"), engine=_get_engine())
        output = result.output or {}
        return {"supported": bool(output.get("supported")),
                "percent": output.get("percent"),
                "error": "" if result.status == c.SUCCEEDED else (result.error or "")}

    @mcp.tool()
    def brightness_set(percent: int) -> BrightnessChanged:
        """
        Set the screen brightness, 0-100, and read it back.

        Requests below 10% are raised to it: a screen taken to black is hard
        to recover from without finding physical buttons.
        """
        result = B.brightness_set(_run("brightness"), percent,
                                  engine=_get_engine())
        output = result.output or {}
        return {"supported": bool(output.get("supported")),
                "previous_percent": output.get("previous_percent"),
                "requested_percent": output.get("requested_percent", percent),
                "floored_to": output.get("floored_to"),
                "observed_percent": output.get("observed_percent"),
                "reversible": bool(output.get("reversible")),
                "error": "" if result.status == c.SUCCEEDED else (result.error or "")}
