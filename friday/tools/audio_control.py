"""
MCP adapter for audio.

Sessions rather than processes, because that is what Windows actually
controls: one application can hold several sessions, and the Volume Mixer
people see is a list of them.

`session_id` is the session instance identifier - the thing Microsoft
documents as unique per session instance. The model never has to hold it to
say "lower Spotify": names and process names resolve, and the id is what comes
back so a follow-up can be exact. Nothing here exposes a COM pointer.

The master volume is a separate tool on purpose. It is every application at
once, and "turn it down" almost always means the thing that is playing.
"""

from __future__ import annotations

import os
from typing import TypedDict

from friday import contracts as c
from friday.policy import PolicyEngine, PolicyError
from friday.toolsets import audio as A

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


class Sessions(TypedDict):
    sessions: list
    count: int
    #: How many are worth acting on. A session can be listed, be reported
    #: ACTIVE by Windows, and belong to a process that no longer exists.
    actionable: int
    error: str


class VolumeChanged(TypedDict):
    session_id: str
    process: str
    previous_percent: int
    requested_percent: int
    #: What it reads as NOW. Drivers round, and a value that was accepted and
    #: ignored is a refusal rather than a change.
    observed_percent: int
    #: Present so a task that fails later can put this back. The old value is
    #: not recoverable once this result is discarded.
    reversible: bool
    error: str


class MuteChanged(TypedDict):
    session_id: str
    process: str
    previous_muted: bool
    observed_muted: bool
    reversible: bool
    error: str


class MasterVolume(TypedDict):
    previous_percent: int
    requested_percent: int
    observed_percent: int
    #: Named rather than implied: this one moved every application at once.
    scope: str
    reversible: bool
    error: str


def _run(label: str):
    return c.Run.create(label, capability="system")


def register(mcp):

    @mcp.tool()
    def audio_sessions(pattern: str = "") -> Sessions:
        """
        What is making sound, how loud, and whether it can be changed.

        Ask this first when the boss names an application - it returns the
        `session_id` the other tools take, and it says which sessions are
        `actionable`. A session that is listed is not necessarily one that can
        be acted on: Windows reports sessions for processes that have exited,
        and changing one of those succeeds while nothing gets quieter.
        """
        result = A.audio_sessions(_run("audio sessions"), pattern,
                                  engine=_get_engine())
        if result.status != c.SUCCEEDED or result.output is None:
            return {"sessions": [], "count": 0, "actionable": 0,
                    "error": result.error or "failed"}
        found = result.output["sessions"]
        return {"sessions": found, "count": len(found),
                "actionable": sum(1 for s in found if s["actionable"]),
                "error": ""}

    @mcp.tool()
    def audio_session_volume(session: str, percent: int) -> VolumeChanged:
        """
        Set one application's volume, 0-100, and read it back.

        `session` is a `session_id` from audio_sessions, or an application
        name - "spotify", "chrome". A name matching several sessions is
        refused rather than guessed at: one app can hold several, and picking
        one at random has an audible result.

        This changes that application only. For the whole machine use
        audio_master_volume, and do not reach for it unless the boss meant the
        machine rather than the thing that is playing.
        """
        result = A.audio_session_volume(_run("session volume"), session,
                                        percent, engine=_get_engine())
        output = result.output or {}
        described = output.get("session", {})
        return {
            "session_id": described.get("session_id", ""),
            "process": described.get("process", ""),
            "previous_percent": output.get("previous_percent", 0),
            "requested_percent": output.get("requested_percent", percent),
            "observed_percent": output.get("observed_percent", 0),
            "reversible": bool(output.get("reversible")),
            "error": "" if result.status == c.SUCCEEDED else (result.error or ""),
        }

    @mcp.tool()
    def audio_session_mute(session: str, muted: bool = True) -> MuteChanged:
        """Mute or unmute one application, and read it back."""
        result = A.audio_session_mute(_run("session mute"), session, muted,
                                      engine=_get_engine())
        output = result.output or {}
        described = output.get("session", {})
        return {
            "session_id": described.get("session_id", ""),
            "process": described.get("process", ""),
            "previous_muted": bool(output.get("previous_muted")),
            "observed_muted": bool(output.get("observed_muted")),
            "reversible": bool(output.get("reversible")),
            "error": "" if result.status == c.SUCCEEDED else (result.error or ""),
        }

    @mcp.tool()
    def audio_master_volume(percent: int) -> MasterVolume:
        """
        Set the master volume for the whole machine, 0-100.

        Every application at once. "Turn it down" while music is playing means
        that application, not this - reach for audio_session_volume first, and
        use this when he means the computer.
        """
        result = A.audio_master_volume(_run("master volume"), percent,
                                       engine=_get_engine())
        output = result.output or {}
        return {
            "previous_percent": output.get("previous_percent", 0),
            "requested_percent": output.get("requested_percent", percent),
            "observed_percent": output.get("observed_percent", 0),
            "scope": output.get("scope", "every application on this machine"),
            "reversible": bool(output.get("reversible")),
            "error": "" if result.status == c.SUCCEEDED else (result.error or ""),
        }
