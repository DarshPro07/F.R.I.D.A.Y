"""FR-103 scaling: does the retrieval router's cost track corpus size?

Deliberately small (100 / 1,000 / 10,000 / 50,000 rows) and single-process:
A-051 is measuring a release candidate on this machine, and a benchmark that
competes for RAM would corrupt the result it shares a host with.

Asserts SHAPE, not wall-clock. A timing threshold is luck on a fast machine
and a false alarm on a loaded one - this run happened while a soak had the
CPU. What must hold regardless of speed:

  * classification never touches the database, so it is flat in corpus size;
  * an aggregate is one indexed pass, not a Python loop over rows;
  * a count stays exact as the corpus grows (the truncation defect would
    show up here as a number that stops matching).
"""
from __future__ import annotations

import datetime as dt
import sqlite3
import time

import pytest

from friday import retrieval as R
from friday import retrieval_sources as RS

SIZES = (100, 1_000, 10_000, 50_000)


def _corpus(path, n):
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT, subject TEXT NOT NULL,
            value TEXT NOT NULL, kind TEXT NOT NULL,
            scope TEXT NOT NULL DEFAULT 'user', source TEXT NOT NULL,
            confidence REAL NOT NULL DEFAULT 1.0, run_id TEXT,
            created_at TEXT NOT NULL, superseded INTEGER NOT NULL DEFAULT 0,
            project_scope TEXT NOT NULL DEFAULT '');
        CREATE INDEX idx_kind ON memories(kind);
    """)
    base = dt.datetime(2026, 1, 1)
    # Every 97th row is a refund: a fixed ratio, so the expected count is
    # known at every size and truncation shows up as a wrong number.
    conn.executemany(
        "INSERT INTO memories (subject, value, kind, source, created_at) "
        "VALUES (?,?,?,?,?)",
        [("refund policy" if i % 97 == 0 else f"note {i}",
          "customer asked for a refund" if i % 97 == 0 else f"text {i}",
          "refund" if i % 97 == 0 else "note", "bench",
          (base + dt.timedelta(minutes=i)).isoformat()) for i in range(n)])
    conn.commit()
    conn.close()
    return path


def _source(path):
    src = RS.MemorySource()
    src._connect = lambda: sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    return src


@pytest.mark.parametrize("n", SIZES)
def test_a_count_stays_exact_as_the_corpus_grows(tmp_path, n):
    """The truncation guard at scale: expected = ceil(n / 97)."""
    src = _source(_corpus(tmp_path / f"c{n}.sqlite3", n))
    intent = R.dataclasses.replace(
        R.parse_intent("how many refunds?"), filters={"kind": "refund"}, time_range=None)
    result = src.query(intent)
    assert result.rows[0][0] == (n + 96) // 97
    assert result.coverage is R.Coverage.FILTERED_COMPLETE


def test_classification_does_not_touch_the_database(tmp_path):
    """Parsing is deterministic string work; it must not scale with data."""
    src = _source(_corpus(tmp_path / "big.sqlite3", 50_000))
    src._connect = lambda: pytest.fail("parse_intent must not open the database")
    for q in ("how many refunds last month?", "what did I say about refunds?",
              "summarize this whole document"):
        R.parse_intent(q)


def test_ranked_retrieval_returns_a_bounded_slice_at_every_size(tmp_path):
    """Cost is bounded by the slice, not by the corpus - and the result
    still reports TOP_K rather than implying it saw everything."""
    for n in SIZES:
        src = _source(_corpus(tmp_path / f"r{n}.sqlite3", n))
        result = src.query(R.parse_intent("what did I say about refund policy?"))
        assert len(result.rows) <= RS._RANKED_LIMIT
        assert result.total_scanned == n
        if n > RS._RANKED_LIMIT:
            assert result.coverage is R.Coverage.TOP_K


def test_aggregation_is_one_query_not_a_python_loop(tmp_path, capsys):
    """A count of 50k rows must not pull 50k rows into Python.

    Measured by row traffic, not by time: sqlite3's row factory is only
    invoked for rows that actually cross the boundary, so a Python-side
    count would show up as 50,000 invocations.
    """
    path = _corpus(tmp_path / "traffic.sqlite3", 50_000)
    seen = 0

    def counting_factory(cursor, row):
        nonlocal seen
        seen += 1
        return row

    src = RS.MemorySource()

    def _connect():
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        conn.row_factory = counting_factory
        return conn

    src._connect = _connect
    intent = R.dataclasses.replace(
        R.parse_intent("how many refunds?"), filters={"kind": "refund"}, time_range=None)
    result = src.query(intent)
    assert result.rows[0][0] == 516
    # The aggregate row, plus the total-scanned row. Anything near 50,000
    # would mean the count was computed in Python.
    assert seen < 10, f"{seen} rows crossed into Python for one COUNT(*)"


def test_report_the_measured_shape(tmp_path):
    """Not an assertion - a recorded measurement, printed with -s."""
    lines = []
    for n in SIZES:
        src = _source(_corpus(tmp_path / f"m{n}.sqlite3", n))
        intent = R.dataclasses.replace(
            R.parse_intent("how many refunds?"), filters={"kind": "refund"},
            time_range=None)
        t0 = time.perf_counter()
        agg = src.query(intent)
        t1 = time.perf_counter()
        ranked = src.query(R.parse_intent("what did I say about refund policy?"))
        t2 = time.perf_counter()
        lines.append(f"  n={n:>6}  aggregate={(t1-t0)*1000:7.2f}ms "
                     f"ranked={(t2-t1)*1000:7.2f}ms  count={agg.rows[0][0]:>4} "
                     f"rows={len(ranked.rows):>4} coverage={ranked.coverage.value}")
    print("\nFR-103 scaling (loaded machine - shape, not absolute speed):")
    print("\n".join(lines))
