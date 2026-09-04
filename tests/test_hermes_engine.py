"""Hermes as the single execution engine: shared memory, token-aware depth,
and the router preferring it over the coding-CLI fallbacks."""
from __future__ import annotations

import asyncio

import pytest

from friday import execution_economics as ee
from friday import executor_router as R
from friday import hermes_bridge as hb


# --- shared memory reaches the sub-agent ------------------------------------

def test_bundle_with_memory_pulls_from_the_one_memory_stack(monkeypatch):
    from friday import memory_stack
    seen = {}

    def fake_aggregate(task, budget_tokens=None, include_episodes=True):
        seen["task"], seen["budget"] = task, budget_tokens
        seen["episodes"] = include_episodes
        return {"prompt": "YOUR PREFERENCES AND RULES:\n- tabs: spaces"}

    monkeypatch.setattr(memory_stack, "aggregate", fake_aggregate)
    b = hb.TaskBundle(goal="refactor the parser").with_memory()
    # A worker gets rules/specs/relations, never the spoken transcript.
    assert seen == {"task": "refactor the parser", "budget": 600, "episodes": False}
    assert "tabs: spaces" in b.memory_context
    assert "WHAT FRIDAY ALREADY KNOWS" in b.render()
    assert "tabs: spaces" in b.render()


def test_memory_that_cannot_be_read_costs_nothing(monkeypatch):
    from friday import memory_stack
    monkeypatch.setattr(memory_stack, "aggregate",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db")))
    b = hb.TaskBundle(goal="x").with_memory()
    assert b.memory_context == ""
    assert "WHAT FRIDAY ALREADY KNOWS" not in b.render()


def test_memory_is_bounded_not_the_store(monkeypatch):
    from friday import memory_stack
    monkeypatch.setattr(memory_stack, "aggregate",
                        lambda task, budget_tokens=None, **kw: {"prompt": "x" * 20000})
    b = hb.TaskBundle(goal="x").with_memory()
    assert b.measure()["oversized"]  # reported, so the caller can see it


def test_delegate_sends_effort_and_memory_to_the_gateway(tmp_path, monkeypatch):
    from friday import memory_stack
    monkeypatch.setattr(memory_stack, "aggregate",
                        lambda task, budget_tokens=None, **kw: {"prompt": "- owner likes brevity"})
    calls = []

    class Sup(hb.HermesSupervisor):
        def start(self):
            self.state = hb.SESSION_READY

        def request(self, method, params, *, timeout=30.0):
            calls.append((method, params))
            if method == "session.create":
                return {"session_id": "s1", "info": {"model": "m"}}
            return {}

    sup = Sup(log=hb.WorkRunLog(tmp_path / "w.sqlite3"))
    sup.state = hb.DISCONNECTED
    sup.delegate(hb.TaskBundle(goal="rename foo"), reasoning_effort="low")
    create = dict(calls[0][1])
    assert calls[0][0] == "session.create"
    assert create["reasoning_effort"] == "low"
    submit = dict(calls[1][1])
    assert "owner likes brevity" in submit["text"]


# --- token-aware depth ------------------------------------------------------

def test_plan_delegation_maps_tier_to_effort():
    tiny = ee.plan_delegation("rename the variable foo to bar in utils.py")
    deep = ee.plan_delegation(
        "design the architecture for a memory-sharing layer between "
        "Friday and Hermes sub-agents, evaluate trade-offs")
    assert tiny["effort"] == "low" and tiny["tier"] == ee.TIER_ECONOMY
    assert deep["effort"] == "high" and deep["tier"] == ee.TIER_DEEP
    assert "effort=" in tiny["reason"]


def test_caller_pins_win_and_are_recorded():
    p = ee.plan_delegation("anything", model="claude-sonnet-5", effort="xhigh")
    assert p["model"] == "claude-sonnet-5" and p["effort"] == "xhigh"
    assert "pinned by caller" in p["reason"]


def test_every_tier_has_a_valid_hermes_effort():
    valid = {"none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"}
    for tier in (ee.TIER_ECONOMY, ee.TIER_STANDARD, ee.TIER_DEEP):
        assert ee.resolve_effort(tier) in valid


# --- the router: Hermes is the engine, Claude the fallback -------------------

def test_hermes_is_the_default_and_claude_the_fallback(monkeypatch):
    monkeypatch.setattr(R.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr("friday.executors.hermes._hermes_python",
                        lambda: "D:/hermes/venv/python.exe")
    monkeypatch.setattr("friday.executors.cli.claude_path", lambda: "/x/claude")
    choice = R.choose("build")
    assert choice.executor == "hermes"
    assert "claude" in choice.alternatives


def test_without_hermes_claude_is_chosen_and_labelled(monkeypatch):
    monkeypatch.setattr(R.shutil, "which", lambda name: None)
    monkeypatch.setattr("friday.executors.hermes._hermes_python", lambda: None)
    monkeypatch.setattr("friday.executors.cli.claude_path", lambda: "/x/claude")
    choice = R.choose("build")
    assert choice.executor == "claude"


def test_build_constructs_the_hermes_executor(monkeypatch):
    monkeypatch.setattr("friday.executors.hermes._hermes_python", lambda: "py")
    from friday.executors.hermes import HermesExecutor
    assert isinstance(R.build("hermes", store=None), HermesExecutor)


# --- the executor contract --------------------------------------------------

def test_hermes_executor_returns_an_honest_envelope(tmp_path):
    from friday.executors.claude_code import TaskBundle as DevBundle
    from friday.executors.hermes import HermesExecutor

    class FakeSup:
        def delegate(self, bundle, **kw):
            assert kw["reasoning_effort"]
            assert kw["workspace"] == str(tmp_path)
            return {"work_run_id": "hermes-1",
                    "result": {"status": hb.COMPLETE, "result": "done",
                               "model": "claude-opus-5"}}

    ex = HermesExecutor(None, supervisor=FakeSup())
    res = asyncio.run(ex.execute(DevBundle(goal="add tests", workspace=str(tmp_path),
                                           acceptance=("tests pass",))))
    assert res.status == "succeeded"
    assert res.output["work_run_id"] == "hermes-1"
    assert "not this claim" in res.verification.evidence


def test_hermes_executor_reports_unavailable_not_a_crash(tmp_path):
    from friday.executors.claude_code import TaskBundle as DevBundle
    from friday.executors.hermes import HermesExecutor

    class Dead:
        def delegate(self, *a, **k):
            raise hb.HermesUnavailable("no gateway")

    res = asyncio.run(HermesExecutor(None, supervisor=Dead()).execute(
        DevBundle(goal="x", workspace=str(tmp_path))))
    assert res.status == "failed" and "unavailable" in res.error
