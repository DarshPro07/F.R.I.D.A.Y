"""
ORCH-001: an objective's independent tasks run as dependency waves.

The executor starts together the tasks whose dependencies succeeded and whose
declared file surfaces are disjoint, as many as the governor admits for the
run; a task that shares a file with a running one waits, and one that
declares no surface runs alone. Nothing here sleeps for time: every task
parks on an event the test opens, so what runs at once is observed, not hoped.
"""

from __future__ import annotations

import asyncio

import pytest

from friday import continuous, trajectory, waves
from friday import governor as G
from friday.continuous import ContinuousTaskExecutor
from friday.contracts import PERSON, ObjectiveContract
from friday.objectives import RUN_TERMINAL, TASK_TERMINAL, TaskStatus, compile_objective
from friday.store import Store


# --- the pure rule ---------------------------------------------------------


def surf(*paths):
    return waves.surface_of(paths)


def test_paths_are_normalised_and_line_ranges_dropped():
    assert waves.normalize_path("friday\\continuous.py:631-657") == "friday/continuous.py"
    assert waves.normalize_path("./friday//a.py") == "friday/a.py"
    assert waves.normalize_path("friday/") == "friday/"
    assert waves.surface_of(None) == frozenset()
    assert waves.surface_of("friday/a.py") == frozenset(), "a bare string is not a list"
    assert waves.surface_of(["", "  "]) == frozenset()


def test_a_directory_overlaps_every_path_under_it():
    assert waves.overlaps(surf("friday/"), surf("friday/a.py"))
    assert waves.overlaps(surf("friday/a.py"), surf("FRIDAY/A.py"))
    assert not waves.overlaps(surf("friday/a.py"), surf("friday/b.py"))
    assert not waves.overlaps(surf("friday/"), surf("fridayx/a.py"))


def test_unknown_runs_alone_and_blocks_everything():
    rows = {"a": surf("x.py"), "b": surf("y.py"), "u": surf()}
    pick = lambda items, running=(), room=9: waves.pick_starts(  # noqa: E731
        items, rows.__getitem__, running=running, room=room)
    assert pick(["a", "b"]) == ["a", "b"]
    assert pick(["u", "a"]) == ["u"], "unknown at the head starts alone"
    assert pick(["a", "u", "b"]) == ["a", "b"], "unknown is passed over, not joined"
    assert pick(["u"], running=[surf("x.py")]) == [], "unknown waits for a quiet moment"
    assert pick(["a"], running=[surf()]) == [], "nothing starts beside an unknown task"
    assert pick(["a"], running=[surf("x.py")]) == [], "shares a file with a running task"
    assert pick(["b"], running=[surf("x.py")]) == ["b"]
    assert pick(["a", "b"], room=1) == ["a"]
    assert pick(["a"], room=0) == []


def test_pack_waves_is_greedy_in_order_and_capped():
    rows = {"a": surf("x.py"), "b": surf("x.py"), "c": surf("y.py"),
            "d": surf("z.py"), "u": surf()}
    got = waves.pack_waves(["a", "b", "c", "d", "u"], rows.__getitem__, cap=2)
    assert got == [["a", "c"], ["b", "d"], ["u"]]


# --- the executor ----------------------------------------------------------


class Probe:
    """Capability stand-in: records what ran at once and parks each task."""

    def __init__(self) -> None:
        self.started: list[str] = []
        self.finished: list[str] = []
        self.running = 0
        self.peak = 0
        self.gates: dict[str, asyncio.Event] = {}

    def gate(self, name: str) -> asyncio.Event:
        return self.gates.setdefault(name, asyncio.Event())

    def open_all(self, *names: str) -> None:
        for name in names:
            self.gate(name).set()

    async def call(self, capability: str, arguments: dict) -> dict:
        name = arguments["name"]
        self.started.append(name)
        self.running += 1
        self.peak = max(self.peak, self.running)
        try:
            await self.gate(name).wait()
        finally:
            self.running -= 1
        self.finished.append(name)
        return {"ok": True, "name": name}


class StubGovernor:
    """What the executor asks of the governor: a ceiling and what is held."""

    def __init__(self, probe: Probe, capacity: int, others: int = 0) -> None:
        self.probe = probe
        self.capacity = capacity
        self.others = others
        self.justified: list[str] = []

    def worker_capacity(self, objective_id: str = "") -> int:
        return self.capacity

    def active(self, kind=None) -> int:
        # Each running task holds a worker lease, as a real worker does.
        return self.others + self.probe.running

    def justify_parallel(self, objective_id: str) -> None:
        self.justified.append(objective_id)


MANIFEST = [{"id": "work", "description": "do one piece of work",
             "arguments": {"name": "str"}}]


def task(name: str, *surface: str, after: tuple[str, ...] = ()) -> dict:
    spec = {"capability": "work", "arguments": {"name": name}}
    if surface:
        spec["surface"] = list(surface)
    if after:
        spec["dependencies"] = list(after)
    return spec


@pytest.fixture
def store() -> Store:
    return Store(":memory:")


