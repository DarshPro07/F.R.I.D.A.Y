"""
Crash / restart / resume chaos (PRD v3.1 FR-005, FR-014, KPI "Restart
Resume >= 99%").

Real processes, real SQLite. A child process runs the objective engine
against a shared on-disk store; the parent kills it (SIGKILL-equivalent
on Windows: TerminateProcess) while a task is mid-flight, then a FRESH
process - new pid, new executor identity, no in-memory state - opens the
same database and continues. Assertions are on the durable state only:

  * the objective reaches COMPLETED after the restart;
  * a task that had already SUCCEEDED before the kill is NOT re-executed
    (its side-effect counter stays at 1);
  * the task that was mid-flight is re-dispatched (INTERRUPTED -> run);
  * the ledger names the crash (watchdog.orphaned) and the new lease
    holder; every task carries evidence; the parent run history is one
    continuous record.

The "capability" the tasks invoke appends to a side-effect file, so
"exactly once" is measured on disk, not inferred from the process that
died.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

CHILD = textwrap.dedent(r'''
    import asyncio, json, os, sys, time
    from pathlib import Path
    sys.path.insert(0, sys.argv[1])
    os.environ["ADA_DB"] = sys.argv[2]
    side = Path(sys.argv[3])
    hold = Path(sys.argv[4])            # while this file exists, task "slow" blocks
    executor_id = sys.argv[5]
    from friday.store import Store
    from friday.continuous import ContinuousTaskExecutor, RunWatchdog
    from friday.objectives import compile_objective

    async def capability(name, arguments):
        with open(side, "a", encoding="utf-8") as fh:
            fh.write(f"{executor_id}\t{name}\tstart\n")
        if name == "slow":
            while hold.exists():
                await asyncio.sleep(0.05)
        with open(side, "a", encoding="utf-8") as fh:
            fh.write(f"{executor_id}\t{name}\tdone\n")
        return {"ok": True, "capability": name}

    async def main():
        store = Store(sys.argv[2])
        run_id = sys.argv[6] if len(sys.argv) > 6 and sys.argv[6] else ""
        if not run_id:
            run = compile_objective(
                store, request="chaos probe",
                tasks=[{"capability": "fast", "arguments": {}},
                       {"capability": "slow", "arguments": {}, "dependencies": ["t1"]},
                       {"capability": "after", "arguments": {}, "dependencies": ["t2"]}],
                manifest=[{"id": "fast", "description": "fast"},
                          {"id": "slow", "description": "slow"},
                          {"id": "after", "description": "after"}],
                objective_summary="chaos probe")
            run_id = run["run_id"]
            print(json.dumps({"run_id": run_id}), flush=True)
            ex = ContinuousTaskExecutor(store, capability, executor_id=executor_id)
            ex.lease_timeout = 1.0
            ex.stop()
            await ex.start(run_id)          # the production path: lease, then drive
        else:
            # A fresh control plane: nothing in memory, only the database.
            ex = ContinuousTaskExecutor(store, capability, executor_id=executor_id)
            ex.lease_timeout = 1.0
            ex.stop()
            watchdog = RunWatchdog(ex, lease_timeout=1.0)
            orphaned = await watchdog.sweep_once()
            print(json.dumps({"orphaned": orphaned}), flush=True)
            deadline = time.time() + 20
            while time.time() < deadline:
                row = store.objective_run(run_id)
                if row["status"] in ("COMPLETED", "PARTIAL", "FAILED", "CANCELLED"):
                    break
                await asyncio.sleep(0.05)
        row = store.objective_run(run_id)
        print(json.dumps({"final": row["status"], "lease": row["lease_executor_id"]}), flush=True)

    asyncio.run(main())
''')


def _spawn(db: Path, side: Path, hold: Path, executor_id: str, run_id: str = "") -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-c", CHILD, str(ROOT), str(db), str(side), str(hold), executor_id, run_id],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=str(ROOT),
        env={**os.environ, "PYTHONIOENCODING": "utf-8"})


def _wait_for(pred, timeout: float, what: str):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {what}")


@pytest.mark.slow
def test_control_plane_killed_mid_task_resumes_in_a_fresh_process(tmp_path):
    db = tmp_path / "chaos.sqlite3"
    side = tmp_path / "side_effects.tsv"
    hold = tmp_path / "hold"
    hold.write_text("block the slow task")
    side.write_text("")

    # 1. First control plane: compiles the objective, runs "fast", blocks in "slow".
    first = _spawn(db, side, hold, "cp-1")
    line = first.stdout.readline()
    run_id = json.loads(line)["run_id"]
    _wait_for(lambda: "cp-1\tslow\tstart" in side.read_text(), 15, "slow task to start")
    assert "cp-1\tfast\tdone" in side.read_text()

    # 2. Kill it. No shutdown hook, no cleanup: the process simply vanishes.
    first.kill()
    first.wait(timeout=10)
    assert first.returncode != 0

    from friday.store import Store
    store = Store(str(db))
    row = store.objective_run(run_id)
    assert row["status"] == "RUNNING" and row["lease_executor_id"] == "cp-1"
    statuses = {t["capability"]: t["status"] for t in store.objective_tasks(run_id)}
    assert statuses["fast"] == "SUCCEEDED"
    assert statuses["slow"] in ("RUNNING", "INTERRUPTED")
    assert statuses["after"] in ("QUEUED", "READY")
    store.close() if hasattr(store, "close") else None

    # 3. Fresh control plane, new identity, only the database in common.
    hold.unlink()
    time.sleep(1.2)                       # let the dead lease expire (lease_timeout=1.0)
    second = _spawn(db, side, hold, "cp-2", run_id)
    out, err = second.communicate(timeout=60)
    assert second.returncode == 0, err[-2000:]
    lines = [json.loads(l) for l in out.splitlines() if l.startswith("{")]
    assert lines[0]["orphaned"] == [run_id], (lines, err[-800:])
    assert lines[-1]["final"] == "COMPLETED", (lines, err[-800:])
    # The lease is released at completion; the ledger says who drove it.

    # 4. Durable truth after the restart.
    store = Store(str(db))
    tasks = {t["capability"]: t for t in store.objective_tasks(run_id)}
    assert {t["status"] for t in tasks.values()} == {"SUCCEEDED"}
    for t in tasks.values():
        assert t["evidence"], t["capability"]          # FR-052 on every task
    all_events = store.objective_events(run_id)
    events = [e["event"] for e in all_events]
    assert "watchdog.orphaned" in events and "run.completed" in events
    assert events.count("run.completed") == 1
    leases = [json.loads(e["detail"]) if isinstance(e.get("detail"), str) else e.get("detail")
              for e in all_events if e["event"] == "lease.acquired"]
    assert [l["executor_id"] for l in leases] == ["cp-1", "cp-2"]

    effects = side.read_text().splitlines()
    assert effects.count("cp-1\tfast\tdone") == 1
    assert "cp-2\tfast\tstart" not in effects           # finished work is never replayed
    assert "cp-2\tslow\tdone" in effects                # interrupted work is retried
    assert "cp-2\tafter\tdone" in effects
    assert effects.count("cp-1\tslow\tdone") == 0       # it died before finishing


@pytest.mark.slow
def test_restart_resume_rate_over_repeated_kills(tmp_path):
    """KPI: Restart Resume >= 99%. Ten kill/resume cycles on ten objectives;
    every one must complete after its restart. Deterministic, so the rate
    is 100% or the test names the cycle that was not."""
    from friday.store import Store
    failures = []
    for i in range(10):
        db = tmp_path / f"c{i}.sqlite3"
        side = tmp_path / f"s{i}.tsv"
        hold = tmp_path / f"h{i}"
        hold.write_text("x")
        side.write_text("")
        first = _spawn(db, side, hold, f"a{i}")
        run_id = json.loads(first.stdout.readline())["run_id"]
        try:
            _wait_for(lambda: f"a{i}\tslow\tstart" in side.read_text(), 15, "slow start")
        finally:
            first.kill()
            first.wait(timeout=10)
        hold.unlink()
        time.sleep(1.2)
        second = _spawn(db, side, hold, f"b{i}", run_id)
        out, err = second.communicate(timeout=60)
        final = [json.loads(l) for l in out.splitlines() if l.startswith("{")]
        status = final[-1]["final"] if final else f"no output: {err[-300:]}"
        if status != "COMPLETED":
            failures.append((i, status))
        else:
            tasks = {t["capability"]: t["status"] for t in Store(str(db)).objective_tasks(run_id)}
            if set(tasks.values()) != {"SUCCEEDED"}:
                failures.append((i, tasks))
    assert not failures, failures
