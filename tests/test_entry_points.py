"""
The console scripts pyproject declares must resolve (V7-D16).

`friday = "server:main"` is the documented way to start the MCP server
(`uv run friday`, AGENTS.md), and server.py had no `main`: the command died
with an ImportError before anything started. A declared entry point that
does not resolve is a broken install, whatever the rest of the tree does.
"""
from __future__ import annotations

import importlib
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
    "project"]["scripts"]


@pytest.mark.parametrize("name", sorted(SCRIPTS))
def test_every_console_script_resolves(name):
    module_name, _, attr = SCRIPTS[name].partition(":")
    module = importlib.import_module(module_name.strip())
    target = getattr(module, attr.strip(), None)
    assert callable(target), f"{name} = {SCRIPTS[name]!r} does not resolve"


def test_the_server_honours_an_emergency_stop(monkeypatch):
    """Starting the server starts this process's STOP watcher (STOP-001)."""
    import server
    from friday import stop

    from friday import hardware_profile

    started, ran, profiled = [], [], []
    monkeypatch.setattr(stop, "watch", lambda *a, **k: started.append(True))
    monkeypatch.setattr(hardware_profile, "watch", lambda *a, **k: profiled.append(True))
    monkeypatch.setattr(server.mcp, "run", lambda **kwargs: ran.append(kwargs))
    server.main()
    assert started and ran == [{"transport": "sse"}]
    # RES-004: the process that admits work keeps the machine's profile current.
    assert profiled
