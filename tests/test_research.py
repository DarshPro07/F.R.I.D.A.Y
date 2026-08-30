"""
Three web modes, so the fast path stays fast.

`web_fetch` strips tags with a regex, which returns the nav bar, the cookie
banner and the footer alongside the article. Acceptable for one page; useless
for six, which is what research actually needs.

Nothing here touches the network. The live proof is scripts/golden_research.py.
"""

from __future__ import annotations

import asyncio

import pytest

from friday import contracts as c
from friday.toolsets import research as R

PAGE = """<html><head><title>The Reactor Log</title></head><body>
<nav><a href="/">Home</a><a href="/about">About</a></nav>
<div class="cookie-banner">We use cookies. Accept all cookies to continue.</div>
<article><h1>Arc reactor maintenance</h1>
<p>The palladium core degrades under sustained load, and the symptoms show up in
the blood before they show up in the telemetry. Swapping the core is a two hour
job if the housing is clean and a two day job if it is not.</p>
<p>Keep a spare housing machined and ready. The failure mode nobody plans for is
the one where the reactor is fine and the socket is not.</p></article>
<footer>Copyright Stark Industries. All rights reserved.</footer></body></html>"""


def run_for(request: str = "test") -> c.Run:
    return c.Run.create(request, capability="web")


# ---------------------------------------------------------------------------
# Extraction: the reason this module exists
# ---------------------------------------------------------------------------


def test_extraction_keeps_the_article_and_drops_the_furniture():
    title, markdown = R.to_markdown(PAGE, "https://example.com/log")
    assert "palladium core" in markdown
    assert "About" not in markdown, "navigation survived"
    assert "cookies" not in markdown, "the cookie banner survived"
    assert "Copyright" not in markdown, "the footer survived"
    assert title


def test_extraction_returns_markdown_not_a_wall_of_text():
    _, markdown = R.to_markdown(PAGE, "https://example.com/log")
    assert markdown.startswith("#")


def test_extraction_on_nothing():
    assert R.to_markdown("") == ("", "")
    assert R.to_markdown("   ") == ("", "")


def test_extraction_falls_back_to_the_title_tag():
    html = "<html><head><title>  Only   a title </title></head><body>x</body></html>"
    title, _ = R.to_markdown(html)
    assert title == "Only a title"


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


def page(url, body="the same opening paragraph about arc reactors", ok=True):
    return {"url": url, "ok": ok, "markdown": body, "title": "t", "chars": len(body)}


def test_the_same_url_after_redirects_is_one_source():
    assert len(R.dedupe([page("https://a.com/x"), page("https://a.com/x")])) == 1


def test_a_mirror_of_the_same_article_is_one_source():
    """Syndicated copy on another domain: different URL, identical opening."""
    pages = R.dedupe([page("https://a.com/x"), page("https://mirror.b.com/x")])
    assert len(pages) == 1


def test_different_articles_both_survive():
    pages = R.dedupe([page("https://a.com/x", "one thing entirely"),
                      page("https://b.com/y", "a completely different thing")])
    assert len(pages) == 2


def test_failures_are_kept_so_they_can_be_reported():
    pages = R.dedupe([page("https://a.com/x"),
                      {"url": "https://b.com/y", "ok": False, "error": "timeout"}])
    assert len(pages) == 2


# ---------------------------------------------------------------------------
# Relevance
# ---------------------------------------------------------------------------


def test_relevance_prefers_the_page_that_talks_about_the_subject():
    on_topic = page("https://a", "agent memory, agent memory, long term memory")
    off_topic = page("https://b", "an article about kitchen appliances")
    question = "long term agent memory"
    assert R.relevance(on_topic, question) > R.relevance(off_topic, question)


def test_relevance_ignores_short_noise_words():
    assert R.relevance(page("https://a", "of to in a an"), "of to in") == 0


def test_relevance_of_an_empty_question_is_zero():
    assert R.relevance(page("https://a"), "") == 0


def test_one_word_repeated_cannot_dominate_the_ranking():
    """A page saying 'memory' 500 times is spam, not the best source."""
    spam = page("https://a", "memory " * 500)
    real = page("https://b", "memory architecture for long term agent recall " * 5)
    assert R.relevance(real, "long term agent memory architecture") > \
        R.relevance(spam, "long term agent memory architecture")


# ---------------------------------------------------------------------------
# The budget
# ---------------------------------------------------------------------------


def test_the_corpus_stays_inside_its_budget():
    pages = [page(f"https://a{i}", "x" * 4000) for i in range(10)]
    kept, dropped = R.build_corpus(pages, "x", budget=9000)
    assert sum(len(p["markdown"]) for p in kept) <= 9000
    assert dropped, "sources were dropped but not reported"


def test_dropping_for_budget_is_stated_never_silent():
    """
    A corpus that quietly lost half its sources reads exactly like one that
    covered everything.
    """
    pages = [page(f"https://a{i}", "y" * 5000) for i in range(4)]
    kept, dropped = R.build_corpus(pages, "y", budget=5000)
    assert len(kept) + len(dropped) == 4