def start_run(store, tasks, capacity=3, others=0, drive=True, governance=None):
    probe = Probe()
    gov = StubGovernor(probe, capacity, others)
    contract = (ObjectiveContract(goal="waves", governance=governance)
                if governance else None)
    run = compile_objective(
        store, request="waves", tasks=tasks, manifest=MANIFEST,
        objective_summary="waves", provenance=PERSON, attended=True,
        contract=contract)
    executor = ContinuousTaskExecutor(store, probe.call, executor_id="waves",
                                      governor=lambda: gov)
    # start() drives the run to its next wait, so it runs beside the test.
    if drive:
        probe.driving = asyncio.ensure_future(executor.start(run["run_id"]))
    return run["run_id"], probe, gov, executor


async def until(condition, timeout: float = 3.0) -> None:
    deadline = asyncio.get_event_loop().time() + timeout
    while not condition():
        assert asyncio.get_event_loop().time() < deadline, "timed out waiting"
        await asyncio.sleep(0.01)


async def settle(store, run_id, probe, executor, *names):
    probe.open_all(*names)
    await until(lambda: store.objective_run(run_id)["status"] in RUN_TERMINAL)
    assert await asyncio.wait_for(probe.driving, 3) is True
    executor.stop()
    return store.objective_run(run_id)


@pytest.mark.asyncio
async def test_independent_branches_over_disjoint_files_run_at_once(store):
    run_id, probe, gov, executor = start_run(store, [
        task("a", "friday/a.py"), task("b", "friday/b.py"),
        task("c", "tests/test_c.py")])
    await until(lambda: len(probe.started) == 3)
    assert probe.peak == 3 and probe.finished == [], "all three parked together"
    assert gov.justified == [run_id], "a wave of independent tasks says so"
    final = await settle(store, run_id, probe, executor, "a", "b", "c")
    assert final["status"] == "COMPLETED", final


@pytest.mark.asyncio
async def test_a_task_sharing_a_file_with_a_running_one_waits(store):
    run_id, probe, gov, executor = start_run(store, [
        task("first", "friday/shared.py:10-20"),
        task("second", "friday/shared.py:90"),
        task("other", "friday/other.py")])
    await until(lambda: set(probe.started) == {"first", "other"})
    await asyncio.sleep(0.1)
    assert "second" not in probe.started, "it shares a file with a running task"
    probe.open_all("first")
    await until(lambda: "second" in probe.started)
    assert probe.finished[0] in {"first", "other"}
    assert "first" in probe.finished, "it started only once the sharer was done"
    final = await settle(store, run_id, probe, executor, "other", "second")
    assert final["status"] == "COMPLETED", final


@pytest.mark.asyncio
async def test_a_task_starts_only_when_its_dependencies_are_done(store):
    run_id, probe, gov, executor = start_run(store, [
        task("base", "friday/a.py"),
        task("child", "friday/b.py", after=("t1",)),
        task("side", "friday/c.py")])
    await until(lambda: set(probe.started) == {"base", "side"})
    await asyncio.sleep(0.1)
    assert "child" not in probe.started, "its dependency has not succeeded"
    probe.open_all("side")
    await asyncio.sleep(0.1)
    assert "child" not in probe.started, "a finished sibling is not its dependency"
    probe.open_all("base")
    await until(lambda: "child" in probe.started)
    assert "base" in probe.finished
    final = await settle(store, run_id, probe, executor, "child")
    assert final["status"] == "COMPLETED", final


@pytest.mark.asyncio
@pytest.mark.parametrize("capacity,others,expected", [
    (2, 0, 2),     # the governor's ceiling
    (1, 0, 1),     # pressure took it down to one
    (3, 2, 1),     # someone else holds two of the three workers
])
async def test_concurrency_never_exceeds_what_the_governor_admits(
        store, capacity, others, expected):
    names = [f"n{i}" for i in range(5)]
    run_id, probe, gov, executor = start_run(
        store, [task(n, f"friday/{n}.py") for n in names],
        capacity=capacity, others=others)
    await until(lambda: probe.running == expected)
    await asyncio.sleep(0.1)
    assert probe.running == expected and probe.peak == expected
    final = await settle(store, run_id, probe, executor, *names)
    assert final["status"] == "COMPLETED", final
    assert probe.peak == expected, "the cap held for the whole run"
    assert sorted(probe.finished) == names, "every task still ran"


@pytest.mark.asyncio
async def test_tasks_that_declare_no_surface_run_one_at_a_time_in_order(store):
    run_id, probe, gov, executor = start_run(
        store, [task("a"), task("b"), task("c")])
    await until(lambda: probe.started == ["a"])
    await asyncio.sleep(0.1)
    assert probe.started == ["a"], "unknown surface runs alone"
    assert gov.justified == [], "nothing independent, nothing to justify"
    probe.open_all("a")
    await until(lambda: probe.started == ["a", "b"])
    final = await settle(store, run_id, probe, executor, "b", "c")
    assert final["status"] == "COMPLETED", final
    assert probe.started == ["a", "b", "c"] and probe.peak == 1


