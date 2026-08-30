#!/usr/bin/env python3
"""
verify_mcp.py - prove the MCPToolset migration did not lose any tool.

Builds the toolset exactly the way agent_friday does, connects to the running
MCP server, and checks that every tool declared in friday.capabilities is
still exposed. Starts the MCP server itself if nothing is listening.

    python scripts/verify_mcp.py

Exit 0 = every declared tool is reachable through MCPToolset.
"""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import agent_friday  # noqa: E402
from friday import capabilities  # noqa: E402


def port_open(host: str, port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


def start_server_if_needed() -> subprocess.Popen | None:
    if port_open("127.0.0.1", 8000):
        print("[verify] MCP server already running")
        return None

    print("[verify] starting MCP server ...")
    python = ROOT / ".venv" / "Scripts" / "python.exe"
    proc = subprocess.Popen(
        [str(python if python.exists() else sys.executable), "server.py"],
        cwd=str(ROOT),
        env=dict(os.environ, PYTHONUNBUFFERED="1"),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        if port_open("127.0.0.1", 8000):
            return proc
        if proc.poll() is not None:
            raise RuntimeError("MCP server exited during startup")
        time.sleep(0.25)
    raise RuntimeError("MCP server never opened :8000")


async def check() -> int:
    from livekit.agents.llm import mcp

    toolset = mcp.MCPToolset(
        id=agent_friday.CLOUD_TOOLSET_ID,
        mcp_server=mcp.MCPServerHTTP(
            url=agent_friday.mcp_sse_url(),
            transport_type="sse",
            client_session_timeout_seconds=30,
        ),
    )
    try:
        await toolset.setup()
        exposed = {tool.info.name for tool in toolset.tools}
    finally:
        await toolset.aclose()

    declared = set(capabilities.CAPABILITIES)
    missing = sorted(declared - exposed)
    undeclared = sorted(exposed - declared)

    print(f"[verify] toolset id : {toolset.id}")
    print(f"[verify] exposed    : {len(exposed)} tools")
    for name in sorted(exposed):
        cap = capabilities.CAPABILITIES.get(name)
        edge = " [requires_edge]" if cap and cap.requires_edge else ""
        scope = cap.execution_scope if cap else "UNDECLARED"
        print(f"             - {name} ({scope}){edge}")

    if missing:
        print(f"[verify] FAIL missing from MCP server: {missing}")
    if undeclared:
        print(f"[verify] FAIL exposed but undeclared in capabilities: {undeclared}")
    if missing or undeclared:
        return 1

    print("[verify] OK - every declared tool is reachable through MCPToolset")
    return 0


def main() -> int:
    proc = start_server_if_needed()
    try:
        return asyncio.run(check())
    finally:
        if proc is not None:
            if os.name == "nt":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                               capture_output=True)
            else:
                proc.terminate()


if __name__ == "__main__":
    sys.exit(main())
