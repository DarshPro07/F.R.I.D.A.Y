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


def test_a_broken_invariant_stops_the_actions_that_depend_on_it(tmp_path):
    """A result that breaks a data invariant fails its task, and what depends
    on it is skipped rather than run on bad data."""
    store = Store(tmp_path / "ada.sqlite3")
    contract = c.ObjectiveContract(
        goal="pay the invoice in USD, then file the receipt",
        governance={"invariants": [{"field": "currency", "equals": "USD"}]})
    tasks = [{"id": "t1", "capability": "files_create",
              "arguments": {"path": "quote.txt", "content": "x"}},
             {"id": "t2", "capability": "files_create", "dependencies": ["t1"],
              "arguments": {"path": "receipt.txt", "content": "x"}}]
    run_id = _compile(store, tasks, contract)["run_id"]
    made: list[str] = []

    async def port(capability, arguments):
        made.append(arguments["path"])
        return {"status": "succeeded", "output": {"currency": "EUR"}}

    async def drive():
        executor = ContinuousTaskExecutor(store, call_capability=port)
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

    asyncio.run(drive())
    by_id = {t["task_id"].rsplit("t", 1)[-1]: t for t in store.objective_tasks(run_id)}
    assert made == ["quote.txt"], made
    assert by_id["1"]["status"] == O.TaskStatus.FAILED
    assert "INVARIANT" in str(by_id["1"]["evidence"]) and "EUR" in str(by_id["1"]["evidence"])
    assert by_id["2"]["status"] == O.TaskStatus.SKIPPED


def test_a_data_invariant_reads_arguments_and_results_alike():
    governance = {"invariants": [{"field": "currency", "equals": "USD"}]}
    assert c.broken_invariants(governance, {"amount": 5, "currency": "USD"}) == []
    assert c.broken_invariants(governance, {"amount": 5}) == []
    assert c.broken_invariants(governance, {"items": [{"currency": "EUR"}]})
    assert c.broken_invariants({}, {"currency": "EUR"}) == []


def _drive(store, run_id, port, *, through_retries=False):
    async def go():
        executor = ContinuousTaskExecutor(store, call_capability=port)
        try:
            loop = asyncio.get_running_loop()
            deadline = loop.time() + 20
            while loop.time() < deadline:
                status = store.objective_run(run_id)["status"]
                if status in O.RUN_TERMINAL:
                    return
                if status in O.RUN_WAITING_STATUSES and not through_retries:
                    return
                await executor.start(run_id)
                await asyncio.sleep(0.02)
        finally:
            executor.stop()

    asyncio.run(go())


def test_invariants_and_recovery_are_read_from_the_request_text():
    text = "send the invoices, currency must be USD, and retry only the failed ones"
    contract = P.contract_for(P.plan_objective(text), text)
    assert {"field": "currency", "equals": "USD"} in contract.governance["invariants"]
    assert contract.governance["recovery"] == c.RESUME_FAILED_ITEMS_ONLY
    plain = P.contract_for(P.plan_objective("create a.txt"), "create a.txt")
    assert "invariants" not in plain.governance and "recovery" not in plain.governance
    # Ordinary prose that only looks like an invariant is not one.
    for prose in ("the email must be short", "this must be done today"):
        said = P.contract_for(P.plan_objective(prose), prose)
        assert "invariants" not in said.governance, (prose, said.governance)
    text = "deploy it, the region stays eu-west-1"
    region = P.contract_for(P.plan_objective(text), text)
    assert region.governance["invariants"] == [{"field": "region", "equals": "eu-west-1"}]


def test_a_state_precondition_said_in_words_is_read_from_the_request_text():
    text = "once the invoice is approved, send it to the client"
    said = P.contract_for(P.plan_objective(text), text)
    assert said.governance["requires"] == [
        {"field": "invoice_status", "equals": "approved"}]
    for prose in ("when it is ready, send the email", "send the invoice"):
        plain = P.contract_for(P.plan_objective(prose), prose)
        assert "requires" not in plain.governance, (prose, plain.governance)


