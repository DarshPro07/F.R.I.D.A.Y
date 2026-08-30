"""
Research toolset: three web modes, so the fast path stays fast.

`web_search` finds URLs. It was never meant to read them, and `web_fetch`'s
regex strip returns the nav bar, the cookie banner and the footer along with
the article. That is fine for one page and useless for six.

So the web splits by what the question actually needs:

    FAST      web_answer         one grounded model call, cited, ~3s.
                                 "who founded OpenAI", "latest Gemini model".
                                 No local browser, no scraping.
    RESEARCH  web_crawl          N URLs in parallel -> clean Markdown.
                                 Boilerplate stripped, budget enforced.
    DEEP      web_deep_research  search -> crawl -> rank -> a corpus with
                                 citations, under a hard character budget.

Nothing here starts at import. Trafilatura is a plain parser; Chromium is
launched only when a page returns too little text to be real, and is closed
again at the end of the call. The machine must not be slower because Friday
knows how to research.

## The extraction engine

`ADA_CRAWL_ENGINE` picks it:

    trafilatura   default. 16 small dependencies, no browser of its own.
    crawl4ai      opt-in: `uv pip install crawl4ai && crawl4ai-setup`.

Crawl4AI is the better-known answer and does more - adaptive crawling, LLM
extraction strategies, its own dispatcher. It also pulls 41 packages into this
environment, including a litellm fork, a second Playwright driver
(`patchright`), a scipy/shapely/trimesh geometry stack, and a cryptography
major-version bump underneath LiveKit's TLS. For a machine whose first
requirement is "must not lag", that is a large bill for the part we use: HTML
in, clean Markdown out. So it is supported, and it is not the default.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
from urllib.parse import urlparse

import httpx

from friday import contracts as c
from friday.policy import PolicyEngine, default_engine
from friday.toolsets.system import APPROVAL_PREFIX
from friday.toolsets.web import USER_AGENT, BrowserSession, _gate, web_search

EXECUTION_SCOPE = "network"

#: Below this, a page almost certainly rendered its body in JavaScript and the
#: HTML we fetched is a shell. Worth one Chromium launch; not worth guessing.
MIN_USEFUL_CHARS = 400

FETCH_TIMEOUT = 25.0
RENDER_TIMEOUT = 30.0

#: How much of one source, and of the whole corpus, may reach the model.
#: A research answer that blows the context window is not an answer.
DEFAULT_SOURCE_CHARS = 3000
DEFAULT_CORPUS_CHARS = 18000

DEFAULT_ENGINE = "trafilatura"

#: Grounded answers and URL reading both come from here.
ANSWER_MODEL = os.getenv("ADA_RESEARCH_MODEL", "gemini-2.5-flash")


def crawl_engine() -> str:
    return os.getenv("ADA_CRAWL_ENGINE", DEFAULT_ENGINE).strip().lower()


def _scoped(payload: dict) -> dict:
    return {"execution_scope": EXECUTION_SCOPE, **payload}


def _valid(url: str) -> bool:
    return urlparse(url or "").scheme in ("http", "https")


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


def to_markdown(html: str, url: str = "") -> tuple[str, str]:
    """
    (title, markdown) from raw HTML, main content only.

    Trafilatura is imported here rather than at module scope: this module is
    loaded by the MCP server at startup and most sessions never research
    anything.
    """
    if not (html or "").strip():
        return "", ""
    import trafilatura

    text = trafilatura.extract(
        html, url=url or None, output_format="markdown",
        include_links=True, include_tables=True,
        include_comments=False, favor_precision=True,
    ) or ""

    title = ""
    try:
        meta = trafilatura.extract_metadata(html)
        title = (getattr(meta, "title", "") or "").strip()
    except Exception:
        pass
    if not title:
        match = re.search(r"(?is)<title[^>]*>(.*?)</title>", html)
        title = re.sub(r"\s+", " ", match.group(1)).strip() if match else ""
    return title, text.strip()


async def _fetch(client: httpx.AsyncClient, url: str) -> tuple[str, str, int]:
    response = await client.get(url, headers={"User-Agent": USER_AGENT})
    response.raise_for_status()
    return response.text, str(response.url), response.status_code


async def _render(browser: BrowserSession, url: str) -> str:
    """Load a page in headless Chromium and return the rendered HTML."""
    page = await browser.page(headless=True)
    await page.goto(url, wait_until="domcontentloaded", timeout=RENDER_TIMEOUT * 1000)
    try:
        await page.wait_for_load_state("networkidle", timeout=5000)
    except Exception:
        pass  # a page that never goes idle is still readable
    return await page.content()


async def crawl_one(client: httpx.AsyncClient, url: str, *, max_chars: int,
                    browser: BrowserSession | None = None) -> dict:
    """One page, cleaned. Never raises: a failed source is a reported source."""
    if not _valid(url):
        return {"url": url, "ok": False, "error": "url must be http(s)"}
    from friday import sensitive_domains
    blocked = sensitive_domains.refusal(url)
    if blocked:
        return {"url": url, "ok": False, "error": blocked}

    try:
        html, final_url, status = await _fetch(client, url)
    except Exception as exc:
        return {"url": url, "ok": False,
                "error": f"{type(exc).__name__}: {str(exc)[:120]}"}
    blocked = sensitive_domains.refusal(final_url)
    if blocked:
        return {"url": final_url, "ok": False, "error": blocked}

    title, text = to_markdown(html, final_url)
    how = "http"

    if len(text) < MIN_USEFUL_CHARS and browser is not None:
        # Probably a JavaScript shell. One render, then judge again.
        try:
            rendered = await _render(browser, final_url)
            r_title, r_text = to_markdown(rendered, final_url)
            if len(r_text) > len(text):
                title, text, how = (r_title or title), r_text, "rendered"
        except Exception as exc:
            how = f"http (render failed: {type(exc).__name__})"

    if not text:
        return {"url": final_url, "ok": False, "status": status,
                "error": "no readable content extracted", "engine": how}

    return {
        "url": final_url, "ok": True, "status": status, "title": title,
        "markdown": text[:max_chars], "chars": len(text),
        "truncated": len(text) > max_chars, "engine": how,
    }


async def _crawl_with_crawl4ai(urls: list[str], max_chars: int) -> list[dict]:
    """The opt-in engine. Imported only when actually selected."""
    from crawl4ai import AsyncWebCrawler

    out: list[dict] = []
    async with AsyncWebCrawler(verbose=False) as crawler:
        results = await crawler.arun_many(urls=urls)
        for result in results:
            markdown = str(getattr(result, "markdown", "") or "")
            if not getattr(result, "success", False) or not markdown.strip():
                out.append({"url": getattr(result, "url", "?"), "ok": False,
                            "engine": "crawl4ai",
                            "error": str(getattr(result, "error_message", "")
                                         or "no content")})
                continue
            metadata = getattr(result, "metadata", None) or {}
            out.append({
                "url": result.url, "ok": True, "engine": "crawl4ai",
                "title": str(metadata.get("title", "")),
                "markdown": markdown[:max_chars], "chars": len(markdown),
                "truncated": len(markdown) > max_chars,
            })
    return out


async def crawl_all(urls: list[str], *, max_chars: int, render: bool) -> list[dict]:
    """Every URL in parallel, with the browser closed again afterwards."""
    if crawl_engine() == "crawl4ai":
        return await _crawl_with_crawl4ai(urls, max_chars)

    browser = BrowserSession() if render else None
    try:
        async with httpx.AsyncClient(follow_redirects=True,
                                     timeout=FETCH_TIMEOUT) as client:
            return list(await asyncio.gather(*(
                crawl_one(client, url, max_chars=max_chars, browser=browser)
                for url in urls
            )))
    finally:
        if browser is not None:
            await browser.close()  # never leave Chromium behind


# ---------------------------------------------------------------------------
# Relevance and deduplication
# ---------------------------------------------------------------------------


def _fingerprint(page: dict) -> str:
    body = (page.get("markdown") or "")[:500].lower()
    return hashlib.sha256(re.sub(r"\s+", " ", body).encode()).hexdigest()


def dedupe(pages: list[dict]) -> list[dict]:
    """Same URL after redirects, or the same opening, is the same source."""
    seen_urls: set[str] = set()
    seen_bodies: set[str] = set()
    out: list[dict] = []
    for page in pages:
        url = page.get("url", "")
        if url in seen_urls:
            continue
        seen_urls.add(url)
        if page.get("ok"):
            body = _fingerprint(page)
            if body in seen_bodies:
                continue
            seen_bodies.add(body)
        out.append(page)
    return out


def relevance(page: dict, question: str) -> int:
    """
    How much of the question this page actually talks about.

    Keyword counting, deliberately: it costs nothing, and a source that never
    names the subject is not a source, however highly it ranked.
    """
    terms = {t for t in re.split(r"[^a-z0-9]+", (question or "").lower())
             if len(t) > 2}
    if not terms:
        return 0
    haystack = ((page.get("title") or "") + " " + (page.get("markdown") or "")).lower()
    return sum(min(haystack.count(term), 20) for term in terms)


def build_corpus(pages: list[dict], question: str, *, budget: int) -> tuple[list[dict], list[str]]:
    """
    The sources that fit, most relevant first, and what was dropped.

    Truncation is stated, never silent: a corpus that quietly lost half its
    sources reads exactly like one that covered everything.
    """
    usable = [p for p in pages if p.get("ok")]
    usable.sort(key=lambda p: relevance(p, question), reverse=True)

    kept: list[dict] = []
    dropped: list[str] = []
    spent = 0
    for page in usable:
        body = page.get("markdown") or ""
        if spent + len(body) > budget:
            room = budget - spent
            if room < 500:
                dropped.append(page["url"])
                continue
            body = body[:room]
            page = {**page, "markdown": body, "truncated": True}
        spent += len(body)
        kept.append(page)
    return kept, dropped


# ---------------------------------------------------------------------------
# FAST: one grounded call
# ---------------------------------------------------------------------------


async def web_answer(
    run: c.Run, question: str, *, urls: str = "",
    engine: PolicyEngine = default_engine,
) -> c.ActionResult:
    """
    Answer a current-facts question with Google grounding, in one call.

    Also reads specific URLs when given, server-side - which handles
    JavaScript-rendered pages without launching anything locally.
    """
    tool_id = "web.answer"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    if not (question or "").strip():
        return run.record(c.failed(started, "empty question"))
    if not os.getenv("GOOGLE_API_KEY", "").strip():
        return run.record(c.failed(
            started, "web.answer needs GOOGLE_API_KEY; use web_search instead"))

    from google import genai
    from google.genai import types

    prompt = question
    listed = [u for u in re.split(r"[\s,]+", urls or "") if _valid(u)]
    tools = [types.Tool(google_search=types.GoogleSearch())]
    if listed:
        prompt = f"{question}\n\nUse these pages:\n" + "\n".join(listed)
        # Only when there is something to read. Offering url_context on every
        # question turned a 3s answer into a 68s one - the model went looking
        # for pages to fetch whether or not any had been named.
        tools.append(types.Tool(url_context=types.UrlContext()))

    client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))
    try:
        response = await client.aio.models.generate_content(
            model=ANSWER_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(tools=tools, temperature=0.0),
        )
    except Exception as exc:
        return run.record(c.failed(
            started, f"grounded answer failed: {type(exc).__name__}: {str(exc)[:160]}"))

    text = (response.text or "").strip()
    citations, queries = _grounding(response)

    if not text:
        return run.record(c.failed(started, "the model returned no answer"))
    if not citations and not listed:
        # An ungrounded answer is the model's memory wearing a search's
        # clothes. Say so rather than passing it off as looked-up.
        return run.record(c.partial(
            started, "answered without grounding - nothing was actually searched",
            output=_scoped({"question": question, "answer": text,
                            "citations": [], "searches": queries}),
        ))

    return run.record(c.succeeded(
        started,
        output=_scoped({"question": question, "answer": text,
                        "citations": citations, "searches": queries,
                        "urls_read": listed}),
        verification=c.Verification(
            method="gemini_grounding",
            evidence=f"{len(citations)} grounded source(s)"
                     + (f", searches: {queries}" if queries else "")
                     + (f", urls read: {listed}" if listed else ""),
        ),
    ))


def _grounding(response) -> tuple[list[dict], list[str]]:
    try:
        metadata = response.candidates[0].grounding_metadata
    except (AttributeError, IndexError):
        return [], []
    if metadata is None:
        return [], []
    citations = [
        {"title": (chunk.web.title or "").strip(), "url": chunk.web.uri}
        for chunk in (metadata.grounding_chunks or []) if chunk.web
    ]
    return citations, list(metadata.web_search_queries or [])


# ---------------------------------------------------------------------------
# RESEARCH: crawl what search found
# ---------------------------------------------------------------------------


async def web_crawl(
    run: c.Run, urls: str, *, max_chars: int = DEFAULT_SOURCE_CHARS,
    render: bool = True, engine: PolicyEngine = default_engine,
) -> c.ActionResult:
    """Read several pages properly. `urls` is whitespace- or comma-separated."""
    tool_id = "web.crawl"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    wanted = [u for u in re.split(r"[\s,]+", urls or "") if u.strip()]
    if not wanted:
        return run.record(c.failed(started, "no urls given"))
    if len(wanted) > 12:
        return run.record(c.failed(
            started, f"{len(wanted)} urls is too many for one call; 12 at most"))

    pages = dedupe(await crawl_all(wanted, max_chars=max_chars, render=render))
    good = [p for p in pages if p.get("ok")]
    failed = [p for p in pages if not p.get("ok")]

    output = _scoped({
        "requested": len(wanted), "read": len(good), "failed": len(failed),
        "engine": crawl_engine(), "pages": good,
        "unreadable": [{"url": p.get("url"), "error": p.get("error")} for p in failed],
    })

    if not good:
        return run.record(c.failed(
            started,
            "no page could be read: "
            + "; ".join(f"{p.get('url')}: {p.get('error')}" for p in failed[:4]),
        ))

    if failed:
        return run.record(c.partial(
            started, f"read {len(good)} of {len(wanted)}; "
                     f"{len(failed)} could not be read", output=output))
    return run.record(c.succeeded(
        started, output=output,
        verification=c.Verification(
            method="content_extracted",
            evidence=f"{len(good)} page(s), "
                     f"{sum(p['chars'] for p in good)} chars of main content; "
                     f"first is {good[0]['url']} ({good[0]['chars']} chars)",
        ),
    ))


# ---------------------------------------------------------------------------
# DEEP: search, crawl, rank, budget
# ---------------------------------------------------------------------------


async def web_deep_research(
    run: c.Run, question: str, *, sources: int = 5,
    max_chars_per_source: int = DEFAULT_SOURCE_CHARS,
    budget: int = DEFAULT_CORPUS_CHARS,
    engine: PolicyEngine = default_engine,
) -> c.ActionResult:
    """
    Search, read the results properly, and return a ranked, budgeted corpus.

    Returns sources - not a synthesis. The agent's own model does the
    reasoning; adding a second model here would cost a call and a hop, and
    lose the citations along the way.
    """
    tool_id = "web.deep_research"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    if not (question or "").strip():
        return run.record(c.failed(started, "empty question"))
    sources = max(1, min(int(sources or 5), 10))

    search = await web_search(run, question, limit=sources * 2, engine=engine)
    if search.status != c.SUCCEEDED:
        return run.record(c.failed(
            started, f"could not search: {search.error or 'no results'}"))

    hits = (search.output or {}).get("results", [])[:sources]
    if not hits:
        return run.record(c.failed(started, "search returned no results to read"))

    pages = dedupe(await crawl_all([h["url"] for h in hits],
                                   max_chars=max_chars_per_source, render=True))
    kept, dropped = build_corpus(pages, question, budget=budget)
    unreadable = [{"url": p.get("url"), "error": p.get("error")}
                  for p in pages if not p.get("ok")]

    output = _scoped({
        "question": question,
        "search_provider": (search.output or {}).get("provider"),
        "considered": len(hits), "read": len(kept),
        "engine": crawl_engine(),
        "corpus_chars": sum(len(p.get("markdown") or "") for p in kept),
        "sources": [
            {"url": p["url"], "title": p.get("title", ""),
             "markdown": p.get("markdown", ""),
             "relevance": relevance(p, question)}
            for p in kept
        ],
        "dropped_for_budget": dropped,
        "unreadable": unreadable,
    })

    if not kept:
        return run.record(c.failed(
            started, f"found {len(hits)} result(s) but none could be read"))

    note = None
    if dropped or unreadable:
        note = (f"read {len(kept)} source(s); "
                f"{len(dropped)} dropped for budget, "
                f"{len(unreadable)} unreadable")
        return run.record(c.partial(started, note, output=output))

    return run.record(c.succeeded(
        started, output=output,
        verification=c.Verification(
            method="searched_and_extracted",
            evidence=f"{len(kept)} source(s) via "
                     f"{(search.output or {}).get('provider')}, "
                     f"{output['corpus_chars']} chars; top source "
                     f"{kept[0]['url']}",
        ),
    ))
