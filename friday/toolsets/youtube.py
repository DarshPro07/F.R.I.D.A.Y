"""
YouTube as data, not as a web page to look at.

"Find Techno Gamerz and review the channel" failed because there was no
YouTube capability at all - only `browser.open("youtube.com")` and a music
searcher. Opening a page is not reviewing a channel.

## Two backends, and the fallback is not a hack

    api    YouTube Data API v3. Full depth: duration, likes, comments,
           per-video statistics. Needs a key with the API enabled.
    feed   the channel's Atom feed at /feeds/videos.xml?channel_id=...
           No key, no quota, and it carries titles, publish dates and view
           counts. It is a documented endpoint, not scraping, and it does not
           rot the way the music path did.

The API is not enabled on this project's key today - `channels.list` answers
403 "Requests to this API youtube method ... are blocked" - so the feed is
what actually runs, and it is enough for L1 and L2. The API path is written,
tested against its own shapes, and takes over the moment the key works.

## Quota is a design constraint, not a detail

`search.list` costs 100 units against a 10,000/day allowance, and has its own
100-call daily limit. `channels.list`, `playlistItems.list` and `videos.list`
cost 1. So:

  * a channel is resolved ONCE and the id is remembered. "Do it again" must
    not spend another search.
  * recent uploads come from the uploads playlist, never from
    `search.list(order=date)`.

## Resolution order

    a channel id (UC...)          -> used directly, no lookup at all
    a remembered name             -> straight from memory
    a handle or name              -> the handle page, once, then remembered

Remembering also stops a later request quietly landing on a fan channel with
a similar name, which is the failure mode that matters here.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

import httpx

from friday import contracts as c
from friday.policy import PolicyEngine, default_engine
from friday.store import FACT, Store
from friday.toolsets.web import USER_AGENT, _gate

EXECUTION_SCOPE = "network"

API_BASE = "https://www.googleapis.com/youtube/v3"
FEED_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
HANDLE_URL = "https://www.youtube.com/{handle}"
WATCH_URL = "https://www.youtube.com/watch?v={video_id}"
CHANNEL_URL = "https://www.youtube.com/channel/{channel_id}"

#: A channel id is stable and unmistakable, so it is worth recognising before
#: spending anything to look one up.
CHANNEL_ID = re.compile(r"^UC[\w-]{20,}$")
VIDEO_ID = re.compile(r"^[\w-]{11}$")

#: Where a resolved channel lives, so the second ask is free.
CHANNEL_SUBJECT = "youtube.channel.{slug}"

TIMEOUT = 20.0

_NS = {
    "a": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
    "m": "http://search.yahoo.com/mrss/",
}


def api_key() -> str:
    """A dedicated key wins; the general Google key is tried after it."""
    return (os.getenv("YOUTUBE_API_KEY", "").strip()
            or os.getenv("GOOGLE_API_KEY", "").strip())


def _scoped(payload: dict) -> dict:
    return {"execution_scope": EXECUTION_SCOPE, **payload}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (text or "").strip().lower()).strip("_")[:60]


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True,
                             headers={"User-Agent": USER_AGENT})


@dataclass(frozen=True)
class Channel:
    channel_id: str
    title: str = ""
    handle: str = ""
    source: str = ""      # id | memory | handle_page | api

    @property
    def url(self) -> str:
        return CHANNEL_URL.format(channel_id=self.channel_id)

    def as_dict(self) -> dict:
        return {"channel_id": self.channel_id, "title": self.title,
                "handle": self.handle, "url": self.url,
                "resolved_from": self.source}


# ---------------------------------------------------------------------------
# Resolution - the part that has to be cheap
# ---------------------------------------------------------------------------


def remembered_channel(store: Store, query: str) -> Channel | None:
    rows = store.recall(CHANNEL_SUBJECT.format(slug=_slug(query)))
    if not rows:
        return None
    value = str(rows[0]["value"])
    channel_id, _, title = value.partition("|")
    return Channel(channel_id=channel_id, title=title or query, source="memory")


def remember_channel(store: Store, query: str, channel: Channel) -> None:
    try:
        store.remember(
            CHANNEL_SUBJECT.format(slug=_slug(query)),
            f"{channel.channel_id}|{channel.title}", kind=FACT,
            source=f"resolved from {channel.source}: {channel.url}",
            scope="possessions")
    except Exception:
        pass


async def channel_from_handle(client: httpx.AsyncClient, handle: str) -> Channel | None:
    """
    The handle page carries the channel id in its own metadata.

    Only for a handle the user actually gave. **Guessing** a handle from a
    name is what this used to do, and it was wrong in the first real test:
    "Techno Gamerz" became "@TechnoGamerz", which is a genuine channel with
    one video from 2010 and 2,790 views. The channel meant has 12.5 million
    subscribers and lives at @TechnoGamerzOfficial.

    Nothing about that failure looked like a failure - a real id, a matching
    title, real view counts. It would have been reported as a confident review
    of the wrong creator, which is the worst kind of wrong.
    """
    text = handle.strip()
    if not text.startswith("@"):
        return None
    try:
        response = await client.get(HANDLE_URL.format(handle=text))
    except Exception:
        return None
    if response.status_code != 200:
        return None
    return _channel_from_page(response.text, text, handle=text)


def _channel_from_page(html: str, fallback_title: str,
                       handle: str = "") -> Channel | None:
    found = re.search(r'"(?:channelId|externalId)"\s*:\s*"(UC[\w-]{20,})"', html)
    if not found:
        return None
    title = re.search(r'<meta\s+property="og:title"\s+content="([^"]+)"', html)
    return Channel(channel_id=found.group(1),
                   title=(title.group(1) if title else fallback_title).strip(),
                   handle=handle, source="handle_page")


#: Words that appear in "the X youtube channel" and identify nothing.
_NOISE = frozenset({"youtube", "channel", "official", "the", "and", "for",
                    "video", "videos", "yt"})


def plausible(query: str, channel: Channel) -> bool:
    """
    Does this channel have anything to do with what was asked for?

    Search always returns something. Without this, "zzqx not a real channel
    84719" resolved to whatever YouTube page happened to rank, and reported
    success - inventing a channel is exactly what the proof-of-work rule
    exists to stop.

    A shared word is a low bar on purpose: it is here to catch nonsense, not
    to adjudicate between similar names. Deciding which of two real channels
    is meant is search ranking's job, and it is better at it than a rule.
    """
    tokens = {word for word in re.split(r"[^a-z0-9]+", (query or "").lower())
              if len(word) > 2 and word not in _NOISE}
    if not tokens:
        return False
    haystack = f"{channel.title} {channel.handle}".lower().replace(" ", "")
    return any(token in haystack for token in tokens)


async def channel_from_search(client: httpx.AsyncClient, name: str) -> Channel | None:
    """
    Ask the web which channel someone means, then read that page for its id.

    Search ranking encodes the thing a name lookup cannot: which of several
    similarly-named channels is the one people mean. It is also already a
    verified capability here, and costs no YouTube quota at all.
    """
    from friday import contracts as ct
    from friday.toolsets.web import web_search

    probe = ct.Run.create(f"resolve youtube channel {name}", capability="youtube")
    found = await web_search(probe, f"{name} youtube channel", limit=6)
    if found.status != ct.SUCCEEDED:
        return None

    for hit in (found.output or {}).get("results", []):
        url = str(hit.get("url", ""))
        direct = re.search(r"youtube\.com/channel/(UC[\w-]{20,})", url)
        if direct:
            candidate = Channel(channel_id=direct.group(1),
                                title=str(hit.get("title", name)).strip(),
                                source="web_search")
            if plausible(name, candidate):
                return candidate
            continue
        page = re.search(r"youtube\.com/(@[\w.-]+)", url)
        if page:
            try:
                response = await client.get(HANDLE_URL.format(handle=page.group(1)))
            except Exception:
                continue
            if response.status_code == 200:
                channel = _channel_from_page(response.text, name,
                                             handle=page.group(1))
                if channel is not None:
                    candidate = Channel(channel_id=channel.channel_id,
                                        title=channel.title,
                                        handle=channel.handle,
                                        source="web_search")
                    if plausible(name, candidate):
                        return candidate
    return None


async def api_search_channel(client: httpx.AsyncClient, query: str) -> Channel | None:
    """
    `search.list`, the expensive last resort. 100 units, and its own daily cap.
    """
    key = api_key()
    if not key:
        return None
    try:
        response = await client.get(f"{API_BASE}/search", params={
            "part": "snippet", "type": "channel", "q": query,
            "maxResults": 1, "key": key})
    except Exception:
        return None
    if response.status_code != 200:
        return None
    items = response.json().get("items") or []
    if not items:
        return None
    snippet = items[0]["snippet"]
    return Channel(channel_id=items[0]["id"]["channelId"],
                   title=snippet.get("channelTitle", query), source="api")


async def resolve_channel(store: Store | None, query: str) -> Channel | None:
    """Cheapest route first, and remember the answer."""
    text = (query or "").strip()
    if not text:
        return None
    if CHANNEL_ID.match(text):
        return Channel(channel_id=text, source="id")

    if store is not None:
        known = remembered_channel(store, text)
        if known:
            return known

    async with _client() as client:
        # An explicit @handle is exact and free. A plain NAME goes to search,
        # because guessing a handle from a name lands on lookalikes - and a
        # lookalike is indistinguishable from success once it is answered.
        channel = await channel_from_handle(client, text)
        if channel is None:
            channel = await channel_from_search(client, text)
        if channel is None:
            channel = await api_search_channel(client, text)

    if channel and store is not None:
        remember_channel(store, text, channel)
    return channel


# ---------------------------------------------------------------------------
# Reading a channel
# ---------------------------------------------------------------------------


async def _feed(client: httpx.AsyncClient, channel_id: str) -> tuple[str, list[dict]]:
    """(channel title, recent videos) from the channel's Atom feed."""
    import defusedxml.ElementTree as ET

    response = await client.get(FEED_URL.format(channel_id=channel_id))
    if response.status_code != 200:
        return "", []
    root = ET.fromstring(response.content)

    title_el = root.find("a:title", _NS)
    videos = []
    for entry in root.findall("a:entry", _NS):
        video_id = entry.find("yt:videoId", _NS)
        name = entry.find("a:title", _NS)
        published = entry.find("a:published", _NS)
        group = entry.find("m:group", _NS)
        stats = group.find("m:community/m:statistics", _NS) if group is not None else None
        rating = group.find("m:community/m:starRating", _NS) if group is not None else None
        description = group.find("m:description", _NS) if group is not None else None
        if video_id is None:
            continue
        videos.append({
            "video_id": video_id.text,
            "title": (name.text or "").strip() if name is not None else "",
            "published_at": published.text if published is not None else "",
            "views": int(stats.get("views")) if stats is not None and stats.get("views") else None,
            "likes": int(rating.get("count")) if rating is not None and rating.get("count") else None,
            "description": ((description.text or "")[:400] if description is not None else ""),
            "url": WATCH_URL.format(video_id=video_id.text),
        })
    return (title_el.text or "") if title_el is not None else "", videos


