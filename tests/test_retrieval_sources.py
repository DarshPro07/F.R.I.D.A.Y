"""FR-103: the memory source and router, against a real SQLite database.

These build an actual `memories` table rather than mocking one, because the
defect being guarded is a SQL-shaped defect: a window in the wrong place, a
filter that silently does not exist, a count taken over a sample. A mock
would agree with whatever the implementation believes.
"""
from __future__ import annotations

import datetime as dt
import sqlite3

import pytest

from friday import retrieval as R
from friday import retrieval_sources as RS


SCHEMA = """
CREATE TABLE memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject TEXT NOT NULL, value TEXT NOT NULL, kind TEXT NOT NULL,
    scope TEXT NOT NULL DEFAULT 'user', source TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 1.0, run_id TEXT,
    created_at TEXT NOT NULL, superseded INTEGER NOT NULL DEFAULT 0,
    project_scope TEXT NOT NULL DEFAULT ''
);
"""

#: A database from before the project_scope column migration.
LEGACY_SCHEMA = SCHEMA.replace(",\n    project_scope TEXT NOT NULL DEFAULT ''", "")


def _seed(path, schema=SCHEMA):
    """500 rows, refunds at 10/201/350/499, spread across July-September."""
    conn = sqlite3.connect(path)
    conn.executescript(schema)
    base = dt.datetime(2026, 7, 1)
    for i in range(500):
        refund = i in (10, 201, 350, 499)
        conn.execute(
            "INSERT INTO memories (subject, value, kind, scope, source, created_at) "
            "VALUES (?,?,?,?,?,?)",
            ("refund policy" if refund else f"note {i}",
             "customer asked for a refund" if refund else f"unrelated text {i}",
             "refund" if refund else "note",
             "preferences" if refund else "episode", "test",
             (base + dt.timedelta(hours=i * 4)).isoformat()))
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def db(tmp_path):
    return _seed(tmp_path / "mem.sqlite3")


def _source_over(path):
    src = RS.MemorySource()
    src._connect = lambda: sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    return src


@pytest.fixture
def source(db):
    return _source_over(db)


class TestAggregationEnumerates:
    def test_a_count_finds_rows_outside_any_recency_window(self, source):
        intent = R.dataclasses.replace(
            R.parse_intent("how many refunds were there?"),
            filters={"kind": "refund"}, time_range=None)
        result = source.query(intent)
        assert result.rows[0][0] == 4
        assert result.coverage is R.Coverage.FILTERED_COMPLETE
        assert result.supports_a_numeric_claim is True

    def test_the_fixture_really_does_defeat_a_newest_200_window(self, db):
        """Without this, the test above could pass on a corpus too small to
        expose the defect - the trap that made an earlier bounded-graph test
        pass against its own plant."""
        conn = sqlite3.connect(db)
        (windowed,) = conn.execute(
            "SELECT COUNT(*) FROM (SELECT kind FROM memories ORDER BY id DESC "
            "LIMIT 200) WHERE kind='refund'").fetchone()
        (truth,) = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE kind='refund'").fetchone()
        conn.close()
        assert windowed == 2 and truth == 4

    def test_an_unfiltered_count_is_complete_not_filtered_complete(self, source):
        intent = R.parse_intent("how many memories are there?")
        result = source.query(R.dataclasses.replace(intent, time_range=None))
        assert result.rows[0][0] == 500
        assert result.coverage is R.Coverage.COMPLETE

    def test_a_time_filtered_count_counts_only_that_window(self, source):
        now = dt.datetime(2026, 10, 1)
        result = source.query(R.parse_intent("how many memories in August?", now=now))
        assert 0 < result.rows[0][0] < 500
        assert result.coverage is R.Coverage.FILTERED_COMPLETE

    def test_the_months_partition_the_corpus_exactly(self, source):
        """The half-open range, proven on real rows rather than on a string:
        if any boundary were closed, the total would exceed 500."""
        now = dt.datetime(2026, 10, 1)
        got = [source.query(R.parse_intent(f"how many in {m}?", now=now)).rows[0][0]
               for m in ("July", "August", "September")]
        assert sum(got) == 500, got

    def test_group_count_returns_one_row_per_group(self, source):
        intent = R.RetrievalIntent(strategy=R.RetrievalStrategy.SQL_AGGREGATE,
                                   aggregate="group_count", group_by=("kind",))
        result = source.query(intent)
        assert {kind: n for kind, n in result.rows} == {"refund": 4, "note": 496}


