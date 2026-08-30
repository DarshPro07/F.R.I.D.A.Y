
"""
Web toolset (Phase 1B): search, fetch, news, and browser control.

Replaces the Phase 0 `search_web` stub, which returned
"[stub] Search results for: ..." - a string a model can and will relay as
though a search happened. Search now either returns real external results or
reports NOT_CONFIGURED. There is no third option that looks like results.

Search providers are tried in order and the one that answered is recorded in
the Verification evidence, so "where did this come from" is always answerable:

    brave      BRAVE_API_KEY   (if set)
    tavily     TAVILY_API_KEY  (if set)
    duckduckgo keyless HTML endpoint

Browser control uses Playwright. Per §26 it is **not** kept running: the
session starts on first use and is closed by `browser_close` or at process
exit. ADA V2's web_agent (MIT) is the donor for the computer-use loop, with
one deliberate change - it auto-acknowledged the model's own safety
confirmations, which is removed here.
"""

from __future__ import annotations

import asyncio

import html

import os

import re

from dataclasses import dataclass

from urllib.parse import parse_qs, unquote, urlparse

import httpx

from friday import contracts as c

from friday import sensitive_domains

from friday.policy import PolicyEngine, default_engine

from friday.toolsets.system import APPROVAL_PREFIX

EXECUTION_SCOPE = "network"

BROWSER_SCOPE = "local_machine"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

NEWS_FEEDS = {
    'world': (
        'https://feeds.bbci.co.uk/news/world/rss.xml',
        'https://rss.nytimes.com/services/xml/rss/nyt/World.xml',
        'https://www.aljazeera.com/xml/rss/all.xml',
        'https://www.cnbc.com/id/100727362/device/rss/rss.html',
    ),
    'finance': (
        'https://www.cnbc.com/id/10000664/device/rss/rss.html',
        'https://feeds.bloomberg.com/markets/news.rss',
        'https://feeds.marketwatch.com/marketwatch/topstories/',
        'https://rss.nytimes.com/services/xml/rss/nyt/Business.xml',
        'https://www.reutersagency.com/feed/?taxonomy=best-sectors&post_type=best',
    ),
}


def _gate(run: c.Run, tool_id: str, engine: PolicyEngine) -> c.ActionResult | None:
    verdict = engine.decide(tool_id)
    if verdict.allowed:
        return None
    return run.record(c.started(run.run_id, tool_id).finish(
        status=c.CANCELLED,
        error=f"{APPROVAL_PREFIX}: {verdict.reason} [{verdict.decision}]",
    ))


def _scoped(payload: dict, scope: str = EXECUTION_SCOPE) -> dict:
    return {"execution_scope": scope, **payload}


def _clean(raw: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", raw)).strip()


@dataclass(frozen=True)
class SearchHit:
    title: str
    url: str
    snippet: str

    def as_dict(self) -> dict:
        return {"title": self.title, "url": self.url, "snippet": self.snippet}


def _unwrap_ddg(href: str) -> str:
    if "uddg=" in href:
        return unquote(parse_qs(urlparse(href).query).get("uddg", [href])[0])
    if href.startswith("//"):
        return "https:" + href
    return href


async def _search_duckduckgo(query: str, limit: int) -> list[SearchHit]:
    async with httpx.AsyncClient(follow_redirects=True, timeout=20) as client:
        response = await client.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query}, headers={"User-Agent": USER_AGENT},
        )
    response.raise_for_status()
    text = response.text

    anchors = re.findall(
        r'<a\b[^>]*class="[^"]*result__a[^"]*"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
        text, re.S,
    )
    snippets = re.findall(
        r'class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>', text, re.S
    )

    hits: list[SearchHit] = []
    for i, (href, title) in enumerate(anchors[:limit]):
        hits.append(SearchHit(
            title=_clean(title),
            url=_unwrap_ddg(href),
            snippet=_clean(snippets[i]) if i < len(snippets) else "",
        ))
    return hits


async def _search_brave(query: str, limit: int) -> list[SearchHit]:
    key = os.getenv("BRAVE_API_KEY", "").strip()
    if not key:
        raise RuntimeError("BRAVE_API_KEY not set")
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": limit},
            headers={"Accept": "application/json", "X-Subscription-Token": key},
        )
    response.raise_for_status()
    return [
        SearchHit(r.get("title", ""), r.get("url", ""),
                  _clean(r.get("description", "")))
        for r in response.json().get("web", {}).get("results", [])[:limit]
    ]


