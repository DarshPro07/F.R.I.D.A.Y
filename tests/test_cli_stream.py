"""
The coding CLI's event stream is read whole, however long a line is.

stream-json puts each event on one line. The first one lists every tool,
skill, plugin and MCP server the CLI loaded, and a tool result carries what
the tool returned, so a line past asyncio's default 64 KiB reader limit is
ordinary; on 2026-09-27 the first live run on this machine died on the init
line with "Separator is found, but chunk is longer than limit".
"""
from __future__ import annotations

import asyncio
import json
import sys

from friday.executors import cli


def test_an_event_line_longer_than_the_default_reader_limit_is_read(tmp_path, monkeypatch):
    long_event = {"type": "system", "subtype": "init", "session_id": "S-long",
                  "tools": ["t" * 50] * 4000}            # ~200 KiB on one line
    result = {"type": "result", "result": "done"}
    script = tmp_path / "fake_cli.py"
    script.write_text(
        "import json, sys\n"
        "sys.stdin.read()\n"
        f"print(json.dumps({long_event!r}))\n"
        f"print(json.dumps({result!r}))\n",
        encoding="utf-8")
    monkeypatch.setattr(cli.Launch, "argv", lambda self: [sys.executable, str(script)])
    assert len(json.dumps(long_event)) > 2 ** 16

    run, events = asyncio.run(cli.stream(cli.Launch(prompt="go", cwd=str(tmp_path)),
                                         timeout=60))
    kinds = [e.kind for e in events]
    assert kinds == ["init", "result"], kinds
    assert run.session_id == "S-long"
