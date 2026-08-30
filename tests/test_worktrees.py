"""
Development happens somewhere else, and only arrives if it earns it.

Real git repositories throughout - a worktree test that mocks git tests
nothing, because every interesting failure here is git's.

The invariant under all of it: **the main checkout is untouched until
promotion**, and a rejection moves nothing at all.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from friday.executors import worktrees as W


def run(cwd, *args):
    out = subprocess.run(args, cwd=str(cwd), capture_output=True, text=True)
    assert out.returncode == 0, f"{' '.join(args)}: {out.stderr}"
    return out.stdout.strip()


@pytest.fixture
def repo(tmp_path):
    """A repository on a feature branch, with work not on the default branch."""
    path = tmp_path / "proj"
    path.mkdir()
    (path / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    run(path, "git", "init", "-q")
    run(path, "git", "config", "user.email", "ada@example.com")
    run(path, "git", "config", "user.name", "ADA")
    run(path, "git", "add", "-A")
    run(path, "git", "commit", "-q", "-m", "seed")
    return path


@pytest.fixture
def manager(repo):
    return W.WorktreeManager(repo)


def make_worktree(manager, name, base=None):
    """What the CLI does, done here so the tests need no Claude."""
    path = manager.path_for(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    run(manager.repo, "git", "worktree", "add", "-q", "-b",
        manager.branch_for(name), str(path), base or "HEAD")
    return path


# ---------------------------------------------------------------------------
# Where things are
# ---------------------------------------------------------------------------


def test_the_path_matches_what_the_cli_uses(manager):
    """Probed against 2.1.233: <repo>/.claude/worktrees/<name>."""
    assert manager.path_for("ada-x") == manager.repo / ".claude" / "worktrees" / "ada-x"


def test_the_branch_matches_what_the_cli_makes(manager):
    assert manager.branch_for("ada-x") == "worktree-ada-x"


def test_a_worktree_is_found_once_it_exists(manager):
    assert manager.find("ada-x") is None
    make_worktree(manager, "ada-x")
    found = manager.find("ada-x")
    assert found is not None and found.branch == "worktree-ada-x"


# ---------------------------------------------------------------------------
# Verification, which is what stops an isolated run becoming unisolated
# ---------------------------------------------------------------------------


def test_a_real_worktree_verifies(manager):
    make_worktree(manager, "ada-x")
    ok, why = manager.verify("ada-x")
    assert ok, why


def test_a_missing_worktree_does_not_verify(manager):
    ok, why = manager.verify("ada-gone")
    assert not ok and "gone" in why


def test_a_deleted_worktree_does_not_verify(manager):
    import shutil

    make_worktree(manager, "ada-x")
    shutil.rmtree(manager.path_for("ada-x"))
    assert not manager.verify("ada-x")[0]


def test_a_directory_that_is_not_a_checkout_does_not_verify(manager):
    manager.path_for("ada-fake").mkdir(parents=True)
    assert not manager.verify("ada-fake")[0]


def test_resuming_without_a_worktree_is_refused(manager):
    """
    The dangerous case. The CLI can fall back to the launch directory, so a
    run that still believes it is isolated would start editing the live
    checkout.
    """
    safe, why = W.guard_before_resume(manager, "ada-gone")
    assert not safe
    assert "main checkout" in why


def test_resuming_with_an_intact_worktree_is_allowed(manager):
    make_worktree(manager, "ada-x")
    assert W.guard_before_resume(manager, "ada-x")[0]


# ---------------------------------------------------------------------------
# Isolation
# ---------------------------------------------------------------------------


def test_work_in_the_worktree_does_not_touch_the_main_checkout(manager):
    path = make_worktree(manager, "ada-x")
    (path / "isolated.py").write_text("HELLO = 1\n", encoding="utf-8")

    assert not (manager.repo / "isolated.py").exists()
    tracked = [line for line in
               run(manager.repo, "git", "status", "--porcelain").splitlines()
               if not line.strip().startswith("??")]
    assert tracked == [], "the main checkout has modified tracked files"


def test_uncommitted_work_is_seen(manager):
    path = make_worktree(manager, "ada-x")
    (path / "isolated.py").write_text("HELLO = 1\n", encoding="utf-8")
    assert "isolated.py" in manager.changes("ada-x")


# ---------------------------------------------------------------------------
# Committing, because a headless run leaves the work uncommitted
# ---------------------------------------------------------------------------


def test_the_runs_work_is_committed_before_it_can_move(manager):
    """
    Probed: `claude -p --worktree` leaves changes uncommitted. Without this
    there is nothing for a merge to carry.
    """
    path = make_worktree(manager, "ada-x")
    (path / "isolated.py").write_text("HELLO = 1\n", encoding="utf-8")
    sha = manager.commit("ada-x", "ADA: add isolated.py")
    assert sha
    assert manager.changes("ada-x") == []


def test_committing_nothing_is_not_an_error(manager):
    make_worktree(manager, "ada-x")
    assert manager.commit("ada-x", "nothing to do") == ""


def test_committing_into_a_missing_worktree_raises(manager):
    with pytest.raises(W.WorktreeError):
        manager.commit("ada-gone", "x")


# ---------------------------------------------------------------------------
# Promote
# ---------------------------------------------------------------------------


def test_promotion_moves_the_work_onto_the_target(manager):
    path = make_worktree(manager, "ada-x")
    (path / "isolated.py").write_text("HELLO = 1\n", encoding="utf-8")

    promotion = manager.promote("ada-x", target=manager.current_branch(),
                                message="ADA: add isolated.py")
    assert promotion.state == W.PROMOTED
    assert (manager.repo / "isolated.py").exists()
    assert promotion.merge_commit and promotion.base_commit


def test_promotion_records_where_to_get_back_to(manager):
    """Worked out later is worked out at the worst possible moment."""
    before = run(manager.repo, "git", "rev-parse", "HEAD")
    path = make_worktree(manager, "ada-x")
    (path / "isolated.py").write_text("HELLO = 1\n", encoding="utf-8")

    promotion = manager.promote("ada-x", target=manager.current_branch(),
                                message="ADA: add isolated.py")
    assert promotion.rollback_target == before


def test_promoting_nothing_is_a_rejection(manager):
    make_worktree(manager, "ada-x")
    promotion = manager.promote("ada-x", target=manager.current_branch(),
                                message="nothing")
    assert promotion.state == W.REJECTED
    assert "nothing to promote" in promotion.reason


def test_promotion_refuses_to_switch_branches_underneath_anyone(manager):
    path = make_worktree(manager, "ada-x")
    (path / "isolated.py").write_text("HELLO = 1\n", encoding="utf-8")
    with pytest.raises(W.WorktreeError, match="never switches branches"):
        manager.promote("ada-x", target="some-other-branch", message="x")


def test_a_conflicting_promotion_leaves_the_target_exactly_as_it_was(manager):
    """A conflict is a rejection, never a half-promotion."""
    path = make_worktree(manager, "ada-x")
    (path / "calc.py").write_text("def add(a, b):\n    return a - b  # worktree\n",
                                  encoding="utf-8")
    manager.commit("ada-x", "worktree edit")

    (manager.repo / "calc.py").write_text(
        "def add(a, b):\n    return a * b  # main\n", encoding="utf-8")
    run(manager.repo, "git", "commit", "-qam", "main edit")
    before = run(manager.repo, "git", "rev-parse", "HEAD")

    promotion = manager.promote("ada-x", target=manager.current_branch(),
                                message="conflicting")
    assert promotion.state == W.REJECTED
    assert run(manager.repo, "git", "rev-parse", "HEAD") == before
    assert "main" in (manager.repo / "calc.py").read_text(encoding="utf-8")
    # No half-merged tracked files left behind. The untracked .claude/ dir is
    # the worktree itself and is expected to be there.
    tracked = [line for line in
               run(manager.repo, "git", "status", "--porcelain").splitlines()
               if not line.strip().startswith("??")]
    assert tracked == []


# ---------------------------------------------------------------------------
# Reject
# ---------------------------------------------------------------------------


def test_rejection_moves_nothing(manager):
    path = make_worktree(manager, "ada-x")
    (path / "isolated.py").write_text("HELLO = 1\n", encoding="utf-8")
    before = run(manager.repo, "git", "rev-parse", "HEAD")

    promotion = manager.reject("ada-x", "the tests failed")
    assert promotion.state == W.REJECTED
    assert run(manager.repo, "git", "rev-parse", "HEAD") == before
    assert not (manager.repo / "isolated.py").exists()


def test_a_rejected_worktree_can_be_kept_as_evidence(manager):
    make_worktree(manager, "ada-x")
    report = manager.cleanup("ada-x", keep=True)
    assert report["kept"] and not report["removed"]
    assert manager.path_for("ada-x").is_dir()


# ---------------------------------------------------------------------------
# Rollback
# ---------------------------------------------------------------------------


def test_rollback_restores_the_state_from_what_was_recorded(manager):
    path = make_worktree(manager, "ada-x")
    (path / "isolated.py").write_text("HELLO = 1\n", encoding="utf-8")
    promotion = manager.promote("ada-x", target=manager.current_branch(),
                                message="ADA: add isolated.py")
    assert (manager.repo / "isolated.py").exists()

    rolled = manager.rollback(promotion, reason="it broke production")
    assert rolled.state == W.ROLLED_BACK
    assert not (manager.repo / "isolated.py").exists()


def test_rollback_reverts_rather_than_resetting(manager):
    """
    History stays. `reset --hard` is on the list no profile may run, and it
    should not sneak in through the back door either.
    """
    path = make_worktree(manager, "ada-x")
    (path / "isolated.py").write_text("HELLO = 1\n", encoding="utf-8")
    promotion = manager.promote("ada-x", target=manager.current_branch(),
                                message="ADA: add isolated.py")
    before_count = int(run(manager.repo, "git", "rev-list", "--count", "HEAD"))

    manager.rollback(promotion)
    after_count = int(run(manager.repo, "git", "rev-list", "--count", "HEAD"))
    assert after_count > before_count, "history was rewritten instead of reverted"
    assert promotion.merge_commit in run(
        manager.repo, "git", "log", "--format=%H")


def test_only_a_completed_promotion_can_be_rolled_back(manager):
    with pytest.raises(W.WorktreeError, match="only a completed promotion"):
        manager.rollback(W.Promotion(W.REJECTED, "b", "main"))


# ---------------------------------------------------------------------------
# Cleanup, because a headless run cleans up nothing
# ---------------------------------------------------------------------------


def test_cleanup_removes_the_worktree_and_the_branch(manager):
    make_worktree(manager, "ada-x")
    report = manager.cleanup("ada-x")
    assert report["removed"] and report["branch_deleted"]
    assert not manager.path_for("ada-x").exists()
    assert "worktree-ada-x" not in run(
        manager.repo, "git", "branch", "--format=%(refname:short)")


def test_cleanup_clears_the_lock_a_finished_session_left(manager):
    """
    The CLI locks the worktree with "claude session <name> (pid NNNN)" and a
    headless run never unlocks it, so the lock outlives the process and blocks
    removal.
    """
    make_worktree(manager, "ada-x")
    run(manager.repo, "git", "worktree", "lock",
        str(manager.path_for("ada-x")), "--reason", "claude session ada-x (pid 1)")

    report = manager.cleanup("ada-x")
    assert report["removed"], report["notes"]


def test_cleaning_up_twice_is_harmless(manager):
    make_worktree(manager, "ada-x")
    manager.cleanup("ada-x")
    report = manager.cleanup("ada-x")
    assert "already gone" in " ".join(report["notes"])


def test_promoted_work_survives_cleanup(manager):
    """Removing the worktree must not remove what was promoted out of it."""
    path = make_worktree(manager, "ada-x")
    (path / "isolated.py").write_text("HELLO = 1\n", encoding="utf-8")
    manager.promote("ada-x", target=manager.current_branch(), message="ADA")
    manager.cleanup("ada-x")
    assert (manager.repo / "isolated.py").exists()


def test_stale_worktrees_are_findable(manager):
    make_worktree(manager, "ada-x")
    make_worktree(manager, "ada-y")
    assert set(manager.stale()) == {"ada-x", "ada-y"}


# ---------------------------------------------------------------------------
# The state machine
# ---------------------------------------------------------------------------


def test_every_state_is_named():
    for state in (W.WORKTREE_CREATED, W.DEVELOPING, W.VERIFYING, W.READY,
                  W.REJECTED, W.PROMOTING, W.PROMOTED, W.ROLLED_BACK):
        assert state in W.PROMOTION_STATES


def test_a_promotion_is_persisted_with_its_rollback_point(tmp_path, repo):
    from friday.store import Store

    store = Store(tmp_path / "p.sqlite3")
    try:
        manager = W.WorktreeManager(repo)
        path = make_worktree(manager, "ada-x")
        (path / "isolated.py").write_text("HELLO = 1\n", encoding="utf-8")
        promotion = manager.promote("ada-x", target=manager.current_branch(),
                                    message="ADA")
        store.record_promotion("DEV-1", worktree="ada-x", promotion=promotion)

        row = store.latest_promotion("DEV-1")
        assert row["state"] == W.PROMOTED
        assert row["rollback_target"] == promotion.base_commit
        assert row["merge_commit"] == promotion.merge_commit
        assert row["target_branch"] == manager.current_branch()
    finally:
        store.close()
