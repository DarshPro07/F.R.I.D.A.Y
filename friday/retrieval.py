"""FR-103: choose a retrieval STRATEGY before retrieving.

Today every memory tier answers every question the same way: lexical
overlap scoring over recent rows. That makes two very different questions
take one path -

    "what did I say about refunds?"        <- find the relevant rows
    "how many refunds happened last month?" <- count ALL matching rows

and the second one is where it goes wrong. A top-K lexical scan cannot
count; it returns the rows that scored best, which looks like an answer and
is not one. That is the same family as the tier-1 defect fixed in 8c4c38d,
where ranking only ever saw the newest 200 rows.

This module is the contract, not the retrieval itself. It decides:

    intent   - what the question actually wants
    strategy - how to get it
    coverage - how much of the corpus the answer is based on

`coverage` is the load-bearing idea. An answer built from TOP_K rows must
never be presented as a count, and a count must never be built from TOP_K
rows. Everything else here exists to make that distinction impossible to
lose.

Deliberately NOT here: an LLM writing SQL. Aggregation goes through a
parameterised builder over a fixed set of operations, so the aggregation
path is deterministic, injection-free and testable without a model.
"""
from __future__ import annotations

import dataclasses
import datetime as _dt
import enum
import re
from typing import Any, Protocol


class RetrievalStrategy(str, enum.Enum):
    """How to answer, not what to answer with."""

    EXACT_FILTER = "exact_filter"      # structured predicate, no ranking
    SQL_AGGREGATE = "sql_aggregate"    # count/sum/avg over ALL matching rows
    LEXICAL = "lexical"                # term match, ranked
    SEMANTIC = "semantic"              # conceptual similarity, ranked
    HYBRID = "hybrid"                  # lexical + semantic, ranked
    FULL_CONTEXT = "full_context"      # the whole document, no selection
    CODE_SEARCH = "code_search"        # repository question
    LIVE_CONNECTOR = "live_connector"  # authoritative external system


class Coverage(str, enum.Enum):
    """What the answer is actually based on.

    The distinction that keeps counts honest:

        COMPLETE          every row in scope was considered
        FILTERED_COMPLETE every row matching the filter was considered
        TOP_K             only the best-scoring N were considered
        APPROXIMATE       derived from ranked retrieval, not enumeration
        UNKNOWN           the source could not say
    """

    COMPLETE = "complete"
    FILTERED_COMPLETE = "filtered_complete"
    TOP_K = "top_k"
    APPROXIMATE = "approximate"
    UNKNOWN = "unknown"


#: Aggregate operations the builder will emit. A fixed list on purpose:
#: anything outside it is refused rather than passed through to SQL.
AGGREGATES = ("count", "sum", "avg", "min", "max", "distinct_count", "group_count")

#: Coverage values that may back a numeric claim ("14 calls"). Anything else
#: must be reported as an estimate with its basis stated.
COUNTABLE_COVERAGE = (Coverage.COMPLETE, Coverage.FILTERED_COMPLETE)


@dataclasses.dataclass(frozen=True)
class TimeRange:
    """Half-open [start, end). Half-open because "in August" must not
    include a September 1st 00:00:00 row."""

    start: _dt.datetime | None = None
    end: _dt.datetime | None = None

    def sql(self, column: str) -> tuple[str, list[Any]]:
        clauses, params = [], []
        if self.start is not None:
            clauses.append(f"{column} >= ?")
            params.append(self.start.isoformat())
        if self.end is not None:
            clauses.append(f"{column} < ?")
            params.append(self.end.isoformat())
        return (" AND ".join(clauses), params)


@dataclasses.dataclass(frozen=True)
class RetrievalIntent:
    """What the question wants, resolved before any retrieval happens."""

    strategy: RetrievalStrategy
    subject: str | None = None
    operation: str | None = None
    time_range: TimeRange | None = None
    filters: dict[str, Any] = dataclasses.field(default_factory=dict)
    aggregate: str | None = None
    group_by: tuple[str, ...] = ()
    source_scope: tuple[str, ...] = ()
    require_complete_coverage: bool = False
    require_fresh_data: bool = False
    confidence: float = 0.0

    def __post_init__(self) -> None:
        if self.aggregate is not None and self.aggregate not in AGGREGATES:
            raise ValueError(
                f"unsupported aggregate {self.aggregate!r}; known: {AGGREGATES}"
            )
        if self.strategy is RetrievalStrategy.SQL_AGGREGATE and not self.aggregate:
            raise ValueError("SQL_AGGREGATE requires an aggregate operation")


@dataclasses.dataclass(frozen=True)
class Evidence:
    """Where one part of an answer came from."""

    source: str
    locator: str
    excerpt: str = ""


