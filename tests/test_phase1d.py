"""
Phase 1D: durable memory with provenance.

The two load-bearing tests:
  - test_memory_survives_a_real_process_restart  (§13)
  - test_recall_falls_back_when_the_exact_key_is_unknown  (regression)
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from friday import contracts as c
from friday import policy as p
from friday.store import FACT, INFERENCE, PATTERN, PREFERENCE, Store
from friday.toolsets import memory as M
from friday.toolsets.system import needs_approval

ROOT = Path(__file__).resolve().parent.parent
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
PYTHON = str(PYTHON if PYTHON.exists() else sys.executable)


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "ada.sqlite3")
    M.reset_store(s)
    yield s
    M.reset_store(None)


@pytest.fixture
def run():
    return c.Run.create("test", capability="memory")


@pytest.fixture
def engine():
    """Guarded explicitly; the default is now full autonomy."""
    return p.PolicyEngine(autonomy=p.GUARDED)


# ---------------------------------------------------------------------------
# §13 — survives a real restart
# ---------------------------------------------------------------------------


def test_memory_survives_a_real_process_restart(tmp_path):
    """
    A new Store object in the same process proves nothing about durability.
    A second python.exe reading what the first wrote does.
    """
    db = tmp_path / "restart.sqlite3"

    write = textwrap.dedent(f"""
        import os, sys
        sys.path.insert(0, r"{ROOT}")
        os.environ["ADA_DB"] = r"{db}"
        from friday import contracts as c
        from friday.store import FACT
        from friday.toolsets import memory as M
        run = c.Run.create("remember", capability="memory")
        r = M.memory_remember(run, "Project Arc Reactor.language", "Python",
                              kind=FACT, source="user stated it")
        M.reset_store(None)
        sys.exit(0 if r.status == "succeeded" else 1)
    """)
    assert subprocess.run([PYTHON, "-c", write], cwd=str(ROOT)).returncode == 0
    assert db.exists() and db.stat().st_size > 0

    read = textwrap.dedent(f"""
        import os, sys
        sys.path.insert(0, r"{ROOT}")
        os.environ["ADA_DB"] = r"{db}"
        from friday import contracts as c
        from friday.toolsets import memory as M
        run = c.Run.create("recall", capability="memory")
        r = M.memory_recall(run, "Project Arc Reactor.language")
        M.reset_store(None)
        row = r.output["memories"][0]
        ok = (r.status == "succeeded" and row["value"] == "Python"
              and row["kind"] == "FACT" and row["source"] == "user stated it")
        sys.exit(0 if ok else 1)
    """)
    assert subprocess.run([PYTHON, "-c", read], cwd=str(ROOT)).returncode == 0, (
        "a fresh process could not recall the persisted fact with provenance"
    )


# ---------------------------------------------------------------------------
# Regression: exact-match-only recall was honest but useless
# ---------------------------------------------------------------------------


def test_recall_falls_back_when_the_exact_key_is_unknown(store, run):
    """
    The agent asked for "Arc Reactor language" while the row was keyed
    "Project Arc Reactor.language". Exact matching found nothing, so the
    agent correctly reported nothing was recorded - honest, and useless.
    """
    M.memory_remember(run, "Project Arc Reactor.language", "Python",
                      kind=FACT, source="user stated it")

    exact = M.memory_recall(run, "Project Arc Reactor.language")
    assert exact.output["match_type"] == "exact"

    fuzzy = M.memory_recall(run, "Arc Reactor language")
    assert fuzzy.status == c.SUCCEEDED, "fuzzy fallback did not fire"
    assert fuzzy.output["match_type"] == "fuzzy"
    assert fuzzy.output["memories"][0]["value"] == "Python"
    assert "fuzzy" in fuzzy.verification.evidence


def test_fuzzy_fallback_does_not_invent_matches(store, run):
    M.memory_remember(run, "Project Arc Reactor.language", "Python",
                      kind=FACT, source="user")
    result = M.memory_recall(run, "quantum entanglement schedule")
    assert result.status == c.FAILED
    assert not result.may_claim_completion


def test_fuzzy_ranks_the_best_match_first(store, run):
    M.memory_remember(run, "Project Arc Reactor.language", "Python",
                      kind=FACT, source="user")
    M.memory_remember(run, "Project Arc Reactor.database", "SQLite",
                      kind=FACT, source="user")
    result = M.memory_recall(run, "Arc Reactor language")
    assert result.output["memories"][0]["value"] == "Python"


# ---------------------------------------------------------------------------
# Provenance survives into speech
# ---------------------------------------------------------------------------


def test_a_fact_and_an_inference_are_not_spoken_the_same_way(store, run):
    M.memory_remember(run, "user.city", "Delhi", kind=FACT, source="user said so")
    M.memory_remember(run, "user.timezone", "IST", kind=INFERENCE,
                      source="guessed from timestamps", confidence=0.6)

    fact = M.memory_recall(run, "user.city").output["memories"][0]["spoken_form"]
    inferred = M.memory_recall(run, "user.timezone").output["memories"][0]["spoken_form"]

    assert fact.startswith("You told me")
    assert "inferred" not in fact
    assert "inferred" in inferred and "60%" in inferred


def test_low_confidence_is_hedged_further(store, run):
    M.memory_remember(run, "user.mood", "focused", kind=INFERENCE,
                      source="tone of messages", confidence=0.3)
    spoken = M.memory_recall(run, "user.mood").output["memories"][0]["spoken_form"]
    assert spoken.startswith("I'm not certain")


@pytest.mark.parametrize("kind, opener", [
    (FACT, "You told me"),
    (PREFERENCE, "You prefer"),
    (PATTERN, "I've noticed"),
    (INFERENCE, "I worked out"),
])
def test_every_kind_has_its_own_phrasing(kind, opener):
    row = {"subject": "s", "value": "v", "kind": kind, "confidence": 1.0}
    assert M.spoken_form(row).startswith(opener)


def test_remember_rejects_an_unknown_kind(store, run):
    result = M.memory_remember(run, "s", "v", kind="VIBES", source="x")
    assert result.status == c.FAILED
    assert "unknown memory kind" in result.error


def test_remember_requires_subject_and_value(store, run):
    assert M.memory_remember(run, "", "v", source="x").status == c.FAILED
    assert M.memory_remember(run, "s", "  ", source="x").status == c.FAILED


def test_remember_verifies_by_reading_back(store, run):
    result = M.memory_remember(run, "k", "v", kind=FACT, source="user")
    assert result.verification.method == "memory_readback"
    assert "read back from" in result.verification.evidence


# ---------------------------------------------------------------------------
# Forgetting marks, never deletes
# ---------------------------------------------------------------------------


def test_forget_is_ask_gated(store, run, engine):
    M.memory_remember(run, "k", "v", source="user")
    result = M.memory_forget(run, "k", engine=engine)
    assert needs_approval(result)
    assert not result.may_claim_completion


def test_forget_supersedes_but_retains_history(store, run, engine):
    engine.approve_for_session("memory.forget")
    M.memory_remember(run, "k", "v", source="user")
    result = M.memory_forget(run, "k", engine=engine)
    assert result.status == c.SUCCEEDED
    assert M.memory_recall(run, "k").status == c.FAILED          # not current
    assert store.recall("k", include_superseded=True)            # still on record
    assert result.output["retained_in_history"] is True


def test_forget_unknown_subject_fails(store, run, engine):
    engine.approve_for_session("memory.forget")
    assert M.memory_forget(run, "never-stored", engine=engine).status == c.FAILED


# ---------------------------------------------------------------------------
# Projects, recap, utterances
# ---------------------------------------------------------------------------


def test_project_context_gathers_memories_and_decisions(store, run):
    M.memory_remember(run, "Project Arc Reactor.language", "Python",
                      kind=FACT, source="user")
    M.project_record_decision(run, "Arc Reactor", "Use SQLite",
                              rationale="JSON trimmed old entries")
    result = M.project_context(run, "Arc Reactor")
    assert result.status == c.SUCCEEDED
    assert result.output["counts"]["memories"] >= 1
    assert result.output["counts"]["decisions"] == 1


def test_session_recap_is_not_destructive(store, run):
    """Mark-L's pop_last_session consumed the entry on read."""
    store.add_message("conv-1", "user", "hello")
    store.close_conversation("conv-1", "we said hello")

    first = M.session_recap(run)
    second = M.session_recap(run)
    assert first.status == c.SUCCEEDED and second.status == c.SUCCEEDED
    assert (len(first.output["conversations"])
            == len(second.output["conversations"]) == 1)


def test_recap_with_no_history_fails_rather_than_inventing(store, run):
    assert M.session_recap(run).status == c.FAILED


def test_raw_utterance_is_preserved_alongside_the_correction(store, run):
    result = M.record_utterance(
        run, "start plot code", normalized="start Claude Code",
        reason="STT confusion", confidence=0.82,
    )
    assert result.status == c.SUCCEEDED
    assert result.output["raw"] == "start plot code"
    assert result.output["normalized"] == "start Claude Code"
    assert "preserved" in result.verification.evidence


def test_memory_results_declare_agent_runtime_scope(store, run):
    """The database is the agent's, not the user's filesystem."""
    result = M.memory_remember(run, "k", "v", source="user")
    assert result.output["execution_scope"] == "agent_runtime"


def test_memory_policy_defaults(engine):
    assert engine.decide("memory.recall").decision == p.AUTO
    assert engine.decide("memory.search").decision == p.AUTO
    assert engine.decide("memory.remember").decision == p.AUTO
    assert engine.decide("memory.forget").decision == p.ASK
