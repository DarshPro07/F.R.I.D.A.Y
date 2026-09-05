"""
Every statement on the Store's one shared connection is serialised and
materialised (A-051 soak finding: "database is locked" once in 1,435
cycles, from a product path).

The shape, deterministic: a read on the shared connection whose cursor is
not exhausted keeps its statement active - and an active statement holds
the WAL read snapshot. ANY other connection then commits (another
process, or the second `Store` in this process that `memory.store()`
hands out for the same file). The next write on the first connection is
refused at once with "database is locked": SQLITE_BUSY_SNAPSHOT, which
`busy_timeout` never waits out because waiting cannot make a stale
snapshot current. 43 read sites in `store.py` and the continuity engine's
thread pool read the shared connection bare. `_SerializedConnection`
executes every statement under the store lock and materialises the rows,
so no statement - and no snapshot - outlives the call.
"""
from __future__ import annotations

import sqlite3

from friday.store import Store


def _raw(store):
    return getattr(store._conn, "_conn", store._conn)


def _other_commit(db) -> None:
    other = sqlite3.connect(str(db), timeout=10)
    other.execute("INSERT INTO memories (subject, value, kind, scope, source, confidence, created_at, "
                  "evidence_count, memory_type, project_scope, source_ref, retention_policy, importance) "
                  "VALUES ('other','v','FACT','user','other',1.0,'2026-01-01T00:00:00+00:00',1,'semantic','','','',0.5)")
    other.commit()
    other.close()


def test_the_bare_shape_is_the_bug(tmp_path):
    """What the store used to do, stated so the fix is not ceremony: a
    read cursor left mid-way + any other commit = the next write fails."""
    db = tmp_path / "bare.sqlite3"
    store = Store(db)
    for i in range(300):
        store.remember(f"s{i}", "v", kind="FACT", source="t")
    cur = _raw(store).execute("SELECT subject FROM memories")
    cur.fetchone()                                   # statement stays active: snapshot held
    _other_commit(db)
    try:
        with __import__("pytest").raises(sqlite3.OperationalError, match="locked"):
            store.remember("after", "v", kind="FACT", source="t")
    finally:
        cur.close()
        store.close()


def test_a_read_through_the_store_never_leaves_a_snapshot_open(tmp_path):
    db = tmp_path / "serialized.sqlite3"
    store = Store(db)
    for i in range(300):
        store.remember(f"s{i}", "v", kind="FACT", source="t")
    # Exactly the pattern the 43 read sites use - and stop reading early.
    rows = store._conn.execute("SELECT subject FROM memories")
    assert rows.fetchone() is not None
    _other_commit(db)
    store.remember("after", "v", kind="FACT", source="t")        # must not raise
    assert store._conn.execute("SELECT COUNT(*) FROM memories WHERE subject='after'").fetchone()[0] == 1
    store.close()


def test_rows_look_like_the_cursor_callers_use(tmp_path):
    store = Store(tmp_path / "s.sqlite3")
    store.remember("a", "1", kind="FACT", source="t")
    store.remember("b", "2", kind="FACT", source="t")
    rows = store._conn.execute("SELECT subject FROM memories ORDER BY subject")
    assert rows.fetchone()["subject"] == "a"
    assert [r["subject"] for r in rows] == ["b"]
    assert rows.fetchone() is None
    ins = store._conn.execute("INSERT INTO memories (subject, value, kind, scope, source, confidence, created_at, "
                              "evidence_count, memory_type, project_scope, source_ref, retention_policy, importance) "
                              "VALUES ('c','3','FACT','user','t',1.0,'2026-01-01T00:00:00+00:00',1,'semantic','','','',0.5)")
    assert ins.lastrowid > 0 and ins.rowcount == 1
    store._conn.commit()
    assert store._conn.execute("SELECT COUNT(*) FROM memories").fetchall()[0][0] == 3
    store.close()
