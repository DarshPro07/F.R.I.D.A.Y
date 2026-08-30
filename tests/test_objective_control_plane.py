"""
Test A: the objectives control plane.

The toolset entry points (objective_start/status/list/pause/resume/cancel/
history) are the contract the MCP adapter and the CLI both call. What they
must guarantee:

  * one active run at a time; a second start is refused, replace=true
    cancels the old run first, with a reason trail
  * pause puts the run into a legitimate wait (no next_wake); resume wakes
    it; cancel interrupts every unfinished task and reaches CANCELLED -
    all lease-free, because stopping must win over whichever process is
    driving
  * every transition lands in the event ledger with reason and executor id
  * a planner clause the planner cannot map becomes a task persisted as
    immediately FAILED/CAPABILITY_MISSING - recorded and never dispatched,
    and the run ends honestly (FAILED) instead of claiming success
"""
from __future__ import annotations


def by_plan_id(store, run_id: str) -> dict:
    """
    Tasks indexed by their plan-facing name - t1, t2 - not the stored id.

    Stored ids are scoped to their run now (`RUN-abc123-t1`), because
    `objective_tasks.task_id` is the table's PRIMARY KEY and per-run numbering
    collided across runs: the second objective ever compiled overwrote the
    first one's rows instead of inserting its own. The plan-facing names these
    tests are written against are unchanged.
    """
    return {row['task_id'].rsplit('-', 1)[-1]: row for row in store.objective_tasks(run_id)}


def plan_id(value: str) -> str:
    """The plan-facing half of a stored task id, for comparing to `blocked_by`."""
    return (value or '').rsplit('-', 1)[-1]
import pytest
from friday import contracts as c
from friday import objectives as O
from friday.continuous import ContinuousTaskExecutor
from friday.store import Store
from friday.toolsets import objectives as OT


@pytest.fixture
def store() -> Store:
    s = Store(":memory:")
    OT.reset_store(s)
    yield s
    OT.reset_store(None)


@pytest.fixture
def run():
    return c.Run.create("objective-test", capability="objectives")


def start(run, objective: str, **kwargs):
    return OT.objective_start(run, objective, **kwargs)


def test_start_compiles_and_persists(store, run):
    result = start(run, 'check the system, then open Paint')
    assert result.status == c.SUCCEEDED
    run_id = result.output['run_id']
    assert result.output['status'] == O.RUN_RUNNING
    assert result.output['task_count'] == 2
    row = store.objective_run(run_id)
    assert row['status'] == O.RUN_RUNNING
    assert row['next_wake']
    tasks = by_plan_id(store, run_id)
    assert tasks['t1']['capability'] == 'system_get_info'
    assert tasks['t1']['status'] == O.TASK_READY
    assert tasks['t2']['capability'] == 'apps_open'
    assert tasks['t2']['status'] == O.TASK_QUEUED
    assert [plan_id(d) for d in tasks['t2']['dependencies']] == ['t1']
    assert tasks['t2']['dependencies'][0] == tasks['t1']['task_id']
    events = store.objective_events(run_id)
    assert events[0]['event'] == O.EVENT_RUN_CREATED
    assert events[0]['detail']['task_count'] == 2


def test_start_refuses_second_active_run(store, run):
    first = start(run, "check the system")
    second = start(run, "open Paint")
    assert second.status == c.FAILED
    assert first.output["run_id"] in second.error

    # replace=true cancels the old run with a reason trail first.
    replaced = start(run, "open Paint", replace=True)
    assert replaced.status == c.SUCCEEDED
    assert replaced.output["run_id"] != first.output["run_id"]

    old = store.objective_run(first.output["run_id"])
    assert old["status"] == O.RUN_CANCELLED
    assert old["summary"]["reason"] == "replaced by a new objective"
    assert old["summary"]["interrupted"] == 1
    trail = [e["event"] for e in store.objective_events(old["run_id"])]
    assert O.EVENT_TASK_INTERRUPTED in trail
    assert O.EVENT_RUN_CANCELLED in trail


def test_start_rejects_empty_and_bad_explicit_tasks(store, run):
    assert start(run, "").status == c.FAILED
    assert start(run, "", tasks="not json").status == c.FAILED
    ghost = start(run, "", tasks='[{"capability": "ghost_tool"}]')
    assert ghost.status == c.FAILED
    assert "ghost_tool" in ghost.error
    dangling = start(run, "", tasks='[{"capability": "system_get_info", '
                                     '"dependencies": ["t7"]}]')
    assert dangling.status == c.FAILED


def test_pause_resume_cycle(store, run):
    run_id = start(run, "check the system, then open Paint").output["run_id"]

    paused = OT.objective_pause(run, run_id)
    assert paused.status == c.SUCCEEDED
    assert paused.output["status"] == O.RUN_PAUSED
    row = store.objective_run(run_id)
    assert row["status"] == O.RUN_PAUSED
    assert row["next_wake"] is None  # a legitimate wait: nobody wakes it

    # Pausing an already-paused run is refused, not silently accepted.
    again = OT.objective_pause(run, run_id)
    assert again.status == c.FAILED
    assert "already paused" in again.error

    resumed = OT.objective_resume(run, run_id)
    assert resumed.status == c.SUCCEEDED
    assert resumed.output["status"] == O.RUN_RUNNING
    row = store.objective_run(run_id)
    assert row["next_wake"]  # immediate wake for the driver loop

    again = OT.objective_resume(run, run_id)
    assert again.status == c.FAILED
    assert "not paused" in again.error


