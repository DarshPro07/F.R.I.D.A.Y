"""
MCP adapter for the Phase 1B web toolset.

Same thin shape as system_control: a Run per call, persisted, result
serialised. All logic lives in friday/toolsets/web.py.

`web_search` replaces the Phase 0 `search_web` stub, which returned
"[stub] Search results for: ..." - text a model relays as though a search
happened. The stub is deleted rather than kept alongside, so there is no
route back to it.
"""

from __future__ import annotations

import os

from friday import contracts as c
from friday.policy import PolicyEngine, PolicyError
from friday.store import DEFAULT_DB, Store
from friday.toolsets import research as R
from friday.toolsets import web as W

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
    run = c.Run.create(request, capability="web")
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
    async def web_search(query: str, limit: int = 8) -> dict:
        """
        Search the web and return real results with titles, URLs and snippets.

        If no provider answers, status is 'failed' - say you could not search.
        Never present a failed search as though results were found.
        """
        return await _execute(f"search: {query}", W.web_search, query, limit=limit)

    @mcp.tool()
    async def web_fetch(url: str, max_chars: int = 8000) -> dict:
        """Fetch a URL and return its readable text and title."""
        return await _execute(f"fetch {url}", W.web_fetch, url, max_chars=max_chars)

    @mcp.tool()
    async def web_answer(question: str, urls: str = "") -> dict:
        """
        Fastest way to answer anything current: one grounded search, with
        citations. Use for questions with a SHORT FACTUAL answer - "what is
        the latest X", "who founded Y", "what does Z cost now".

        NOT for research. If the boss says "research", "compare", "what are
        the approaches to", or asks anything that needs more than one source,
        this will give him a shallow paragraph. Search for a research
        capability instead and use that.

        Give `urls` (space-separated) to have specific pages read instead;
        that also handles pages that need JavaScript, without opening a
        browser here.

        Status 'partial' means the model answered without actually grounding
        it - treat that as unverified and say so.
        """
        return await _execute(f"answer: {question}", R.web_answer, question, urls=urls)

    @mcp.tool()
    async def web_crawl(urls: str, max_chars: int = 3000, render: bool = True) -> dict:
        """
        Read several web pages properly - main content as Markdown, without
        the nav bars and cookie banners that web_fetch returns. Up to 12 URLs,
        space- or comma-separated, read in parallel.

        Use after web_search when the answer needs the pages themselves rather
        than the snippets.
        """
        return await _execute(f"crawl {urls[:80]}", R.web_crawl, urls,
                              max_chars=max_chars, render=render)

    @mcp.tool()
    async def web_deep_research(question: str, sources: int = 5,
                                max_chars_per_source: int = 3000) -> dict:
        """
        Research a question properly: search, read the top results, rank them
        by relevance and return the sources with citations.

        Use for "research X", "compare A B and C", "what are the approaches
        to Y" - anything where one snippet is not an answer. Returns sources,
        not conclusions; the reasoning is yours. Cite the URLs you used.
        """
        return await _execute(f"research: {question}", R.web_deep_research, question,
                              sources=sources,
                              max_chars_per_source=max_chars_per_source)

    @mcp.tool()
    async def web_news(topic: str = "world", limit: int = 12) -> dict:
        """Current headlines from public RSS feeds. topic: 'world' or 'finance'."""
        return await _execute(f"news: {topic}", W.web_news, topic, limit=limit)

    @mcp.tool()
    async def browser_open(url: str) -> dict:
        """
        Open a URL in a real browser window on the user's computer and confirm
        the page loaded. Use when the user wants to SEE a page.
        """
        return await _execute(f"open {url}", W.browser_open, url)

    @mcp.tool()
    async def browser_navigate(url: str) -> dict:
        """Navigate the already-open browser to another URL."""
        return await _execute(f"navigate {url}", W.browser_navigate, url)

    @mcp.tool()
    async def browser_inspect(max_chars: int = 4000) -> dict:
        """Read the currently open browser page: url, title and visible text."""
        return await _execute("inspect page", W.browser_inspect, max_chars=max_chars)

    @mcp.tool()
    async def browser_close() -> dict:
        """Close the browser session. Do this when browsing work is finished."""
        return await _execute("close browser", W.browser_close)

    @mcp.tool()
    async def browser_act(primitive: str, target: str = "", args: dict | None = None) -> dict:
        """
        One browser primitive through the observe -> plan -> policy -> act ->
        observe -> verify loop: open, inspect, navigate, click, type, scroll,
        select, upload, download, tabs, screenshot, wait, verify.

        `target` names an element in words ("the Sign in button", "Search
        box") or as CSS. `args`: url (open/navigate), text/clear/enter
        (type), value (select), paths (upload), into (download), op/index/url
        (tabs: list/new/switch/close), condition/value (wait: selector/url/
        text/load), dy (scroll), expect {url|title|text: substring} to verify.

        Reads run automatically. State changes need approval. A purchase,
        publish, destructive or security-settings action on the page returns
        APPROVAL_REQUIRED even in a signed-in session; a CAPTCHA or human
        verification page returns partial with HUMAN_VERIFICATION and is never
        clicked through - hand it to the user.
        """
        return await _execute(f"browser {primitive} {target}".strip(), W.browser_act,
                              primitive, target, args)

    @mcp.tool()
    async def browser_automate(task: str, start_url: str = "https://www.google.com",
                               max_turns: int = 6) -> dict:
        """
        Drive the browser toward a goal by clicking and typing. Requires
        approval and may return APPROVAL_REQUIRED.

        If the underlying model asks for confirmation before an action, this
        stops and returns 'partial' with the question - relay it to the user
        rather than proceeding.
        """
        return await _execute(f"automate: {task}", W.browser_automate, task,
                              start_url=start_url, max_turns=max_turns)
