"""
S7: the gate between an observed candidate and Friday's canonical memory.
"""
from __future__ import annotations

import logging

import pytest

from friday.handoff import Handoff
from friday.memory_promotion import Candidate, promote, promote_handoff
from friday.store import FACT, Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "test.sqlite3")
    yield s
    s.close()


def test_promotion_rejects_unverified_and_secret_shaped_candidates(store):
    secret = Candidate(
        statement="api_key: sk-abcdefghijklmnopqrstuvwx", kind="project_fact",
        source="test", owner="friday", scope="project", confidence=0.9,
        evidence=["he pasted it"],
    )
    assert promote(secret, store=store).accepted is False

    no_evidence = Candidate(
        statement="the deploy target is staging", kind="project_fact",
        source="test", owner="friday", scope="project", confidence=0.9,
        evidence=[],
    )
    assert promote(no_evidence, store=store).accepted is False

    low_confidence = Candidate(
        statement="the deploy target is staging", kind="project_fact",
        source="test", owner="friday", scope="project", confidence=0.3,
        evidence=["it came up once"],
    )
    assert promote(low_confidence, store=store).accepted is False

    one_off = Candidate(
        statement="the test run passed", kind="outcome",
        source="test", owner="friday", scope="project", confidence=0.9,
        evidence=["pytest exited 0"],
    )
    assert promote(one_off, store=store).accepted is False


def test_duplicate_is_not_written_twice(store):
    c = Candidate(
        statement="the deploy target is staging", kind="project_fact",
        source="test", owner="friday", scope="project", confidence=0.9,
        evidence=["said once"],
    )
    first = promote(c, store=store)
    assert first.accepted is True
    second = promote(c, store=store)
    assert second.accepted is False
    assert "duplicate" in second.reason
    assert len(store.recall("the deploy target")) == 1


def test_contradiction_supersedes_not_overwrites(store):
    weak = Candidate(
        statement="the deploy target is staging", kind="project_fact",
        source="test", owner="friday", scope="project", confidence=0.6,
        evidence=["said once"],
    )
    assert promote(weak, store=store).accepted is True

    stronger = Candidate(
        statement="the deploy target is production", kind="project_fact",
        source="test", owner="friday", scope="project", confidence=0.9,
        evidence=["confirmed twice"],
    )
    decision = promote(stronger, store=store)
    assert decision.accepted is True
    assert decision.superseded

    rows = store.recall("the deploy target", include_superseded=True)
    assert len(rows) == 2
    assert {r["superseded"] for r in rows} == {0, 1}
    assert len(store.contradictions()) == 1

    weaker_again = Candidate(
        statement="the deploy target is qa", kind="project_fact",
        source="test", owner="friday", scope="project", confidence=0.65,
        evidence=["overheard"],
    )
    decision2 = promote(weaker_again, store=store)
    assert decision2.accepted is False
    assert "contradiction" in decision2.reason
    # both prior claims stay, nothing overwritten silently
    assert len(store.recall("the deploy target", include_superseded=True)) == 2


def test_procedure_becomes_a_skill_candidate_not_a_fact(store):
    c = Candidate(
        statement="always run migrations before restarting the worker",
        kind="procedure", source="test", owner="friday", scope="project",
        confidence=0.9, evidence=["it broke without this once"],
    )
    decision = promote(c, store=store)
    assert decision.accepted is True
    assert decision.target == "skill"
    assert store.recall("always run migrations") == []

    from friday.memory_promotion import SKILL_CANDIDATES_DIR
    files = list(SKILL_CANDIDATES_DIR.glob("*.md"))
    assert files, "expected a skill candidate file to be written"
    files[-1].unlink()  # keep the repo clean after the test


def test_handoff_candidates_flow_through_promotion(store):
    handoff = Handoff(
        task_id="t1", agent="claude", summary="did the thing",
        memory_candidates=("the deploy target is staging",),
        skill_candidates=("always run migrations before restarting the worker",),
    )
    decisions = promote_handoff(handoff, store=store)
    assert len(decisions) == 2
    assert decisions[0].accepted is True and decisions[0].target == "memory"
    assert decisions[1].accepted is True and decisions[1].target == "skill"

    from friday.memory_promotion import SKILL_CANDIDATES_DIR
    for f in SKILL_CANDIDATES_DIR.glob("*.md"):
        f.unlink()


def test_autolearn_uses_the_gate(monkeypatch, caplog):
    from friday import autolearn as A

    calls = []

    def fake_promote(candidate, *, store=None):
        calls.append(candidate)
        from friday.memory_promotion import Decision
        return Decision(False, "refused: no evidence")

    monkeypatch.setattr(A, "promote", fake_promote, raising=False)
    monkeypatch.setattr("friday.memory_promotion.promote", fake_promote)

    learner = A.AutoLearner(invoke=None)
    with caplog.at_level(logging.INFO):
        learner._gate(
            [{"subject": "user.role", "value": "engineer"}], "I'm an engineer"
        )

    assert len(calls) == 1
    assert calls[0].kind == "project_fact"
    assert "user.role" in calls[0].statement and "engineer" in calls[0].statement
    assert any("autolearn gate" in r.message for r in caplog.records)
