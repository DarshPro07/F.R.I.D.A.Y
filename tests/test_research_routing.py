"""
RSCH-004: research artifacts go to their owners.

A research run's raw corpus lives in the research store; its claims become
epistemic-ledger records; a pointer to it goes to the Project Brain; and only
small, sourced facts cross into memory, through the promotion gate as read
material. Every test uses a temp store and a temp research cache.
"""
from __future__ import annotations

import pytest

from friday import contracts as c
from friday import research_cache as RC
from friday import research_routing as RR
from friday import workspace as W
from friday.project_brain import ProjectBrain
from friday.store import Store

FAKE_KEY = "sk-" + "abcdefghijklmnopqrstuvwx"
CORPUS = "Paragraph of a fetched page about local models. " * 200  # ~9 KB


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


def memories(store):
    return [dict(r) for r in store._conn.execute(
        "SELECT subject, value, source, scope, trust_class FROM memories "
        "ORDER BY id")]


def artifact(facts, *, confidence=0.8, sources=None, question="which local model calls tools best"):
    return RC.Artifact.create(
        question,
        sources=sources if sources is not None else [
            {"url": "https://a.example/x", "title": "A"}],
        facts=facts, synthesis="Model Q wins on tool calls.",
        confidence=confidence)


def test_research_run_changes_memory_only_by_its_small_facts(store):
    before = memories(store)
    result = RR.route(artifact(
        ["Model Q scores 91 on the tool-call benchmark.",
         "Model Z has a 32k context window.",
         CORPUS]), store=store, project="friday")

    after = memories(store)
    added = after[len(before):]
    assert after[:len(before)] == before
    assert [r["value"] for r in added] == [
        "Model Q scores 91 on the tool-call benchmark.",
        "Model Z has a 32k context window."]
    assert all(r["trust_class"] == c.READ_MATERIAL for r in added)
    assert all(r["scope"] not in {"preferences", "wants", "goals", "identity"}
               for r in added)
    assert len(result.promoted) == 2


def test_raw_corpus_is_in_the_research_store_and_never_in_memory(store):
    RR.route(artifact(["short fact about Q.", CORPUS]), store=store)

    assert not any("Paragraph of a fetched page" in r["value"]
                   for r in memories(store))
    kept = RC.lookup("which local model calls tools best")
    assert kept is not None and CORPUS in kept.facts


def test_claims_become_ledger_records_and_the_brain_points_at_the_artifact(store):
    result = RR.route(artifact(["Model Q scores 91 on the tool-call benchmark."]),
                      store=store, project="friday")

    assert [cl.claim for cl in result.ledger] == [
        "Model Q scores 91 on the tool-call benchmark."]
    assert result.ledger[0].sources == ("https://a.example/x",)
    record = ProjectBrain(store).get("friday", "research", result.brain_key)
    assert record is not None
    assert record.body["research_key"] == RC.question_key(
        "which local model calls tools best")
    assert record.body["claim_ids"] == [result.ledger[0].id]
    assert "Paragraph" not in str(record.body)


def test_a_rerun_supersedes_the_brain_record_and_does_not_duplicate_facts(store):
    fact = "Model Q scores 91 on the tool-call benchmark."
    first = RR.route(artifact([fact]), store=store)
    second = RR.route(artifact([fact]), store=store)

    assert first.brain_key == second.brain_key
    assert len(memories(store)) == 1
    assert second.promoted == ()
    assert len(ProjectBrain(store).history("friday", "research",
                                           first.brain_key)) == 2


def test_unsourced_research_is_refused_before_anything_is_written(store):
    with pytest.raises(ValueError):
        RR.route(artifact(["a fact"], sources=[]), store=store)
    assert memories(store) == []
    assert ProjectBrain(store).list("friday", "research") == []


def test_secret_shaped_and_low_confidence_facts_stay_out_of_memory(store):
    result = RR.route(artifact(["token is " + FAKE_KEY, "Model Q is fast."],
                               confidence=0.3), store=store)
    assert memories(store) == []
    assert result.promoted == ()
    # the research store still holds them: it is the owner of raw findings
    assert RC.lookup("which local model calls tools best") is not None


def test_at_most_max_promoted_facts_cross_into_memory(store):
    facts = [f"Fact number {i} about model Q." for i in range(RR.MAX_PROMOTED + 4)]
    RR.route(artifact(facts), store=store)
    assert len(memories(store)) == RR.MAX_PROMOTED
