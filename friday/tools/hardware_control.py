"""
MCP adapter for what this machine physically is.

Four reads, each answering a question `system_get_info` and
`system_resource_usage` could not: whether it is on battery, what else is
mounted, how many screens, what it is plugged into.
"""

from __future__ import annotations

import os
from typing import TypedDict

from friday import contracts as c
from friday.policy import PolicyEngine, PolicyError
from friday.toolsets import hardware as H

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


class Battery(TypedDict):
    has_battery: bool
    percent: float | None
    plugged_in: bool | None
    hours_left: float | None
    error: str


class Disks(TypedDict):
    volumes: list
    count: int
    #: Mount points at 90% or more. The answer to "why is everything slow" is
    #: often here, and it is not visible in a single overall percentage.
    nearly_full: list
    error: str


class Displays(TypedDict):
    displays: list
    count: int
    error: str


class Network(TypedDict):
    adapters: list
    count: int
    connected: int
    error: str


def _run(label: str):
    return c.Run.create(label, capability="system")


def register(mcp):

    @mcp.tool()
    def system_battery() -> Battery:
        """
        Charge, and whether it is going up or down.

        A machine with no battery answers has_battery=false rather than
        failing - "this is a desktop" is the answer, not an error.
        """
        result = H.system_battery(_run("battery"), engine=_get_engine())
        if result.status != c.SUCCEEDED or result.output is None:
            return {"has_battery": False, "percent": None, "plugged_in": None,
                    "hours_left": None, "error": result.error or "failed"}
        output = result.output
        return {"has_battery": output["has_battery"],
                "percent": output["percent"],
                "plugged_in": output["plugged_in"],
                "hours_left": output["hours_left"], "error": ""}

    @mcp.tool()
    def system_disks() -> Disks:
        """
        Every mounted volume, with free space.

        Not the same question as system_resource_usage, which reports the one
        volume the process happens to be running from.
        """
        result = H.system_disks(_run("disks"), engine=_get_engine())
        if result.status != c.SUCCEEDED or result.output is None:
            return {"volumes": [], "count": 0, "nearly_full": [],
                    "error": result.error or "failed"}
        return {"volumes": result.output["volumes"],
                "count": result.output["count"],
                "nearly_full": result.output["nearly_full"], "error": ""}

    @mcp.tool()
    def system_displays() -> Displays:
        """How many screens, how big, and which one is the primary."""
        result = H.system_displays(_run("displays"), engine=_get_engine())
        if result.status != c.SUCCEEDED or result.output is None:
            return {"displays": [], "count": 0,
                    "error": result.error or "failed"}
        return {"displays": result.output["displays"],
                "count": result.output["count"], "error": ""}

    @mcp.tool()
    def system_network() -> Network:
        """
        Which network adapters exist, which are up, and their addresses.

        The question behind "why can't you reach anything?". A VPN adapter up
        and a physical one down look identical from inside a failed request.
        MAC addresses are deliberately not reported.
        """
        result = H.system_network(_run("network"), engine=_get_engine())
        if result.status != c.SUCCEEDED or result.output is None:
            return {"adapters": [], "count": 0, "connected": 0,
                    "error": result.error or "failed"}
        return {"adapters": result.output["adapters"],
                "count": result.output["count"],
                "connected": result.output["connected"], "error": ""}
