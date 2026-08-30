"""
Deciding from evidence instead of from "Opus is probably best for this".

The dangerous failure here is a router that swings on one lucky run and looks
informed while doing it. Most of these tests are about refusing to conclude.
"""
import pytest

from friday import evaluation as E


def _attempt(agent, verdict, seconds=10.0, task="build", model="", tokens=0):
    return E.Attempt(task=task, agent=agent, model=model, verdict=verdict,
                     seconds=seconds, tokens=tokens,
                     exit_code=0 if verdict == E.PASSED else 1)


@pytest.fixture
def record():
    return E.Record()


# --- scoring --------------------------------------------------------------

def test_a_pass_rate_is_computed_from_decided_attempts(record):
    for verdict in (E.PASSED, E.PASSED, E.FAILED):
        record.add(_attempt("claude", verdict))
    scored = record.scored(agent="claude")
    assert scored["attempts"] == 3
    assert scored["passed"] == 2
    assert scored["pass_rate"] == pytest.approx(0.667, abs=0.01)


def test_inconclusive_attempts_are_counted_and_excluded(record):
    """
    A setup that fails to run half the time is a real problem with that
    setup. Dropping those rows flatters the survivor.
    """
    record.add(_attempt("claude", E.PASSED))
    record.add(_attempt("claude", E.INCONCLUSIVE))
    scored = record.scored(agent="claude")
    assert scored["attempts"] == 2
    assert scored["decided"] == 1
    assert scored["inconclusive"] == 1
    assert scored["pass_rate"] == 1.0


def test_nothing_recorded_yields_no_rate_rather_than_zero(record):
    """A rate of 0.0 reads as "always fails"; None reads as "no idea"."""
    assert record.scored(agent="nobody")["pass_rate"] is None


def test_timing_is_measured_over_passing_attempts_only(record):
    record.add(_attempt("claude", E.PASSED, seconds=10))
    record.add(_attempt("claude", E.PASSED, seconds=20))
    record.add(_attempt("claude", E.FAILED, seconds=1))
    assert record.scored(agent="claude")["median_seconds"] == 15.0


def test_scoring_can_be_narrowed_to_a_model(record):
    record.add(_attempt("claude", E.PASSED, model="opus"))
    record.add(_attempt("claude", E.FAILED, model="haiku"))
    assert record.scored(model="opus")["pass_rate"] == 1.0
    assert record.scored(model="haiku")["pass_rate"] == 0.0


# --- refusing to conclude -------------------------------------------------

def test_one_lucky_run_does_not_decide_anything(record):
    """
    A router that swings on a single result is worse than a fixed default,
    because it looks informed.
    """
    record.add(_attempt("claude", E.PASSED))
    assert record.best_for("build") is None


def test_a_verdict_needs_the_minimum_number_of_attempts(record):
    for _ in range(2):
        record.add(_attempt("claude", E.PASSED))
    assert record.best_for("build", minimum=3) is None
    record.add(_attempt("claude", E.PASSED))
    assert record.best_for("build", minimum=3) == "claude"


def test_correctness_outranks_speed(record):
    """A fast wrong answer must never beat a slow right one."""
    for _ in range(3):
        record.add(_attempt("quick", E.FAILED, seconds=1))
        record.add(_attempt("slow", E.PASSED, seconds=100))
    assert record.best_for("build") == "slow"


def test_speed_only_separates_equals(record):
    for _ in range(3):
        record.add(_attempt("brisk", E.PASSED, seconds=5))
        record.add(_attempt("plodding", E.PASSED, seconds=50))
    assert record.best_for("build") == "brisk"


def test_evidence_for_one_task_does_not_decide_another(record):
    for _ in range(3):
        record.add(_attempt("claude", E.PASSED, task="build"))
    assert record.best_for("build") == "claude"
    assert record.best_for("refactor") is None


def test_compare_ranks_agents_for_a_human_to_read(record):
    for _ in range(3):
        record.add(_attempt("good", E.PASSED))
        record.add(_attempt("bad", E.FAILED))
    rows = record.compare("build")
    assert [r["agent"] for r in rows] == ["good", "bad"]


# --- persistence ----------------------------------------------------------

def test_a_saved_record_loads_back(record, tmp_path):
    record.add(_attempt("claude", E.PASSED, tokens=1200))
    out = record.save(tmp_path / "e" / "attempts.json")

    again = E.Record.load(out)
    assert len(again.attempts) == 1
    assert again.attempts[0].agent == "claude"
    assert again.attempts[0].tokens == 1200


def test_a_missing_record_starts_empty(tmp_path):
    assert E.Record.load(tmp_path / "absent.json").attempts == []


def test_a_corrupt_record_starts_empty_rather_than_raising(tmp_path):
    bad = tmp_path / "attempts.json"
    bad.write_text("{not json", encoding="utf-8")
    assert E.Record.load(bad).attempts == []


# --- the verifier contract ------------------------------------------------

def test_a_verifier_describes_what_it_proves():
    verifier = E.Verifier(command=("pytest", "-q"), proves="the suite passes")
    assert verifier.describe() == "pytest -q"
    assert verifier.proves == "the suite passes"


def test_an_attempt_that_did_not_pass_is_not_passed():
    assert not _attempt("claude", E.FAILED).passed
    assert not _attempt("claude", E.INCONCLUSIVE).passed
    assert _attempt("claude", E.PASSED).passed