async def _search_tavily(query: str, limit: int) -> list[SearchHit]:
    key = os.getenv("TAVILY_API_KEY", "").strip()
    if not key:
        raise RuntimeError("TAVILY_API_KEY not set")
    async with httpx.AsyncClient(timeout=25) as client:
        response = await client.post(
            "https://api.tavily.com/search",
            json={"api_key": key, "query": query, "max_results": limit},
        )
    response.raise_for_status()
    return [
        SearchHit(r.get("title", ""), r.get("url", ""), _clean(r.get("content", "")))
        for r in response.json().get("results", [])[:limit]
    ]


async def _search_duckduckgo_lite(query: str, limit: int) -> list[SearchHit]:
    """
    A second DuckDuckGo endpoint with different markup.

    The HTML endpoint intermittently answers 200 with a page containing no
    parseable results - anti-bot behaviour, not an outage, and indistinguishable
    from success at the HTTP layer. Observed live: several 200 OKs in the
    server log while the agent reported "search providers are unresponsive".
    Lite is a genuinely different response shape, so it usually still answers.
    """
    async with httpx.AsyncClient(follow_redirects=True, timeout=20) as client:
        response = await client.post(
            "https://lite.duckduckgo.com/lite/",
            data={"q": query}, headers={"User-Agent": USER_AGENT},
        )
    response.raise_for_status()

    hits: list[SearchHit] = []
    seen: set[str] = set()
    for href, title in re.findall(
        r'<a[^>]+href="(https?://[^"]+|//duckduckgo\.com/l/\?[^"]+)"[^>]*>(.*?)</a>',
        response.text, re.S,
    ):
        url = _unwrap_ddg(href)
        text = _clean(title)
        if not text or "duckduckgo.com" in url or url in seen:
            continue
        seen.add(url)
        hits.append(SearchHit(title=text, url=url, snippet=""))
        if len(hits) >= limit:
            break
    return hits

SEARCH_PROVIDERS = (
    ("brave", _search_brave),
    ("tavily", _search_tavily),
    ("duckduckgo", _search_duckduckgo),
    ("duckduckgo_lite", _search_duckduckgo_lite),
)


async def web_search(
    run: c.Run, query: str, *, limit: int = 8,
    engine: PolicyEngine = default_engine,
) -> c.ActionResult:
    """Real web search. Never returns anything results-shaped without results."""
    tool_id = "web.search"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    if not (query or "").strip():
        return run.record(c.failed(started, "empty query"))

    attempts: list[str] = []
    for name, provider in SEARCH_PROVIDERS:
        try:
            hits = await provider(query, limit)
        except Exception as exc:
            attempts.append(f"{name}: {type(exc).__name__}: {str(exc)[:80]}")
            continue
        if not hits:
            attempts.append(f"{name}: returned 0 results")
            continue
        return run.record(c.succeeded(
            started,
            output=_scoped({"query": query, "provider": name, "count": len(hits),
                            "results": [h.as_dict() for h in hits]}),
            verification=c.Verification(
                method=f"search_provider:{name}",
                evidence=f"{len(hits)} result(s) from {name}; first is "
                         f"{hits[0].url}",
            ),
        ))

    return run.record(c.failed(
        started,
        "no search provider returned results. Tried: " + "; ".join(attempts),
    ))


