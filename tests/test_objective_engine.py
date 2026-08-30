"""
Test H: the agent engine wiring and the lease arbitration.

The durable lease is the only shared state between drivers - the agent
engine, the MCP server's executor and a CLI child may all be alive in the
same process space, and exactly one of them may drive any given wake. What
is proved here:

  the engine  FridayAgent.start_objective_engine() exists, starts once, and
              its background loop actually drives a compiled run to
              COMPLETED through the real dispatch.
  the lease   two executors racing over one run execute every task exactly
              once - a second driver must lose the lease, not re-run work.
  the stop    a stopped executor does not pick up due wakes; a later
              executor can still resume the run from where it paused.
"""

from __future__ import annotations

import asyncio

import pytest

from friday.continuous import ContinuousTaskExecutor
from friday.objectives import TaskStatus, compile_objective
from friday.store import Store

import agent_friday
from friday.objectives import RUN_TERMINAL


class FakeCapabilities:
    """Deterministic capability registry that counts every call."""

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


def demo_registry() -> dict[str, callable]:
    async def system_get_info(args):
        return {"ok": True, "cpu": 23, "memory": 61}

    async def apps_open(args):
        return {"ok": True, "app": args["name"], "pid": 4242}

    return {"system_get_info": system_get_info, "apps_open": apps_open}


def manifest() -> list[dict]:
    return [
        {"id": "system_get_info", "description": "report cpu, memory"},
        {"id": "apps_open", "description": "open an app by name",
         "arguments": {"name": "str"}},
    ]


@pytest.fixture
def store() -> Store:
    return Store(":memory:")


@pytest.fixture
def caps() -> FakeCapabilities:
    return FakeCapabilities(demo_registry())


def chain_tasks() -> list[dict]:
    return [
        {"capability": "system_get_info", "arguments": {}},
        {"capability": "apps_open", "arguments": {"name": "Paint"},
         "dependencies": ["t1"]},
    ]


async def drive_to_terminal(store: Store, run_id: str, timeout: float = 5.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        row = store.objective_run(run_id)
        if row is None or row["status"] in RUN_TERMINAL:
            return row
        await asyncio.sleep(0.05)
    return store.objective_run(run_id)


# ---------------------------------------------------------------------------
# The agent engine
# ---------------------------------------------------------------------------


def test_agent_engine_starts_once_and_has_process_identity(monkeypatch,
                                                          tmp_path):
    monkeypatch.setenv("ADA_DB", str(tmp_path / "engine.sqlite3"))
    agent = object.__new__(agent_friday.FridayAgent)
    agent._objective_engine = None
    agent._router = None

    engine = agent.start_objective_engine()
    assert engine.executor_id == f"agent-{__import__('os').getpid()}"
    assert agent.start_objective_engine() is engine, "starts only once"
    engine.stop()


@pytest.mark.asyncio
async def test_agent_engine_drives_a_real_run_to_completed(monkeypatch,
                                                           tmp_path):
    """The wiring that runs in production: compile + real dispatch + engine."""
    monkeypatch.setenv("ADA_DB", str(tmp_path / "engine.sqlite3"))
    agent = object.__new__(agent_friday.FridayAgent)
    agent._objective_engine = None
    agent._router = None

    engine = agent.start_objective_engine()
    created = compile_objective(
        engine.store, request="check the system",
        tasks=[{"capability": "system_get_info", "arguments": {}}],
        manifest=manifest(), objective_summary="check the system",
    )
    run_id = created["run_id"]

    won = await engine.start(run_id)
    assert won is True
    final = await drive_to_terminal(engine.store, run_id)
    assert final["status"] == "COMPLETED", final
    task = engine.store.objective_tasks(run_id)[0]
    assert task["status"] == TaskStatus.SUCCEEDED
    engine.stop()


# ---------------------------------------------------------------------------
# Two drivers, one run: the lease arbitrates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_two_executors_never_double_drive(store, caps):
    run = compile_objective(
        store, request="race probe", tasks=chain_tasks(),
        manifest=manifest(), objective_summary="race probe",
    )
    run_id = run["run_id"]

    first = ContinuousTaskExecutor(store, caps.call, executor_id="race-a")
    second = ContinuousTaskExecutor(store, caps.call, executor_id="race-b")
    # Both background loops tick concurrently; the lease must let only one
    # drive at a time.
    won = await first.start(run_id)
    assert won is True
    await asyncio.sleep(0.2)

    final = await drive_to_terminal(store, run_id)
    assert final["status"] == "COMPLETED", final
    await asyncio.sleep(0.2)  # give a stray driver a chance to misbehave

    by_cap = {}
    for capability, _ in caps.calls:
        by_cap[capability] = by_cap.get(capability, 0) + 1
    assert by_cap == {"system_get_info": 1, "apps_open": 1}, (
        f"work was executed more than once: {by_cap}"
    )

    assert await second.start(run_id) is False, (
        "a completed run must not be re-acquirable by another driver"
    )
    first.stop()
    second.stop()


@pytest.mark.asyncio
async def test_stopped_executor_does_not_drive_and_new_one_resumes(
        store, caps):
    run = compile_objective(
        store, request="stop probe", tasks=chain_tasks(),
        manifest=manifest(), objective_summary="stop probe",
    )
    run_id = run["run_id"]

    driver = ContinuousTaskExecutor(store, caps.call, executor_id="stop-me")
    driver.portion_budget = 1  # leave the second task for a later wake
    await driver.start(run_id)
    tasks = store.objective_tasks(run_id)
    assert [t["status"] for t in tasks] == ["SUCCEEDED", "QUEUED"]
    row = store.objective_run(run_id)
    assert row["next_wake"], "work remains and must be scheduled"

    driver.stop()
    await asyncio.sleep(0.3)
    tasks = store.objective_tasks(run_id)
    assert [t["status"] for t in tasks] == ["SUCCEEDED", "QUEUED"], (
        "a stopped executor must not keep driving due wakes"
    )

    successor = ContinuousTaskExecutor(store, caps.call,
                                       executor_id="successor")
    successor.portion_budget = 1
    final = await drive_to_terminal(store, run_id)
    assert final["status"] == "COMPLETED", final
    assert len(caps.calls) == 2, caps.calls
    successor.stop()