"""
RSCH-001 on the live research path: web_deep_research routes by depth
(R0-R5) against an immutable CanonicalInquiry, drops off-inquiry pages,
and records why it stopped. Stand-in search and fetch only; no network.
"""

from __future__ import annotations

import asyncio

import pytest

from friday import contracts as c
from friday import research_cache as RC
from friday import workspace as W
from friday.store import Store
from friday.toolsets import research as R

TRIVIAL = "Who founded OpenAI?"
BROAD = "Compare Postgres and MySQL replication"
ON_TOPIC = (
    "Postgres replication ships write ahead log records to standbys, while MySQL "
    "replication relays binary log events. Both support asynchronous and "
    "semi synchronous modes, and each has its own failover tooling. "
) * 4
OFF_TOPIC = (
    "Celebrity diets of 2026 ranked by followers. Smoothies, fasting windows and "
    "protein bars dominate the lists this season, with trainers weighing in. "
) * 4


FOUNDED = "OpenAI was founded in 2015 by a group of researchers and entrepreneurs. " * 4


def _page(url: str, text: str, title: str = "") -> dict:
    return {"url": url, "title": title, "ok": True, "markdown": text,
            "chars": len(text)}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(RC, "DB_PATH", tmp_path / "artifacts.sqlite3")
    monkeypatch.delenv(W.ENV_VAR, raising=False)
    W.reset_override()
    yield
    W.reset_override()


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "test.sqlite3")
    yield s
    s.close()


@pytest.fixture
def rig(store, monkeypatch):
    """Stand-in search over `urls` and a crawl that records what it was asked for."""
    from friday.toolsets import memory as toolset_memory

    toolset_memory.reset_store(store)
    state = {"crawled": [], "limit": None}

    def install(urls, pages=None):
        by_url = pages or {u: _page(u, ON_TOPIC) for u in urls}

        async def fake_search(run, query, *, limit=8, engine=None):
            state["limit"] = limit
            started = c.started(run.run_id, "web.search")
            return run.record(c.succeeded(started, output={
                "provider": "stub", "results": [
                    {"url": u, **({"title": by_url[u]["title"]} if by_url[u]["title"] else {})}
                    for u in urls]},
                verification=c.Verification(method="stub", evidence="stand-in search")))

        async def fake_crawl_all(requested, **_kw):
            state["crawled"].append(list(requested))
            return [by_url[u] for u in requested]

        monkeypatch.setattr(R, "web_search", fake_search)
        monkeypatch.setattr(R, "crawl_all", fake_crawl_all)
        return state

    yield install
    toolset_memory.reset_store(None)


def _wide(count: int) -> tuple[str, dict[str, dict]]:
    """A question with `count` distinct terms and `count` pages that each add one,
    so no page is barren and only a budget can stop the walk."""
    question = "Compare " + " ".join(f"topic{i}" for i in range(count))
    urls = [f"https://s{i}.example/page" for i in range(count)]
    return question, {u: _page(u, f"topic{i} notes and detail. " * 8) for i, u in enumerate(urls)}


def _run(question: str, **kw) -> c.ActionResult:
    return asyncio.run(R.web_deep_research(
        c.Run.create("test", capability="web"), question, **kw))


def test_trivial_question_reads_one_source_even_when_ten_are_asked_for(rig):
    urls = [f"https://s{i}.example/openai" for i in range(8)]
    state = rig(urls, pages={u: _page(u, FOUNDED) for u in urls})
    result = _run(TRIVIAL, sources=10, depth="R5")

    assert result.status in (c.SUCCEEDED, c.PARTIAL)
    out = result.output
    assert out["depth"] == "R1"
    assert sum(len(batch) for batch in state["crawled"]) == 1
    assert out["stop"]["depth"] == "R1"
    assert out["stop"]["reason"] == "budget_sources"
    assert out["read"] == 1


def test_broad_question_gets_its_requested_depth_and_budget(rig):
    question, pages = _wide(8)
    state = rig(list(pages), pages=pages)
    out = _run(question, depth="R3").output
    assert out["depth"] == "R3"
    assert sum(len(batch) for batch in state["crawled"]) == R.DEPTH_BUDGETS["R3"].max_sources
    assert out["stop"]["reason"] == "budget_sources"


def test_off_inquiry_page_is_rejected_and_recorded(rig):
    good, bad = "https://a.example/pg", "https://b.example/diets"
    state = rig([good, bad], pages={good: _page(good, ON_TOPIC, "Postgres replication"),
                                    bad: _page(bad, OFF_TOPIC, "Celebrity diets")})
    out = _run(BROAD, sources=5).output
    assert [s["url"] for s in out["sources"]] == [good]
    assert state["crawled"] == [[good]]   # the drifted result was never fetched
    assert out["off_inquiry"] == [{"url": bad, "title": "Celebrity diets",
                                   "reason": "off_inquiry"}]


def test_char_budget_stop_is_recorded(rig):
    urls = [f"https://s{i}.example/pg" for i in range(3)]
    rig(urls)
    out = _run(BROAD, sources=3, budget=len(ON_TOPIC) + 10).output
    assert out["stop"]["reason"] == "budget_chars"
    assert out["stop"]["chars"] >= len(ON_TOPIC)


def test_all_pages_off_inquiry_fails_instead_of_returning_a_corpus(rig):
    bad = "https://b.example/diets"
    state = rig([bad], pages={bad: _page(bad, OFF_TOPIC, "Celebrity diets")})
    result = _run(BROAD)
    assert state["crawled"] == []
    assert result.status == c.FAILED
    assert "inquiry" in (result.error or "")


# --- review repairs: R0, the depth ceiling, R1 chars, the MCP default -------

