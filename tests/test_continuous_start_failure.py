"""
The error path of ContinuousTaskExecutor.start (found by the CHAOS-001 suite).

When driving a run fails and releasing its lease on the error path fails too -
a write lock held on the database - the caller must still see the original
failure, and the failed release must be said out loud: the lease then stays
until it expires, and nobody should hunt for a lock error that was only the
second thing to go wrong.
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3

import pytest

from friday import contracts as c
from friday import objectives as O
from friday.continuous import ContinuousTaskExecutor
from friday.store import Store


def test_a_failed_lease_release_does_not_mask_the_drive_failure(tmp_path, monkeypatch, caplog):
    store = Store(tmp_path / "start.db")
    run_id = O.compile_objective(
        store, request="report the system", objective_summary="info",
        tasks=[{"capability": "system_get_info", "arguments": {}}],
        manifest=[{"id": "system_get_info", "description": "info"}],
        provenance=c.PERSON, attended=True)["run_id"]
    executor = ContinuousTaskExecutor(store, call_capability=lambda *a, **k: None)

    async def drive_fails(run):
        raise RuntimeError("the drive failed")

    def release_locked(run):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(executor, "_drive_until_done", drive_fails)
    monkeypatch.setattr(executor, "release", release_locked)
    with caplog.at_level(logging.ERROR, logger="friday-continuous"), \
            pytest.raises(RuntimeError, match="the drive failed"):
        asyncio.run(executor.start(run_id))
    assert any("could not release" in r.getMessage() for r in caplog.records), \
        [r.getMessage() for r in caplog.records]
    assert run_id not in executor._driving