def test_resume_failed_items_only_retries_only_the_failed_items(tmp_path):
    store = Store(tmp_path / "ada.sqlite3")
    contract = c.ObjectiveContract(
        goal="file three reports, retry only the failed ones",
        governance={"recovery": c.RESUME_FAILED_ITEMS_ONLY})
    run_id = _compile(store, [{"id": "t1", "capability": "files_create",
                               "arguments": {"items": ["a", "b", "c"]}}],
                      contract)["run_id"]
    calls: list[list[str]] = []

    async def port(capability, arguments):
        calls.append(list(arguments["items"]))
        # "b" fails the first time it is tried and succeeds the second.
        rows = [{"item": i, "ok": not (i == "b" and len(calls) == 1)}
                for i in arguments["items"]]
        return {"status": "succeeded", "output": {"items": rows}}

    _drive(store, run_id, port, through_retries=True)
    assert calls ==[["a", "b", "c"], ["b"]], calls
    (task,) = store.objective_tasks(run_id)
    assert task["status"] == O.TaskStatus.SUCCEEDED
    assert task["detail"]["item_outcomes"] == {"a": True, "b": True, "c": True}


def test_the_prds_spelling_of_the_write_limit_is_held_by_the_executor(tmp_path):
    """`max_external_writes` is the PRD's name for the ceiling: the write after
    the Nth is refused, exactly as for the internal `max_side_effects`."""
    store = Store(tmp_path / "ada.sqlite3")
    contract = c.ObjectiveContract(goal="create three notes, at most 2 external writes",
                                   governance={"max_external_writes": 2})
    tasks = [{"id": f"t{i}", "capability": "files_create",
              "arguments": {"path": f"n{i}.txt", "content": "x"},
              "dependencies": [f"t{i - 1}"] if i > 1 else []} for i in (1, 2, 3)]
    run_id = _compile(store, tasks, contract)["run_id"]
    made: list[str] = []

    async def port(capability, arguments):
        made.append(arguments["path"])
        return {"status": "succeeded", "output": {}, "side_effects": [f"wrote {arguments['path']}"]}

    _drive(store, run_id, port)
    assert made == ["n1.txt", "n2.txt"], made
    third = [t for t in store.objective_tasks(run_id) if t["task_id"].endswith("t3")][0]
    assert third["status"] != O.TaskStatus.SUCCEEDED
    assert "at most 2" in str(third.get("evidence") or ""), third


def test_the_write_ceiling_is_the_strictest_limit_the_contract_names():
    assert c.write_ceiling({"max_external_writes": 3, "max_side_effects": 2}) == 2
    assert c.write_ceiling({"max_external_writes": 2}) == 2
    assert c.write_ceiling({"max_side_effects": 4}) == 4
    assert c.write_ceiling({"max_external_writes": 0, "max_side_effects": 5}) == 5
    assert c.write_ceiling({}) == 0
    assert c.write_ceiling(None) == 0


def test_the_prds_spelling_of_the_recovery_retries_only_the_failed_items(tmp_path):
    store = Store(tmp_path / "ada.sqlite3")
    contract = c.ObjectiveContract(
        goal="file three reports, retry only the failed ones",
        governance={"recovery": {"on_partial_failure": c.RESUME_FAILED_ITEMS_ONLY}})
    run_id = _compile(store, [{"id": "t1", "capability": "files_create",
                               "arguments": {"items": ["a", "b", "c"]}}],
                      contract)["run_id"]
    calls: list[list[str]] = []

    async def port(capability, arguments):
        calls.append(list(arguments["items"]))
        rows = [{"item": i, "ok": not (i == "b" and len(calls) == 1)}
                for i in arguments["items"]]
        return {"status": "succeeded", "output": {"items": rows}}

    _drive(store, run_id, port, through_retries=True)
    assert calls == [["a", "b", "c"], ["b"]], calls
    (task,) = store.objective_tasks(run_id)
    assert task["status"] == O.TaskStatus.SUCCEEDED


