"""
PRO-001: the UI process's existing background loop (the one that already
warms the memory snapshot and the GBrain/Hermes probes) calls the proactive
scan on a bounded interval, and an idle tick never reaches a model.
"""

from __future__ import annotations

import socket
import time

import pytest

from friday import contracts as c
from friday import opportunities
from friday import ui_server as U
from friday.store import Store
from friday.toolsets import objectives


class _StopLoop(BaseException):
    """Ends the refresher after one cycle; BaseException so its own
    `except Exception` guards cannot swallow it."""


@pytest.fixture
def store(tmp_path, monkeypatch):
    s = Store(tmp_path / "test.db")
    s.save_automation("nightly", trigger={"kind": "manual"}, steps=[])
    objectives.reset_store(s)
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

    def stop(_seconds):
        raise _StopLoop

    monkeypatch.setattr(U.time, "sleep", stop)
    yield s
    objectives.reset_store(None)


def _cycle():
    with pytest.raises(_StopLoop):
        U._mem_refresher()


def _fail(store, n):
    for i in range(n):
        store.start_automation_run(f"R{i}", "nightly", "test")
        store.finish_automation_run(f"R{i}", status=c.FAILED, steps=[], error="boom")


def test_the_background_loop_scans_for_opportunities(store):
    _fail(store, 3)
    _cycle()
    assert [o["kind"] for o in store.opportunities("default")] == ["automation_failing"]


def test_the_scan_is_bounded_by_an_interval_not_run_every_cycle(store, monkeypatch):
    seen = []
    monkeypatch.setattr(opportunities, "scan", lambda s: seen.append(s) or [])
    _cycle()
    assert len(seen) == 1
    _cycle()                      # a second cycle inside the interval
    assert len(seen) == 1
    monkeypatch.setitem(U._PROACTIVE_CACHE, "at", time.time() - U._PROACTIVE_TTL - 1)
    _cycle()
    assert len(seen) == 2
    assert U._PROACTIVE_TTL >= 60


def test_a_failing_scan_does_not_stop_the_loop(store, monkeypatch):
    def boom(_s):
        raise RuntimeError("detector broke")

    monkeypatch.setattr(opportunities, "scan", boom)
    _cycle()                      # reaches time.sleep, i.e. survived the scan


def test_an_idle_tick_never_reaches_a_model(store, monkeypatch):
    from friday import model_gateway, providers

    calls = []

    def trap(name):
        def _fn(*a, **k):
            calls.append(name)
            raise AssertionError(f"idle tick called {name}")
        return _fn

    monkeypatch.setattr(providers, "build_llm", trap("providers.build_llm"))
    monkeypatch.setattr(providers, "build_resilient_llm", trap("build_resilient_llm"))
    monkeypatch.setattr(model_gateway, "gateway", trap("model_gateway.gateway"))
    monkeypatch.setattr(model_gateway.ModelGateway, "__init__", trap("ModelGateway()"))
    monkeypatch.setattr(socket, "create_connection", trap("socket.create_connection"))
    monkeypatch.setattr(socket.socket, "connect", trap("socket.connect"))
    _fail(store, 3)
    _cycle()
    assert calls == []
    assert len(store.opportunities("default")) == 1
