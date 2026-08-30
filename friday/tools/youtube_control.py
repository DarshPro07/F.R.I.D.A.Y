"""
MCP adapter for YouTube.

The point of these existing at all: "find Techno Gamerz and review the
channel" is a data question, and answering it by opening a browser was the
wrong shape. None of these open anything.
"""

from __future__ import annotations

import os

from friday import contracts as c
from friday.policy import PolicyEngine, PolicyError
from friday.store import DEFAULT_DB, Store
from friday.toolsets import youtube as Y

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


async def _execute(request: str, fn, *args, **kwargs) -> dict:
    run = c.Run.create(request, capability="youtube")
    result = await fn(run, *args, engine=_get_engine(), **kwargs)
    run.transition("completed" if run.all_succeeded else "partial",
                   None if run.all_succeeded else (result.error or "not verified"))
    try:
        _get_store().save_run(run)
    except Exception:
        pass
    return result.to_dict()


def register(mcp):

    @mcp.tool()
    async def youtube_find_channel(query: str) -> dict:
        """
        Find a YouTube channel by name or @handle and return its exact id.

        Use this FIRST whenever a channel is mentioned - it pins down which
        channel is meant, so a later request cannot drift onto a fan channel
        with a similar name. The answer is remembered, so asking twice is free.
        """
        return await _execute(f"find channel {query}", Y.youtube_find_channel,
                              query, store=_get_store())

    @mcp.tool()
    async def youtube_channel_details(channel: str) -> dict:
        """
        Who a channel is: title, handle, and where the Data API is available,
        subscriber count, total views and video count.

        `channel` may be a name, an @handle or a UC... id. No browser opens.
        """
        return await _execute(f"channel details {channel}",
                              Y.youtube_channel_details, channel,
                              store=_get_store())

    @mcp.tool()
    async def youtube_recent_videos(channel: str, limit: int = 5) -> dict:
        """
        A channel's newest uploads with titles, dates and real view counts.

        This is what answers "review this channel", "how often do they post",
        "which recent video did best". Cheap - it reads the channel's own
        uploads feed rather than searching.
        """
        return await _execute(f"recent videos {channel}",
                              Y.youtube_recent_videos, channel, limit=limit,
                              store=_get_store())

    @mcp.tool()
    async def youtube_video_details(video: str) -> dict:
        """
        Full detail for one video: description, tags, duration, views, likes,
        comment count. Takes a video id or any YouTube url.

        Needs the YouTube Data API. If it is not configured this fails and
        says so - use youtube_recent_videos, which does not need it.
        """
        return await _execute(f"video details {video}", Y.youtube_video_details,
                              video)
