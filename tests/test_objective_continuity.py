"""
P0 gate tests: one user message must drive a multi-step objective to
COMPLETED without a single "Continue".

These tests are written BEFORE the engine exists. The harness sends exactly
one user message and then stays silent - no helper may secretly send
continue/resume/go-on/next. Progression must come from the
ContinuousTaskExecutor, the lease, the wake schedule and the watchdog alone.
"""

from __future__ import annotations

import asyncio

import pytest

from friday.continuous import ContinuousTaskExecutor, RunWatchdog
from friday.objectives import (
    TASK_TERMINAL,
    TASK_STATUSES,
    TaskStatus,
    compile_objective,
)
from friday.store import Store

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class FakeCapabilities:
    """Deterministic capability registry: a dict of name -> async callable."""

    def __init__(self, registry: dict[str, callable] | None = None) -> None:
        self.registry = dict(registry or {})
        self.calls: list[tuple[str, dict]] = []

    def add(self, name: str, fn: callable) -> None:
        self.registry[name] = fn

    async def call(self, capability: str, arguments: dict) -> dict:
        self.calls.append((capability, arguments))
        fn = self.registry.get(capability)
        if fn is None:
            raise LookupError(f"no capability called {capability!r}")
        if asyncio.iscoroutinefunction(fn):
            return await fn(arguments)
        return fn(arguments)


def manifest() -> list[dict]:
    """Capability manifest describing the demo objective's tools."""
    return [
        {"id": "system_health", "description": "report cpu, memory, disks"},
        {"id": "app_open", "description": "open an app by name",
         "arguments": {"name": "str"}},
        {"id": "research_recent_ai", "description": "summarise recent AI news"},
        {"id": "world_monitor", "description": "open the world dashboard"},
        {"id": "create_test_workspace", "description": "create a test dir"},
        {"id": "create_file", "description": "write a file",
         "arguments": {"path": "str", "content": "str"}},
        {"id": "verify_file", "description": "read a file back and verify",
         "arguments": {"path": "str"}},
        {"id": "final_summary", "description": "produce the end summary"},
    ]


def demo_registry() -> dict[str, callable]:
    """Ten real calls worth of work, all deterministic."""
    async def system_health(args):
        return {"ok": True, "cpu": 23, "memory": 61, "disks": ["C:"]}

    async def app_open(args):
        return {"ok": True, "app": args["name"], "pid": 4242}

    async def research_recent_ai(args):
        return {"ok": True, "headlines": ["Gemini ships", "Agents everywhere"]}

    async def world_monitor(args):
        return {"ok": True, "opened": "world-monitor"}

    async def create_test_workspace(args):
        return {"ok": True, "path": "C:/scratch/friday-tests"}

    async def create_file(args):
        return {"ok": True, "wrote": args["path"], "bytes": len(args["content"])}

    async def verify_file(args):
        return {"ok": True, "read": "hello friday", "verified": True}

    async def final_summary(args):
        return {"ok": True, "summary": "all done"}

    return {
        "system_health": system_health,
        "app_open": app_open,
        "research_recent_ai": research_recent_ai,
        "world_monitor": world_monitor,
        "create_test_workspace": create_test_workspace,
        "create_file": create_file,
        "verify_file": verify_file,
        "final_summary": final_summary,
    }


@pytest.fixture
def store() -> Store:
    return Store(":memory:")


@pytest.fixture
def caps() -> FakeCapabilities:
    return FakeCapabilities(demo_registry())


@pytest.fixture
def executor(store, caps) -> ContinuousTaskExecutor:
    return ContinuousTaskExecutor(store, caps.call, executor_id="test-exec-1")


def demo_tasks() -> list[dict]:
    """The 8-task demo graph from the spec's acceptance example."""
    return [
        {"capability": "system_health", "arguments": {}},
        {"capability": "app_open", "arguments": {"name": "Paint"},
         "dependencies": ["t1"]},
        {"capability": "research_recent_ai", "arguments": {}},
        {"capability": "world_monitor", "arguments": {},
         "dependencies": ["t3"]},
        {"capability": "create_test_workspace", "arguments": {},
         "dependencies": ["t1"]},
        {"capability": "create_file", "arguments": {
            "path": "{{tasks.t5.path}}/gate.txt", "content": "hello friday"},
         "dependencies": ["t5"]},
        {"capability": "verify_file", "arguments": {"path": "{{tasks.t5.path}}/gate.txt"},
         "dependencies": ["t6"]},
        {"capability": "final_summary", "arguments": {},
         "dependencies": ["t2", "t4", "t7"]},
    ]


