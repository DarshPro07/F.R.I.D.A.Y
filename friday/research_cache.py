"""
Research artifact cache: FR-MEM-005.

Every research call in friday.toolsets.research builds its corpus from
scratch - search, crawl, dedupe, rank, budget - even when the exact same
question was answered an hour ago. That is not just wasted network time: it
means "how fresh is what Friday just told you" has no answer anywhere in the
system, because nothing records when a fact was fetched or how long it
should be trusted. Architectural invariant 18 is blunt about it: research has
source and freshness. This module is where both are kept.

Storage is its own SQLite ledger at DATA_DIR/research/artifacts.sqlite3,
opened per call through friday.dbconn.ledger_connection - the same pattern
friday/skills/registry.py uses to keep the skill registry out of the main
store, and for the same two reasons: the main store is 2,900 lines and 44
tables and does not need a 45th while another change is migrating it, and a
self-contained ledger can be pruned or exported without ever touching
canonical memory.

Freshness is a property of the TOPIC, not a fixed TTL - a stock price is
wrong within the day, a TCP handshake is not wrong within the year.
FRESHNESS maps a topic class to a maximum age in days, and classify_freshness
picks the class from the question's own words: deterministic keyword
matching, no model call, so classifying a question never costs what caching
it was meant to save. When nothing matches, it defaults to `weekly` rather
than to something longer-lived - "every week something new" is the owner's
own description of how fast most researched topics actually move, and a
wrong-but-short default costs one unnecessary refresh where a wrong-but-long
default costs a stale answer nobody knew to doubt.

Workspaces: research is private state, and friday.workspace is the only
place that scope gets decided (see that module's docstring). save() and
lookup() both resolve their workspace through it rather than inventing a
second answer to "whose data is this".

What this module does NOT do: decide *when* invalidate() should fire. An
artifact's invalidation_triggers are recorded strings for a human or a
future monitor to read and act on - "a new model release from this
provider" - not rules this module evaluates itself. Past its own
expires_at, an artifact simply stops being returned by lookup(); nothing
here watches the world for you.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Iterator

from friday import workspace
from friday.config import DATA_DIR
from friday.dbconn import ledger_connection

DB_PATH = DATA_DIR / "research" / "artifacts.sqlite3"

# ---------------------------------------------------------------------------
# Freshness classes
# ---------------------------------------------------------------------------

VOLATILE = "volatile"
WEEKLY = "weekly"
STABLE = "stable"
REFERENCE = "reference"

#: class -> maximum age in days before an artifact stops being "fresh enough".
FRESHNESS: dict[str, int] = {
    VOLATILE: 1,      # prices, stock, breaking news, model releases, availability
    WEEKLY: 7,         # tooling, libraries, competitor pages, rankings
    STABLE: 90,        # standards, protocols, engineering fundamentals
    REFERENCE: 365,    # history, published papers
}

DEFAULT_FRESHNESS = WEEKLY

# Checked in this order because the cost of being wrong is not symmetric: a
# volatile fact served stale is actively misleading, so any volatile signal
# wins even over a question that also mentions history or a standard ("the
# history of today's Nvidia stock price"). Reference is checked before
# stable only to break the rare tie between the two long-lived classes; both
# are so long-lived that the order barely matters next to getting volatile
# right. Weekly is checked explicitly too, even though it is also the
# fallthrough default, so a question that clearly says "library" or
# "competitor" reads as a deliberate match rather than an accident of
# nothing else firing.
_VOLATILE_TERMS = (
    "price", "prices", "pricing", "stock price", "stock quote",
    "exchange rate", "currency rate", "breaking news", "news today",
    "weather", "forecast", "availability", "in stock", "out of stock",
    "inventory", "model release", "new model", "latest model",
    "just released", "right now", "as of today",
)
_REFERENCE_TERMS = (
    "history of", "historical", "published paper", "research paper",
    "founded", "invented", "biography", "born", "died", "world war",
    "timeline of", "origin of", "ancient",
)
_STABLE_TERMS = (
    "protocol", "specification", "rfc", "standard", "algorithm",
    "data structure", "time complexity", "tcp", "http", "tls", "dns",
    "design pattern", "compiler", "cpu architecture",
)
_WEEKLY_TERMS = (
    "library", "framework", "tooling", "sdk", "competitor", "ranking",
    "rankings", "comparison", "benchmark", "changelog",
)


def _matches(text: str, terms: tuple[str, ...]) -> bool:
    return any(re.search(rf"\b{re.escape(term)}\b", text) for term in terms)


def classify_freshness(question: str, *, hint: str | None = None) -> str:
    """
    Which row of FRESHNESS this question belongs to.

    `hint` lets a caller that already knows the domain (a skill, a future
    monitor) state the class directly rather than have it re-derived from
    text. An unrecognised hint falls through to text matching rather than
    raising - a bad hint should degrade the classification, not break the
    lookup.
    """
    if hint in FRESHNESS:
        return hint
    text = (question or "").lower()
    if _matches(text, _VOLATILE_TERMS):
        return VOLATILE
    if _matches(text, _REFERENCE_TERMS):
        return REFERENCE
    if _matches(text, _STABLE_TERMS):
        return STABLE
    if _matches(text, _WEEKLY_TERMS):
        return WEEKLY
    return DEFAULT_FRESHNESS


# ---------------------------------------------------------------------------
# Question normalisation
# ---------------------------------------------------------------------------

_STOPWORDS = frozenset({
    "a", "an", "the", "of", "for", "to", "in", "on", "is", "are", "was",
    "were", "and", "or", "with", "at", "by", "this", "that", "what",
    "which", "who", "how", "do", "does", "did", "be", "as", "it", "its",
})


def normalize_question(question: str) -> str:
    """
    `question` -> its comparison key: lower-cased, punctuation stripped,
    stop words dropped, remaining words sorted.

    Sorted so word order cannot split one question into two cache entries -
    "best local LLM" and "local LLM best" must be the same lookup.
    Deliberately lossy: the point is to collapse rephrasings onto the same
    artifact, not to preserve the question - the original text is kept
    separately in Artifact.question for what actually gets shown back.
    """
    words = re.findall(r"[a-z0-9]+", (question or "").lower())
    kept = sorted(w for w in words if w not in _STOPWORDS)
    return " ".join(kept)


def question_key(question: str) -> str:
    """Deterministic id for `question`'s normalised form."""
    return hashlib.sha256(normalize_question(question).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------


def expiry(freshness: str, since: datetime) -> datetime:
    """When something gathered at `since` stops being good enough."""
    return since + timedelta(days=FRESHNESS[freshness])


@dataclass
class Source:
    """One page an artifact's facts came from. Attribution, not content."""

    url: str
    title: str = ""
    fetched_at: str = ""


@dataclass
class Artifact:
    """
    One piece of research, kept so the next question like it is a lookup
    instead of a crawl.

    `question` is what was actually asked, verbatim, for display.
    `question_key` is what save() and lookup() key on, and save() always
    recomputes it from `question` rather than trusting whatever the caller
    set - the two must never be allowed to disagree.
    """

    question: str
    sources: list[Source]
    facts: list[str]
    synthesis: str
    freshness: str
    fetched_at: datetime
    expires_at: datetime
    question_key: str = ""
    workspace_id: str = ""
    confidence: float = 0.0
    invalidation_triggers: list[str] = field(default_factory=list)
    invalidated_at: str = ""
    invalidated_reason: str = ""

    @classmethod
    def create(
        cls, question: str, *, sources: list[dict], facts: list[str],
        synthesis: str = "", confidence: float = 0.0,
        freshness: str | None = None, freshness_hint: str | None = None,
        invalidation_triggers: list[str] | None = None,
        workspace_id: str = "", fetched_at: datetime | None = None,
    ) -> "Artifact":
        """
        Build an artifact the normal way: classify freshness unless the
        caller already knows it, and stamp fetched_at/expires_at together so
        the two can never disagree about which freshness class produced them.
        """
        when = fetched_at or datetime.now(timezone.utc)
        cls_name = freshness or classify_freshness(question, hint=freshness_hint)
        if cls_name not in FRESHNESS:
            raise ValueError(f"unknown freshness class {cls_name!r}")
        stamp = _iso(when)
        resolved_sources = [
            Source(url=str(s.get("url", "")), title=str(s.get("title") or ""),
                   fetched_at=str(s.get("fetched_at") or stamp))
            for s in sources
        ]
        return cls(
            question=question,
            question_key=question_key(question),
            workspace_id=workspace_id,
            sources=resolved_sources,
            facts=list(facts),
            synthesis=synthesis,
            confidence=float(confidence),
            freshness=cls_name,
            fetched_at=when,
            expires_at=expiry(cls_name, when),
            invalidation_triggers=list(invalidation_triggers or ()),
        )

    def to_ledger(self) -> list:
        """
        This research as epistemic-ledger records (PRD section 35), one per
        fact - a fact is the claim, the artifact is why it is believed.

        Every record shares the artifact's sources, confidence, freshness and
        scope. Invalidation triggers become the assumptions the facts hold
        under; an invalidated artifact carries its reason as a contradiction.
        An artifact that names no source URLs is `speculative`, not `sourced`.
        """
        from friday import epistemic

        urls = tuple(s.url for s in self.sources if s.url)
        evidence = f"answers: {self.question}"
        if self.synthesis:
            evidence += f" - {self.synthesis}"
        claims = [f for f in self.facts if str(f).strip()]
        if not claims and self.synthesis.strip():
            claims = [self.synthesis]
        return [
            epistemic.LedgerClaim(
                claim=claim,
                status=epistemic.SOURCED if urls else epistemic.SPECULATIVE,
                sources=urls, evidence=evidence, confidence=self.confidence,
                freshness=self.freshness, as_of=_iso(self.fetched_at),
                expires_at=_iso(self.expires_at),
                assumptions=tuple(f"holds unless: {t}"
                                  for t in self.invalidation_triggers),
                contradictions=((self.invalidated_reason or "invalidated",)
                                if self.invalidated_at else ()),
                scope=self.workspace_id)
            for claim in claims
        ]


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS artifacts (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    question_key          TEXT NOT NULL,
    workspace_id          TEXT NOT NULL,
    question              TEXT NOT NULL,
    sources               TEXT NOT NULL DEFAULT '[]',
    facts                 TEXT NOT NULL DEFAULT '[]',
    synthesis             TEXT NOT NULL DEFAULT '',
    confidence            REAL NOT NULL DEFAULT 0,
    freshness             TEXT NOT NULL,
    invalidation_triggers TEXT NOT NULL DEFAULT '[]',
    fetched_at            TEXT NOT NULL,
    expires_at            TEXT NOT NULL,
    invalidated_at        TEXT NOT NULL DEFAULT '',
    invalidated_reason    TEXT NOT NULL DEFAULT '',
    UNIQUE (workspace_id, question_key)
);
"""


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    """
    One connection, schema guaranteed, closed when the block ends - the same
    per-call discipline as friday.skills.registry, and for the same reason
    (A-051's Windows handle leak from a connection used as a context manager
    that commits but never closes). There is no long-lived instance here to
    create the schema once against, so it is ensured on every call instead;
    `CREATE TABLE IF NOT EXISTS` against an existing table costs microseconds
    next to a network-bound research call.
    """
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with ledger_connection(DB_PATH) as db:
        db.executescript(SCHEMA)
        yield db


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _parse_iso(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _row_to_artifact(row: sqlite3.Row) -> Artifact:
    data = dict(row)
    return Artifact(
        question=data["question"],
        question_key=data["question_key"],
        workspace_id=data["workspace_id"],
        sources=[Source(**s) for s in json.loads(data["sources"] or "[]")],
        facts=list(json.loads(data["facts"] or "[]")),
        synthesis=data["synthesis"] or "",
        confidence=float(data["confidence"] or 0.0),
        freshness=data["freshness"],
        invalidation_triggers=list(json.loads(data["invalidation_triggers"] or "[]")),
        fetched_at=_parse_iso(data["fetched_at"]),
        expires_at=_parse_iso(data["expires_at"]),
        invalidated_at=data["invalidated_at"] or "",
        invalidated_reason=data["invalidated_reason"] or "",
    )


def save(artifact: Artifact) -> Artifact:
    """
    Persist one artifact, keyed on (workspace, question_key).

    A second save for the same question in the same workspace replaces the
    first - one current answer per question is the point of a cache; a
    history of every time it was asked belongs to `observations`, and that
    table is in the main store, not here.
    """
    if not (artifact.question or "").strip():
        raise ValueError("an artifact needs a question")
    if not artifact.sources:
        raise ValueError("an artifact needs at least one source - it is "
                          "source-backed by definition (FR-MEM-005)")
    if artifact.freshness not in FRESHNESS:
        raise ValueError(f"unknown freshness class {artifact.freshness!r}")

    artifact.workspace_id = workspace.resolve(artifact.workspace_id or None)
    artifact.question_key = question_key(artifact.question)

    with _connect() as db:
        db.execute(
            "INSERT INTO artifacts (question_key, workspace_id, question, sources, "
            "facts, synthesis, confidence, freshness, invalidation_triggers, "
            "fetched_at, expires_at, invalidated_at, invalidated_reason) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(workspace_id, question_key) DO UPDATE SET "
            "question = excluded.question, sources = excluded.sources, "
            "facts = excluded.facts, synthesis = excluded.synthesis, "
            "confidence = excluded.confidence, freshness = excluded.freshness, "
            "invalidation_triggers = excluded.invalidation_triggers, "
            "fetched_at = excluded.fetched_at, expires_at = excluded.expires_at, "
            "invalidated_at = '', invalidated_reason = ''",
            (
                artifact.question_key, artifact.workspace_id, artifact.question,
                json.dumps([vars(s) for s in artifact.sources]),
                json.dumps(list(artifact.facts)),
                artifact.synthesis, float(artifact.confidence), artifact.freshness,
                json.dumps(list(artifact.invalidation_triggers)),
                _iso(artifact.fetched_at), _iso(artifact.expires_at),
                artifact.invalidated_at, artifact.invalidated_reason,
            ),
        )
    # A save always supersedes a prior invalidation: the row it just wrote is
    # the refreshed data the invalidation was asking for.
    artifact.invalidated_at = ""
    artifact.invalidated_reason = ""
    return artifact


def lookup(question: str, *, workspace_id: str | None = None,
           now: datetime | None = None) -> Artifact | None:
    """
    The cached artifact for `question`, if one exists and is still fresh.

    None covers both "never researched" and "researched, but past its
    freshness window" - a caller only ever needs to know "can I use this",
    and both answers are the same instruction: go research it. A caller that
    wants the stale row anyway, to decide whether to refresh it, uses
    stale(), not this - lookup() never returns an expired artifact with a
    warning flag, because a caller that gets a value back uses it.
    """
    scope = workspace.resolve(workspace_id)
    when = now or datetime.now(timezone.utc)
    with _connect() as db:
        row = db.execute(
            "SELECT * FROM artifacts WHERE workspace_id = ? AND question_key = ?",
            (scope, question_key(question)),
        ).fetchone()
    if row is None:
        return None
    artifact = _row_to_artifact(row)
    if artifact.expires_at <= when:
        return None
    return artifact


def stale(now: datetime | None = None) -> list[Artifact]:
    """
    Every artifact, in every workspace, whose expires_at has passed.

    Deliberately not workspace-scoped: this is the maintenance view a
    refresh job works from, not an answer to one workspace's question, so it
    must never be wired to anything that hands a result back to a user -
    that is lookup()'s job, and lookup() is the one that enforces the
    boundary.
    """
    when = now or datetime.now(timezone.utc)
    with _connect() as db:
        rows = db.execute(
            "SELECT * FROM artifacts WHERE expires_at <= ? ORDER BY expires_at",
            (_iso(when),),
        ).fetchall()
    return [_row_to_artifact(row) for row in rows]


def invalidate(question_key: str, reason: str, *,
               workspace_id: str | None = None) -> bool:
    """
    Force one artifact out of freshness immediately, with a reason on record.

    Implemented as pulling expires_at back to now rather than adding a
    second "valid" flag: lookup() only ever needs to ask one question, "is
    this fresh", and a second flag would only be a second place that
    question could be answered wrong. Returns whether a row was actually
    found to invalidate.
    """
    if not (reason or "").strip():
        raise ValueError("invalidate needs a reason")
    scope = workspace.resolve(workspace_id)
    now = _iso(datetime.now(timezone.utc))
    with _connect() as db:
        changed = db.execute(
            "UPDATE artifacts SET expires_at = ?, invalidated_at = ?, "
            "invalidated_reason = ? WHERE workspace_id = ? AND question_key = ?",
            (now, now, reason, scope, question_key),
        ).rowcount
    return bool(changed)


def prune(before: datetime) -> int:
    """
    Delete every artifact that expired before `before`. Returns the count.

    Separate from stale(): stale() is "what needs a look", this is "reclaim
    the space" - it takes an explicit cutoff so nothing is ever deleted by a
    caller that only meant to look.
    """
    with _connect() as db:
        removed = db.execute(
            "DELETE FROM artifacts WHERE expires_at < ?", (_iso(before),)
        ).rowcount
    return int(removed)
