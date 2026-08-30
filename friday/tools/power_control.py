"""
MCP adapter for lock, sleep, hibernate, shutdown, restart - and calling it back.

Every one of these needs a person to say yes, and no autonomy setting says it
for them. The pattern is the same as `process_terminate`: call once with no
`nonce` to get the question, say the question out loud, wait for a real
answer, then call again with the nonce.

The result wording matters more here than anywhere else in the tool surface.
`outcome` is `initiated` when Windows has accepted a request and the machine
has not done it yet - which is most of the time, since any application can
still stop a shutdown. Do not tell the boss the computer restarted because
this returned. Tell them it is going to, and that it can still be called back.
"""

from __future__ import annotations

import os
from typing import TypedDict

from friday import contracts as c
from friday.policy import PolicyEngine, PolicyError
from friday.toolsets import power as W

_engine: PolicyEngine | None = None


def _get_engine() -> PolicyEngine:
    global _engine
    if _engine is None:
        _engine = PolicyEngine()
        for tool_id in (t.strip() for t in
                        os.getenv("ADA_PREAPPROVED_TOOLS", "").split(",")
                        if t.strip()):
            try:
                _engine.approve_for_session(tool_id)
            except PolicyError:
                continue
    return _engine


def _run(label: str) -> c.Run:
    return c.Run.create(label, capability="system")


class PowerResult(TypedDict):
    outcome: str
    detail: str
    question: str
    nonce: str
    seconds_until: int
    can_be_called_back: bool
    unsaved_work_at_risk: bool


def _shape(result: c.ActionResult) -> PowerResult:
    output = result.output or {}
    confirm = output.get("confirm") or {}
    return {
        "outcome": result.status,
        "detail": (result.verification.evidence if result.verification
                   else (result.error or "")),
        "question": str(confirm.get("question", "")),
        "nonce": str(confirm.get("nonce", "")),
        "seconds_until": int(output.get("seconds_until", 0) or 0),
        "can_be_called_back": bool(output.get("can_be_called_back")),
        "unsaved_work_at_risk": bool(output.get("unsaved_work_at_risk")),
    }


def register(mcp):

    @mcp.tool()
    def power_lock(nonce: str = "") -> PowerResult:
        """
        Lock the screen. Nothing is lost; the boss signs in again.

        `outcome` comes back as `initiated`: Windows accepted the request, and
        whether the session actually locked is not observable from inside it.
        """
        return _shape(W.power_lock(_run("lock the computer"), nonce,
                                   engine=_get_engine()))

    @mcp.tool()
    def power_sleep(nonce: str = "") -> PowerResult:
        """
        Put the computer to sleep. Open applications stay as they are.

        If this machine cannot sleep, `outcome` is `unsupported` - say so
        rather than offering to hibernate instead, which is a different thing.
        """
        return _shape(W.power_sleep(_run("sleep the computer"), nonce,
                                    engine=_get_engine()))

    @mcp.tool()
    def power_hibernate(nonce: str = "") -> PowerResult:
        """
        Hibernate the computer, if this machine supports it.

        Never silently replaced with sleep. `unsupported` means this machine
        has no hibernation file, and that is the honest answer.
        """
        return _shape(W.power_hibernate(_run("hibernate the computer"), nonce,
                                        engine=_get_engine()))

    @mcp.tool()
    def power_shutdown(nonce: str = "", force: bool = False) -> PowerResult:
        """
        Turn this computer off, after a thirty-second countdown.

        `outcome` is `initiated`, not done - the machine is going to shut down
        and has not yet. Tell the boss it can still be called back with
        `power_cancel`.

        `force` discards unsaved work in every open application without asking
        them. It is a **separate** question: a yes to shutting down is not a
        yes to forcing it, and passing an ordinary nonce with force=True is
        refused.
        """
        return _shape(W.power_shutdown(_run("shut down the computer"), nonce,
                                       force=force, engine=_get_engine()))

    @mcp.tool()
    def power_restart(nonce: str = "", force: bool = False) -> PowerResult:
        """
        Restart this computer, after a thirty-second countdown.

        Friday will disconnect. `outcome` is `initiated`; whether the machine
        really came back is settled the next time Friday starts.

        `force` is a separate question, for the same reason as in
        `power_shutdown`.
        """
        return _shape(W.power_restart(_run("restart the computer"), nonce,
                                      force=force, engine=_get_engine()))

    @mcp.tool()
    def power_cancel() -> PowerResult:
        """
        Call back a shutdown or restart that has not happened yet.

        Needs no approval and never has. Stopping a destructive thing is not
        itself destructive, and the boss saying "no, wait" should reach this
        immediately.
        """
        return _shape(W.power_cancel(_run("cancel the shutdown"),
                                     engine=_get_engine()))