async def web_fetch(
    run: c.Run, url: str, *, max_chars: int = 8000,
    engine: PolicyEngine = default_engine,
) -> c.ActionResult:
    """Fetch a URL and return readable text."""
    tool_id = "web.fetch"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    parsed = urlparse(url or "")
    if parsed.scheme not in ("http", "https"):
        return run.record(c.failed(started, f"url must be http(s), got {url!r}"))
    blocked_reason = sensitive_domains.refusal(url)
    if blocked_reason:
        return run.record(c.failed(started, blocked_reason))

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=25) as client:
            response = await client.get(url, headers={"User-Agent": USER_AGENT})
        response.raise_for_status()
    except Exception as exc:
        return run.record(c.failed(started, f"fetch failed: {type(exc).__name__}: {exc}"))
    # Where it landed, not just where it was sent: a redirect can end up
    # on a site the request was never allowed to reach.
    blocked_reason = sensitive_domains.refusal(str(response.url))
    if blocked_reason:
        return run.record(c.failed(started, blocked_reason))

    body = response.text
    stripped = re.sub(r"(?is)<(script|style|noscript)\b.*?</\1>", " ", body)
    text = re.sub(r"\s+", " ", _clean(stripped)).strip()
    title_match = re.search(r"(?is)<title[^>]*>(.*?)</title>", body)
    title = _clean(title_match.group(1)) if title_match else ""

    if not text:
        return run.record(c.partial(
            started, "fetched but no readable text extracted",
            output=_scoped({"url": str(response.url), "status": response.status_code,
                            "bytes": len(body)}),
        ))

    return run.record(c.succeeded(
        started,
        output=_scoped({"url": str(response.url), "final_url": str(response.url),
                        "status": response.status_code, "title": title,
                        "text": text[:max_chars], "truncated": len(text) > max_chars,
                        "chars": len(text)}),
        verification=c.Verification(
            method="http_get",
            evidence=f"HTTP {response.status_code} from {response.url}, "
                     f"{len(text)} chars of text, title={title[:60]!r}",
        ),
    ))

#: Subdomains that carry no publisher identity. Without stripping these,
#: feeds.bbci.co.uk labels every BBC story "FEEDS".
_FEED_SUBDOMAINS = frozenset({"www", "feeds", "feed", "rss", "news", "api", "static"})


def _source_label(hostname: str) -> str:
    """feeds.bbci.co.uk -> BBCI, rss.nytimes.com -> NYTIMES."""
    labels = [p for p in (hostname or "").lower().split(".") if p]
    meaningful = [p for p in labels if p not in _FEED_SUBDOMAINS]
    return (meaningful[0] if meaningful else (labels[0] if labels else "?")).upper()


async def _fetch_feed(client: httpx.AsyncClient, url: str) -> list[dict]:
    # defusedxml, not xml.etree: RSS is attacker-influenceable input and the
    # stdlib parser is vulnerable to entity-expansion ("billion laughs") and
    # external-entity attacks. A hijacked or MITM'd feed should not be able to
    # exhaust memory in the MCP server.
    import defusedxml.ElementTree as ET

    try:
        # Connect fast, read patiently. One unreachable feed used to set the
        # latency of the whole briefing - the NYT feed does not resolve from
        # this network, and an 8s connect wait on it made a 1s answer take 8.
        # gather() only returns when the slowest one does, so the slowest one
        # has to be cheap.
        response = await client.get(
            url, headers={"User-Agent": USER_AGENT},
            timeout=httpx.Timeout(connect=3.0, read=6.0, write=6.0, pool=6.0))
        if response.status_code != 200:
            return []
        root = ET.fromstring(response.content)
    except Exception:
        return []

    source = _source_label(urlparse(url).hostname or url)
    items = []
    for item in root.findall(".//item")[:5]:
        description = item.findtext("description") or ""
        items.append({
            "source": source,
            "title": (item.findtext("title") or "").strip(),
            "summary": _clean(description)[:220],
            "link": (item.findtext("link") or "").strip(),
        })
    return items


async def web_news(
    run: c.Run, topic: str = "world", *, limit: int = 12,
    engine: PolicyEngine = default_engine,
) -> c.ActionResult:
    tool_id = "web.news"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    feeds = NEWS_FEEDS.get(topic)
    if feeds is None:
        return run.record(c.failed(
            started, f"unknown topic {topic!r}; known: {sorted(NEWS_FEEDS)}"
        ))

    async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
        batches = await asyncio.gather(*[_fetch_feed(client, u) for u in feeds])
    articles = [a for batch in batches for a in batch][:limit]

    if not articles:
        return run.record(c.failed(
            started, f"no {topic} feed responded ({len(feeds)} tried)"
        ))

    live = len([b for b in batches if b])
    return run.record(c.succeeded(
        started,
        output=_scoped({"topic": topic, "count": len(articles), "articles": articles}),
        verification=c.Verification(
            method="rss_fetch",
            evidence=f"{len(articles)} article(s) from {live}/{len(feeds)} feeds; "
                     f"first: {articles[0]['title'][:60]!r}",
        ),
    ))


def _headless() -> bool:
    return os.getenv("ADA_BROWSER_HEADLESS", "false").strip().lower() in ("1", "true", "yes")


