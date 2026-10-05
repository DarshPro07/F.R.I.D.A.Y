"""
Chaos suite (PRD V7 CHAOS-001).

Nine faults, one truthful outcome each. For every fault the durable end
state is either a recovery that happens exactly once, or a failure that
names its reason. Never a success that did not happen.

    browser crash            the browser dies under a live task
    provider outage          the model provider cannot answer
    Hermes crash             the execution engine is down
    invalid model response   the model returned nothing usable
    DB contention            two writers, or a writer killed mid-write
    network loss             the link drops mid-task and comes back
    malformed tool output    a tool returned something that is not a result
    worker death             the process holding the leases dies
    reboot                   everything is gone but the database

`FAULT_MAP` is the table. Three faults were already injected by earlier
suites and are named here rather than re-written; six are injected in this
file: five on the real Store and the real ContinuousTaskExecutor, and an
invalid model answer at the planner, where it meets the durable engine.
(invalid_model_response and worker_death also keep the earlier tests.) A fault with no
mapping, or a mapping that points at a test that does not exist, fails the
suite. `scripts/chaos_report.py` runs exactly this table and writes the
per-fault report that is CHAOS-001's acceptance evidence.
"""
from __future__ import annotations

import ast
import asyncio
import json
import os
import socket
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from friday import contracts as c
from friday import objectives as O
from friday.continuous import ContinuousTaskExecutor, RunWatchdog
from friday.contracts import PERSON
from friday.objectives import FailureKind, TaskStatus, compile_objective
from friday.store import Store

ROOT = Path(__file__).resolve().parents[1]

PRD_FAULTS = ("browser_crash", "provider_outage", "hermes_crash",
              "invalid_model_response", "db_contention", "network_loss",
              "malformed_tool_output", "worker_death", "reboot")

# Literal on purpose: scripts/chaos_report.py reads it with ast, not import.
FAULT_MAP = {
    "browser_crash": (
        "tests/test_chaos_suite.py::test_browser_crash_mid_task_fails_truthfully",
    ),
    "provider_outage": (
        "tests/test_provider_outage.py::test_an_outage_does_not_end_the_run",
        "tests/test_provider_outage.py::test_the_task_succeeds_once_the_provider_comes_back",
    ),
    "hermes_crash": (
        "tests/test_hermes_engine.py::test_hermes_executor_reports_unavailable_not_a_crash",
    ),
    "invalid_model_response": (
        "tests/test_chaos_suite.py::test_an_invalid_model_answer_leaves_the_deterministic_plan_not_a_crash",
        "tests/test_resilience.py::test_the_empty_completion_is_recognised",
        "tests/test_resilience.py::test_the_exhausted_form_is_recognised_too",
    ),
    "db_contention": (
        "tests/test_store_durability.py::test_kill_during_write_leaves_a_consistent_database",
        "tests/test_store_durability.py::test_two_processes_write_concurrently_without_lock_errors",
    ),
    "network_loss": (
        "tests/test_chaos_suite.py::test_network_loss_marks_the_task_degraded_and_recovers",
        "tests/test_chaos_suite.py::test_network_loss_that_never_ends_fails_truthfully",
    ),
    "malformed_tool_output": (
        "tests/test_chaos_suite.py::test_malformed_tool_output_is_a_failure_not_success",
    ),
    "worker_death": (
        "tests/test_chaos_restart.py::test_control_plane_killed_mid_task_resumes_in_a_fresh_process",
        "tests/test_chaos_suite.py::test_worker_death_with_two_runs_in_flight_resumes_both_exactly_once",
    ),
    "reboot": (
        "tests/test_chaos_suite.py::test_cold_boot_sweeps_stale_leases_and_names_the_orphan",
    ),
}


