"""
PRO-001 acceptance: an idle system has no unnecessary inference loop.

Runs FRIDAY's real background loops - the continuous executor's driver and
its watchdog, the voice continuity scheduler (with its heartbeat), the
autolearn worker, the stop watcher, the UI health refresher, and the voice
room's 5-second delivery poller (its three drain passes) - through
idle ticks over an empty store, with every model entry point and the network
booby-trapped. Each loop is also proved to have really ticked, so a loop that
never ran cannot pass for an idle one.
"""

from __future__ import annotations

import asyncio
import socket
import time
import types
from datetime import datetime, timezone

import pytest

from friday import continuity as C
from friday import continuous as CT
from friday import ui_server as U
import agent_friday as af
from friday.autolearn import AutoLearner
from friday.continuity_livekit import LiveKitContinuity
from friday.stop import Watcher
from friday.store import Store
from friday.toolsets import objectives

NOW = datetime(2026, 10, 10, tzinfo=timezone.utc)


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "idle.db")
    yield s
    s.close()


@pytest.fixture
def calls(monkeypatch):
    """Every way FRIDAY reaches a model; the list records any that fired."""
    from friday import model_gateway, providers

    seen: list[str] = []

    def trap(name):
        def _fn(*a, **k):
            seen.append(name)
            raise AssertionError(f"idle system called {name}")
        return _fn

    for fn in ("build_llm", "build_resilient_llm", "build_stt", "build_tts"):
        monkeypatch.setattr(providers, fn, trap(f"providers.{fn}"))
    monkeypatch.setattr(model_gateway, "gateway", trap("model_gateway.gateway"))
    monkeypatch.setattr(model_gateway.ModelGateway, "__init__", trap("ModelGateway()"))
    monkeypatch.setattr(model_gateway.ModelGateway, "infer", trap("ModelGateway.infer"))
    return seen


def _counted(obj, name, ticks):
    original = getattr(obj, name)

    async def wrapper(*a, **k):
        ticks.append(name)
        return await original(*a, **k)

    setattr(obj, name, wrapper)


def _refuse(message):
    def _fn(*a, **k):
        raise AssertionError(message)
    return _fn


def _block_network(monkeypatch):
    # Called after the event loop exists: its own self-pipe is a connected
    # socket pair, made at loop creation.
    monkeypatch.setattr(socket, "create_connection", _refuse("idle system opened a connection"))
    monkeypatch.setattr(socket.socket, "connect", _refuse("idle system opened a socket"))


def test_the_executor_driver_and_the_watchdog_are_idle_without_work(store, calls, monkeypatch):
    port_calls = []

    async def port(*a, **k):
        port_calls.append((a, k))
        raise AssertionError("idle executor ran a capability")

    async def scenario():
        _block_network(monkeypatch)
        ticks: list[str] = []
        executor = CT.ContinuousTaskExecutor(store, port, executor_id="idle")
        _counted(executor, "_driver_tick", ticks)
        watchdog = CT.RunWatchdog(executor, interval=0.01)
        _counted(watchdog, "sweep_once", ticks)
        task = watchdog.start()
        await asyncio.sleep(0.4)
        task.cancel()
        executor.stop()
        await asyncio.gather(task, executor._driver_task, return_exceptions=True)
        return ticks

    ticks = asyncio.run(scenario())
    assert ticks.count("_driver_tick") >= 3 and ticks.count("sweep_once") >= 3
    assert port_calls == [] and calls == []


def test_the_voice_continuity_scheduler_and_heartbeat_are_idle(store, calls, monkeypatch):
    class Session:
        def __init__(self):
            self.replies = []

        def generate_reply(self, **kwargs):
            self.replies.append(kwargs)
            raise AssertionError("idle scheduler asked the model for a reply")

    async def scenario():
        _block_network(monkeypatch)
        manager = C.ContinuityManager(store, clock=lambda: NOW)
        adapter = LiveKitContinuity(manager, worker_id="voice-idle")
        adapter.session = Session()
        pumps: list[str] = []
        _counted(adapter, "pump_once", pumps)
        beats: list[int] = []
        real_tick = adapter.telemetry.heartbeat_tick

        def tick(**kwargs):
            beats.append(1)
            return real_tick(**kwargs)

        adapter.telemetry.heartbeat_tick = tick
        adapter.start(poll_seconds=0.01)
        await asyncio.sleep(0.7)
        await adapter.aclose()
        return adapter.session.replies, pumps, beats

    replies, pumps, beats = asyncio.run(scenario())
    assert len(pumps) >= 5
    assert len(beats) >= 1, "the heartbeat never ticked"
    assert replies == [] and calls == []


def test_the_autolearn_worker_does_nothing_but_wait_when_no_turn_arrives(calls, monkeypatch):
    invoked: list[str] = []

    async def invoke(name, arguments):
        invoked.append(name)
        return {"status": "succeeded", "brief": ""}

    async def scenario():
        _block_network(monkeypatch)
        learner = AutoLearner(invoke)
        await learner.start()
        await asyncio.sleep(0.3)
        await learner.aclose()
        return learner.learned

    assert asyncio.run(scenario()) == 0
    assert invoked == ["profile_get"], "only the one briefing read at start, no learning"
    assert calls == []