@pytest.mark.asyncio
async def test_a_chain_is_never_called_parallel(store):
    run_id, probe, gov, executor = start_run(store, [
        task("a", "friday/a.py"),
        task("b", "friday/b.py", after=("t1",))])
    final = await settle(store, run_id, probe, executor, "a", "b")
    assert final["status"] == "COMPLETED", final
    assert gov.justified == [] and probe.peak == 1


@pytest.mark.asyncio
async def test_a_failing_task_is_raised_after_the_running_ones_settle(store):
    run_id, probe, gov, executor = start_run(store, [
        task("boom", "friday/a.py"), task("slow", "friday/b.py")], drive=False)
    original = executor._execute_task

    async def explode(run, one):
        if one["arguments"]["name"] == "boom":
            await until(lambda: "slow" in probe.started)
            raise RuntimeError("worker exploded")
        await original(run, one)

    executor._execute_task = explode
    ready = executor._ready_snapshot(run_id)
    opener = asyncio.get_event_loop().call_later(0.2, probe.open_all, "slow")
    with pytest.raises(RuntimeError, match="worker exploded"):
        await executor._execute_tasks(run_id, ready, 8)
    opener.cancel()
    assert "slow" in probe.finished, "the other task was not abandoned mid-call"
    executor.stop()


def task_row(store, run_id, name):
    return next(t for t in store.objective_tasks(run_id)
                if t["arguments"]["name"] == name)


@pytest.mark.asyncio
async def test_changes_in_flight_count_toward_the_side_effect_ceiling(store, monkeypatch):
    """Serially the first change had finished before the second was checked;
    side by side it is still running, and it is still being made."""
    monkeypatch.setattr(trajectory, "_side_effecting", lambda cap: cap == "work")
    run_id, probe, gov, executor = start_run(
        store, [task("a", "friday/a.py"), task("b", "friday/b.py")],
        governance={"max_side_effects": 1})
    await until(lambda: task_row(store, run_id, "b")["status"] in TASK_TERMINAL)
    assert probe.started == ["a"], "the second change went through beside the first"
    assert "at most 1" in str(task_row(store, run_id, "b")["evidence"])
    await settle(store, run_id, probe, executor, "a")
    assert task_row(store, run_id, "a")["status"] == TaskStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_preconditions_on_finished_steps_keep_the_run_one_at_a_time(store):
    """`requires` reads the results of FINISHED steps, so a step that could
    establish one must finish before the next starts."""
    run_id, probe, gov, executor = start_run(
        store, [task("a", "friday/a.py"), task("b", "friday/b.py")],
        governance={"requires": [{"field": "approved", "equals": True}]})
    await until(lambda: "a" in probe.started)
    await asyncio.sleep(0.1)
    assert probe.started == ["a"], "b started before a, which may establish its precondition"
    probe.open_all("a")
    await until(lambda: probe.started == ["a", "b"])
    final = await settle(store, run_id, probe, executor, "b")
    assert final["status"] == "COMPLETED", final
    assert probe.peak == 1


def refusal() -> G.Refused:
    return G.Refused(G.Decision(G.QUEUE, G.WORKER, "worker cap 2 reached (2 active)",
                                G.NORMAL))


@pytest.mark.asyncio
async def test_a_worker_the_governor_refuses_leaves_the_task_for_later(store):
    run_id, probe, gov, executor = start_run(store, [task("a", "friday/a.py")],
                                             drive=False)

    async def refuse(capability, arguments):
        raise refusal()

    executor.call_capability = refuse
    await executor.start(run_id)
    one = task_row(store, run_id, "a")
    assert one["status"] == TaskStatus.WAITING, "a refusal is not a failure"
    assert one["attempts"] == 0, "no work was done, so no attempt was spent"
    assert "worker cap 2" in str(one["evidence"])
    assert store.objective_run(run_id)["status"] not in RUN_TERMINAL
    executor.stop()


@pytest.mark.asyncio
async def test_refusals_never_use_up_the_attempts_and_the_task_still_runs(
        store, monkeypatch):
    monkeypatch.setattr(continuous, "ADMISSION_BACKOFF_SECONDS", 0.0)
    run_id, probe, gov, executor = start_run(store, [task("a", "friday/a.py")],
                                             drive=False)
    refused: list[int] = []

    async def refuse_then_work(capability, arguments):
        if len(refused) < 4:       # more than the task's three attempts
            refused.append(1)
            raise refusal()
        return await probe.call(capability, arguments)

    executor.call_capability = refuse_then_work
    probe.open_all("a")
    await executor.start(run_id)
    assert store.objective_run(run_id)["status"] == "COMPLETED"
    assert len(refused) == 4 and probe.started == ["a"]
    assert task_row(store, run_id, "a")["attempts"] == 1
    executor.stop()


def test_compile_keeps_the_declared_surface_on_the_task(store):
    run = compile_objective(
        store, request="surface", manifest=MANIFEST,
        objective_summary="surface", provenance=PERSON, attended=True,
        tasks=[task("a", "friday\\b.py:3-9", "friday/a.py"), task("b")])
    first, second = store.objective_tasks(run["run_id"])
    assert first["detail"]["surface"] == ["friday/a.py", "friday/b.py"]
    assert "surface" not in second["detail"], "undeclared stays unknown"