async def _api_channel(client: httpx.AsyncClient, channel_id: str) -> dict | None:
    key = api_key()
    if not key:
        return None
    try:
        response = await client.get(f"{API_BASE}/channels", params={
            "part": "snippet,statistics,contentDetails", "id": channel_id,
            "key": key})
    except Exception:
        return None
    if response.status_code != 200:
        return None
    items = response.json().get("items") or []
    if not items:
        return None
    item = items[0]
    snippet, stats = item["snippet"], item.get("statistics", {})
    related = (item.get("contentDetails") or {}).get("relatedPlaylists") or {}
    return {
        "channel_id": item["id"],
        "title": snippet.get("title", ""),
        "description": (snippet.get("description") or "")[:1200],
        "handle": snippet.get("customUrl", ""),
        "published_at": snippet.get("publishedAt", ""),
        "country": snippet.get("country", ""),
        "subscribers": stats.get("subscriberCount"),
        "views": stats.get("viewCount"),
        "video_count": stats.get("videoCount"),
        # The cheap route to recent uploads. search.list(order=date) costs a
        # hundred times more for a worse answer.
        "uploads_playlist": related.get("uploads", ""),
    }


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


async def youtube_find_channel(
    run: c.Run, query: str, *, store: Store | None = None,
    engine: PolicyEngine = default_engine,
) -> c.ActionResult:
    """Name or handle -> the exact channel, remembered so it is asked once."""
    tool_id = "youtube.find_channel"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    if not (query or "").strip():
        return run.record(c.failed(started, "no channel named"))

    channel = await resolve_channel(store, query)
    if channel is None:
        return run.record(c.failed(
            started, f"could not find a YouTube channel for {query!r}"))

    payload = _scoped({"query": query, **channel.as_dict()})

    # A handle, an id or a remembered answer is exact. A plain name resolved
    # through web search is not: the same query returned three different
    # channels across three runs, and one of them was a real channel with one
    # video from 2010 that a review would have described in confident detail.
    #
    # So an inexact resolution is returned as a question. Without the Data
    # API's own search there is nothing here that can settle it, and a coin
    # flip presented as an answer is the failure this codebase is built to
    # avoid.
    if channel.source == "web_search":
        payload["needs_confirmation"] = True
        return run.record(c.partial(
            started,
            f"best guess for {query!r} is {channel.title!r} "
            f"({channel.url}) - confirm it is the right one, or give the "
            f"@handle. Name lookup is only exact with the YouTube Data API, "
            f"which is not enabled for this key.",
            output=payload))

    return run.record(c.succeeded(
        started, output=payload,
        verification=c.Verification(
            method="channel_resolved",
            evidence=f"{query!r} -> {channel.channel_id} "
                     f"({channel.title!r}) via {channel.source}"),
    ))


