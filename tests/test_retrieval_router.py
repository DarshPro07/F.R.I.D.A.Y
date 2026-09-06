"""FR-103: the router must pick a strategy, and a count must be a count.

The tests that matter here are the coverage ones. A lexical top-K scan can
produce a number that looks exactly like an answer, and the only thing
separating "14 calls" from "14 of the rows I ranked highest" is whether the
retrieval enumerated the corpus or sampled it. 8c4c38d was that bug in its
first form (ranking only ever saw the newest 200 rows); these tests exist so
the next version of it cannot ship quietly.
"""
from __future__ import annotations

import datetime as dt
import sqlite3

import pytest

from friday import retrieval as R


class TestStrategyClassification:
    @pytest.mark.parametrize("question, expected", [
        ("How many calls did I make last month?", R.RetrievalStrategy.SQL_AGGREGATE),
        ("how much did I spend on providers in August?", R.RetrievalStrategy.SQL_AGGREGATE),
        ("what is the average call length?", R.RetrievalStrategy.SQL_AGGREGATE),
        ("What did I say about hiring Rahul?", R.RetrievalStrategy.HYBRID),
        ("what do I think about the gateway design?", R.RetrievalStrategy.HYBRID),
        ("show memories containing the exact phrase 'provider gateway'", R.RetrievalStrategy.LEXICAL),
        ("find the verbatim wording of the refund policy", R.RetrievalStrategy.LEXICAL),
        ("Summarize this whole requirements document", R.RetrievalStrategy.FULL_CONTEXT),
        ("read the entire transcript", R.RetrievalStrategy.FULL_CONTEXT),
        ("which module defines the budget ceiling?", R.RetrievalStrategy.CODE_SEARCH),
    ])
    def test_questions_route_to_the_right_strategy(self, question, expected):
        assert R.parse_intent(question).strategy is expected

    def test_a_counting_question_that_sounds_conceptual_still_counts(self):
        """"how many times have I talked about X" is the trap: it reads like
        a topic question and is a counting question."""
        intent = R.parse_intent("how many times have I talked about ecommerce?")
        assert intent.strategy is R.RetrievalStrategy.SQL_AGGREGATE
        assert intent.require_complete_coverage is True

    def test_aggregation_always_demands_complete_coverage(self):
        for q in ("how many emails did I get today?",
                  "what is the total spend this month?",
                  "count of failed runs yesterday"):
            assert R.parse_intent(q).require_complete_coverage is True

    def test_a_freshness_word_marks_the_intent_fresh(self):
        assert R.parse_intent("what is the current provider health?").require_fresh_data is True
        assert R.parse_intent("what did I say about refunds?").require_fresh_data is False

    def test_an_unknown_aggregate_is_refused_at_construction(self):
        with pytest.raises(ValueError):
            R.RetrievalIntent(strategy=R.RetrievalStrategy.SQL_AGGREGATE, aggregate="median")

    def test_sql_aggregate_without_an_operation_is_refused(self):
        with pytest.raises(ValueError):
            R.RetrievalIntent(strategy=R.RetrievalStrategy.SQL_AGGREGATE)


class TestTimeFiltering:
    def test_in_august_means_august_only(self):
        now = dt.datetime(2026, 9, 6, 12, 0)
        tr = R.parse_intent("how many calls in August?", now=now).time_range
        assert tr.start == dt.datetime(2026, 8, 1)
        assert tr.end == dt.datetime(2026, 9, 1)

    def test_the_range_is_half_open_so_september_first_is_excluded(self):
        """A closed range silently pulls in the next month's first row."""
        now = dt.datetime(2026, 9, 6)
        tr = R.parse_intent("how many in August?", now=now).time_range
        clause, params = tr.sql("created_at")
        assert ">= ?" in clause and "< ?" in clause
        assert "<= ?" not in clause

    def test_last_month_is_the_previous_calendar_month(self):
        now = dt.datetime(2026, 9, 6)
        tr = R.parse_intent("how many calls last month?", now=now).time_range
        assert tr.start == dt.datetime(2026, 8, 1)
        assert tr.end == dt.datetime(2026, 9, 1)

    def test_a_december_reference_in_january_looks_back_a_year(self):
        now = dt.datetime(2026, 1, 15)
        tr = R.parse_intent("how many in December?", now=now).time_range
        assert tr.start.year == 2025


