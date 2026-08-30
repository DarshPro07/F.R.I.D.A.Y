"""
MCP adapter for the music toolset.

This is the one to use for "play <song>". The Spotify tools remain for
controlling Spotify when the user is already listening there, but Spotify
Free cannot start a named track, and these can.
"""

from __future__ import annotations

import os

from friday import contracts as c
from friday.policy import PolicyEngine, PolicyError
from friday.store import DEFAULT_DB, Store
from friday.toolsets import music as M

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
    run = c.Run.create(request, capability="music")
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
    def music_play(query: str) -> dict:
        """
        Play a song by name. Use this for "play <anything>" — it needs no
        account and works for essentially any track.

        Takes a few seconds to start. Only say it is playing when
        may_claim_completion is true; the verification names the actual track
        that started, which may differ from what was asked for.
        """
        return _execute(f"play {query}", M.music_play, query)

    @mcp.tool()
    def music_play_mood(mood: str) -> dict:
        """
        Play something matching a mood: happy, relaxing, party, focus, sad,
        energetic, romantic.

        Selection is by search phrasing, not audio analysis — say "here's
        something relaxing", not "I analysed your library".
        """
        return _execute(f"play {mood} music", M.music_play_mood, mood)

    @mcp.tool()
    def music_search(query: str, limit: int = 8) -> dict:
        """Find songs without playing them. Returns titles and durations."""
        return _execute(f"search music: {query}", M.music_search, query, limit=limit)

    @mcp.tool()
    def music_pause() -> dict:
        """Pause playback."""
        return _execute("pause music", M.music_pause)

    @mcp.tool()
    def music_resume() -> dict:
        """Resume playback from where it was paused."""
        return _execute("resume music", M.music_resume)

    @mcp.tool()
    def music_next() -> dict:
        """Skip to the next result from the last search."""
        return _execute("next track", M.music_next)

    @mcp.tool()
    def music_stop() -> dict:
        """Stop playback entirely and shut the player down."""
        return _execute("stop music", M.music_stop)

    @mcp.tool()
    def music_current() -> dict:
        """What is playing right now, and how far in."""
        return _execute("what is playing", M.music_current)
