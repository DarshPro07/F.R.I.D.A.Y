"""
ORCH-004 (slice 3): every path a worker's result takes to FRIDAY carries the
bounded record, not the transcript - the deferred delivery for a run that
outlives the 240 s wait, hermes_status, and the objective-engine executor.
"""
from __future__ import annotations

import asyncio
import json

from friday import hermes_bridge as hb
from friday.handoff import RECORD_TOKEN_CAP

REPORT = (
    "Fixed the parser off-by-one.\n"
    "Status: SUCCEEDED\n"
    "Commit: 3f2a9c1d7e\n"
    "Files changed: friday/parser.py, tests/test_parser.py\n"
    "Tests: tests/test_parser.py::test_off_by_one (red->green)\n"
    "Evidence: data/runs/parser/green.txt\n"
    "Blockers: none\n"
)
NOISE = "TRANSCRIPT-NOISE-LINE. "
LONG = (REPORT + NOISE * 170)[:4000]          # the stored cap


class FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def register(fn):
            self.tools[fn.__name__] = fn
            return fn
        return register


def _finished(log, result=LONG, status=hb.COMPLETE):
    run_id = log.create(task="fix the parser")
    log.update(run_id, hermes_stored_session_id="stored-77")
    log.update(run_id, status=status, result=result)
    return run_id


def _tokens(text) -> int:
    return len(text if isinstance(text, str) else json.dumps(text)) // 4


def test_deferred_delivery_message_is_the_record_not_the_transcript(tmp_path):
    from friday.record_delivery import RecordDeliveryLog

    log = RecordDeliveryLog(tmp_path / "t.sqlite3")
    run_id = _finished(log)
    assert log.sweep_undelivered() == 1             # the path a late finish takes
    message = log.pending_deliveries()[0]["message"]
    assert message.startswith("Hermes finished: fix the parser")
    assert "3f2a9c1d7e" in message and "data/runs/parser/green.txt" in message
    assert message.count(NOISE) < 15                       # a short excerpt at most
    assert _tokens(message) <= RECORD_TOKEN_CAP + 100
    assert run_id in message and "full=true" in message     # how to ask for more
    assert log.get(run_id)["result"] == LONG                # transcript untouched


def test_deferred_delivery_keeps_the_failure_framing(tmp_path):
    from friday.record_delivery import RecordDeliveryLog

    log = RecordDeliveryLog(tmp_path / "t.sqlite3")
    _finished(log, result="gateway died mid-run", status=hb.FAILED)
    log.sweep_undelivered()
    message = log.pending_deliveries()[0]["message"]
    assert message.startswith("Hermes couldn't finish: fix the parser")
    assert "gateway died mid-run" in message


def test_the_tool_supervisor_uses_the_record_delivery_log(tmp_path, monkeypatch):
    from friday.record_delivery import RecordDeliveryLog
    from friday.tools import hermes_control as hc

    captured = {}

    class FakeSupervisor:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(hb, "HermesSupervisor", FakeSupervisor)
    hc.configure(None)
    try:
        hc.supervisor()
    finally:
        hc.configure(None)
    assert isinstance(captured.get("log"), RecordDeliveryLog)


def _status_tool(log, monkeypatch):
    from friday import policy as P
    from friday.tools import hermes_control as hc

    class Sup:
        def __init__(self):
            self.log = log

        def stall_state(self, work_run_id):
            return {}

        def health(self):
            return {"alive": False}

    monkeypatch.setattr(hc, "_engine", P.PolicyEngine(autonomy=P.FULL))
    mcp = FakeMCP()
    hc.register(mcp)
    hc.configure(Sup())
    return mcp.tools["hermes_status"], hc


def test_hermes_status_returns_the_record_and_the_transcript_only_on_request(
        tmp_path, monkeypatch):
    log = hb.WorkRunLog(tmp_path / "t.sqlite3")
    run_id = _finished(log)
    status, hc = _status_tool(log, monkeypatch)
    try:
        lean = status(work_run_id=run_id)
        full = status(work_run_id=run_id, full=True)
    finally:
        hc.configure(None)
    assert lean["run"]["status"] == hb.COMPLETE
    assert lean["record"]["commit"] == "3f2a9c1d7e"
    assert json.dumps(lean).count(NOISE) < 30
    assert _tokens(lean) <= 2 * RECORD_TOKEN_CAP
    assert _tokens(lean["record"]) <= RECORD_TOKEN_CAP
    assert full["run"]["result"] == LONG                   # explicit request


