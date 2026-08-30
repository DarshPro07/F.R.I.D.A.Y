"""
The user model: learning, reconciliation, and correcting itself.

Extraction needs a model, so it is `live`. Reconciliation is deterministic by
design - that is the point - so all of it is tested offline.
"""

from __future__ import annotations

import pytest
from dotenv import load_dotenv

# Live extraction needs GOOGLE_API_KEY. The toolsets never load .env
# themselves - entry points do - so the test suite is the entry point here.
load_dotenv()

from friday import profile as P
from friday.store import FACT, INFERENCE, PATTERN, PREFERENCE, Store

live = pytest.mark.live


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "profile.sqlite3")
    yield s
    s.close()


def candidate(**kw) -> P.Candidate:
    base = dict(dimension=P.PREFERENCES, subject="user.editor", value="VS Code",
                kind=FACT, confidence=0.9, evidence="I use VS Code")
    base.update(kw)
    return P.Candidate(**base).validated()


# ---------------------------------------------------------------------------
# The reconciliation table
# ---------------------------------------------------------------------------


def test_new_fact_supersedes_an_earlier_inference(store):
    """We had guessed; you told us. You win."""
    store.remember("user.timezone", "PST", kind=INFERENCE,
                   source="guessed from timestamps", confidence=0.6)
    action, reason = P.decide(
        candidate(subject="user.timezone", value="IST", kind=FACT),
        store.recall("user.timezone")[0])
    assert action == "store"
    assert "supersedes" in reason


def test_an_inference_never_overwrites_something_you_said(store):
    store.remember("user.timezone", "IST", kind=FACT, source="user said so")
    action, reason = P.decide(
        candidate(subject="user.timezone", value="PST", kind=INFERENCE,
                  confidence=0.9),
        store.recall("user.timezone")[0])
    assert action == "reject"
    assert "does not overwrite" in reason


def test_two_stated_facts_disagreeing_is_a_conflict_not_a_decision(store):
    """
    This is the "correct me from previous data" case. It is deliberately NOT
    auto-resolved: which of two things you said is true is not ours to settle.
    """
    store.remember("project.db", "Postgres", kind=FACT, source="user said so")
    action, reason = P.decide(
        candidate(subject="project.db", value="SQLite", kind=FACT),
        store.recall("project.db")[0])
    assert action == "conflict"
    assert "not ours to settle" in reason


def test_seeing_the_same_thing_again_reinforces(store):
    store.remember("user.editor", "VS Code", kind=PREFERENCE,
                   source="observed", confidence=0.6)
    action, _ = P.decide(
        candidate(subject="user.editor", value="vs code", kind=PREFERENCE),
        store.recall("user.editor")[0])
    assert action == "reinforce", "matching should be case-insensitive"


def test_nothing_known_yet_is_simply_stored(store):
    action, reason = P.decide(candidate(), None)
    assert action == "store"
    assert "nothing was recorded" in reason


def test_low_confidence_candidates_are_rejected():
    action, reason = P.decide(candidate(confidence=0.2), None)
    assert action == "reject"
    assert "below the" in reason


def test_a_clearly_more_confident_peer_replaces_the_earlier_one(store):
    store.remember("user.editor", "Vim", kind=PREFERENCE,
                   source="observed", confidence=0.5)
    action, _ = P.decide(
        candidate(subject="user.editor", value="VS Code", kind=PREFERENCE,
                  confidence=0.9),
        store.recall("user.editor")[0])
    assert action == "store"


def test_similar_confidence_peers_conflict_rather_than_coin_flip(store):
    store.remember("user.editor", "Vim", kind=PREFERENCE,
                   source="observed", confidence=0.7)
    action, _ = P.decide(
        candidate(subject="user.editor", value="VS Code", kind=PREFERENCE,
                  confidence=0.75),
        store.recall("user.editor")[0])
    assert action == "conflict"


