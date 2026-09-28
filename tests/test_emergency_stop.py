"""
STOP-001 (PRD v7 section 53): the out-of-band emergency stop.

STOP is deterministic and never asks the model. It latches in the store, so
every FRIDAY process refuses new actions; it revokes every network grant;
it parks running objectives with the reason; and in every process a watcher
runs that process's own stop hooks and kills the worker processes it owns,
by PID - a worker that ignores a polite request is not asked twice. Resuming
is the person's explicit act, and the latch survives a restart until then.
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time

import psutil
import pytest

from friday import capability_runtime as CR
from friday import contracts as c
from friday import objectives as O
from friday import policy as P
from friday import stop as S
from friday import trust
from friday.store import Store


@pytest.fixture
def store(tmp_path):
    db = Store(tmp_path / "stop.sqlite3")
    yield db
    db.close()


@pytest.fixture(autouse=True)
def _clean_stop_state():
    S.reset_for_tests()
    yield
    S.reset_for_tests()


def _objective(store) -> str:
    return O.compile_objective(
        store, request="read the news",
        tasks=[{"capability": "web_fetch", "arguments": {}}],
        manifest=[{"id": "web_fetch", "description": "fetch"}],
        objective_summary="read the news", provenance=c.PERSON, attended=True)["run_id"]


def test_stop_latches_revokes_every_grant_and_parks_the_work(store):
    run_id = _objective(store)
    grant_id = json.loads(store.objective_run(run_id)["envelope"])["network"]["grant_id"]
    report = S.emergency_stop("the person said stop", actor="person", store=store)
    assert store.stop_latched()["reason"] == "the person said stop"
    assert store.grant_revoked(grant_id) and report["grants_revoked"] >= 1
    final = store.objective_run(run_id)
    assert final["status"] == O.RUN_PAUSED and "stopped" in final["blocker"]
    assert run_id in report["paused"]
    stopped = [r for r in trust.audit().query(limit=20) if r["action"] == "friday.stop"]
    assert stopped and stopped[0]["decision"] == "STOPPED"


def test_nothing_new_starts_while_latched():
    """The process store is the one every capability consults."""
    S.emergency_stop("drill", actor="person")
    result = CR.CapabilityRuntime(engine=P.PolicyEngine(autonomy=P.DANGEROUS)).execute(
        "get_current_time", {})
    assert result.status == c.NOT_PERMITTED
    assert "STOPPED" in result.error


def test_a_latched_objective_is_parked_not_dispatched(store):
    from friday.continuous import ContinuousTaskExecutor

    run_id = _objective(store)
    store.stop_latch(reason="drill", actor="person", now=c.now_iso())
    called = []

    async def port(capability_id, arguments, *, run=None):
        called.append(capability_id)
        return c.succeeded(c.started(run.run_id, capability_id), "ran").to_dict()

    async def go():
        executor = ContinuousTaskExecutor(store, call_capability=port)
        try:
            for _ in range(100):
                if store.objective_run(run_id)["status"] in (O.RUN_WAITING_STATUSES
                                                             + O.RUN_TERMINAL):
                    return
                await executor.start(run_id)
                await asyncio.sleep(0.02)
        finally:
            executor.stop()

    asyncio.run(go())
    assert called == [], "a task was dispatched while STOP was latched"
    assert store.objective_run(run_id)["status"] == O.RUN_PAUSED


def _delegated(store) -> tuple[str, str]:
    """An objective whose one task has already handed its work to a worker."""
    run_id = O.compile_objective(
        store, request="build it",
        tasks=[{"capability": "hermes_delegate", "arguments": {}}],
        manifest=[{"id": "hermes_delegate", "description": "delegate"}],
        objective_summary="build it", provenance=c.PERSON, attended=True)["run_id"]
    task_id = f"{run_id}-t1"
    store.update_objective_task(task_id, status=O.TaskStatus.WAITING,
                                result={"work_run_id": "WR-LIVE-1"}, next_wake=c.now_iso())
    return run_id, task_id


def test_work_waiting_on_a_worker_is_parked_without_polling_it(store):
    """Slice 2 review: the worker poll ran before the latch was read, so a
    stopped objective kept polling a gateway STOP had just killed."""
    from friday.continuous import ContinuousTaskExecutor

    run_id, _ = _delegated(store)
    store.stop_latch(reason="drill", actor="person", now=c.now_iso())
    called = []

    async def port(capability_id, arguments, *, run=None):
        called.append(capability_id)
        return {"status": "working", "work_run_id": "WR-LIVE-1"}

    async def go():
        executor = ContinuousTaskExecutor(store, call_capability=port)
        try:
            for _ in range(50):
                if store.objective_run(run_id)["status"] in (O.RUN_WAITING_STATUSES
                                                             + O.RUN_TERMINAL):
                    return
                await executor.start(run_id)
                await asyncio.sleep(0.02)
        finally:
            executor.stop()

    asyncio.run(go())
    assert called == [], f"the worker was polled while STOP was latched: {called}"
    final = store.objective_run(run_id)
    assert final["status"] == O.RUN_PAUSED and "stopped" in final["blocker"]


def test_no_repair_runs_once_the_stop_is_latched(store):
    """STOP pressed while a poll is in flight: the gateway it just killed
    answers unreachable, and a repair would restart it - undoing the stop."""
    from friday import hermes_health as H
    from friday.continuous import ContinuousTaskExecutor

    run_id, task_id = _delegated(store)
    repairs = []

    async def port(capability_id, arguments, *, run=None):
        if capability_id == "hermes_status":
            store.stop_latch(reason="pressed mid-poll", actor="person", now=c.now_iso())
            return {"status": "not_configured", "error": "gateway connection refused"}
        return {"ok": True}

    def probe(active_workruns=()):
        return H.Report({name: False for name in H.LAYERS},
                        active_workruns=tuple(active_workruns))

    async def recover(report):
        repairs.append(report.recovery)
        return True

    async def go():
        executor = ContinuousTaskExecutor(store, call_capability=port,
                                          health_probe=probe, health_recover=recover)
        try:
            await executor.start(run_id)
            for _ in range(250):
                if "unreachable" in (store.objective_task(task_id).get("evidence") or ""):
                    return
                await asyncio.sleep(0.02)
        finally:
            executor.stop()

    asyncio.run(go())
    assert "unreachable" in (store.objective_task(task_id).get("evidence") or ""), (
        "the poll never came back, so this proves nothing")
    assert repairs == [], f"a repair ran after the stop was latched: {repairs}"


def test_a_worker_that_ignores_the_stop_is_killed_by_pid(store):
    """A real OS process that will not stop on its own: the stop does not
    ask it, it ends it."""
    worker = subprocess.Popen([sys.executable, "-c", "import time\nwhile True: time.sleep(0.1)"])
    try:
        S.own_process(worker.pid)
        report = S.emergency_stop("drill", actor="person", store=store)
        deadline = time.time() + 10
        while psutil.pid_exists(worker.pid) and worker.poll() is None and time.time() < deadline:
            time.sleep(0.1)
        assert worker.poll() is not None, "the worker outlived the stop"
        assert worker.pid in report["killed"]
    finally:
        if worker.poll() is None:
            worker.kill()


def test_every_process_runs_its_own_hooks_when_another_one_stops(tmp_path):
    """The latch is seen by a watcher in each process; the hooks here run
    even though the stop was pressed elsewhere."""
    path = tmp_path / "shared.sqlite3"
    here, elsewhere = Store(path), Store(path)
    ran = []
    try:
        S.register_hook("test-service", lambda reason: ran.append(reason))
        watcher = S.Watcher(store=lambda: here, interval=0.05)
        watcher.start()
        try:
            elsewhere.stop_latch(reason="pressed in the UI", actor="person", now=c.now_iso())
            deadline = time.time() + 5
            while not ran and time.time() < deadline:
                time.sleep(0.05)
        finally:
            watcher.stop()
        assert ran == ["pressed in the UI"]
    finally:
        here.close()
        elsewhere.close()


def test_resume_is_explicit_and_the_latch_survives_a_restart(tmp_path):
    path = tmp_path / "reboot.sqlite3"
    before = Store(path)
    S.emergency_stop("drill", actor="person", store=before)
    before.close()
    after = Store(path)                                    # a restart
    try:
        assert after.stop_latched() is not None, "a restart cleared the stop"
        assert S.resume(actor="person", store=after)
        assert after.stop_latched() is None
        resumed = [r for r in trust.audit().query(limit=20)
                   if r["action"] == "friday.stop" and r["decision"] == "RESUMED"]
        assert resumed
    finally:
        after.close()


@pytest.mark.parametrize("said,stops", [
    ("emergency stop", True), ("Friday, stop everything!", True),
    ("abort everything", True), ("stop the music", False),
    ("please don't stop everything you're doing to help", False),
])
def test_the_stop_phrase_is_narrow(said, stops):
    assert S.is_stop_phrase(said) is stops


# --- every door refuses, and every trigger stops without a model -----------

def test_every_conversational_tool_refuses_while_latched():
    """ownership.guard wraps every MCP tool the model can call."""
    from friday import ownership

    ran = []

    class FakeMCP:
        def tool(self, *args, **kwargs):
            return lambda fn: fn

    guarded = ownership.guard(FakeMCP())

    @guarded.tool()
    def power_shutdown() -> dict:
        ran.append(True)
        return {"status": "succeeded"}

    S.emergency_stop("drill", actor="person")
    answer = power_shutdown()
    assert ran == [] and answer["status"] == "not_permitted"
    assert "STOPPED" in answer["error"]


def test_the_text_brain_stops_without_asking_its_model(monkeypatch):
    from friday import voice_brain as V

    asked = []
    monkeypatch.setattr(V, "_model", lambda: asked.append(True))
    monkeypatch.setattr(V, "_remember_turn", lambda role, text: None)
    out = V.reply("Friday, emergency stop")
    assert asked == [], "the model was asked about STOP"
    assert out["action"] == "friday.stop" and S.latched() is not None
    assert V._run_capability("system", "get_info", {})["status"] == "not_permitted"
    assert V.reply("resume everything")["action"] == "friday.resume"
    assert S.latched() is None


def test_the_voice_turn_stops_before_the_model_and_silences_itself(monkeypatch):
    import agent_friday as A

    later, said, interrupted = [], [], []

    async def nothing(*args, **kwargs):
        later.append(True)

    monkeypatch.setattr(A, "research_first", nothing)
    monkeypatch.setattr(A, "check_what_he_asserted", nothing)
    class Session:
        async def say(self, text, **kwargs):
            said.append(text)

        def interrupt(self):
            interrupted.append(True)

    class Stub:
        """`session` is a read-only property on the real agent."""
        session = Session()

        def prepare_turn(self, turn_ctx, text):
            later.append(True)

    with pytest.raises(A.StopResponse):
        asyncio.run(A.FridayAgent.read_before_answering(Stub(), object(), "emergency stop"))
    assert S.latched() is not None
    assert interrupted and said and later == [], "the turn went on after STOP"


def test_the_terminal_stop_latches_before_it_ends_the_processes(monkeypatch):
    from friday import launcher

    ended = []
    monkeypatch.setattr(launcher, "stop_all", lambda grace=8.0: ended.append(S.latched()) or 0)
    assert launcher.emergency_stop_everything() == 0
    assert ended and ended[0] is not None, "the processes ended before the stop was latched"
    assert launcher.resume_everything() == 0 and S.latched() is None


def test_the_terminal_stop_ends_the_processes_even_when_the_store_cannot_latch(monkeypatch):
    from friday import launcher

    ended = []

    def broken(*args, **kwargs):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(S, "emergency_stop", broken)
    monkeypatch.setattr(launcher, "stop_all", lambda grace=8.0: ended.append(True) or 0)
    launcher.emergency_stop_everything()
    assert ended == [True]


def test_stop_ends_a_desktop_takeover_the_way_desktop_stop_does(store):
    from friday.toolsets import desktop as D

    D.ABORT.clear()
    D._PLANS["RUN-1"] = {"task": "open notepad", "steps": []}
    D._register_stop_hook()                    # reset_for_tests cleared it
    S.emergency_stop("drill", actor="person", store=store)
    assert D.ABORT.is_set() and D._PLANS == {}
    D.ABORT.clear()