async def youtube_channel_details(
    run: c.Run, channel: str, *, store: Store | None = None,
    engine: PolicyEngine = default_engine,
) -> c.ActionResult:
    """Who the channel is and how big, without opening a browser."""
    tool_id = "youtube.channel_details"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    resolved = await resolve_channel(store, channel)
    if resolved is None:
        return run.record(c.failed(started, f"no such channel: {channel!r}"))

    async with _client() as client:
        details = await _api_channel(client, resolved.channel_id)
        if details is None:
            # Feed-only: fewer fields, and it says so rather than implying the
            # numbers are missing because the channel has none.
            title, videos = await _feed(client, resolved.channel_id)
            if not title and not videos:
                return run.record(c.failed(
                    started,
                    f"could not read {resolved.channel_id} - the Data API is "
                    f"not enabled for this key and the feed did not answer"))
            details = {
                "channel_id": resolved.channel_id,
                "title": title or resolved.title,
                "handle": resolved.handle,
                "recent_video_count": len(videos),
                "backend": "feed",
                "unavailable_without_api": [
                    "subscribers", "total views", "video count", "description"],
            }
        else:
            details["backend"] = "api"

    details["url"] = resolved.url
    return run.record(c.succeeded(
        started, output=_scoped(details),
        verification=c.Verification(
            method=f"youtube_{details['backend']}",
            evidence=f"{details.get('title')!r} ({resolved.channel_id}) via "
                     f"{details['backend']}"),
    ))


