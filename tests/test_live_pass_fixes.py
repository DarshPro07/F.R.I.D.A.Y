"""
Four defects found by the 2026-09-04 21:15 live pass through the control room:
the browser digest re-spoke the same step every poll; the model rewrote the
boss's path ('friday/desk.py' -> '/desk.py') in the Hermes goal; 'next:'
showed a raw tool name or 'wrapping up' before the first tool; a content
search from '.' read every file under .venv / node_modules / third_party.
"""
import time

from friday import progress_digest as pd
from friday import ui_server as u
from friday import voice_brain as vb
from friday.tools import execution_bridge as eb


class _Log:
    def __init__(self, rows):
        self.rows = rows

    def recent(self, limit=10):
        return self.rows[:limit]

    def active(self):
        return [r for r in self.rows if r["status"] not in pd.TERMINAL]

    def sweep_orphans(self):
        return []


class _Sup:
    def __init__(self, rows):
        self.log = _Log(rows)
        self.goals = []

    def progress(self, wid):
        return {"work_run_id": wid, "seq": 2, "tools": 2, "current": "searching files",
                "line": "Hermes is searching files - step 2, 40s in."}

    def delegate(self, bundle, **kw):
        self.goals.append(bundle.goal)
        return {"work_run_id": "w-new", "bundle": {"chars": 10}}


def _patch(monkeypatch, sup):
    import friday.tools.hermes_control as hc
    monkeypatch.setattr(hc, "supervisor", lambda: sup)


def test_browser_digest_keeps_the_room_cadence(monkeypatch):
    sup = _Sup([{"work_run_id": "w", "status": "WORKING", "origin": "production",
                 "task": "t", "model": "m", "route_reason": "r", "last_event_at": time.time()}])
    _patch(monkeypatch, sup)
    monkeypatch.setitem(u._WORK_DIGEST, "last_at", 0.0)
    first = u._probe_work()
    assert first["digest"].startswith("did 2 tools")
    assert u._probe_work()["digest"] == ""          # same cadence window: quiet
    monkeypatch.setitem(u._WORK_DIGEST, "last_at", time.time() - 200)
    assert u._probe_work()["digest"].startswith("did 2 tools")


def test_the_boss_path_reaches_hermes_verbatim():
    spoken = "Friday, hand this to Hermes: add a one-line docstring to `_busy` in friday/desk.py, cheapest model."
    goal = "add a one-line docstring to `_busy` in `/desk.py` explaining what a busy clipboard means"
    fixed = vb._keep_literal_paths(goal, spoken)
    assert "`friday/desk.py`" in fixed and "`/desk.py`" not in fixed
    assert vb._keep_literal_paths("edit friday/desk.py now", spoken) == "edit friday/desk.py now"
    assert vb._keep_literal_paths("compare tests/desk.py", spoken) == "compare tests/desk.py"


def test_run_hermes_delegates_the_literal_path(monkeypatch):
    sup = _Sup([])
    _patch(monkeypatch, sup)
    vb._CURRENT_TURN["text"] = "hand this to Hermes: add a docstring to `_busy` in friday/desk.py, cheapest model."
    out = vb._run_hermes("delegate", {"goal": "add a docstring to `_busy` in `/desk.py`, cheapest model"})
    assert "error" not in out, out
    assert sup.goals == ["add a docstring to `_busy` in `friday/desk.py`, cheapest model"]


def test_next_step_is_described_or_first_tool():
    assert "next: first tool" in pd._digest_line({"tools": 0, "line": "Hermes is reading the task - 3s in."})
    assert "next: searching files" in pd._digest_line({"tools": 2, "current": "searching files", "line": "x"})


def test_search_skips_dependency_trees_and_caps(monkeypatch):
    read = []

    class Env:
        def listing(self, path):
            return ["node_modules/x/a.py", ".venv/Lib/b.py", "third_party/c.py", "friday/d.py"]

        def read(self, name, limit=0):
            read.append(name)
            return "needle here\n"

    monkeypatch.setattr(eb, "_environment", lambda: Env())
    out = eb.bridge_search_files("needle", ".")
    assert read == ["friday/d.py"] and out.startswith("friday/d.py:1:")
    monkeypatch.setattr(eb, "_SEARCH_MAX_FILES", 1)

    class Env2(Env):
        def listing(self, path):
            return ["friday/d.py", "friday/e.py"]

    monkeypatch.setattr(eb, "_environment", lambda: Env2())
    assert "stopped after 1 files" in eb.bridge_search_files("nothing-matches", ".")
