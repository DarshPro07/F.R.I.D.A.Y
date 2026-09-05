"""
The objective ledger (PRD v3.1 FR-001, FR-002, FR-052, FR-053).

    FR-001  every objective carries class, risk tier, budgets, constraints,
            approvals, evidence and blocker as durable columns; the PRD 9.2
            shape is assembled from the rows and is inspectable end-to-end
    FR-002  the class is decided at admission, deterministically
    FR-052  every consequential step writes expected -> actual -> method
            -> pass/fail to the ledger
    FR-053  COMPLETED requires passing evidence for every succeeded task;
            a task somebody merely SAID finished leaves the run PARTIAL
            with the gap named
"""
from __future__ import annotations

import asyncio
import json

import pytest

from friday import objectives as O
from friday.continuous import ContinuousTaskExecutor
from friday.store import Store


class Registry:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def call(self, capability: str, arguments: dict) -> dict:
        self.calls.append(capability)
        if capability == "boom":
            raise ValueError("no such thing")
        return {"ok": True, "capability": capability,
                "verification": {"method": "return_value", "evidence": "ran"}}


MANIFEST = [{"id": c, "description": c} for c in ("a", "b", "boom")]


def compile_(store: Store, request: str, tasks: list[dict]) -> str:
    return O.compile_objective(store, request=request, tasks=tasks,
                               manifest=MANIFEST, objective_summary=request)["run_id"]


# -- FR-001 / FR-002 -------------------------------------------------------


def test_admission_classifies_and_persists_the_objective_schema():
    store = Store(":memory:")
    run_id = compile_(store, "find why the app crashes after login, fix it and prove it works",
                      [{"capability": "a", "arguments": {"x": 1}},
                       {"capability": "b", "arguments": {}, "dependencies": ["t1"]}])
    ledger = store.objective_ledger(run_id)
    assert ledger["task_class"] == "COMPLEX"
    assert ledger["risk_tier"] == "R1"
    assert ledger["required_capabilities"] == ["a", "b"]
    assert ledger["cost_budget_tokens"] > 0 and ledger["time_budget_s"] == 7200
    assert ledger["retry_budget"] == 3
    assert ledger["current_state"] == O.RunStatus.RUNNING
    assert [s["capability"] for s in ledger["plan_steps"]] == ["a", "b"]
    assert ledger["plan_steps"][1]["dependencies"] == [f"{run_id}-t1"]
    assert ledger["approvals"] == [] and ledger["evidence"] == []
    created = [e for e in store.objective_events(run_id) if e["event"] == "run.created"]
    assert created and created[0]["detail"]["classification"]["task_class"] == "COMPLEX"


def test_critical_wording_sets_the_r3_floor():
    store = Store(":memory:")
    run_id = compile_(store, "delete all the old backups permanently",
                      [{"capability": "a", "arguments": {}}])
    ledger = store.objective_ledger(run_id)
    assert ledger["task_class"] == "CRITICAL" and ledger["risk_tier"] == "R3"


def test_schema_columns_survive_a_reopen_and_the_ledger_is_traceable():
    """FR-001 acceptance: inspect, persist, resume, trace request->evidence."""
    store = Store(":memory:")
    run_id = compile_(store, "open spotify", [{"capability": "a", "arguments": {}}])
    store.touch_objective_run(run_id, project_scope="friday", blocker="waiting on wifi",
                              constraints=["no purchases"], source_channel="telegram")
    # A restart reconciles by re-opening the same run id.
    run = store.objective_run(run_id)
    store.open_objective_run(run_id, request=run["request"],
                             objective_summary=run["objective_summary"], status="RUNNING")
    ledger = store.objective_ledger(run_id)
    assert ledger["task_class"] == "TRIVIAL"          # not wiped by the reopen
    assert ledger["project_scope"] == "friday"
    assert ledger["blocker"] == "waiting on wifi"
    assert ledger["constraints"] == ["no purchases"]
    assert ledger["source_channel"] == "telegram"
    assert ledger["intent"] == "open spotify"


# -- FR-052 append-only evidence and approvals ----------------------------