class TestAggregateSqlIsParameterised:
    def test_values_are_bound_never_interpolated(self):
        intent = R.RetrievalIntent(strategy=R.RetrievalStrategy.SQL_AGGREGATE,
                                   aggregate="count", filters={"kind": "preference"})
        sql, params = R.build_aggregate_sql("memories", intent)
        assert "?" in sql and params == ["preference"]
        assert "preference" not in sql

    def test_an_injection_attempt_in_a_value_stays_a_value(self):
        evil = "x'; DROP TABLE memories;--"
        intent = R.RetrievalIntent(strategy=R.RetrievalStrategy.SQL_AGGREGATE,
                                   aggregate="count", filters={"kind": evil})
        sql, params = R.build_aggregate_sql("memories", intent)
        assert "DROP" not in sql.upper()
        assert params == [evil]

    def test_an_unknown_column_is_refused_rather_than_interpolated(self):
        intent = R.RetrievalIntent(strategy=R.RetrievalStrategy.SQL_AGGREGATE,
                                   aggregate="count", filters={"kind); DROP TABLE x;--": 1})
        with pytest.raises(ValueError, match="not filterable"):
            R.build_aggregate_sql("memories", intent)

    def test_an_unknown_table_is_refused(self):
        intent = R.RetrievalIntent(strategy=R.RetrievalStrategy.SQL_AGGREGATE, aggregate="count")
        with pytest.raises(ValueError, match="not aggregatable"):
            R.build_aggregate_sql("secrets", intent)

    def test_group_count_emits_a_group_by(self):
        intent = R.RetrievalIntent(strategy=R.RetrievalStrategy.SQL_AGGREGATE,
                                   aggregate="group_count", group_by=("kind",))
        sql, _ = R.build_aggregate_sql("memories", intent)
        assert "GROUP BY kind" in sql


class TestCoverageKeepsCountsHonest:
    """The defect class this whole module exists to prevent."""

    def _db(self):
        db = sqlite3.connect(":memory:")
        db.execute("""CREATE TABLE memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT, subject TEXT, value TEXT,
            kind TEXT, scope TEXT DEFAULT 'user', source TEXT,
            confidence REAL DEFAULT 1.0, run_id TEXT, created_at TEXT,
            superseded INTEGER DEFAULT 0)""")
        # 500 rows; the relevant ones sit at 10, 201, 350 and 499 so that a
        # newest-200 window (or any top-K sample) provably misses some.
        base = dt.datetime(2026, 8, 1)
        for i in range(500):
            relevant = i in (10, 201, 350, 499)
            db.execute(
                "INSERT INTO memories (subject, value, kind, source, created_at) "
                "VALUES (?,?,?,?,?)",
                ("refund" if relevant else f"other-{i}",
                 "refund requested" if relevant else "unrelated",
                 "refund" if relevant else "note", "test",
                 (base + dt.timedelta(hours=i)).isoformat()))
        db.commit()
        return db

    def test_a_structured_count_sees_every_matching_row(self):
        db = self._db()
        intent = R.parse_intent("how many refunds were there?")
        intent = R.dataclasses.replace(intent, filters={"kind": "refund"}, time_range=None)
        sql, params = R.build_aggregate_sql("memories", intent)
        (n,) = db.execute(sql, params).fetchone()
        assert n == 4, "the count must include rows outside any recency window"

    def test_a_newest_200_window_would_have_missed_two_of_them(self):
        """Proves the fixture actually reproduces the defect it guards."""
        db = self._db()
        (n,) = db.execute(
            "SELECT COUNT(*) FROM (SELECT kind FROM memories "
            "ORDER BY id DESC LIMIT 200) WHERE kind='refund'").fetchone()
        assert n == 2 and n != 4

    def test_top_k_results_may_not_back_a_numeric_claim(self):
        ranked = R.RetrievalResult(
            strategy=R.RetrievalStrategy.LEXICAL, source="memories",
            rows=(1, 2, 3), coverage=R.Coverage.TOP_K, total_scanned=200)
        assert ranked.supports_a_numeric_claim is False

    def test_filtered_complete_results_may(self):
        counted = R.RetrievalResult(
            strategy=R.RetrievalStrategy.SQL_AGGREGATE, source="memories",
            rows=(4,), coverage=R.Coverage.FILTERED_COMPLETE, total_matching=4)
        assert counted.supports_a_numeric_claim is True

    def test_an_approximate_count_is_phrased_as_an_estimate(self):
        ranked = R.RetrievalResult(
            strategy=R.RetrievalStrategy.SEMANTIC, source="memories",
            rows=(), coverage=R.Coverage.APPROXIMATE, total_scanned=11)
        said = R.describe_count(ranked, 11)
        assert said != "11"
        assert "may not be every one" in said

    def test_an_exact_count_is_stated_plainly(self):
        exact = R.RetrievalResult(
            strategy=R.RetrievalStrategy.SQL_AGGREGATE, source="memories",
            rows=(14,), coverage=R.Coverage.FILTERED_COMPLETE)
        assert R.describe_count(exact, 14) == "14"

    def test_unknown_coverage_is_not_treated_as_complete(self):
        """The safe default when a source cannot say."""
        unknown = R.RetrievalResult(
            strategy=R.RetrievalStrategy.LIVE_CONNECTOR, source="calendar",
            rows=(), coverage=R.Coverage.UNKNOWN)
        assert unknown.supports_a_numeric_claim is False


class TestUnstructuredFallback:
    """PRD §6: not every question has structured data behind it."""

    def test_a_topic_count_has_no_structured_column_to_filter_on(self):
        intent = R.parse_intent("how many times have I talked about ecommerce?")
        assert intent.strategy is R.RetrievalStrategy.SQL_AGGREGATE
        with pytest.raises(ValueError, match="not filterable"):
            R.build_aggregate_sql(
                "memories", R.dataclasses.replace(intent, filters={"topic": "ecommerce"}))
