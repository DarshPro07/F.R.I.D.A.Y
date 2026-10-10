"""
PRO-001: an automation failure is an event that runs the proactive scan
directly - deterministic code, no model, no waiting for the 5-minute tick.
"""

from __future__ import annotations

import asyncio
import json
import socket

import pytest

from friday import contracts as c
from friday import opportunities
from friday.store import Store
from friday.toolsets import automations as A


@pytest.fixture
def store(tmp_path, monkeypatch):
    fresh = Store(tmp_path / "test.db")
    A.reset_store(fresh)
    outcomes = {"web.search": "fail"}

    def make(name):
        def fake(run, *, engine=None, **args):
            result = c.started(run.run_id, name)
            if outcomes.get(name) == "fail":
                return c.failed(result, f"{name} refused")
            return c.succeeded(result, output={}, verification=c.Verification(
                method="fake", evidence=f"{name} ran"))
        return fake

    monkeypatch.setattr(A, "TOOLS", {n: make(n) for n in ("web.search", "web.news")})
    A.automations_create(
        c.Run.create("define", capability="automation"), "nightly",
        json.dumps({"kind": "manual"}),
        json.dumps([{"id": "a", "tool": "web.search", "args": {}}]))
    fresh.outcomes = outcomes
    yield fresh
    A.reset_store(None)
    fresh.close()


def _run():
    return asyncio.run(A.execute("nightly"))


def test_the_third_failed_run_raises_the_opportunity_without_a_tick(store):
    for _ in range(2):
        assert _run()["status"] == c.FAILED
    assert store.opportunities("default") == []          # one streak of two: not yet
    assert _run()["status"] == c.FAILED
    assert [o["kind"] for o in store.opportunities("default")] == ["automation_failing"]


def test_a_successful_run_does_not_scan(store, monkeypatch):
    seen = []
    monkeypatch.setattr(opportunities, "scan", lambda s: seen.append(s) or [])
    store.outcomes.clear()
    assert _run()["status"] == c.SUCCEEDED
    assert seen == []


def test_a_failed_run_scans_exactly_once_on_the_store_it_recorded_in(store, monkeypatch):
    seen = []
    monkeypatch.setattr(opportunities, "scan", lambda s: seen.append(s) or [])
    assert _run()["status"] == c.FAILED
    assert seen == [store]


def test_a_broken_scan_is_logged_and_the_run_record_survives(store, monkeypatch, caplog):
    def boom(_s):
        raise RuntimeError("detector broke")

    monkeypatch.setattr(opportunities, "scan", boom)
    with caplog.at_level("ERROR"):
        record = _run()
    assert record["status"] == c.FAILED
    assert store.automation_history("nightly", limit=1)[0]["status"] == c.FAILED
    assert "detector broke" in caplog.text


def test_the_event_path_reaches_no_model_and_no_network(store, monkeypatch):
    from friday import model_gateway, providers

    calls = []

    def trap(name):
        def _fn(*a, **k):
            calls.append(name)
            raise AssertionError(f"failure event called {name}")
        return _fn

    monkeypatch.setattr(providers, "build_llm", trap("providers.build_llm"))
    monkeypatch.setattr(providers, "build_resilient_llm", trap("build_resilient_llm"))
    monkeypatch.setattr(model_gateway, "gateway", trap("model_gateway.gateway"))
    monkeypatch.setattr(model_gateway.ModelGateway, "__init__", trap("ModelGateway()"))
    monkeypatch.setattr(socket, "create_connection", trap("socket.create_connection"))
    for _ in range(3):
        _run()
    assert calls == []
    assert len(store.opportunities("default")) == 1
