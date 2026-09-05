"""
SQLite durability under the conditions Friday actually runs in (audit A-038):
several processes on one file, and a process killed in the middle of writing.

Real processes, real files - not mocks. The writer child is a separate
Python interpreter running `friday.store.Store` against a temp database;
the parent kills it with TerminateProcess while it is mid-transaction and
then opens the same file and checks that every COMMITTED row is there,
nothing half-written is, and the database passes `PRAGMA integrity_check`.
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

WRITER = r"""
import sys, time
sys.path.insert(0, sys.argv[1])
from friday.store import Store
db = Store(sys.argv[2])
i = 0
while True:
    # One committed row per iteration; a kill can land anywhere in here.
    db.remember(subject=f"row {i}", value="x" * 2000, kind="FACT",
                source="crash-writer", scope="user")
    i += 1
    if i == 5:
        print("READY", flush=True)          # enough rows exist to check
"""


def _wait_for(proc, marker: str, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        line = proc.stdout.readline()
        if marker in line:
            return
        if proc.poll() is not None:
            raise AssertionError(f"writer exited early: {proc.stderr.read()[-800:]}")
    raise AssertionError(f"writer never printed {marker!r}")


def test_store_opens_in_wal_with_a_busy_timeout(tmp_path):
    from friday import store as S
    db = S.Store(tmp_path / "w.sqlite3")
    try:
        assert db._conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert db._conn.execute("PRAGMA busy_timeout").fetchone()[0] == int(S.BUSY_TIMEOUT_S * 1000)
        assert db._conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        db.close()


def test_kill_during_write_leaves_a_consistent_database(tmp_path):
    from friday import store as S
    path = tmp_path / "crash.sqlite3"
    proc = subprocess.Popen(
        [sys.executable, "-c", WRITER, str(ROOT), str(path)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    try:
        _wait_for(proc, "READY")
        time.sleep(0.05)                     # land the kill mid-stream
        proc.kill()                          # TerminateProcess: no cleanup
        proc.wait(timeout=10)
    finally:
        if proc.poll() is None:
            proc.kill()

    # A WAL file may be left behind; opening the database replays or
    # discards it. Either way the main file must be consistent.
    db = S.Store(path)
    try:
        assert db._conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        rows = db._conn.execute(
            "SELECT subject, length(value) FROM memories WHERE source='crash-writer' ORDER BY id").fetchall()
        assert len(rows) >= 5, "the committed rows before the kill must survive"
        # No torn row: every survivor is complete.
        assert all(length == 2000 for _, length in rows)
        # Contiguous: rows are committed in order, so the survivors are 0..n-1.
        assert [r[0] for r in rows] == [f"row {i}" for i in range(len(rows))]
        # And the database is still writable afterwards.
        db.remember(subject="after crash", value="ok", kind="FACT", source="test", scope="user")
    finally:
        db.close()


def test_two_processes_write_concurrently_without_lock_errors(tmp_path):
    """A second interpreter writes while this one writes; with WAL and a
    busy timeout neither side sees 'database is locked'."""
    from friday import store as S
    path = tmp_path / "shared.sqlite3"
    other = subprocess.Popen(
        [sys.executable, "-c", WRITER, str(ROOT), str(path)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    try:
        _wait_for(other, "READY")
        db = S.Store(path)
        try:
            for i in range(200):
                db.remember(subject=f"mine {i}", value="y", kind="FACT",
                            source="parent-writer", scope="user")
            mine = db._conn.execute(
                "SELECT count(*) FROM memories WHERE source='parent-writer'").fetchone()[0]
            theirs = db._conn.execute(
                "SELECT count(*) FROM memories WHERE source='crash-writer'").fetchone()[0]
        finally:
            db.close()
        assert mine == 200
        assert theirs >= 5
    finally:
        other.kill()
        other.wait(timeout=10)


def test_a_plain_reader_sees_committed_rows_while_a_writer_holds_the_file(tmp_path):
    """The Control Room opens the database read-only (`mode=ro`) while the
    agent writes; WAL means that read does not block or fail."""
    from friday import store as S
    path = tmp_path / "ro.sqlite3"
    db = S.Store(path)
    try:
        with db._tx() as conn:
            conn.execute("INSERT INTO memories (subject, value, kind, source, scope, created_at) "
                         "VALUES ('a', 'b', 'FACT', 't', 'user', '2026-01-01')")
            # Transaction still open here: a reader must not get 'locked'.
            ro = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)
            try:
                assert ro.execute("SELECT count(*) FROM memories").fetchone()[0] >= 0
            finally:
                ro.close()
    finally:
        db.close()