class TestCoverageStatesWhatWasNotDone:
    def test_a_filter_with_no_column_downgrades_coverage(self, source):
        """The dangerous case: the count succeeds, but it answers a
        different question than the one asked."""
        intent = R.RetrievalIntent(
            strategy=R.RetrievalStrategy.SQL_AGGREGATE, aggregate="count",
            filters={"topic": "refunds"}, require_complete_coverage=True)
        result = source.query(intent)
        assert result.rows[0][0] == 500
        assert result.coverage is R.Coverage.APPROXIMATE
        assert result.supports_a_numeric_claim is False
        assert "topic" in result.note

    def test_a_legacy_database_without_project_scope_still_answers(self, tmp_path):
        """PRAGMA-checked columns: an old file must not raise mid-query."""
        src = _source_over(_seed(tmp_path / "legacy.sqlite3", LEGACY_SCHEMA))
        result = src.query(R.dataclasses.replace(
            R.parse_intent("how many memories?"), time_range=None))
        assert result.rows[0][0] == 500

    def test_no_database_at_all_is_unknown_not_zero(self, tmp_path):
        """Reporting 0 for a missing database is a false answer."""
        src = RS.MemorySource()
        src._connect = lambda: None
        result = src.query(R.parse_intent("how many memories?"))
        assert result.coverage is R.Coverage.UNKNOWN
        assert result.supports_a_numeric_claim is False
        assert result.rows == ()


class TestRankedRetrievalSaysWhenItSampled:
    def test_a_wide_ranked_query_over_a_big_corpus_is_top_k(self, source):
        result = source.query(R.parse_intent("what did I say about refunds?"))
        assert result.strategy is R.RetrievalStrategy.HYBRID
        assert result.coverage is R.Coverage.TOP_K
        assert result.supports_a_numeric_claim is False
        assert result.total_scanned == 500

    def test_ranking_happens_over_every_candidate_not_a_window(self, source):
        """The 8c4c38d lesson: rank first, slice second.

        The oldest refund row (id 11) must be reachable even though ~490
        newer rows exist - impossible if the query took the newest N and
        scored those.
        """
        result = source.query(R.parse_intent("what did I say about refund policy?"))
        ids = [r["id"] for r in result.rows[:10]]
        assert 11 in ids, ids

    def test_a_plural_query_term_does_not_match_the_singular_row(self, source):
        """A real limitation of substring matching, recorded rather than
        hidden: "refunds" does not match "refund", so every row scores 0 and
        the result degrades to newest-first. Found while writing the test
        above, which asked about "refunds" and got the 500 newest rows.

        This is why FR-103 lists SEMANTIC as a separate strategy - lexical
        retrieval cannot answer a question whose wording differs from the
        record's. Until a semantic source exists, HYBRID is lexical, and
        this test documents exactly what that costs.
        """
        result = source.query(R.parse_intent("what did I say about refunds?"))
        ids = [r["id"] for r in result.rows[:10]]
        assert 11 not in ids
        assert result.coverage is R.Coverage.TOP_K

    def test_a_narrow_filtered_query_can_be_filtered_complete(self, source):
        intent = R.dataclasses.replace(
            R.parse_intent("show memories containing the exact phrase refund"),
            filters={"kind": "refund"})
        result = source.query(intent)
        assert len(result.rows) == 4
        assert result.coverage is R.Coverage.FILTERED_COMPLETE

    def test_evidence_points_at_real_rows(self, source):
        result = source.query(R.parse_intent("what did I say about refunds?"))
        assert result.evidence
        assert all(e.locator.startswith("memories#") for e in result.evidence)


class TestRouter:
    def test_it_routes_a_count_to_aggregation_and_a_topic_to_ranking(self, source):
        router = RS.RetrievalRouter([source])
        counted = router.route("how many memories are there?", now=dt.datetime(2026, 10, 1))
        ranked = router.route("what did I say about refunds?")
        assert counted.strategy is R.RetrievalStrategy.SQL_AGGREGATE
        assert ranked.strategy is R.RetrievalStrategy.HYBRID

    def test_a_strategy_no_source_supports_is_refused_not_faked(self, source):
        router = RS.RetrievalRouter([source])
        result = router.execute(R.RetrievalIntent(
            strategy=R.RetrievalStrategy.LIVE_CONNECTOR, subject="calendar"))
        assert result.coverage is R.Coverage.UNKNOWN
        assert "no source" in result.note

    def test_a_complete_coverage_demand_answered_by_a_sample_is_annotated(self, source):
        """The router's own guard: it will not hand back a sample for a
        question that needed enumeration without saying so."""
        router = RS.RetrievalRouter([source])
        intent = R.RetrievalIntent(
            strategy=R.RetrievalStrategy.HYBRID, subject="refunds",
            require_complete_coverage=True)
        result = router.execute(intent)
        assert result.coverage is R.Coverage.TOP_K
        assert result.note, "a sample answering a complete-coverage question must be annotated"
