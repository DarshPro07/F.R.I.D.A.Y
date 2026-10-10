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

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from friday import contracts as c
from friday.brain import _sensitive
from friday.store import FACT, PREFERENCE, Store

_log = logging.getLogger("friday.memory_promotion")

#: Below this a candidate is recorded as observed, but never promoted.
MIN_CONFIDENCE = 0.6

#: A kind that only ever describes one run of one task, not a durable claim.
ONE_OFF_KINDS = frozenset({"outcome"})

PROCEDURE_KIND = "procedure"
DECISION_KIND = "decision"
PREFERENCE_KIND = "preference"

#: Scopes FRIDAY is shown as what the person wants and who they are
#: (`memory_stack.preferences`). Read material is never filed under them.
PERSON_SCOPES = frozenset({"preferences", "wants", "goals", "identity"})

#: `memories` table kinds a candidate kind maps onto. `decision` and
#: `procedure` are handled separately - they don't live in `memories`.
KIND_TO_STORE_KIND = {
    "project_fact": FACT,
    "preference": PREFERENCE,
    "relation": FACT,
}

from friday.config import DATA_DIR as _DATA_DIR

SKILL_CANDIDATES_DIR = _DATA_DIR / "skills_candidates"


@dataclass(frozen=True)
class Candidate:
    statement: str
    kind: str
    source: str
    owner: str
    scope: str
    confidence: float
    #: Where it came from (PRD v7 MEM-002): `contracts.PERSON`, or
    #: `contracts.READ_MATERIAL` for anything a page, a message, a tool or a
    #: worker said. Required: every producer decides, none is defaulted.
    provenance: str
    evidence: list[str] = field(default_factory=list)
    observed_at: str = ""

    def __post_init__(self) -> None:
        if self.provenance not in c.PROVENANCES:
            raise ValueError(f"unknown provenance {self.provenance!r}")
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
    read = candidate.provenance == c.READ_MATERIAL
    if read:
        # Read material may be remembered, never with the person's voice.
        if candidate.kind == PREFERENCE_KIND:
            return Decision(False, "refused: read material cannot set a preference; "
                                   "that is the person's to state")
        if candidate.kind == DECISION_KIND:
            return Decision(False, "refused: read material cannot record a decision; "
                                   "a decision settles a worker's questions for the person")
        if candidate.scope in PERSON_SCOPES:
            return Decision(False, f"refused: read material cannot be filed under "
                                   f"{candidate.scope}; that speaks for the person")

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
        # Superseding retires every active row on this subject and kind, so
        # every one of them is checked, not only the newest (Slice 3 review).
        theirs = next((row for row in same_kind
                       if row.get("trust_class") != c.READ_MATERIAL), None)
        if read and theirs is not None:
            # Confidence is the writer's own claim; it does not let something
            # read overrule what the person said (or a row that predates
            # trust classes). Both stay, the disagreement is flagged.
            store.add_contradiction(
                subject=subject, existing_value=theirs["value"],
                existing_kind=theirs["kind"], new_value=candidate.statement,
                new_kind=store_kind, resolution="pending")
            return Decision(False, "contradiction: read material cannot overrule "
                                   "what the person said; kept theirs, flagged",
                            target="rejected")
        # What the person says replaces something read, whatever confidence
        # the read one claimed.
        stronger = candidate.confidence > float(current["confidence"]) or (
            not read and current.get("trust_class") == c.READ_MATERIAL)
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
            trust_class=candidate.provenance,
        )
        return Decision(
            True, "accepted: superseded a weaker contradicting fact",
            superseded=[current["id"]], target="memory",
        )

    store.remember(
        subject, candidate.statement, kind=store_kind, source=candidate.source,
        scope=candidate.scope, confidence=candidate.confidence, supersede=False,
        trust_class=candidate.provenance,
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


def promote_handoff(handoff, *, store: Store | None = None,
                    genome=None) -> list[Decision]:
    """Run everything a work-run handed back through the gate.

    A failed handoff that reports its root cause and repair is also recorded
    in the Failure Genome (`handoff.capture_failure`, FAIL-001): this is the
    one seam every terminal work run already passes through.
    """
    decisions = []
    for text in handoff.memory_candidates:
        candidate = Candidate(
            statement=text, kind="project_fact", source=f"handoff:{handoff.task_id}",
            owner=handoff.agent or "friday", scope="project", confidence=0.75,
            evidence=[handoff.summary or text], provenance=c.READ_MATERIAL,
        )
        decisions.append(promote(candidate, store=store))
    for text in handoff.skill_candidates:
        candidate = Candidate(
            statement=text, kind=PROCEDURE_KIND, source=f"handoff:{handoff.task_id}",
            owner=handoff.agent or "friday", scope="project", confidence=0.75,
            evidence=[handoff.summary or text], provenance=c.READ_MATERIAL,
        )
        decisions.append(promote(candidate, store=store))
    from friday.handoff import capture_failure
    outcome = capture_failure(handoff, genome=genome)
    status = outcome.get("status")
    if status in ("skipped", "refused") and (
            (handoff.status or "").strip().lower() in ("failed", "blocked")):
        # Field names only, never contents: show whether auto-capture fires.
        _log.log(logging.WARNING if status == "refused" else logging.INFO,
                 "failure capture %s for %s: missing=%s",
                 status, handoff.task_id,
                 ",".join(outcome.get("missing") or []) or "-")
    return decisions
