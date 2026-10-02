"""
The run-level trajectory check (PRD v7 TRAJ-001, §12).

A synthetic long-horizon drift benchmark over generated event traces: healthy
runs, with the ordinary failures that recover, are never flagged; each shape
of drift is flagged within a stated number of events of its onset. Then the
executor itself: a run whose every call fails across tasks is paused with
the reasons, and a healthy run completes unflagged.
"""
from __future__ import annotations

import asyncio
import random

import pytest

from friday import contracts as c
from friday import objectives as O
from friday import trajectory as T
from friday.store import Store


class _Trace:
    """Builds the events a run would record, in order."""

    def __init__(self, tasks: int, *, capability: str = "system_get_info"):
        self.events: list[dict] = [{"event": O.EVENT_RUN_STARTED, "task_id": None, "detail": {}}]
        self.tasks = [{"task_id": f"t{i}", "capability": capability, "status": "READY"}
                      for i in range(tasks)]

    def started(self, task):
        self.events.append({"event": O.EVENT_TASK_STARTED, "task_id": task, "detail": {}})

    def succeeded(self, task):
        self.started(task)
        self.events.append({"event": O.EVENT_TASK_SUCCEEDED, "task_id": task, "detail": {}})
        for row in self.tasks:
            if row["task_id"] == task:
                row["status"] = "SUCCEEDED"

    def failed(self, task, kind="TRANSIENT", reason="503 from the service"):
        self.started(task)
        self.events.append({"event": O.EVENT_TASK_RETRY, "task_id": task,
                            "detail": {"kind": kind, "reason": reason}})

    def assess(self, spend=0.0):
        return T.assess(self.events, self.tasks, spend=spend)


# -- healthy runs never flag ------------------------------------------------------

@pytest.mark.parametrize("seed", range(40))
def test_a_healthy_run_is_never_flagged(seed):
    """Long runs where failures happen and recover: one or two retries on a
    task, then it succeeds, as real providers behave. Zero false positives
    across 40 seeded runs of 60 tasks."""
    rng = random.Random(seed)
    trace = _Trace(60)
    for i in range(60):
        for _ in range(rng.choice([0, 0, 0, 1, 1, 2])):
            trace.failed(f"t{i}", kind=rng.choice(["TRANSIENT", "PROVIDER_DOWN"]))
            assert trace.assess().verdict == T.ON_TRACK, (seed, i, trace.assess())
        trace.succeeded(f"t{i}")
        assert trace.assess(spend=min(0.9, (i + 1) / 60)).verdict == T.ON_TRACK


def test_concurrent_transient_blips_rarely_ask_the_person():
    """The benchmark's false-positive rate. Rounds of four tasks run
    together, as the executor runs them; each attempt fails transiently with
    probability 0.3 and a task succeeds by its third attempt, as a flaky
    provider behaves. Over 200 seeded runs of 15 rounds, at most 2% of runs
    may be paused by the check."""
    flagged = 0
    for seed in range(200):
        rng = random.Random(seed)
        trace = _Trace(60)
        paused = False
        for round_start in range(0, 60, 4):
            pending = [f"t{i}" for i in range(round_start, round_start + 4)]
            for attempt in range(3):
                still = []
                for task in pending:
                    if attempt < 2 and rng.random() < 0.3:
                        trace.failed(task, kind="TRANSIENT")
                        still.append(task)
                    else:
                        trace.succeeded(task)
                pending = still
                if trace.assess().verdict != T.ON_TRACK:
                    paused = True
            if paused:
                break
        flagged += paused
    assert flagged / 200 <= 0.02, f"{flagged} of 200 healthy runs were paused"


# -- each drift shape is flagged, promptly --------------------------------------

def test_no_progress_across_tasks_is_flagged_within_its_threshold():
    trace = _Trace(10)
    for i in range(4):
        trace.succeeded(f"t{i}")
    flagged_at = None
    for step, task in enumerate(["t4", "t5", "t6", "t4", "t5", "t6", "t4", "t5"]):
        trace.failed(task, kind=["TRANSIENT", "TIMEOUT", "STRUCTURAL"][step % 3])
        if trace.assess().verdict != T.ON_TRACK:
            flagged_at = step + 1
            break
    assert flagged_at == T.NO_PROGRESS_ATTEMPTS
    assessment = trace.assess()
    assert assessment.verdict == T.NO_PROGRESS
    assert any("succeeded" in r for r in assessment.reasons)


