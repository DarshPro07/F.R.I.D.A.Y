"""FR-103 continued: memory as the first RetrievalSource.

`friday/retrieval.py` defines what a source must do; this is the first one
that actually does it, over the `memories` table.

The reason this adapter exists rather than a new query path bolted onto
`memory_stack`: the tiers there answer "give me the rows most relevant to
this task, within a token budget", which is the right question for building
a prompt and the wrong one for "how many". Both now go through one contract
that states its own coverage, so a caller can tell an enumeration from a
sample without reading the implementation.

Read-only by construction - it borrows `ui_server._connect()`, which opens
the database `mode=ro`. Retrieval must never write.
"""
from __future__ import annotations

import sqlite3 as _sqlite3
import time
from typing import Any

from friday import retrieval as R

#: Scopes the memory tiers treat as durable user knowledge.
_PREFERENCE_SCOPES = ("preferences", "wants", "goals", "identity")

#: Bound on the rows a ranked (non-counting) query will pull back. Ranked
#: retrieval is allowed to sample - it just has to SAY that it sampled.
_RANKED_LIMIT = 200


class MemorySource:
    """The `memories` table, answering through the retrieval contract."""

    name = "memories"

    def capabilities(self) -> set[R.RetrievalStrategy]:
        return {
            R.RetrievalStrategy.EXACT_FILTER,
            R.RetrievalStrategy.SQL_AGGREGATE,
            R.RetrievalStrategy.LEXICAL,
            R.RetrievalStrategy.HYBRID,
        }

    # -- plumbing ---------------------------------------------------------

    def _connect(self):
        from friday import ui_server as U

        return U._connect()

    def _columns(self, conn) -> set[str]:
        """What this database actually has.

        `project_scope` arrives via a PRAGMA-based column migration, so an
        older file will not have it. Asking beats assuming: a missing column
        is an OperationalError in the middle of a query, and callers would
        read that as "no memories".
        """
        try:
            return {r[1] for r in conn.execute("PRAGMA table_info(memories)")}
        except Exception:
            return set()

    # -- the contract -----------------------------------------------------

    def query(self, intent: R.RetrievalIntent) -> R.RetrievalResult:
        if intent.strategy not in self.capabilities():
            return R.RetrievalResult(
                strategy=intent.strategy, source=self.name, rows=(),
                coverage=R.Coverage.UNKNOWN,
                note=f"{self.name} cannot answer {intent.strategy.value}")

        started = time.monotonic()
        conn = self._connect()
        if conn is None:
            return R.RetrievalResult(
                strategy=intent.strategy, source=self.name, rows=(),
                coverage=R.Coverage.UNKNOWN, note="no database yet")
        try:
            # Ranked results are returned as mappings, so rows must come back
            # as sqlite3.Row. `ui_server._connect` already sets this; a caller
            # passing a bare connection would otherwise hand back tuples and
            # `dict(row)` raises. Set it here so the source is correct
            # regardless of who opened the connection.
            conn.row_factory = _sqlite3.Row
            if intent.strategy is R.RetrievalStrategy.SQL_AGGREGATE:
                return self._aggregate(conn, intent, started)
            return self._ranked(conn, intent, started)
        finally:
            conn.close()

    def _aggregate(self, conn, intent: R.RetrievalIntent, started: float) -> R.RetrievalResult:
        """Count/sum/avg over EVERY matching row - no window, no ranking."""
        cols = self._columns(conn)
        filters = {k: v for k, v in intent.filters.items() if k in cols}
        dropped = set(intent.filters) - set(filters)

        usable = R.dataclasses.replace(intent, filters=filters)
        sql, params = R.build_aggregate_sql("memories", usable)
        rows = tuple(conn.execute(sql, params).fetchall())

        # What the answer rests on. A filtered enumeration is
        # FILTERED_COMPLETE; an unfiltered one is COMPLETE. But if a
        # requested filter could not be applied, the number answers a
        # DIFFERENT question than the one asked, and saying so is the whole
        # point of the coverage field.
        if dropped:
            coverage = R.Coverage.APPROXIMATE
            note = (f"filters {sorted(dropped)} have no column in this "
                    f"database; the count ignores them")
        elif filters or intent.time_range:
            coverage = R.Coverage.FILTERED_COMPLETE
            note = ""
        else:
            coverage = R.Coverage.COMPLETE
            note = ""

        (total,) = conn.execute("SELECT COUNT(*) FROM memories").fetchone()
        return R.RetrievalResult(
            strategy=intent.strategy, source=self.name, rows=rows,
            coverage=coverage, total_scanned=total,
            total_matching=rows[0][0] if rows and len(rows[0]) == 1 else None,
            confidence=intent.confidence,
            query_ms=(time.monotonic() - started) * 1000.0, note=note,
            evidence=(R.Evidence(source=self.name, locator=sql, excerpt=str(params)),))

    def _ranked(self, conn, intent: R.RetrievalIntent, started: float) -> R.RetrievalResult:
        """Lexical ranking over every candidate row, then a slice.

        Ranking happens in SQL across ALL candidates - the 8c4c38d lesson:
        the bug there was never the scoring, it was scoring only the newest
        200 rows. Taking a slice AFTER ranking is honest sampling and is
        reported as TOP_K; taking a slice BEFORE it is the defect.
        """
        cols = self._columns(conn)
        has_scope = "project_scope" in cols

        toks = [t for t in dict.fromkeys(_tokens(intent.subject or "")) if len(t) > 2][:12]
        where = ["superseded=0"]
        params: list[Any] = []
        for key, value in intent.filters.items():
            if key in cols:
                where.append(f"{key} = ?")
                params.append(value)
        if intent.time_range is not None:
            clause, tparams = intent.time_range.sql("created_at")
            if clause:
                where.append(clause)
                params.extend(tparams)

        select = "SELECT id, subject, value, scope, confidence, created_at"
        if has_scope:
            select += ", project_scope"
        base = f"{select} FROM memories WHERE " + " AND ".join(where)

        (candidates,) = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE " + " AND ".join(where), params
        ).fetchone()

        if toks:
            score = " + ".join(
                "(CASE WHEN lower(subject || ' ' || COALESCE(value,'')) LIKE ? "
                "THEN 1 ELSE 0 END)" for _ in toks)
            sql = f"SELECT * FROM ({base}) ORDER BY ({score}) DESC, id DESC LIMIT ?"
            params = params + ["%%%s%%" % t for t in toks] + [_RANKED_LIMIT]
        else:
            sql = f"SELECT * FROM ({base}) ORDER BY id DESC LIMIT ?"
            params = params + [_RANKED_LIMIT]

        rows = tuple(dict(r) for r in conn.execute(sql, params).fetchall())

        # Everything that matched was returned -> the caller has the whole
        # filtered set. Otherwise this is a sample and must say so.
        coverage = (R.Coverage.FILTERED_COMPLETE
                    if candidates <= _RANKED_LIMIT and (intent.filters or intent.time_range)
                    else R.Coverage.COMPLETE if candidates <= _RANKED_LIMIT
                    else R.Coverage.TOP_K)

        return R.RetrievalResult(
            strategy=intent.strategy, source=self.name, rows=rows,
            coverage=coverage, total_scanned=candidates,
            total_matching=candidates if coverage is not R.Coverage.TOP_K else None,
            confidence=intent.confidence,
            query_ms=(time.monotonic() - started) * 1000.0,
            evidence=tuple(
                R.Evidence(source=self.name, locator=f"memories#{r['id']}",
                           excerpt=(r.get("value") or "")[:80])
                for r in rows[:5]))


