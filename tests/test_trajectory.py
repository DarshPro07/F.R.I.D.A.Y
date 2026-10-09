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


def _event(name):
    return {"event": name, "task_id": None, "detail": {}}


def test_repeated_plan_amendments_are_flagged():
    """PRD §12 'user amendments': the plan not settling, not one correction."""
    trace = _Trace(6)
    for _ in range(T.AMENDMENTS - 1):
        trace.events.append(_event("graph_edited"))
    assert trace.assess().verdict == T.ON_TRACK
    trace.events.append(_event("graph_edited"))
    assessment = trace.assess()
    assert assessment.verdict == T.DRIFTING
    assert any("amendment" in r for r in assessment.reasons)
    assert assessment.signals["amendments"] == T.AMENDMENTS


def test_repeated_worker_takeovers_are_flagged():
    """PRD §12 'worker conflict': the run keeps losing the worker driving it."""
    trace = _Trace(6)
    for _ in range(T.TAKEOVERS - 1):
        trace.events.append(_event(O.EVENT_WATCHDOG_ORPHANED))
    assert trace.assess().verdict == T.ON_TRACK
    trace.events.append(_event(O.EVENT_WATCHDOG_ORPHANED))
    assessment = trace.assess()
    assert assessment.verdict == T.DRIFTING
    assert any("takeover" in r for r in assessment.reasons)
    assert assessment.signals["takeovers"] == T.TAKEOVERS


def test_one_crash_recovered_and_a_few_amendments_are_not_drift():
    """One takeover is ordinary crash recovery (OBJ-001) and a few amendments
    are a person steering; neither alone, nor both together, ask the person."""
    trace = _Trace(5)
    trace.succeeded("t0")
    trace.events.append(_event(O.EVENT_WATCHDOG_ORPHANED))
    for _ in range(T.AMENDMENTS - 1):
        trace.events.append(_event("graph_edited"))
    for task in ("t1", "t2", "t3", "t4"):
        trace.succeeded(task)
    assessment = trace.assess()
    assert assessment.verdict == T.ON_TRACK
    assert assessment.signals["takeovers"] == 1


def test_a_wake_is_not_a_takeover():
    """Every wake takes a lease (lease.acquired); only the watchdog seizing an
    orphaned run is a takeover, or a long healthy run would be flagged."""
    trace = _Trace(8)
    for task in ("t0", "t1", "t2", "t3", "t4"):
        trace.events.append(_event("lease.acquired"))
        trace.succeeded(task)
    assert trace.assess().verdict == T.ON_TRACK
    assert trace.assess().signals["takeovers"] == 0


def test_a_resume_forgives_the_amendments_and_takeovers_before_it():
    trace = _Trace(6)
    for _ in range(T.AMENDMENTS):
        trace.events.append(_event("graph_edited"))
    for _ in range(T.TAKEOVERS):
        trace.events.append(_event(O.EVENT_WATCHDOG_ORPHANED))
    assert trace.assess().verdict == T.DRIFTING
    trace.events.append(_event(O.EVENT_RUN_RESUMED))
    assert trace.assess().verdict == T.ON_TRACK


@pytest.mark.parametrize("seed", range(20))
def test_a_long_steered_run_below_the_drift_thresholds_is_never_flagged(seed):
    """Long-horizon benchmark, healthy side: over 60 tasks a person steers the plan
    and a worker dies now and then, but each window (a resume starts a new one)
    stays under the amendment and takeover thresholds, and every wake takes a
    lease. Zero false positives at every step."""
    rng = random.Random(seed)
    trace = _Trace(60)
    amended = taken_over = 0
    for i in range(60):
        if i and i % rng.choice([12, 15, 20]) == 0:
            trace.events.append(_event(O.EVENT_RUN_RESUMED))
            amended = taken_over = 0
        trace.events.append(_event("lease.acquired"))
        if rng.random() < 0.3 and amended < T.AMENDMENTS - 1:
            trace.events.append(_event("graph_edited"))
            amended += 1
        if rng.random() < 0.15 and taken_over < T.TAKEOVERS - 1:
            trace.events.append(_event(O.EVENT_WATCHDOG_ORPHANED))
            taken_over += 1
        trace.succeeded(f"t{i}")
        found = trace.assess(spend=min(0.9, (i + 1) / 60))
        assert found.verdict == T.ON_TRACK, (seed, i, found)
        assert found.signals["amendments"] == amended
        assert found.signals["takeovers"] == taken_over


