"""
Test D: routing and dispatch reachability.

Three layers must agree on the same seven names:

  * the capability router assigns every objective_* tool to the objectives
    group, and policy lets them through (AUTO, agent_runtime scope)
  * OT.capability_port() exposes the same names to the CLI and the engine
  * build_dispatch() (objective_cli) resolves those names - and the four
    demo capabilities - to working calls, raising LookupError for anything
    it does not know, and handling both sync and async toolset functions

A capability that the router knows but the dispatch cannot call, or vice
versa, would strand a run mid-graph; this file is the junction check.
"""

from __future__ import annotations

import json

import pytest

from friday import contracts as c
from friday.capability_router import group_of
from friday.objective_cli import build_dispatch
from friday.policy import default_engine
from friday.store import Store
from friday.toolsets import objectives as OT

OBJECTIVE_TOOLS = ("objective_start", "objective_status", "objective_list",
                   "objective_pause", "objective_resume", "objective_cancel",
                   "objective_history")


@pytest.fixture
def store() -> Store:
    s = Store(":memory:")
    OT.reset_store(s)
    yield s
    OT.reset_store(None)


# ---------------------------------------------------------------------------
# Router and policy
# ---------------------------------------------------------------------------


def test_router_assigns_all_objective_tools_to_the_objectives_group():
    for tool in OBJECTIVE_TOOLS:
        assert group_of(tool) == "objectives", tool


def test_objective_tools_are_auto_policy_and_agent_scoped():
    from friday import capabilities as cap
    for tool in OBJECTIVE_TOOLS:
        c = cap.by_id(tool)
        assert c.execution_scope == "agent_runtime", tool
        assert c.side_effect in ("write", "none"), tool
        # The policy speaks tool ids ("objectives.start"), not capability
        # ids ("objective_start").
        tool_id = "objectives." + tool[len("objective_"):]
        verdict = default_engine.decide(tool_id)
        assert verdict.allowed, tool


# ---------------------------------------------------------------------------
# The port
# ---------------------------------------------------------------------------


def test_port_exposes_the_same_seven_names(store):
    port = OT.capability_port()
    assert set(port) == set(OBJECTIVE_TOOLS)


def test_port_start_compiles_via_the_port(store):
    run = c.Run.create("port probe", capability="objective_start")
    result = OT.capability_port()["objective_start"](
        run, "check the system", engine=default_engine)
    assert result.status == c.SUCCEEDED
    assert result.output["run_id"].startswith("RUN-")
    assert result.output["task_count"] == 1


def test_port_pause_resume_cancel_are_reachable(store):
    run = c.Run.create("port probe", capability="objective_start")
    port = OT.capability_port()
    run_id = port["objective_start"](
        run, "check the system", engine=default_engine).output["run_id"]

    paused = port["objective_pause"](run, run_id, engine=default_engine)
    assert paused.status == c.SUCCEEDED
    resumed = port["objective_resume"](run, run_id, engine=default_engine)
    assert resumed.status == c.SUCCEEDED
    cancelled = port["objective_cancel"](run, run_id, engine=default_engine)
    assert cancelled.status == c.SUCCEEDED
    assert port["objective_status"](run, run_id,
                                    engine=default_engine).status == c.SUCCEEDED
    assert port["objective_history"](run, run_id,
                                     engine=default_engine).status == c.SUCCEEDED
    listed = port["objective_list"](run, engine=default_engine)
    assert listed.status == c.SUCCEEDED
    assert listed.output[0]["run_id"] == run_id


# ---------------------------------------------------------------------------
# Engine dispatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_routes_known_capabilities(store):
    dispatch = build_dispatch()
    result = await dispatch("objective_start",
                            {"objective": "check the system"})
    assert result["status"] == "succeeded"
    assert result["output"]["task_count"] == 1

    status = await dispatch("objective_status", {})
    assert status["status"] == "succeeded"


@pytest.mark.asyncio
async def test_dispatch_handles_async_toolset_functions(store, monkeypatch):
    import friday.toolsets.web as web

    async def stub(run, *, query: str):
        started = c.started(run.run_id, "web.search")
        return run.record(c.succeeded(
            started, output={"ok": True, "headlines": ["stubbed"]},
            verification=c.Verification(method="stub", evidence="stubbed")))

    monkeypatch.setattr(web, "web_search", stub)
    dispatch = build_dispatch()
    result = await dispatch("web_search", {"query": "stub query"})
    assert result["status"] == "succeeded"
    assert result["output"]["headlines"] == ["stubbed"]


@pytest.mark.asyncio
async def test_dispatch_handles_sync_toolset_functions(store):
    dispatch = build_dispatch()
    result = await dispatch("system_get_info", {})
    assert result["status"] == "succeeded"
    assert result["output"]["cpu_logical_cores"] >= 1


@pytest.mark.asyncio
async def test_dispatch_unknown_capability_raises_lookup_error(store):
    dispatch = build_dispatch()
    with pytest.raises(LookupError):
        await dispatch("ghost_capability", {})


@pytest.mark.asyncio
async def test_dispatch_output_is_json_serialisable(store):
    dispatch = build_dispatch()
    result = await dispatch("objective_list", {})
    json.dumps(result)  # the MCP adapter serialises this


# ---------------------------------------------------------------------------
# The --port-* CLI flags (in-process plumbing the spawn tests rely on)
# ---------------------------------------------------------------------------


def test_port_flag_runners_work(store, capsys):
    from friday.objective_cli import _port
    _port("--port-start")
    _port("--port-status")
    _port("--port-pause")
    _port("--port-resume")
    _port("--port-cancel")
    out = capsys.readouterr().out
    assert "check the system" in out
    assert '"status": "succeeded"' in out