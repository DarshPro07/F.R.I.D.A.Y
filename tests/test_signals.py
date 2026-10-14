"""
SIGNAL-001: human signals reach an objective through one bus.

Two behaviours carry the requirement:

  the amendment barrier   an amendment sent from a second surface while a
                          worker runs stops the old version's dependent
                          actions - the running call is never rewritten, but
                          nothing that depends on it starts until the
                          amendment is applied or the person says go on.
  waiting survives        an objective parked for an approval resumes from a
                          signal sent to a freshly opened store, i.e. after
                          a restart.

Every signal is written to `objective_events` before anything moves, so what
each surface said and what it did are answerable from the trace.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from friday import signals as S
from friday import objectives as O
from friday import requirements as REQ
from friday.continuous import ContinuousTaskExecutor
from friday.contracts import PERSON
from friday.objectives import RUN_TERMINAL, compile_objective
from friday.store import Store

MANIFEST = [{"id": "work", "description": "do one piece of work",
             "arguments": {"name": "str"}}]


class Probe:
    """Capability stand-in: records what started and parks each task."""

    def __init__(self) -> None:
        self.started: list[str] = []
        self.args: dict[str, dict] = {}
        self.gates: dict[str, asyncio.Event] = {}

    def gate(self, name: str) -> asyncio.Event:
        return self.gates.setdefault(name, asyncio.Event())

    async def call(self, capability: str, arguments: dict) -> dict:
        name = arguments["name"]
        self.started.append(name)
        self.args[name] = arguments
        await self.gate(name).wait()
        return {"ok": True, "name": name}


class OneWorker:
    def worker_capacity(self, objective_id: str = "") -> int:
        return 3

    def active(self, kind=None) -> int:
        return 0

    def justify_parallel(self, objective_id: str) -> None:
        pass


async def until(condition, timeout: float = 3.0) -> None:
    deadline = asyncio.get_event_loop().time() + timeout
    while not condition():
        assert asyncio.get_event_loop().time() < deadline, "timed out waiting"
        await asyncio.sleep(0.01)


def start(store, tasks):
    probe = Probe()
    run = compile_objective(
        store, request="signals", tasks=tasks, manifest=MANIFEST,
        objective_summary="signals", provenance=PERSON, attended=True)
    executor = ContinuousTaskExecutor(store, probe.call, executor_id="sig",
                                      governor=lambda: OneWorker())
    driving = asyncio.ensure_future(executor.start(run["run_id"]))
    return run["run_id"], probe, executor, driving


def two_step():
    return [
        {"capability": "work", "arguments": {"name": "a"},
         "surface": ["friday/a.py"]},
        {"capability": "work", "arguments": {"name": "b"},
         "surface": ["friday/b.py"], "dependencies": ["t1"]},
    ]


def names_of(store, run_id):
    return [e["event"] for e in store.objective_events(run_id)]


# --- the bus ---------------------------------------------------------------


def test_the_bus_takes_the_eight_kinds_from_the_five_surfaces():
    assert set(S.KINDS) == {
        "USER_CORRECTION", "USER_APPROVAL", "USER_DENIAL", "USER_ANSWER",
        "USER_STOP", "USER_PRIORITY_CHANGE", "USER_POLICY_CHANGE", "USER_BACK"}
    assert set(S.SURFACES) == {"desktop", "voice", "telegram", "web", "mobile"}


def test_every_signal_is_written_down_with_its_surface_before_it_acts():
    store = Store(":memory:")
    store.open_objective_run("R", request="x", objective_summary="x",
                             status=O.RUN_RUNNING)
    for kind in S.KINDS:
        got = S.send(store, "R", kind, surface="telegram",
                     payload={"note": "hello"})
        assert got.signal_id > 0 and got.kind == kind
    received = [e for e in store.objective_events("R")
                if e["event"] == S.EVENT_RECEIVED]
    assert [e["detail"]["kind"] for e in received] == list(S.KINDS)
    assert {e["detail"]["surface"] for e in received} == {"telegram"}
    assert all(e["detail"]["payload"] == {"note": "hello"} for e in received)


def test_an_unknown_kind_surface_or_run_is_refused_and_writes_nothing():
    store = Store(":memory:")
    store.open_objective_run("R", request="x", objective_summary="x",
                             status=O.RUN_RUNNING)
    before = len(store.objective_events("R"))
    with pytest.raises(ValueError):
        S.send(store, "R", "USER_SHRUG", surface="desktop")
    with pytest.raises(ValueError):
        S.send(store, "R", "USER_STOP", surface="fax")
    with pytest.raises(ValueError):
        S.send(store, "R", "USER_STOP", surface="desktop", payload="rm -rf")
    with pytest.raises(ValueError):
        S.send(store, "NO-SUCH-RUN", "USER_STOP", surface="desktop")
    assert len(store.objective_events("R")) == before


def test_stop_from_one_surface_and_back_from_another():
    store = Store(":memory:")
    store.open_objective_run("R", request="x", objective_summary="x",
                             status=O.RUN_RUNNING)
    stopped = S.send(store, "R", "USER_STOP", surface="voice")
    assert stopped.applied and stopped.effect == S.PAUSED
    assert store.objective_run("R")["status"] == O.RUN_PAUSED
    back = S.send(store, "R", "USER_BACK", surface="mobile")
    assert back.applied and back.effect == S.RESUMED
    assert store.objective_run("R")["status"] == O.RUN_RUNNING


# --- waiting survives a restart --------------------------------------------


def test_a_run_waiting_for_approval_resumes_from_a_signal_after_a_restart(tmp_path):
    path = tmp_path / "objectives.sqlite3"
    store = Store(path)
    store.open_objective_run("R", request="x", objective_summary="x",
                             status=O.RUN_WAITING_PERMISSION)
    store.save_objective_task(
        task_id="t1", run_id="R", capability="files_write",
        arguments=json.dumps({"path": "notes.md"}), dependencies="[]",
        status=O.TaskStatus.WAITING)
    store.update_objective_task(
        "t1", failure_kind=O.FailureKind.USER_REQUIRED,
        evidence="approval_required: write notes.md")
    del store                                   # the process dies here

    reopened = Store(path)                      # and a new one starts
    assert reopened.objective_run("R")["status"] == O.RUN_WAITING_PERMISSION
    got = S.send(reopened, "R", "USER_APPROVAL", surface="telegram",
                 payload={"operation": "files_write", "target": "notes.md"})
    assert got.applied and got.effect == S.RESUMED, got.reason
    assert reopened.objective_run("R")["status"] == O.RUN_RUNNING
    assert reopened.objective_task("t1")["status"] == O.TaskStatus.READY
    seen = [e for e in reopened.objective_events("R")
            if e["event"] == S.EVENT_RECEIVED]
    assert seen and seen[0]["detail"]["surface"] == "telegram"


def test_an_approval_for_a_different_action_does_not_resume_it():
    store = Store(":memory:")
    store.open_objective_run("R", request="x", objective_summary="x",
                             status=O.RUN_WAITING_PERMISSION)
    store.save_objective_task(
        task_id="t1", run_id="R", capability="files_write",
        arguments=json.dumps({"path": "notes.md"}), dependencies="[]",
        status=O.TaskStatus.WAITING)
    store.update_objective_task(
        "t1", failure_kind=O.FailureKind.USER_REQUIRED,
        evidence="approval_required: write notes.md")
    got = S.send(store, "R", "USER_APPROVAL", surface="web",
                 payload={"operation": "files_write", "target": "other.md"})
    assert not got.applied and got.effect == S.IGNORED
    assert store.objective_run("R")["status"] == O.RUN_WAITING_PERMISSION


# --- the amendment barrier -------------------------------------------------


@pytest.mark.asyncio
async def test_an_amendment_from_a_second_surface_stops_the_old_versions_dependents():
    store = Store(":memory:")
    run_id, probe, executor, driving = start(store, two_step())
    await until(lambda: probe.started == ["a"])          # the worker is running

    got = S.send(store, run_id, "USER_CORRECTION", surface="mobile",
                 payload={"said": "actually use the other source"})
    assert got.applied and got.effect == S.BARRIER_RAISED
    assert S.barrier_up(store, run_id)

    probe.gate("a").set()                                # the old call finishes
    assert await asyncio.wait_for(driving, 3) is True
    await until(lambda: store.objective_run(run_id)["status"] == O.RUN_PAUSED)
    executor.stop()

    assert probe.started == ["a"], "the dependent of the old version started"
    run = store.objective_run(run_id)
    assert run["status"] == O.RUN_PAUSED and "mobile" in run["blocker"]
    assert store.objective_task(f"{run_id}-t1")["status"] == O.TaskStatus.SUCCEEDED
    assert store.objective_task(f"{run_id}-t2")["status"] != O.TaskStatus.RUNNING


@pytest.mark.asyncio
async def test_user_back_releases_the_barrier_and_the_run_carries_on():
    store = Store(":memory:")
    run_id, probe, executor, driving = start(store, two_step())
    await until(lambda: probe.started == ["a"])
    S.send(store, run_id, "USER_CORRECTION", surface="mobile")
    probe.gate("a").set()
    await asyncio.wait_for(driving, 3)
    await until(lambda: store.objective_run(run_id)["status"] == O.RUN_PAUSED)
    assert probe.started == ["a"]

    back = S.send(store, run_id, "USER_BACK", surface="desktop")
    assert back.applied and back.effect == S.RESUMED
    assert not S.barrier_up(store, run_id)

    probe.gate("b").set()
    assert await asyncio.wait_for(executor.start(run_id), 3) is True
    await until(lambda: store.objective_run(run_id)["status"] in RUN_TERMINAL)
    executor.stop()
    assert probe.started == ["a", "b"]
    events = names_of(store, run_id)
    assert S.EVENT_BARRIER_RAISED in events and S.EVENT_BARRIER_RELEASED in events


def test_an_amendment_applied_through_the_bus_edits_the_future_and_lifts_the_barrier():
    store = Store(":memory:")
    store.open_objective_run("R", request="x", objective_summary="x",
                             status=O.RUN_RUNNING)
    store.save_objective_task(
        task_id="t1", run_id="R", capability="work",
        arguments=json.dumps({"name": "research", "source": "reuters"}),
        dependencies="[]", status=O.TaskStatus.QUEUED)
    got = S.send(store, "R", "USER_CORRECTION", surface="mobile",
                 payload={"said": "use bbc instead for the research",
                          "amend": {"description": "research",
                                    "changes": {"source": "bbc"}}})
    assert got.effect == S.BARRIER_RELEASED, got.reason
    assert store.objective_task("t1")["arguments"]["source"] == "bbc"
    assert not S.barrier_up(store, "R")


# -- denial and answer: the two signals that settle a waiting step ----------

def _parked_for_approval(store, run_id="R"):
    store.open_objective_run(run_id, request="x", objective_summary="x",
                             status=O.RUN_WAITING_PERMISSION)
    store.save_objective_task(
        task_id="t1", run_id=run_id, capability="files_write",
        arguments=json.dumps({"path": "notes.md"}), dependencies="[]",
        status=O.TaskStatus.WAITING)
    store.update_objective_task(
        "t1", failure_kind=O.FailureKind.USER_REQUIRED,
        evidence="approval_required: write notes.md")
    store.save_objective_task(
        task_id="t2", run_id=run_id, capability="work",
        arguments=json.dumps({"name": "after"}), dependencies='["t1"]',
        status=O.TaskStatus.QUEUED)


def test_a_denial_stops_the_waiting_step_and_is_on_the_record():
    store = Store(":memory:")
    _parked_for_approval(store)
    got = S.send(store, "R", "USER_DENIAL", surface="mobile",
                 payload={"operation": "files_write", "target": "notes.md"})
    assert got.applied and got.effect == S.DENIED, got.reason
    task = store.objective_task("t1")
    assert task["status"] == O.TaskStatus.FAILED
    assert "denied" in task["evidence"] and "mobile" in task["evidence"]
    # The run is handed back to the driver, which skips what hung off the step.
    assert store.objective_run("R")["status"] == O.RUN_RUNNING
    ledger = store.objective_ledger("R")
    assert [a["decision"] for a in ledger["approvals"]][-1] == "DENIED"
    assert ledger["approvals"][-1]["decided_by"] == "mobile"


def test_a_denial_for_a_different_action_or_an_idle_run_changes_nothing():
    store = Store(":memory:")
    _parked_for_approval(store)
    wrong = S.send(store, "R", "USER_DENIAL", surface="web",
                   payload={"operation": "files_delete"})
    assert wrong.effect == S.IGNORED
    assert store.objective_task("t1")["status"] == O.TaskStatus.WAITING
    store.open_objective_run("Q", request="y", objective_summary="y",
                             status=O.RUN_RUNNING)
    assert S.send(store, "Q", "USER_DENIAL", surface="web").effect == S.IGNORED


def test_a_targetless_parked_action_is_never_approved_from_any_surface():
    """Owner ruling 2026-10-13 (row 10): no target, no approval. A parked
    step that names nothing cannot be approved from any surface, even with a
    blank operation/target; it can only be denied (or amended)."""
    from friday import continuous

    store = Store(":memory:")
    store.open_objective_run("R", request="x", objective_summary="x",
                             status=O.RUN_RUNNING)
    store.save_objective_task(
        task_id="t1", run_id="R", capability="files_write",
        arguments=json.dumps({"content": "x"}), dependencies="[]",
        status=O.TaskStatus.RUNNING)
    executor = ContinuousTaskExecutor(store, Probe().call, executor_id="sig",
                                      governor=lambda: OneWorker())
    executor._park_for_approval("R", "t1", store.objective_task("t1"),
                                "approval_required: write a file")
    run = store.objective_run("R")
    assert run["status"] == O.RUN_WAITING_PERMISSION
    assert "names no target, cannot be approved" in run["blocker"]
    told = store.pending_objective_deliveries()[-1]["message"].lower()
    assert "say yes" not in told and "deny" in told

    for payload in ({"operation": "files_write"}, {}):
        got = S.send(store, "R", "USER_APPROVAL", surface="telegram",
                     payload=payload)
        assert got.effect == S.IGNORED and "names no target" in got.reason, got
        assert store.objective_run("R")["status"] == O.RUN_WAITING_PERMISSION
        assert store.objective_task("t1")["status"] == O.TaskStatus.WAITING
    assert continuous.resume_after_approval(store, "R", decided_by="boss") is False
    decisions = [a["decision"] for a in store.objective_ledger("R")["approvals"]]
    assert "APPROVED" not in decisions

    denied = S.send(store, "R", "USER_DENIAL", surface="telegram", payload={})
    assert denied.effect == S.DENIED, denied.reason


@pytest.mark.parametrize("arguments", [
    {"path": [], "content": "x"}, {"target": {}}, {"name": 0}, {"url": "  "}])
def test_an_empty_valued_target_counts_as_no_target(arguments):
    store = Store(":memory:")
    store.open_objective_run("R", request="x", objective_summary="x",
                             status=O.RUN_RUNNING)
    store.save_objective_task(
        task_id="t1", run_id="R", capability="files_write",
        arguments=json.dumps(arguments), dependencies="[]",
        status=O.TaskStatus.RUNNING)
    executor = ContinuousTaskExecutor(store, Probe().call, executor_id="sig",
                                      governor=lambda: OneWorker())
    executor._park_for_approval("R", "t1", store.objective_task("t1"),
                                "approval_required: write a file")
    got = S.send(store, "R", "USER_APPROVAL", surface="telegram", payload={})
    assert got.effect == S.IGNORED and "names no target" in got.reason, got


def test_a_development_run_parked_on_its_root_is_approvable():
    store = Store(":memory:")
    store.open_objective_run("R", request="x", objective_summary="x",
                             status=O.RUN_WAITING_PERMISSION)
    store.save_objective_task(
        task_id="t1", run_id="R", capability="development_run",
        arguments=json.dumps({"work": "x", "root": "C:/repo"}),
        dependencies="[]", status=O.TaskStatus.WAITING)
    store.update_objective_task(
        "t1", failure_kind=O.FailureKind.USER_REQUIRED,
        evidence="approval_required: development_run")
    got = S.send(store, "R", "USER_APPROVAL", surface="web",
                 payload={"operation": "development_run", "target": "C:/repo"})
    assert got.effect == S.RESUMED, got.reason
    assert store.objective_task("t1")["status"] == O.TaskStatus.READY


def test_an_answer_resumes_the_run_held_for_an_assumption_after_a_restart(tmp_path):
    path = tmp_path / "objectives.sqlite3"
    store = Store(path)
    store.open_objective_run("R", request="x", objective_summary="x",
                             status=O.RUN_WAITING_QUESTION)
    store.save_objective_task(
        task_id="t1", run_id="R", capability="work",
        arguments=json.dumps({"name": "a"}), dependencies="[]",
        status=O.TaskStatus.QUEUED)
    entry = REQ.Assumption(statement="the report is for Q3", origin="request",
                           confidence=REQ.LOW, consequence=REQ.HIGH,
                           validation="ask the person")
    store.save_assumption("R", key=entry.key, **entry.to_dict())
    del store

    reopened = Store(path)
    got = S.send(reopened, "R", "USER_ANSWER", surface="voice",
                 payload={"answer": "yes, Q3"})
    assert got.applied and got.effect == S.ANSWERED, got.reason
    assert reopened.objective_run("R")["status"] == O.RUN_RUNNING
    assert reopened.objective_task("t1")["status"] == O.TaskStatus.READY
    assert [a["state"] for a in reopened.list_assumptions("R")] == ["VALIDATED"]
    applied = [e for e in reopened.objective_events("R")
               if e["event"] == S.EVENT_RECEIVED]
    assert applied[0]["detail"]["payload"]["answer"] == "yes, Q3"


def test_an_answer_to_a_run_that_asked_nothing_is_ignored():
    store = Store(":memory:")
    store.open_objective_run("R", request="x", objective_summary="x",
                             status=O.RUN_RUNNING)
    got = S.send(store, "R", "USER_ANSWER", surface="web",
                 payload={"answer": "yes"})
    assert got.effect == S.IGNORED and not got.applied


# -- every surface goes through the one bus ---------------------------------

def _received(store, run_id):
    return [e["detail"] for e in store.objective_events(run_id)
            if e["event"] == S.EVENT_RECEIVED]


def test_the_pause_and_resume_tools_send_signals_not_direct_calls():
    from friday import contracts as c
    from friday.toolsets import objectives as OT

    store = Store(":memory:")
    store.open_objective_run("R", request="x", objective_summary="x",
                             status=O.RUN_RUNNING)
    run = c.Run.create("signal-test", capability="objectives")
    OT.reset_store(store)
    try:
        assert OT.objective_pause(run, "R", reason="gone").status == c.SUCCEEDED
        assert OT.objective_resume(run, "R").status == c.SUCCEEDED
        sent = _received(store, "R")
    finally:
        OT.reset_store(None)
    assert [s["kind"] for s in sent] == ["USER_STOP", "USER_BACK"]


def test_history_keeps_the_signal_bookkeeping_out_unless_asked_for():
    from friday import contracts as c
    from friday.toolsets import objectives as OT

    store = Store(":memory:")
    store.open_objective_run("R", request="x", objective_summary="x",
                             status=O.RUN_RUNNING)
    S.send(store, "R", "USER_STOP", surface="voice")
    run = c.Run.create("signal-test", capability="objectives")
    OT.reset_store(store)
    try:
        plain = [e["event"] for e in OT.objective_history(run, "R").output]
        full = [e["event"] for e in OT.objective_history(
            run, "R", include_signals=True).output]
    finally:
        OT.reset_store(None)
    assert plain == ["run.paused"]
    assert S.EVENT_RECEIVED in full and S.EVENT_APPLIED in full


def test_the_cli_pause_and_resume_send_signals(monkeypatch, capsys):
    from friday import objective_cli

    store = Store(":memory:")
    store.open_objective_run("R", request="x", objective_summary="x",
                             status=O.RUN_RUNNING)
    monkeypatch.setattr(objective_cli, "_db", lambda: store)
    objective_cli._pause("R")
    objective_cli._resume("R")
    assert [s["kind"] for s in _received(store, "R")] == [
        "USER_STOP", "USER_BACK"]
    assert store.objective_run("R")["status"] == O.RUN_RUNNING


def test_a_verified_connector_resumes_the_auth_parked_run_through_the_bus():
    store = Store(":memory:")
    store.open_objective_run("A", request="x", objective_summary="x",
                             status=O.RUN_WAITING_PERMISSION)
    store.save_objective_task(
        task_id="a1", run_id="A", capability="work",
        arguments=json.dumps({"name": "a"}), dependencies="[]",
        status=O.TaskStatus.WAITING)
    store.update_objective_task(
        "a1", failure_kind=O.FailureKind.USER_REQUIRED,
        evidence="not logged in to anthropic")
    _parked_for_approval(store, "B")      # waits on an approval, not a sign-in

    resumed = S.auth_verified(store, surface="desktop",
                              reason="anthropic verified")
    assert resumed == ["A"]
    assert store.objective_run("A")["status"] == O.RUN_RUNNING
    assert store.objective_run("B")["status"] == O.RUN_WAITING_PERMISSION
    assert [s["kind"] for s in _received(store, "A")] == ["USER_APPROVAL"]
    assert _received(store, "B") == []    # the other run was never signalled