def test_r0_searches_and_crawls_nothing(rig):
    urls = [f"https://s{i}.example/postgres-replication" for i in range(3)]
    state = rig(urls)
    searched = []
    real_search = R.web_search

    async def spy(run, query, **kw):
        searched.append(query)
        return await real_search(run, query, **kw)

    import friday.toolsets.research as mod
    mod.web_search = spy
    try:
        result = _run(BROAD, depth="R0")
    finally:
        mod.web_search = real_search
    assert searched == []
    assert state["crawled"] == []
    assert result.status == c.SUCCEEDED
    out = result.output
    assert out["depth"] == "R0"
    assert out["sources"] == []
    assert out["stop"]["reason"] == "depth_r0"
    assert out["stop"]["sources"] == 0 and out["stop"]["chars"] == 0


@pytest.mark.parametrize("depth", ["R4", "R5"])
def test_deep_levels_read_their_own_budget_not_ten(rig, depth):
    question, pages = _wide(30)
    state = rig(list(pages), pages=pages)
    out = _run(question, depth=depth).output
    want = R.DEPTH_BUDGETS[depth].max_sources
    assert out["depth"] == depth
    assert sum(len(b) for b in state["crawled"]) == want
    assert out["stop"]["depth"] == depth
    assert out["stop"]["sources"] == want
    assert out["stop"]["reason"] == "budget_sources"


def test_explicit_sources_never_exceed_the_depths_budget(rig):
    urls = [f"https://s{i}.example/postgres-replication" for i in range(10)]
    state = rig(urls)
    out = _run(BROAD, depth="R2", sources=9).output
    assert out["depth"] == "R2"
    assert sum(len(b) for b in state["crawled"]) == R.DEPTH_BUDGETS["R2"].max_sources
    assert out["stop"]["sources"] == R.DEPTH_BUDGETS["R2"].max_sources


def test_r1_character_budget_is_not_widened_by_an_explicit_budget(rig):
    url = "https://s0.example/openai"
    long_page = _page(url, "OpenAI was founded in 2015 by researchers. " * 400)
    rig([url], pages={url: long_page})
    out = _run(TRIVIAL, budget=50000).output
    assert out["depth"] == "R1"
    assert out["corpus_chars"] <= R.DEPTH_BUDGETS["R1"].max_chars


def test_mcp_wrapper_leaves_sources_to_the_depth_budget(monkeypatch):
    """The wrapper must not pass a fixed sources=5 that overrides the depth."""
    from friday.tools import web_control as WC

    captured = {}

    async def fake_execute(request, fn, *args, **kwargs):
        captured.update(kwargs)
        return {}

    monkeypatch.setattr(WC, "_execute", fake_execute)

    class FakeMcp:
        tools = {}

        def tool(self):
            def deco(f):
                self.tools[f.__name__] = f
                return f
            return deco

    mcp = FakeMcp()
    WC.register(mcp)
    asyncio.run(mcp.tools["web_deep_research"](BROAD))
    assert captured["sources"] is None
    assert captured["depth"] is None
    asyncio.run(mcp.tools["web_deep_research"](BROAD, depth="R4"))
    assert captured["depth"] == "R4"


# --- RSCH-001: information-gain stop on the live path ------------------------

def test_stalled_gain_stops_the_walk_before_the_remaining_fetches(rig):
    urls = [f"https://s{i}.example/postgres-replication" for i in range(8)]
    # Distinct sources that all repeat the same three inquiry terms and add no new one.
    state = rig(urls, pages={
        u: _page(u, "Postgres MySQL replication. " + " ".join(f"filler{i}x{j}" for j in range(60)))
        for i, u in enumerate(urls)})
    out = _run(BROAD, depth="R3").output
    assert out["stop"]["reason"] == "no_information_gain"
    assert [len(b) for b in state["crawled"]] == [2, 2]   # two waves, then stop
    assert out["stop"]["not_fetched"] == R.DEPTH_BUDGETS["R3"].max_sources - 4


def test_pages_that_keep_adding_terms_never_trip_the_gain_stop(rig):
    question, pages = _wide(8)
    state = rig(list(pages), pages=pages)
    out = _run(question, depth="R3").output
    assert out["stop"]["reason"] == "budget_sources"
    assert sum(len(b) for b in state["crawled"]) == R.DEPTH_BUDGETS["R3"].max_sources
    assert out["stop"]["not_fetched"] == 0


def test_one_barren_page_is_not_a_stall(rig):
    question, pages = _wide(8)
    urls = list(pages)
    pages[urls[1]] = _page(urls[1], "nothing relevant here at all. " * 8)
    state = rig(urls, pages=pages)
    out = _run(question, depth="R3").output
    assert out["stop"]["reason"] == "budget_sources"
    assert sum(len(b) for b in state["crawled"]) == R.DEPTH_BUDGETS["R3"].max_sources


def test_gain_stop_does_not_lower_the_saved_confidence(rig, monkeypatch):
    """Pages the gain stop skipped were never attempted; a saturated walk whose
    fetched pages were all readable is stored at full confidence."""
    from friday import research_routing
    saved = []
    monkeypatch.setattr(research_routing, "route",
                        lambda artifact, **kw: saved.append(artifact))
    urls = [f"https://s{i}.example/postgres-replication" for i in range(8)]
    rig(urls, pages={
        u: _page(u, "Postgres MySQL replication. " + " ".join(f"filler{i}x{j}" for j in range(60)))
        for i, u in enumerate(urls)})
    out = _run(BROAD, depth="R3").output
    assert out["stop"]["reason"] == "no_information_gain"
    assert out["stop"]["not_fetched"] > 0
    assert len(saved) == 1
    assert saved[0].confidence == 1.0
