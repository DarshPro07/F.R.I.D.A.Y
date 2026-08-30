"""
How often, and from where.

Confidence answers "how sure", and on its own it cannot tell a fact stated
five times from one mentioned once at high conviction. Two columns close
that: `evidence_count` and `last_confirmed`, plus `observation_id` so every
belief traces back to the raw episode it came from.

The migration matters as much as the columns. Databases were already in use,
and CREATE TABLE IF NOT EXISTS does nothing to a table that exists - losing
what Friday has learned to gain a column is not a trade worth making.
"""

from __future__ import annotations

import sqlite3

import pytest
from dotenv import load_dotenv

# The live admission tests call the real extractor, which needs
# GOOGLE_API_KEY. The toolsets never load .env themselves - the process that
# owns them does - so the tests have to.
load_dotenv()

from friday import profile as P  # noqa: E402
from friday.store import FACT, INFERENCE, PREFERENCE, Store  # noqa: E402


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "test.sqlite3")
    yield s
    s.close()


# ---------------------------------------------------------------------------
# Provenance on a new memory
# ---------------------------------------------------------------------------


def test_a_new_memory_has_been_seen_once(store):
    store.remember("user.os", "Linux", kind=FACT, source="he said so")
    row = store.recall("user.os")[0]
    assert row["evidence_count"] == 1


def test_a_new_memory_is_confirmed_when_it_is_created(store):
    store.remember("user.os", "Linux", kind=FACT, source="he said so")
    row = store.recall("user.os")[0]
    assert row["last_confirmed"] == row["created_at"]


def test_a_memory_can_point_at_the_episode_it_came_from(store):
    observation = store.add_observation(
        dimension="possessions", subject="user.os", value="Linux", kind=FACT,
        confidence=1.0, evidence="I switched everything to Linux")
    memory_id = store.remember("user.os", "Linux", kind=FACT, source="said",
                               observation_id=observation)
    assert store.recall("user.os")[0]["observation_id"] == observation
    assert memory_id


def test_evidence_count_below_one_is_refused(store):
    """Zero observations is not a memory, it is a guess with a table row."""
    with pytest.raises(ValueError, match="at least 1"):
        store.remember("x", "y", kind=FACT, source="s", evidence_count=0)


# ---------------------------------------------------------------------------
# Reinforcement carries the count
# ---------------------------------------------------------------------------


def test_hearing_the_same_thing_again_raises_the_count(store):
    candidate = P.Candidate(
        dimension="preferences", subject="user.editor", value="vim",
        kind=PREFERENCE, confidence=0.7, evidence="I live in vim")
    P.learn(store, [candidate])
    P.learn(store, [candidate])
    P.learn(store, [candidate])

    row = store.recall("user.editor")[0]
    assert row["evidence_count"] == 3, "each confirmation must count"


def test_the_count_survives_the_new_row_reinforcement_writes(store):
    """
    Reinforcement writes a new row rather than editing the old one, so the
    count has to be carried across - otherwise every confirmation looks like
    the first time it was ever said.
    """
    candidate = P.Candidate(
        dimension="thinking", subject="user.style", value="terse",
        kind=PREFERENCE, confidence=0.6, evidence="keep it short")
    P.learn(store, [candidate])
    first = store.recall("user.style")[0]
    P.learn(store, [candidate])
    second = store.recall("user.style")[0]

    assert second["id"] != first["id"], "reinforcement should write a new row"
    assert second["evidence_count"] == first["evidence_count"] + 1


