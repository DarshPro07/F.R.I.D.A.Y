"""
The user model: what Friday learns about you, and how it corrects itself.

Phase 1D gave memory a shape - kind, source, confidence, provenance - but
nothing filled it in unless the model chose to call `memory_remember`. This is
the layer that learns from ordinary conversation, every day, without being
asked.

Four stages:

    observe      pull candidate facts out of a turn, each with the quote
    reconcile    compare each against what is already known
    consolidate  store, reinforce, or raise a conflict
    retrieve     assemble a briefing so the next turn starts informed

The division of labour matters. A model decides **what was said** - that is a
language problem. Deterministic rules decide **what overrides what** - that is
a trust problem, and a model that can be talked into anything must not own it.

The reconciliation table is the whole "correct me from previous data" idea:

    new FACT      vs existing INFERENCE  -> new wins; we had guessed
    new INFERENCE vs existing FACT       -> existing wins; a guess never
                                            overwrites something you told us
    new FACT      vs existing FACT       -> CONFLICT, ask; two stated facts
                                            disagreeing is not ours to settle
    same value again                     -> reinforce; confidence rises

Nothing is ever deleted. A superseded memory stays queryable, and every
contradiction is recorded with how it was resolved.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass

from friday.store import FACT, INFERENCE, MEMORY_KINDS, PATTERN, PREFERENCE, Store

#: The dimensions of the model, in the user's own terms.
IDENTITY = "identity"        # who you are
THINKING = "thinking"        # how you think and reason
POSSESSIONS = "possessions"  # what you have
WANTS = "wants"              # what you want
GOALS = "goals"              # what you are working toward
PREFERENCES = "preferences"  # how you like things done

DIMENSIONS = (IDENTITY, THINKING, POSSESSIONS, WANTS, GOALS, PREFERENCES)

DIMENSION_HELP = {
    IDENTITY: "who the user is: role, background, context they operate in",
    THINKING: "how they reason: what they value in an approach, how they "
              "decide, what they are sceptical of",
    POSSESSIONS: "what they have: hardware, tools, accounts, codebases, assets",
    WANTS: "what they want: desired capabilities, outcomes they are asking for",
    GOALS: "what they are working toward: projects, milestones, ambitions",
    PREFERENCES: "how they like things done: style, tooling, process, tone",
}

#: Reinforcing an observation raises confidence toward, but never to, certainty.
REINFORCE_STEP = 0.1
MAX_INFERRED_CONFIDENCE = 0.95
#: Below this a candidate is recorded but not promoted into the profile.
MIN_STORE_CONFIDENCE = 0.35

EXTRACTION_MODEL = os.getenv("ADA_PROFILE_MODEL", "gemini-2.5-flash")


@dataclass(frozen=True)
class Candidate:
    dimension: str
    subject: str
    value: str
    kind: str
    confidence: float
    evidence: str

    def validated(self) -> "Candidate":
        if self.dimension not in DIMENSIONS:
            raise ValueError(f"unknown dimension {self.dimension!r}")
        if self.kind not in MEMORY_KINDS:
            raise ValueError(f"unknown kind {self.kind!r}")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence out of range: {self.confidence}")
        if not self.evidence.strip():
            raise ValueError("a candidate must carry the quote it came from")
        return self

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Outcome:
    """What happened to one candidate."""

    action: str          # stored | reinforced | rejected | conflict | ignored
    subject: str
    value: str
    reason: str
    observation_id: int | None = None
    memory_id: int | None = None
    contradiction_id: int | None = None

    def as_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Observe
# ---------------------------------------------------------------------------

_EXTRACTION_SYSTEM = """You extract durable facts about a user from one turn of
conversation, for an assistant's long-term memory.

Return only things worth remembering next week. Ignore small talk, one-off task
instructions, and anything already obvious from context.

For each item give:
  dimension  one of: {dimensions}
  subject    a stable dotted key, e.g. "user.role", "project.arc_reactor.language",
             "hardware.laptop". Reuse the same key for the same thing so later
             mentions line up with earlier ones.

             CRITICAL: if the KNOWN SUBJECTS list below contains a key for the
             same thing, you MUST reuse that exact key, even when the user is
             now contradicting it. Coining a new key for a fact that already
             has one hides the disagreement instead of surfacing it, and the
             assistant ends up holding both. A correction is only a correction
             if it lands on the same key.

             Record the corrected fact positively ("Linux"), never as a
             negation of the old one ("not Windows").
  value      the fact itself, short and plain
  kind       FACT        the user stated it outright
             PREFERENCE  how they like things done
             PATTERN     repeated behaviour you noticed
             INFERENCE   you worked it out; it was not said
  confidence 0.0-1.0, honestly calibrated. Use FACT with high confidence only
             for things actually said. If you are reading between the lines it
             is an INFERENCE, and its confidence should show that.
  evidence   the user's own words, verbatim, that support this. Never
             paraphrase here. If you cannot quote it, do not report it.

