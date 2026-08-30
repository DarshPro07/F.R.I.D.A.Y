"""
The coding session is disposable. The work is not.

This is the claim the whole executor exists to make good on: kill Claude, kill
ADA, open a conversation that has never heard of any of it, and the run is
still there with everything needed to carry on.

Two invariants underneath:

  * a process exiting is not a verdict. A run whose process died was
    INTERRUPTED, which invites a resume; FAILED invites a shrug.
  * one run_id means at most one live executor. Asking twice must attach, not
    launch a second Claude at the same worktree.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from friday.executors import runs as R
from friday.executors.claude_code import ClaudeCodeExecutor, TaskBundle
from friday.store import Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "runs.sqlite3")
    yield s
    s.close()


@pytest.fixture
def manager(store):
    return R.RunManager(store)


@pytest.fixture
def bundle(tmp_path):
    return TaskBundle(
        goal="build a parser with tests", workspace=str(tmp_path),
        project="halo", run_id="DEV-continuity",
        acceptance=("tests pass",), context=("style: terse",))


# ---------------------------------------------------------------------------
# It is written down before it starts
# ---------------------------------------------------------------------------


def test_a_run_is_recorded_before_anything_happens(manager, bundle, store):
    """A run that dies during startup must still leave something to recover."""
    manager.open(bundle)
    row = store.executor_run("DEV-continuity")
    assert row["status"] == R.STARTING
    assert row["working_directory"] == bundle.workspace
    assert row["project"] == "halo"


def test_the_whole_bundle_is_kept_not_a_summary_of_it(manager, bundle):
    """A resume that has to reconstruct the goal has already lost it."""
    manager.open(bundle)
    restored = manager.bundle_of(manager.store.executor_run("DEV-continuity"))
    assert restored.goal == bundle.goal
    assert restored.acceptance == ("tests pass",)
    assert restored.context == ("style: terse",)
    assert restored.workspace == bundle.workspace


def test_progress_is_heartbeated(manager, bundle, store):
    manager.open(bundle)
    manager.running("DEV-continuity", pid=1234, session_id="sess-a")
    manager.saw("DEV-continuity", "using Edit")
    row = store.executor_run("DEV-continuity")
    assert row["session_id"] == "sess-a"
    assert row["last_event"] == "using Edit"
    assert row["status"] == R.RUNNING


# ---------------------------------------------------------------------------
# A dead process is interrupted, not failed
# ---------------------------------------------------------------------------


def test_a_dead_process_makes_the_run_interrupted(manager, bundle, monkeypatch):
    """
    Nobody was there to write FAILED. Calling it FAILED would be inventing a
    verdict nobody reached.
    """
    monkeypatch.setattr(R, "process_alive", lambda pid, started_at=None: False)
    manager.open(bundle)
    manager.running("DEV-continuity", pid=999999)

    row = manager.reconcile("DEV-continuity")
    assert row["status"] == R.INTERRUPTED
    assert row["pid"] is None


def test_a_live_process_is_left_alone(manager, bundle, monkeypatch):
    monkeypatch.setattr(R, "process_alive", lambda pid, started_at=None: True)
    manager.open(bundle)
    manager.running("DEV-continuity", pid=4321)
    assert manager.reconcile("DEV-continuity")["status"] == R.RUNNING


def test_a_finished_run_is_not_reopened(manager, bundle, monkeypatch):
    from friday import contracts as c

    monkeypatch.setattr(R, "process_alive", lambda pid, started_at=None: False)
    manager.open(bundle)
    result = c.succeeded(
        c.started("RUN-x", "executor.claude_code"),
        output={"claude_says": "done"},
        verification=c.Verification(method="worktree_diff", evidence="1 file"))
    manager.close("DEV-continuity", result)
    assert manager.reconcile("DEV-continuity")["status"] == R.SUCCEEDED


def test_startup_reconciles_every_run_that_thinks_it_is_alive(manager, bundle,
                                                              monkeypatch, tmp_path):
    monkeypatch.setattr(R, "process_alive", lambda pid, started_at=None: False)
    for n in range(3):
        b = TaskBundle(goal=f"task {n}", workspace=str(tmp_path),
                       run_id=f"DEV-{n}", project="halo")
        manager.open(b)
        manager.running(f"DEV-{n}", pid=900000 + n)

    interrupted = manager.reconcile_all()
    assert {row["run_id"] for row in interrupted} == {"DEV-0", "DEV-1", "DEV-2"}


def test_exit_code_zero_is_not_success(manager, bundle):
    """
    The process ended tidily and changed nothing. That is PARTIAL, and the
    ActionResult is what says so - never the exit code.
    """
    from friday import contracts as c

    manager.open(bundle)
    result = c.partial(c.started("RUN-x", "executor.claude_code"),
                       "claude finished but nothing changed on disk",
                       output={"claude_says": "All done!"})
    manager.close("DEV-continuity", result)
    assert manager.store.executor_run("DEV-continuity")["status"] != R.SUCCEEDED


# ---------------------------------------------------------------------------
# One run, one process
# ---------------------------------------------------------------------------


def test_a_second_executor_for_a_live_run_is_refused(manager, bundle, monkeypatch):
    monkeypatch.setattr(R, "process_alive", lambda pid, started_at=None: True)
    manager.open(bundle)
    manager.running("DEV-continuity", pid=4321)

    with pytest.raises(R.DuplicateRun) as caught:
        manager.open(bundle)
    assert caught.value.pid == 4321


def test_resuming_twice_attaches_rather_than_launching_again(store, bundle,
                                                             monkeypatch):
    monkeypatch.setattr(R, "process_alive", lambda pid, started_at=None: True)
    ex = ClaudeCodeExecutor(store)
    ex.runs.open(bundle)
    ex.runs.running("DEV-continuity", pid=777, session_id="sess-a")

    result = asyncio.run(ex.continue_run("DEV-continuity"))
    assert (result.output or {}).get("already_running") is True
    assert (result.output or {}).get("pid") == 777


def test_a_dead_run_may_be_restarted(manager, bundle, monkeypatch):
    monkeypatch.setattr(R, "process_alive", lambda pid, started_at=None: False)
    manager.open(bundle)
    manager.running("DEV-continuity", pid=999999)
    manager.open(bundle)          # no exception: nothing is alive
    assert manager.store.executor_run("DEV-continuity")["resume_count"] >= 1


def test_nothing_is_alive_without_a_pid():
    assert not R.process_alive(None)
    assert not R.process_alive(0)


def test_a_recycled_pid_is_not_the_process_we_meant():
    """
    The bug the live run found. Checking the process *name* was not enough -
    this machine runs plenty of node processes, so after a hard kill a
    recycled pid matched "node" and the run still read as RUNNING. A killed run
    that looks alive is exactly the state the recovery ladder exists to catch.

    Identity is the pid AND when it started.
    """
    import os

    me = os.getpid()
    real_start = R.process_started_at(me)
    assert real_start is not None

    assert R.process_alive(me, real_start), "this interpreter is alive"
    assert not R.process_alive(me, real_start - 3600), \
        "a pid that started an hour earlier is a different process"


def test_a_run_records_when_its_process_started(manager, bundle):
    """Otherwise there is nothing to compare against later."""
    import os

    manager.open(bundle)
    manager.running("DEV-continuity", pid=os.getpid())
    row = manager.store.executor_run("DEV-continuity")
    assert row["pid_started_at"] is not None
    assert manager.is_live(row)


def test_a_row_with_no_start_time_still_works(manager, bundle):
    """Rows written before the column existed must not crash the ladder."""
    manager.open(bundle)
    manager.store.touch_executor_run("DEV-continuity", pid=999999,
                                     status=R.RUNNING)
    assert manager.is_live(manager.store.executor_run("DEV-continuity")) is False


# ---------------------------------------------------------------------------
# The recovery ladder
# ---------------------------------------------------------------------------


def test_rung_one_attach(manager, bundle, monkeypatch):
    monkeypatch.setattr(R, "process_alive", lambda pid, started_at=None: True)
    manager.open(bundle)
    manager.running("DEV-continuity", pid=42, session_id="sess-a")
    assert manager.recover("DEV-continuity").action == "attach"


def test_rung_two_resume_the_saved_session(manager, bundle, monkeypatch):
    monkeypatch.setattr(R, "process_alive", lambda pid, started_at=None: False)
    manager.open(bundle)
    manager.running("DEV-continuity", pid=999999, session_id="sess-a")

    recovery = manager.recover("DEV-continuity", session_exists=lambda s: True)
    assert recovery.action == "resume"
    assert recovery.session_id == "sess-a"
    assert recovery.can_continue


def test_rung_three_a_lost_session_does_not_lose_the_project(manager, bundle,
                                                             monkeypatch):
    """
    Claude's session files are cleaned up locally. If the work died with them
    the whole architecture would be a nicer chat client.
    """
    monkeypatch.setattr(R, "process_alive", lambda pid, started_at=None: False)
    manager.open(bundle)
    manager.running("DEV-continuity", pid=999999, session_id="sess-gone")

    recovery = manager.recover("DEV-continuity", session_exists=lambda s: False)
    assert recovery.action == "restart"
    assert recovery.can_continue
    assert manager.bundle_of(recovery.run).goal == bundle.goal


def test_rung_three_also_covers_a_run_that_never_got_a_session(manager, bundle,
                                                               monkeypatch):
    monkeypatch.setattr(R, "process_alive", lambda pid, started_at=None: False)
    manager.open(bundle)
    manager.reconcile("DEV-continuity")
    assert manager.recover("DEV-continuity").action == "restart"


def test_rung_four_asks_only_when_something_is_really_missing(manager):
    recovery = manager.recover("DEV-never-existed")
    assert recovery.action == "ask"
    assert not recovery.can_continue


def test_an_already_finished_run_is_not_silently_restarted(manager, bundle,
                                                           monkeypatch):
    from friday import contracts as c

    monkeypatch.setattr(R, "process_alive", lambda pid, started_at=None: False)
    manager.open(bundle)
    manager.close("DEV-continuity", c.failed(
        c.started("RUN-x", "executor.claude_code"), "it broke"))
    assert manager.recover("DEV-continuity").action == "ask"


def test_a_restart_tells_the_new_session_what_the_old_one_did(store, bundle,
                                                              monkeypatch):
    """Otherwise it starts the task over instead of continuing it."""
    monkeypatch.setattr(R, "process_alive", lambda pid, started_at=None: False)
    from friday.executors import cli

    captured = {}

    async def fake_execute(self, b, **kwargs):
        captured["context"] = b.context
        from friday import contracts as c

        return c.failed(c.started("RUN-x", "t"), "stopped here for the test")

    ex = ClaudeCodeExecutor(store)
    ex.runs.open(bundle)
    ex.runs.running("DEV-continuity", pid=999999, session_id="sess-gone")
    store.touch_executor_run("DEV-continuity",
                             summary="wrote parser.py, tests not written yet")

    monkeypatch.setattr(cli, "available", lambda: True)
    monkeypatch.setattr(ClaudeCodeExecutor, "execute", fake_execute)
    asyncio.run(ex.continue_run("DEV-continuity"))

    joined = " ".join(captured["context"])
    assert "parser.py" in joined
    assert "some of the work may already be done" in joined


# ---------------------------------------------------------------------------
# A conversation that has never heard of any of it
# ---------------------------------------------------------------------------


def test_a_fresh_conversation_can_describe_the_work(manager, bundle, monkeypatch):
    monkeypatch.setattr(R, "process_alive", lambda pid, started_at=None: False)
    manager.open(bundle)
    manager.running("DEV-continuity", pid=999999, session_id="sess-a")
    manager.saw("DEV-continuity", "using Edit")
    manager.reconcile("DEV-continuity")

    brief = manager.brief(project="halo")
    assert "DEV-continuity" in brief
    assert "INTERRUPTED" in brief
    assert "build a parser with tests" in brief
    assert "using Edit" in brief


def test_the_briefing_is_empty_when_there_is_nothing_to_say(manager):
    assert manager.brief(project="halo") == ""


def test_active_work_excludes_what_is_finished(manager, bundle, monkeypatch, tmp_path):
    from friday import contracts as c

    monkeypatch.setattr(R, "process_alive", lambda pid, started_at=None: False)
    manager.open(bundle)
    manager.open(TaskBundle(goal="another", workspace=str(tmp_path),
                            run_id="DEV-other", project="halo"))
    manager.close("DEV-other", c.failed(
        c.started("RUN-x", "executor.claude_code"), "no"))

    assert {r["run_id"] for r in manager.active("halo")} == {"DEV-continuity"}


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------


def test_an_unknown_column_cannot_be_written(store, bundle, manager):
    manager.open(bundle)
    with pytest.raises(ValueError, match="no updatable column"):
        store.touch_executor_run("DEV-continuity", nonsense="x")


def test_touching_an_unknown_run_is_not_fatal(store):
    store.touch_executor_run("DEV-nope", status=R.RUNNING)  # no exception


def test_every_status_is_in_exactly_one_class():
    """
    Three classes, not two. INTERRUPTED is neither live nor finished, and that
    is the whole point of it - in TERMINAL a recoverable run looks dead, in
    LIVE it looks like something is still working on it.

    A status in none of them would never be reconciled and never closed.
    """
    for status in R.STATUSES:
        classes = [name for name, group in
                   (("LIVE", R.LIVE), ("TERMINAL", R.TERMINAL),
                    ("RESUMABLE", R.RESUMABLE))
                   if status in group]
        assert len(classes) == 1, f"{status} is in {classes}"


def test_an_interrupted_run_is_not_treated_as_finished():
    assert R.INTERRUPTED not in R.TERMINAL
    assert R.INTERRUPTED not in R.LIVE


def test_a_missing_session_directory_does_not_block_recovery(monkeypatch):
    """
    Session storage is Claude's business. "Cannot tell" answers yes, and a
    failed --resume falls through to a restart, which was the safe direction
    anyway.
    """
    monkeypatch.setattr("os.path.isdir", lambda path: False)
    assert R.session_file_exists("sess-a") is True
    assert R.session_file_exists("") is False