def test_one_task_stuck_alone_is_left_to_the_task_level_detector():
    trace = _Trace(3)
    for _ in range(12):
        trace.failed("t0", kind="STRUCTURAL")
    assert trace.assess().verdict == T.ON_TRACK


def test_one_cause_failing_every_task_is_flagged_once_it_recurs():
    """One round in which every running task hit the same blip is not
    drift; the same cause failing them all again is."""
    trace = _Trace(8)
    trace.succeeded("t0")
    for task in ("t1", "t2", "t3"):
        trace.failed(task, kind="NETWORK")
    assert trace.assess().verdict == T.ON_TRACK
    trace.failed("t1", kind="NETWORK")
    trace.failed("t2", kind="NETWORK")
    assert trace.assess().verdict == T.ON_TRACK
    trace.failed("t3", kind="NETWORK")
    assessment = trace.assess()
    assert assessment.verdict == T.NO_PROGRESS
    assert any("NETWORK" in r for r in assessment.reasons)


def test_repeated_policy_refusals_are_flagged():
    trace = _Trace(6)
    for task in ("t0", "t1", "t2"):
        trace.events.append({"event": O.EVENT_TASK_FAILED, "task_id": task,
                             "detail": {"kind": "POLICY", "reason": "ENVELOPE: files_delete is outside"}})
    assessment = trace.assess()
    assert assessment.verdict == T.DRIFTING
    assert any("refus" in r for r in assessment.reasons)


def test_spend_far_ahead_of_progress_is_flagged():
    trace = _Trace(10)
    trace.succeeded("t0")
    assert trace.assess(spend=0.5).verdict == T.ON_TRACK
    assessment = trace.assess(spend=0.8)
    assert assessment.verdict == T.DRIFTING
    assert any("budget" in r for r in assessment.reasons)


def test_a_budget_already_spent_is_left_to_the_budget():
    """Past a ceiling, objective_budget pauses the run with the exact
    dimension (wall_time, tokens...); this check does not pre-empt it."""
    trace = _Trace(10)
    assert trace.assess(spend=5.0).verdict == T.ON_TRACK


def test_a_side_effecting_step_run_again_and_again_is_flagged():
    trace = _Trace(4, capability="files_write")
    trace.succeeded("t0")
    trace.started("t1")
    trace.started("t1")
    assert trace.assess().verdict == T.ON_TRACK
    trace.started("t1")
    assessment = trace.assess()
    assert assessment.verdict == T.DRIFTING
    assert any("side effect" in r for r in assessment.reasons)


def test_a_side_effecting_step_that_finally_succeeded_is_not_flagged():
    """Two retries and then success: the step is done and cannot run again,
    so it is no reason to stop the run and ask the person."""
    trace = _Trace(4, capability="files_write")
    trace.failed("t0")
    trace.failed("t0")
    trace.succeeded("t0")
    assert trace.assess().verdict == T.ON_TRACK, trace.assess().reasons


def test_a_resume_starts_a_new_window():
    trace = _Trace(10)
    for task in ("t0", "t1", "t2") * 2:
        trace.failed(task)
    assert trace.assess().verdict == T.NO_PROGRESS
    trace.events.append({"event": O.EVENT_RUN_RESUMED, "task_id": None, "detail": {}})
    assert trace.assess().verdict == T.ON_TRACK


# -- the executor acts on it -------------------------------------------------------

def _compile(store, n):
    created = O.compile_objective(
        store, request="check the system", objective_summary="check the system",
        tasks=[{"capability": "system_get_info", "arguments": {}} for _ in range(n)],
        manifest=[{"id": "system_get_info", "description": "info"}],
        provenance=c.PERSON, attended=True)
    return created["run_id"]