def test_executor_carries_the_record_and_keeps_the_transcript_on_disk(tmp_path):
    from friday.executors.claude_code import TaskBundle as DevBundle
    from friday.executors.hermes import HermesExecutor

    class FakeSup:
        def delegate(self, bundle, **kw):
            return {"work_run_id": "hermes-9",
                    "result": {"status": hb.COMPLETE, "result": LONG,
                               "model": "claude-opus-5",
                               "hermes_stored_session_id": "stored-77"}}

    res = asyncio.run(HermesExecutor(None, supervisor=FakeSup()).execute(
        DevBundle(goal="fix the parser", workspace=str(tmp_path))))
    assert res.status == "succeeded"
    out = res.output
    assert out["record"]["commit"] == "3f2a9c1d7e"
    assert out["record"]["work_run_id"] == "hermes-9"
    assert out["record"]["transcript_ref"] == "stored-77"
    assert _tokens(out["record"]) <= RECORD_TOKEN_CAP
    assert json.dumps(out, default=str).count(NOISE) < 30
    assert _tokens(json.dumps(out, default=str)) <= 2 * RECORD_TOKEN_CAP
    assert out["record"]["files_changed"] == ["friday/parser.py", "tests/test_parser.py"]


def test_the_supervisors_terminal_event_delivers_the_record(tmp_path):
    """The real late-finish path: HermesSupervisor's message.complete handler."""
    from friday.record_delivery import RecordDeliveryLog

    log = RecordDeliveryLog(tmp_path / "t.sqlite3")
    sup = hb.HermesSupervisor(log=log, profile="")
    run_id = log.create(task="fix the parser", friday_run_id="",
                        bundle_chars=10, token_budget=1000)
    sup._session_runs["s1"] = run_id
    sup._handle_event({"type": "message.complete", "session_id": "s1",
                       "payload": {"status": "complete", "text": LONG}})
    message = log.pending_deliveries()[0]["message"]
    assert "3f2a9c1d7e" in message and "full=true" in message
    assert message.count(NOISE) < 15
    assert log.get(run_id)["result"] == LONG


def test_hermes_status_run_carries_the_record_for_the_objective_engine(
        tmp_path, monkeypatch):
    """continuous._worker_status reads only observed['run']; the outcome must be in it."""
    from friday import continuous

    log = hb.WorkRunLog(tmp_path / "t.sqlite3")
    run_id = _finished(log)
    status, hc = _status_tool(log, monkeypatch)
    try:
        observed = status(work_run_id=run_id)
    finally:
        hc.configure(None)
    state, payload = continuous._worker_status(observed)
    assert state == hb.COMPLETE.lower()
    assert payload["record"]["commit"] == "3f2a9c1d7e"
    assert payload["record"]["evidence"]
    assert payload["record"]["tests"]
    assert "result" not in payload                      # not the transcript
    assert json.dumps(payload).count(NOISE) < 30


def test_creating_the_tool_supervisor_sweeps_undelivered_runs_as_records(
        tmp_path, monkeypatch):
    """A run whose delivery was lost to a crash is swept in record form."""
    from friday.record_delivery import RecordDeliveryLog
    from friday.tools import hermes_control as hc

    log = RecordDeliveryLog(tmp_path / "t.sqlite3")
    _finished(log)                                      # terminal, no delivery row
    monkeypatch.setattr(hc, "RecordDeliveryLog", lambda: log)
    monkeypatch.setattr(hb, "HermesSupervisor",
                        lambda **kw: type("S", (), {"log": kw["log"]})())
    hc.configure(None)
    try:
        hc.supervisor()
    finally:
        hc.configure(None)
    message = log.pending_deliveries()[0]["message"]
    assert "3f2a9c1d7e" in message and message.count(NOISE) < 15


def test_the_voice_agents_startup_sweep_renders_the_record(monkeypatch):
    # The crash-recovery sweep in the voice agent creates deliveries too, so it must build
    # them through the record-rendering log, not a plain WorkRunLog (verifier, ORCH-004).
    import inspect

    import agent_friday
    from friday import record_delivery

    class Spy:
        pass

    monkeypatch.setattr(record_delivery, "RecordDeliveryLog", Spy)
    assert isinstance(agent_friday.hermes_delivery_log(), Spy)
    entry = inspect.getsource(agent_friday.entrypoint)
    assert "hermes_delivery_log()" in entry and "hb.WorkRunLog()" not in entry


def test_hermes_status_lists_active_runs_as_records_not_transcripts(
        tmp_path, monkeypatch):
    log = hb.WorkRunLog(tmp_path / "t.sqlite3")
    run_id = log.create(task="fix the parser")
    log.update(run_id, result=LONG,                   # still running
               progress_json=json.dumps({"last_text": NOISE * 40}))
    status, hc = _status_tool(log, monkeypatch)
    monkeypatch.setattr(hb, "native_tools_healthy", lambda *a, **k: False)
    try:
        listing = status()
        full = status(work_run_id=run_id, full=True)
    finally:
        hc.configure(None)
    [row] = listing["active"]
    assert row["work_run_id"] == run_id
    assert row["record"]["work_run_id"] == run_id
    assert row["record"]["commit"] == "3f2a9c1d7e"
    assert json.dumps(listing).count(NOISE) < 30
    assert _tokens(listing["active"]) <= 2 * RECORD_TOKEN_CAP
    assert full["run"]["result"] == LONG                   # on request only


