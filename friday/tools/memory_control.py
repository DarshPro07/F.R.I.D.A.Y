"""
MCP adapter for the Phase 1D memory toolset.

Tool descriptions matter more here than elsewhere: the model decides when to
consult memory, so "use this when the user refers to something from before" is
part of the contract, not decoration.
"""

from __future__ import annotations

import os

from friday import contracts as c
from friday.policy import PolicyEngine, PolicyError
from friday.store import FACT
from friday.toolsets import memory as M

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


def _execute(request: str, fn, *args, **kwargs) -> dict:
    run = c.Run.create(request, capability="memory")
    result = fn(run, *args, engine=_get_engine(), **kwargs)
    run.transition("completed" if run.all_succeeded else "partial",
                   None if run.all_succeeded else (result.error or "not verified"))
    try:
        M.store().save_run(run)
    except Exception:
        pass
    return result.to_dict()


def register(mcp):

    @mcp.tool()
    def memory_remember(subject: str, value: str, kind: str = FACT,
                        source: str = "user stated it",
                        confidence: float = 1.0) -> dict:
        """
        Store something durably so it survives a restart.

        Use when the user says "remember that ...", states a preference, or
        settles a project detail.

        kind must be one of FACT (the user stated it), PREFERENCE (how they
        like things), PATTERN (repeated behaviour), INFERENCE (you worked it
        out). Use INFERENCE with a confidence below 1.0 when you are guessing
        — never record a guess as a FACT.
        """
        return _execute(f"remember {subject}", M.memory_remember, subject, value,
                        kind=kind, source=source, confidence=confidence)

    @mcp.tool()
    def memory_recall(subject: str) -> dict:
        """
        Look up what is remembered about a subject. Tries an exact key first,
        then falls back to keyword matching, so you do not need to know the
        exact key — "Arc Reactor language" finds a row keyed
        "Project Arc Reactor.language". `match_type` says which path answered.

        Each result carries a `spoken_form` phrased to match how it was
        learned — say that rather than inventing your own phrasing, so an
        inference is never repeated as a fact.
        """
        return _execute(f"recall {subject}", M.memory_recall, subject)

    @mcp.tool()
    def memory_search(query: str, limit: int = 20) -> dict:
        """
        Search memories by keyword when you do not know the exact subject.

        Use whenever the user refers to something from a previous session —
        a project, a preference, a decision. Prefer this over guessing.
        """
        return _execute(f"search memory: {query}", M.memory_search, query, limit=limit)

    @mcp.tool()
    def memory_forget(subject: str) -> dict:
        """
        Stop treating a memory as current. Requires approval. Rows are marked
        superseded, not deleted, so history is retained.
        """
        return _execute(f"forget {subject}", M.memory_forget, subject)

    @mcp.tool()
    def memory_record_decision(project: str, decision: str,
                               rationale: str = "") -> dict:
        """Record a project decision and why it was made."""
        return _execute(f"decision for {project}", M.project_record_decision,
                        project, decision, rationale=rationale or None)

    @mcp.tool()
    def memory_project_context(project: str) -> dict:
        """Everything recorded about a project: memories and decisions."""
        return _execute(f"context for {project}", M.project_context, project)

    @mcp.tool()
    def projects_list(limit: int = 20) -> dict:
        """
        Every project on record, most recently touched first. Use for "what am
        I working on", "what projects are active", "what have we got going".
        """
        return _execute("list projects", M.projects_list, limit=limit)

    @mcp.tool()
    def project_resume(project: str) -> dict:
        """
        Pick a project up again: what was decided, what is still unanswered,
        what is in flight and what to do next. Use for "continue Halo",
        "where were we on that", "what's left to do on it". Reconstructed from
        the store, so it works after a restart with no history.
        """
        return _execute(f"resume {project}", M.project_resume, project)

    @mcp.tool()
    def memory_session_recap(limit: int = 3) -> dict:
        """
        What happened in recent sessions. Use for "where were we", "what did
        we do yesterday", "catch me up". Reading does not consume it.
        """
        return _execute("session recap", M.session_recap, limit=limit)

    @mcp.tool()
    def memory_record_utterance(raw: str, normalized: str = "",
                                reason: str = "", confidence: float = 0.0) -> dict:
        """
        Record what was heard and, separately, what you corrected it to.

        Use when transcription looks wrong — e.g. raw "start plot code",
        normalized "start Claude Code". The raw text is never overwritten.
        """
        return _execute("record utterance", M.record_utterance, raw,
                        normalized=normalized or None, reason=reason or None,
                        confidence=confidence or None)
