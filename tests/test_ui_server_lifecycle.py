"""
Importing the control room starts nothing; serving it does (V7-D36).

`friday/ui_server.py` builds its app at import (`app = create_app()`, for
uvicorn), and `create_app` started the health refresher: a daemon that
spawns the brain's probe and reads memory every 25 s. So every process that
merely imported the module - the voice agent through `memory_stack`, the
memory graph, a test - ran it, and its reads landed inside other code's
measurements. The refresher belongs to the served app's lifespan.
"""
from __future__ import annotations

import os
import subprocess
import sys


def _python(code: str, tmp_path) -> str:
    env = dict(os.environ, ADA_DB=str(tmp_path / "ada.sqlite3"))
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         timeout=120, env=env, cwd=os.getcwd())
    assert out.returncode == 0, out.stderr[-2000:]
    return out.stdout.strip().splitlines()[-1]


def test_importing_the_ui_server_starts_no_thread(tmp_path):
    names = _python(
        "import threading, friday.ui_server\n"
        "print(sorted(t.name for t in threading.enumerate()))", tmp_path)
    assert "health-refresh" not in names, names


def test_serving_the_app_starts_its_health_refresher(tmp_path):
    started = _python(
        "from starlette.testclient import TestClient\n"
        "import friday.ui_server as U\n"
        "with TestClient(U.create_app()):\n"
        "    print(U._MEM_THREAD is not None and U._MEM_THREAD.is_alive())", tmp_path)
    assert started == "True"