@pytest.mark.parametrize("shape,name,threshold", [
    ("amendments", "graph_edited", T.AMENDMENTS),
    ("takeovers", O.EVENT_WATCHDOG_ORPHANED, T.TAKEOVERS),
])
@pytest.mark.parametrize("seed", range(20))
def test_a_long_run_drifting_while_tasks_still_succeed_is_flagged_at_its_threshold(
        seed, shape, name, threshold):
    """Long-horizon benchmark, drifting side: tasks keep succeeding (so nothing is
    'stuck') while the plan keeps being amended, or the run keeps losing its
    worker. It is flagged on the event that reaches the threshold, not before
    and not after."""
    rng = random.Random(seed)
    trace = _Trace(60)
    next_task = 0
    for i in range(threshold):
        for _ in range(rng.randint(0, 3)):
            trace.succeeded(f"t{next_task}")
            next_task += 1
        trace.events.append(_event(name))
        found = trace.assess()
        assert found.signals[shape] == i + 1
        if i + 1 < threshold:
            assert found.verdict == T.ON_TRACK, (seed, shape, i, found)
    assert found.verdict == T.DRIFTING, (seed, shape, found)
    assert any(shape[:-1] in reason for reason in found.reasons), found.reasons


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


class _CountingDispatch:
    def __init__(self):
        self.calls = 0

    async def __call__(self, capability, arguments, *, run=None):
        self.calls += 1
        return {"ok": True}


def test_a_drifting_run_is_paused_by_the_single_round_the_watchdog_drives(tmp_path):
    """`_drive_one_round` is the path RunWatchdog uses to recover an orphaned
    run, not `_drive_until_done`; it must pause a run the trajectory check
    flags instead of dispatching into it."""
    store = Store(tmp_path / "ada.sqlite3")
    run_id = _compile(store, 4)
    for task in ("t0", "t1", "t2", "t0", "t1", "t2"):
        store.append_objective_event(run_id, O.EVENT_TASK_RETRY, task_id=task,
                                     detail={"kind": "TRANSIENT", "reason": "503"})
    assert T.for_run(store, run_id).verdict == T.NO_PROGRESS
    dispatch = _CountingDispatch()

    async def go():
        from friday.continuous import ContinuousTaskExecutor
        return await ContinuousTaskExecutor(store, call_capability=dispatch)._drive_one_round(run_id)

    assert asyncio.run(go()) == 0
    run = store.objective_run(run_id)
    assert run["status"] == O.RUN_PAUSED, run
    assert "trajectory" in (run.get("blocker") or "")
    flagged = [e for e in store.objective_events(run_id) if e["event"] == T.EVENT_FLAGGED]
    assert flagged and flagged[0]["detail"]["verdict"] == T.NO_PROGRESS
    assert dispatch.calls == 0


def test_a_run_orphaned_again_and_again_is_paused_by_the_watchdog(tmp_path):
    """PRD §12 'worker conflict', end to end: the run was taken over twice
    already, the watchdog takes it over a third time, and instead of driving
    it again it pauses it and asks the person - no round, no wake, no
    continuation left behind."""
    from friday.continuous import ContinuousTaskExecutor, RunWatchdog

    store = Store(tmp_path / "ada.sqlite3")
    run_id = _compile(store, 3)
    for _ in range(T.TAKEOVERS - 1):
        store.append_objective_event(run_id, O.EVENT_WATCHDOG_ORPHANED, detail={})
    dispatch = _CountingDispatch()

    async def go():
        executor = ContinuousTaskExecutor(store, call_capability=dispatch)
        watchdog = RunWatchdog(executor)
        orphaned = await watchdog.sweep_once()
        await asyncio.sleep(0.2)
        return orphaned

    assert asyncio.run(go()) == [run_id]
    run = store.objective_run(run_id)
    assert run["status"] == O.RUN_PAUSED, run
    assert run["next_wake"] is None
    events = store.objective_events(run_id)
    flagged = [e for e in events if e["event"] == T.EVENT_FLAGGED]
    assert flagged and any("takeover" in r for r in flagged[0]["detail"]["reasons"])
    assert not [e for e in events if e["event"] == O.EVENT_CONTINUATION_SCHEDULED]
    assert dispatch.calls == 0
    assert all(t["status"] == "READY" for t in store.objective_tasks(run_id))


