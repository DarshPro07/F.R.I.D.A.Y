"""
One rule for every SQLite ledger that opens a connection per call.

`sqlite3.Connection` as a context manager commits or rolls back - it does
NOT close. Written as `with sqlite3.connect(...) as db:` the connection
lives until the garbage collector finds it: on Windows that is a kernel
handle per call, and the A-051 soak measured it as a monotonic handle leak
(seven ledgers, ~3.5 handles per Hermes crash cycle). `ledger_connection`
keeps the commit/rollback semantics and closes on exit.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Iterator


class LedgerConnection:
    """A sqlite3 connection whose `with` block closes it afterwards."""

    __slots__ = ("_conn",)

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def __enter__(self) -> sqlite3.Connection:
        return self._conn

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if exc_type is None:
                self._conn.commit()
            else:
                self._conn.rollback()
        finally:
            self._conn.close()

    def __getattr__(self, name: str):
        return getattr(self._conn, name)


def ledger_connection(path, *, timeout: float = 10, row_factory=sqlite3.Row,
                      **kwargs) -> LedgerConnection:
    conn = sqlite3.connect(str(path), timeout=timeout, **kwargs)
    if row_factory is not None:
        conn.row_factory = row_factory
    return LedgerConnection(conn)
