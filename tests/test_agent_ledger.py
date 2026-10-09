"""
AGENT-001 slice 1: every agent FRIDAY spawns leaves a durable ledger record.

The claim: team activity is derived from the ledger, so it is still correct
after a restart. A model saying "I started a worker" is not a worker; only a
start event written by the executor puts a run in `active()`.
"""

from __future__ import annotations

import sqlite3

import pytest

from friday.agent_ledger import AgentLedger, LedgerError
from friday.executors import runs as R
from friday.executors.claude_code import TaskBundle
from friday.store import Store

ROUTE = "anthropic/claude-sonnet/5.5/subscription"


@pytest.fixture
def db(tmp_path):
    return tmp_path / "ledger.sqlite3"


@pytest.fixture
def store(db):
    s = Store(db)
    yield s
    s.close()


@pytest.fixture
def ledger(store):
    return AgentLedger(store)


def _tree_of_three(ledger):
    ledger.start("P", task="parent", workspace="/w", executor_id="claude_code",
                 role_id="lead", model_route=ROUTE)
    ledger.start("C1", task="child one", workspace="/w/1",
                 executor_id="claude_code", parent_run_id="P", role_id="eng")
    ledger.start("C2", task="child two", workspace="/w/2",
                 executor_id="claude_code", parent_run_id="P", role_id="qa")


def test_a_run_round_trips_with_its_identity_and_events(ledger):
    ledger.start("A", task="fix the bug", workspace="/w", executor_id="claude_code",
                 role_id="eng", model_route=ROUTE)
    ledger.event("A", "action", "edited store.py", ["file:friday/store.py"])
    run = ledger.run("A")
    assert run["run_id"] == "A"
    assert run["parent_run_id"] in (None, "")
    assert (run["role_id"], run["executor_id"], run["task"], run["workspace"]) \
        == ("eng", "claude_code", "fix the bug", "/w")
    assert run["model_route"] == ROUTE
    kinds = [e["kind"] for e in ledger.events("A")]
    assert kinds == ["claimed", "action"]
    assert ledger.events("A")[-1]["evidence_refs"] == ["file:friday/store.py"]


def test_parent_and_children_form_a_tree(ledger):
    _tree_of_three(ledger)
    assert [r["run_id"] for r in ledger.children("P")] == ["C1", "C2"]
    tree = ledger.tree("P")
    assert tree["run"]["run_id"] == "P"
    assert [c["run"]["run_id"] for c in tree["children"]] == ["C1", "C2"]


def test_reopening_the_same_file_gives_the_identical_tree(db):
    first = Store(db)
    _tree_of_three(AgentLedger(first))
    before = AgentLedger(first).tree("P")
    first.close()

    second = Store(db)
    try:
        assert AgentLedger(second).tree("P") == before
    finally:
        second.close()


def test_unknown_parent_raises(ledger):
    with pytest.raises(LedgerError):
        ledger.start("X", task="t", workspace="/w", executor_id="claude_code",
                     parent_run_id="NOPE")
    assert ledger.run("X") is None


def test_a_malformed_model_route_raises(ledger):
    with pytest.raises(LedgerError):
        ledger.start("X", task="t", workspace="/w", executor_id="claude_code",
                     model_route="sonnet")


def test_a_run_nobody_started_is_never_active(ledger):
    """A model can claim a run; only a worker start event makes it active."""
    ledger.start("CLAIMED", task="t", workspace="/w", executor_id="claude_code")
    assert ledger.run("CLAIMED")["status"] == "STARTING"
    assert ledger.active() == []


def test_a_started_run_is_active_while_its_process_lives(store, ledger, monkeypatch):
    monkeypatch.setattr(R, "process_alive", lambda pid, started_at=None: True)
    bundle = TaskBundle(goal="g", workspace="/w", run_id="DEV-1")
    manager = R.RunManager(store)
    manager.open(bundle)
    assert ledger.active() == []
    manager.running("DEV-1", pid=4321)
    assert [r["run_id"] for r in ledger.active()] == ["DEV-1"]
    assert [e["kind"] for e in ledger.events("DEV-1")][-1] == "worker_started"


def test_a_live_row_with_a_dead_pid_reads_interrupted_after_restart(db, monkeypatch):
    monkeypatch.setattr(R, "process_alive", lambda pid, started_at=None: True)
    first = Store(db)
    manager = R.RunManager(first)
    manager.open(TaskBundle(goal="g", workspace="/w", run_id="DEV-2"))
    manager.running("DEV-2", pid=4321)
    assert AgentLedger(first).run("DEV-2")["status"] == "RUNNING"
    first.close()

    monkeypatch.setattr(R, "process_alive", lambda pid, started_at=None: False)
    second = Store(db)
    try:
        again = AgentLedger(second)
        assert again.run("DEV-2")["status"] == "INTERRUPTED"
        assert again.active() == []
        assert again.events("DEV-2")[-1]["kind"] == "interrupted"
    finally:
        second.close()


def test_a_finished_run_leaves_active_with_its_outcome(store, ledger, monkeypatch):
    monkeypatch.setattr(R, "process_alive", lambda pid, started_at=None: True)
    manager = R.RunManager(store)
    manager.open(TaskBundle(goal="g", workspace="/w", run_id="DEV-3"))
    manager.running("DEV-3", pid=4321)
    manager.cancelled("DEV-3")
    assert ledger.active() == []
    run = ledger.run("DEV-3")
    assert run["status"] == "CANCELLED" and run["ended_at"]


def test_every_event_carries_evidence(ledger):
    ledger.start("A", task="t", workspace="/w", executor_id="claude_code")
    with pytest.raises(LedgerError):
        ledger.event("A", "action", "did something", [])
    with pytest.raises(LedgerError):
        ledger.event("NOPE", "action", "did something", ["x"])


def test_events_are_append_only(store, ledger):
    ledger.start("A", task="t", workspace="/w", executor_id="claude_code")
    for sql in ("UPDATE agent_events SET message='rewritten'",
                "DELETE FROM agent_events"):
        with pytest.raises(sqlite3.DatabaseError):
            with store._tx() as conn:
                conn.execute(sql)
    assert ledger.events("A")[0]["message"] != "rewritten"
    assert not any(hasattr(AgentLedger, n) for n in
                   ("delete_event", "update_event", "remove", "delete"))
