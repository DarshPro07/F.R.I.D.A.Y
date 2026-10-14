"""ACC-001 slice 1: an agent's state comes only from durable worker records.

A model or objective that merely *claims* work has no row, so it is never
shown as running. Each Hermes WorkRun status, run_tasks status, governor
queue entry and pending approval gate maps to exactly one of Queued, Running,
Verified, Blocked, Failed or Cancelled, and changes are emitted as AG-UI
frames in the UI server's existing _event_frame envelope.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from friday import agent_ledger as al
from friday import command_center as cc
from friday import hermes_bridge as hb


def _run(wid, status, **kw):
    return {"work_run_id": wid, "status": status, "model": "sonnet",
            "pending_question": "", "started_at": 1.0, **kw}


def _by_id(states):
    return {s.run_id: s for s in states}


def test_a_claim_with_no_row_is_never_running():
    # Nothing durable: no ledger row, no run, no task, no queue, no gate.
    assert cc.derive() == []


@pytest.mark.parametrize("status,state", [
    (hb.WORKING, "running"),
    (hb.STEERED, "running"),
    (hb.STARTING, "queued"),
    (hb.CONNECTED, "queued"),
    (hb.SESSION_READY, "queued"),
    (hb.WAIT_FRIDAY, "blocked"),
    (hb.WAIT_USER, "blocked"),
    (hb.PARTIAL, "blocked"),
    (hb.CANCELLING, "cancelled"),
    (hb.FAILED, "failed"),
    (hb.COMPLETE, "blocked"),          # no evidence refs: never verified
])
def test_each_workrun_status_maps_to_one_state(status, state):
    got = _by_id(cc.derive(runs=[_run("w1", status)]))["w1"]
    assert got.state == state
    assert got.state in cc.STATES
    if got.state == "blocked":
        assert got.reason


def test_wait_states_carry_the_pending_question_as_reason():
    got = cc.derive(runs=[_run("w1", hb.WAIT_USER,
                               pending_question="Delete the branch?")])[0]
    assert got.state == "blocked" and got.reason == "Delete the branch?"


def _lrow(run_id, status, **kw):
    """A row as AgentLedger.view returns it (agent_runs + evidence_refs)."""
    return {"run_id": run_id, "parent_run_id": None, "role_id": "builder",
            "model_route": "anthropic/sonnet/5.5/subscription",
            "status": status, "started_at": "2026-10-14T10:00:00+00:00",
            "evidence_refs": [], **kw}


def test_complete_is_verified_only_with_evidence_refs():
    ledger = [
        _lrow("a", al.SUCCEEDED, evidence_refs=["tests/test_x.py::test_y"]),
        _lrow("b", al.SUCCEEDED, evidence_refs=[]),
        _lrow("c", al.SUCCEEDED, evidence_refs="[]"),
    ]
    got = _by_id(cc.derive(ledger=ledger))
    assert got["a"].state == "verified"
    assert got["b"].state == "blocked"
    assert got["b"].reason == "completion unverified"
    assert got["c"].state == "blocked"


_REPORT = ("Done, the repair is in.\n"
           "Commit: abc1234\n"
           "Tests: tests/test_x.py::test_y\n"
           "Evidence: tests/test_x.py::test_y passed; log:run-9\n")


def test_a_workrun_row_has_no_evidence_column_so_its_report_is_the_source():
    # hermes_work_runs keeps the worker's report in `result`; the labelled
    # Evidence line (REPORTING_CONTRACT) is what backs a COMPLETE.
    got = _by_id(cc.derive(runs=[
        _run("with", hb.COMPLETE, result=_REPORT),
        _run("prose", hb.COMPLETE, result="All done, trust me."),
        _run("none", hb.COMPLETE, result="Evidence: none\nTests: none"),
        _run("empty", hb.COMPLETE, result=""),
    ]))
    assert got["with"].state == "verified"
    for run_id in ("prose", "none", "empty"):
        assert got[run_id].state == "blocked", run_id
        assert got[run_id].reason == "completion unverified", run_id


def test_a_non_complete_workrun_is_not_made_verified_by_its_report():
    got = cc.derive(runs=[_run("w1", hb.PARTIAL, result=_REPORT,
                               pending_question="needs a decision")])[0]
    assert got.state == "blocked" and got.reason == "needs a decision"


def test_a_real_hermes_complete_run_reaches_verified_after_a_restart(
        tmp_path, monkeypatch):
    # on_terminal would write the outcome into FRIDAY's memory store.
    monkeypatch.setattr(hb.WorkRunLog, "on_terminal", lambda *a, **k: None)
    path = tmp_path / "runs.sqlite3"
    log = hb.WorkRunLog(path)
    backed = log.create(task="backed")
    bare = log.create(task="bare")
    log.update(backed, status=hb.COMPLETE, result=_REPORT)
    log.update(bare, status=hb.COMPLETE, result="All done, trust me.")

    first = _by_id(cc.derive(runs=log.recent(50)))
    assert first[backed].state == "verified"
    assert first[bare].state == "blocked"
    assert first[bare].reason == "completion unverified"
    assert _by_id(cc.derive(runs=hb.WorkRunLog(path).recent(50))) == first


def test_ledger_row_supplies_role_model_and_parent():
    got = cc.derive(ledger=[_lrow("r2", al.RUNNING, parent_run_id="r1",
                                  role_id="qa", model_route="a/haiku/5/sub")])[0]
    assert (got.run_id, got.parent_run_id, got.role, got.model, got.state) == \
        ("r2", "r1", "qa", "a/haiku/5/sub", "running")


@pytest.mark.parametrize("status,refs,state", [
    (al.STARTING, [], "queued"),          # a claim is not a worker
    (al.RUNNING, [], "running"),
    (al.INTERRUPTED, [], "blocked"),
    (al.SUCCEEDED, ["agent_runs:x"], "verified"),
    (al.SUCCEEDED, [], "blocked"),
    (al.FAILED, [], "failed"),
    (al.CANCELLED, [], "cancelled"),
])
def test_each_ledger_status_maps_to_one_state(status, refs, state):
    got = cc.derive(ledger=[_lrow("L", status, evidence_refs=refs)])[0]
    assert got.state == state
    if state == "blocked":
        assert got.reason


def test_an_unknown_ledger_status_is_never_running():
    got = cc.derive(ledger=[_lrow("L", "WORKING")])[0]
    assert got.state == "blocked" and "WORKING" in got.reason


def test_ledger_claims_queue_by_their_start_time():
    got = _by_id(cc.derive(ledger=[
        _lrow("late", al.STARTING, started_at="2026-10-14T10:05:00+00:00"),
        _lrow("early", al.STARTING, started_at="2026-10-14T10:01:00+00:00")]))
    assert (got["early"].position, got["late"].position) == (1, 2)


def test_run_tasks_map_by_status_and_evidence():
    tasks = [
        {"task_id": "t1", "run_id": "o1", "status": "runnable",
         "evidence_refs": "[]"},
        {"task_id": "t2", "run_id": "o1", "status": "running",
         "evidence_refs": "[]"},
        {"task_id": "t3", "run_id": "o1", "status": "succeeded",
         "evidence_refs": json.dumps(["log:1"])},
        {"task_id": "t4", "run_id": "o1", "status": "succeeded",
         "evidence_refs": "[]"},
        {"task_id": "t5", "run_id": "o1", "status": "unknown",
         "evidence_refs": "[]"},
    ]
    got = _by_id(cc.derive(tasks=tasks))
    assert [got[t].state for t in ("t1", "t2", "t3", "t4", "t5")] == \
        ["queued", "running", "verified", "blocked", "blocked"]
    assert got["t2"].parent_run_id == "o1"


def test_an_unrecognised_status_is_never_running():
    got = cc.derive(runs=[_run("w1", "SOMETHING_NEW")])[0]
    assert got.state == "blocked" and "SOMETHING_NEW" in got.reason


def test_queued_workers_report_their_position():
    runs = [_run("w2", hb.STARTING, started_at=20.0),
            _run("w1", hb.SESSION_READY, started_at=10.0)]
    queue = [("hermes", "build the thing", 5.0)]       # governor (kind,label,at)
    got = cc.derive(runs=runs, queue=queue)
    queued = sorted((s for s in got if s.state == "queued"),
                    key=lambda s: s.position)
    assert [s.position for s in queued] == [1, 2, 3]
    assert queued[0].role == "hermes" and queued[0].reason == "build the thing"
    assert [s.run_id for s in queued[1:]] == ["w1", "w2"]
    assert all(s.position is None for s in got if s.state != "queued")


def test_pending_approval_gate_is_blocked_with_its_question():
    gates = [{"nonce": "n1", "action": "mcp.files_delete", "target": "x",
              "question": "Delete x?", "state": "PENDING"}]
    got = cc.derive(gates=gates)[0]
    assert got.state == "blocked" and got.reason == "Delete x?"
    assert got.role == "approval"


def test_equal_states_twice_from_one_sqlite_file(tmp_path):
    path = tmp_path / "runs.sqlite3"
    log = hb.WorkRunLog(path)
    w1 = log.create(task="one")
    w2 = log.create(task="two")
    log.update(w1, status=hb.WORKING)
    log.update(w2, status=hb.WAIT_USER)
    first = cc.derive(runs=log.recent(50))
    # simulated restart: a brand-new handle on the same file
    second = cc.derive(runs=hb.WorkRunLog(path).recent(50))
    assert first == second
    assert {s.state for s in first} == {"running", "blocked"}


def test_run_tasks_survive_a_restart_from_one_sqlite_file(tmp_path):
    path = tmp_path / "t.sqlite3"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE run_tasks (task_id TEXT, run_id TEXT,"
               " status TEXT, evidence_refs TEXT)")
    db.execute("INSERT INTO run_tasks VALUES ('t1','o1','running','[]')")
    db.commit()
    db.close()

    def read():
        c = sqlite3.connect(path)
        c.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in c.execute("SELECT * FROM run_tasks")]
        finally:
            c.close()

    assert cc.derive(tasks=read()) == cc.derive(tasks=read())


def test_snapshot_with_a_source_down_gives_empty_plus_reason():
    def boom():
        raise RuntimeError("db is locked")

    out = cc.snapshot({"runs": boom})
    assert out["agents"] == []
    assert "runs" in out["reason"] and "db is locked" in out["reason"]


def test_snapshot_keeps_what_the_live_sources_know():
    def boom():
        raise RuntimeError("gone")

    out = cc.snapshot({"runs": lambda: [_run("w1", hb.WORKING)],
                       "gates": boom})
    assert [a["run_id"] for a in out["agents"]] == ["w1"]
    assert "gates" in out["reason"]
    json.dumps(out)                       # the endpoint encodes exactly this


def test_snapshot_with_every_source_up_has_no_reason():
    out = cc.snapshot({"runs": lambda: [_run("w1", hb.WORKING)]})
    assert out["reason"] == ""


def _st(run_id, state, reason=""):
    return cc.AgentState(run_id=run_id, parent_run_id="", role="r",
                         model="m", state=state, reason=reason, position=None)


def test_agui_first_frame_is_a_state_snapshot():
    frames = cc.to_agui(None, [_st("a", "running")])
    assert [f.type for f in frames] == ["STATE_SNAPSHOT"]
    assert frames[0].payload["agents"][0]["run_id"] == "a"


def test_agui_transitions_emit_lifecycle_frames():
    def kinds(prev, now):
        return [f.type for f in cc.to_agui(prev, now)]

    assert "RUN_STARTED" in kinds([_st("a", "queued")], [_st("a", "running")])
    assert "RUN_FINISHED" in kinds([_st("a", "running")], [_st("a", "verified")])
    err = cc.to_agui([_st("a", "running")], [_st("a", "failed", "boom")])
    assert "RUN_ERROR" in [f.type for f in err]
    assert [f for f in err if f.type == "RUN_ERROR"][0].payload["message"] == "boom"
    blocked = kinds([_st("a", "running")], [_st("a", "blocked", "why")])
    assert "STEP_STARTED" in blocked
    assert "STEP_FINISHED" in kinds([_st("a", "blocked", "why")],
                                    [_st("a", "running")])
    assert "RUN_ERROR" in kinds([_st("a", "running")], [_st("a", "cancelled")])


def test_agui_no_change_emits_nothing():
    same = [_st("a", "running")]
    assert cc.to_agui(same, [_st("a", "running")]) == []


def test_agui_state_delta_is_a_json_patch():
    frames = cc.to_agui([_st("a", "queued")], [_st("a", "running"),
                                               _st("b", "queued")])
    delta = [f for f in frames if f.type == "STATE_DELTA"]
    assert len(delta) == 1
    ops = {(o["op"], o["path"]) for o in delta[0].payload["delta"]}
    assert ops == {("replace", "/agents/a"), ("add", "/agents/b")}


def test_every_frame_type_is_agui_and_fits_the_event_envelope():
    from friday import ui_server as u
    frames = cc.to_agui(None, [_st("a", "running")]) + cc.to_agui(
        [_st("a", "running")], [_st("a", "failed", "x")])
    assert frames
    for f in frames:
        assert f.type in cc.AGUI_TYPES
        env = u._event_frame(f.type, f.payload, f.run_id)
        body = json.loads(env["data"])
        assert env["event"] == f.type and body["type"] == f.type
        assert body["run_id"] == f.run_id and body["v"] == 1


def test_endpoint_reports_real_rows_and_never_raises(monkeypatch, tmp_path):
    pytest.importorskip("httpx")
    from starlette.testclient import TestClient
    from friday import access
    from friday import ui_server as u

    monkeypatch.setattr(access, "GATE_ENABLED", False)
    path = tmp_path / "ada.sqlite3"
    monkeypatch.setenv("ADA_DB", str(path))
    log = hb.WorkRunLog(path)
    wid = log.create(task="real")
    log.update(wid, status=hb.WORKING)
    with TestClient(u.create_app(), base_url="http://127.0.0.1:8770") as client:
        body = client.get("/api/command_center/state").json()
        assert [a["run_id"] for a in body["agents"]] == [wid]
        assert body["agents"][0]["state"] == "running"
        assert {"agents", "reason", "events"} <= body.keys()
        assert all(e["event"] in cc.AGUI_TYPES for e in body["events"])

        def boom(*a, **k):
            raise RuntimeError("down")
        monkeypatch.setattr(hb, "WorkRunLog", boom)
        down = client.get("/api/command_center/state")
        assert down.status_code == 200
        assert down.json()["agents"] == []
        assert "runs" in down.json()["reason"]


def _seed_real_ledger(path, monkeypatch):
    """A real AgentLedger file: a claim, a live worker, a cancel, a success."""
    from friday.executors import runs as R
    from friday.executors.claude_code import TaskBundle
    from friday.store import Store

    monkeypatch.setattr(R, "process_alive", lambda pid, started_at=None: True)
    store = Store(path)
    try:
        ledger = al.AgentLedger(store)
        ledger.start("CLAIM", task="t", workspace="/w", executor_id="claude_code")
        manager = R.RunManager(store)
        manager.open(TaskBundle(goal="g", workspace="/w", run_id="LIVE"))
        manager.running("LIVE", pid=4321)
        manager.open(TaskBundle(goal="g", workspace="/w", run_id="STOP"))
        manager.running("STOP", pid=4322)
        manager.cancelled("STOP")
        ledger.start("DONE", task="t", workspace="/w", executor_id="claude_code")
        ledger.finish("DONE", al.SUCCEEDED, ["agent_runs:DONE"])
    finally:
        store.close()


def _read_ledger(path):
    conn = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        return al.AgentLedger.view(conn)
    finally:
        conn.close()


def test_a_real_ledger_derives_equal_states_after_a_restart(tmp_path, monkeypatch):
    from friday.executors import runs as R

    path = tmp_path / "ledger.sqlite3"
    _seed_real_ledger(path, monkeypatch)

    first = cc.derive(ledger=_read_ledger(path))
    got = _by_id(first)
    assert {k: v.state for k, v in got.items()} == {
        "CLAIM": "queued", "LIVE": "running", "STOP": "cancelled",
        "DONE": "verified"}
    assert got["CLAIM"].position == 1
    # a second look, from a fresh connection, is the same answer
    assert cc.derive(ledger=_read_ledger(path)) == first

    # the worker process died while FRIDAY was down: the view says so, and
    # reading it did not write (the row still says RUNNING)
    monkeypatch.setattr(R, "process_alive", lambda pid, started_at=None: False)
    after = _by_id(cc.derive(ledger=_read_ledger(path)))
    assert after["LIVE"].state == "blocked" and after["LIVE"].reason
    raw = sqlite3.connect(str(path))
    try:
        assert raw.execute("SELECT status FROM agent_runs WHERE run_id='LIVE'"
                           ).fetchone()[0] == "RUNNING"
    finally:
        raw.close()


def test_endpoint_reads_the_real_ledger_and_names_it_when_down(monkeypatch, tmp_path):
    pytest.importorskip("httpx")
    from starlette.testclient import TestClient
    from friday import access
    from friday import ui_server as u

    monkeypatch.setattr(access, "GATE_ENABLED", False)
    path = tmp_path / "ada.sqlite3"
    _seed_real_ledger(path, monkeypatch)
    monkeypatch.setenv("ADA_DB", str(path))
    with TestClient(u.create_app(), base_url="http://127.0.0.1:8770") as client:
        body = client.get("/api/command_center/state").json()
        states = {a["run_id"]: a["state"] for a in body["agents"]}
        assert states == {"CLAIM": "queued", "LIVE": "running",
                          "STOP": "cancelled", "DONE": "verified"}
        assert body["reason"] == ""

        def boom(*a, **k):
            raise RuntimeError("ledger down")
        monkeypatch.setattr(al.AgentLedger, "view", staticmethod(boom))
        down = client.get("/api/command_center/state")
        assert down.status_code == 200
        assert down.json()["agents"] == []
        assert "ledger" in down.json()["reason"]
