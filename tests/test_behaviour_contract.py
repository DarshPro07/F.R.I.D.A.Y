"""
The objective's contract is enforced, not just recorded (PRD v7 BCON-001).

What the person forbids and the limits they set are governance: a plan that
does what they forbade is not compiled, whoever wrote the plan, and "at most
N changes" is a ceiling the executor holds - the change after the Nth is
refused, not made.
"""
from __future__ import annotations

import asyncio

import pytest

from friday import capabilities
from friday import capability_runtime as CR
from friday import contracts as c
from friday import fsjail
from friday import objectives as O
from friday import planner as P
from friday import policy as PO
from friday.continuous import ContinuousTaskExecutor
from friday.store import Store
from friday.toolsets import files as F


def test_the_contract_reads_what_is_forbidden_and_what_is_limited():
    text = "create a.txt, create b.txt, at most 2 changes, and do not delete anything"
    contract = P.contract_for(P.plan_objective(text), text)
    assert contract.governance == {"max_side_effects": 2, "forbidden": ["delete"]}


def test_a_named_prohibition_protects_what_it_names():
    text = "tidy up notes.txt but don't delete config.yaml"
    contract = P.contract_for(P.plan_objective(text), text)
    assert contract.governance.get("protected") == {"delete": ["config.yaml"]}


def _compile(store, tasks, contract):
    return O.compile_objective(store, request=contract.goal, tasks=tasks,
                               manifest=capabilities.as_dicts(), objective_summary=contract.goal,
                               provenance=c.PERSON, attended=True, contract=contract)


def test_a_plan_that_does_what_the_person_forbade_is_not_compiled(tmp_path):
    store = Store(tmp_path / "ada.sqlite3")
    contract = c.ObjectiveContract(goal="clean up, do not delete anything",
                                   governance={"forbidden": ["delete"]})
    with pytest.raises(O.ForbiddenByContract, match="delete"):
        _compile(store, [{"capability": "files_recycle", "arguments": {"path": "old.txt"}}],
                 contract)
    assert store.objective_runs(limit=5) == []


def test_a_protected_name_cannot_be_the_target_of_what_was_forbidden(tmp_path):
    store = Store(tmp_path / "ada.sqlite3")
    contract = c.ObjectiveContract(goal="tidy, but don't delete config.yaml",
                                   governance={"protected": {"delete": ["config.yaml"]}})
    with pytest.raises(O.ForbiddenByContract, match="config.yaml"):
        _compile(store, [{"capability": "files_delete", "arguments": {"path": "config.yaml"}}],
                 contract)
    # Deleting something else is not what was forbidden.
    _compile(store, [{"capability": "files_delete", "arguments": {"path": "old.txt"}}],
             contract)


def test_the_change_after_the_limit_is_refused_not_made(tmp_path, monkeypatch):
    root = tmp_path / "work"
    root.mkdir()
    monkeypatch.chdir(root)
    monkeypatch.setattr(fsjail, "configured_roots", lambda: [root])
    F.reset_jail(fsjail.FileJail(roots=(root,)))
    store = Store(tmp_path / "ada.sqlite3")
    contract = c.ObjectiveContract(goal="create three notes, at most 2 changes",
                                   governance={"max_side_effects": 2})
    tasks = [{"capability": "files_create", "arguments": {"path": f"n{i}.txt", "content": "x"},
              "dependencies": [f"t{i - 1}"] if i > 1 else []} for i in (1, 2, 3)]
    for index, task in enumerate(tasks, 1):
        task["id"] = f"t{index}"
    created = _compile(store, tasks, contract)
    run_id = created["run_id"]
    dispatch = CR.build_dispatch(engine=PO.PolicyEngine(autonomy=PO.FULL))

    async def drive():
        executor = ContinuousTaskExecutor(store, call_capability=dispatch)
        try:
            loop = asyncio.get_running_loop()
            deadline = loop.time() + 15
            while loop.time() < deadline:
                status = store.objective_run(run_id)["status"]
                if status in O.RUN_TERMINAL or status in O.RUN_WAITING_STATUSES:
                    return
                await executor.start(run_id)
                await asyncio.sleep(0.02)
        finally:
            executor.stop()

    try:
        asyncio.run(drive())
    finally:
        F.reset_jail(None)
    written = sorted(p.name for p in root.glob("n*.txt"))
    assert written == ["n1.txt", "n2.txt"], written
    third = [t for t in store.objective_tasks(run_id) if t["task_id"].endswith("t3")][0]
    assert third["status"] != O.TaskStatus.SUCCEEDED
    assert "at most 2" in str(third.get("evidence") or ""), third


def test_a_change_still_in_flight_counts_toward_the_limit(tmp_path):
    """A delegated change still running on its worker is a change: the next
    one may not go through merely because the first has not finished."""
    store = Store(tmp_path / "ada.sqlite3")
    contract = c.ObjectiveContract(goal="delegate the fix and write a note, at most 1 change",
                                   governance={"max_side_effects": 1})
    tasks = [{"id": "t1", "capability": "hermes_delegate",
              "arguments": {"task": "fix the build"}},
             {"id": "t2", "capability": "files_create",
              "arguments": {"path": "note.txt", "content": "x"}}]
    run_id = _compile(store, tasks, contract)["run_id"]
    made: list[str] = []

    async def port(capability, arguments):
        if capability in ("hermes_delegate", "hermes_status"):
            return {"status": "working", "work_run_id": "WR-1"}
        made.append(capability)
        return {"status": "succeeded", "output": {}, "side_effects": ["wrote note.txt"]}

    def second():
        return [t for t in store.objective_tasks(run_id) if t["task_id"].endswith("t2")][0]

    async def drive():
        executor = ContinuousTaskExecutor(store, call_capability=port)
        try:
            loop = asyncio.get_running_loop()
            deadline = loop.time() + 15
            while loop.time() < deadline and second()["status"] not in O.TASK_TERMINAL:
                await executor.start(run_id)
                await asyncio.sleep(0.02)
        finally:
            executor.stop()

    asyncio.run(drive())
    assert made == [], "a second change went through while the first was in flight"
    assert "at most 1" in str(second().get("evidence") or ""), second()


def test_an_edit_to_the_plan_is_held_to_the_same_contract(tmp_path):
    """The contract binds the plan whoever changes it and whenever: an amended
    or appended step that does what was forbidden is refused, and says so."""
    from friday import modification as M

    store = Store(tmp_path / "ada.sqlite3")
    contract = c.ObjectiveContract(
        goal="tidy up, never shut the machine down, and don't delete config.yaml",
        governance={"forbidden": ["power"], "protected": {"delete": ["config.yaml"]}})
    run_id = _compile(store, [{"capability": "files_delete", "arguments": {"path": "old.txt"}}],
                      contract)["run_id"]

    edit = M.amend_task(store, run_id, "delete old.txt", {"path": "config.yaml"})
    assert edit.outcome == M.REFUSED and "config.yaml" in edit.reason, edit
    (task,) = store.objective_tasks(run_id)
    assert task["arguments"] == {"path": "old.txt"}

    edit = M.append_task(store, run_id, "power_shutdown", {})
    assert edit.outcome == M.REFUSED and "power" in edit.reason, edit
    assert len(store.objective_tasks(run_id)) == 1

    # What the contract does not rule out is still editable.
    assert M.amend_task(store, run_id, "delete old.txt", {"path": "older.txt"}).outcome == M.APPLIED