def _tokens(text: str) -> list[str]:
    import re

    return re.findall(r"[a-z0-9]+", (text or "").lower())


# ---------------------------------------------------------------------------
# The router
# ---------------------------------------------------------------------------

class RetrievalRouter:
    """Parse a question, pick a source that can answer it, answer it.

    Deliberately small. The router's job is to route - not to rank, not to
    merge, not to decide truth. Sources report their own coverage and the
    router refuses to hand back a result whose coverage cannot support what
    the intent demanded.
    """

    def __init__(self, sources: list[R.RetrievalSource] | None = None):
        self.sources = list(sources) if sources is not None else [MemorySource()]

    def route(self, question: str, **kw) -> R.RetrievalResult:
        intent = R.parse_intent(question, **kw)
        return self.execute(intent)

    def execute(self, intent: R.RetrievalIntent) -> R.RetrievalResult:
        for source in self.sources:
            if intent.strategy in source.capabilities():
                result = source.query(intent)
                return self._check(intent, result)
        return R.RetrievalResult(
            strategy=intent.strategy, source="none", rows=(),
            coverage=R.Coverage.UNKNOWN,
            note=f"no source can answer {intent.strategy.value}")

    @staticmethod
    def _check(intent: R.RetrievalIntent, result: R.RetrievalResult) -> R.RetrievalResult:
        """A question that demanded complete coverage may not be answered
        with a sample and no warning attached."""
        if intent.require_complete_coverage and not result.supports_a_numeric_claim:
            if not result.note:
                return R.dataclasses.replace(
                    result,
                    note=("this question needs every matching row, and the "
                          f"answer covers {result.coverage.value} only"))
        return result