class BrowserSession:
    """One Playwright browser, started on demand."""

    def __init__(self) -> None:
        self._pw = None
        self._browser = None
        self._page = None
        self._loop = None

    @property
    def running(self) -> bool:
        return self._page is not None

    def _abandon(self) -> None:
        """
        Drop a session belonging to a dead event loop.

        Playwright handles are bound to the loop that created them. Awaiting
        close() on the old loop raises "Event loop is closed"; the transports
        are already gone with it, so the references are simply released. The
        MCP server uses one loop and never hits this, but a stale handle
        otherwise surfaces as a confusing PARTIAL instead of a clean restart.
        """
        self._pw = self._browser = self._page = self._loop = None

    async def page(self, *, headless: bool | None = None):
        loop = asyncio.get_running_loop()
        if self._page is not None:
            if self._loop is loop:
                return self._page
            self._abandon()

        from playwright.async_api import async_playwright

        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(
            headless=_headless() if headless is None else headless
        )
        context = await self._browser.new_context(
            viewport={"width": 1440, "height": 900}, user_agent=USER_AGENT
        )
        self._page = await context.new_page()
        self._loop = loop
        return self._page

    async def close(self) -> bool:
        was_running = self.running
        for closer in (self._browser, self._pw):
            if closer is None:
                continue
            try:
                await (closer.close() if closer is self._browser else closer.stop())
            except Exception:
                pass
        self._abandon()
        return was_running

session = BrowserSession()


async def _page_state(page) -> dict:
    try:
        title = await page.title()
    except Exception:
        title = ""
    return {"url": page.url, "title": title}


async def browser_open(
    run: c.Run, url: str, *, engine: PolicyEngine = default_engine,
    headless: bool | None = None, timeout_ms: int = 30000,
) -> c.ActionResult:
    """Open a URL in a real browser and verify the page loaded."""
    tool_id = "browser.open"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    if not urlparse(url or "").scheme:
        url = f"https://{url}"
    if urlparse(url).scheme not in ("http", "https"):
        return run.record(c.failed(started, f"url must be http(s), got {url!r}"))
    blocked_reason = sensitive_domains.refusal(url)
    if blocked_reason:
        return run.record(c.failed(started, blocked_reason))

    try:
        page = await session.page(headless=headless)
        response = await page.goto(url, timeout=timeout_ms)
    except Exception as exc:
        return run.record(c.failed(started, f"browser could not open {url!r}: {exc}"))

    state = await _page_state(page)
    status = response.status if response is not None else None
    blocked_reason = sensitive_domains.refusal(state.get("url", ""))
    if blocked_reason:
        return run.record(c.failed(started, blocked_reason))

    if status is not None and status >= 400:
        return run.record(c.partial(
            started, f"page returned HTTP {status}",
            output=_scoped(state | {"status": status}, BROWSER_SCOPE),
        ))

    return run.record(c.succeeded(
        started,
        output=_scoped(state | {"status": status}, BROWSER_SCOPE),
        side_effects=("browser session opened",),
        verification=c.Verification(
            method="page_loaded",
            evidence=f"browser at {state['url']} (HTTP {status}), "
                     f"title={state['title'][:60]!r}",
        ),
    ))


async def browser_navigate(
    run: c.Run, url: str, *, engine: PolicyEngine = default_engine,
    timeout_ms: int = 30000,
) -> c.ActionResult:
    """Navigate the existing session. Fails if no browser is open."""
    tool_id = "browser.navigate"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    if not session.running:
        return run.record(c.failed(
            started, "no browser session is open - use browser.open first"
        ))
    return await browser_open(run, url, engine=engine, timeout_ms=timeout_ms)


async def browser_inspect(
    run: c.Run, *, max_chars: int = 4000, engine: PolicyEngine = default_engine
) -> c.ActionResult:
    """Read the current page: url, title and visible text."""
    tool_id = "browser.inspect"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    if not session.running:
        return run.record(c.failed(started, "no browser session is open"))

    page = await session.page()
    state = await _page_state(page)
    blocked_reason = sensitive_domains.refusal(state.get("url", ""))
    if blocked_reason:
        return run.record(c.failed(started, blocked_reason))
    try:
        text = re.sub(r"\s+", " ", await page.inner_text("body")).strip()
    except Exception as exc:
        return run.record(c.partial(
            started, f"page open but body text unreadable: {exc}",
            output=_scoped(state, BROWSER_SCOPE),
        ))

    return run.record(c.succeeded(
        started,
        output=_scoped(state | {"text": text[:max_chars], "chars": len(text)},
                       BROWSER_SCOPE),
        verification=c.Verification(
            method="page_read",
            evidence=f"read {len(text)} chars from {state['url']}, "
                     f"title={state['title'][:60]!r}",
        ),
    ))

