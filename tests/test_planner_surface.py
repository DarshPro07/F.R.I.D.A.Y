"""
ORCH-001: the planner declares the surface each task works on, so the graphs
it builds run as dependency waves instead of one task at a time.

A task's surface is the files its path arguments name. A task whose path
cannot be read off its arguments declares none, and none means unknown, which
runs alone (friday/waves.py).
"""

from __future__ import annotations

import asyncio

import pytest

from friday import continuous, planner, waves
from friday.continuous import ContinuousTaskExecutor
from friday.contracts import PERSON
from friday.objectives import RUN_TERMINAL, compile_objective
from friday.store import Store

TWO_FILES = ("create a file alpha.txt containing hello "
             "and create a file beta.txt containing world")
THEN_READ = ("create a file alpha.txt containing hello "
             "then read alpha.txt and open notepad")

MANIFEST = [{"id": "files_create", "description": "create a file",
             "arguments": {"path": "str", "content": "str"}},
            {"id": "files_read", "description": "read a file",
             "arguments": {"path": "str"}},
            {"id": "apps_open", "description": "open an app", "arguments": {}}]


def short(task: dict) -> str:
    return task["task_id"].rsplit("-", 1)[-1]


def specs(text: str) -> list[dict]:
    return planner.task_specs(planner.plan_objective(text))


# --- what the planner declares ---------------------------------------------


def test_a_file_task_declares_the_file_it_works_on():
    first, second = specs(TWO_FILES)
    assert first["surface"] == ["alpha.txt"]
    assert second["surface"] == ["beta.txt"]


def test_a_task_with_no_file_declares_nothing_so_it_runs_alone():
    *_, opened = specs(THEN_READ)
    assert opened["capability"] == "apps_open"
    assert "surface" not in opened, "no declared surface means unknown"


def test_a_path_the_task_did_not_get_leaves_the_surface_unknown(monkeypatch):
    # `source` is a path the capability takes; naming only `destination` would
    # understate what it touches, and a missed conflict is the costly error.
    monkeypatch.setattr(planner, "parameters_of",
                        lambda _cap: {"source": True, "destination": True})
    assert planner.surface_for("files_copy", {"destination": "b.txt"}) == []
    assert planner.surface_for(
        "files_copy", {"source": "a.txt", "destination": "b.txt"}) \
        == ["a.txt", "b.txt"]
    assert planner.surface_for("files_copy", {"source": "  "}) == []


def test_a_blank_or_non_text_path_is_not_a_surface(monkeypatch):
    monkeypatch.setattr(planner, "parameters_of", lambda _cap: {"path": True})
    assert planner.surface_for("files_read", {"path": ""}) == []
    assert planner.surface_for("files_read", {"path": 7}) == []
    assert planner.surface_for("files_read", {}) == []


def test_a_group_declares_no_surface_and_its_children_run_alone(store):
    # Real groups use the `composite` marker, which takes no path, and compile
    # keeps no surface on a group's leaves; so a grouped plan stays serial.
    group = planner.Goal(
        goal_id="g1", intent="make two files", operation="CREATE",
        target="file", capability="composite",
        children=({"capability": "files_create",
                   "arguments": {"path": "alpha.txt", "content": "a"}},
                  {"capability": "files_create",
                   "arguments": {"path": "beta.txt", "content": "b"}}))
    (spec,) = planner.task_specs(planner.Plan(goals=[group]))
    assert spec["capability"] == "composite" and len(spec["children"]) == 2
    assert "surface" not in spec, "a group is a view over leaves, not a step"
    run = compile_objective(
        store, request="two files", tasks=[spec], manifest=MANIFEST,
        objective_summary="two files", provenance=PERSON, attended=True)
    leaves = [t for t in store.objective_tasks(run["run_id"])
              if t.get("parent_id")]
    assert len(leaves) == 2
    got = waves.pack_waves(leaves, continuous._task_surface, cap=3)
    assert [len(wave) for wave in got] == [1, 1], "unknown leaves run alone"


# --- the planner-built graph through the wave scheduler ---------------------


@pytest.fixture
def store() -> Store:
    return Store(":memory:")