# ---------------------------------------------------------------------------
# Learning writes evidence, not just conclusions
# ---------------------------------------------------------------------------


def test_learning_records_the_quote_it_came_from(store):
    outcomes = P.learn(store, [candidate(evidence="I always use VS Code, boss")])
    assert outcomes[0].action == "stored"
    observation = store.observations(subject="user.editor")[0]
    assert observation["evidence"] == "I always use VS Code, boss"
    assert observation["status"] == "accepted"


def test_an_observation_cannot_be_stored_without_evidence(store):
    with pytest.raises(ValueError, match="quote it came from"):
        store.add_observation(dimension=P.WANTS, subject="s", value="v",
                              kind=FACT, confidence=0.9, evidence="   ")


def test_rejected_candidates_are_kept_on_record(store):
    P.learn(store, [candidate(confidence=0.1)])
    observation = store.observations(subject="user.editor")[0]
    assert observation["status"] == "rejected"
    assert not store.recall("user.editor"), "a rejected candidate must not enter the profile"


def test_reinforcement_raises_confidence_but_not_to_certainty(store):
    store.remember("user.editor", "VS Code", kind=PREFERENCE,
                   source="observed", confidence=0.9)
    P.learn(store, [candidate(kind=PREFERENCE, value="VS Code")])
    row = store.recall("user.editor")[0]
    assert row["confidence"] == pytest.approx(P.MAX_INFERRED_CONFIDENCE)
    assert row["confidence"] < 1.0


def test_repeated_observation_climbs_over_days(store):
    """"Learn every day" means confidence should actually move."""
    confidences = []
    for _ in range(4):
        P.learn(store, [candidate(kind=PATTERN, confidence=0.5)])
        confidences.append(store.recall("user.editor")[0]["confidence"])
    assert confidences == sorted(confidences), confidences
    assert confidences[-1] > confidences[0]


def test_a_conflict_is_recorded_with_both_sides(store):
    store.remember("project.db", "Postgres", kind=FACT, source="user said so")
    outcomes = P.learn(store, [candidate(subject="project.db", value="SQLite",
                                         kind=FACT, dimension=P.GOALS)])
    assert outcomes[0].action == "conflict"

    conflict = store.contradictions(resolution="pending")[0]
    assert conflict["existing_value"] == "Postgres"
    assert conflict["new_value"] == "SQLite"
    # The profile still holds the old value until a human settles it.
    assert store.recall("project.db")[0]["value"] == "Postgres"


# ---------------------------------------------------------------------------
# Correcting Friday
# ---------------------------------------------------------------------------


def test_resolving_a_conflict_in_favour_of_the_new_value(store):
    store.remember("project.db", "Postgres", kind=FACT, source="user said so")
    P.learn(store, [candidate(subject="project.db", value="SQLite", kind=FACT)])
    conflict = store.contradictions(resolution="pending")[0]

    result = P.resolve(store, conflict["id"], keep="new",
                       rationale="user confirmed we switched")
    assert result["kept"] == "new"
    assert store.recall("project.db")[0]["value"] == "SQLite"
    assert store.contradictions(resolution="pending") == []


def test_resolving_in_favour_of_the_existing_value(store):
    store.remember("project.db", "Postgres", kind=FACT, source="user said so")
    P.learn(store, [candidate(subject="project.db", value="SQLite", kind=FACT)])
    conflict = store.contradictions(resolution="pending")[0]

    P.resolve(store, conflict["id"], keep="existing", rationale="user misspoke")
    assert store.recall("project.db")[0]["value"] == "Postgres"


def test_resolve_rejects_a_nonsense_choice(store):
    with pytest.raises(ValueError, match="must be"):
        P.resolve(store, 1, keep="maybe", rationale="x")


def test_resolve_unknown_conflict_raises(store):
    with pytest.raises(KeyError):
        P.resolve(store, 999, keep="new", rationale="x")


# ---------------------------------------------------------------------------
# Retrieval and explainability
# ---------------------------------------------------------------------------