def test_a_run_orphaned_once_is_still_recovered_by_the_watchdog(tmp_path):
    """One takeover is ordinary crash recovery: the watchdog drives the run on."""
    from friday.continuous import ContinuousTaskExecutor, RunWatchdog

    store = Store(tmp_path / "ada.sqlite3")
    run_id = _compile(store, 3)
    dispatch = _CountingDispatch()

    async def go():
        executor = ContinuousTaskExecutor(store, call_capability=dispatch)
        return await RunWatchdog(executor).sweep_once()

    assert asyncio.run(go()) == [run_id]
    assert dispatch.calls >= 1
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


# -- long-horizon contract drift (synthetic) ---------------------------------------

def _mixed_trace(tasks, write_every):
    """A long trace of read steps with a write step now and then; the writes'
    indexes are returned so a test knows which step is which."""
    trace = _Trace(tasks)
    writes = [i for i in range(tasks) if i % write_every == write_every - 1]
    for i in writes:
        trace.tasks[i]["capability"] = "files_write"
    return trace, writes


@pytest.mark.parametrize("seed", range(20))
def test_a_long_run_inside_its_contract_is_never_flagged(seed):
    """Writes within the allowed count, no forbidden class, no open questions:
    the contract signals add no false positives to ordinary retries."""
    rng = random.Random(seed)
    trace, writes = _mixed_trace(60, rng.choice([5, 6, 7]))
    governance = {"forbidden": ["delete", "send"], "max_side_effects": len(writes)}
    for i in range(60):
        if i not in writes and rng.random() < 0.3:
            trace.failed(f"t{i}")
        trace.succeeded(f"t{i}")
        found = T.assess(trace.events, trace.tasks, governance=governance)
        assert found.verdict == T.ON_TRACK, (seed, i, found)


@pytest.mark.parametrize("seed", range(20))
def test_one_write_past_the_contracts_limit_is_flagged_when_it_starts(seed):
    rng = random.Random(seed)
    trace, writes = _mixed_trace(60, 6)
    allowed = rng.randint(2, len(writes) - 1)
    governance = {"max_side_effects": allowed}
    breach = writes[allowed]
    for i in range(breach):
        trace.succeeded(f"t{i}")
        assert T.assess(trace.events, trace.tasks, governance=governance).verdict == T.ON_TRACK, (seed, i)
    trace.started(f"t{breach}")
    found = T.assess(trace.events, trace.tasks, governance=governance)
    assert found.verdict == T.DRIFTING, (seed, found)
    assert f"{allowed + 1} steps with side effects against a limit of {allowed}" in " ".join(found.reasons)


@pytest.mark.parametrize("seed", range(20))
def test_a_forbidden_step_in_a_long_run_is_flagged_when_it_starts(seed):
    rng = random.Random(seed)
    trace = _Trace(60)
    onset = rng.randrange(5, 55)
    trace.tasks[onset]["capability"] = "files_delete"
    governance = {"forbidden": ["delete"]}
    for i in range(onset):
        trace.succeeded(f"t{i}")
    assert T.assess(trace.events, trace.tasks, governance=governance).verdict == T.ON_TRACK
    trace.started(f"t{onset}")
    found = T.assess(trace.events, trace.tasks, governance=governance)
    assert found.verdict == T.DRIFTING, (seed, found)
    assert "files_delete (delete was forbidden)" in " ".join(found.reasons)


@pytest.mark.parametrize("seed", range(20))
def test_the_first_write_under_an_open_question_is_flagged_when_it_starts(seed):
    rng = random.Random(seed)
    trace, writes = _mixed_trace(60, rng.choice([8, 10, 12]))
    first = writes[0]
    open_questions = ("which folder?",)
    for i in range(first):
        trace.succeeded(f"t{i}")
        assert T.assess(trace.events, trace.tasks,
                        open_questions=open_questions).verdict == T.ON_TRACK, (seed, i)
    trace.started(f"t{first}")
    found = T.assess(trace.events, trace.tasks, open_questions=open_questions)
    assert found.verdict == T.DRIFTING, (seed, found)
    assert found.signals["unanswered_questions"] == 1


