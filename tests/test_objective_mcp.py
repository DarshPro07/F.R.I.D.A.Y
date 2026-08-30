"""
Test E: the MCP surface of the objectives control plane.

The adapter in friday/tools/objective_control.py is what the model actually
touches: the seven tools it registers must be the same seven names the
router, the port and the CLI know, and every control-plane operation must
round-trip through them. The CLI flags are the operator's version of the
same surface, so they are exercised here too - in a real subprocess, because
an operator flag that only works in-process is a demo, not a flag.
"""
from __future__ import annotations
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
import pytest
from mcp.server.fastmcp import FastMCP
from friday import contracts as c
from friday.store import Store
from friday.tools import objective_control
from friday.toolsets import objectives as OT
ROOT = Path(__file__).resolve().parent.parent
PYTHON = ROOT / '.venv' / 'Scripts' / 'python.exe'
PYTHON = str(PYTHON if PYTHON.exists() else sys.executable)
TOOLS = ('objective_start', 'objective_status', 'objective_list', 'objective_pause', 'objective_resume', 'objective_cancel', 'objective_history')


@pytest.fixture
def server(tmp_path):
    OT.reset_store(Store(tmp_path / "objectives.sqlite3"))
    mcp = FastMCP(name="test")
    objective_control.register(mcp)
    yield mcp
    OT.reset_store(None)


def call(server, name, **arguments):
    result = asyncio.run(server.call_tool(name, arguments))
    if isinstance(result, dict):
        return result
    if isinstance(result, tuple):
        return result[1] if isinstance(result[1], dict) else result[0]
    if isinstance(result, list):
        text = "".join(getattr(b, "text", "") or "" for b in result)
        try:
            return json.loads(text)
        except ValueError:
            return text
    return result


def test_register_exposes_the_seven_tools(server):
    tools = {t.name for t in asyncio.run(server.list_tools())}
    for tool in TOOLS:
        assert tool in tools, tool


def test_mcp_start_compiles_the_demo_objective(server):
    result = call(server, 'objective_start', objective='check whether this computer looks healthy, open Paint, find me one current technology story, create and clean up a temporary note, tell me when the whole job is finished')
    assert result['status'] == 'succeeded'
    out = result['output']
    assert out['status'] == 'RUNNING'
    assert out['task_count'] == 5
    caps = [t['capability'] for t in out['tasks']]
    assert caps == ['system_resource_usage', 'apps_open', 'web_search', 'files_create', 'files_recycle']


def test_mcp_start_refuses_a_second_active_run(server):
    first = call(server, "objective_start", objective="check the system")
    second = call(server, "objective_start", objective="check the system")
    assert second["status"] == "failed"
    assert "already active" in second["error"]

    replaced = call(server, "objective_start",
                    objective="check the system", replace=True)
    assert replaced["status"] == "succeeded"
    assert replaced["output"]["run_id"] != first["output"]["run_id"]


def test_mcp_pause_resume_cancel_round_trip(server):
    started = call(server, "objective_start", objective="check the system")
    run_id = started["output"]["run_id"]

    paused = call(server, "objective_pause", run_id=run_id,
                  reason="test pause")
    assert paused["status"] == "succeeded"
    assert paused["output"]["status"] == "PAUSED"

    resumed = call(server, "objective_resume", run_id=run_id,
                   reason="test resume")
    assert resumed["status"] == "succeeded"
    assert resumed["output"]["status"] == "RUNNING"

    status = call(server, "objective_status", run_id=run_id)
    assert status["output"]["status"] == "RUNNING"
    assert status["output"]["tasks"][0]["capability"] == "system_get_info"

    cancelled = call(server, "objective_cancel", run_id=run_id,
                     reason="test cancel")
    assert cancelled["status"] == "succeeded"
    assert cancelled["output"]["status"] == "CANCELLED"

    history = call(server, "objective_history", run_id=run_id)
    events = [e["event"] for e in history["output"]]
    assert events == ["run.created", "run.paused", "run.resumed",
                      "task.interrupted", "run.cancelled"]

    listed = call(server, "objective_list", limit=5)
    assert listed["status"] == "succeeded"
    assert listed["output"][0]["run_id"] == run_id


def test_mcp_history_without_run_id_uses_most_recent(server):
    call(server, "objective_start", objective="check the system")
    history = call(server, "objective_history")
    assert history["output"][0]["event"] == "run.created"


def cli(tmp_path, *argv):
    env = dict(os.environ)
    env["ADA_DB"] = str(tmp_path / "cli.sqlite3")
    env["PYTHONUTF8"] = "1"
    proc = subprocess.run(
        [PYTHON, "-m", "friday.objective_cli", *argv],
        cwd=str(ROOT), env=env, capture_output=True, text=True,
        timeout=120)
    return proc.returncode, proc.stdout, proc.stderr


def test_cli_port_flags_round_trip(tmp_path):
    code, out, err = cli(tmp_path, "--port-start")
    assert code == 0, err
    assert '"status": "succeeded"' in out
    run_id = json.loads(out)["output"]["run_id"]

    code, out, err = cli(tmp_path, "--status", run_id)
    assert code == 0, err
    assert run_id in out and "system_get_info" in out

    code, out, err = cli(tmp_path, "--pause", run_id)
    assert "paused" in out
    code, out, err = cli(tmp_path, "--resume", run_id)
    assert "resumed" in out
    code, out, err = cli(tmp_path, "--cancel", run_id)
    assert "cancelled" in out

    code, out, err = cli(tmp_path, "--history", run_id)
    assert "run.created" in out and "run.cancelled" in out

    code, out, err = cli(tmp_path, "--list")
    assert run_id in out


def test_cli_drive_drives_a_real_run_to_terminal(tmp_path):
    code, out, err = cli(tmp_path, "--objective", "check the system")
    assert code == 0, err
    assert "COMPLETED" in out, out
    assert "1 succeeded" in out, out


def test_cli_drive_is_honest_about_an_unmappable_objective(tmp_path):
    code, out, err = cli(tmp_path, "--objective", "flurb the wibble")
    assert code == 0, err
    assert "FAILED" in out, out
    assert "1 failed" in out, out
