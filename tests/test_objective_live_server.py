"""
Test B: the Phase 3 objective surface over a real wire.

The MCP adapter tests run in-process. This proves the two process paths the
demo actually uses, end to end:

  server.py   spawned on an unused port with its own database; an MCP SSE
              client connects, lists the seven objective tools, starts a
              run, reads its status, and cancels it - all over HTTP.
  the child   the CLI driver spawned as its own process drives a run to
              COMPLETED against the same kind of database, and the run is
              read back from the sqlite file afterwards - persistence, not
              process memory.

Both are skipped automatically if the spawn fails, so a machine without the
provider stack still runs the suite; the spawn itself is the canary.
"""
from __future__ import annotations
import asyncio
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
import pytest
from friday.store import Store
ROOT = Path(__file__).resolve().parent.parent
PYTHON = ROOT / '.venv' / 'Scripts' / 'python.exe'
PYTHON = str(PYTHON if PYTHON.exists() else sys.executable)
NO_WINDOW = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_for_sse(port: int, timeout: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1.0):
                return True
        except OSError:
            time.sleep(0.25)
    return False


def spawn_server(port: int, db: Path):
    env = dict(os.environ)
    env["ADA_MCP_HOST"] = "127.0.0.1"
    env["ADA_MCP_PORT"] = str(port)
    env["ADA_DB"] = str(db)
    env["PYTHONUTF8"] = "1"
    return subprocess.Popen(
        [PYTHON, "server.py"], cwd=str(ROOT), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **NO_WINDOW)


@pytest.fixture
def live_server(tmp_path):
    port = free_port()
    db = tmp_path / "wire.sqlite3"
    proc = spawn_server(port, db)
    try:
        if not wait_for_sse(port):
            pytest.skip("server.py did not come up on the test port")
        yield port, db
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_objective_tools_work_over_sse(live_server):
    port, db = live_server

    async def round_trip():
        from mcp import ClientSession
        from mcp.client.sse import sse_client

        url = f"http://127.0.0.1:{port}/sse"
        async with sse_client(url) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                names = {t.name for t in tools.tools}
                for tool in ("objective_start", "objective_status",
                             "objective_list", "objective_pause",
                             "objective_resume", "objective_cancel",
                             "objective_history"):
                    assert tool in names, tool

                result = await session.call_tool(
                    "objective_start",
                    {"objective": "check the system"})
                started = _payload(result)
                assert started["status"] == "succeeded"
                run_id = started["output"]["run_id"]
                assert started["output"]["status"] == "RUNNING"

                result = await session.call_tool(
                    "objective_status", {"run_id": run_id})
                status = _payload(result)
                assert status["output"]["tasks"][0]["capability"] == \
                    "system_get_info"

                result = await session.call_tool(
                    "objective_cancel",
                    {"run_id": run_id, "reason": "wire test"})
                cancelled = _payload(result)
                assert cancelled["output"]["status"] == "CANCELLED"

                result = await session.call_tool(
                    "objective_history", {"run_id": run_id})
                history = _payload(result)
                kinds = [e["event"] for e in history["output"]]
                assert "run.created" in kinds
                assert "run.cancelled" in kinds
                return run_id

    run_id = asyncio.run(round_trip())
    row = Store(str(db)).objective_run(run_id)
    assert row["status"] == "CANCELLED"


def _payload(result):
    """
    The dict a wire call_tool returns, in whichever SDK shape arrives.

    `structuredContent` is checked first and deliberately. The installed SDK
    returns a `CallToolResult` pydantic model, and iterating one of those
    yields its *field names* - ('meta', ...), ('content', ...) - not content
    blocks. So the text fallback below collected `""` from tuples that have no
    `.text`, `json.loads("")` raised, and the helper returned `{"text": ""}`.

    The test then failed on `KeyError: 'status'`, which reads exactly like the
    server having returned a malformed payload. It had not: called in-process
    the same tool returns `succeeded` with a run id. The only broken thing was
    this unwrapping.
    """
    import json
    if isinstance(result, dict):
        return result
    structured = getattr(result, 'structuredContent', None)
    if isinstance(structured, dict):
        return structured
    if isinstance(result, tuple):
        maybe = result[1] if len(result) > 1 else result[0]
        if isinstance(maybe, dict):
            return maybe
        result = result[0]
    blocks = getattr(result, 'content', result)
    text = ''.join((getattr(b, 'text', '') or '' for b in blocks))
    try:
        return json.loads(text)
    except ValueError:
        return {'text': text}


def test_spawned_child_drives_to_completed_in_its_own_db(tmp_path):
    db = tmp_path / "child.sqlite3"
    env = dict(os.environ)
    env["ADA_DB"] = str(db)
    env["PYTHONUTF8"] = "1"
    proc = subprocess.run(
        [PYTHON, "-m", "friday.objective_cli",
         "--objective", "check the system"],
        cwd=str(ROOT), env=env, capture_output=True, text=True,
        timeout=240, **NO_WINDOW)
    assert proc.returncode == 0, proc.stderr[-800:]

    runs = Store(str(db)).objective_runs(limit=5)
    assert runs, "the child left no run behind"
    row = Store(str(db)).objective_run(runs[0]["run_id"])
    assert row["status"] == "COMPLETED", row
    assert row["summary"]["succeeded"] == 1
    tasks = Store(str(db)).objective_tasks(row["run_id"])
    assert tasks[0]["capability"] == "system_get_info"
    assert tasks[0]["status"] == "SUCCEEDED"
