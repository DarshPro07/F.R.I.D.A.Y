"""
The per-call ledger connections close (A-051 finding).

`sqlite3.Connection` as a context manager commits/rolls back and does NOT
close. Seven ledgers wrote `with self._connect() as db:` and left every
connection to the garbage collector - a kernel handle per call on Windows,
measured as a monotonic handle leak in the A-051 soak (~3.5 per Hermes
crash cycle). `friday.dbconn.ledger_connection` is the one fix; this test
holds it for every ledger that uses the pattern, by counting the open
handles on the ledger file itself.
"""
from __future__ import annotations

import gc
import sqlite3

import psutil
import pytest

from friday import dbconn


def _open_count(path) -> int:
    gc.collect()
    target = str(path).lower()
    return sum(1 for f in psutil.Process().open_files() if f.path.lower() == target)


def test_ledger_connection_closes_on_exit_and_commits(tmp_path):
    db = tmp_path / "l.sqlite3"
    with dbconn.ledger_connection(db) as conn:
        conn.execute("CREATE TABLE t (x)")
        conn.execute("INSERT INTO t VALUES (1)")
    assert _open_count(db) == 0
    with dbconn.ledger_connection(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 1
    assert _open_count(db) == 0


def test_ledger_connection_rolls_back_on_error_and_still_closes(tmp_path):
    db = tmp_path / "l.sqlite3"
    with dbconn.ledger_connection(db) as conn:
        conn.execute("CREATE TABLE t (x)")
    with pytest.raises(RuntimeError):
        with dbconn.ledger_connection(db) as conn:
            conn.execute("INSERT INTO t VALUES (1)")
            raise RuntimeError("boom")
    assert _open_count(db) == 0
    with dbconn.ledger_connection(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 0


def test_a_bare_sqlite3_connection_would_have_leaked(tmp_path):
    """The behaviour being fixed, stated so the fix is not mistaken for
    ceremony: the stdlib context manager leaves the handle open."""
    db = tmp_path / "bare.sqlite3"
    conn = sqlite3.connect(db)
    with conn:
        conn.execute("SELECT 1")
    # psutil de-duplicates by path; the point is the handle is still there.
    assert _open_count(db) == 1
    conn.close()
    assert _open_count(db) == 0


@pytest.mark.parametrize("make", [
    lambda p: __import__("friday.hermes_bridge", fromlist=["WorkRunLog"]).WorkRunLog(p),
    lambda p: __import__("friday.execution_economics", fromlist=["RouteOutcomes"]).RouteOutcomes(p),
    lambda p: __import__("friday.model_gateway", fromlist=["GatewayTelemetry"]).GatewayTelemetry(p),
    lambda p: __import__("friday.skill_ladder", fromlist=["SkillLadder"]).SkillLadder(p),
    lambda p: __import__("friday.user_policy", fromlist=["UserPolicy"]).UserPolicy(p),
    lambda p: __import__("friday.orgplane", fromlist=["LocalControlPlane"]).LocalControlPlane(p),
], ids=["WorkRunLog", "RouteOutcomes", "GatewayTelemetry", "SkillLadder", "UserPolicy", "LocalControlPlane"])
def test_every_per_call_ledger_leaves_no_handle_behind(tmp_path, make):
    db = tmp_path / "ledger.sqlite3"
    ledger = make(db)
    for _ in range(10):
        with ledger._connect() as conn:
            conn.execute("SELECT 1").fetchone()
    assert _open_count(db) == 0, "a ledger connection outlived its with-block"