def test_pause_and_resume_are_lease_free_and_ledgered(store, run):
    run_id = start(run, "check the system").output["run_id"]
    OT.objective_pause(run, run_id, reason="user went away")
    OT.objective_resume(run, run_id, reason="user is back")

    events = store.objective_events(run_id)
    kinds = [(e["event"], e["detail"]) for e in events]
    assert ("run.paused", {"reason": "user went away",
                           "by": "objectives.pause"}) in kinds
    assert ("run.resumed", {"reason": "user is back",
                            "by": "objectives.resume"}) in kinds


def test_resume_without_run_id_targets_most_recent_paused(store, run):
    first = start(run, "check the system").output["run_id"]
    OT.objective_pause(run, first)
    second = start(run, "open Paint", replace=True).output["run_id"]
    OT.objective_pause(run, second)

    resumed = OT.objective_resume(run)
    assert resumed.status == c.SUCCEEDED
    assert resumed.output["run_id"] == second


def test_cancel_interrupts_unfinished_tasks(store, run):
    run_id = start(run, "check the system, then open Paint").output["run_id"]

    cancelled = OT.objective_cancel(run, run_id, reason="never mind")
    assert cancelled.status == c.SUCCEEDED
    assert cancelled.output["status"] == O.RUN_CANCELLED

    row = store.objective_run(run_id)
    assert row["status"] == O.RUN_CANCELLED
    assert row["summary"]["interrupted"] == 2
    assert row["summary"]["reason"] == "never mind"
    assert row["summary"]["by"] == "objectives.cancel"

    for task in store.objective_tasks(run_id):
        assert task["status"] == O.TASK_INTERRUPTED
        assert "never mind" in task["evidence"]

    events = store.objective_events(run_id)
    assert events[-1]["event"] == O.EVENT_RUN_CANCELLED
    assert events[-1]["detail"]["reason"] == "never mind"
    assert any(e["event"] == O.EVENT_TASK_INTERRUPTED for e in events)


def test_cancel_of_terminal_run_is_refused(store, run):
    run_id = start(run, "check the system").output["run_id"]
    OT.objective_cancel(run, run_id)
    assert OT.objective_cancel(run, run_id).status == c.FAILED
    assert OT.objective_pause(run, run_id).status == c.FAILED


def test_status_list_history(store, run):
    run_id = start(run, 'check the system, then open Paint').output['run_id']
    status = OT.objective_status(run, run_id)
    assert status.status == c.SUCCEEDED
    assert status.output['run_id'] == run_id
    assert status.output['status'] == O.RUN_RUNNING
    assert [plan_id(t['task_id']) for t in status.output['tasks']] == ['t1', 't2']
    listed = OT.objective_list(run, limit=5)
    assert listed.status == c.SUCCEEDED
    assert listed.output[0]['run_id'] == run_id
    assert listed.output[0]['objective_summary'] == 'check the system, then open Paint'
    history = OT.objective_history(run, run_id)
    assert history.status == c.SUCCEEDED
    assert history.output[0]['event'] == O.EVENT_RUN_CREATED


def test_status_without_active_run_is_honest(store, run):
    run_id = start(run, "check the system").output["run_id"]
    OT.objective_cancel(run, run_id)
    # No active run remains: unqualified lookups say so, named ones still
    # read the finished run.
    assert OT.objective_status(run).status == c.FAILED
    assert OT.objective_status(run, run_id).status == c.SUCCEEDED
    assert OT.objective_pause(run).status == c.FAILED


class Recorder:
    """Registry that records dispatch attempts and always succeeds."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def call(self, capability: str, arguments: dict) -> dict:
        self.calls.append((capability, arguments))
        return {"ok": True}


@pytest.mark.asyncio
async def test_unmapped_clause_is_failed_at_compile_and_never_dispatched(store, run):
    recorder = Recorder()
    result = start(run, 'flurb the wibble')
    assert result.status == c.SUCCEEDED, 'the plan itself is honest, not dead'
    run_id = result.output['run_id']
    assert result.output['task_count'] == 1
    task = store.objective_tasks(run_id)[0]
    assert task['capability'] == OT.UNMAPPED_CAPABILITY
    assert task['status'] == O.TASK_FAILED
    assert task['failure_kind'] == O.FailureKind.CAPABILITY_MISSING
    assert 'flurb the wibble' in task['evidence']
    assert 'objective.unmapped' not in task['evidence']
    executor = ContinuousTaskExecutor(store, recorder.call, executor_id='plane-test')
    await executor.start(run_id)
    assert recorder.calls == [], 'an unmapped task must never be dispatched'
    final = store.objective_run(run_id)
    assert final['status'] == O.RUN_FAILED, 'nothing succeeded: FAILED'
    assert final['summary']['failed'] == 1
    assert final['summary']['failures'][0]['kind'] == O.FailureKind.CAPABILITY_MISSING


@pytest.mark.asyncio
async def test_dependents_of_unmapped_are_skipped_not_run(store, run):
    recorder = Recorder()
    run_id = start(run, 'flurb the wibble, then open Paint').output['run_id']
    assert store.objective_tasks(run_id)[0]['status'] == O.TASK_FAILED
    executor = ContinuousTaskExecutor(store, recorder.call, executor_id='plane-test')
    await executor.start(run_id)
    tasks = by_plan_id(store, run_id)
    assert tasks['t2']['status'] == O.TASK_SKIPPED
    assert plan_id(tasks['t2']['blocked_by']) == 't1'
    assert recorder.calls == [], 'a step behind a failed marker must not run'
    final = store.objective_run(run_id)
    assert final['status'] == O.RUN_FAILED
    assert final['summary']['failed'] == 1
    assert final['summary']['skipped'] == 1