COMPUTER_USE_MODEL = os.getenv(
    "ADA_COMPUTER_USE_MODEL", "gemini-2.5-computer-use-preview-10-2025"
)


def _denormalize(value: int, span: int) -> int:
    """Computer-use models emit 0-1000 coordinates; scale to the viewport."""
    return int((value / 1000) * span)


def pending_confirmation(calls) -> tuple[str, str] | None:
    """
    Find a model action that asked for human confirmation first.

    Separated out so the rule is directly testable. ADA V2 found these and
    auto-acknowledged them:

        print("   -> Auto-acknowledging to proceed.")
        requires_acknowledgement = True

    which turns the model's own safety brake into a speed bump. Returning the
    action here makes the caller stop instead.
    """
    for call in calls or ():
        args = dict(getattr(call, "args", None) or {})
        decision = args.get("safety_decision") or {}
        if isinstance(decision, dict) and decision.get("decision") == "require_confirmation":
            return (
                getattr(call, "name", "?"),
                decision.get("explanation", "no explanation given"),
            )
    return None


async def _apply_action(page, name: str, args: dict, width: int, height: int) -> dict:
    """Execute one computer-use action. Returns {} or {'error': ...}."""
    try:
        if name == "open_web_browser":
            pass
        elif name == "navigate":
            await page.goto(args["url"])
        elif name == "go_back":
            await page.go_back()
        elif name == "go_forward":
            await page.go_forward()
        elif name == "search":
            await page.goto("https://www.google.com")
        elif name == "wait_5_seconds":
            await asyncio.sleep(5)
        elif name == "click_at":
            await page.mouse.click(_denormalize(args["x"], width),
                                   _denormalize(args["y"], height))
        elif name == "hover_at":
            await page.mouse.move(_denormalize(args["x"], width),
                                  _denormalize(args["y"], height))
        elif name == "type_text_at":
            await page.mouse.click(_denormalize(args["x"], width),
                                   _denormalize(args["y"], height))
            if args.get("clear_before_typing", True):
                await page.keyboard.press("Control+A")
                await page.keyboard.press("Backspace")
            await page.keyboard.type(args["text"])
            if args.get("press_enter"):
                await page.keyboard.press("Enter")
        elif name == "key_combination":
            await page.keyboard.press(args.get("keys", ""))
        elif name in ("scroll_document", "scroll_at"):
            magnitude = args.get("magnitude", 800)
            direction = args.get("direction", "down")
            if name == "scroll_at":
                await page.mouse.move(_denormalize(args["x"], width),
                                      _denormalize(args["y"], height))
            delta = {"down": (0, magnitude), "up": (0, -magnitude),
                     "right": (magnitude, 0), "left": (-magnitude, 0)}
            await page.mouse.wheel(*delta.get(direction, (0, magnitude)))
        else:
            return {"error": f"unimplemented action {name!r}"}
        await asyncio.sleep(0.8)
        return {}
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