Return an empty list rather than inventing something. Most turns contain
nothing worth keeping, and that is a correct answer."""

_EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "dimension": {"type": "string"},
                    "subject": {"type": "string"},
                    "value": {"type": "string"},
                    "kind": {"type": "string"},
                    "confidence": {"type": "number"},
                    "evidence": {"type": "string"},
                },
                "required": ["dimension", "subject", "value", "kind",
                             "confidence", "evidence"],
            },
        }
    },
    "required": ["items"],
}


class ExtractionError(RuntimeError):
    """The extractor could not produce usable candidates."""


def known_subjects(store: Store, limit: int = 120) -> list[str]:
    """Subject keys already in the profile, for the extractor to reuse."""
    subjects: list[str] = []
    for dimension in DIMENSIONS:
        for row in store.memories_by_scope(dimension, limit=limit):
            if row["subject"] not in subjects:
                subjects.append(f"{row['subject']} = {row['value']}")
    return subjects


def extract_candidates(
    user_text: str, *, assistant_text: str = "",
    existing_subjects: list[str] | None = None,
) -> list[Candidate]:
    """
    Pull candidate facts out of one turn. Returns [] when there is nothing.

    `existing_subjects` is what makes correction work. Reconciliation only
    fires when a new claim lands on the same subject key as the old one, and
    without this the extractor invents a fresh key for a fact it already has -
    so "actually I'm on Linux" became `user.os = "not Windows"` alongside a
    still-live `hardware.laptop.os = Windows`, and the contradiction was never
    seen.
    """
    if not (user_text or "").strip():
        return []
    if not os.getenv("GOOGLE_API_KEY", "").strip():
        raise ExtractionError("profile learning needs GOOGLE_API_KEY")

    from google import genai
    from google.genai import types

    conversation = ""
    if existing_subjects:
        conversation += (
            "KNOWN SUBJECTS (reuse these exact keys when the turn is about the "
            "same thing, including when it contradicts them):\n"
            + "\n".join(f"  {s}" for s in existing_subjects)
            + "\n\n"
        )
    conversation += f"USER: {user_text}"
    if assistant_text.strip():
        conversation += f"\n\nASSISTANT: {assistant_text}"

    client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))
    response = client.models.generate_content(
        model=EXTRACTION_MODEL,
        contents=conversation,
        config=types.GenerateContentConfig(
            system_instruction=_EXTRACTION_SYSTEM.format(
                dimensions=", ".join(DIMENSIONS)
            ),
            response_mime_type="application/json",
            response_schema=_EXTRACTION_SCHEMA,
            temperature=0.0,
        ),
    )
    raw = (response.text or "").strip()
    if not raw:
        return []
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ExtractionError(f"extractor returned unparseable JSON: {exc}") from exc

    candidates: list[Candidate] = []
    for item in payload.get("items", []):
        try:
            candidates.append(Candidate(
                dimension=str(item["dimension"]).strip().lower(),
                subject=str(item["subject"]).strip(),
                value=str(item["value"]).strip(),
                kind=str(item["kind"]).strip().upper(),
                confidence=float(item["confidence"]),
                evidence=str(item["evidence"]).strip(),
            ).validated())
        except (KeyError, ValueError, TypeError):
            continue  # a malformed item is dropped, never guessed at
    return candidates


# ---------------------------------------------------------------------------
# Reconcile - deterministic, never delegated to a model
# ---------------------------------------------------------------------------

#: How much a kind is trusted when two claims disagree.
_AUTHORITY = {FACT: 3, PREFERENCE: 2, PATTERN: 1, INFERENCE: 0}


def same_value(left: str, right: str) -> bool:
    return left.strip().lower() == right.strip().lower()


def decide(candidate: Candidate, existing: dict | None) -> tuple[str, str]:
    """
    Decide what to do with a candidate given what is already known.

    Returns (action, reason). Actions: store, reinforce, reject, conflict.
    """
    if candidate.confidence < MIN_STORE_CONFIDENCE:
        return "reject", (
            f"confidence {candidate.confidence:.0%} is below the "
            f"{MIN_STORE_CONFIDENCE:.0%} floor for the profile"
        )

    if existing is None:
        return "store", "nothing was recorded for this subject"

    if same_value(candidate.value, existing["value"]):
        return "reinforce", "the same value was observed again"

    new_authority = _AUTHORITY[candidate.kind]
    old_authority = _AUTHORITY[existing["kind"]]

    if new_authority > old_authority:
        return "store", (
            f"a {candidate.kind} supersedes the stored {existing['kind']} "
            f"({existing['value']!r})"
        )
    if new_authority < old_authority:
        return "reject", (
            f"an {candidate.kind} does not overwrite a stored "
            f"{existing['kind']} ({existing['value']!r})"
        )

    # Equal authority and different values.
    if candidate.kind == FACT:
        return "conflict", (
            f"you previously said {existing['value']!r} and now {candidate.value!r} - "
            f"two stated facts disagree, so this is not ours to settle"
        )
    if candidate.confidence > float(existing["confidence"]) + 0.15:
        return "store", (
            f"a more confident {candidate.kind} ({candidate.confidence:.0%} vs "
            f"{float(existing['confidence']):.0%}) replaces the earlier one"
        )
    return "conflict", (
        f"a competing {candidate.kind} of similar confidence "
        f"({existing['value']!r} vs {candidate.value!r})"
    )


# ---------------------------------------------------------------------------
# Consolidate
# ---------------------------------------------------------------------------


def learn_from_turn(
    store: Store, user_text: str, *, assistant_text: str = "",
    conversation_id: str | None = None, run_id: str | None = None,
) -> list[Outcome]:
    """
    Extract and reconcile in one step, with key alignment wired up.

    This is the entry point callers should use; it passes the existing subject
    keys to the extractor so a correction lands on the fact it corrects.
    """
    candidates = extract_candidates(
        user_text, assistant_text=assistant_text,
        existing_subjects=known_subjects(store),
    )
    return learn(store, candidates, conversation_id=conversation_id, run_id=run_id)


def learn(
    store: Store, candidates: list[Candidate], *,
    conversation_id: str | None = None, run_id: str | None = None,
) -> list[Outcome]:
    """Record candidates, reconciling each against what is already known."""
    outcomes: list[Outcome] = []

    for candidate in candidates:
        current = store.recall(candidate.subject)
        existing = current[0] if current else None
        action, reason = decide(candidate, existing)

        observation_id = store.add_observation(
            dimension=candidate.dimension, subject=candidate.subject,
            value=candidate.value, kind=candidate.kind,
            confidence=candidate.confidence, evidence=candidate.evidence,
            conversation_id=conversation_id, run_id=run_id,
            status="pending",
        )

        if action == "store":
            # One active belief per subject in the profile. store.remember()
            # only supersedes within a kind, so a FACT replacing an INFERENCE
            # would otherwise leave both live and the profile would show two
            # contradictory values for the same thing.
            if existing is not None:
                store.supersede(candidate.subject)
            memory_id = store.remember(
                candidate.subject, candidate.value, kind=candidate.kind,
                source=f"learned from conversation: {candidate.evidence[:120]}",
                scope=candidate.dimension, confidence=candidate.confidence,
                run_id=run_id, observation_id=observation_id,
            )
            store.set_observation_status(observation_id, "accepted", memory_id)
            outcomes.append(Outcome("stored", candidate.subject, candidate.value,
                                    reason, observation_id, memory_id))

        elif action == "reinforce":
            raised = min(
                float(existing["confidence"]) + REINFORCE_STEP,
                1.0 if existing["kind"] == FACT else MAX_INFERRED_CONFIDENCE,
            )
            # Reinforcement writes a new row rather than editing the old one,
            # so the count has to be carried across or every confirmation
            # would look like the first time it was ever said.
            seen = int(existing["evidence_count"] or 1) + 1
            memory_id = store.remember(
                candidate.subject, existing["value"], kind=existing["kind"],
                source=f"{existing['source']} (+ seen again: {candidate.evidence[:80]})",
                scope=candidate.dimension, confidence=raised, run_id=run_id,
                evidence_count=seen, observation_id=observation_id,
            )
            store.set_observation_status(observation_id, "accepted", memory_id)
            outcomes.append(Outcome(
                "reinforced", candidate.subject, existing["value"],
                f"{reason}; confidence {float(existing['confidence']):.0%} -> {raised:.0%}, "
                f"seen {seen} times",
                observation_id, memory_id,
            ))

        elif action == "conflict":
            contradiction_id = store.add_contradiction(
                subject=candidate.subject, existing_value=existing["value"],
                existing_kind=existing["kind"], new_value=candidate.value,
                new_kind=candidate.kind, observation_id=observation_id,
                resolution="pending", rationale=reason,
            )
            store.set_observation_status(observation_id, "pending")
            outcomes.append(Outcome(
                "conflict", candidate.subject, candidate.value, reason,
                observation_id, contradiction_id=contradiction_id,
            ))

        else:
            store.set_observation_status(observation_id, "rejected")
            outcomes.append(Outcome("rejected", candidate.subject,
                                    candidate.value, reason, observation_id))

    return outcomes


def resolve(
    store: Store, contradiction_id: int, *, keep: str, rationale: str,
    run_id: str | None = None,
) -> dict:
    """
    Settle a conflict. `keep` is "new" or "existing".

    This is what runs after the user answers "no, it's actually X".
    """
    if keep not in ("new", "existing"):
        raise ValueError("keep must be 'new' or 'existing'")

    pending = {row["id"]: row for row in store.contradictions()}
    row = pending.get(contradiction_id)
    if row is None:
        raise KeyError(f"no contradiction with id {contradiction_id}")

    if keep == "new":
        memory_id = store.remember(
            row["subject"], row["new_value"], kind=row["new_kind"],
            source=f"user resolved a conflict: {rationale}",
            confidence=1.0 if row["new_kind"] == FACT else 0.8, run_id=run_id,
        )
        if row["observation_id"]:
            store.set_observation_status(row["observation_id"], "accepted", memory_id)
    else:
        memory_id = None
        if row["observation_id"]:
            store.set_observation_status(row["observation_id"], "rejected")

    store.resolve_contradiction(
        contradiction_id, f"{keep}_wins", rationale
    )
    return {"contradiction_id": contradiction_id, "kept": keep,
            "subject": row["subject"], "memory_id": memory_id}


# ---------------------------------------------------------------------------
# Retrieve
# ---------------------------------------------------------------------------


def profile(store: Store, *, per_dimension: int = 12) -> dict:
    """The current user model, grouped by dimension."""
    out: dict[str, list[dict]] = {}
    for dimension in DIMENSIONS:
        rows = store.memories_by_scope(dimension, limit=per_dimension)
        out[dimension] = [
            {"subject": r["subject"], "value": r["value"], "kind": r["kind"],
             "confidence": r["confidence"], "since": r["created_at"],
             "evidence_count": int(r["evidence_count"] or 1),
             "last_confirmed": r["last_confirmed"] or r["created_at"],
             "observation_id": r["observation_id"]}
            for r in rows
        ]
    return out


def brief(store: Store, *, max_chars: int = 1800) -> str:
    """
    A compact briefing to put in front of the next turn.

    Inferences are marked as inferences here too. If the assistant is going to
    act on a guess it should be able to see that it is one.

    Something heard once is marked as heard once. A fact stated five times and
    a fact mentioned in passing read identically otherwise, and they should
    not be leaned on equally.
    """
    data = profile(store)
    lines: list[str] = []
    for dimension in DIMENSIONS:
        rows = data.get(dimension) or []
        if not rows:
            continue
        lines.append(f"{dimension.upper()}:")
        for row in rows:
            marker = "" if row["kind"] == FACT else f" [{row['kind'].lower()}, {row['confidence']:.0%}]"
            if row["evidence_count"] > 1:
                marker += f" [confirmed {row['evidence_count']}x]"
            elif row["kind"] != FACT:
                marker += " [heard once]"
            lines.append(f"  - {row['subject']}: {row['value']}{marker}")
    if not lines:
        return ""

    header = ("[WHAT YOU KNOW ABOUT THIS USER - use it naturally, never recite "
              "it, and never state an inference as a fact]\n")
    text = header + "\n".join(lines)
    return text[:max_chars]


def explain(store: Store, subject: str) -> dict:
    """
    Why does Friday believe this? Returns the memory plus every observation
    that ever supported or contested it.
    """
    current = store.recall(subject)
    history = store.recall(subject, include_superseded=True)
    observations = store.observations(subject=subject)
    conflicts = [row for row in store.contradictions(limit=200)
                 if row["subject"] == subject]
    return {
        "subject": subject,
        "current": current[0] if current else None,
        "superseded": [r for r in history if r["superseded"]],
        "observations": observations,
        "contradictions": conflicts,
    }
