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
import dataclasses
import hashlib
import os
import re
from urllib.parse import urlparse

import httpx

from friday import contracts as c
from friday.policy import PolicyEngine, default_engine
from friday.toolsets import artifacts
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


def _refusal(url: str, net: dict | None = None) -> str:
    """
    Why FRIDAY will not fetch this URL, or "" if it will.

    sensitive_domains knows authenticated/financial hosts; it does not know
    where a name RESOLVES. Research crawls URLs chosen by a search engine or
    by page content, so without netguard a crafted result can point the
    crawler at cloud metadata, loopback, or the LAN. `net` holds a durable
    run to its envelope's network scope as well (NET-001; netguard.run_scope).
    Every engine asks this, before the fetch and of the page it landed on.
    """
    from friday import netguard
    from friday import sensitive_domains
    blocked = sensitive_domains.refusal(url)
    if blocked:
        return blocked
    try:
        netguard.check(url, **(net or {}))
    except netguard.UrlRefused as exc:
        return str(exc)
    return ""


async def crawl_one(client: httpx.AsyncClient, url: str, *, max_chars: int,
                    browser: BrowserSession | None = None,
                    net: dict | None = None) -> dict:
    """One page, cleaned. Never raises: a failed source is a reported source."""
    if not _valid(url):
        return {"url": url, "ok": False, "error": "url must be http(s)"}
    from friday import breaker
    refused = _refusal(url, net)
    if refused:
        return {"url": url, "ok": False, "error": refused}
    # Research crawls many URLs at once; one dead host must not spend the
    # timeout budget over and over while the boss waits.
    try:
        breaker.allow(url)
    except breaker.CircuitOpen as exc:
        return {"url": url, "ok": False, "error": str(exc), "skipped": True}

    try:
        html, final_url, status = await _fetch(client, url)
    except Exception as exc:
        if breaker.is_transport_failure(exc):
            breaker.record_failure(url)
        else:
            breaker.record_success(url)
        return {"url": url, "ok": False,
                "error": f"{type(exc).__name__}: {str(exc)[:120]}"}
    breaker.record_success(url)
    # Redirects are the interesting case: a public URL is allowed to answer
    # with a 302 to 169.254.169.254, so the landing URL must be re-checked.
    refused = _refusal(final_url, net)
    if refused:
        return {"url": final_url, "ok": False, "error": refused}

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


async def _crawl_with_crawl4ai(urls: list[str], max_chars: int,
                               net: dict | None = None) -> list[dict]:
    """
    The opt-in engine. Imported only when actually selected.

    It is held to the same guard as the default engine (V7-D14): a URL is
    refused before crawl4ai sees it, and a page that landed somewhere refused
    is dropped rather than read. The one residual is a redirect inside the
    engine's own browser, which is only seen after it has landed.
    """
    out: list[dict] = []
    allowed = []
    for url in urls:
        refused = _refusal(url, net)
        if refused:
            out.append({"url": url, "ok": False, "engine": "crawl4ai",
                        "error": refused})
        else:
            allowed.append(url)
    if not allowed:
        return out
    from crawl4ai import AsyncWebCrawler

    async with AsyncWebCrawler(verbose=False) as crawler:
        results = await crawler.arun_many(urls=allowed)
        for result in results:
            landed = str(getattr(result, "url", "") or "")
            refused = _refusal(landed, net) if landed else ""
            if refused:
                out.append({"url": landed, "ok": False, "engine": "crawl4ai",
                            "error": refused})
                continue
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


