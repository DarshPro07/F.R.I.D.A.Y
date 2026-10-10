"""
ORCH-004: a worker's result reaches FRIDAY as a record (status, commit, tests,
evidence references, blockers) of a few hundred tokens; the transcript stays on
disk and is read only on demand.

The audit found the Handoff had no commit or evidence field, never filled
files/tests, and the hermes_delegate return carried the whole 4000-char result.
"""
from __future__ import annotations

import json

import pytest

from friday import hermes_bridge as hb
from friday.handoff import RECORD_TOKEN_CAP, Handoff, work_run_record

REPORT = (
    "Fixed the parser off-by-one.\n"
    "Status: SUCCEEDED\n"
    "Commit: 3f2a9c1d7e\n"
    "Files changed: friday/parser.py, tests/test_parser.py\n"
    "Tests: tests/test_parser.py::test_off_by_one (red->green); tests/test_lexer.py (12 passed)\n"
    "Evidence: data/runs/parser/green.txt; docs/plans/parser/report.md\n"
    "Blockers: none\n"
)

RECORD_KEYS = {"work_run_id", "status", "commit", "tests", "evidence", "blockers",
               "files_changed", "summary", "transcript_ref", "next_action"}


def _record(result: str, **extra) -> dict:
    return {"work_run_id": "hermes-r1", "status": hb.COMPLETE, "task": "fix the parser",
            "result": result, "hermes_stored_session_id": "stored-77", **extra}


def _tokens(record: dict) -> int:
    return len(json.dumps(record)) // 4


def test_record_holds_status_commit_tests_evidence_blockers():
    rec = work_run_record(_record(REPORT))
    assert set(rec) == RECORD_KEYS
    assert rec["status"] == hb.COMPLETE
    assert rec["commit"] == "3f2a9c1d7e"
    assert rec["files_changed"] == ["friday/parser.py", "tests/test_parser.py"]
    assert rec["tests"][0].startswith("tests/test_parser.py::test_off_by_one")
    assert rec["evidence"] == ["data/runs/parser/green.txt", "docs/plans/parser/report.md"]
    assert rec["blockers"] == []            # the worker said "none"
    assert rec["transcript_ref"] == "stored-77"


def test_record_never_invents_what_the_worker_did_not_state():
    rec = work_run_record(_record("done, all good"))
    assert rec["commit"] == ""
    assert rec["tests"] == rec["evidence"] == rec["blockers"] == rec["files_changed"] == []


def test_record_is_a_few_hundred_tokens_however_long_the_transcript():
    transcript = REPORT + ("I read the file and thought about it. " * 400)
    many = "; ".join(f"tests/test_{i}.py::test_case_{i}" for i in range(200))
    rec = work_run_record(_record(transcript + f"\nTests: {many}\nBlockers: " + "x" * 3000))
    assert _tokens(rec) <= RECORD_TOKEN_CAP
    assert RECORD_TOKEN_CAP <= 400
    assert rec["commit"] == "3f2a9c1d7e"    # trimming drops the tail, not the key facts


def test_handoff_stores_the_same_fields_and_old_rows_still_load():
    h = Handoff.from_work_run(_record(REPORT))
    assert h.commit == "3f2a9c1d7e" and h.evidence_refs and h.transcript_ref == "stored-77"
    assert h.tests_run and h.files_changed
    old = json.dumps({"task_id": "hermes-old", "status": hb.COMPLETE})
    assert Handoff.from_json(old).commit == ""


def test_the_worker_is_told_the_labels_the_record_is_read_from():
    contract = hb.TaskBundle.REPORTING_CONTRACT
    for label in ("Status:", "Commit:", "Files changed:", "Tests:", "Evidence:", "Blockers:"):
        assert label in contract


def test_delegate_tool_returns_the_record_not_the_transcript(tmp_path, monkeypatch):
    from friday import policy as P
    from friday.tools import hermes_control as hc

    log = hb.WorkRunLog(tmp_path / "t.sqlite3")
    long_result = REPORT + ("transcript line. " * 230)       # ~4000 chars, the stored cap

    class Sup:
        def __init__(self):
            self.log = log

        def delegate(self, bundle, **kwargs):
            run_id = log.create(task="fix the parser")
            log.update(run_id, hermes_stored_session_id="stored-77")
            log.update(run_id, status=hb.COMPLETE, result=long_result[:4000])
            return {"work_run_id": run_id, "bundle": {"chars": 1}}

    class FakeMCP:
        tools: dict = {}

        def tool(self):
            def register(fn):
                self.tools[fn.__name__] = fn
                return fn
            return register

    monkeypatch.setattr(hc, "_engine", P.PolicyEngine(autonomy=P.FULL))
    mcp = FakeMCP()
    hc.register(mcp)
    hc.configure(Sup())
    try:
        out = mcp.tools["hermes_delegate"](goal="fix the parser", wait_seconds=5)
    finally:
        hc.configure(None)
    assert out["status"] == "complete"
    assert set(out["handoff"]) == RECORD_KEYS
    assert out["handoff"]["commit"] == "3f2a9c1d7e"
    assert _tokens(out["handoff"]) <= RECORD_TOKEN_CAP
    assert len(out["result"]) <= 600                         # was the whole 4000
    # The transcript stays on disk, whole, for an on-demand read.
    full = log.get(out["work_run_id"])["result"]
    assert full == long_result[:4000] and len(full) > 3500
    assert out["handoff"]["transcript_ref"] == "stored-77"