# -- the executor acts on the contract ---------------------------------------------

def _compile_under(store, contract, capability, n, *, chained=True):
    """`n` steps under a contract: a chain (each its own batch) or, with
    `chained=False`, independent steps that the executor runs as one batch."""
    created = O.compile_objective(
        store, request="tidy the folder", objective_summary="tidy the folder",
        tasks=[{"capability": capability, "arguments": {},
                "dependencies": [f"t{i}"] if chained and i else []} for i in range(n)],
        manifest=[{"id": capability, "description": capability}],
        provenance=c.PERSON, attended=True, contract=contract)
    return created["run_id"]


async def _every_call_works(capability, arguments, *, run=None):
    return {"ok": True}


def test_a_run_acting_under_an_open_question_is_paused_with_the_question(tmp_path):
    store = Store(tmp_path / "ada.sqlite3")
    contract = c.ObjectiveContract(goal="tidy the folder",
                                   open_questions=("which folder?",))
    run_id = _compile_under(store, contract, "files_write", 3)

    run = _drive(store, run_id, _every_call_works)
    assert run["status"] == O.RUN_PAUSED, run
    assert "trajectory" in (run.get("blocker") or "")
    assert "question(s) in the contract are still open" in run["blocker"]
    flagged = [e for e in store.objective_events(run_id) if e["event"] == T.EVENT_FLAGGED]
    assert len(flagged) == 1
    assert flagged[0]["detail"]["verdict"] == T.DRIFTING
    assert flagged[0]["detail"]["signals"]["unanswered_questions"] == 1


def test_read_only_steps_under_an_open_question_complete_unflagged(tmp_path):
    store = Store(tmp_path / "ada.sqlite3")
    contract = c.ObjectiveContract(goal="check the system",
                                   open_questions=("which report?",))
    run_id = _compile_under(store, contract, "system_get_info", 3)

    run = _drive(store, run_id, _every_call_works)
    assert run["status"] == O.RUN_COMPLETED, run
    assert not [e for e in store.objective_events(run_id) if e["event"] == T.EVENT_FLAGGED]


# -- before a step starts: the check looks at what is about to run ---------------------

OPEN = ("which folder?",)


def _flagged(unanswered):
    return {"event": T.EVENT_FLAGGED, "task_id": None,
            "detail": {"verdict": T.DRIFTING, "signals": {"unanswered_questions": unanswered}}}


def _resumed():
    return {"event": O.EVENT_RUN_RESUMED, "task_id": None, "detail": {}}


def test_a_side_effecting_step_about_to_start_under_an_open_question_is_flagged():
    trace = _Trace(3, capability="files_write")
    found = T.assess(trace.events, trace.tasks, open_questions=OPEN, upcoming=("t0",))
    assert found.verdict == T.DRIFTING
    assert "about to start while 1 question(s) in the contract are still open" in " ".join(found.reasons)
    assert found.signals["unanswered_questions"] == 1
    assert found.signals["pending_side_effects"] == ["files_write"]


def test_a_read_only_step_about_to_start_under_an_open_question_is_not_flagged():
    trace = _Trace(3)
    found = T.assess(trace.events, trace.tasks, open_questions=OPEN, upcoming=("t0", "t1"))
    assert found.verdict == T.ON_TRACK
    assert found.signals["unanswered_questions"] == 0


def test_a_step_about_to_start_with_no_open_question_is_not_flagged():
    trace = _Trace(3, capability="files_write")
    assert T.assess(trace.events, trace.tasks, upcoming=("t0", "t1", "t2")).verdict == T.ON_TRACK


def test_a_step_that_has_already_started_is_reported_once_not_as_about_to_start():
    trace = _Trace(3, capability="files_write")
    trace.started("t0")
    found = T.assess(trace.events, trace.tasks, open_questions=OPEN, upcoming=("t0",))
    assert found.verdict == T.DRIFTING
    assert found.signals["pending_side_effects"] == []
    assert "about to start" not in " ".join(found.reasons)


def test_carrying_on_past_the_question_is_not_asked_again_before_the_step():
    trace = _Trace(3, capability="files_write")
    trace.events += [_flagged(1), _resumed()]
    assert T.assess(trace.events, trace.tasks, open_questions=OPEN,
                    upcoming=("t0",)).verdict == T.ON_TRACK


