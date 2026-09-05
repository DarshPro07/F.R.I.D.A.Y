"""
S7: the gate between an observed candidate and Friday's canonical memory.

ADR-001: no second canonical memory. This module never writes anywhere but
the store `friday/store.py` already owns (and, for a procedure, a skill
candidate file) - it only decides whether a candidate is trustworthy enough
to cross from "observed" into "known". Fed by `friday/handoff.py`'s
`memory_candidates`/`skill_candidates` (see `promote_handoff`) and by
`friday/autolearn.py` as a second, cheap check over what the profile system
already wrote.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from friday.brain import _sensitive
from friday.store import FACT, PREFERENCE, Store

#: Below this a candidate is recorded as observed, but never promoted.
MIN_CONFIDENCE = 0.6

#: A kind that only ever describes one run of one task, not a durable claim.
ONE_OFF_KINDS = frozenset({"outcome"})

PROCEDURE_KIND = "procedure"
DECISION_KIND = "decision"

#: `memories` table kinds a candidate kind maps onto. `decision` and
#: `procedure` are handled separately - they don't live in `memories`.
KIND_TO_STORE_KIND = {
    "project_fact": FACT,
    "preference": PREFERENCE,
    "relation": FACT,
}

SKILL_CANDIDATES_DIR = Path(__file__).resolve().parent.parent / "data" / "skills_candidates"


@dataclass(frozen=True)
class Candidate:
    statement: str
    kind: str
    source: str
    owner: str
    scope: str
    confidence: float
    evidence: list[str] = field(default_factory=list)
    observed_at: str = ""

    def __post_init__(self) -> None:
        if not self.observed_at:
            object.__setattr__(
                self, "observed_at", datetime.now(timezone.utc).isoformat()
            )


@dataclass(frozen=True)
class Decision:
    accepted: bool
    reason: str
    superseded: list = field(default_factory=list)
    target: str = "rejected"  # "memory" | "skill" | "rejected"


def _subject(statement: str) -> str:
    """
    A stable-ish key so two claims about the same thing land on one subject.

    No entity resolution here - split on the first linking word/colon, fall
    back to the whole statement.
    # ponytail: heuristic, not an NLP subject extractor. Upgrade if
    # candidates about the same thing keep landing on different keys.
    """
    lowered = statement.strip().lower()
    for sep in (": ", " is ", " are ", " prefers ", " likes ", " uses ", " runs on "):
        if sep in lowered:
            return lowered.split(sep, 1)[0].strip()
    return lowered


def promote(candidate: Candidate, *, store: Store | None = None) -> Decision:
    """Decide whether `candidate` may cross into canonical memory (or a skill)."""
    reason = _sensitive(candidate.statement)
    if reason:
        return Decision(False, f"refused: {reason}")
    if not candidate.evidence:
        return Decision(False, "refused: no evidence")
    if candidate.confidence < MIN_CONFIDENCE:
        return Decision(
            False,
            f"refused: confidence {candidate.confidence:.2f} below {MIN_CONFIDENCE}",
        )
    if candidate.kind in ONE_OFF_KINDS:
        return Decision(False, "refused: one-off task state, not durable")

    if candidate.kind == PROCEDURE_KIND:
        return _to_skill_candidate(candidate)

    store = store or Store()

    if candidate.kind == DECISION_KIND:
        return _promote_decision(candidate, store)

    store_kind = KIND_TO_STORE_KIND.get(candidate.kind)
    if store_kind is None:
        return Decision(False, f"refused: unknown kind {candidate.kind!r}")

    subject = _subject(candidate.statement)
    same_kind = [row for row in store.recall(subject) if row["kind"] == store_kind]
    statement_lower = candidate.statement.strip().lower()

    for row in same_kind:
        if row["value"].strip().lower() == statement_lower:
            return Decision(False, "refused: duplicate, already known")

    if same_kind:
        current = same_kind[0]
        stronger = candidate.confidence > float(current["confidence"])
        store.add_contradiction(
            subject=subject,
            existing_value=current["value"],
            existing_kind=current["kind"],
            new_value=candidate.statement,
            new_kind=store_kind,
            resolution="new_wins" if stronger else "pending",
        )
        if not stronger:
            # Never overwrite silently: both stay, the disagreement is flagged.
            return Decision(
                False, "contradiction: kept both, flagged for review", target="rejected"
            )
        store.remember(
            subject, candidate.statement, kind=store_kind, source=candidate.source,
            scope=candidate.scope, confidence=candidate.confidence, supersede=True,
        )
        return Decision(
            True, "accepted: superseded a weaker contradicting fact",
            superseded=[current["id"]], target="memory",
        )

    store.remember(
        subject, candidate.statement, kind=store_kind, source=candidate.source,
        scope=candidate.scope, confidence=candidate.confidence, supersede=False,
    )
    return Decision(True, "accepted", target="memory")


def _promote_decision(candidate: Candidate, store: Store) -> Decision:
    project = candidate.scope or "friday"
    existing = {d["decision"].strip() for d in store.decisions(project)}
    if candidate.statement.strip() in existing:
        return Decision(False, "refused: duplicate, already recorded")
    store.record_decision(project, candidate.statement, source=candidate.source)
    return Decision(True, "accepted", target="memory")


def _to_skill_candidate(candidate: Candidate) -> Decision:
    SKILL_CANDIDATES_DIR.mkdir(parents=True, exist_ok=True)
    stamp = candidate.observed_at.replace(":", "").replace("-", "").replace(".", "")
    slug = abs(hash(candidate.statement)) % 100000
    path = SKILL_CANDIDATES_DIR / f"{stamp}-{slug}.md"
    body = (
        f"# procedure candidate\n\nsource: {candidate.source}\n"
        f"owner: {candidate.owner}\nconfidence: {candidate.confidence}\n\n"
        f"{candidate.statement}\n\nEvidence:\n"
        + "\n".join(f"- {e}" for e in candidate.evidence)
    )
    path.write_text(body, encoding="utf-8")
    return Decision(True, f"routed to skill candidate {path.name}", target="skill")


def promote_handoff(handoff, *, store: Store | None = None) -> list[Decision]:
    """Run everything a work-run handed back through the gate."""
    decisions = []
    for text in handoff.memory_candidates:
        candidate = Candidate(
            statement=text, kind="project_fact", source=f"handoff:{handoff.task_id}",
            owner=handoff.agent or "friday", scope="project", confidence=0.75,
            evidence=[handoff.summary or text],
        )
        decisions.append(promote(candidate, store=store))
    for text in handoff.skill_candidates:
        candidate = Candidate(
            statement=text, kind=PROCEDURE_KIND, source=f"handoff:{handoff.task_id}",
            owner=handoff.agent or "friday", scope="project", confidence=0.75,
            evidence=[handoff.summary or text],
        )
        decisions.append(promote(candidate, store=store))
    return decisions
