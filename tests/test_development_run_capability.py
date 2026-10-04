"""
Starting a development run is an action that asks, and it reaches a worker only
through the context compiler (PRD v7 CTX-003, owner ruling 2026-10-03d).

`development_run` is selfdev/FILE_WRITE-class: it asks by default and goes
ahead without another prompt only inside a valid APPROVAL-001 lease for that
capability, repository and blast radius. `development_promote` (landing the
work on a live branch) is COMMAND_EXECUTION and is not covered by a yes to start
the run - only by a lease naming that exact branch. A run that needs commands is
refused citing EXEC-SBX-001, lease or no lease.

Real git repositories and the real objective engine throughout.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import subprocess
from pathlib import Path

import pytest

from friday import capabilities as CAPS
from friday import capability_router as router
from friday import capability_runtime as CR
from friday import contracts as c
from friday import development, fsjail
from friday import objectives as O
from friday import policy as PO
from friday.continuous import ContinuousTaskExecutor, resume_after_approval
from friday.executors import worktrees as W
from friday.store import Store
from friday.toolsets import development_runs as DT
from friday.toolsets import files as F
from friday.toolsets import objectives as OT


def _git(cwd, *args) -> str:
    out = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)
    assert out.returncode == 0, f"git {' '.join(args)}: {out.stderr}"
    return out.stdout.strip()


def _repo(path: Path) -> Path:
    path.mkdir(parents=True)
    (path / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "ada@example.com")
    _git(path, "config", "user.name", "ADA")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "seed")
    return path


@pytest.fixture
def bench(tmp_path, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    repo = _repo(work / "proj")
    monkeypatch.chdir(work)
    monkeypatch.setattr(fsjail, "configured_roots", lambda: [work])
    F.reset_jail(fsjail.FileJail(roots=(work,)))
    store = Store(tmp_path / "ada.sqlite3")
    OT.reset_store(store)

    reached = []
    real = development.for_request
    monkeypatch.setattr(development, "for_request",
                        lambda db, request: reached.append(request) or real(db, request))
    try:
        yield store, work, repo, reached
    finally:
        F.reset_jail(None)
        OT.reset_store(None)


def _objective(store, task: dict) -> str:
    contract = c.ObjectiveContract(
        goal="add a subtract function to calc.py without touching add",
        success_criteria=("calc.py has subtract and add still passes",),
        hard_constraints=("keep add exactly as it is",),
        exact_literals=("calc.py",))
    return O.compile_objective(
        store, request=contract.goal, objective_summary=contract.goal,
        tasks=[{"id": "t1", **task}], manifest=CAPS.as_dicts(),
        provenance=c.PERSON, attended=True, contract=contract)["run_id"]


def _start(repo, **extra) -> dict:
    return {"capability": "development_run",
            "arguments": {"work": "add subtract to calc.py", "root": str(repo), **extra}}


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


def _approved(store, run_id):
    assert _drive(store, run_id)["status"] == O.RUN_WAITING_PERMISSION
    assert resume_after_approval(store, run_id, decided_by="boss")


def _covers(run_id: str, capability: str, arguments: dict) -> str:
    runtime = CR.CapabilityRuntime(engine=PO.PolicyEngine(autonomy=PO.GUARDED))
    return runtime._approval_on_record(capability, arguments, "boss", run_id)


def _task(store, run_id) -> dict:
    (task,) = store.objective_tasks(run_id)
    return task


# --- authority ---------------------------------------------------------------


def test_the_capabilities_carry_the_authority_the_owner_ruled():
    assert PO.TOOL_CATEGORIES["development_run"] == PO.FILE_WRITE
    assert PO.TOOL_CATEGORIES["development_promote"] == PO.COMMAND_EXECUTION
    assert {"development_run", "development_promote"} <= set(router.GROUPS["selfdev"])
    engine = PO.PolicyEngine(autonomy=PO.GUARDED)
    assert engine.decide("development_run").decision == PO.ASK
    assert engine.decide("development_promote").decision == PO.ASK
    assert CAPS.by_id("development_run").requires_approval
    assert CAPS.by_id("development_promote").requires_approval


def test_with_no_lease_a_development_run_asks_and_no_worker_is_chosen(bench):
    store, _, repo, reached = bench
    run_id = _objective(store, _start(repo))
    assert _drive(store, run_id)["status"] == O.RUN_WAITING_PERMISSION
    assert "APPROVAL_REQUIRED" in str(_task(store, run_id).get("evidence"))
    assert reached == []


def test_a_covering_lease_starts_the_run_with_no_further_prompt(bench):
    store, _, repo, reached = bench
    run_id = _objective(store, _start(repo))
    _approved(store, run_id)
    assert _drive(store, run_id)["status"] == O.RUN_COMPLETED
    (request,) = reached
    assert request.objective_id == run_id and request.root == str(repo.resolve())
    assert "critical facts kept by the context compiler" in str(_task(store, run_id).get("evidence"))


def test_a_lease_is_for_the_repository_it_was_given_for(bench, tmp_path):
    store, work, repo, _ = bench
    run_id = _objective(store, _start(repo))
    _approved(store, run_id)
    arguments = _start(repo)["arguments"]
    assert _covers(run_id, "development_run", arguments) == run_id
    other = _repo(work / "other")
    assert _covers(run_id, "development_run", {**arguments, "root": str(other)}) == ""


def test_a_different_blast_radius_asks_again(bench, monkeypatch):
    store, _, repo, reached = bench
    run_id = _objective(store, _start(repo))
    _approved(store, run_id)
    declared = CAPS.CAPABILITIES["development_run"]
    wider = "IRREVERSIBLE" if declared.risk != "IRREVERSIBLE" else "LOW"
    monkeypatch.setitem(CAPS.CAPABILITIES, "development_run",
                        dataclasses.replace(declared, risk=wider))
    assert _drive(store, run_id)["status"] == O.RUN_WAITING_PERMISSION
    assert reached == [], "a yes to a narrower action was acted on"


def test_a_lease_to_start_a_run_does_not_cover_promoting_it(bench):
    store, _, repo, _ = bench
    run_id = _objective(store, _start(repo))
    _approved(store, run_id)
    promote = {"root": str(repo), "run_id": "r1", "branch": "main"}
    assert _covers(run_id, "development_promote", promote) == ""


# --- promotion ---------------------------------------------------------------


@pytest.fixture
def finished(bench):
    """A development run's sandbox worktree with work in it, as the executor
    leaves it: `ada-<run id>` on its own branch, the main checkout untouched."""
    store, work, repo, reached = bench
    manager = W.WorktreeManager(repo)
    name = "ada-r1"
    path = manager.path_for(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    _git(repo, "worktree", "add", "-q", "-b", manager.branch_for(name), str(path), "HEAD")
    (path / "calc.py").write_text(
        "def add(a, b):\n    return a + b\n\n\ndef subtract(a, b):\n    return a - b\n",
        encoding="utf-8")
    return store, repo, _git(repo, "rev-parse", "--abbrev-ref", "HEAD")


def _promote(repo, branch, **extra) -> dict:
    return {"capability": "development_promote",
            "arguments": {"root": str(repo), "run_id": "r1", "branch": branch,
                          "message": "add subtract to calc.py", **extra}}


def test_promotion_without_an_exact_branch_lease_asks_and_lands_nothing(finished):
    store, repo, branch = finished
    before = _git(repo, "rev-parse", branch)
    run_id = _objective(store, _promote(repo, branch))
    assert _drive(store, run_id)["status"] == O.RUN_WAITING_PERMISSION
    assert _git(repo, "rev-parse", branch) == before
    assert "def subtract" not in (repo / "calc.py").read_text(encoding="utf-8")


def test_a_lease_naming_the_exact_branch_lands_the_work(finished):
    store, repo, branch = finished
    before = _git(repo, "rev-parse", branch)
    run_id = _objective(store, _promote(repo, branch))
    _approved(store, run_id)
    assert _drive(store, run_id)["status"] == O.RUN_COMPLETED
    assert "def subtract" in (repo / "calc.py").read_text(encoding="utf-8")
    assert f"rollback target {before[:12]}" in str(_task(store, run_id).get("evidence"))


def test_a_lease_for_one_branch_does_not_cover_another(finished):
    store, repo, branch = finished
    run_id = _objective(store, _promote(repo, branch))
    _approved(store, run_id)
    arguments = _promote(repo, branch)["arguments"]
    assert _covers(run_id, "development_promote", arguments) == run_id
    assert _covers(run_id, "development_promote", {**arguments, "branch": "release"}) == ""


def test_promotion_names_its_branch_and_never_picks_the_live_one(finished):
    store, repo, branch = finished
    run = c.Run.create("promote", capability="development_promote")
    result = DT.development_promote(run, str(repo), "r1", "  ")
    assert result.status == c.FAILED and "exact branch" in result.error
    assert "def subtract" not in (repo / "calc.py").read_text(encoding="utf-8")


def test_a_promotion_onto_a_branch_that_is_not_checked_out_is_refused(finished):
    _, repo, _ = finished
    run = c.Run.create("promote", capability="development_promote")
    result = DT.development_promote(run, str(repo), "r1", "release")
    assert result.status == c.FAILED and "not promoted" in result.error


# --- commands, facts and the envelope ----------------------------------------


def test_a_run_that_needs_commands_is_refused_citing_the_sandbox(bench):
    store, _, repo, reached = bench
    run = c.Run.create("development run", capability="development_run")
    result = DT.development_run(run, "run the tests", str(repo), "any", needs_commands=True)
    assert result.status == c.NOT_PERMITTED and "EXEC-SBX-001" in result.error
    assert reached == []


def test_a_lease_does_not_lift_the_refusal_of_a_run_that_needs_commands(bench):
    store, _, repo, reached = bench
    run_id = _objective(store, _start(repo, needs_commands=True))
    _approved(store, run_id)
    run = _drive(store, run_id)
    assert run["status"] != O.RUN_COMPLETED, run
    assert "EXEC-SBX-001" in json.dumps(_task(store, run_id), default=str)
    assert reached == []


def test_a_run_whose_critical_facts_cannot_be_kept_chooses_no_worker(bench, monkeypatch):
    store, _, repo, _ = bench
    staffed = []
    monkeypatch.setattr(development, "for_goal", lambda *a, **k: staffed.append(a))
    run_id = _objective(store, _start(repo))
    run = c.Run.create("development run", capability="development_run")
    result = DT.development_run(run, "go", str(repo), run_id, budget_tokens=20)
    assert result.status == c.FAILED and "ContextOverBudget" in result.error
    assert staffed == []


def test_a_run_for_an_objective_that_does_not_exist_does_not_start(bench):
    _, _, repo, reached = bench
    run = c.Run.create("development run", capability="development_run")
    result = DT.development_run(run, "go", str(repo), "no-such-run")
    assert result.status == c.FAILED and "no objective no-such-run" in result.error
    assert reached == []


def test_a_repository_outside_the_run_envelope_does_not_start(bench, tmp_path):
    store, _, _, reached = bench
    outside = _repo(tmp_path / "elsewhere")
    run_id = _objective(store, _start(outside))
    _approved(store, run_id)
    assert _drive(store, run_id)["status"] != O.RUN_COMPLETED
    assert "ENVELOPE" in json.dumps(_task(store, run_id), default=str)
    assert reached == []
