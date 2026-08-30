"""
Gate 5: passing is not promotion.

The lifecycle exists because "its tests passed" and "this is switched on" are
different claims, and a system that writes its own code and then enables it is
a system that grows capabilities nobody chose.
"""

from __future__ import annotations

import textwrap

import pytest

from friday import forge as F
from friday.store import Store

SPEC = {
    "name": "word_count",
    "goal": "count words",
    "inputs": {"text": "string"},
    "outputs": {"words": "integer"},
    "verification": [{"inputs": {"text": "one two three"},
                      "expect": {"words": 3}}],
}

GOOD = "def run(ctx, text):\n    return {'words': len(text.split())}\n"
WRONG = "def run(ctx, text):\n    return {'words': 0}\n"
FORBIDDEN = "import os\ndef run(ctx, text):\n    return {'words': 3}\n"


@pytest.fixture(autouse=True)
def store(tmp_path):
    fresh = Store(tmp_path / "t.db")
    F.reset_store(fresh)
    yield fresh
    F.reset_store(None)
    fresh.close()


@pytest.fixture
def registry(tmp_path):
    return F.Registry(tmp_path / "skills")


def spec(**overrides) -> F.CapabilitySpec:
    return F.CapabilitySpec.from_dict({**SPEC, **overrides})


def submit(registry, source=GOOD, **overrides) -> dict:
    return registry.submit(spec(**overrides), source, provenance="test")


# ---------------------------------------------------------------------------
# Submission promises nothing
# ---------------------------------------------------------------------------


def test_a_submitted_skill_is_only_a_candidate(registry):
    assert submit(registry)["state"] == F.CANDIDATE


def test_a_candidate_carries_its_provenance_and_hash(registry):
    record = submit(registry)
    assert record["source_sha256"] == F.digest(GOOD)
    assert record["provenance"] == "test"
    assert record["version"] == 1
    assert record["verified_at"] is None


def test_risk_is_derived_from_the_spec_not_asserted_by_the_author(registry):
    assert submit(registry)["risk"] == "contained"
    scoped = submit(registry, network="scoped", allowed_hosts=("example.com",))
    assert scoped["risk"] == "moderate"


# ---------------------------------------------------------------------------
# The ladder, and the rungs that cannot be skipped
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_the_happy_path_takes_four_deliberate_steps(registry):
    submit(registry)
    assert registry.verify("word_count")["state"] == F.VERIFIED
    assert registry.register("word_count", actor="boss")["state"] == F.REGISTERED
    enabled = registry.enable("word_count", ("voice",), actor="boss")
    assert enabled["state"] == F.ENABLED
    assert enabled["scopes"] == ["voice"]


def test_a_candidate_cannot_be_enabled_directly(registry):
    submit(registry)
    with pytest.raises(F.ForgeError, match="may only move to"):
        registry.enable("word_count", ("voice",), actor="boss")


@pytest.mark.slow
def test_a_verified_skill_still_cannot_be_enabled_without_registering(registry):
    submit(registry)
    registry.verify("word_count")
    with pytest.raises(F.ForgeError, match="may only move to"):
        registry.enable("word_count", ("voice",), actor="boss")


@pytest.mark.slow
def test_forge_may_not_switch_on_what_forge_wrote(registry):
    submit(registry)
    registry.verify("word_count")
    registry.register("word_count", actor="boss")
    with pytest.raises(F.ForgeError, match="does not get to switch it on"):
        registry.enable("word_count", ("voice",), actor="forge")


@pytest.mark.slow
def test_enabling_without_a_scope_is_refused(registry):
    submit(registry)
    registry.verify("word_count")
    registry.register("word_count", actor="boss")
    with pytest.raises(F.ForgeError, match="ambient authority"):
        registry.enable("word_count", (), actor="boss")


# ---------------------------------------------------------------------------
# Verification is a real check, not a rubber stamp
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_a_skill_that_fails_its_own_cases_is_rejected(registry):
    submit(registry, WRONG)
    record = registry.verify("word_count")
    assert record["state"] == F.REJECTED
    assert record["verification"]["cases"][0]["got"] == {"words": 0}


def test_a_skill_the_static_gate_refuses_is_rejected(registry):
    submit(registry, FORBIDDEN)
    record = registry.verify("word_count")
    assert record["state"] == F.REJECTED
    assert any("os" in f for f in record["verification"]["findings"])


def test_rejection_is_terminal(registry):
    submit(registry, FORBIDDEN)
    registry.verify("word_count")
    with pytest.raises(F.ForgeError, match="nowhere"):
        registry.register("word_count", actor="boss")


def test_code_changed_after_submission_is_rejected_not_verified(registry):
    """
    A verification that refers to code which has since changed is worse than
    none: it carries the authority of a check nobody ran on this code.
    """
    record = submit(registry)
    from pathlib import Path

    Path(record["source_path"]).write_text(FORBIDDEN, encoding="utf-8")
    after = registry.verify("word_count")
    assert after["state"] == F.REJECTED
    assert "not what was submitted" in registry.history("word_count")[-1]["reason"]


# ---------------------------------------------------------------------------
# Nothing changes state quietly
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_every_transition_is_recorded_with_who_and_why(registry):
    submit(registry)
    registry.verify("word_count")
    registry.register("word_count", actor="boss")
    registry.enable("word_count", ("voice",), actor="boss")

    history = registry.history("word_count")
    assert [h["to_state"] for h in history] == [
        F.CANDIDATE, F.VERIFIED, F.REGISTERED, F.ENABLED]
    assert history[-1]["actor"] == "boss"
    assert "voice" in history[-1]["reason"]
    assert all(h["at"] for h in history)


@pytest.mark.slow
def test_a_new_version_is_never_born_enabled(registry):
    """The scopes belonged to code that no longer exists."""
    submit(registry)
    registry.verify("word_count")
    registry.register("word_count", actor="boss")
    registry.enable("word_count", ("voice",), actor="boss")

    again = registry.submit(spec(), GOOD + "# changed\n", provenance="test")
    assert again["state"] == F.CANDIDATE
    assert again["scopes"] == []
    assert again["version"] == 2


def test_verifying_something_that_does_not_exist_fails_clearly(registry):
    with pytest.raises(F.ForgeError, match="no forged skill"):
        registry.verify("ghost")