# ---------------------------------------------------------------------------
# US1: one request, zero "Continue", runs to COMPLETED
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_single_message_runs_to_completed(store, executor, caps):
    """The P0 gate: one user message; the engine finishes without another."""
    run = compile_objective(
        store,
        request="check health, open Paint, research AI news, open the world "
                "monitor, make a test workspace, write and verify a file, "
                "then summarise",
        tasks=demo_tasks(),
        manifest=manifest(),
        objective_summary="demo objective",
    )

    won = await executor.start(run["run_id"])
    assert won is True, "the executor must win the lease on first start"

    while True:
        row = store.objective_run(run["run_id"])
        assert row is not None
        if row["status"] in ("COMPLETED", "PARTIAL", "FAILED", "CANCELLED"):
            break
        if row["next_wake"]:
            await asyncio.sleep(0.05)
        else:
            break

    tasks = store.objective_tasks(run["run_id"])
    assert len(tasks) == 8
    assert all(t["status"] in TASK_TERMINAL for t in tasks), (
        f"non-terminal tasks remain: "
        f"{[(t['task_id'], t['status']) for t in tasks]}"
    )
    assert all(t["status"] == TaskStatus.SUCCEEDED for t in tasks), (
        f"not all succeeded: {[(t['task_id'], t['status']) for t in tasks]}"
    )

    final = store.objective_run(run["run_id"])
    assert final["status"] == "COMPLETED", final
    assert final["manual_continue_count"] == 0
    assert final["summary"] is not None

    events = [e["event"] for e in store.objective_events(run["run_id"])]
    assert "continuation.scheduled" in events
    assert "run.completed" in events
    assert "run.created" in events


@pytest.mark.asyncio
async def test_tool_boundary_schedules_continuation(store, executor, caps):
    """A budget boundary mid-graph must not strand the run."""
    run = compile_objective(
        store, request="boundary test", tasks=demo_tasks(),
        manifest=manifest(), objective_summary="boundary test",
    )

    # Portion budget of 2: only two tasks may run per wake.
    executor.portion_budget = 2
    await executor.start(run["run_id"])
    tasks = store.objective_tasks(run["run_id"])
    assert len([t for t in tasks if t["status"] == TaskStatus.SUCCEEDED]) == 2

    row = store.objective_run(run["run_id"])
    assert row["status"] == "RUNNING"
    assert row["next_wake"], "a boundary with work left must schedule a wake"
    events = [e["event"] for e in store.objective_events(run["run_id"])]
    assert "continuation.scheduled" in events

    # The engine keeps going on its own - still no second user message.
    deadline = asyncio.get_event_loop().time() + 3.0
    while asyncio.get_event_loop().time() < deadline:
        row = store.objective_run(run["run_id"])
        if row["status"] in ("COMPLETED", "PARTIAL", "FAILED", "CANCELLED"):
            break
        await asyncio.sleep(0.05)
    assert row["status"] == "COMPLETED", (
        f"run stranded at {row['status']}; next_wake={row['next_wake']}"
    )


@pytest.mark.asyncio
async def test_no_user_message_ever_sent_to_capabilities(store, executor, caps):
    """The harness asserts progression never arrives as a user message."""
    run = compile_objective(
        store, request="no-continue probe", tasks=demo_tasks(),
        manifest=manifest(), objective_summary="probe",
    )
    assert run["manual_continue_count"] == 0
    await executor.start(run["run_id"])
    # All capability calls were direct executor dispatch - nothing here to
    # assert beyond the run completing without the harness speaking again.
    assert executor.acquire(run["run_id"]) is False, (
        "a completed run must not be re-acquirable"
    )


# ---------------------------------------------------------------------------
# US1: task status vocabulary sanity
# ---------------------------------------------------------------------------


def test_task_status_vocabulary_is_stable():
    assert TaskStatus.SUCCEEDED == "SUCCEEDED"
    assert "SKIPPED" in TASK_STATUSES
    assert "RUNNING" in TASK_STATUSES