def test_evidence_and_approvals_are_append_only_with_sequence_and_time():
    store = Store(":memory:")
    run_id = compile_(store, "open spotify", [{"capability": "a", "arguments": {}}])
    n1 = store.append_objective_evidence(run_id, task_id="t", expected="a()",
                                         actual="ok", method="return_value", passed=True)
    n2 = store.append_objective_evidence(run_id, task_id="t", expected="a()",
                                         actual="nope", method="retest", passed=False)
    assert (n1, n2) == (1, 2)
    n = store.append_objective_approval(run_id, operation="files_delete", target="C:/x",
                                        parameters={"permanent": True}, decision="approved",
                                        decided_by="owner", nonce="abc", expires_at="2026-09-05")
    assert n == 1
    ledger = store.objective_ledger(run_id)
    assert [e["seq"] for e in ledger["evidence"]] == [1, 2]
    assert all(e["at"] for e in ledger["evidence"])
    assert ledger["evidence"][1]["passed"] is False
    assert ledger["approvals"][0]["target"] == "C:/x"
    assert ledger["approvals"][0]["parameters"] == {"permanent": True}
    with pytest.raises(LookupError):
        store.append_objective_evidence("nope", expected="", actual="", method="", passed=True)
    with pytest.raises(ValueError):
        store._append_objective_json(run_id, "summary", {})   # noqa: SLF001


# -- FR-052 / FR-053 through the real executor -----------------------------


@pytest.mark.asyncio
async def test_every_step_writes_evidence_and_completion_enumerates_it():
    store = Store(":memory:")
    registry = Registry()
    run_id = compile_(store, "open spotify then open chrome",
                      [{"capability": "a", "arguments": {"q": 1}},
                       {"capability": "b", "arguments": {}, "dependencies": ["t1"]}])
    executor = ContinuousTaskExecutor(store, registry.call, executor_id="exec-1")
    row = await _run_to_terminal(store, executor, run_id)
    assert row["status"] == O.RunStatus.COMPLETED
    ledger = store.objective_ledger(run_id)
    assert len(ledger["evidence"]) == 2
    by_task = {e["task_id"]: e for e in ledger["evidence"]}
    assert by_task[f"{run_id}-t1"]["expected"].startswith('a({"q": 1}')
    assert by_task[f"{run_id}-t1"]["method"] == "return_value"
    assert all(e["passed"] for e in ledger["evidence"])
    assert ledger["final_result"]["succeeded"] == 2


@pytest.mark.asyncio
async def test_a_failed_step_is_evidence_too():
    store = Store(":memory:")
    registry = Registry()
    run_id = compile_(store, "open spotify",
                      [{"capability": "boom", "arguments": {}, "max_attempts": 1}])
    executor = ContinuousTaskExecutor(store, registry.call, executor_id="exec-1")
    row = await _run_to_terminal(store, executor, run_id)
    assert row["status"] == O.RunStatus.FAILED
    ledger = store.objective_ledger(run_id)
    failed = [e for e in ledger["evidence"] if not e["passed"]]
    assert failed and "no such thing" in failed[-1]["actual"]
    assert failed[-1]["method"] == "failure"


@pytest.mark.asyncio
async def test_completion_gate_refuses_a_task_that_was_only_said_to_be_done():
    """FR-053: a worker (or anyone) flipping a task to SUCCEEDED without
    evidence cannot make the objective COMPLETED."""
    store = Store(":memory:")
    registry = Registry()
    run_id = compile_(store, "open spotify then open chrome",
                      [{"capability": "a", "arguments": {}},
                       {"capability": "b", "arguments": {}, "dependencies": ["t1"]}])
    # Somebody claims t2 is done before it ever ran; t1 runs for real.
    store.update_objective_task(f"{run_id}-t2", status=O.TaskStatus.SUCCEEDED,
                                result={"claimed": True}, evidence="worker said so")
    executor = ContinuousTaskExecutor(store, registry.call, executor_id="exec-1")
    row = await _run_to_terminal(store, executor, run_id)
    assert row["status"] == O.RunStatus.PARTIAL
    summary = json.loads(row["summary"]) if isinstance(row["summary"], str) else row["summary"]
    assert summary["outcome"].startswith("evidence_gap:")
    assert f"{run_id}-t2" in summary["outcome"]
    events = [e["event"] for e in store.objective_events(run_id)]
    assert "completion.gate_refused" in events
    assert registry.calls == ["a"]                 # t2 was never dispatched


async def _run_to_terminal(store: Store, executor: ContinuousTaskExecutor,
                           run_id: str) -> dict:
    """Drive the run with the executor's own loop (the same entry the
    restart tests use), stopping the background driver first so the
    test owns the loop."""
    executor.stop()
    await executor._drive_until_done(run_id)   # noqa: SLF001
    return store.objective_run(run_id)