async def youtube_recent_videos(
    run: c.Run, channel: str, *, limit: int = 5, store: Store | None = None,
    engine: PolicyEngine = default_engine,
) -> c.ActionResult:
    """
    The channel's newest uploads, with real view counts.

    From the uploads feed, never from `search.list(order=date)` - same answer,
    a hundredth of the quota.
    """
    tool_id = "youtube.recent_videos"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    resolved = await resolve_channel(store, channel)
    if resolved is None:
        return run.record(c.failed(started, f"no such channel: {channel!r}"))

    limit = max(1, min(int(limit or 5), 15))
    async with _client() as client:
        title, videos = await _feed(client, resolved.channel_id)

    if not videos:
        return run.record(c.failed(
            started, f"no recent uploads found for {resolved.title or channel!r}"))

    videos = videos[:limit]
    best = max(videos, key=lambda v: v.get("views") or 0)
    return run.record(c.succeeded(
        started,
        output=_scoped({
            "channel": title or resolved.title,
            "channel_id": resolved.channel_id,
            "count": len(videos),
            "videos": videos,
            "most_viewed_recent": best["title"],
        }),
        verification=c.Verification(
            method="uploads_feed",
            evidence=f"{len(videos)} upload(s) for {resolved.channel_id}; "
                     f"newest {videos[0]['title'][:50]!r} "
                     f"({videos[0]['published_at'][:10]})"),
    ))


async def youtube_video_details(
    run: c.Run, video: str, *, engine: PolicyEngine = default_engine,
) -> c.ActionResult:
    """Everything about one video. Needs the Data API - the feed has no such view."""
    tool_id = "youtube.video_details"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    video_id = video.strip()
    match = re.search(r"(?:v=|youtu\.be/|shorts/)([\w-]{11})", video_id)
    if match:
        video_id = match.group(1)
    if not VIDEO_ID.match(video_id):
        return run.record(c.failed(started, f"{video!r} is not a video id or url"))

    key = api_key()
    if not key:
        return run.record(c.failed(
            started, "video details need the YouTube Data API; set "
                     "YOUTUBE_API_KEY, or use recent_videos which does not"))

    async with _client() as client:
        response = await client.get(f"{API_BASE}/videos", params={
            "part": "snippet,statistics,contentDetails", "id": video_id,
            "key": key})
    if response.status_code != 200:
        return run.record(c.failed(
            started, f"the Data API refused: "
                     f"{response.json().get('error', {}).get('message', '')[:200]}"))
    items = response.json().get("items") or []
    if not items:
        return run.record(c.failed(started, f"no video with id {video_id}"))

    item = items[0]
    snippet, stats = item["snippet"], item.get("statistics", {})
    return run.record(c.succeeded(
        started,
        output=_scoped({
            "video_id": video_id,
            "title": snippet.get("title", ""),
            "channel": snippet.get("channelTitle", ""),
            "channel_id": snippet.get("channelId", ""),
            "published_at": snippet.get("publishedAt", ""),
            "description": (snippet.get("description") or "")[:1500],
            "tags": (snippet.get("tags") or [])[:15],
            "duration": (item.get("contentDetails") or {}).get("duration", ""),
            "views": stats.get("viewCount"),
            "likes": stats.get("likeCount"),
            "comments": stats.get("commentCount"),
            "url": WATCH_URL.format(video_id=video_id),
        }),
        verification=c.Verification(
            method="youtube_api",
            evidence=f"videos.list returned {snippet.get('title','')[:50]!r}, "
                     f"{stats.get('viewCount','?')} views"),
    ))
