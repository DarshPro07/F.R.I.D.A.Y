"""
Phase 1B: web search, fetch, news, browser control.

Most tests are offline. The few that touch the network are marked `live` and
skipped with -m "not live".
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from friday import contracts as c
from friday import policy as p
from friday.toolsets import system as S
from friday.toolsets import web as W

live = pytest.mark.live


@pytest.fixture
def run():
    return c.Run.create("test", capability="web")


@pytest.fixture
def engine():
    """Guarded explicitly; the default is now full autonomy."""
    return p.PolicyEngine(autonomy=p.GUARDED)


def run_async(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# The donor bug: auto-acknowledging the model's own safety confirmation
# ---------------------------------------------------------------------------


@dataclass
class FakeCall:
    name: str
    args: dict


def test_confirmation_request_halts_instead_of_being_acknowledged():
    """
    ADA V2 detected require_confirmation and printed
    "-> Auto-acknowledging to proceed." That must not survive the port.
    """
    calls = [
        FakeCall("click_at", {"x": 1, "y": 2}),
        FakeCall("click_at", {"safety_decision": {
            "decision": "require_confirmation",
            "explanation": "This will submit a purchase.",
        }}),
    ]
    found = W.pending_confirmation(calls)
    assert found is not None
    action, explanation = found
    assert action == "click_at"
    assert "purchase" in explanation


def test_ordinary_actions_do_not_trigger_the_gate():
    assert W.pending_confirmation([FakeCall("click_at", {"x": 1, "y": 2})]) is None
    assert W.pending_confirmation([]) is None
    assert W.pending_confirmation(None) is None


def test_non_confirmation_safety_decisions_pass_through():
    calls = [FakeCall("navigate", {"safety_decision": {"decision": "allow"}})]
    assert W.pending_confirmation(calls) is None


def test_browser_automation_is_ask_gated(engine, run):
    assert engine.decide("browser.automate").decision == p.ASK
    result = run_async(W.browser_automate(run, "buy something", engine=engine))
    assert result.status == c.CANCELLED
    assert S.needs_approval(result)
    assert not result.may_claim_completion


def test_browser_control_is_auto_but_automation_is_not(engine):
    for tool in ("browser.open", "browser.navigate", "browser.inspect", "browser.close"):
        assert engine.decide(tool).decision == p.AUTO
    assert engine.decide("browser.automate").decision == p.ASK


# ---------------------------------------------------------------------------
# Search: never results-shaped without results
# ---------------------------------------------------------------------------


def test_search_failure_is_failed_not_empty_results(run, monkeypatch):
    async def boom(query, limit):
        raise RuntimeError("provider down")

    monkeypatch.setattr(W, "SEARCH_PROVIDERS", (("fake", boom),))
    result = run_async(W.web_search(run, "anything"))
    assert result.status == c.FAILED
    assert not result.may_claim_completion
    assert "provider down" in result.error
    # Nothing that a model could relay as results.
    assert result.output is None


def test_search_falls_through_to_the_next_provider(run, monkeypatch):
    async def broken(query, limit):
        raise RuntimeError("nope")

    async def working(query, limit):
        return [W.SearchHit("Title", "https://example.com", "snippet")]

    monkeypatch.setattr(W, "SEARCH_PROVIDERS", (("broken", broken), ("working", working)))
    result = run_async(W.web_search(run, "anything"))
    assert result.status == c.SUCCEEDED
    assert result.output["provider"] == "working"
    assert result.verification.method == "search_provider:working"


def test_provider_returning_zero_results_is_not_success(run, monkeypatch):
    async def empty(query, limit):
        return []

    monkeypatch.setattr(W, "SEARCH_PROVIDERS", (("empty", empty),))
    result = run_async(W.web_search(run, "anything"))
    assert result.status == c.FAILED
    assert "0 results" in result.error


def test_empty_query_rejected(run):
    assert run_async(W.web_search(run, "   ")).status == c.FAILED


def test_search_evidence_names_the_provider_and_first_url(run, monkeypatch):
    async def working(query, limit):
        return [W.SearchHit("T", "https://verified.example/page", "s")]

    monkeypatch.setattr(W, "SEARCH_PROVIDERS", (("prov", working),))
    result = run_async(W.web_search(run, "q"))
    assert "prov" in result.verification.evidence
    assert "verified.example" in result.verification.evidence


# ---------------------------------------------------------------------------
# Fetch and news
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("url", ["ftp://example.com/x", "file:///etc/passwd", "javascript:alert(1)", ""])
def test_fetch_rejects_non_http_schemes(run, url):
    result = run_async(W.web_fetch(run, url))
    assert result.status == c.FAILED
    assert "http(s)" in result.error


def test_news_rejects_unknown_topic(run):
    result = run_async(W.web_news(run, "astrology"))
    assert result.status == c.FAILED
    assert "unknown topic" in result.error


@pytest.mark.parametrize("hostname, expected", [
    ("feeds.bbci.co.uk", "BBCI"),
    ("rss.nytimes.com", "NYTIMES"),
    ("www.cnbc.com", "CNBC"),
    ("feeds.bloomberg.com", "BLOOMBERG"),
    ("www.aljazeera.com", "ALJAZEERA"),
    ("", "?"),
])
def test_source_label_strips_meaningless_subdomains(hostname, expected):
    """Without this, every BBC story was labelled 'FEEDS'."""
    assert W._source_label(hostname) == expected


def test_ddg_redirect_urls_are_unwrapped():
    wrapped = "//duckduckgo.com/l/?uddg=https%3A%2F%2Fgithub.com%2Flivekit%2Fagents&rut=x"
    assert W._unwrap_ddg(wrapped) == "https://github.com/livekit/agents"
    assert W._unwrap_ddg("https://plain.example/x") == "https://plain.example/x"
    assert W._unwrap_ddg("//example.com/x") == "https://example.com/x"


def test_html_is_stripped_and_unescaped():
    assert W._clean("<b>Hello</b> &amp; goodbye") == "Hello & goodbye"


# ---------------------------------------------------------------------------
# Browser session lifecycle (§26 - not left running)
# ---------------------------------------------------------------------------


def test_navigate_without_session_fails_clearly(run):
    assert not W.session.running
    result = run_async(W.browser_navigate(run, "https://example.com"))
    assert result.status == c.FAILED
    assert "browser.open first" in result.error


def test_inspect_without_session_fails(run):
    assert not W.session.running
    assert run_async(W.browser_inspect(run)).status == c.FAILED


def test_close_with_no_session_is_honest_not_a_lie(run):
    result = run_async(W.browser_close(run))
    assert result.status == c.SUCCEEDED
    assert result.output["was_running"] is False
    assert "no browser session was open" in result.verification.evidence


def test_browser_rejects_non_http_url(run):
    result = run_async(W.browser_open(run, "ftp://example.com"))
    assert result.status == c.FAILED
    assert not result.may_claim_completion


def test_bare_hostname_is_upgraded_to_https(run, monkeypatch):
    seen = {}

    class FakePage:
        url = "https://example.com/"

        async def goto(self, url, timeout=None):
            seen["url"] = url
            return type("R", (), {"status": 200})()

        async def title(self):
            return "Example"

    async def fake_page(headless=None):
        return FakePage()

    monkeypatch.setattr(W.session, "page", fake_page)
    result = run_async(W.browser_open(run, "example.com"))
    assert seen["url"] == "https://example.com"
    assert result.status == c.SUCCEEDED


def test_http_error_page_is_partial_not_succeeded(run, monkeypatch):
    class FakePage:
        url = "https://example.com/missing"

        async def goto(self, url, timeout=None):
            return type("R", (), {"status": 404})()

        async def title(self):
            return "Not Found"

    async def fake_page(headless=None):
        return FakePage()

    monkeypatch.setattr(W.session, "page", fake_page)
    result = run_async(W.browser_open(run, "https://example.com/missing"))
    assert result.status == c.PARTIAL
    assert not result.may_claim_completion
    assert "404" in result.error


# ---------------------------------------------------------------------------
# Live network (skip with:  -m "not live")
# ---------------------------------------------------------------------------


@live
def test_live_search_returns_real_external_sources(run):
    """§27 golden journey, as a test."""
    result = run_async(W.web_search(run, "livekit agents python", limit=5))
    assert result.status == c.SUCCEEDED
    hits = result.output["results"]
    assert hits, "no results"
    assert all(h["url"].startswith("http") for h in hits)
    assert any("livekit" in h["url"].lower() for h in hits)


@live
def test_live_fetch_returns_real_page_text(run):
    result = run_async(W.web_fetch(run, "https://example.com"))
    assert result.status == c.SUCCEEDED
    assert "Example Domain" in result.output["text"]
    assert result.output["status"] == 200


@live
def test_live_browser_opens_and_closes(run):
    """
    One event loop for the whole journey, as the MCP server has. Playwright
    handles are loop-bound; see BrowserSession._abandon.
    """

    async def journey():
        try:
            opened = await W.browser_open(run, "https://example.com", headless=True)
            assert opened.status == c.SUCCEEDED
            assert opened.output["title"] == "Example Domain"
            inspected = await W.browser_inspect(run)
            assert "Example Domain" in inspected.output["text"]
        finally:
            await W.browser_close(run)

    run_async(journey())
    assert W.session.running is False


@live
# Abandoning a loop-bound Playwright handle is deliberate here, so the GC's
# "unclosed transport" complaint about the dead loop is expected noise.
@pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning")
def test_session_rebuilds_rather_than_failing_on_a_new_event_loop(run):
    """
    A stale handle from a closed loop must produce a clean restart, not a
    confusing PARTIAL. This is what the first version of the test above hit.
    """
    first = run_async(W.browser_open(run, "https://example.com", headless=True))
    assert first.status == c.SUCCEEDED

    # New asyncio.run -> new loop -> the old handle is dead.
    second = run_async(W.browser_open(run, "https://example.com", headless=True))
    assert second.status == c.SUCCEEDED, second.error
    assert second.output["title"] == "Example Domain"

    run_async(W.browser_close(run))
    assert W.session.running is False