@dataclasses.dataclass(frozen=True)
class RetrievalResult:
    strategy: RetrievalStrategy
    source: str
    rows: tuple[Any, ...]
    coverage: Coverage
    total_scanned: int | None = None
    total_matching: int | None = None
    confidence: float = 0.0
    query_ms: float = 0.0
    evidence: tuple[Evidence, ...] = ()
    note: str = ""

    @property
    def supports_a_numeric_claim(self) -> bool:
        """True only when a number from this result may be stated as fact.

        The guard against "14 calls" when the truth is "14 of the rows I
        happened to rank highest".
        """
        return self.coverage in COUNTABLE_COVERAGE


class RetrievalSource(Protocol):
    """One place answers can come from. Memory is the first implementation."""

    name: str

    def capabilities(self) -> set[RetrievalStrategy]: ...

    def query(self, intent: RetrievalIntent) -> RetrievalResult: ...


# ---------------------------------------------------------------------------
# Intent parsing
# ---------------------------------------------------------------------------

_COUNT_PATTERNS = (
    r"\bhow many\b", r"\bhow much\b", r"\bcount of\b", r"\bnumber of\b",
    r"\btotal (?:number|count)\b",
)
_SUM_PATTERNS = (r"\btotal (?:spend|cost|amount|value)\b", r"\bsum of\b")
_AVG_PATTERNS = (r"\baverage\b", r"\bmean\b", r"\btypical\b")
_MAX_PATTERNS = (r"\b(?:most|highest|largest|maximum|longest)\b",)
_MIN_PATTERNS = (r"\b(?:least|lowest|smallest|minimum|shortest)\b",)
_FULL_DOC_PATTERNS = (
    r"\bsummari[sz]e (?:this|the) (?:whole|entire|full)\b",
    r"\bthe (?:whole|entire) (?:document|file|page|transcript)\b",
    r"\bread (?:this|the) (?:whole|entire)\b",
)
_EXACT_PATTERNS = (
    r"\bexact (?:phrase|wording|text)\b", r"\bverbatim\b",
    r"\bcontaining\b", r"\bwith the (?:phrase|word)\b",
)
_OPINION_PATTERNS = (
    r"\bwhat (?:did|do) i (?:say|think|feel)\b", r"\bmy (?:view|opinion|thoughts)\b",
    r"\bhow (?:did|do) i feel\b", r"\bwhat's my take\b",
)
_CODE_PATTERNS = (
    r"\bwhich (?:file|module|function|class)\b", r"\bin the (?:codebase|repo|repository)\b",
    r"\bwhere is .{0,40}\bdefined\b", r"\bcalls?\b.{0,20}\bfunction\b",
)
_FRESH_PATTERNS = (
    r"\b(?:right )?now\b", r"\bcurrent(?:ly)?\b", r"\btoday'?s\b",
    r"\blatest\b", r"\bup to date\b",
)


def _any(patterns: tuple[str, ...], text: str) -> bool:
    return any(re.search(p, text) for p in patterns)


def parse_intent(question: str, *, now: _dt.datetime | None = None) -> RetrievalIntent:
    """Resolve a question into an intent. Deterministic, no model call.

    Order matters. Aggregation is tested FIRST because "how many times did I
    talk about X" is a counting question that also looks conceptual, and
    treating it as conceptual is exactly the defect this module exists to
    prevent.
    """
    text = " ".join((question or "").lower().split())
    now = now or _dt.datetime.now()
    time_range = _parse_time_range(text, now)

    if _any(_FULL_DOC_PATTERNS, text):
        return RetrievalIntent(
            strategy=RetrievalStrategy.FULL_CONTEXT,
            subject=question, require_complete_coverage=True, confidence=0.9,
        )

    aggregate = None
    if _any(_COUNT_PATTERNS, text):
        aggregate = "count"
    elif _any(_SUM_PATTERNS, text):
        aggregate = "sum"
    elif _any(_AVG_PATTERNS, text):
        aggregate = "avg"
    elif _any(_MAX_PATTERNS, text):
        aggregate = "max"
    elif _any(_MIN_PATTERNS, text):
        aggregate = "min"

    if aggregate:
        return RetrievalIntent(
            strategy=RetrievalStrategy.SQL_AGGREGATE,
            subject=_subject_of(question), operation=aggregate,
            aggregate=aggregate, time_range=time_range,
            require_complete_coverage=True,
            require_fresh_data=_any(_FRESH_PATTERNS, text),
            confidence=0.85,
        )

    if _any(_CODE_PATTERNS, text):
        return RetrievalIntent(
            strategy=RetrievalStrategy.CODE_SEARCH,
            subject=_subject_of(question), time_range=time_range, confidence=0.75,
        )

    if _any(_EXACT_PATTERNS, text):
        return RetrievalIntent(
            strategy=RetrievalStrategy.LEXICAL,
            subject=_subject_of(question), time_range=time_range, confidence=0.8,
        )

    if _any(_OPINION_PATTERNS, text):
        return RetrievalIntent(
            strategy=RetrievalStrategy.HYBRID,
            subject=_subject_of(question), time_range=time_range, confidence=0.7,
        )

    return RetrievalIntent(
        strategy=RetrievalStrategy.HYBRID,
        subject=_subject_of(question), time_range=time_range,
        require_fresh_data=_any(_FRESH_PATTERNS, text), confidence=0.4,
    )