def test_a_corrected_fact_starts_counting_again(store):
    """A new value is a new belief, not a fifth confirmation of the old one."""
    P.learn(store, [P.Candidate(
        dimension="possessions", subject="user.os", value="Windows",
        kind=INFERENCE, confidence=0.6, evidence="looks like Windows")])
    P.learn(store, [P.Candidate(
        dimension="possessions", subject="user.os", value="Windows",
        kind=INFERENCE, confidence=0.6, evidence="still looks like Windows")])
    assert store.recall("user.os")[0]["evidence_count"] == 2

    P.learn(store, [P.Candidate(
        dimension="possessions", subject="user.os", value="Linux",
        kind=FACT, confidence=1.0, evidence="actually I'm on Linux")])
    row = store.recall("user.os")[0]
    assert row["value"] == "Linux"
    assert row["evidence_count"] == 1


def test_reinforcement_moves_last_confirmed_forward(store):
    candidate = P.Candidate(
        dimension="goals", subject="project.goal", value="ship it",
        kind=FACT, confidence=1.0, evidence="we ship this month")
    P.learn(store, [candidate])
    first = store.recall("project.goal")[0]
    P.learn(store, [candidate])
    second = store.recall("project.goal")[0]
    assert second["last_confirmed"] >= first["last_confirmed"]


def test_the_reason_says_how_many_times(store):
    candidate = P.Candidate(
        dimension="preferences", subject="user.shell", value="bash",
        kind=PREFERENCE, confidence=0.6, evidence="bash, always")
    P.learn(store, [candidate])
    outcome = P.learn(store, [candidate])[0]
    assert outcome.action == "reinforced"
    assert "seen 2 times" in outcome.reason


# ---------------------------------------------------------------------------
# The briefing says how well-attested a thing is
# ---------------------------------------------------------------------------


def test_something_heard_once_is_marked_as_heard_once(store):
    P.learn(store, [P.Candidate(
        dimension="thinking", subject="user.risk", value="cautious",
        kind=INFERENCE, confidence=0.5, evidence="hedged twice")])
    assert "[heard once]" in P.brief(store)


def test_something_confirmed_repeatedly_says_so(store):
    candidate = P.Candidate(
        dimension="preferences", subject="user.editor", value="vim",
        kind=PREFERENCE, confidence=0.7, evidence="vim again")
    for _ in range(3):
        P.learn(store, [candidate])
    assert "[confirmed 3x]" in P.brief(store)


def test_a_stated_fact_needs_no_hedge(store):
    P.learn(store, [P.Candidate(
        dimension="identity", subject="user.name", value="Iron Mon",
        kind=FACT, confidence=1.0, evidence="my name is Iron Mon")])
    line = [ln for ln in P.brief(store).splitlines() if "user.name" in ln][0]
    assert "[heard once]" not in line
    assert "[inference" not in line


def test_the_profile_exposes_the_provenance(store):
    P.learn(store, [P.Candidate(
        dimension="possessions", subject="user.laptop", value="16 cores",
        kind=FACT, confidence=1.0, evidence="sixteen cores")])
    entry = P.profile(store)["possessions"][0]
    assert entry["evidence_count"] == 1
    assert entry["last_confirmed"]
    assert entry["observation_id"] is not None, "no trace back to the episode"


# ---------------------------------------------------------------------------
# Admission: what is allowed to become permanent at all
# ---------------------------------------------------------------------------


def test_low_confidence_never_reaches_the_profile(store):
    """The deterministic floor, underneath whatever the extractor decides."""
    outcome = P.learn(store, [P.Candidate(
        dimension="thinking", subject="user.mood", value="frustrated",
        kind=INFERENCE, confidence=0.2, evidence="sighed")])[0]
    assert outcome.action == "rejected"
    assert store.recall("user.mood") == []


def test_a_rejected_candidate_is_still_on_record(store):
    """Rejected is a decision, not a disappearance."""
    P.learn(store, [P.Candidate(
        dimension="thinking", subject="user.mood", value="frustrated",
        kind=INFERENCE, confidence=0.2, evidence="sighed")])
    observations = store.observations(subject="user.mood")
    assert observations and observations[0]["status"] == "rejected"