def _drive(store, run_id, dispatch, seconds=20.0):
    from friday.continuous import ContinuousTaskExecutor

    async def go():
        executor = ContinuousTaskExecutor(store, call_capability=dispatch)
        try:
            loop = asyncio.get_running_loop()
            deadline = loop.time() + seconds
            while loop.time() < deadline:
                status = store.objective_run(run_id)["status"]
                if status in O.RUN_TERMINAL or status in O.RUN_WAITING_STATUSES:
                    return
                # Backoff is the executor's own pacing; the test makes every
                # scheduled wake due now so the run's history accumulates.
                store._conn.execute("UPDATE objective_tasks SET next_wake=NULL WHERE run_id=?",
                                    (run_id,))
                store._conn.execute("UPDATE objective_runs SET next_wake=NULL WHERE run_id=?",
                                    (run_id,))
                await executor.start(run_id)
                await asyncio.sleep(0.01)
        finally:
            executor.stop()

    asyncio.run(go())
    return store.objective_run(run_id)


def test_a_run_failing_across_tasks_is_paused_with_its_reasons(tmp_path):
    store = Store(tmp_path / "ada.sqlite3")
    run_id = _compile(store, 4)

    async def always_unavailable(capability, arguments, *, run=None):
        # Transient, so the executor keeps retrying: the long run the
        # trajectory check exists for (a hard error fails the run at once).
        raise TimeoutError("the service did not answer")

    run = _drive(store, run_id, always_unavailable)
    assert run["status"] == O.RUN_PAUSED, run
    assert "trajectory" in (run.get("blocker") or "")
    flagged = [e for e in store.objective_events(run_id) if e["event"] == T.EVENT_FLAGGED]
    assert flagged and flagged[0]["detail"]["verdict"] == T.NO_PROGRESS


def test_a_healthy_run_completes_unflagged(tmp_path):
    store = Store(tmp_path / "ada.sqlite3")
    run_id = _compile(store, 4)
    from friday import capability_runtime as CR
    from friday import policy as PO

    run = _drive(store, run_id, CR.build_dispatch(engine=PO.PolicyEngine(autonomy=PO.FULL)))
    assert run["status"] == O.RUN_COMPLETED, run
    assert not [e for e in store.objective_events(run_id) if e["event"] == T.EVENT_FLAGGED]


# -- alignment with the objective contract (INTENT-001) ----------------------------

def test_a_started_step_the_contract_forbids_is_scope_creep():
    trace = _Trace(3, capability="files_delete")
    trace.succeeded("t0")
    found = T.assess(trace.events, trace.tasks, governance={"forbidden": ["delete"]})
    assert found.verdict == T.DRIFTING
    assert any("beyond the objective's contract" in r and "delete was forbidden" in r
               for r in found.reasons)
    assert T.assess(trace.events, trace.tasks).verdict == T.ON_TRACK


def test_more_side_effecting_steps_than_the_contract_allows_is_scope_creep():
    trace = _Trace(4, capability="files_write")
    for i in range(3):
        trace.succeeded(f"t{i}")
    over = T.assess(trace.events, trace.tasks, governance={"max_side_effects": 2})
    assert over.verdict == T.DRIFTING
    assert "3 steps with side effects against a limit of 2" in " ".join(over.reasons)
    within = T.assess(trace.events, trace.tasks, governance={"max_side_effects": 3})
    assert within.verdict == T.ON_TRACK


def test_acting_while_the_contract_has_open_questions_is_flagged():
    trace = _Trace(2, capability="files_write")
    trace.succeeded("t0")
    found = T.assess(trace.events, trace.tasks, open_questions=("which folder?",))
    assert found.verdict == T.DRIFTING
    assert found.signals["unanswered_questions"] == 1
    read_only = _Trace(2)
    read_only.succeeded("t0")
    assert T.assess(read_only.events, read_only.tasks,
                    open_questions=("which folder?",)).verdict == T.ON_TRACK


def test_a_resume_clears_the_contract_signals_for_the_new_window():
    trace = _Trace(2, capability="files_delete")
    trace.succeeded("t0")
    trace.events.append({"event": O.EVENT_RUN_RESUMED, "task_id": None, "detail": {}})
    assert T.assess(trace.events, trace.tasks,
                    governance={"forbidden": ["delete"]}).verdict == T.ON_TRACK
