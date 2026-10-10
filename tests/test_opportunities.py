"""
PRO-001: anomaly detectors feed an Opportunities Inbox, and an idle system
makes no model call.

The detectors are deterministic code over rows the automation engine already
writes. The claim that matters is negative - nothing here ever asks a model
anything - so it is proved three ways: the module imports no model or network
client, every model entry point is booby-trapped while a simulated week of
ticks runs, and the sockets are shut for the same period.
"""

from __future__ import annotations

import ast
import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from friday import contracts as c
from friday import opportunities as O
from friday import workspace as W
from friday.store import Store

T0 = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "test.db")
    s.save_automation("nightly", trigger={"kind": "manual"}, steps=[])
    yield s
    s.close()


def _run(store, name, run_id, status, started):
    """One automation_runs row, written the way the engine writes it."""
    store.start_automation_run(run_id, name, "test")
    store._conn.execute("UPDATE automation_runs SET started_at=? WHERE run_id=?",
                        (started.isoformat(), run_id))
    if status != "running":
        store.finish_automation_run(run_id, status=status, steps=[], error=None)


def test_a_streak_of_failures_raises_one_opportunity(store):
    for i in range(3):
        _run(store, "nightly", f"R{i}", c.FAILED, T0 - timedelta(hours=3 - i))
    got = O.scan(store, workspace_id="default", now=T0)
    assert [(o["kind"], o["subject"]) for o in got] == [("automation_failing", "nightly")]
    assert store.opportunities("default")[0]["detail"]["run_ids"] == ["R2", "R1", "R0"]


def test_two_failures_is_not_yet_a_pattern(store):
    for i in range(2):
        _run(store, "nightly", f"R{i}", c.FAILED, T0 - timedelta(hours=2 - i))
    assert O.scan(store, workspace_id="default", now=T0) == []


def test_a_success_breaks_the_streak(store):
    _run(store, "nightly", "R0", c.FAILED, T0 - timedelta(hours=4))
    _run(store, "nightly", "R1", c.FAILED, T0 - timedelta(hours=3))
    _run(store, "nightly", "R2", c.SUCCEEDED, T0 - timedelta(hours=2))
    _run(store, "nightly", "R3", c.FAILED, T0 - timedelta(hours=1))
    assert O.scan(store, workspace_id="default", now=T0) == []


def test_rescanning_the_same_facts_adds_nothing(store):
    for i in range(3):
        _run(store, "nightly", f"R{i}", c.FAILED, T0 - timedelta(hours=3 - i))
    assert len(O.scan(store, workspace_id="default", now=T0)) == 1
    assert O.scan(store, workspace_id="default", now=T0 + timedelta(minutes=5)) == []
    # a longer streak of the same failure is the same opportunity, not a new one
    _run(store, "nightly", "R3", c.FAILED, T0 + timedelta(hours=1))
    assert O.scan(store, workspace_id="default", now=T0 + timedelta(hours=2)) == []
    assert len(store.opportunities("default")) == 1


def test_a_dismissed_opportunity_stays_dismissed_until_a_new_streak(store):
    for i in range(3):
        _run(store, "nightly", f"R{i}", c.FAILED, T0 - timedelta(hours=3 - i))
    (opp,) = O.scan(store, workspace_id="default", now=T0)
    assert store.opportunity_resolve("default", opp["id"], "dismissed")
    assert store.opportunities("default") == []
    assert O.scan(store, workspace_id="default", now=T0 + timedelta(hours=1)) == []
    # the automation recovers, then fails three more times: a new streak
    _run(store, "nightly", "S0", c.SUCCEEDED, T0 + timedelta(hours=2))
    for i in range(3):
        _run(store, "nightly", f"S{i + 1}", c.FAILED, T0 + timedelta(hours=3 + i))
    assert len(O.scan(store, workspace_id="default", now=T0 + timedelta(hours=7))) == 1


def test_a_run_stuck_running_raises_an_opportunity_only_after_the_window(store):
    _run(store, "nightly", "R0", "running", T0 - timedelta(minutes=10))
    assert O.scan(store, workspace_id="default", now=T0) == []
    got = O.scan(store, workspace_id="default", now=T0 + timedelta(hours=2))
    assert [(o["kind"], o["subject"]) for o in got] == [("automation_stalled", "nightly")]


def test_the_inbox_is_private_to_its_workspace(store):
    for i in range(3):
        _run(store, "nightly", f"R{i}", c.FAILED, T0 - timedelta(hours=3 - i))
    O.scan(store, workspace_id="brand-a", now=T0)
    assert len(store.opportunities("brand-a")) == 1
    assert store.opportunities("brand-b") == []
    assert not store.opportunity_resolve("brand-b", store.opportunities("brand-a")[0]["id"],
                                         "dismissed")


def test_the_table_is_declared_private_state():
    assert "opportunities" in W.SCOPED


def test_an_unknown_resolution_is_refused(store):
    with pytest.raises(ValueError):
        store.opportunity_resolve("default", 1, "maybe")


# --- the acceptance: an idle system makes zero model calls -------------------

def _booby_trap(monkeypatch):
    """Every way FRIDAY reaches a model, and the network under all of them."""
    from friday import model_gateway, providers

    calls: list[str] = []

    def trap(name):
        def _fn(*a, **k):
            calls.append(name)
            raise AssertionError(f"idle system called {name}")
        return _fn

    monkeypatch.setattr(providers, "build_llm", trap("providers.build_llm"))
    monkeypatch.setattr(providers, "build_resilient_llm",
                        trap("providers.build_resilient_llm"))
    monkeypatch.setattr(model_gateway, "gateway", trap("model_gateway.gateway"))
    monkeypatch.setattr(model_gateway.ModelGateway, "__init__",
                        trap("ModelGateway()"))
    monkeypatch.setattr(socket, "create_connection", trap("socket.create_connection"))
    monkeypatch.setattr(socket.socket, "connect", trap("socket.connect"))
    return calls


def test_an_idle_system_makes_no_model_call_over_a_simulated_week(store, monkeypatch):
    for i in range(5):
        _run(store, "nightly", f"OK{i}", c.SUCCEEDED, T0 - timedelta(days=5 - i))
    calls = _booby_trap(monkeypatch)
    ticks = 0
    for minute in range(0, 7 * 24 * 60, 15):
        O.scan(store, workspace_id="default", now=T0 + timedelta(minutes=minute))
        ticks += 1
    assert ticks == 672
    assert calls == []
    assert store.opportunities("default") == []


def test_finding_an_anomaly_makes_no_model_call_either(store, monkeypatch):
    for i in range(3):
        _run(store, "nightly", f"R{i}", c.FAILED, T0 - timedelta(hours=3 - i))
    _run(store, "nightly", "H0", "running", T0 - timedelta(hours=5))
    calls = _booby_trap(monkeypatch)
    got = O.scan(store, workspace_id="default", now=T0)
    assert {o["kind"] for o in got} == {"automation_failing", "automation_stalled"}
    assert calls == []


def test_the_detector_module_imports_no_model_or_network_client():
    tree = ast.parse(Path(O.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
            imported |= {a.name for a in node.names}
    forbidden = {"providers", "model_gateway", "gateway_llm", "llm_roles",
                 "cli_model_provider", "tool_models", "planner_model",
                 "anthropic", "openai", "requests", "httpx", "aiohttp",
                 "urllib", "socket", "livekit"}
    assert imported & forbidden == set()