async def browser_automate(
    run: c.Run, task: str, *, start_url: str = "https://www.google.com",
    max_turns: int = 12, engine: PolicyEngine = default_engine,
    headless: bool | None = None,
) -> c.ActionResult:
    """
    Drive the browser toward a goal using a computer-use model.

    ADA V2's web_agent is the donor (MIT). One behaviour is deliberately NOT
    carried over: it detected the model's own `require_confirmation` safety
    decisions and auto-acknowledged them to keep going

        print("   -> Auto-acknowledging to proceed.")

    which defeats the guard the model raised. Here that halts the run and
    returns PARTIAL with the question, so a human answers it.
    """
    tool_id = "browser.automate"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    if not (task or "").strip():
        return run.record(c.failed(started, "empty task"))
    blocked_reason = sensitive_domains.refusal(start_url)
    if blocked_reason:
        return run.record(c.failed(started, blocked_reason))
    if not os.getenv("GOOGLE_API_KEY", "").strip():
        return run.record(c.failed(
            started, "browser automation needs GOOGLE_API_KEY (computer-use model)"
        ))

    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:
        return run.record(c.failed(started, f"google-genai unavailable: {exc}"))

    width, height = 1440, 900
    try:
        page = await session.page(headless=headless)
        await page.goto(start_url, timeout=30000)
        client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))
        config = types.GenerateContentConfig(
            tools=[types.Tool(computer_use=types.ComputerUse(
                environment=types.Environment.ENVIRONMENT_BROWSER
            ))]
        )
        shot = await page.screenshot(type="png")
        history = [types.Content(role="user", parts=[
            types.Part(text=task),
            types.Part.from_bytes(data=shot, mime_type="image/png"),
        ])]
    except Exception as exc:
        return run.record(c.failed(started, f"automation setup failed: {exc}"))

    actions_taken: list[str] = []
    final_text = ""

    for turn in range(max_turns):
        try:
            response = await client.aio.models.generate_content(
                model=COMPUTER_USE_MODEL, contents=history, config=config
            )
        except Exception as exc:
            state = await _page_state(page)
            return run.record(c.partial(
                started, f"model call failed on turn {turn + 1}: {exc}",
                output=_scoped(state | {"actions_taken": actions_taken}, BROWSER_SCOPE),
            ))

        if not response.candidates:
            break
        content = response.candidates[0].content
        history.append(content)

        calls = []
        for part in (content.parts or []):
            if getattr(part, "text", None) and not getattr(part, "thought", False):
                final_text = part.text
            if getattr(part, "function_call", None):
                calls.append(part.function_call)

        if not calls:
            break  # model is done talking, no more actions

        # Safety gate: the model asked for confirmation. Stop and ask a human.
        confirmation = pending_confirmation(calls)
        if confirmation is not None:
            action, explanation = confirmation
            state = await _page_state(page)
            return run.record(c.partial(
                started,
                f"{APPROVAL_PREFIX}: the model requested confirmation before "
                f"'{action}': {explanation}",
                output=_scoped(state | {"actions_taken": actions_taken,
                                        "pending_action": action}, BROWSER_SCOPE),
            ))

        results = []
        for call in calls:
            outcome = await _apply_action(page, call.name, dict(call.args or {}),
                                          width, height)
            actions_taken.append(call.name)
            results.append((getattr(call, "id", None), call.name, outcome))

        # The model may have navigated somewhere the request was never
        # allowed to reach; that ends the run rather than the next turn.
        blocked_reason = sensitive_domains.refusal(page.url)
        if blocked_reason:
            return run.record(c.partial(
                started, blocked_reason,
                output=_scoped({"url": page.url, "actions_taken": actions_taken},
                               BROWSER_SCOPE)))

        try:
            shot = await page.screenshot(type="png")
        except Exception as exc:
            return run.record(c.partial(
                started, f"could not screenshot after actions: {exc}",
                output=_scoped(await _page_state(page) | {"actions_taken": actions_taken},
                               BROWSER_SCOPE),
            ))

        history.append(types.Content(role="user", parts=[
            types.Part(function_response=types.FunctionResponse(
                name=name, id=call_id, response={"url": page.url, **outcome},
                parts=[types.FunctionResponsePart(inline_data=types.FunctionResponseBlob(
                    mime_type="image/png", data=shot))],
            ))
            for call_id, name, outcome in results
        ]))

    state = await _page_state(page)
    if not actions_taken:
        return run.record(c.partial(
            started, "the model took no browser actions",
            output=_scoped(state | {"model_said": final_text}, BROWSER_SCOPE),
        ))

    return run.record(c.succeeded(
        started,
        output=_scoped(state | {"actions_taken": actions_taken,
                                "turns": len(actions_taken),
                                "model_said": final_text}, BROWSER_SCOPE),
        side_effects=(f"performed {len(actions_taken)} browser action(s)",),
        verification=c.Verification(
            method="browser_actions_applied",
            evidence=f"{len(actions_taken)} action(s) ({', '.join(actions_taken[:5])}); "
                     f"final page {state['url']} title={state['title'][:50]!r}",
        ),
    ))


