"""
The side-effect ledger (PRD v7 SIDE-001): every meaningful external change is
a durable row with its reversibility, and the completion verifier reads the
rows back - a claimed side effect with no row is not verified.
"""
from __future__ import annotations

import pytest

from friday import contracts as c
from friday import objectives as O
from friday import side_effects
from friday import workspace as W
from friday.fsjail import FileJail
from friday.policy import FULL, PolicyEngine
from friday.store import CompletionRefused, Store
from friday.toolsets import files as F
from friday.toolsets import memory


@pytest.fixture
def root(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    artifacts = workspace / "artifacts"
    artifacts.mkdir()
    F.reset_jail(FileJail(roots=(workspace,)))
    monkeypatch.setattr(F, "ARTIFACTS_DIR", artifacts)
    yield workspace
    F.reset_jail(None)


def _run(objective: str = "") -> c.Run:
    correlation = c.Correlation(objective=objective) if objective else None
    return c.Run.create("write a note", capability="files", correlation=correlation)


def _engine() -> PolicyEngine:
    return PolicyEngine(autonomy=FULL)


# -- the table and its writer -------------------------------------------------


def test_the_ledger_table_is_declared_with_a_reason():
    assert "side_effect_ledger" in W.SCOPED or "side_effect_ledger" in W.UNSCOPED


def test_a_row_round_trips_and_reversibility_is_one_of_three():
    store = Store(":memory:")
    store.record_side_effect(run_id="r1", objective_id="o1", capability="files.write",
                             target="/x/a.txt", effect="created /x/a.txt",
                             reversibility=side_effects.COMPENSABLE)
    [row] = store.side_effects_for(run_id="r1")
    assert (row["capability"], row["target"], row["effect"], row["reversibility"],
            row["run_id"], row["objective_id"]) == (
        "files.write", "/x/a.txt", "created /x/a.txt", "compensable", "r1", "o1")
    assert store.side_effects_for(objective_id="o1") == [row]
    assert store.side_effects_for(run_id="other") == []
    with pytest.raises(ValueError, match="reversibility"):
        store.record_side_effect(run_id="r1", objective_id="", capability="files.write",
                                 target="t", effect="e", reversibility="maybe")


# -- a real capability writes the row -----------------------------------------


def test_files_write_records_a_compensable_row_for_a_new_file(root):
    target = root / "note.txt"
    result = F.files_write(_run("obj-7"), str(target), "hello", engine=_engine())
    assert result.status == c.SUCCEEDED
    [row] = memory.store().side_effects_for(run_id=result.run_id)
    assert row["capability"] == "files.write"
    assert row["target"] == str(target)
    assert row["effect"] == f"created {target}"
    assert row["reversibility"] == side_effects.COMPENSABLE
    assert row["objective_id"] == "obj-7"


def test_overwriting_a_file_is_irreversible(root):
    target = root / "note.txt"
    target.write_text("old", encoding="utf-8")
    result = F.files_write(_run(), str(target), "new", engine=_engine())
    [row] = memory.store().side_effects_for(run_id=result.run_id)
    assert row["reversibility"] == side_effects.IRREVERSIBLE


def test_a_permanent_delete_is_irreversible_and_a_recycle_is_reversible(root, monkeypatch):
    owned = root / "artifacts" / "scratch.txt"
    owned.write_text("x", encoding="utf-8")
    result = F.files_delete(_run(), str(owned), permanent=True, engine=_engine())
    assert result.status == c.SUCCEEDED
    [row] = memory.store().side_effects_for(run_id=result.run_id)
    assert (row["capability"], row["reversibility"]) == (
        "files.delete_permanent", side_effects.IRREVERSIBLE)

    kept = root / "kept.txt"
    kept.write_text("x", encoding="utf-8")
    monkeypatch.setattr("send2trash.send2trash", lambda path: kept.unlink())
    result = F.files_delete(_run(), str(kept), engine=_engine())
    [row] = memory.store().side_effects_for(run_id=result.run_id)
    assert (row["capability"], row["reversibility"]) == (
        "files.delete", side_effects.REVERSIBLE)


def test_edit_copy_move_and_recycle_each_write_their_row(root, monkeypatch):
    note = root / "note.txt"
    note.write_text("old text", encoding="utf-8")
    copy, moved, binned = root / "copy.txt", root / "moved.txt", root / "binned.txt"
    binned.write_text("x", encoding="utf-8")
    monkeypatch.setattr("send2trash.send2trash", lambda path: binned.unlink())
    calls = [
        (lambda run: F.files_edit(run, str(note), "old", "new", engine=_engine()),
         "files.edit", note, side_effects.IRREVERSIBLE),
        (lambda run: F.files_copy(run, str(note), str(copy), engine=_engine()),
         "files.copy", copy, side_effects.COMPENSABLE),
        (lambda run: F.files_move(run, str(copy), str(moved), engine=_engine()),
         "files.move", moved, side_effects.COMPENSABLE),
        (lambda run: F.files_recycle(run, str(binned), engine=_engine()),
         "files.recycle", binned, side_effects.REVERSIBLE),
    ]
    for call, capability, target, reversibility in calls:
        result = call(_run("obj-9"))
        assert result.status == c.SUCCEEDED, (capability, result.error)
        assert capability in side_effects.LEDGERED
        [row] = memory.store().side_effects_for(run_id=result.run_id)
        assert (row["capability"], row["target"], row["reversibility"],
                row["objective_id"]) == (capability, str(target), reversibility, "obj-9")


def test_a_failed_write_leaves_no_row(root):
    result = F.files_write(_run(), str(root.parent / "outside.txt"), "x", engine=_engine())
    assert result.status != c.SUCCEEDED
    assert memory.store().side_effects_for(run_id=result.run_id) == []


# -- the verifier reads the rows back -----------------------------------------


def _objective_with_claim(store: Store, action_run_id: str) -> tuple[str, str]:
    created = O.compile_objective(
        store, request="write the note", objective_summary="write the note",
        tasks=[{"capability": "files_write",
                "arguments": {"path": "note.txt", "content": "x"}}],
        manifest=[{"id": "files_write", "description": "write"}],
        provenance=c.PERSON, attended=True)
    run_id = created["run_id"]
    task_id = store.objective_tasks(run_id)[0]["task_id"]
    store.update_objective_task(
        task_id, status=O.TaskStatus.SUCCEEDED,
        result={"run_id": action_run_id, "tool_id": "files.write",
                "status": "succeeded", "side_effects": ["created note.txt"]})
    store.append_objective_evidence(run_id, task_id=task_id, expected="files_write",
                                    actual="ok", method="write_readback", passed=True)
    return run_id, task_id


def test_a_claimed_side_effect_with_no_row_is_not_verified():
    store = Store(":memory:")
    run_id, task_id = _objective_with_claim(store, "act-1")

    assert store.completion_evidence_gap(run_id) == [task_id]
    evidence = store.objective_ledger(run_id)["evidence"]
    assert [e["passed"] for e in evidence] == [False]
    with pytest.raises(CompletionRefused, match="no passing evidence"):
        store.finish_objective_run(run_id, status=O.RUN_COMPLETED, summary={})

    store.record_side_effect(run_id="act-1", objective_id=run_id,
                             capability="files.write", target="note.txt",
                             effect="created note.txt",
                             reversibility=side_effects.COMPENSABLE)
    assert store.completion_evidence_gap(run_id) == []
    assert [e["passed"] for e in store.objective_ledger(run_id)["evidence"]] == [True]
    store.finish_objective_run(run_id, status=O.RUN_COMPLETED, summary={})


def test_a_capability_without_a_ledger_is_not_held_to_it():
    """Only capabilities wired to the ledger are checked, so this lands
    without refusing every other tool that reports a side effect."""
    store = Store(":memory:")
    run_id, task_id = _objective_with_claim(store, "act-2")
    store.update_objective_task(
        task_id, status=O.TaskStatus.SUCCEEDED,
        result={"run_id": "act-2", "tool_id": "audio.set_volume",
                "side_effects": ["volume 10% -> 20%"]})
    assert store.completion_evidence_gap(run_id) == []


# -- end to end: the executor's own store holds the row the verifier reads ----


def _drive_files_create(store, run_id):
    import asyncio

    from friday import capability_runtime as CR
    from friday.continuous import ContinuousTaskExecutor

    dispatch = CR.build_dispatch(engine=_engine())

    async def go():
        executor = ContinuousTaskExecutor(store, call_capability=dispatch)
        try:
            loop = asyncio.get_running_loop()
            deadline = loop.time() + 20
            while loop.time() < deadline:
                status = store.objective_run(run_id)["status"]
                if status in O.RUN_TERMINAL or status in O.RUN_WAITING_STATUSES:
                    return
                await executor.start(run_id)
                await asyncio.sleep(0.02)
        finally:
            executor.stop()

    asyncio.run(go())


def test_a_capability_run_in_an_objective_writes_the_row_the_verifier_reads(
        root, tmp_path, monkeypatch):
    from friday import fsjail

    monkeypatch.setattr(fsjail, "configured_roots", lambda: [root])
    store = Store(tmp_path / "objective.sqlite3")
    assert store is not memory.store(), "the point is a store the capability would not find"
    created = O.compile_objective(
        store, request="create note.txt", objective_summary="note",
        tasks=[{"capability": "files_create",
                "arguments": {"path": str(root / "note.txt"), "content": "hi"}}],
        manifest=[{"id": "files_create", "description": "create"}],
        provenance=c.PERSON, attended=True)
    run_id = created["run_id"]
    _drive_files_create(store, run_id)

    assert store.objective_run(run_id)["status"] == O.RUN_COMPLETED
    [row] = store.side_effects_for(objective_id=run_id)
    assert row["capability"] == "files.create"
    [task] = store.objective_tasks(run_id)
    assert row["run_id"] == task["result"]["run_id"]
    assert store.completion_evidence_gap(run_id) == []
    assert memory.store().side_effects_for(run_id=row["run_id"]) == [], \
        "the row went to a store the verifier does not read"