def test_the_most_relevant_source_survives_the_budget():
    best = page("https://best", "agent memory architecture " * 50)
    filler = [page(f"https://f{i}", "unrelated filler text " * 200) for i in range(5)]
    kept, _ = R.build_corpus(filler + [best], "agent memory architecture",
                             budget=3000)
    assert kept[0]["url"] == "https://best"


def test_unreadable_pages_never_enter_the_corpus():
    kept, _ = R.build_corpus(
        [{"url": "https://a", "ok": False, "error": "timeout"}], "anything",
        budget=9000)
    assert kept == []


# ---------------------------------------------------------------------------
# Guards, without touching the network
# ---------------------------------------------------------------------------


def test_crawling_nothing_fails_rather_than_succeeding_emptily():
    result = asyncio.run(R.web_crawl(run_for(), "   "))
    assert result.status == c.FAILED
    assert "no urls" in (result.error or "")


def test_too_many_urls_is_refused_before_any_request_is_made():
    urls = " ".join(f"https://example.com/{i}" for i in range(20))
    result = asyncio.run(R.web_crawl(run_for(), urls))
    assert result.status == c.FAILED
    assert "too many" in (result.error or "")


def test_researching_nothing_fails():
    result = asyncio.run(R.web_deep_research(run_for(), ""))
    assert result.status == c.FAILED


def test_answering_nothing_fails():
    result = asyncio.run(R.web_answer(run_for(), ""))
    assert result.status == c.FAILED


def test_a_bad_url_is_reported_not_raised():
    async def go():
        import httpx

        async with httpx.AsyncClient() as client:
            return await R.crawl_one(client, "ftp://example.com/x", max_chars=100)

    page_result = asyncio.run(go())
    assert page_result["ok"] is False
    assert "http(s)" in page_result["error"]


# ---------------------------------------------------------------------------
# The engine choice
# ---------------------------------------------------------------------------


def test_the_default_engine_needs_no_extra_install(monkeypatch):
    monkeypatch.delenv("ADA_CRAWL_ENGINE", raising=False)
    assert R.crawl_engine() == "trafilatura"


def test_crawl4ai_is_opt_in(monkeypatch):
    monkeypatch.setenv("ADA_CRAWL_ENGINE", "  Crawl4AI  ")
    assert R.crawl_engine() == "crawl4ai"


def test_crawl4ai_is_never_imported_unless_chosen():
    """
    41 packages, a litellm fork and a second browser driver. It must not be a
    module-level import that everyone pays for.
    """
    source = (R.__file__ and open(R.__file__, encoding="utf-8").read()) or ""
    header = source.split("async def _crawl_with_crawl4ai")[0]
    assert "import crawl4ai" not in header
    assert "from crawl4ai" not in header


def test_trafilatura_is_imported_lazily_too():
    source = open(R.__file__, encoding="utf-8").read()
    header = source.split("def to_markdown")[0]
    assert "import trafilatura" not in header


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tool_id", ["web.answer", "web.crawl", "web.deep_research"])
def test_every_new_tool_has_a_policy_category(tool_id):
    from friday.policy import DEFAULT_POLICY, TOOL_CATEGORIES

    assert tool_id in TOOL_CATEGORIES
    assert TOOL_CATEGORIES[tool_id] in DEFAULT_POLICY


@pytest.mark.parametrize("name", ["web_answer", "web_crawl", "web_deep_research"])
def test_every_new_tool_is_declared_and_reachable(name):
    from friday import capability_router as CR
    from friday.capabilities import CAPABILITIES

    assert name in CAPABILITIES, f"{name} is not declared"
    assert name in CR.CORE_TOOLS or CR.group_of(name), f"{name} is unreachable"


def test_the_fast_path_is_always_visible():
    """
    web_answer is core: the cheapest way to be right about anything current is
    one call, and making the model search for it first defeats the point.
    """
    from friday import capability_router as CR

    assert "web_answer" in CR.CORE_TOOLS


def test_deep_research_is_not_core():
    """It reads whole pages. It should be asked for, not offered every turn."""
    from friday import capability_router as CR

    assert "web_deep_research" not in CR.CORE_TOOLS
    assert CR.group_of("web_deep_research") == "research"


@pytest.mark.parametrize("query, expected", [
    ("research a topic properly", "web_deep_research"),
    ("read these pages", "web_crawl"),
])
def test_plain_words_find_the_research_tools(query, expected):
    from mcp.server.fastmcp import FastMCP

    from friday import capability_router as CR
    from friday.tools import register_all_tools

    server = FastMCP(name="test")
    register_all_tools(server)
    tools = asyncio.run(server.list_tools())

    class SchemaTool:
        def __init__(self, tool):
            self.info = type("I", (), {
                "name": tool.name,
                "raw_schema": {"name": tool.name,
                               "description": tool.description or "",
                               "parameters": tool.inputSchema},
            })()

    router = CR.Router()
    router.load([SchemaTool(t) for t in tools])
    found = [hit["capability"] for hit in router.search(query, limit=3)]
    assert expected in found, f"{query!r} returned {found}"