_MONTHS = ("january", "february", "march", "april", "may", "june", "july",
           "august", "september", "october", "november", "december")


def _parse_time_range(text: str, now: _dt.datetime) -> TimeRange | None:
    if re.search(r"\blast month\b", text):
        first_this = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        last_end = first_this
        last_start = (first_this - _dt.timedelta(days=1)).replace(day=1)
        return TimeRange(last_start, last_end)
    if re.search(r"\bthis month\b", text):
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return TimeRange(start, None)
    if re.search(r"\btoday\b", text):
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return TimeRange(start, start + _dt.timedelta(days=1))
    if re.search(r"\byesterday\b", text):
        start = (now - _dt.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return TimeRange(start, start + _dt.timedelta(days=1))
    m = re.search(r"\blast (\d+) days?\b", text)
    if m:
        return TimeRange(now - _dt.timedelta(days=int(m.group(1))), None)
    for i, month in enumerate(_MONTHS, start=1):
        if re.search(rf"\bin {month}\b", text):
            year = now.year if i <= now.month else now.year - 1
            start = _dt.datetime(year, i, 1)
            end = (_dt.datetime(year + 1, 1, 1) if i == 12
                   else _dt.datetime(year, i + 1, 1))
            return TimeRange(start, end)
    return None


_STOP_PREFIX = re.compile(
    r"^(?:how many|how much|what did i|what do i|show me|find|tell me|list)\s+", re.I
)


def _subject_of(question: str) -> str:
    subject = _STOP_PREFIX.sub("", (question or "").strip())
    subject = re.sub(r"\b(?:last month|this month|today|yesterday)\b", "", subject, flags=re.I)
    return re.sub(r"\s+", " ", subject).strip(" ?.")


# ---------------------------------------------------------------------------
# Aggregation: parameterised, never model-authored
# ---------------------------------------------------------------------------

#: Columns an aggregate may filter or group on, per table. An allow-list,
#: because the alternative is interpolating caller strings into SQL.
AGGREGATABLE = {
    "memories": {"kind", "scope", "source", "subject", "superseded", "run_id"},
    "utterances": {"speaker", "run_id"},
    "runs": {"status", "objective_id"},
    "tool_results": {"tool", "ok", "run_id"},
}

_TIME_COLUMN = {"memories": "created_at", "utterances": "created_at",
                "runs": "created_at", "tool_results": "created_at"}


def build_aggregate_sql(table: str, intent: RetrievalIntent, *,
                        column: str = "*") -> tuple[str, list[Any]]:
    """Parameterised SQL for one aggregate. No caller string reaches the query.

    Table and column names cannot be parameters in SQL, so both are checked
    against the allow-list and rejected outright otherwise; every VALUE is a
    bound parameter.
    """
    if table not in AGGREGATABLE:
        raise ValueError(f"table {table!r} is not aggregatable; known: {sorted(AGGREGATABLE)}")
    if intent.aggregate not in AGGREGATES:
        raise ValueError(f"unsupported aggregate {intent.aggregate!r}")

    allowed = AGGREGATABLE[table]
    for key in intent.filters:
        if key not in allowed:
            raise ValueError(f"column {key!r} is not filterable on {table}; allowed: {sorted(allowed)}")
    for key in intent.group_by:
        if key not in allowed:
            raise ValueError(f"column {key!r} is not groupable on {table}")
    if column != "*" and column not in allowed:
        raise ValueError(f"column {column!r} is not aggregatable on {table}")

    op = intent.aggregate
    if op == "count":
        select = "COUNT(*)"
    elif op == "distinct_count":
        select = f"COUNT(DISTINCT {column})"
    elif op == "group_count":
        select = "COUNT(*)"
    else:
        select = f"{op.upper()}({column})"

    where, params = [], []
    for key, value in intent.filters.items():
        where.append(f"{key} = ?")
        params.append(value)
    if intent.time_range is not None:
        clause, tparams = intent.time_range.sql(_TIME_COLUMN[table])
        if clause:
            where.append(clause)
            params.extend(tparams)

    group_cols = ", ".join(intent.group_by)
    sql = f"SELECT {group_cols + ', ' if group_cols else ''}{select} FROM {table}"
    if where:
        sql += " WHERE " + " AND ".join(where)
    if group_cols:
        sql += f" GROUP BY {group_cols}"
    return sql, params


def describe_count(result: RetrievalResult, n: int) -> str:
    """Phrase a number according to what actually backs it.

    A structured count and a retrieval-based estimate are both legitimate
    answers; presenting them identically is not.
    """
    if result.supports_a_numeric_claim:
        return str(n)
    return (f"about {n} (found by relevance search over "
            f"{result.total_scanned if result.total_scanned is not None else 'some'} "
            f"records, so this may not be every one)")
