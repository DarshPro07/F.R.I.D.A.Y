"""
Orphaned work runs and cross-process progress.

2026-09-04 16:08, first digest after a launcher restart: "did 0 tools, last:
Hermes is reading the task - 0s in., on claude-opus-5 (default route), next:
wrapping up; did 0 tools, ..." twenty times in one breath. Two defects:
(1) a run whose owning process died stayed non-terminal forever, so every
poll narrated it; (2) the progress ledger lived only in the memory of the
process that ran the delegation, so the room and the control room (other
processes) saw "0 tools, 0s in" for every run, live ones included.
"""
import json
import re
import time

import pytest

from friday import hermes_bridge as hb
from friday import progress_digest as pd


@pytest.fixture
def log(tmp_path):
    return hb.WorkRunLog(tmp_path / "runs.sqlite3")


def _row(log, wid, *, owner, status, at):
    with log._connect() as db:
        db.execute(
            "INSERT INTO hermes_work_runs (work_run_id, task, status, origin,"
            " owner, started_at, last_event_at) VALUES (?,?,?,?,?,?,?)",
            (wid, "t", status, "production", owner, at, at))


def test_sweep_marks_dead_and_ownerless_runs_lost(log):
    old = time.time() - 3600
    _row(log, "legacy", owner="", status=hb.WORKING, at=old)
    _row(log, "dead", owner="99999999:0", status=hb.WORKING, at=old)
    _row(log, "done", owner="", status=hb.COMPLETE, at=old)
    live = log.create(task="live")              # owned by this process
    assert set(log.sweep_orphans()) == {"legacy", "dead"}
    for wid in ("legacy", "dead"):
        rec = log.get(wid)
        assert rec["status"] == hb.FAILED and rec["failure_kind"] == "LOST"
        assert "restart" in rec["result"]
        assert rec["last_event_at"] == old      # stale: no fresh milestone
    assert log.get("done")["status"] == hb.COMPLETE
    assert log.get(live)["status"] == hb.STARTING
    assert [r["work_run_id"] for r in log.active()] == [live]
    assert log.sweep_orphans() == []            # idempotent


def test_progress_reads_the_persisted_ledger_from_another_process(log):
    wid = log.create(task="t")
    ledger = {"tools": 2, "current": "", "last": "ran the tests", "seq": 3,
              "started_at": time.time() - 42}
    log.update(wid, status=hb.WORKING, progress_json=json.dumps(ledger))
    sup = hb.HermesSupervisor(log=log)          # fresh process: empty memory
    prog = sup.progress(wid)
    assert prog["tools"] == 2 and prog["seq"] == 3
    assert "ran the tests" in prog["line"]
    assert re.search(r"\b4[2-4]s in", prog["line"]), prog["line"]


def test_gather_sweeps_orphans_before_narrating(log):
    old = time.time() - 3600
    _row(log, "ghost", owner="", status=hb.WORKING, at=old)

    class Sup:
        def __init__(self):
            self.log = log

        def progress(self, wid):
            return {"work_run_id": wid, "line": "x", "seq": 0, "tools": 0}

    assert [r["work_run_id"] for r in pd.gather(Sup())] == []


def test_digest_caps_at_three_runs_and_counts_the_rest():
    runs = [{"work_run_id": f"w{i}", "status": hb.WORKING, "line": f"step {i}",
             "tools": i, "model": "m", "route_reason": "r"} for i in range(6)]
    d = pd.compose(runs, now=1000.0, last_digest_at=0.0)
    assert d.digest.count("did ") == 3 and "3 more" in d.digest


def test_tool_events_persist_the_ledger_for_other_processes(log):
    wid = log.create(task="t")
    sup = hb.HermesSupervisor(log=log)
    sup._session_runs["s1"] = wid
    sup._handle_event({"type": "tool.start", "session_id": "s1",
                       "payload": {"name": "read_file", "args": {"path": "a.py"}}})
    stored = json.loads(log.get(wid)["progress_json"])
    assert stored["tools"] == 1 and stored["seq"] == 1
    other = hb.HermesSupervisor(log=log)        # the room / control-room process
    assert other.progress(wid)["tools"] == 1


def test_milestones_are_capped_like_the_digest():
    runs = [{"work_run_id": f"d{i}", "status": hb.COMPLETE, "result": f"done {i}",
             "model": "m", "route_reason": "r"} for i in range(6)]
    d = pd.compose(runs, now=1000.0, last_digest_at=1000.0)
    assert len(d.milestones) == 4 and d.milestones[-1] == "and 3 more finished"
