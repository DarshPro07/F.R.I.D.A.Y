"""
Changing a run that is already under way, and refusing to change what is not.

The behaviour these gates pin down is mostly about what modification will
*not* do. Editing future work is easy; the failures worth guarding are the
three that look like helpfulness:

  rewriting history   "actually don't open Paint" after Paint opened. The task
                      stays SUCCEEDED with its evidence. Saying it was skipped
                      would put a false statement in the trace.
  losing a race       the boss edits a task in the same moment the executor
                      claims it. Rewriting the arguments of a capability that
                      is mid-call changes what it is doing halfway through.
  silent refusal      the previous behaviour. MODIFICATION was classified and
                      the handler left the run alone without saying so, which
                      is indistinguishable from having done it.
"""
from __future__ import annotations
import json
import pytest
from friday import modification as MOD
from friday import objectives as O
from friday.store import Store


@pytest.fixture
def run(tmp_path):
    """A three-task run: research, open the World Monitor, write a summary."""
    store = Store(tmp_path / 'objectives.sqlite3')
    run_id = 'RUN-modification'
    store.open_objective_run(run_id, request='do the morning briefing', objective_summary='morning briefing', status=O.RUN_RUNNING)
    for task_id, capability, arguments, dependencies in (('t1', 'web_deep_research', {'topic': 'fusion', 'source': 'reuters'}, []), ('t2', 'open_world_monitor', {'focus': 'conflicts'}, ['t1']), ('t3', 'files_write', {'path': 'briefing.md'}, ['t2'])):
        store.save_objective_task(task_id=task_id, run_id=run_id, capability=capability, arguments=json.dumps(arguments), dependencies=json.dumps(dependencies), status=O.TaskStatus.QUEUED)
    return (store, run_id)


def status_of(store, task_id):
    return store.objective_task(task_id)["status"]


def test_skipping_a_pending_task(run):
    store, run_id = run
    edit = MOD.skip_task(store, run_id, "skip the world monitor part")

    assert edit.applied, edit.reason
    assert edit.task_id == "t2"
    assert status_of(store, "t2") == O.TaskStatus.SKIPPED
    assert "t3" in edit.affected, "the dependent was not identified"


def test_independent_work_is_untouched_by_a_skip(run):
    store, run_id = run
    MOD.skip_task(store, run_id, "skip the world monitor part")

    assert status_of(store, "t1") == O.TaskStatus.QUEUED, \
        "skipping one task disturbed a task that did not depend on it"


def test_a_skip_that_matches_nothing_says_so(run):
    store, run_id = run
    edit = MOD.skip_task(store, run_id, "skip the underwater basket weaving")

    assert edit.outcome == MOD.NOT_FOUND
    assert not edit.applied
    assert "matches" in edit.reason


def test_amending_a_pending_argument(run):
    store, run_id = run
    edit = MOD.amend_task(store, run_id, "the research",
                          {"source": "bbc"}, said="use BBC instead")

    assert edit.applied, edit.reason
    assert store.objective_task("t1")["arguments"]["source"] == "bbc"
    assert store.objective_task("t1")["arguments"]["topic"] == "fusion", \
        "amending one argument dropped the others"


def test_amending_preserves_completed_evidence(run):
    store, run_id = run
    store.update_objective_task("t1", status=O.TaskStatus.SUCCEEDED,
                                evidence="read 4 pages from reuters")

    edit = MOD.amend_task(store, run_id, "the research", {"source": "bbc"})

    assert edit.outcome == MOD.TOO_LATE
    assert store.objective_task("t1")["arguments"]["source"] == "reuters", \
        "the arguments of a finished task were rewritten"
    assert store.objective_task("t1")["evidence"] == "read 4 pages from reuters"


def test_appending_a_task_waits_for_the_outstanding_work(run):
    store, run_id = run
    edit = MOD.append_task(store, run_id, "memory_remember",
                           {"subject": "briefing"},
                           said="also save a short summary when you're done")

    assert edit.applied, edit.reason
    added = store.objective_task(edit.task_id)
    assert set(added["dependencies"]) == {"t1", "t2", "t3"}, \
        f"appended task depends on {added['dependencies']}"


def test_appending_only_waits_for_work_that_is_still_outstanding(run):
    store, run_id = run
    store.update_objective_task("t1", status=O.TaskStatus.SUCCEEDED)

    edit = MOD.append_task(store, run_id, "memory_remember", {})
    added = store.objective_task(edit.task_id)

    assert "t1" not in added["dependencies"], \
        "the new task waits on something that has already finished"
    assert set(added["dependencies"]) == {"t2", "t3"}