def test_carrying_on_past_the_question_is_not_asked_again_after_the_step():
    trace = _Trace(3, capability="files_write")
    trace.events += [_flagged(1), _resumed()]
    trace.started("t0")
    assert T.assess(trace.events, trace.tasks, open_questions=OPEN).verdict == T.ON_TRACK


def test_carrying_on_past_some_other_flag_does_not_waive_the_open_question():
    """The person was asked about failures, not about the unanswered question."""
    trace = _Trace(3, capability="files_write")
    trace.events += [_flagged(0), _resumed()]
    found = T.assess(trace.events, trace.tasks, open_questions=OPEN, upcoming=("t0",))
    assert found.verdict == T.DRIFTING


def test_a_question_added_after_the_person_carried_on_is_asked_again():
    trace = _Trace(3, capability="files_write")
    trace.events += [_flagged(1), _resumed()]
    found = T.assess(trace.events, trace.tasks, open_questions=OPEN + ("which day?",),
                     upcoming=("t0",))
    assert found.verdict == T.DRIFTING
    assert found.signals["unanswered_questions"] == 2


@pytest.mark.parametrize("seed", range(20))
def test_the_first_write_under_an_open_question_is_flagged_before_it_starts(seed):
    """The reads ahead of it are never flagged, and the write is flagged while
    still pending: no side effect has happened when the person is asked."""
    rng = random.Random(seed)
    trace, writes = _mixed_trace(60, rng.choice([8, 10, 12]))
    first = writes[0]
    for i in range(first):
        assert T.assess(trace.events, trace.tasks, open_questions=OPEN,
                        upcoming=(f"t{i}",)).verdict == T.ON_TRACK, (seed, i)
        trace.succeeded(f"t{i}")
    found = T.assess(trace.events, trace.tasks, open_questions=OPEN, upcoming=(f"t{first}",))
    assert found.verdict == T.DRIFTING, (seed, found)
    assert found.signals["pending_side_effects"] == ["files_write"]
    assert not any(e["task_id"] == f"t{first}" for e in trace.events)


def _recording_dispatch(calls):
    async def dispatch(capability, arguments, *, run=None):
        calls.append(capability)
        return {"ok": True}
    return dispatch


def test_a_first_batch_of_writes_under_an_open_question_is_stopped_before_any_starts(tmp_path):
    store = Store(tmp_path / "ada.sqlite3")
    contract = c.ObjectiveContract(goal="tidy the folder", open_questions=OPEN)
    run_id = _compile_under(store, contract, "files_write", 3, chained=False)
    calls: list[str] = []

    run = _drive(store, run_id, _recording_dispatch(calls))

    assert run["status"] == O.RUN_PAUSED, run
    assert calls == []
    assert not [e for e in store.objective_events(run_id) if e["event"] == O.EVENT_TASK_STARTED]
    assert "about to start" in run["blocker"]
    flagged = [e for e in store.objective_events(run_id) if e["event"] == T.EVENT_FLAGGED]
    assert len(flagged) == 1
    assert flagged[0]["detail"]["signals"]["pending_side_effects"] == ["files_write"] * 3


def test_carrying_on_runs_the_steps_without_asking_about_the_question_again(tmp_path):
    store = Store(tmp_path / "ada.sqlite3")
    contract = c.ObjectiveContract(goal="tidy the folder", open_questions=OPEN)
    run_id = _compile_under(store, contract, "files_write", 3, chained=False)
    calls: list[str] = []
    dispatch = _recording_dispatch(calls)
    assert _drive(store, run_id, dispatch)["status"] == O.RUN_PAUSED

    assert O.resume_run(store, run_id=run_id, reason="carry on", executor_id="test")
    run = _drive(store, run_id, dispatch)

    assert run["status"] == O.RUN_COMPLETED, run
    assert calls == ["files_write"] * 3
    assert len([e for e in store.objective_events(run_id) if e["event"] == T.EVENT_FLAGGED]) == 1


# -- "carry on" survives a run longer than the store's recent-events window ----------

def _pad(store, run_id, n):
    """`n` events that mean nothing to the trajectory, as a long run records."""
    for _ in range(n):
        store.append_objective_event(run_id, "run.note")