async def crawl_all(urls: list[str], *, max_chars: int, render: bool,
                    net: dict | None = None) -> list[dict]:
    """Every URL in parallel, with the browser closed again afterwards.
    `net` holds a durable run to its network scope (netguard.run_scope)."""
    if crawl_engine() == "crawl4ai":
        return await _crawl_with_crawl4ai(urls, max_chars, net=net)

    browser = BrowserSession() if render else None
    try:
        async with httpx.AsyncClient(follow_redirects=True,
                                     timeout=FETCH_TIMEOUT) as client:
            return list(await asyncio.gather(*(
                crawl_one(client, url, max_chars=max_chars, browser=browser, net=net)
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


def collapse_copies(pages: list[dict]) -> list[dict]:
    """
    One page per independent source (RSCH-003), the copies listed under it.

    Near-duplicate pages are clustered by `research_provenance`; the cluster's
    primary stands for the source and carries `copies`, the other URLs, so five
    reposts of one article reach the result as one source, not five.
    Unreadable pages pass through untouched.
    """
    from friday import research_provenance

    primaries = {c["primary"]: [u for u in c["members"] if u != c["primary"]]
                 for c in research_provenance.analyze(pages)["clusters"]}
    members = {u for copies in primaries.values() for u in copies}
    out: list[dict] = []
    seen: set[str] = set()
    for page in pages:
        url = page.get("url", "")
        if page.get("ok"):
            if url in seen or (url in members and url not in primaries):
                continue
            seen.add(url)
            if primaries.get(url):
                page = {**page, "copies": primaries[url]}
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
# RSCH-001: depth routing, the canonical inquiry, the drift guard, budgets
# ---------------------------------------------------------------------------

DEPTHS = ("R0", "R1", "R2", "R3", "R4", "R5")


@dataclasses.dataclass(frozen=True)
class DepthBudget:
    max_sources: int
    max_chars: int


# R0 answers from what is already known; R1 is one source for one fact.
DEPTH_BUDGETS: dict[str, DepthBudget] = {
    "R0": DepthBudget(0, 0),
    "R1": DepthBudget(1, 3000),
    "R2": DepthBudget(3, 9000),
    "R3": DepthBudget(6, DEFAULT_CORPUS_CHARS),
    "R4": DepthBudget(12, 36000),
    "R5": DepthBudget(24, 72000),
}

# Consecutive pages that add no inquiry term before the search stops.
GAIN_PATIENCE = 2

_FACT_OPENERS = ("who", "what", "when", "where", "which", "how many", "how much",
                 "how old", "how long", "is", "are", "was", "were", "does", "did")
_DEPTH_MARKERS = ("compare", "versus", " vs ", "trade-off", "tradeoff", "pros and cons",
                  "analyze", "analyse", "in depth", "in-depth", "comprehensive",
                  "literature", "state of the art", "survey", "systematic", "report",
                  "evaluate", "benchmark", "landscape", "exhaustive", "why")
_FACT_MAX_WORDS = 12
_INSTRUCTION_WORDS = frozenset(
    "the and for with from that this what who when where which how are was were does did "
    "compare analyze analyse write report about into your their there than then them "
    "comprehensive depth".split())


def _terms(text: str) -> frozenset[str]:
    return frozenset(t for t in re.split(r"[^a-z0-9]+", (text or "").lower())
                     if len(t) > 2 and t not in _INSTRUCTION_WORDS)


@dataclasses.dataclass(frozen=True)
class CanonicalInquiry:
    """The question as asked. Frozen: a branch may not rewrite what is being asked."""

    question: str
    terms: frozenset[str]

    @classmethod
    def from_question(cls, question: str) -> "CanonicalInquiry":
        question = (question or "").strip()
        return cls(question=question, terms=_terms(question))

    def on_inquiry(self, text: str) -> bool:
        """Drift guard: a branch must share a third of the inquiry's terms (at least one)."""
        if not self.terms:
            return False
        need = max(1, -(-len(self.terms) // 3))
        return len(self.terms & _terms(text)) >= need


def route_depth(question: str, requested: str | None = None) -> str:
    """
    The depth a question deserves, R0-R5.

    A one-fact question is capped at R1 whatever was requested; for anything
    else a valid request is honoured.
    """
    text = (question or "").strip().lower()
    if not text:
        return "R0"
    words = text.split()
    padded = f" {text} "
    markers = sum(1 for m in _DEPTH_MARKERS if m in padded)
    is_fact = (len(words) <= _FACT_MAX_WORDS and markers == 0
               and text.startswith(_FACT_OPENERS))
    if is_fact:
        return "R1"
    if requested in DEPTHS:
        return requested
    score = markers + (1 if len(words) > 40 else 0)
    if score == 0:
        return "R2"
    return DEPTHS[min(2 + score, 5)]


def filter_branches(inquiry: CanonicalInquiry, branches: list[dict]) -> tuple[list[dict], list[dict]]:
    """(kept, rejected): rejected branches carry reason="off_inquiry"."""
    kept: list[dict] = []
    rejected: list[dict] = []
    for branch in branches:
        text = f"{branch.get('title', '')} {branch.get('snippet', '')} {branch.get('url', '')}"
        if inquiry.on_inquiry(text):
            kept.append(branch)
        else:
            rejected.append({**branch, "reason": "off_inquiry"})
    return kept, rejected


def _barren_tail(inquiry: CanonicalInquiry, pages: list[dict]) -> int:
    """
    How many independent sources at the end of `pages` added no inquiry term.

    Mirrors of one article are one source (RSCH-003), so reposts are neither a
    stall nor a gain: five copies in a row must not hide a distinct source
    that has not been read yet.
    """
    covered: set[str] = set()
    barren = 0
    for page in collapse_copies(pages):
        fresh = (inquiry.terms & _terms(f"{page.get('title', '')} {page.get('markdown', '')}")) - covered
        covered |= fresh
        barren = 0 if fresh else barren + 1
    return barren


def explore(inquiry: CanonicalInquiry, depth: str, branches: list[dict], fetch, *,
            budget: DepthBudget | None = None, patience: int | None = GAIN_PATIENCE,
            guard: bool = True) -> dict:
    """
    Walk on-inquiry branches under the depth's budget, recording why it stopped.

    `fetch(branch)` returns a page dict (see `dedupe`). Stop reasons:
    budget_sources, budget_chars, no_information_gain (`patience` pages in a
    row covered no new inquiry term; None turns this stop off), or exhausted
    (nothing left to read). `budget` overrides the depth's own budget;
    `guard=False` skips the drift guard for branches the caller already screened.
    """
    budget = budget or DEPTH_BUDGETS[depth]
    kept, rejected = filter_branches(inquiry, branches) if guard else (list(branches), [])
    pages: list[dict] = []
    failed: list[str] = []
    covered: set[str] = set()
    spent = 0
    barren = 0
    reason = "exhausted"
    for index, branch in enumerate(kept):
        if len(pages) >= budget.max_sources:
            reason = "budget_sources"
            break
        page = fetch(branch)
        if not page.get("ok"):
            failed.append(branch.get("url", ""))
            continue
        pages.append(page)
        spent += len(page.get("markdown") or "")
        fresh = (inquiry.terms & _terms(f"{page.get('title', '')} {page.get('markdown', '')}")) - covered
        covered |= fresh
        barren = 0 if fresh else barren + 1
        more = index + 1 < len(kept)
        if spent >= budget.max_chars:
            reason = "budget_chars" if more else "exhausted"
            break
        if more and patience is not None and barren >= patience:
            reason = "no_information_gain"
            break
    # "sources" is what was fetched (what the budget counts); copies of one
    # article are one independent source (RSCH-003).
    stop = {"reason": reason, "depth": depth, "sources": len(pages), "chars": spent}
    pages = collapse_copies(pages)
    stop["independent_sources"] = len(pages)
    return {"pages": pages, "rejected": rejected, "failed": failed, "stop": stop}


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


def _offloaded(body: str, url: str) -> dict:
    """The `markdown` keys for one source: a preview and a path when the body
    is large, the body itself when it is small.

    Ranking, deduplication and the corpus budget all run on the full text
    before this point, and `research_cache` saves the full text after it, so
    offloading changes only what the next prompt has to carry.
    """
    return artifacts.offload(body or "", label=url or "source",
                             suffix=".md").fields("markdown")


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

    from friday import netguard
    pages = dedupe(await crawl_all(wanted, max_chars=max_chars, render=render,
                                   net=netguard.run_scope(run)))
    good = [p for p in pages if p.get("ok")]
    failed = [p for p in pages if not p.get("ok")]

    output = _scoped({
        "requested": len(wanted), "read": len(good), "failed": len(failed),
        "engine": crawl_engine(),
        "pages": [{**p, **_offloaded(p.get("markdown"), p.get("url", ""))}
                  for p in good],
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
    run: c.Run, question: str, *, sources: int | None = None,
    max_chars_per_source: int = DEFAULT_SOURCE_CHARS,
    budget: int | None = None, depth: str | None = None,
    engine: PolicyEngine = default_engine,
) -> c.ActionResult:
    """
    Search, read the results properly, and return a ranked, budgeted corpus.

    RSCH-001: the depth (R0-R5) comes from `route_depth`, so a one-fact
    question stays at R1 whatever `depth` asks for. The depth's budget is the
    default for `sources` and `budget` and also their ceiling: an explicit
    value may narrow a depth's budget, never widen it (R1 included). R0 reads
    nothing: it returns before any search or fetch. The result records the
    depth, the stop reason and any page that drifted off the inquiry.

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
    inquiry = CanonicalInquiry.from_question(question)
    depth = route_depth(question, depth)
    depth_budget = DEPTH_BUDGETS[depth]
    if depth == "R0":
        # R0 answers from what is already known: no search, no fetch.
        return run.record(c.succeeded(
            started,
            output=_scoped({
                "question": question, "depth": "R0",
                "stop": {"depth": "R0", "reason": "depth_r0", "sources": 0,
                         "chars": 0, "independent_sources": 0},
                "off_inquiry": [], "considered": 0, "read": 0,
                "independent_sources": 0, "corpus_chars": 0, "sources": [],
                "dropped_for_budget": [], "unreadable": [],
            }),
            verification=c.Verification(
                method="depth_r0",
                evidence="routed to R0: answered from what is known, nothing searched or fetched",
            ),
        ))
    # The depth's budget is the default and the ceiling.
    sources = (depth_budget.max_sources if sources is None
               else max(1, min(int(sources), depth_budget.max_sources)))
    budget = (depth_budget.max_chars if budget is None
              else max(1, min(int(budget), depth_budget.max_chars)))

    from friday import research_cache
    cached = research_cache.lookup(question)
    if cached is not None:
        return run.record(c.succeeded(
            started,
            output=_scoped({
                "question": question, "search_provider": "cache",
                "considered": len(cached.sources), "read": len(cached.sources),
                "engine": crawl_engine(),
                "corpus_chars": sum(len(f) for f in cached.facts),
                "sources": [
                    {"url": s.url, "title": s.title, "chars": len(fact),
                     **_offloaded(fact, s.url)}
                    for s, fact in zip(cached.sources, cached.facts)
                ],
                "dropped_for_budget": [], "unreadable": [],
                "cached": True, "freshness": cached.freshness,
                "fetched_at": cached.fetched_at.isoformat(),
            }),
            verification=c.Verification(
                method="research_cache_hit",
                evidence=f"served from cache, freshness={cached.freshness}, "
                         f"fetched {cached.fetched_at.isoformat()}, "
                         f"{len(cached.sources)} source(s)",
            ),
        ))

    search = await web_search(run, question, limit=sources * 2, engine=engine)
    if search.status != c.SUCCEEDED:
        return run.record(c.failed(
            started, f"could not search: {search.error or 'no results'}"))

    found = (search.output or {}).get("results", [])
    # Drift guard on what the search said about each result, before anything is
    # fetched. A result with no title or snippet says nothing to judge, so it
    # is read rather than assumed off the inquiry.
    _, drifted = filter_branches(inquiry, [h for h in found if h.get("title") or h.get("snippet")])
    drifted_urls = {d.get("url") for d in drifted}
    on_inquiry = [h for h in found if h.get("url") not in drifted_urls]
    hits = on_inquiry[:sources]
    if not hits and drifted:
        return run.record(c.failed(
            started, f"found {len(found)} result(s) but none stayed on the inquiry"))
    if not hits:
        return run.record(c.failed(started, "search returned no results to read"))

    from friday import netguard
    # Fetch in waves of GAIN_PATIENCE pages: the smallest batch that can show
    # a stall, so the information-gain stop saves the fetches it skips.
    # Pages already read in a wave are kept (they were paid for).
    crawled: list[dict] = []
    gain_stopped = False
    for start in range(0, len(hits), GAIN_PATIENCE):
        crawled.extend(await crawl_all(
            [h["url"] for h in hits[start:start + GAIN_PATIENCE]],
            max_chars=max_chars_per_source, render=True,
            net=netguard.run_scope(run)))
        if (start + GAIN_PATIENCE < len(hits)
                and _barren_tail(inquiry, [p for p in crawled if p.get("ok")]) >= GAIN_PATIENCE):
            gain_stopped = True
            break
    unreadable = [{"url": p.get("url"), "error": p.get("error")}
                  for p in crawled if not p.get("ok")]
    # The source and char budgets apply to what was read; the drift guard has
    # already screened the results (guard=False); the gain stop was applied
    # between waves above, so explore() does not repeat it (patience=None).
    readable = {p["url"]: p for p in crawled if p.get("ok")}
    walked = explore(inquiry, depth, [{"url": u} for u in readable],
                     lambda b: readable[b["url"]],
                     budget=DepthBudget(sources, budget), patience=None, guard=False)
    stop = walked["stop"]
    if stop["reason"] == "exhausted":
        if gain_stopped:
            stop["reason"] = "no_information_gain"
        elif len(on_inquiry) > len(hits):
            stop["reason"] = "budget_sources"
    stop["not_fetched"] = len(hits) - len(crawled)
    off_inquiry = [{"url": d.get("url"), "title": d.get("title", ""), "reason": d["reason"]}
                   for d in drifted]
    pages = dedupe(walked["pages"])
    kept, dropped = build_corpus(pages, question, budget=budget)

    output = _scoped({
        "question": question, "depth": depth, "stop": stop,
        "off_inquiry": off_inquiry,
        "search_provider": (search.output or {}).get("provider"),
        "considered": len(hits), "read": len(kept),
        "independent_sources": len(kept),
        "engine": crawl_engine(),
        "corpus_chars": sum(len(p.get("markdown") or "") for p in kept),
        "sources": [
            {"url": p["url"], "title": p.get("title", ""),
             "chars": len(p.get("markdown") or ""),
             "relevance": relevance(p, question),
             "copies": p.get("copies", []),
             **_offloaded(p.get("markdown"), p["url"])}
            for p in kept
        ],
        "dropped_for_budget": dropped,
        "unreadable": unreadable,
    })

    if not kept:
        return run.record(c.failed(
            started, f"found {len(hits)} result(s) but none could be read"))

    # Saved for every completed research pass, partial or not: a corpus that
    # lost half its sources to the budget is still real research, and the
    # owner's direction is that every web search gets saved so the same
    # question is not researched twice. There is no synthesis to cache here -
    # this call hands the corpus to the agent's own model rather than
    # synthesising it (see the docstring) - so only the source-backed facts
    # are kept; a future call that does synthesise can save one too.
    # route() saves to the research store itself, then sends the run's other
    # outputs to their owners (RSCH-004): ledger claims, a Project Brain
    # pointer, and only the small facts through the memory promotion gate.
    from friday import research_routing
    from friday.toolsets.memory import store as memory_store
    research_routing.route(research_cache.Artifact.create(
        question,
        sources=[{"url": p["url"], "title": p.get("title", "")} for p in kept],
        facts=[p.get("markdown", "") for p in kept],
        confidence=len(kept) / max(len(crawled), 1),
    ), store=memory_store())

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