def test_a_guess_never_overwrites_something_he_stated(store):
    P.learn(store, [P.Candidate(
        dimension="possessions", subject="user.os", value="Linux",
        kind=FACT, confidence=1.0, evidence="I run Linux")])
    outcome = P.learn(store, [P.Candidate(
        dimension="possessions", subject="user.os", value="macOS",
        kind=INFERENCE, confidence=0.9, evidence="mentioned Homebrew")])[0]
    assert outcome.action == "rejected"
    assert store.recall("user.os")[0]["value"] == "Linux"


TRANSIENT = [
    "I have a headache today.",
    "I'm hungry, going to grab lunch.",
    "It's raining here right now.",
    "I'm tired, been up since five.",
    "Open Spotify for me.",
    "What time is it?",
    "That test is still running.",
]

DURABLE = [
    "I always use Windows for development.",
    "We're calling this project ADA.",
    "I hate frameworks that hide what they're doing.",
    "My laptop has 16 cores and 16 gigs of RAM.",
]


@pytest.mark.live
@pytest.mark.parametrize("said", TRANSIENT)
def test_a_passing_remark_does_not_become_permanent(said):
    """
    "I have a headache today" must not still be in the profile next month.
    Measured 7/7 empty when this was written; pinned so it stays that way.
    """
    assert P.extract_candidates(said) == [], f"{said!r} was kept"


@pytest.mark.live
@pytest.mark.parametrize("said", DURABLE)
def test_something_worth_keeping_is_kept(said):
    """The gate has to let the real thing through, or it is just a wall."""
    assert P.extract_candidates(said), f"{said!r} was dropped"


# ---------------------------------------------------------------------------
# The migration
# ---------------------------------------------------------------------------


OLD_SCHEMA = """
CREATE TABLE memories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    subject     TEXT NOT NULL,
    value       TEXT NOT NULL,
    kind        TEXT NOT NULL,
    scope       TEXT NOT NULL DEFAULT 'user',
    source      TEXT NOT NULL,
    confidence  REAL NOT NULL DEFAULT 1.0,
    run_id      TEXT,
    created_at  TEXT NOT NULL,
    superseded  INTEGER NOT NULL DEFAULT 0
);
"""


@pytest.fixture
def old_database(tmp_path):
    """A database written before the new columns existed."""
    path = tmp_path / "old.sqlite3"
    conn = sqlite3.connect(str(path))
    conn.executescript(OLD_SCHEMA)
    conn.execute(
        "INSERT INTO memories (subject, value, kind, scope, source, confidence, created_at) "
        "VALUES ('user.os', 'Windows', 'FACT', 'possessions', 'he said so', 1.0, "
        "'2026-08-01T09:00:00+00:00')")
    conn.commit()
    conn.close()
    return path


def test_an_existing_database_keeps_what_it_learned(old_database):
    store = Store(old_database)
    try:
        rows = store.recall("user.os")
        assert len(rows) == 1
        assert rows[0]["value"] == "Windows"
    finally:
        store.close()


def test_old_rows_get_sensible_provenance(old_database):
    """
    They were confirmed when they were written. Leaving last_confirmed NULL
    would read as "never confirmed", which is worse than approximately right.
    """
    store = Store(old_database)
    try:
        row = store.recall("user.os")[0]
        assert row["evidence_count"] == 1
        assert row["last_confirmed"] == row["created_at"] == "2026-08-01T09:00:00+00:00"
        assert row["observation_id"] is None
    finally:
        store.close()


def test_migrating_twice_is_harmless(old_database):
    for _ in range(3):
        store = Store(old_database)
        store.close()
    store = Store(old_database)
    try:
        assert len(store.recall("user.os")) == 1
    finally:
        store.close()


def test_a_migrated_database_can_still_be_written_to(old_database):
    store = Store(old_database)
    try:
        store.remember("user.editor", "vim", kind=PREFERENCE, source="said so")
        assert store.recall("user.editor")[0]["evidence_count"] == 1
    finally:
        store.close()
