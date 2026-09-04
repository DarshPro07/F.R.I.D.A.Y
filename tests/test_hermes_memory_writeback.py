"""
S2: a terminal Hermes run must reach Friday's SHARED memory, so (a) her next
spoken turn can answer "what did Hermes just do?" and (b) the next
TaskBundle.with_memory() for a follow-up goal already knows the result.

Before this slice nothing writes it: hermes_bridge.py only READS
memory_stack at the TaskBundle.with_memory() call site, and
memory_stack.log_result() has zero production callers.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from friday import hermes_bridge as hb

FAKE = str(Path(__file__).parent / "fake_hermes_gateway.py")


@pytest.fixture()
def db(monkeypatch, tmp_path):
    """One temp sqlite file for both WorkRunLog and store()/memory_stack -
    they must share it, the way production shares one ada.sqlite3."""
    path = tmp_path / "t.sqlite3"
    monkeypatch.setenv("ADA_DB", str(path))
    from friday.toolsets import memory as M
    M.reset_store(None)
    yield path
    M.reset_store(None)


def _terminal_record(db_path, *, status=hb.COMPLETE, result="did the thing",
                      task="check the deploy logs"):
    log = hb.WorkRunLog(db_path)
    run_id = log.create(task=task)
    log.update(run_id, status=status, result=result, model="fake-model",
               route_reason="trivial")
    return log, run_id


def test_terminal_run_is_written_to_shared_memory(db):
    log, run_id = _terminal_record(db)
    from friday.toolsets.memory import store
    rows = store().decisions("hermes")
    assert any(run_id in (r.get("source") or "") for r in rows)
    assert "did the thing" in rows[0]["decision"]


def test_outcome_visible_to_next_hermes_bundle(db):
    _terminal_record(db, task="rotate the API keys", result="rotated all 4 keys")
    bundle = hb.TaskBundle(goal="what's next after the key rotation?").with_memory()
    assert "rotated all 4 keys" in bundle.memory_context


def test_outcome_visible_to_voice_brain_context(db):
    from friday import voice_brain as V
    # _recent_turns() drops the LAST row (reply() records the live question
    # before building history) and then strips any LEADING model turn
    # (Gemini requires history to open on the user's side) - so mimic the
    # real sequence: a user turn opened this, Hermes's outcome landed, then
    # the user speaks again. That is also just how it happens in production.
    V._remember_turn("user", "please delegate the backup to hermes")
    _terminal_record(db, task="back up the database", result="backup written to s3")
    V._remember_turn("user", "did that finish?")
    assert any("backup written to s3" in text for _, text in V._recent_turns())
    assert "backup written to s3" in V._memory_context("what happened with the backup")


def test_outcome_with_secret_shape_is_refused(db):
    _terminal_record(db, result="here is the key: sk-abcdefghijklmnopqrstuvwx")
    from friday.toolsets.memory import store
    assert store().decisions("hermes") == []
    assert store().recent_messages(10) == []


def test_write_is_idempotent_per_work_run(db):
    log, run_id = _terminal_record(db, result="ran once")
    record = log.get(run_id)
    log.on_terminal(record)   # direct second call
    log.on_terminal(record)   # direct third call
    from friday.toolsets.memory import store
    decisions = [r for r in store().decisions("hermes")
                 if run_id in (r.get("source") or "")]
    assert len(decisions) == 1
    messages = [m for m in store().recent_messages(10)
                if "ran once" in (m.get("content") or "")]
    assert len(messages) == 1


def test_one_complete_run_through_the_fake_gateway_reaches_both_readers(db, monkeypatch):
    """The 05-slices.md tracer bullet: a REAL run through the scripted
    gateway (not a direct WorkRunLog.update() call), proving the
    message.complete event handler goes through the same choke point as
    the direct-call tests above."""
    from friday import voice_brain as V
    V._remember_turn("user", "please inspect the project")
    log = hb.WorkRunLog(db)
    supervisor = hb.HermesSupervisor(log=log, command=[sys.executable, FAKE], profile="")
    supervisor.READY_TIMEOUT = 20
    try:
        out = supervisor.delegate(
            hb.TaskBundle(goal="inspect the project, do not modify"),
            wait=True, turn_timeout=30)
        assert out["result"]["status"] == hb.COMPLETE
    finally:
        supervisor.stop()
    V._remember_turn("user", "how did that go?")
    assert any("DONE:" in text for _, text in V._recent_turns())