def test_appending_to_a_finished_run_is_a_new_objective(run):
    store, run_id = run
    store.finish_objective_run(run_id, status=O.RUN_COMPLETED, summary={})

    edit = MOD.append_task(store, run_id, "memory_remember", {})
    assert edit.outcome == MOD.REFUSED
    assert "new objective" in edit.reason


def test_a_completed_side_effect_is_not_retracted(run):
    """
    Paint has opened. "Actually don't open Paint" cannot un-open it, and
    marking the task SKIPPED would put a false statement in the trace - the
    run would then read as though Paint was never opened, which is the one
    thing this project exists not to produce.
    """
    store, run_id = run
    store.update_objective_task("t2", status=O.TaskStatus.SUCCEEDED,
                                evidence="worldmonitor.app opened")

    edit = MOD.skip_task(store, run_id, "actually don't open the world monitor")

    assert edit.outcome == MOD.TOO_LATE
    assert status_of(store, "t2") == O.TaskStatus.SUCCEEDED
    assert "already finished" in edit.reason
    assert store.objective_task("t2")["evidence"] == "worldmonitor.app opened"


def test_every_edit_advances_the_graph_version(run):
    store, run_id = run
    assert MOD.graph_version(store, run_id) == 1, "a compiled graph is version 1"

    MOD.skip_task(store, run_id, "skip the world monitor")
    assert MOD.graph_version(store, run_id) == 2

    MOD.amend_task(store, run_id, "the research", {"source": "bbc"})
    assert MOD.graph_version(store, run_id) == 3


def test_the_version_survives_a_restart(run, tmp_path):
    """
    No in-memory-only graph edits. A process that resumes must read the same
    authoritative version as the one that made the edit.
    """
    store, run_id = run
    MOD.skip_task(store, run_id, "skip the world monitor")

    reopened = Store(tmp_path / "objectives.sqlite3")
    assert MOD.graph_version(reopened, run_id) == 2
    assert status_of(reopened, "t2") == O.TaskStatus.SKIPPED


def test_an_edit_records_what_was_said_and_both_states(run):
    store, run_id = run
    MOD.skip_task(store, run_id, 'skip the world monitor part', said='skip the World Monitor part')
    edits = [e for e in store.objective_events(run_id) if e['event'] == MOD.GRAPH_EDIT]
    assert len(edits) == 1
    detail = edits[0]['detail']
    assert isinstance(detail, dict), f"detail came back as {type(detail)}"
    assert detail['graph_version'] == 2
    assert detail['old_state'] == O.TaskStatus.QUEUED
    assert detail['new_state'] == O.TaskStatus.SKIPPED
    assert detail['source_turn'] == 'skip the World Monitor part'
    assert detail['reason']


def test_a_running_task_cannot_be_amended(run):
    store, run_id = run
    store.update_objective_task("t1", status=O.TaskStatus.RUNNING)

    edit = MOD.amend_task(store, run_id, "the research", {"source": "bbc"})

    assert edit.outcome == MOD.TOO_LATE
    assert store.objective_task("t1")["arguments"]["source"] == "reuters", \
        "the arguments changed underneath a running capability"


def test_a_running_task_cannot_be_skipped(run):
    store, run_id = run
    store.update_objective_task("t2", status=O.TaskStatus.RUNNING)

    edit = MOD.skip_task(store, run_id, "skip the world monitor")
    assert edit.outcome == MOD.TOO_LATE
    assert status_of(store, "t2") == O.TaskStatus.RUNNING


def test_the_conditional_update_is_what_decides_the_race(run):
    """
    The status check and the write are one statement. A task that becomes
    RUNNING between them must lose - checked here directly, because the window
    is too small to hit by arranging threads and a test that cannot fail is
    not a guard.
    """
    store, run_id = run

    assert store.update_objective_task_if(
        "t1", expect=MOD.EDITABLE, status=O.TaskStatus.SKIPPED) is True

    store.update_objective_task("t2", status=O.TaskStatus.RUNNING)
    assert store.update_objective_task_if(
        "t2", expect=MOD.EDITABLE, status=O.TaskStatus.SKIPPED) is False, \
        "a RUNNING task was updated by a guard that expected QUEUED or READY"
    assert status_of(store, "t2") == O.TaskStatus.RUNNING


def test_asking_how_far_along_changes_nothing(run):
    from friday.arbiter import classify_input

    store, run_id = run
    before = [dict(t) for t in store.objective_tasks(run_id)]

    for question in ("how far are you", "what's the status",
                     "how's it going", "where are you up to"):
        assert classify_input(question) == "QUERY_ABOUT_RUN", \
            f"{question!r} was not read as a question"

    assert [dict(t) for t in store.objective_tasks(run_id)] == before
    assert MOD.graph_version(store, run_id) == 1