def test_profile_groups_by_the_users_own_dimensions(store):
    P.learn(store, [
        candidate(dimension=P.POSSESSIONS, subject="hardware.laptop",
                  value="Windows 11, 16 cores"),
        candidate(dimension=P.GOALS, subject="goal.ada",
                  value="build a local Jarvis"),
        candidate(dimension=P.THINKING, subject="thinking.evidence",
                  value="wants claims backed by evidence"),
    ])
    data = P.profile(store)
    assert data[P.POSSESSIONS] and data[P.GOALS] and data[P.THINKING]
    assert data[P.WANTS] == []


def test_brief_marks_inferences_so_they_are_not_read_as_facts(store):
    P.learn(store, [
        candidate(subject="user.role", value="engineer", kind=FACT),
        candidate(subject="user.timezone", value="IST", kind=INFERENCE,
                  confidence=0.6, dimension=P.IDENTITY),
    ])
    text = P.brief(store)
    assert "user.role: engineer" in text
    assert "[inference, 60%]" in text
    assert "never state an inference as a fact" in text


def test_brief_is_empty_when_nothing_is_known(store):
    assert P.brief(store) == ""


def test_brief_is_bounded(store):
    for i in range(60):
        P.learn(store, [candidate(subject=f"user.thing{i}", value="x" * 40)])
    assert len(P.brief(store, max_chars=500)) <= 500


def test_explain_traces_a_belief_back_to_the_words(store):
    P.learn(store, [candidate(subject="user.editor", value="VS Code",
                              evidence="I live in VS Code")])
    explanation = P.explain(store, "user.editor")
    assert explanation["current"]["value"] == "VS Code"
    assert explanation["observations"][0]["evidence"] == "I live in VS Code"


def test_explain_shows_superseded_beliefs_too(store):
    store.remember("user.editor", "Vim", kind=INFERENCE, source="guess",
                   confidence=0.5)
    P.learn(store, [candidate(subject="user.editor", value="VS Code", kind=FACT)])
    explanation = P.explain(store, "user.editor")
    assert explanation["current"]["value"] == "VS Code"
    assert any(r["value"] == "Vim" for r in explanation["superseded"])


def test_one_active_belief_per_subject_across_kinds(store):
    """
    remember() supersedes only within a kind, so a FACT replacing an INFERENCE
    would leave both live and the profile would show two contradictory values
    for the same thing. The profile layer supersedes across kinds.
    """
    store.remember("user.timezone", "PST", kind=INFERENCE,
                   source="guessed", confidence=0.6)
    P.learn(store, [candidate(subject="user.timezone", value="IST", kind=FACT,
                              dimension=P.IDENTITY)])

    active = store.recall("user.timezone")
    assert len(active) == 1, [r["value"] for r in active]
    assert active[0]["value"] == "IST"

    values = [row["value"] for row in P.profile(store)[P.IDENTITY]]
    assert values.count("IST") == 1 and "PST" not in values


# ---------------------------------------------------------------------------
# Candidate validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", [
    {"dimension": "vibes"},
    {"kind": "HUNCH"},
    {"confidence": 1.4},
    {"confidence": -0.1},
    {"evidence": "   "},
])
def test_malformed_candidates_are_refused(bad):
    with pytest.raises(ValueError):
        candidate(**bad)


@live
def test_live_extraction_finds_something_real():
    said = ("I'm building ADA on a Windows laptop and I care a lot about "
            "evidence - I don't want it claiming things it didn't do. "
            "I prefer local-first tools.")
    candidates = P.extract_candidates(said)
    assert candidates, "extractor found nothing in a fact-rich turn"
    for item in candidates:
        assert item.evidence.strip()
        assert item.dimension in P.DIMENSIONS


@live
def test_live_extraction_returns_nothing_for_small_talk():
    candidates = P.extract_candidates("ok cool thanks")
    assert candidates == [], [c.as_dict() for c in candidates]
