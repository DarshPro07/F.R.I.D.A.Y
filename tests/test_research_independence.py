"""
RSCH-003 on the live research path: five copied articles count as one
independent source. Stand-in search and fetch only; no network.
"""

from __future__ import annotations

import asyncio

import pytest

from friday import contracts as c
from friday import research_cache as RC
from friday import workspace as W
from friday.store import Store
from friday.toolsets import research as R

ARTICLE = (
    "Model Q is a local language model that scores ninety one on the tool call "
    "benchmark published this spring. The evaluation ran four hundred prompts "
    "across six tool families and counted a call as correct only when the name "
    "and every argument matched the reference. Reviewers noted that the model "
    "stays accurate when the tool list grows past forty entries, which earlier "
    "local models did not manage. Memory use stayed under nine gigabytes during "
    "the whole run on a single consumer graphics card."
)
OTHER = (
    "Separate lab notes on Model Q measured latency instead of accuracy. Median "
    "time to first token was under three hundred milliseconds when the prompt "
    "cache was warm, and roughly one second when cold. The authors disagree with "
    "headline benchmark claims and ran their own harness with different seeds."
)
MIRRORS = ["alpha", "bravo", "charlie", "delta", "echo"]
COPY_URLS = [f"https://mirror{i}.example/q" for i in range(5)]
OTHER_URL = "https://lab.example/notes"


def _copy(i: int) -> str:
    return f"Reposted by {MIRRORS[i]}. " + ARTICLE


def _page(url: str, text: str) -> dict:
    return {"url": url, "title": url, "ok": True, "markdown": text, "chars": len(text)}


def _pages() -> list[dict]:
    return [_page(u, _copy(i)) for i, u in enumerate(COPY_URLS)] + [_page(OTHER_URL, OTHER)]


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(RC, "DB_PATH", tmp_path / "artifacts.sqlite3")
    monkeypatch.delenv(W.ENV_VAR, raising=False)
    W.reset_override()
    yield
    W.reset_override()


def test_explore_reports_five_copies_as_one_source_with_copies_listed():
    inq = R.CanonicalInquiry.from_question(
        "Model Q tool call benchmark latency " + " ".join(MIRRORS))
    by_url = {p["url"]: p for p in _pages()}
    branches = [{"url": u, "title": "Model Q tool call benchmark"} for u in by_url]
    out = R.explore(inq, "R3", branches, lambda b: by_url[b["url"]])

    assert out["stop"]["independent_sources"] == 2
    assert len(out["pages"]) == 2
    kept = {p["url"]: p for p in out["pages"]}
    assert set(kept[COPY_URLS[0]]["copies"]) == set(COPY_URLS[1:])
    assert "copies" not in kept[OTHER_URL]


def test_web_deep_research_counts_five_copies_as_one_source(store, monkeypatch):
    from friday.toolsets import memory as toolset_memory

    toolset_memory.reset_store(store)
    urls = COPY_URLS + [OTHER_URL]

    async def fake_search(run, query, *, limit=8, engine=None):
        started = c.started(run.run_id, "web.search")
        return run.record(c.succeeded(started, output={
            "provider": "stub", "results": [{"url": u} for u in urls]},
            verification=c.Verification(method="stub", evidence="stand-in search")))

    async def fake_crawl_all(requested, **_kw):
        by_url = {p["url"]: p for p in _pages()}
        return [by_url[u] for u in requested]

    monkeypatch.setattr(R, "web_search", fake_search)
    monkeypatch.setattr(R, "crawl_all", fake_crawl_all)
    try:
        result = asyncio.run(R.web_deep_research(
            c.Run.create("test", capability="web"), "Model Q tool call benchmark",
            sources=10))
    finally:
        toolset_memory.reset_store(None)

    assert result.status == c.SUCCEEDED
    out = result.output
    assert out["independent_sources"] == 2
    assert out["read"] == 2
    listed = {s["url"]: s for s in out["sources"]}
    assert len(listed) == 2
    assert set(listed[COPY_URLS[0]]["copies"]) == set(COPY_URLS[1:])
    assert listed[OTHER_URL].get("copies", []) == []


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "test.sqlite3")
    yield s
    s.close()