def _long_run_under_an_open_question(tmp_path, *, flagged):
    store = Store(tmp_path / "ada.sqlite3")
    contract = c.ObjectiveContract(goal="tidy the folder", open_questions=OPEN)
    run_id = _compile_under(store, contract, "files_write", 3, chained=False)
    if flagged:
        store.append_objective_event(run_id, T.EVENT_FLAGGED, detail={
            "verdict": T.DRIFTING, "signals": {"unanswered_questions": len(OPEN)}})
        store.append_objective_event(run_id, O.EVENT_RUN_RESUMED, detail={"reason": "carry on"})
    _pad(store, run_id, 600)
    return store, run_id, tuple(t["task_id"] for t in store.objective_tasks(run_id))


def test_carry_on_is_remembered_after_more_events_than_the_recent_window_holds(tmp_path):
    store, run_id, tasks = _long_run_under_an_open_question(tmp_path, flagged=True)
    kept = {e["event"] for e in store.recent_objective_events(run_id)}
    assert T.EVENT_FLAGGED not in kept and O.EVENT_RUN_RESUMED not in kept

    found = T.for_run(store, run_id, upcoming=tasks)

    assert found.verdict == T.ON_TRACK, found
    assert found.signals["pending_side_effects"] == []


def test_a_long_run_never_asked_about_the_question_is_still_asked(tmp_path):
    store, run_id, tasks = _long_run_under_an_open_question(tmp_path, flagged=False)

    found = T.for_run(store, run_id, upcoming=tasks)

    assert found.verdict == T.DRIFTING, found
    assert found.signals["pending_side_effects"] == ["files_write"] * 3


# -- drift that builds up over more events than the recent window holds ----------------

_LONG_DRIFT = [
    ("amendments", "graph_edited", T.AMENDMENTS),
    ("takeovers", O.EVENT_WATCHDOG_ORPHANED, T.TAKEOVERS),
]


def _long_run_with(tmp_path, seed, name, count, *, resume_after=False):
    """A real store run with `count` `name` events scattered among a few hundred
    ordinary events, so the early ones fall out of `recent_objective_events`."""
    rng = random.Random(seed)
    store = Store(tmp_path / "ada.sqlite3")
    run_id = _compile_under(store, c.ObjectiveContract(goal="tidy the folder"),
                            "system_get_info", 3)
    for _ in range(count):
        _pad(store, run_id, rng.randint(150, 250))
        store.append_objective_event(run_id, name)
    if resume_after:
        store.append_objective_event(run_id, O.EVENT_RUN_RESUMED, detail={"reason": "carry on"})
    _pad(store, run_id, rng.randint(150, 250))
    return store, run_id


@pytest.mark.parametrize("shape,name,threshold", _LONG_DRIFT)
@pytest.mark.parametrize("seed", range(5))
def test_drift_spread_over_a_long_run_is_flagged_though_its_events_are_not_recent(
        tmp_path, seed, shape, name, threshold):
    store, run_id = _long_run_with(tmp_path, seed, name, threshold)
    recent = [e for e in store.recent_objective_events(run_id) if e["event"] == name]
    assert len(recent) < threshold, "the benchmark must outlive the recent window"

    found = T.for_run(store, run_id)

    assert found.verdict == T.DRIFTING, found
    assert found.signals[shape] == threshold


@pytest.mark.parametrize("shape,name,threshold", _LONG_DRIFT)
@pytest.mark.parametrize("seed", range(5))
def test_a_long_run_one_short_of_the_threshold_is_not_flagged(
        tmp_path, seed, shape, name, threshold):
    store, run_id = _long_run_with(tmp_path, seed, name, threshold - 1)

    found = T.for_run(store, run_id)

    assert found.verdict == T.ON_TRACK, found
    assert found.signals[shape] == threshold - 1


@pytest.mark.parametrize("shape,name,threshold", _LONG_DRIFT)
def test_a_long_run_that_was_resumed_forgives_the_drift_before_the_resume(
        tmp_path, shape, name, threshold):
    store, run_id = _long_run_with(tmp_path, 0, name, threshold, resume_after=True)

    found = T.for_run(store, run_id)

    assert found.verdict == T.ON_TRACK, found
    assert found.signals[shape] == 0