def test_hermes_status_lean_run_does_not_leak_the_transcript():
    from friday.tools import hermes_control as hc

    row = {"work_run_id": "hermes-1", "task": "fix the parser",
           "status": hb.COMPLETE, "result": LONG,
           "progress_json": json.dumps({"last_text": NOISE * 40}),
           "usage_json": json.dumps({"total": 10}), "handoff": NOISE * 40}
    lean = hc._lean_run(row)
    assert not set(lean) & hc._TRANSCRIPT_COLUMNS
    assert lean["record"]["commit"] == "3f2a9c1d7e"
    assert json.dumps(lean).count(NOISE) < 30
    assert _tokens(lean) <= 2 * RECORD_TOKEN_CAP


def test_a_supervisor_built_without_a_log_delivers_the_record(tmp_path, monkeypatch):
    """A caller that passes no log (a script, a new entry point) must not
    fall back to a log whose deliveries carry the whole transcript."""
    monkeypatch.setenv("ADA_DB", str(tmp_path / "t.sqlite3"))
    sup = hb.HermesSupervisor(profile="")
    run_id = sup.log.create(task="fix the parser", friday_run_id="",
                            bundle_chars=10, token_budget=1000)
    sup._session_runs["s1"] = run_id
    sup._handle_event({"type": "message.complete", "session_id": "s1",
                       "payload": {"status": "complete", "text": LONG}})
    message = sup.log.pending_deliveries()[0]["message"]
    assert "3f2a9c1d7e" in message and "full=true" in message
    assert message.count(NOISE) < 15
    assert sup.log.get(run_id)["result"] == LONG


def test_progress_polled_by_the_browser_carries_the_summary_not_the_transcript(
        tmp_path):
    """/api/hermes/progress and the digest rows both read progress(); a settled
    run's `result` there used to be the first 1200 characters of the transcript
    and `handoff` its 600-character render of it."""
    log = hb.WorkRunLog(tmp_path / "t.sqlite3")
    run_id = _finished(log)
    with log._connect() as db:
        db.execute("UPDATE hermes_work_runs SET handoff=? WHERE work_run_id=?",
                   (json.dumps({"summary": NOISE * 40}), run_id))
    prog = hb.HermesSupervisor(log=log, profile="").progress(run_id)
    assert not {"result", "handoff"} & set(prog)
    assert prog["summary"].startswith("Fixed the parser off-by-one.")
    assert "Commit:" not in prog["summary"] and len(prog["summary"]) <= 300
    assert json.dumps(prog).count(NOISE) < 15
    assert _tokens(prog) <= RECORD_TOKEN_CAP
    assert log.get(run_id)["result"] == LONG               # transcript untouched


def test_progress_of_a_run_that_has_not_finished_has_no_summary(tmp_path):
    log = hb.WorkRunLog(tmp_path / "t.sqlite3")
    run_id = log.create(task="fix the parser")
    prog = hb.HermesSupervisor(log=log, profile="").progress(run_id)
    assert prog["summary"] == ""


_LABELLED_RESULT = "Commit: deadbee\nTests: tests/test_parser.py\nFixed the parser.\n"


def test_a_finished_runs_spoken_milestone_is_the_record_not_the_transcript():
    """The progress digest is spoken into FRIDAY's session (deliver_message and
    the `work` tool result); it must carry the record's summary, not the first
    200 characters of the transcript with its labelled lines."""
    from friday.handoff import work_run_record
    from friday.progress_digest import compose

    run = {"work_run_id": "hermes-9", "status": hb.COMPLETE, "seq": 1,
           "result": _LABELLED_RESULT, "model": "sonnet",
           "route_reason": "coding"}
    summary = work_run_record(run)["summary"]
    assert summary == "Fixed the parser."
    milestone = compose([run], now=5, last_digest_at=0).milestones[0]
    assert summary in milestone
    assert "Commit:" not in milestone and "Tests:" not in milestone


def test_a_milestone_with_no_prose_falls_back_to_the_progress_line():
    from friday.progress_digest import compose

    run = {"work_run_id": "hermes-9", "status": hb.COMPLETE, "seq": 1,
           "result": "Commit: deadbee\nTests: tests/test_parser.py\n",
           "line": "wrote the fix", "model": "sonnet", "route_reason": "coding"}
    milestone = compose([run], now=5, last_digest_at=0).milestones[0]
    assert "wrote the fix" in milestone
    assert "Commit:" not in milestone and "Tests:" not in milestone


def test_outcome_line_falls_back_to_the_record_not_the_transcript():
    """With no `handoff` column the "what finished" reply must still say the
    record's summary, not the transcript head."""
    from friday.handoff import work_run_record
    from friday.progress_digest import outcome_line

    record = {"work_run_id": "hermes-9", "status": hb.COMPLETE,
              "task": "fix the parser", "result": _LABELLED_RESULT,
              "model": "sonnet", "route_reason": "coding"}
    line = outcome_line(record, now=0)
    assert work_run_record(record)["summary"] in line
    assert "Commit:" not in line and "Tests:" not in line
