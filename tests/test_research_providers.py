"""
RSCH-002: deep research behind one FridayResearchProvider interface.

Two providers sit behind it: the existing web research toolset (wrapped) and
a deterministic fake. Nothing here touches the network.
"""

from __future__ import annotations

import asyncio

import pytest

from friday import contracts as c
from friday import research_providers as P
from friday.toolsets import research as R


def run_for() -> c.Run:
    return c.Run.create("test", capability="web")


class FakeProvider:
    """Deterministic second provider: canned sources, records what it was asked."""

    name = "fake"

    def __init__(self, sources=None, error=None):
        self.sources = sources if sources is not None else [
            {"url": "https://fake.example/pg", "title": "Postgres replication", "chars": 120},
        ]
        self.error = error
        self.calls: list[tuple[str, str]] = []

    async def research(self, run, question, depth):
        self.calls.append((question, depth))
        if self.error:
            raise self.error
        return P.ProviderResult(provider=self.name, question=question, depth=depth,
                                status=c.SUCCEEDED, sources=tuple(self.sources))


@pytest.fixture
def fake(monkeypatch):
    provider = FakeProvider()
    monkeypatch.setitem(P.PROVIDERS, "fake", provider)
    return provider


def _fake_deep_research(seen):
    async def fake_deep(run, question, *, sources=5, max_chars_per_source=0,
                        budget=0, engine=None):
        seen.update(question=question, sources=sources, budget=budget)
        started = c.started(run.run_id, "web.deep_research")
        return run.record(c.succeeded(started, output={
            "question": question,
            "sources": [{"url": "https://w.example/a", "title": "Postgres and MySQL replication", "chars": 900,
                         "relevance": 3}],
        }, verification=c.Verification(method="test", evidence="canned")))
    return fake_deep


Q = "Compare Postgres and MySQL replication"


def test_both_providers_satisfy_the_interface():
    assert isinstance(P.PROVIDERS["web"], P.FridayResearchProvider)
    assert isinstance(FakeProvider(), P.FridayResearchProvider)


def test_two_providers_return_the_same_shaped_result(monkeypatch, fake):
    monkeypatch.setattr(R, "web_deep_research", _fake_deep_research({}))
    web = asyncio.run(P.research_with(run_for(), Q, provider="web", requested="R2"))
    other = asyncio.run(P.research_with(run_for(), Q, provider="fake", requested="R2"))
    for result, name in ((web, "web"), (other, "fake")):
        assert isinstance(result, P.ProviderResult)
        assert result.provider == name
        assert result.status == c.SUCCEEDED
        assert result.depth == "R2"
        assert result.sources and {"url", "title", "chars"} <= set(result.sources[0])


def test_web_provider_wraps_the_research_toolset_under_the_depth_budget(monkeypatch):
    seen: dict = {}
    monkeypatch.setattr(R, "web_deep_research", _fake_deep_research(seen))
    result = asyncio.run(P.research_with(run_for(), Q, provider="web", requested="R2"))
    assert seen["question"] == Q
    assert seen["sources"] == R.DEPTH_BUDGETS["R2"].max_sources
    assert seen["budget"] == R.DEPTH_BUDGETS["R2"].max_chars
    assert result.sources[0]["url"] == "https://w.example/a"


def test_web_provider_failure_is_a_failed_result_not_an_exception(monkeypatch):
    async def failing(run, question, **_kw):
        started = c.started(run.run_id, "web.deep_research")
        return run.record(c.failed(started, "could not search: no provider"))

    monkeypatch.setattr(R, "web_deep_research", failing)
    result = asyncio.run(P.research_with(run_for(), Q, provider="web", requested="R2"))
    assert result.status == c.FAILED
    assert "could not search" in result.error
    assert result.sources == ()


def test_depth_routing_caps_a_one_fact_question(fake):
    asyncio.run(P.research_with(run_for(), "Who founded OpenAI?", provider="fake", requested="R5"))
    assert fake.calls == [("Who founded OpenAI?", "R1")]


def test_a_blank_question_needs_no_provider(fake):
    result = asyncio.run(P.research_with(run_for(), "  ", provider="fake"))
    assert fake.calls == []
    assert result.depth == "R0"
    assert result.sources == ()


def test_off_inquiry_sources_are_rejected_at_the_interface(monkeypatch):
    drifted = FakeProvider(sources=[
        {"url": "https://fake.example/pg", "title": "Postgres replication", "chars": 10},
        {"url": "https://fake.example/cats", "title": "Cute kittens gallery", "chars": 10},
    ])
    monkeypatch.setitem(P.PROVIDERS, "drift", drifted)
    result = asyncio.run(P.research_with(run_for(), Q, provider="drift", requested="R2"))
    assert [s["title"] for s in result.sources] == ["Postgres replication"]
    assert result.rejected[0]["reason"] == "off_inquiry"


def test_a_raising_provider_cannot_crash_the_caller(monkeypatch):
    monkeypatch.setitem(P.PROVIDERS, "boom", FakeProvider(error=RuntimeError("upstream down")))
    result = asyncio.run(P.research_with(run_for(), Q, provider="boom", requested="R2"))
    assert result.status == c.FAILED
    assert "RuntimeError" in result.error and "upstream down" in result.error


def test_unknown_provider_names_the_available_ones():
    with pytest.raises(ValueError, match="web"):
        asyncio.run(P.research_with(run_for(), Q, provider="nope"))
