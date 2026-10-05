"""
Tests for friday/research_cache.py (FR-MEM-005).

Every test points DB_PATH at a fresh tmp_path file and clears the workspace
override before and after, so no test can see another test's cache or
inherit another test's workspace - the same discipline
tests/test_workspace_isolation.py uses for the main store. Freshness tests
inject `now` rather than sleeping: two days old is a fact about the data,
not about the clock.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from friday import research_cache as RC
from friday import workspace as W


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(RC, "DB_PATH", tmp_path / "artifacts.sqlite3")
    monkeypatch.delenv(W.ENV_VAR, raising=False)
    W.reset_override()
    yield
    W.reset_override()


def make(question="best local LLM for tool calling", *, freshness=None,
         fetched_at=None, sources=None, facts=None, **kw) -> RC.Artifact:
    return RC.Artifact.create(
        question,
        sources=sources or [{"url": "https://a.example/x", "title": "A"}],
        facts=facts or ["the fact found on the page"],
        freshness=freshness, fetched_at=fetched_at, **kw,
    )


# ---------------------------------------------------------------------------
# Round trip (criterion 3: reused research stays attributable to its sources)
# ---------------------------------------------------------------------------


def test_save_then_lookup_returns_the_artifact_with_sources_intact():
    artifact = make(
        "what is the capital of France",
        sources=[{"url": "https://a.example/paris", "title": "Paris - overview"},
                 {"url": "https://b.example/france", "title": "France"}],
        facts=["Paris is the capital of France.", "France is in Europe."],
        freshness=RC.STABLE, confidence=0.9,
        invalidation_triggers=["a new capital were ever declared"],
    )
    RC.save(artifact)

    found = RC.lookup("what is the capital of France")
    assert found is not None
    assert found.question == "what is the capital of France"
    assert [s.url for s in found.sources] == \
        ["https://a.example/paris", "https://b.example/france"]
    assert [s.title for s in found.sources] == ["Paris - overview", "France"]
    assert found.facts == ["Paris is the capital of France.", "France is in Europe."]
    assert found.confidence == 0.9
    assert found.invalidation_triggers == ["a new capital were ever declared"]


def test_lookup_of_a_never_saved_question_is_none():
    assert RC.lookup("nobody has ever asked this before") is None


# ---------------------------------------------------------------------------
# Freshness (criterion 2: fast-moving facts expire sooner than stable ones)
# ---------------------------------------------------------------------------


def test_a_volatile_artifact_two_days_old_is_not_returned():
    base = datetime(2026, 1, 10, tzinfo=timezone.utc)
    RC.save(make("what is the stock price of Nvidia today",
                 freshness=RC.VOLATILE, fetched_at=base - timedelta(days=2)))
    assert RC.lookup("what is the stock price of Nvidia today", now=base) is None


def test_a_stable_artifact_two_days_old_is_returned():
    base = datetime(2026, 1, 10, tzinfo=timezone.utc)
    RC.save(make("how does the TCP handshake protocol work",
                 freshness=RC.STABLE, fetched_at=base - timedelta(days=2)))
    found = RC.lookup("how does the TCP handshake protocol work", now=base)
    assert found is not None
    assert found.freshness == RC.STABLE


# ---------------------------------------------------------------------------
# Reuse (criterion 1: sufficiently fresh prior research is reused)
# ---------------------------------------------------------------------------


def test_differently_punctuated_and_cased_questions_hit_one_artifact():
    RC.save(make("best local LLM for tool calling"))
    same = RC.lookup("Best local LLM for tool calling?")
    assert same is not None
    assert same.question == "best local LLM for tool calling"


def test_normalize_question_collapses_case_punctuation_and_word_order():
    a = RC.normalize_question("Best local LLM for tool calling?")
    b = RC.normalize_question("best local llm for tool calling")
    c = RC.normalize_question("tool calling, best local LLM!!")
    assert a == b == c
    assert RC.question_key("Best local LLM for tool calling?") == \
        RC.question_key("tool calling, best local LLM!!")


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("question, expected", [
    ("What is the current stock price of Nvidia?", RC.VOLATILE),
    ("What is the best local LLM for tool calling?", RC.WEEKLY),
    ("How does the TCP handshake protocol work?", RC.STABLE),
    ("When was Alan Turing born?", RC.REFERENCE),
])
def test_classify_freshness_representative_questions(question, expected):
    assert RC.classify_freshness(question) == expected


def test_classify_freshness_defaults_to_weekly_when_nothing_matches():
    assert RC.classify_freshness("something with no particular signal at all") == RC.WEEKLY


def test_classify_freshness_hint_overrides_text():
    assert RC.classify_freshness("anything at all", hint=RC.REFERENCE) == RC.REFERENCE


def test_classify_freshness_bad_hint_falls_through_to_text():
    assert RC.classify_freshness("what is the current stock price",
                                 hint="not-a-real-class") == RC.VOLATILE


# ---------------------------------------------------------------------------
# Workspace isolation
# ---------------------------------------------------------------------------


def test_lookup_does_not_cross_workspaces():
    """
    An artifact saved in workspace A must not be returned by a lookup in
    workspace B - the PRD's named leak case (FR-PRV-002), applied to research.
    """
    question = "what is our refund window"
    with W.use("brand-a"):
        RC.save(make(question, sources=[{"url": "https://a.example/refund"}]))
    with W.use("brand-b"):
        # Gives the raw table a second workspace's row, so the negative
        # control below is not trivially satisfied by one row alone.
        RC.save(make("an unrelated brand-b question"))

    # Negative control: a direct, unfiltered read MUST see rows for both
    # workspaces. Without this, "lookup in B finds nothing" below could pass
    # for the wrong reason - an empty table looks identical from the outside
    # to a correctly-filtered one.
    raw = sqlite3.connect(RC.DB_PATH)
    try:
        rows = raw.execute("SELECT workspace_id FROM artifacts").fetchall()
    finally:
        raw.close()
    assert {row[0] for row in rows} == {"brand-a", "brand-b"}, \
        "negative control failed: the raw table must show rows for both workspaces"

    with W.use("brand-b"):
        assert RC.lookup(question) is None, \
            "workspace B's lookup returned workspace A's artifact"
    with W.use("brand-a"):
        found = RC.lookup(question)
    assert found is not None
    assert found.sources[0].url == "https://a.example/refund"


def test_lookup_workspace_id_kwarg_overrides_the_ambient_workspace():
    with W.use("brand-a"):
        RC.save(make("what is our refund window"))
    # No W.use() active here; pass the workspace explicitly instead.
    assert RC.lookup("what is our refund window", workspace_id="brand-a") is not None
    assert RC.lookup("what is our refund window", workspace_id="brand-b") is None


# ---------------------------------------------------------------------------
# Maintenance: stale() and invalidate()
# ---------------------------------------------------------------------------


def test_stale_finds_an_expired_artifact():
    base = datetime(2026, 1, 10, tzinfo=timezone.utc)
    RC.save(make("what is the weather in london today",
                 freshness=RC.VOLATILE, fetched_at=base - timedelta(days=2)))
    found = RC.stale(now=base)
    assert len(found) == 1
    assert found[0].question == "what is the weather in london today"


def test_stale_does_not_list_a_fresh_artifact():
    base = datetime(2026, 1, 10, tzinfo=timezone.utc)
    RC.save(make("how does the TCP handshake protocol work",
                 freshness=RC.STABLE, fetched_at=base))
    assert RC.stale(now=base) == []


def test_invalidate_makes_a_fresh_artifact_stop_being_returned():
    question = "what is the best local LLM for tool calling"
    RC.save(make(question, freshness=RC.STABLE))  # 90 days from expiring
    assert RC.lookup(question) is not None

    changed = RC.invalidate(RC.question_key(question),
                            "a new model release from this provider")
    assert changed is True
    assert RC.lookup(question) is None


def test_invalidate_returns_false_for_an_unknown_key():
    assert RC.invalidate("no-such-question-key", "reason given") is False


def test_invalidate_requires_a_reason():
    RC.save(make("some question"))
    with pytest.raises(ValueError):
        RC.invalidate(RC.question_key("some question"), "   ")


def test_invalidated_reason_is_kept_on_record():
    question = "what is the best local LLM for tool calling"
    RC.save(make(question, freshness=RC.STABLE))
    RC.invalidate(RC.question_key(question), "a new model release from this provider")
    [found] = RC.stale()
    assert found.invalidated_reason == "a new model release from this provider"


# ---------------------------------------------------------------------------
# prune()
# ---------------------------------------------------------------------------


def test_prune_deletes_expired_and_leaves_fresh_artifacts():
    base = datetime(2026, 1, 10, tzinfo=timezone.utc)
    RC.save(make("what is the weather in london today",
                 freshness=RC.VOLATILE, fetched_at=base - timedelta(days=10)))
    RC.save(make("how does the TCP handshake protocol work",
                 freshness=RC.STABLE, fetched_at=base))

    removed = RC.prune(base)
    assert removed == 1
    assert RC.lookup("how does the TCP handshake protocol work", now=base) is not None

    raw = sqlite3.connect(RC.DB_PATH)
    try:
        remaining = raw.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0]
    finally:
        raw.close()
    assert remaining == 1


# ---------------------------------------------------------------------------
# save() guard rails
# ---------------------------------------------------------------------------


def test_save_refuses_an_artifact_with_no_sources():
    """Source-backed is not optional - FR-MEM-005 names it as the point."""
    with pytest.raises(ValueError):
        RC.save(RC.Artifact.create("a question", sources=[], facts=[]))


def test_create_refuses_an_unknown_freshness_class():
    with pytest.raises(ValueError):
        RC.Artifact.create("a question", sources=[{"url": "https://a.example"}],
                           facts=[], freshness="daily")


# ---------------------------------------------------------------------------
# to_ledger() (EPI-001: the PRD section 35 epistemic-ledger record)
# ---------------------------------------------------------------------------


def test_an_artifact_becomes_one_ledger_claim_per_fact():
    base = datetime(2026, 1, 10, tzinfo=timezone.utc)
    artifact = make("which parser is fastest", freshness=RC.WEEKLY,
                    fetched_at=base, facts=["parser A wins", "parser B is slow"],
                    sources=[{"url": "https://a.example/x"},
                             {"url": "https://b.example/y"}],
                    synthesis="A beats B", confidence=0.8,
                    invalidation_triggers=["a new parser release"],
                    workspace_id="ws-1")

    first, second = artifact.to_ledger()

    assert [first.claim, second.claim] == ["parser A wins", "parser B is slow"]
    assert first.status == "sourced"
    assert first.sources == ("https://a.example/x", "https://b.example/y")
    assert first.confidence == 0.8
    assert first.freshness == RC.WEEKLY
    assert first.as_of == base.isoformat()
    assert first.expires_at == (base + timedelta(days=7)).isoformat()
    assert first.assumptions == ("holds unless: a new parser release",)
    assert first.scope == "ws-1"
    assert "which parser is fastest" in first.evidence and "A beats B" in first.evidence
    assert first.id != second.id


def test_an_invalidated_artifact_carries_its_reason_as_a_contradiction():
    question = "what is the best local LLM for tool calling"
    RC.save(make(question, freshness=RC.STABLE))
    RC.invalidate(RC.question_key(question), "a new model release")
    [found] = RC.stale()

    [claim] = found.to_ledger()

    assert claim.contradictions == ("a new model release",)


def test_an_artifact_with_no_source_urls_is_speculative_not_sourced():
    artifact = make(sources=[{"url": ""}])

    [claim] = artifact.to_ledger()

    assert claim.status == "speculative" and claim.sources == ()


def test_an_artifact_with_only_a_synthesis_still_yields_its_conclusion():
    artifact = RC.Artifact.create(
        "a question", sources=[{"url": "https://a.example"}], facts=[],
        synthesis="the conclusion")

    assert [c.claim for c in artifact.to_ledger()] == ["the conclusion"]
