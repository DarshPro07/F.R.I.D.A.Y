"""
S4b: a delegated task hands back structured data, not just a spoken
sentence. Before this slice `friday/handoff.py` did not exist and
`hermes_work_runs` had no `handoff` column.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from friday import hermes_bridge as hb
from friday.handoff import Handoff

FAKE = str(Path(__file__).parent / "fake_hermes_gateway.py")


@pytest.fixture()
def db(monkeypatch, tmp_path):
    path = tmp_path / "t.sqlite3"
    monkeypatch.setenv("ADA_DB", str(path))
    from friday.toolsets import memory as M
    M.reset_store(None)
    yield path
    M.reset_store(None)


def test_handoff_from_work_run_has_every_field():
    record = {"work_run_id": "hermes-abc", "model": "claude", "status": hb.COMPLETE,
              "task": "check the deploy logs", "result": "logs are clean"}
    handoff = Handoff.from_work_run(record, {"tools": 2, "last": "reading log.txt"})
    for f in ("task_id", "agent", "role", "status", "summary", "files_read",
              "files_changed", "tests_run", "verification", "decisions",
              "assumptions", "failed_attempts", "residual_risks", "blockers",
              "memory_candidates", "skill_candidates", "next_action"):
        assert hasattr(handoff, f)
    assert handoff.task_id == "hermes-abc"
    assert handoff.agent == "claude"
    assert handoff.status == hb.COMPLETE
    assert "logs are clean" in handoff.summary


def test_handoff_never_invents_files_or_tests():
    record = {"work_run_id": "hermes-xyz", "status": hb.COMPLETE,
              "task": "edit widget.py", "result": "done"}
    handoff = Handoff.from_work_run(record, {"tools": 3, "last": "editing widget.py"})
    # The progress dict never gave a real path list - honest emptiness,
    # not a guess extracted from the truncated tool description.
    assert handoff.files_read == ()
    assert handoff.files_changed == ()
    assert handoff.tests_run == ()


def test_secret_shaped_summary_is_refused():
    record = {"work_run_id": "hermes-sec", "status": hb.COMPLETE,
              "task": "rotate the key", "result": "sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123"}
    handoff = Handoff.from_work_run(record, {})
    assert handoff.summary == ""


def test_handoff_is_stored_on_the_work_run_and_spoken(db):
    log = hb.WorkRunLog(db)
    run_id = log.create(task="check the deploy logs")
    log.update(run_id, status=hb.COMPLETE, result="all clear", route_reason="trivial")
    record = log.get(run_id)
    assert record["handoff"]
    handoff = Handoff.from_json(record["handoff"])
    assert handoff.task_id == run_id
    spoken = hb.render_completion(record)
    assert "all clear" in spoken
    assert "trivial" in spoken


def test_on_terminal_stays_idempotent(db):
    log = hb.WorkRunLog(db)
    run_id = log.create(task="check the deploy logs")
    log.update(run_id, status=hb.COMPLETE, result="all clear")
    first = log.get(run_id)["handoff"]
    log.on_terminal(log.get(run_id))
    second = log.get(run_id)["handoff"]
    assert first == second
