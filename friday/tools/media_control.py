"""MCP adapter for the Phase 1F media toolset."""

from __future__ import annotations

import os

from friday import contracts as c
from friday.policy import PolicyEngine, PolicyError
from friday.store import DEFAULT_DB, Store
from friday.toolsets import media as M

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
    run = c.Run.create(request, capability="media")
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
    def spotify_current() -> dict:
        """What Spotify is playing right now, read from its own window title."""
        return _execute("what is playing", M.spotify_current)

    @mcp.tool()
    def spotify_open() -> dict:
        """Open the Spotify desktop app, verified by process."""
        return _execute("open spotify", M.spotify_open)

    @mcp.tool()
    def spotify_resume() -> dict:
        """Resume playback. Confirmed by Spotify's title showing a track."""
        return _execute("resume spotify", M.spotify_resume)

    @mcp.tool()
    def spotify_pause() -> dict:
        """Pause playback. Confirmed by Spotify's title going idle."""
        return _execute("pause spotify", M.spotify_pause)

    @mcp.tool()
    def spotify_next() -> dict:
        """Skip to the next track. Confirmed by the title becoming a different one."""
        return _execute("next track", M.spotify_next)

    @mcp.tool()
    def spotify_previous() -> dict:
        """Go back a track. Confirmed by the title becoming a different one."""
        return _execute("previous track", M.spotify_previous)

    @mcp.tool()
    def spotify_search(query: str) -> dict:
        """
        Open a search in Spotify. This SHOWS results; it does not start
        playback. Do not tell the user a song is playing after calling this.
        """
        return _execute(f"spotify search: {query}", M.spotify_search, query)

    @mcp.tool()
    def spotify_play(query: str = "") -> dict:
        """
        With no query, resume playback. With a query, open a search for it.

        A query returns 'partial': without Spotify Web API credentials a
        specific track cannot be started, only found. Say that plainly rather
        than claiming the song is playing.
        """
        return _execute(f"spotify play: {query or '(resume)'}", M.spotify_play, query)
