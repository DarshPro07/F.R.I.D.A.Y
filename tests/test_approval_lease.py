"""
An approval is a lease on the plan it was given for (PRD v7 APPROVAL-001).

The person said yes to one exact action inside one plan. If the plan changes
materially afterwards - a task added, an argument amended - or the approval
expires, the yes no longer applies: the run asks again rather than acting on
a yes to something that no longer exists. A record from before leases (no
plan it was given for) is not honoured either.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from friday import capability_runtime as CR
from friday import contracts as c
from friday import fsjail
from friday import modification as M
from friday import objectives as O
from friday import policy as PO
from friday.continuous import ContinuousTaskExecutor, resume_after_approval
from friday.store import Store
from friday.toolsets import files as F
from friday.toolsets import objectives as OT


@pytest.fixture
def bench(tmp_path, monkeypatch):
    root = tmp_path / "work"
    root.mkdir()
    monkeypatch.chdir(root)
    monkeypatch.setattr(fsjail, "configured_roots", lambda: [root])
    F.reset_jail(fsjail.FileJail(roots=(root,)))
    store = Store(tmp_path / "ada.sqlite3")
    # The runtime reads approvals from the objective store it was given.
    OT.reset_store(store)
    created = O.compile_objective(
        store, request="write the note and check the system", objective_summary="note",
        tasks=[{"capability": "files_create", "arguments": {"path": "note.txt", "content": "hi"}},
               {"capability": "system_get_info", "arguments": {}}],
        manifest=[{"id": "files_create", "description": "create"},
                  {"id": "system_get_info", "description": "info"}],
        provenance=c.PERSON, attended=True)
    try:
        yield store, created["run_id"], root
    finally:
        F.reset_jail(None)
        OT.reset_store(None)


def _drive(store, run_id, seconds=15.0):
    dispatch = CR.build_dispatch(engine=PO.PolicyEngine(autonomy=PO.GUARDED))

    async def go():
        executor = ContinuousTaskExecutor(store, call_capability=dispatch)
        try:
            loop = asyncio.get_running_loop()
            deadline = loop.time() + seconds
            while loop.time() < deadline:
                status = store.objective_run(run_id)["status"]
                if status in O.RUN_TERMINAL or status in O.RUN_WAITING_STATUSES:
                    return
                await executor.start(run_id)
                await asyncio.sleep(0.02)
        finally:
            executor.stop()

    asyncio.run(go())
    return store.objective_run(run_id)


def _parked_then_approved(store, run_id):
    assert _drive(store, run_id)["status"] == O.RUN_WAITING_PERMISSION
    assert resume_after_approval(store, run_id, decided_by="boss")


def test_an_approval_for_the_same_plan_is_honoured(bench):
    store, run_id, root = bench
    _parked_then_approved(store, run_id)
    assert _drive(store, run_id)["status"] == O.RUN_COMPLETED
    assert (root / "note.txt").read_text(encoding="utf-8") == "hi"


def test_the_approval_records_the_plan_it_was_given_for(bench):
    store, run_id, _ = bench
    _parked_then_approved(store, run_id)
    (record,) = [a for a in json.loads(store.objective_run(run_id)["approvals"])
                 if a["decision"] == "APPROVED"]
    assert record["plan_hash"] == O.plan_hash(store, run_id)
    assert record["graph_version"] == M.graph_version(store, run_id)
    assert record["expires_at"]


def test_a_material_change_to_the_plan_makes_the_approval_stale(bench):
    store, run_id, root = bench
    _parked_then_approved(store, run_id)
    M.append_task(store, run_id, "system_get_info", {}, said="one more check")
    run = _drive(store, run_id)
    assert run["status"] == O.RUN_WAITING_PERMISSION, run
    assert not (root / "note.txt").exists(), "a stale yes was acted on"


def test_an_expired_approval_does_not_authorise(bench):
    store, run_id, root = bench
    _parked_then_approved(store, run_id)
    approvals = json.loads(store.objective_run(run_id)["approvals"])
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    for record in approvals:
        record["expires_at"] = past
    store._conn.execute("UPDATE objective_runs SET approvals=? WHERE run_id=?",
                        (json.dumps(approvals), run_id))
    assert _drive(store, run_id)["status"] == O.RUN_WAITING_PERMISSION
    assert not (root / "note.txt").exists()


def test_an_approval_from_before_leases_is_not_honoured(bench):
    store, run_id, root = bench
    _parked_then_approved(store, run_id)
    approvals = json.loads(store.objective_run(run_id)["approvals"])
    for record in approvals:
        record.pop("plan_hash", None)
    store._conn.execute("UPDATE objective_runs SET approvals=? WHERE run_id=?",
                        (json.dumps(approvals), run_id))
    assert _drive(store, run_id)["status"] == O.RUN_WAITING_PERMISSION
    assert not (root / "note.txt").exists()


def test_an_approval_on_one_objective_does_not_authorise_another(bench):
    """A yes is on the run it was given in. Another objective that writes the
    same marker into its own task arguments is asked, not waved through."""
    store, run_a, _ = bench
    _parked_then_approved(store, run_a)
    other = O.compile_objective(
        store, request="write the note", objective_summary="another objective",
        tasks=[{"capability": "files_create",
                "arguments": {"path": "note.txt", "content": "hi", "approved_by": "boss"}}],
        manifest=[{"id": "files_create", "description": "create"}],
        provenance=c.PERSON, attended=True)
    run = _drive(store, other["run_id"])
    assert run["status"] == O.RUN_WAITING_PERMISSION, run
    # Run A may finish on its own yes meanwhile; what matters is B's write.
    (task,) = store.objective_tasks(other["run_id"])
    assert task["status"] == O.TaskStatus.WAITING, "another objective's yes was acted on"
    assert "APPROVAL_REQUIRED" in str(task.get("evidence"))


def test_a_call_outside_any_objective_cannot_use_an_objective_approval(bench):
    store, run_id, root = bench
    _parked_then_approved(store, run_id)
    runtime = CR.CapabilityRuntime(engine=PO.PolicyEngine(autonomy=PO.GUARDED))
    result = runtime.execute("files_create", {"path": "note.txt", "content": "hi",
                                              "approved_by": "boss"})
    assert result.status == c.CANCELLED and "APPROVAL_REQUIRED" in result.error, result
    assert not (root / "note.txt").exists()


def test_blast_radius_combines_the_declared_effect_with_the_target_scope():
    one = O.blast_radius("files_create", {"path": "a.txt"})
    many = O.blast_radius("files_create", {"paths": ["a.txt", "b.txt"]})
    others = O.blast_radius("files_create", {"path": "a.txt", "to": "x@example.com"})
    assert len({one, many, others}) == 3
    assert O.blast_radius("no_such_capability") == "unknown:unknown"


def test_the_approval_records_the_blast_radius_it_was_given_for(bench):
    store, run_id, _ = bench
    _parked_then_approved(store, run_id)
    (record,) = [a for a in json.loads(store.objective_run(run_id)["approvals"])
                 if a["decision"] == "APPROVED"]
    assert record["blast_radius"] == O.blast_radius("files_create", record["parameters"])


def test_a_change_of_blast_radius_makes_the_approval_stale(bench, monkeypatch):
    import dataclasses

    from friday import capabilities as CAPS

    store, run_id, root = bench
    _parked_then_approved(store, run_id)
    declared = CAPS.CAPABILITIES["files_create"]
    wider = "IRREVERSIBLE" if declared.risk != "IRREVERSIBLE" else "LOW"
    monkeypatch.setitem(CAPS.CAPABILITIES, "files_create",
                        dataclasses.replace(declared, risk=wider))
    run = _drive(store, run_id)
    assert run["status"] == O.RUN_WAITING_PERMISSION, run
    assert not (root / "note.txt").exists(), "a yes to a narrower action was acted on"


@pytest.mark.parametrize("flag", ["consequential", "reversible", "idempotent"])
def test_a_change_of_a_consequence_flag_makes_the_approval_stale(bench, monkeypatch, flag):
    """The consequence flags are part of the radius: a yes given while the
    capability was reversible does not cover it once it is not, even when
    `side_effect` and `risk` are unchanged. Asked of the lease itself: a run
    with an envelope meets the JIT fingerprint (which also hashes the flags)
    first, so a driven run would be refused before the lease is consulted."""
    import dataclasses

    from friday import capabilities as CAPS

    store, run_id, root = bench
    _parked_then_approved(store, run_id)
    (record,) = [a for a in json.loads(store.objective_run(run_id)["approvals"])
                 if a["decision"] == "APPROVED"]
    runtime = CR.CapabilityRuntime(engine=PO.PolicyEngine(autonomy=PO.GUARDED))

    def lease():
        return runtime._approval_on_record("files_create", record["parameters"],
                                           record["decided_by"], run_id)

    assert lease() == run_id, "the unchanged capability should honour its own yes"
    declared = CAPS.CAPABILITIES["files_create"]
    monkeypatch.setitem(
        CAPS.CAPABILITIES, "files_create",
        dataclasses.replace(declared, **{flag: not getattr(declared, flag)}))
    assert lease() == "", f"a yes given before {flag} changed still authorises"
    assert not (root / "note.txt").exists()


def test_a_capability_with_an_undeclared_flag_has_an_unknown_blast_radius(monkeypatch):
    import dataclasses

    from friday import capabilities as CAPS

    declared = CAPS.CAPABILITIES["files_create"]
    monkeypatch.setitem(CAPS.CAPABILITIES, "files_create",
                        dataclasses.replace(declared, idempotent=None))
    assert O.blast_radius("files_create", {"path": "a.txt"}) == "unknown:unknown"


def test_an_approval_with_no_blast_radius_is_not_honoured(bench):
    store, run_id, root = bench
    _parked_then_approved(store, run_id)
    approvals = json.loads(store.objective_run(run_id)["approvals"])
    for record in approvals:
        record.pop("blast_radius", None)
    store._conn.execute("UPDATE objective_runs SET approvals=? WHERE run_id=?",
                        (json.dumps(approvals), run_id))
    assert _drive(store, run_id)["status"] == O.RUN_WAITING_PERMISSION
    assert not (root / "note.txt").exists()
