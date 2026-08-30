"""
MCP adapter for closing and ending programs.

Thin, like the other adapters: a Run per call, the toolset does the work, the
ActionResult is serialised. No logic here.

The one thing worth reading twice is `nonce`. It arrives from the model,
having been handed to the model in the previous turn's result - and that is
safe, because it is a correlation handle rather than a credential. It says
*which* pending question is being answered. It does not say that anybody
answered: `Book.consume` refuses anything not APPROVED, and only a person's
spoken yes, matched above this boundary by `friday.approval`, sets that.

There is deliberately no tool here that approves anything.
"""

from __future__ import annotations

import os
from typing import TypedDict

from friday import contracts as c
from friday.policy import PolicyEngine, PolicyError
from friday.toolsets import processes as P

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


class CloseResult(TypedDict):
    closed: bool
    outcome: str
    detail: str
    windows_asked: int
    force_would_need_confirmation: bool
    token: str


class TerminateResult(TypedDict):
    ended: bool
    outcome: str
    detail: str
    question: str
    nonce: str
    unsaved_work_at_risk: bool


def register(mcp):

    @mcp.tool()
    def process_close(pattern: str) -> CloseResult:
        """
        Ask a running program to close, the way clicking its X does.

        The application may put up "save your changes?" and it may decline.
        That is not a failure - it is the application doing its job - and the
        result says so rather than pretending it closed.

        This never ends a process. If the program will not go, say so to the
        boss and use `process_terminate`, which asks first.

        `pattern` matches the program name or a pid. It refuses rather than
        guessing when more than one thing matches.
        """
        result = P.processes_close(_run(f"close {pattern}"), pattern,
                                   engine=_get_engine())
        output = result.output or {}
        return {
            "closed": bool(output.get("closed")),
            "outcome": result.status,
            "detail": (result.verification.evidence if result.verification
                       else (result.error or "")),
            "windows_asked": int(output.get("windows_asked", 0) or 0),
            "force_would_need_confirmation": bool(
                output.get("force_would_need_confirmation")),
            "token": str(output.get("token", "")),
        }

    @mcp.tool()
    def process_terminate(pattern: str, nonce: str = "") -> TerminateResult:
        """
        End a program that will not close. Anything unsaved in it is lost.

        Call it once with no `nonce` to get the question. Say that question to
        the boss and wait for a real answer - you cannot answer it yourself,
        and there is no tool that lets you. When they say yes, call again
        passing the `nonce` you were given.

        The target is resolved again at that moment, so if the program exited
        in between, nothing else is ended in its place.

        Windows system processes, Friday itself, and whatever Friday is
        running inside are refused before the question is ever asked.
        """
        result = P.processes_terminate(_run(f"terminate {pattern}"), pattern,
                                       nonce, engine=_get_engine())
        output = result.output or {}
        confirm = output.get("confirm") or {}
        return {
            "ended": result.status == c.SUCCEEDED,
            "outcome": result.status,
            "detail": (result.verification.evidence if result.verification
                       else (result.error or "")),
            "question": str(confirm.get("question", "")),
            "nonce": str(confirm.get("nonce", "")),
            "unsaved_work_at_risk": bool(output.get("unsaved_work_at_risk")),
        }