async def browser_close(
    run: c.Run, *, engine: PolicyEngine = default_engine
) -> c.ActionResult:
    """Close the browser. Playwright must not be left running (§26)."""
    tool_id = "browser.close"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    was_running = await session.close()
    if not was_running:
        return run.record(c.succeeded(
            started, output=_scoped({"was_running": False}, BROWSER_SCOPE),
            verification=c.Verification(
                method="session_state", evidence="no browser session was open"
            ),
        ))
    return run.record(c.succeeded(
        started, output=_scoped({"was_running": True}, BROWSER_SCOPE),
        side_effects=("browser session closed",),
        verification=c.Verification(
            method="session_closed",
            evidence="browser and playwright driver stopped; session.running is False",
        ),
    ))


"""
The World Monitor dashboard: a built view, an opened browser, and an honest
`partial` until the browser itself can be observed.
"""

"""
The address is what can be verified; the browser is not watched yet.
"""


def world_monitor_open(run: c.Run, focus: str = '', time_range: str = '', lat: float | None = None, lon: float | None = None, *, engine: PolicyEngine = default_engine) -> c.ActionResult:
    """
    Open the global intelligence dashboard, at the view that was asked for.

    The previous implementation opened `https://worldmonitor.app/` - the
    marketing landing page - and returned "Displaying the World Monitor on
    your primary screen now, sir." It said that because `webbrowser.open`
    returned, which reports that a browser was launched and nothing about
    where it went.

    `focus` narrows the layers ("weather", "conflicts", "economic",
    "nuclear"); `time_range` is one of 24h/7d/30d/90d; `lat`/`lon` move the
    viewport off the global default. Everything unset keeps the boss's own
    preset: the whole world, the last week, twelve layers.
    """
    from friday import worldmonitor as WM

    tool_id = "world_monitor.open"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    try:
        view = WM.WorldMonitorView()
        if time_range.strip():
            view = view.over(time_range.strip())
        if lat is not None and lon is not None:
            view = view.at(float(lat), float(lon))
        if focus.strip():
            view = view.focused_on(focus)
    except ValueError as exc:
        return run.record(c.failed(started, str(exc)))

    url = view.url()

    try:
        import webbrowser

        opened = webbrowser.open(url)
    except Exception as exc:                                 # noqa: BLE001
        return run.record(c.failed(started, f"could not open a browser: {exc}"))

    if not opened:
        return run.record(c.failed(
            started, "no browser accepted the dashboard address"))

    # The one thing that can be verified from here is the address itself:
    # that it is the dashboard, at the view that was asked for, and not the
    # landing page. What the browser did with it is the Browser Companion's
    # to observe, and until it can, the result says so rather than claiming
    # a screen nobody looked at.
    correct, why = WM.shows(url, view)
    if not correct:
        return run.record(c.failed(
            started, f"built the wrong address: {why}"))

    return run.record(c.partial(
        started,
        "the dashboard address was handed to the browser; whether it finished "
        "loading is not observable from here yet",
        output=_scoped({
            "url": url, "view": view.view, "time_range": view.time_range,
            "layers": list(view.layers), "lat": view.lat, "lon": view.lon,
            "browser_state_observed": False,
        })))


async def get_world_news(run: c.Run, *, limit: int = 12, engine: PolicyEngine = default_engine) -> c.ActionResult:
    """The world headlines. `web_news` under the name the registry uses."""
    return await web_news(run, "world", limit=limit, engine=engine)


async def get_world_finance_news(run: c.Run, *, limit: int = 12, engine: PolicyEngine = default_engine) -> c.ActionResult:
    """Markets and economics. `web_news` under the name the registry uses."""
    return await web_news(run, "finance", limit=limit, engine=engine)


"""
Finance is the same dashboard focused on the economic layers.
"""

"""
One view builder, two entry points - the landing-page bug came from having two URLs.
"""


def open_finance_world_monitor(run: c.Run, time_range: str = '', lat: float | None = None, lon: float | None = None, *, engine: PolicyEngine = default_engine) -> c.ActionResult:
    """
    The World Monitor showing the economic layers.

    "Finance" is not a separate dashboard, it is the same one focused on
    sanctions, waterways, outages and economics - which is what
    `WorldMonitorView.showing("economic")` already builds. Opening a second
    hard-coded URL for it was how the landing-page bug got in twice.
    """
    return world_monitor_open(run, focus="economic", time_range=time_range,
                              lat=lat, lon=lon, engine=engine)


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# News
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Browser session - lazily started, never left running by default
# ---------------------------------------------------------------------------
