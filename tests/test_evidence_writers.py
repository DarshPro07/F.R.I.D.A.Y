"""AT-08 — the two executors that still reported outside the ledger now
write into it, with the same rule as everyone else: `verified` comes from
a read-back, never from the executor saying it finished.

    OBJECTIVE_WORKER  every objective step -> one row; verified only when
                      the capability reported a verification with evidence
    CLAUDE_CODE       every git-listed change -> one row read back from disk;
                      the run -> one row; Claude's summary is never evidence

Negative controls: a step that passed WITHOUT a verification is recorded
unverified (a SUCCESS claim about it is refused); a file git lists that the
disk does not have is FAILED, not verified; the ledger being unavailable
never fails the executor.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from friday import action_evidence as AE
from friday import contracts as c
from friday import objectives as O
from friday.continuous import ContinuousTaskExecutor
from friday.store import Store


# --------------------------------------------------------------------------
# OBJECTIVE_WORKER
# --------------------------------------------------------------------------

class Registry:
    def __init__(self, verified: bool) -> None:
        self.verified = verified

    async def call(self, capability: str, arguments: dict) -> dict:
        if capability == "boom":
            raise ValueError("no such thing")
        out = {"ok": True, "capability": capability, "status": "succeeded"}
        if self.verified:
            out["verification"] = {"method": "disk_readback", "evidence": "read back 12 bytes"}
        return out


MANIFEST = [{"id": x, "description": x} for x in ("files_create", "boom")]


def _compile(store, tasks):
    return O.compile_objective(store, request="create the note", tasks=tasks,
                               manifest=MANIFEST, objective_summary="create the note")["run_id"]


async def _drive(store, executor, run_id):
    executor.stop()
    await executor._drive_until_done(run_id)   # noqa: SLF001
    return store.objective_run(run_id)


@pytest.mark.asyncio
async def test_a_verified_objective_step_is_a_verified_worker_row(tmp_path):
    store = Store(":memory:")
    run_id = _compile(store, [{"capability": "files_create", "arguments": {"path": str(tmp_path / "note.txt")}}])
    executor = ContinuousTaskExecutor(store, Registry(verified=True).call, executor_id="exec-1")
    row = await _drive(store, executor, run_id)
    assert row["status"] == O.RunStatus.COMPLETED
    rows = [r for r in AE.ledger().for_objective(run_id) if r.executor == AE.OBJECTIVE_WORKER]
    assert len(rows) == 1
    r = rows[0]
    assert r.capability == "files_create" and r.status == AE.SUCCEEDED
    assert r.verified and r.evidence_type == "disk_readback" and "read back" in r.evidence_ref
    assert r.target.endswith("note.txt")
    # the row backs a claim about that file
    verdict = AE.audit_claims("I created note.txt for you.", rows)
    assert verdict.ok, verdict


@pytest.mark.asyncio
async def test_an_unverified_step_is_recorded_unverified_and_backs_no_success_claim(tmp_path):
    """Negative control: passing is not verification."""
    store = Store(":memory:")
    run_id = _compile(store, [{"capability": "files_create", "arguments": {"path": str(tmp_path / "note.txt")}}])
    executor = ContinuousTaskExecutor(store, Registry(verified=False).call, executor_id="exec-1")
    await _drive(store, executor, run_id)
    rows = [r for r in AE.ledger().for_objective(run_id) if r.executor == AE.OBJECTIVE_WORKER]
    assert len(rows) == 1 and rows[0].status == AE.SUCCEEDED and not rows[0].verified
    verdict = AE.audit_claims("I created note.txt for you.", rows)
    assert not verdict.ok


@pytest.mark.asyncio
async def test_a_failed_step_is_a_failed_worker_row():
    store = Store(":memory:")
    run_id = _compile(store, [{"capability": "boom", "arguments": {}, "max_attempts": 1}])
    executor = ContinuousTaskExecutor(store, Registry(verified=True).call, executor_id="exec-1")
    row = await _drive(store, executor, run_id)
    assert row["status"] == O.RunStatus.FAILED
    rows = [r for r in AE.ledger().for_objective(run_id) if r.executor == AE.OBJECTIVE_WORKER]
    assert rows and rows[-1].status == AE.FAILED and "no such thing" in rows[-1].result
    # a FAILURE claim about it is now backed
    verdict = AE.audit_claims("I tried to run boom but it failed.", rows)
    assert verdict.ok, verdict


@pytest.mark.asyncio
async def test_ledger_outage_never_fails_the_objective(monkeypatch, tmp_path):
    store = Store(":memory:")
    run_id = _compile(store, [{"capability": "files_create", "arguments": {"path": str(tmp_path / "n.txt")}}])

    def broken():
        raise RuntimeError("ledger down")
    monkeypatch.setattr(AE, "ledger", broken)
    executor = ContinuousTaskExecutor(store, Registry(verified=True).call, executor_id="exec-1")
    row = await _drive(store, executor, run_id)
    assert row["status"] == O.RunStatus.COMPLETED


# --------------------------------------------------------------------------
# CLAUDE_CODE
# --------------------------------------------------------------------------

def _executor(tmp_path):
    from friday.executors.claude_code import ClaudeCodeExecutor
    return ClaudeCodeExecutor(Store(":memory:"))


def _bundle(tmp_path):
    from friday.executors.claude_code import TaskBundle
    return TaskBundle(goal="add a hello script", workspace=str(tmp_path), isolate=False, run_id="DEV-t1")


def test_claude_changed_files_become_verified_rows_read_back_from_disk(tmp_path, monkeypatch):
    ex = _executor(tmp_path)
    (tmp_path / "hello.py").write_text("print('hi')\n", encoding="utf-8")
    monkeypatch.setattr(ex, "changed_files", staticmethod(lambda where: ["hello.py", "ghost.py"]))
    run = c.Run.create("dev", capability="executor.claude_code")
    started = c.started(run.run_id, "executor.claude_code")
    out = ex.finish(_bundle(tmp_path), started, {"result": "I added hello.py and ghost.py", "num_turns": 3})
    assert out.status == c.SUCCEEDED
    rows = {r.action_id: r for r in AE.ledger().for_objective("DEV-t1")}
    real = rows["claude:DEV-t1:hello.py"]
    ghost = rows["claude:DEV-t1:ghost.py"]
    assert real.executor == AE.CLAUDE_CODE and real.verified and real.evidence_type == "disk_readback"
    assert not ghost.verified and ghost.status == AE.FAILED          # git said so; the disk did not
    assert rows["claude:DEV-t1:run"].status == AE.SUCCEEDED
    # the claim about the real file is backed; the ghost is not
    assert AE.audit_claims("I created hello.py.", list(rows.values())).ok
    assert not AE.audit_claims("I created ghost.py.", list(rows.values())).ok


def test_claude_saying_done_with_no_change_is_partial_and_unverified(tmp_path, monkeypatch):
    ex = _executor(tmp_path)
    monkeypatch.setattr(ex, "changed_files", staticmethod(lambda where: []))
    run = c.Run.create("dev", capability="executor.claude_code")
    started = c.started(run.run_id, "executor.claude_code")
    out = ex.finish(_bundle(tmp_path), started, {"result": "All done, I wrote everything.", "num_turns": 2})
    assert out.status == c.PARTIAL
    rows = AE.ledger().for_objective("DEV-t1")
    assert [r.capability for r in rows] == ["executor.claude_code"]
    assert rows[0].status == AE.PARTIAL and not rows[0].verified
