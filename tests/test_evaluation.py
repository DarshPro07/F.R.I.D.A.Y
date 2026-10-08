"""
Deciding from evidence instead of from "Opus is probably best for this".

The dangerous failure here is a router that swings on one lucky run and looks
informed while doing it. Most of these tests are about refusing to conclude.
"""
import logging
import sys
from pathlib import Path

import pytest

from friday import evaluation as E


def _attempt(agent, verdict, seconds=10.0, task="build", model="", tokens=0,
             commit="0a1b2c3", fingerprint="f00d"):
    """A versioned attempt: judged at `commit` by the verifier `fingerprint`."""
    return E.Attempt(task=task, agent=agent, model=model, verdict=verdict,
                     seconds=seconds, tokens=tokens,
                     exit_code=0 if verdict == E.PASSED else 1,
                     build_commit=commit, verifier_fingerprint=fingerprint)


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


# --- graded: verify, then record where the router reads -------------------

def _verifier(code: str) -> E.Verifier:
    return E.Verifier(command=(sys.executable, "-c", code))


@pytest.fixture
def workspace(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    return work


def test_the_suite_never_writes_the_live_record():
    """
    Every row in the boss's record was a test fixture: 120 attempts by
    "scripted" on "widget", "greeter" and "feature", appended by each suite
    run that graded work without a record of its own. The router reads that
    file, so `conftest._evaluation_record_is_per_test` points it elsewhere.
    """
    from friday.config import DATA_DIR

    live = Path(DATA_DIR) / "evaluation" / "attempts.json"
    assert E.record_path().resolve() != live.resolve()


def _git(workspace, *args) -> str:
    import subprocess

    return subprocess.run(
        ("git", "-c", "user.name=t", "-c", "user.email=t@t", *args),
        cwd=workspace, capture_output=True, text=True, check=True,
    ).stdout.strip()


def _committed(workspace) -> str:
    """Make the workspace a repository with one commit; its short head."""
    _git(workspace, "init", "-q")
    (workspace / "t.py").write_text("pass\n")
    _git(workspace, "add", ".")
    _git(workspace, "commit", "-q", "-m", "one")
    return _git(workspace, "rev-parse", "--short", "HEAD")


def test_graded_work_is_persisted_where_the_router_reads_it(workspace):
    """
    With no record of its own an attempt is still evidence: three passes
    are enough for `best_for` to name the agent on the next load.
    """
    _committed(workspace)
    for _ in range(3):
        E.graded("build", "claude", workspace, _verifier("pass"))
    record = E.Record.load(E.record_path())
    assert [a.verdict for a in record.attempts] == [E.PASSED] * 3
    assert record.best_for("build") == "claude"


def test_unversioned_passes_do_not_steer_the_router(workspace, record):
    """
    The live record once held 120 test-fixture rows with no build or
    verifier on them. Graded outside a repository, or written before the
    stamp, a pass names nothing that can be re-run and routes nothing.
    """
    for _ in range(3):
        E.graded("build", "claude", workspace, _verifier("pass"), record=record)
    assert {a.build_commit for a in record.attempts} == {"unknown"}
    for _ in range(3):
        record.add(E.Attempt(task="build", agent="codex", verdict=E.PASSED,
                             exit_code=0))
    assert not any(a.versioned for a in record.attempts)
    assert record.best_for("build") is None


def test_a_graded_attempt_is_versioned_and_linked_to_the_build(workspace):
    """An eval names the build it judged and the command that judged it."""
    head = _committed(workspace)

    verifier = E.Verifier(command=(sys.executable, "t.py"), seconds=30.0,
                          proves="the file runs")
    first = E.graded("build", "claude", workspace, verifier)
    # The stamp is the workspace's own commit, not the Friday process's.
    assert first.build_commit == head
    assert first.build_dirty is False
    assert first.verifier_command == verifier.describe()
    assert first.verifier_seconds == 30.0
    assert first.verifier_proves == "the file runs"
    stored = E.Record.load(E.record_path()).attempts[0]
    assert stored.build_commit == head
    assert stored.verifier_fingerprint == first.verifier_fingerprint != ""

    # A later attempt in the same process sees the new state: not cached.
    (workspace / "t.py").write_text("pass  # edited\n")
    second = E.graded("build", "claude", workspace, verifier)
    assert second.build_dirty is True
    # Editing the file the verifier runs changes the verifier's identity.
    assert second.verifier_fingerprint != first.verifier_fingerprint


def test_a_workspace_that_is_not_a_repo_is_stamped_unknown(workspace):
    attempt = E.graded("build", "claude", workspace, _verifier("pass"))
    assert attempt.build_commit == "unknown"


def test_a_failing_verifier_is_recorded_as_failed(workspace):
    attempt = E.graded("build", "claude", workspace,
                       _verifier("import sys; sys.exit(3)"))
    assert attempt.verdict == E.FAILED and attempt.exit_code == 3
    assert not attempt.passed
    scored = E.Record.load(E.record_path()).scored(agent="claude")
    assert scored["decided"] == 1 and scored["pass_rate"] == 0.0


def test_a_verifier_that_cannot_start_is_inconclusive_not_failed(workspace):
    """
    A missing interpreter says nothing about the code. Grading it FAILED
    would drag the agent's pass rate down for a fault of the machine.
    """
    attempt = E.graded("build", "claude", workspace, E.Verifier(
        command=(str(workspace / "no-such-verifier.exe"),)))
    assert attempt.verdict == E.INCONCLUSIVE and attempt.exit_code == 127
    scored = E.Record.load(E.record_path()).scored(agent="claude")
    assert scored["inconclusive"] == 1 and scored["pass_rate"] is None


def test_a_caller_held_record_is_not_counted_twice(workspace):
    """The caller's record is the record; the shared file is not fed too."""
    record = E.Record()
    E.graded("build", "claude", workspace, _verifier("pass"), record=record)
    assert len(record.attempts) == 1
    assert not E.record_path().exists()


def test_an_unwritable_record_does_not_lose_the_attempt(workspace, monkeypatch,
                                                         caplog):
    """A full disk or a locked file is logged; the verdict still comes back."""
    blocker = workspace / "blocker"
    blocker.write_text("a file where the directory should be", encoding="utf-8")
    monkeypatch.setattr(E, "record_path", lambda: blocker / "attempts.json")
    with caplog.at_level(logging.ERROR, logger="friday-agent"):
        attempt = E.graded("build", "claude", workspace, _verifier("pass"))
    assert attempt.verdict == E.PASSED
    assert "could not persist the attempt" in caplog.text


# --- model promotion (MODEL-003): only on reproducible evidence -----------

def _scores(record, model, passed, failed, fingerprint="f00d", commit="0a1b2c3"):
    for verdict in [E.PASSED] * passed + [E.FAILED] * failed:
        record.add(_attempt("hermes", verdict, model=model,
                            commit=commit, fingerprint=fingerprint))


def test_a_model_that_beats_the_baseline_under_one_verifier_is_promoted(record):
    _scores(record, "candidate", passed=3, failed=0)
    _scores(record, "baseline", passed=2, failed=1)
    promotion = record.promote_model("build", "candidate", baseline="baseline")
    assert promotion.allowed and promotion.reason == E.PROMOTED
    assert promotion.verifier_fingerprint == "f00d"
    assert promotion.builds == ("0a1b2c3",)
    assert promotion.candidate_score["pass_rate"] == 1.0
    assert promotion.baseline_score["pass_rate"] == 0.667


def test_unversioned_rows_never_promote_a_model(record):
    """A row that names no build or verifier cannot be re-run."""
    _scores(record, "candidate", passed=5, failed=0, fingerprint="", commit="")
    _scores(record, "baseline", passed=0, failed=5, fingerprint="", commit="")
    promotion = record.promote_model("build", "candidate", baseline="baseline")
    assert not promotion.allowed and promotion.reason == E.NOT_COMPARABLE
    assert promotion.unversioned == 10


def test_pass_rates_under_different_verifiers_are_not_compared(record):
    """Two pass rates under different tests are two different exams."""
    _scores(record, "candidate", passed=3, failed=0, fingerprint="easy")
    _scores(record, "baseline", passed=1, failed=2, fingerprint="hard")
    promotion = record.promote_model("build", "candidate", baseline="baseline")
    assert not promotion.allowed and promotion.reason == E.NOT_COMPARABLE


def test_a_tie_keeps_the_baseline(record):
    _scores(record, "candidate", passed=3, failed=1)
    _scores(record, "baseline", passed=3, failed=1)
    promotion = record.promote_model("build", "candidate", baseline="baseline")
    assert not promotion.allowed
    assert promotion.reason == E.DOES_NOT_BEAT_BASELINE


def test_too_few_attempts_promote_nothing(record):
    _scores(record, "candidate", passed=2, failed=0)
    _scores(record, "baseline", passed=0, failed=3)
    promotion = record.promote_model("build", "candidate", baseline="baseline")
    assert not promotion.allowed
    assert promotion.reason == E.NOT_ENOUGH_EVIDENCE


def test_a_model_is_not_promoted_over_itself(record):
    _scores(record, "same", passed=3, failed=0)
    promotion = record.promote_model("build", "same", baseline="same")
    assert not promotion.allowed and promotion.reason == E.NOT_COMPARABLE


def test_graded_evidence_promotes_a_model(workspace, record):
    """The same graded() path that gates code feeds model promotion."""
    head = _committed(workspace)
    marker = workspace / "works"
    # One verifier for both models; what each model left behind decides it.
    verifier = _verifier(
        f"import os, sys; sys.exit(0 if os.path.exists({str(marker)!r}) else 1)")
    marker.write_text("yes")
    for _ in range(3):
        E.graded("build", "hermes", workspace, verifier, model="candidate",
                 record=record)
    E.graded("build", "hermes", workspace, verifier, model="baseline",
             record=record)
    marker.unlink()
    for _ in range(2):
        E.graded("build", "hermes", workspace, verifier, model="baseline",
                 record=record)
    assert [a.verdict for a in record.attempts] == [E.PASSED] * 4 + [E.FAILED] * 2
    promotion = record.promote_model("build", "candidate", baseline="baseline")
    assert promotion.allowed, promotion.detail
    assert promotion.builds == (head,)
    assert promotion.verifier_fingerprint == verifier.fingerprint(workspace)