def _defined_tests(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {node.name for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}


def test_every_prd_fault_is_mapped_to_a_test_that_exists():
    assert set(FAULT_MAP) == set(PRD_FAULTS), (
        f"unmapped: {sorted(set(PRD_FAULTS) - set(FAULT_MAP))}, "
        f"unknown: {sorted(set(FAULT_MAP) - set(PRD_FAULTS))}")
    missing = []
    for fault, node_ids in FAULT_MAP.items():
        assert node_ids, f"{fault} names no test"
        for node_id in node_ids:
            rel, _, name = node_id.partition("::")
            path = ROOT / rel
            if not path.is_file() or name.split("[")[0] not in _defined_tests(path):
                missing.append((fault, node_id))
    assert not missing, f"mapped to tests that do not exist: {missing}"


def _load_report_script():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "chaos_report", ROOT / "scripts" / "chaos_report.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_report_is_green_only_when_every_mapped_test_passed():
    """The report is the acceptance evidence, so it must not read a skipped,
    missing or failing test as a pass."""
    report = _load_report_script()
    prd_faults, fault_map = report.load_fault_map()
    assert prd_faults == PRD_FAULTS and fault_map == FAULT_MAP

    outcomes = {}
    for node_ids in FAULT_MAP.values():
        for node_id in node_ids:
            rel, _, name = node_id.partition("::")
            outcomes[(Path(rel).stem, name)] = ["PASS"]
    assert {r["result"] for r in report.judge(FAULT_MAP, outcomes)} == {"PASS"}

    rel, _, name = FAULT_MAP["network_loss"][0].partition("::")
    key = (Path(rel).stem, name)
    for spoiled in (["PASS", "FAIL"], ["SKIP"], None):
        broken = dict(outcomes)
        if spoiled is None:
            del broken[key]
        else:
            broken[key] = spoiled
        results = {r["fault"]: r["result"] for r in report.judge(FAULT_MAP, broken)}
        assert results["network_loss"] == "FAIL", spoiled
        assert [f for f, v in results.items() if v != "PASS"] == ["network_loss"]


# ---------------------------------------------------------------------------
# In-process injections: the real Store and the real executor, scripted tools
# ---------------------------------------------------------------------------


class ScriptedPort:
    """A capability port whose outcome per capability is scripted. The last
    outcome is sticky, so a fault that never clears is `script(cap, error)`."""

    def __init__(self) -> None:
        self.outcomes: dict[str, list] = {}
        self.calls: list[str] = []

    def script(self, capability: str, *outcomes) -> None:
        self.outcomes[capability] = list(outcomes)

    async def __call__(self, capability: str, arguments: dict):
        self.calls.append(capability)
        queue = self.outcomes.get(capability) or [{"ok": True, "capability": capability}]
        outcome = queue[0]
        if len(queue) > 1:
            self.outcomes[capability] = queue[1:]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


@pytest.fixture
def store() -> Store:
    return Store(":memory:")


@pytest.fixture
def port() -> ScriptedPort:
    return ScriptedPort()


@pytest.fixture
def executor(store, port) -> ContinuousTaskExecutor:
    return ContinuousTaskExecutor(store, port, executor_id="chaos")


def compile_chain(store, capabilities: list[str], *, request: str = "chaos probe") -> str:
    """A straight chain t1 -> t2 -> ... one task per capability."""
    tasks = [{"capability": cap, "arguments": {},
              **({"dependencies": [f"t{i}"]} if i else {})}
             for i, cap in enumerate(capabilities)]
    run = compile_objective(
        store, request=request, tasks=tasks,
        manifest=[{"id": cap, "description": cap} for cap in capabilities],
        objective_summary=request, provenance=PERSON, attended=True)
    return run["run_id"]


def by_capability(store, run_id: str) -> dict:
    return {row["capability"]: row for row in store.objective_tasks(run_id)}


def event_names(store, run_id: str) -> list[str]:
    return [e["event"] for e in store.objective_events(run_id)]


def bring_every_wake_forward(store, run_id: str) -> None:
    """Backoffs are deliberately in the future; do not wait them out in a test."""
    for row in store.objective_tasks(run_id):
        store.update_objective_task(row["task_id"], next_wake=None)
    store.touch_objective_run(run_id, next_wake=None)


class TargetClosedError(Exception):
    """What Playwright raises when the browser dies under a live page."""


@pytest.mark.asyncio
async def test_browser_crash_mid_task_fails_truthfully(store, port, executor):
    port.script("browser_click", TargetClosedError(
        "Target page, context or browser has been closed"))
    run_id = compile_chain(store, ["browser_open", "browser_click", "summarise_page"])

    await executor.start(run_id)

    tasks = by_capability(store, run_id)
    assert tasks["browser_open"]["status"] == TaskStatus.SUCCEEDED
    crashed = tasks["browser_click"]
    assert crashed["status"] == TaskStatus.FAILED
    assert "TargetClosedError" in crashed["evidence"] and "browser" in crashed["evidence"]
    assert tasks["summarise_page"]["status"] == TaskStatus.SKIPPED
    assert "summarise_page" not in port.calls, "work downstream of a crash ran anyway"
    row = store.objective_run(run_id)
    assert row["status"] in O.RUN_TERMINAL and row["status"] != O.RunStatus.COMPLETED
    assert "run.completed" not in event_names(store, run_id)


NETWORK_LOSS = [
    ("connection-reset", ConnectionResetError("connection reset by peer")),
    ("connection-refused", ConnectionRefusedError("connection refused")),
    ("dns-failure", socket.gaierror(-2, "Name or service not known")),
    ("link-down-envelope", {"status": c.NOT_CONFIGURED,
                            "error": "gateway unreachable: connection refused"}),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("name, loss", NETWORK_LOSS, ids=[n for n, _ in NETWORK_LOSS])
async def test_network_loss_marks_the_task_degraded_and_recovers(
        store, port, executor, name, loss):
    port.script("web_fetch", loss, {"ok": True, "capability": "web_fetch"})
    run_id = compile_chain(store, ["web_fetch"])

    await executor.start(run_id)

    # The degradation is on the record - a retry that names the loss - and
    # the same task then completes once, rather than failing for good.
    assert port.calls == ["web_fetch", "web_fetch"], \
        "a dropped link was not retried, or was retried more than once"
    retries = [e for e in store.objective_events(run_id) if e["event"] == "task.retry"]
    assert len(retries) == 1
    detail = json.loads(retries[0]["detail"]) if isinstance(retries[0]["detail"], str) \
        else retries[0]["detail"]
    assert detail["kind"] == FailureKind.CONNECTIVITY and detail["reason"]
    task = by_capability(store, run_id)["web_fetch"]
    assert task["status"] == TaskStatus.SUCCEEDED and task["attempts"] == 2
    assert store.objective_run(run_id)["status"] == O.RunStatus.COMPLETED


@pytest.mark.asyncio
async def test_network_loss_that_never_ends_fails_truthfully(store, port, executor):
    """Waiting is bounded. A link that stays down ends the task with the
    reason, and the objective is never reported complete."""
    port.script("web_fetch", ConnectionResetError("connection reset by peer"))
    run_id = compile_chain(store, ["web_fetch", "write_report"])

    for _ in range(12):
        await executor.start(run_id)
        if store.objective_run(run_id)["status"] in O.RUN_TERMINAL:
            break
        bring_every_wake_forward(store, run_id)

    tasks = by_capability(store, run_id)
    assert tasks["web_fetch"]["status"] in (TaskStatus.FAILED, TaskStatus.BLOCKED)
    assert "connection reset" in tasks["web_fetch"]["evidence"]
    assert "write_report" not in port.calls
    assert 1 < len(port.calls) <= 12, \
        f"expected a bounded number of retries, saw {len(port.calls)} calls"
    row = store.objective_run(run_id)
    assert row["status"] in O.RUN_TERMINAL and row["status"] != O.RunStatus.COMPLETED


MALFORMED = [
    ("none", None),
    ("proxy-html", "<html><body>502 Bad Gateway</body></html>"),
    ("bare-list", ["not", "an", "envelope"]),
    ("raw-bytes", b"\x00\xff\xfe"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("name, output", MALFORMED, ids=[n for n, _ in MALFORMED])
async def test_malformed_tool_output_is_a_failure_not_success(
        store, port, executor, name, output):
    port.script("write_file", output)
    run_id = compile_chain(store, ["write_file", "announce_done"])

    await executor.start(run_id)

    tasks = by_capability(store, run_id)
    assert tasks["write_file"]["status"] == TaskStatus.FAILED, \
        "unreadable tool output was recorded as a success"
    assert tasks["write_file"]["failure_kind"] == FailureKind.STRUCTURAL
    assert "malformed tool output" in tasks["write_file"]["evidence"]
    assert tasks["announce_done"]["status"] == TaskStatus.SKIPPED
    assert "announce_done" not in port.calls
    row = store.objective_run(run_id)
    assert row["status"] in O.RUN_TERMINAL and row["status"] != O.RunStatus.COMPLETED
    assert "run.completed" not in event_names(store, run_id)


# ---------------------------------------------------------------------------
# Worker death: one process holds the leases of two runs, and vanishes
# ---------------------------------------------------------------------------

CHILD = textwrap.dedent(r'''
    import asyncio, json, os, sys, time
    from pathlib import Path
    sys.path.insert(0, sys.argv[1])
    os.environ["ADA_DB"] = sys.argv[2]
    side = Path(sys.argv[3])
    hold = Path(sys.argv[4])            # while this file exists, every "slow_*" blocks
    executor_id = sys.argv[5]
    run_ids = [r for r in sys.argv[6].split(",") if r]
    from friday.store import Store
    from friday.continuous import ContinuousTaskExecutor, RunWatchdog
    from friday.contracts import PERSON
    from friday.objectives import compile_objective

    async def capability(name, arguments):
        with open(side, "a", encoding="utf-8") as fh:
            fh.write(f"{executor_id}\t{name}\tstart\n")
        if name.startswith("slow_"):
            while hold.exists():
                await asyncio.sleep(0.05)
        with open(side, "a", encoding="utf-8") as fh:
            fh.write(f"{executor_id}\t{name}\tdone\n")
        return {"ok": True, "capability": name}

    def chain(store, tag):
        names = [f"fast_{tag}", f"slow_{tag}", f"after_{tag}"]
        tasks = [{"capability": n, "arguments": {},
                  **({"dependencies": [f"t{i}"]} if i else {})}
                 for i, n in enumerate(names)]
        return compile_objective(
            store, request=f"chaos run {tag}", tasks=tasks,
            manifest=[{"id": n, "description": n} for n in names],
            objective_summary=f"chaos run {tag}", provenance=PERSON,
            attended=True)["run_id"]

    async def main():
        store = Store(sys.argv[2])
        ex = ContinuousTaskExecutor(store, capability, executor_id=executor_id)
        ex.lease_timeout = 1.0
        ex.stop()
        if not run_ids:
            ids = [chain(store, "a"), chain(store, "b")]
            print(json.dumps({"run_ids": ids}), flush=True)
            await asyncio.gather(*(ex.start(r) for r in ids))   # one worker, two runs
        else:
            # A fresh control plane: nothing in memory, only the database.
            watchdog = RunWatchdog(ex, lease_timeout=1.0)
            orphaned = await watchdog.sweep_once()
            print(json.dumps({"orphaned": sorted(orphaned)}), flush=True)
            deadline = time.time() + 30
            while time.time() < deadline:
                runs = [store.objective_run(r) for r in run_ids]
                if all(r["status"] in ("COMPLETED", "PARTIAL", "FAILED", "CANCELLED")
                       for r in runs):
                    break
                await asyncio.sleep(0.05)
            again = await watchdog.sweep_once()
            print(json.dumps({"finals": {r: store.objective_run(r)["status"]
                                         for r in run_ids},
                              "second_sweep": again}), flush=True)

    asyncio.run(main())
''')


def _spawn(db: Path, side: Path, hold: Path, executor_id: str,
           run_ids: str = "") -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-c", CHILD, str(ROOT), str(db), str(side), str(hold),
         executor_id, run_ids],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=str(ROOT),
        env={**os.environ, "PYTHONIOENCODING": "utf-8"})


def _wait_for(pred, timeout: float, what: str) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {what}")


@pytest.mark.slow
def test_worker_death_with_two_runs_in_flight_resumes_both_exactly_once(tmp_path):
    db = tmp_path / "chaos.sqlite3"
    side = tmp_path / "side_effects.tsv"
    hold = tmp_path / "hold"
    hold.write_text("block the slow tasks")
    side.write_text("")

    first = _spawn(db, side, hold, "worker-1")
    run_ids = json.loads(first.stdout.readline())["run_ids"]
    assert len(run_ids) == 2
    _wait_for(lambda: all(f"worker-1\tslow_{t}\tstart" in side.read_text() for t in "ab"),
              20, "both slow tasks to be in flight")
    first.kill()                          # no hook, no cleanup: the worker vanishes
    first.wait(timeout=10)
    assert first.returncode != 0

    store = Store(str(db))
    for run_id in run_ids:
        row = store.objective_run(run_id)
        assert row["status"] == "RUNNING" and row["lease_executor_id"] == "worker-1"
    hold.unlink()
    time.sleep(1.2)                       # let both dead leases expire

    second = _spawn(db, side, hold, "worker-2", ",".join(run_ids))
    out, err = second.communicate(timeout=90)
    assert second.returncode == 0, err[-2000:]
    lines = [json.loads(l) for l in out.splitlines() if l.startswith("{")]
    assert lines[0]["orphaned"] == sorted(run_ids), (lines, err[-800:])
    assert set(lines[-1]["finals"].values()) == {"COMPLETED"}, (lines, err[-800:])
    assert lines[-1]["second_sweep"] == [], "a finished run was reconciled twice"

    store = Store(str(db))
    effects = side.read_text().splitlines()
    for tag, run_id in zip("ab", run_ids):
        tasks = by_capability(store, run_id)
        assert {t["status"] for t in tasks.values()} == {"SUCCEEDED"}
        assert all(t["evidence"] for t in tasks.values())
        names = event_names(store, run_id)
        assert names.count("watchdog.orphaned") == 1, names
        assert names.count("run.completed") == 1, names
        leases = [json.loads(e["detail"]) if isinstance(e.get("detail"), str) else e.get("detail")
                  for e in store.objective_events(run_id) if e["event"] == "lease.acquired"]
        assert [l["executor_id"] for l in leases] == ["worker-1", "worker-2"]
        assert effects.count(f"worker-1\tfast_{tag}\tdone") == 1
        assert f"worker-2\tfast_{tag}\tstart" not in effects      # finished work is never replayed
        assert effects.count(f"worker-1\tslow_{tag}\tdone") == 0   # it died before finishing
        assert effects.count(f"worker-2\tslow_{tag}\tdone") == 1   # interrupted work runs once
        assert effects.count(f"worker-2\tafter_{tag}\tdone") == 1


# ---------------------------------------------------------------------------
# Reboot: no process survives, only the file. A cold start finds the strays.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cold_boot_sweeps_stale_leases_and_names_the_orphan(tmp_path):
    from datetime import datetime, timedelta

    db = str(tmp_path / "rebooted.sqlite3")
    before = Store(db)
    hour_ago = (datetime.now() - timedelta(hours=1)).isoformat()
    next_hour = (datetime.now() + timedelta(hours=1)).isoformat()

    died_mid_task = compile_chain(before, ["fetch", "summarise"], request="died mid task")
    died_before_lease = compile_chain(before, ["draft"], request="died before its lease")
    owned_elsewhere = compile_chain(before, ["alive"], request="owned by a live worker")
    asked_the_boss = compile_chain(before, ["blocked"], request="waiting on the boss")

    # The host before the reboot really ran `fetch` (so its evidence is in the
    # ledger), then was caught with `summarise` in flight and a lease nobody
    # will refresh.
    dying = ContinuousTaskExecutor(before, ScriptedPort(), executor_id="host-before-reboot")
    dying.portion_budget = 1
    await dying.start(died_mid_task)
    dying.stop()
    assert by_capability(before, died_mid_task)["fetch"]["status"] == TaskStatus.SUCCEEDED
    before.update_objective_task(by_capability(before, died_mid_task)["summarise"]["task_id"],
                                 status=TaskStatus.RUNNING, started_at=hour_ago)
    before.touch_objective_run(died_mid_task, status=O.RunStatus.RUNNING, next_wake=hour_ago,
                               lease_executor_id="host-before-reboot", lease_expiry=hour_ago)
    before.touch_objective_run(died_before_lease, status=O.RunStatus.RUNNING)
    before.touch_objective_run(owned_elsewhere, status=O.RunStatus.RUNNING,
                               lease_executor_id="another-live-worker", lease_expiry=next_hour)
    before.touch_objective_run(asked_the_boss, status=O.RUN_WAITING_PERMISSION,
                               lease_executor_id="host-before-reboot", lease_expiry=hour_ago)
    del before                            # the machine goes down

    after = Store(db)                     # the machine comes back: new Store, new identity
    port = ScriptedPort()
    executor = ContinuousTaskExecutor(after, port, executor_id="boot-2")
    executor.stop()
    orphaned = await RunWatchdog(executor).sweep_once()

    assert sorted(orphaned) == sorted([died_mid_task, died_before_lease]), orphaned
    deadline = time.time() + 20
    while time.time() < deadline and any(
            after.objective_run(r)["status"] not in O.RUN_TERMINAL
            for r in (died_mid_task, died_before_lease)):
        await asyncio.sleep(0.05)

    for run_id in (died_mid_task, died_before_lease):
        assert after.objective_run(run_id)["status"] == O.RunStatus.COMPLETED
        names = event_names(after, run_id)
        assert names.count("watchdog.orphaned") == 1 and names.count("run.completed") == 1
        orphan = next(e for e in after.objective_events(run_id)
                      if e["event"] == "watchdog.orphaned")
        detail = json.loads(orphan["detail"]) if isinstance(orphan["detail"], str) else orphan["detail"]
        assert detail["watchdog"] == "boot-2"
    mid = by_capability(after, died_mid_task)
    assert "fetch" not in port.calls, "finished work was replayed after the reboot"
    assert port.calls.count("summarise") == 1
    assert mid["summarise"]["status"] == TaskStatus.SUCCEEDED
    assert "task.interrupted" in event_names(after, died_mid_task)

    # A live lease and a legitimate wait are not orphans, and are left alone.
    assert "alive" not in port.calls and "blocked" not in port.calls
    assert after.objective_run(owned_elsewhere)["lease_executor_id"] == "another-live-worker"
    assert after.objective_run(asked_the_boss)["status"] == O.RUN_WAITING_PERMISSION
    assert "watchdog.orphaned" not in event_names(after, owned_elsewhere)
    assert "watchdog.orphaned" not in event_names(after, asked_the_boss)


# ---------------------------------------------------------------------------
# Invalid model response: the planner is where a model's answer meets the
# durable engine, so the fault is injected there.
# ---------------------------------------------------------------------------

# Long enough, and vague enough, that the deterministic reading hands the
# request to the model (planner_model.needs_model) and the model's answer
# is what gets read.
VAGUE_REQUEST = ("I would like you to think about whether the whole arrangement "
                 "of my afternoon could be improved somehow today")

INVALID_MODEL_ANSWERS = [
    ("empty-text", ""),
    ("not-json", "Sorry, I cannot help with that."),
    ("json-array", "[]"),
    ("json-null", "null"),
    ("json-string", '"all done"'),
    ("goal-is-not-an-object", '{"goals": [1, 2]}'),
    ("goals-is-not-a-list", '{"goals": "open paint"}'),
    ("constraints-is-not-a-list", '{"constraints": 5}'),
    ("provider-error", RuntimeError("no response generated")),
]


class _ScriptedGemini:
    """Stands in for the genai client: one scripted answer per call."""

    def __init__(self, answer) -> None:
        self.models = self
        self._answer = answer

    def generate_content(self, **_kwargs):
        if isinstance(self._answer, BaseException):
            raise self._answer
        return type("Answer", (), {"text": self._answer})()


@pytest.mark.parametrize("name, answer", INVALID_MODEL_ANSWERS,
                         ids=[n for n, _ in INVALID_MODEL_ANSWERS])
def test_an_invalid_model_answer_leaves_the_deterministic_plan_not_a_crash(
        monkeypatch, name, answer):
    from friday import planner as P
    from friday import planner_model as PM

    monkeypatch.setattr(PM, "_RECENT", {})
    monkeypatch.setattr(PM, "_model", lambda: (_ScriptedGemini(answer), "fake-model"))
    deterministic = P.resolve(P.interpret(VAGUE_REQUEST))
    assert PM.needs_model(VAGUE_REQUEST, P.interpret(VAGUE_REQUEST)), \
        "the request no longer reaches the model, so this probes nothing"

    plan = PM.plan_objective(VAGUE_REQUEST)

    # The end state is the plan the deterministic pass already made - same
    # goals, same unresolved remainder - with nothing the model "said" in it.
    assert [g.intent for g in plan.goals] == [g.intent for g in deterministic.goals]
    assert list(plan.unresolved) == list(deterministic.unresolved)
    # And the unusable answer was not kept to fail again on the next read.
    assert PM._RECENT == {}