def test_the_declared_recovery_is_read_in_either_spelling():
    assert c.partial_failure_recovery({"recovery": c.RESUME_FAILED_ITEMS_ONLY}) \
        == c.RESUME_FAILED_ITEMS_ONLY
    assert c.partial_failure_recovery(
        {"recovery": {"on_partial_failure": c.RESUME_FAILED_ITEMS_ONLY}}) \
        == c.RESUME_FAILED_ITEMS_ONLY
    assert c.partial_failure_recovery({"recovery": {"on_timeout": "x"}}) == ""
    assert c.partial_failure_recovery({}) == ""
    assert c.partial_failure_recovery(None) == ""
    narrowed = c.resume_arguments({"recovery": {"on_partial_failure": c.RESUME_FAILED_ITEMS_ONLY}},
                                  {"items": ["a", "b"]}, {"a": True, "b": False})
    assert narrowed == {"items": ["b"]}


def test_without_the_declared_recovery_a_retry_is_not_narrowed():
    arguments = {"items": ["a", "b"]}
    assert c.resume_arguments({}, arguments, {"a": True, "b": False}) is arguments
    narrowed = c.resume_arguments({"recovery": c.RESUME_FAILED_ITEMS_ONLY},
                                  arguments, {"a": True, "b": False})
    assert narrowed == {"items": ["b"]}


def test_a_state_precondition_holds_an_action_until_the_state_is_established(tmp_path):
    store = Store(tmp_path / "ada.sqlite3")
    contract = c.ObjectiveContract(
        goal="file the receipt once the invoice is approved",
        governance={"requires": [{"field": "approved", "equals": True}]})
    run_id = _compile(store, [{"id": "t1", "capability": "files_create",
                               "arguments": {"path": "receipt.txt", "content": "x"}}],
                      contract)["run_id"]
    made: list[str] = []

    async def port(capability, arguments):
        made.append(arguments["path"])
        return {"status": "succeeded", "output": {}}

    _drive(store, run_id, port)
    assert made == []
    (task,) = store.objective_tasks(run_id)
    assert task["status"] == O.TaskStatus.FAILED and "approved" in str(task["evidence"])
    assert c.unmet_requirements(contract.governance, [{"approved": True}]) == []
    assert c.unmet_requirements(contract.governance, [{"approved": False}])


def test_the_step_that_establishes_a_state_precondition_is_not_held_by_it(tmp_path):
    """A state precondition gates the change, not the read that checks it."""
    store = Store(tmp_path / "ada.sqlite3")
    contract = c.ObjectiveContract(
        goal="check the invoice is approved, then file the receipt",
        governance={"requires": [{"field": "approved", "equals": True}]})
    tasks = [{"id": "t1", "capability": "files_read", "arguments": {"path": "invoice.txt"}},
             {"id": "t2", "capability": "files_create", "dependencies": ["t1"],
              "arguments": {"path": "receipt.txt", "content": "x"}}]
    run_id = _compile(store, tasks, contract)["run_id"]
    made: list[str] = []

    async def port(capability, arguments):
        made.append(capability)
        return {"status": "succeeded", "output": {"approved": True}}

    _drive(store, run_id, port)
    assert made == ["files_read", "files_create"], made
    assert all(t["status"] == O.TaskStatus.SUCCEEDED for t in store.objective_tasks(run_id))


def test_a_state_precondition_can_name_the_actions_it_gates():
    governance = {"requires": [{"field": "approved", "equals": True, "before": "delete"}]}
    assert c.unmet_requirements(governance, [], capability="files_create") == []
    assert c.unmet_requirements(governance, [], capability="files_delete")
    assert c.unmet_requirements(governance, [], capability="files_read") == []
    named = {"requires": [{"field": "approved", "equals": True, "before": "files_create"}]}
    assert c.unmet_requirements(named, [], capability="files_create")
    assert c.unmet_requirements(named, [], capability="files_write") == []
