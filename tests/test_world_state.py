"""
PRD v7 WORLD-001 (section 13): structured, provenance-aware world state.

An entity of a PRD-listed type carries its source, timestamp, confidence,
current measurement, expected transitions and invalidation conditions. An
authoritative update supersedes with a link; speculation that disagrees with
authoritative state is kept as a pending row in its workspace and does not
become current ("speculation cannot silently become authoritative").
"""
from __future__ import annotations

import pytest

from friday import workspace as W
from friday.store import FACT, INFERENCE, WORLD_ENTITY_TYPES, Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "world.sqlite3")
    s.register_workspace("brand-a")
    s.register_workspace("brand-b")
    return s


def test_every_listed_entity_type_is_storable_with_its_prd_fields(store):
    for t in ("workspace", "device", "repository", "application", "account",
              "capability"):
        assert t in WORLD_ENTITY_TYPES
    out = store.set_world_state(
        "device", "laptop", "on battery, 40%", kind=FACT, source="battery probe",
        source_ref="probe:7", confidence=0.9,
        expected_transitions="charging when plugged in",
        invalidation="stale after 10 minutes", workspace_id="brand-a")
    row = store.world_state("device", "laptop", workspace_id="brand-a")[0]
    assert row["id"] == out["id"] and out["pending_id"] is None
    assert (row["measurement"], row["source"], row["source_ref"]) == (
        "on battery, 40%", "battery probe", "probe:7")
    assert row["confidence"] == 0.9 and row["observed_at"]
    assert row["expected_transitions"] == "charging when plugged in"
    assert row["invalidation"] == "stale after 10 minutes"
    with pytest.raises(ValueError):
        store.set_world_state("spaceship", "x", "y", kind=FACT, source="s")
    with pytest.raises(ValueError):
        store.set_world_state("device", "x", "y", kind=FACT, source=" ")


def test_an_authoritative_update_supersedes_with_a_link(store):
    old = store.set_world_state("repository", "friday", "branch main @ 0b792c0",
                                kind=FACT, source="git", workspace_id="brand-a")
    new = store.set_world_state("repository", "friday", "branch main @ 432effe",
                                kind=FACT, source="git", workspace_id="brand-a")
    current = store.world_state("repository", "friday", workspace_id="brand-a")
    assert [r["id"] for r in current] == [new["id"]]
    assert current[0]["supersedes_id"] == old["id"]
    assert new["pending_id"] is None


def test_speculation_cannot_silently_become_authoritative(store):
    fact = store.set_world_state("account", "github", "signed in", kind=FACT,
                                 source="gh auth status", workspace_id="brand-a")
    guess = store.set_world_state("account", "github", "signed out",
                                  kind=INFERENCE, source="a 401 in a log",
                                  confidence=0.4, workspace_id="brand-a")
    assert guess["id"] is None and guess["pending_id"]
    current = store.world_state("account", "github", workspace_id="brand-a")
    assert [r["id"] for r in current] == [fact["id"]]
    pending = store.world_state("account", "github", pending=True,
                                workspace_id="brand-a")
    assert [r["id"] for r in pending] == [guess["pending_id"]]
    p = pending[0]
    assert (p["measurement"], p["kind"], p["supersedes_id"]) == (
        "signed out", INFERENCE, fact["id"])
    # The same speculation repeated is one pending row, not a growing pile.
    again = store.set_world_state("account", "github", "signed out",
                                  kind=INFERENCE, source="a 401 in a log",
                                  workspace_id="brand-a")
    assert again["pending_id"] == guess["pending_id"]
    # Agreeing speculation corroborates but never replaces or downgrades fact.
    agree = store.set_world_state("account", "github", "signed in",
                                  kind=INFERENCE, source="cookie present",
                                  workspace_id="brand-a")
    assert agree == {"id": fact["id"], "pending_id": None}
    [row] = store.world_state("account", "github", workspace_id="brand-a")
    assert (row["id"], row["kind"], row["source"]) == (
        fact["id"], FACT, "gh auth status")
    # Speculation with nothing to contradict may stand, visibly INFERENCE.
    first = store.set_world_state("device", "printer", "jammed", kind=INFERENCE,
                                  source="guess", workspace_id="brand-a")
    assert first["id"] and first["pending_id"] is None


def test_a_pending_conflict_stays_in_its_workspace(store):
    store.set_world_state("account", "bank", "signed in", kind=FACT,
                          source="probe", workspace_id="brand-a")
    store.set_world_state("account", "bank", "locked out", kind=INFERENCE,
                          source="guess", workspace_id="brand-a")
    assert store.world_state(pending=True, workspace_id="brand-b") == []
    assert store.contradictions(resolution="pending") == []


def test_empty_key_or_measurement_is_refused(store):
    for key, value in (("", "on"), ("laptop", " ")):
        with pytest.raises(ValueError):
            store.set_world_state("device", key, value, kind=FACT, source="s")

def test_world_state_is_scoped_to_a_workspace(store):
    assert "world_state" in W.SCOPED
    store.set_world_state("device", "phone", "online", kind=FACT, source="ping",
                          workspace_id="brand-a")
    store.set_world_state("device", "phone", "offline", kind=FACT, source="ping",
                          workspace_id="brand-b")
    assert [r["measurement"] for r in
            store.world_state("device", workspace_id="brand-a")] == ["online"]
    assert [r["measurement"] for r in
            store.world_state("device", workspace_id="brand-b")] == ["offline"]