def compile_text(store, text: str):
    run = compile_objective(
        store, request=text, tasks=specs(text), manifest=MANIFEST,
        objective_summary=text, provenance=PERSON, attended=True)
    return run["run_id"], store.objective_tasks(run["run_id"])


def test_independent_planner_tasks_pack_into_one_wave(store):
    _, tasks = compile_text(store, TWO_FILES)
    got = waves.pack_waves(tasks, continuous._task_surface, cap=3)
    assert [[short(t) for t in wave] for wave in got] == [["t1", "t2"]]


def test_a_planner_task_without_a_surface_gets_a_wave_of_its_own(store):
    _, tasks = compile_text(store, THEN_READ)
    ready = [t for t in tasks if short(t) in ("t1", "t3")]
    got = waves.pack_waves(ready, continuous._task_surface, cap=3)
    assert [[short(t) for t in wave] for wave in got] == [["t1"], ["t3"]]


def test_planner_tasks_naming_one_file_wait_for_each_other(store):
    text = ("create a file alpha.txt containing hello "
            "and create a file alpha.txt containing world")
    _, tasks = compile_text(store, text)
    assert [t["detail"]["surface"] for t in tasks] == [["alpha.txt"]] * 2
    got = waves.pack_waves(tasks, continuous._task_surface, cap=3)
    assert [[short(t) for t in wave] for wave in got] == [["t1"], ["t2"]]


class Probe:
    """Capability stand-in: parks each task on its path until the test opens it."""

    def __init__(self) -> None:
        self.started: list[str] = []
        self.running = 0
        self.peak = 0
        self.gates: dict[str, asyncio.Event] = {}

    def gate(self, key: str) -> asyncio.Event:
        return self.gates.setdefault(key, asyncio.Event())

    async def call(self, capability: str, arguments: dict) -> dict:
        key = arguments.get("path", capability)
        self.started.append(key)
        self.running += 1
        self.peak = max(self.peak, self.running)
        try:
            await self.gate(key).wait()
        finally:
            self.running -= 1
        return {"ok": True}


class Governor:
    def __init__(self, probe: Probe, capacity: int) -> None:
        self.probe, self.capacity = probe, capacity

    def worker_capacity(self, objective_id: str = "") -> int:
        return self.capacity

    def active(self, kind=None) -> int:
        return self.probe.running

    def justify_parallel(self, objective_id: str) -> None:
        pass


async def until(condition, timeout: float = 3.0) -> None:
    deadline = asyncio.get_event_loop().time() + timeout
    while not condition():
        assert asyncio.get_event_loop().time() < deadline, "timed out waiting"
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_a_planner_built_graph_runs_its_independent_tasks_at_once(store):
    run_id, _ = compile_text(store, TWO_FILES)
    probe = Probe()
    executor = ContinuousTaskExecutor(
        store, probe.call, executor_id="surface",
        governor=lambda: Governor(probe, capacity=3))
    driving = asyncio.ensure_future(executor.start(run_id))
    await until(lambda: len(probe.started) == 2)
    assert probe.peak == 2, "both file tasks were parked together"
    for key in ("alpha.txt", "beta.txt"):
        probe.gate(key).set()
    await until(lambda: store.objective_run(run_id)["status"] in RUN_TERMINAL)
    assert await asyncio.wait_for(driving, 3) is True
    executor.stop()
    assert store.objective_run(run_id)["status"] == "COMPLETED"


@pytest.mark.asyncio
async def test_a_planner_built_graph_stays_within_the_governor_ceiling(store):
    run_id, _ = compile_text(store, TWO_FILES)
    probe = Probe()
    executor = ContinuousTaskExecutor(
        store, probe.call, executor_id="surface",
        governor=lambda: Governor(probe, capacity=1))
    driving = asyncio.ensure_future(executor.start(run_id))
    await until(lambda: len(probe.started) == 1)
    await asyncio.sleep(0.1)
    assert probe.peak == 1 and len(probe.started) == 1, "capacity 1 admits one"
    probe.gate("alpha.txt").set()
    await until(lambda: len(probe.started) == 2)
    probe.gate("beta.txt").set()
    await until(lambda: store.objective_run(run_id)["status"] in RUN_TERMINAL)
    assert await asyncio.wait_for(driving, 3) is True
    executor.stop()
    assert probe.peak == 1
