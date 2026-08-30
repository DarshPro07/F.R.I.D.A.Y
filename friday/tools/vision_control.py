"""
MCP adapter for the Phase 1E vision toolset.

The tool descriptions carry the honesty contract, because the model reads them
and decides how to speak: a `partial` status means the identification is not
certain enough to assert, and `spoken_form` is the sentence to say.
"""

from __future__ import annotations

import os

from friday import contracts as c
from friday.policy import PolicyEngine, PolicyError
from friday.store import DEFAULT_DB, Store
from friday.toolsets import vision as V

_store: Store | None = None
_engine: PolicyEngine | None = None


def _get_store() -> Store:
    global _store
    if _store is None:
        _store = Store(os.getenv("ADA_DB") or DEFAULT_DB)
    return _store


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


def _execute(request: str, fn, *args, **kwargs) -> dict:
    run = c.Run.create(request, capability="vision")
    result = fn(run, *args, engine=_get_engine(), **kwargs)
    run.transition("completed" if run.all_succeeded else "partial",
                   None if run.all_succeeded else (result.error or "not verified"))
    try:
        _get_store().save_run(run)
    except Exception:
        pass
    return result.to_dict()


def register(mcp):

    @mcp.tool()
    def vision_inspect_camera(question: str = "What am I holding?") -> dict:
        """
        Look through the webcam right now and answer a question about what is
        there. Use for "look at this", "what am I holding", "what is this".

        Read `spoken_form` and say that. If `may_claim_completion` is false the
        identification is NOT certain — relay the hedge and, if
        `suggested_better_view` is set, ask the user for that view. Never turn
        an uncertain answer into a confident one.
        """
        return _execute(f"look: {question}", V.inspect_camera, question)

    @mcp.tool()
    def vision_inspect_screen(question: str = "What is on this screen?") -> dict:
        """
        Capture the user's screen now and answer a question about it. Use for
        "look at my screen", "what is this error", "read this".

        `text_found` holds any legible text verbatim. Same rule as the camera:
        say `spoken_form`, and do not upgrade an uncertain answer.
        """
        return _execute(f"screen: {question}", V.inspect_screen, question)

    @mcp.tool()
    def vision_camera_frame() -> dict:
        """
        Take one photo with the webcam and save it, without analysing it. The
        camera is released immediately. Use when the user wants a picture kept
        rather than a question answered.
        """
        return _execute("camera frame", V.camera_frame)

    @mcp.tool()
    def vision_screen_capture() -> dict:
        """Capture and save a screenshot without analysing it."""
        return _execute("screen capture", V.screen_capture)