def test_the_stop_watcher_polls_the_latch_and_nothing_else(store, calls, monkeypatch):
    _block_network(monkeypatch)
    polls: list[int] = []
    real = store.stop_latched

    def latched():
        polls.append(1)
        return real()

    monkeypatch.setattr(store, "stop_latched", latched)
    watcher = Watcher(store=lambda: store, interval=0.01).start()
    time.sleep(0.3)
    watcher.stop()
    assert len(polls) >= 3
    assert calls == []


class _StopLoop(BaseException):
    """Ends the refresher after one cycle; BaseException so its own
    `except Exception` guards cannot swallow it."""


def _stop_sleeping(_seconds):
    raise _StopLoop


def test_the_ui_health_refresher_scans_but_infers_nothing(store, calls, monkeypatch):
    objectives.reset_store(store)
    try:
        monkeypatch.setattr(U, "_compute_mem_snapshot", lambda: {"at": "x"})
        monkeypatch.setattr(U, "_probe_gbrain", lambda: {"status": "unavailable"})
        monkeypatch.setattr(U, "_probe_hermes", lambda: {"status": "unavailable"})
        monkeypatch.setitem(U._PROACTIVE_CACHE, "at", 0.0)
        monkeypatch.setitem(U._PROACTIVE_CACHE, "value", None)
        # The refresher also writes these; restore them so the stub snapshot and probes
        # cannot leak into a later test that reads the cache (test_ui_server's snapshot test).
        for cache in (U._MEM_CACHE, U._GBRAIN_CACHE, U._HERMES_CACHE):
            for key in list(cache):
                monkeypatch.setitem(cache, key, cache[key])
        # Replace the module's `time` with a copy whose sleep ends the loop,
        # so the process-wide time.sleep other threads use is untouched.
        shim = types.SimpleNamespace(**{k: getattr(time, k) for k in dir(time)})
        shim.sleep = _stop_sleeping
        monkeypatch.setattr(U, "time", shim)
        _block_network(monkeypatch)
        with pytest.raises(_StopLoop):
            U._mem_refresher()
        assert U._PROACTIVE_CACHE["value"] == 0       # the scan ran, found nothing
        assert calls == []
    finally:
        objectives.reset_store(None)


class _CountingLog:
    """A real WorkRunLog on an empty db; counts the sweep the digest gather runs."""

    def __init__(self, log):
        self._log = log
        self.sweeps = 0

    def sweep_orphans(self):
        self.sweeps += 1
        return self._log.sweep_orphans()

    def __getattr__(self, name):
        return getattr(self._log, name)


class _IdleSupervisor:
    """What `hermes_control.supervisor()` hands the poller, with no runs."""

    def __init__(self, log):
        self.log = log
        self.expiry_sweeps = 0

    def expire_overdue(self):
        self.expiry_sweeps += 1
        return []

    def progress(self, work_run_id):
        raise AssertionError("idle supervisor has no run to report on")


class _SilentSession:
    """Any speech or model reply from an idle poller fails the test."""

    user_state = "listening"
    current_speech = None

    def __init__(self):
        self.attempts = []

    async def say(self, text, allow_interruptions=True, add_to_chat_ctx=None):
        self.attempts.append(("say", text))
        raise AssertionError(f"idle poller spoke: {text!r}")

    def generate_reply(self, **kwargs):
        self.attempts.append(("generate_reply", kwargs))
        raise AssertionError("idle poller asked the model for a reply")


def test_the_voice_room_delivery_poller_is_idle_without_runs(store, tmp_path, calls, monkeypatch):
    """agent_friday.deliver_hermes_completions runs these three passes every
    5 s for the life of a room: Hermes completions, finished objectives and
    progress narration. It is a closure inside entrypoint, so the passes it
    calls are driven here in its order with its production defaults (the
    supervisor comes from hermes_control, as in the loop)."""
    from friday import hermes_bridge as hb
    from friday.tools import hermes_control

    log = _CountingLog(hb.WorkRunLog(tmp_path / "work_runs.db"))
    sup = _IdleSupervisor(log)
    monkeypatch.setattr(hermes_control, "_supervisor", sup)
    session = _SilentSession()
    state: dict = {}

    async def scenario():
        _block_network(monkeypatch)
        results = []
        for _ in range(4):
            results.append((
                await af.drain_hermes_deliveries(session, log),
                await af.drain_objective_deliveries(session, store),
                await af.speak_progress_digests(session, state=state),
            ))
        return results

    results = asyncio.run(scenario())
    assert len(results) == 4
    assert all(r[0] == 0 and r[1] == 0 for r in results)
    assert all(r[2] == {"spoken": 0, "milestones": 0, "digest": False} for r in results)
    # The narration pass really gathered each time (not skipped as a no-op).
    assert log.sweeps == 4 and sup.expiry_sweeps == 4
    assert session.attempts == [] and calls == []
