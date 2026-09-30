"""
Tests never write the owner's live state (PRD v7 TEST-ISOLATION-001).

The session runs against its own state root (tests/conftest.py sets
FRIDAY_HOME before anything imports FRIDAY), so the store, the audit log, the
workspace, the nonce and cooldown files and the logs all live in a temporary
directory - for the test process and for every process it starts, which
inherits the environment. Opening a SQLite store under the repository's live
data directory is refused outright.
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

LIVE_DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def test_the_session_runs_on_its_own_state_root():
    from friday import config

    assert config.STATE_ROOT == Path(os.environ["FRIDAY_HOME"])
    assert config.DATA_DIR != LIVE_DATA_DIR
    assert LIVE_DATA_DIR not in config.DATA_DIR.parents


def test_a_process_a_test_starts_inherits_the_isolated_root():
    child = subprocess.run(
        [sys.executable, "-c", "from friday import config; print(config.DATA_DIR)"],
        capture_output=True, text=True, timeout=60, env=dict(os.environ))
    assert child.returncode == 0, child.stderr
    data_dir = Path(child.stdout.strip())
    assert data_dir != LIVE_DATA_DIR and LIVE_DATA_DIR not in data_dir.parents


def test_opening_a_live_store_is_refused(monkeypatch):
    # Read-only URIs on files that do not exist: without the guard these fail
    # differently and still create nothing in the live directory - so neither
    # this test nor a mutation of the guard can ever open the real store.
    probe = (LIVE_DATA_DIR / "test-isolation-probe.sqlite3").as_posix()
    with pytest.raises(sqlite3.OperationalError, match="TEST-ISOLATION-001"):
        sqlite3.connect(f"file:{probe}?mode=ro", uri=True)
    # A relative path is judged where it resolves, from the repository root.
    monkeypatch.chdir(LIVE_DATA_DIR.parent)
    with pytest.raises(sqlite3.OperationalError, match="TEST-ISOLATION-001"):
        sqlite3.connect("file:data/test-isolation-probe-2.sqlite3?mode=ro", uri=True)
    assert not (LIVE_DATA_DIR / "test-isolation-probe.sqlite3").exists()
    assert not (LIVE_DATA_DIR / "test-isolation-probe-2.sqlite3").exists()
