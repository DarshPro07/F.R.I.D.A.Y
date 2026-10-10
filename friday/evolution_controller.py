"""
The Evolution Controller (EVO-001): a concrete trigger becomes exactly one proposal.

Owner ruling 2026-10-08c (docs/plans/build-harness/00-owner-directive-2026-10-08c.md):
"Trigger-based + overnight (Recommended) and Automatic when idle and when find issue
bug problem related to these uograding on it own nt asking". The controller is the
trigger-based half: something concrete happened (a capability was missing, a failure
repeated, a path was slow or costly) and the controller records ONE proposal for it.

Deciding and deduplicating are plain code. No model runs here - not to judge a
trigger, not to write the proposal - so without a trigger FRIDAY spends nothing and
creates nothing. A proposal is stored (`Store.evolution_proposals`), so it survives a
restart, and a repeat of the same trigger only counts one more occurrence.

Admission is EVO-006's `evolution_governor.admit`; this module never reimplements
precedence, capacity or the machine's headroom. The ruling's per-experiment budget is
written into each proposal because, with the daily cap off, it is the only spend bound.

Assumptions, stated rather than asked (revisit if wrong):
  * a failure must repeat MIN_REPEATS times, and a path must be slow (SLOW_MS) or
    costly (COSTLY_USD per call) that many times, before it earns a proposal; a
    missing capability needs one occurrence. The caller supplies the count;
  * a repeated failure is RELIABILITY_REPAIR; the others are APPROVED_IMPROVEMENT;
  * the budget figures below are conservative starting values, per class;
  * a proposal whose components reach a Z4 trust root is recorded but never admitted
    (policy.zone_of, owner ruling: Z4 stays proposal-only);
  * a proposal in any status still absorbs its repeats, so a finished or rejected
    one is not proposed again; the archive (EVO-002) decides when to reopen one.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field

from friday import evolution_governor as E
from friday import objective_budget as B
from friday import policy as P

# -- trigger kinds -------------------------------------------------------------

MISSING_CAPABILITY = "missing_capability"
REPEATED_FAILURE = "repeated_failure"
SLOW_PATH = "slow_path"
COSTLY_PATH = "costly_path"
TRIGGER_KINDS = (MISSING_CAPABILITY, REPEATED_FAILURE, SLOW_PATH, COSTLY_PATH)

#: How often a failure, a slow path or a costly path must be seen to be worth a proposal.
MIN_REPEATS = 3
#: One call slower than this (milliseconds) counts as slow.
SLOW_MS = 5000.0
#: One call dearer than this (US dollars) counts as costly.
COSTLY_USD = 0.50

# -- proposal status -----------------------------------------------------------

PENDING = "pending"
ADMITTED = "admitted"

MAX_SUBJECT = 200
MAX_EVIDENCE_BYTES = 4096

#: Per-experiment ceilings by class. Tool calls and turns come from the objective
#: budget's class table so the run's own enforcement matches the proposal.
_BUDGET_CLASS = {E.RELIABILITY_REPAIR: "STANDARD", E.APPROVED_IMPROVEMENT: "STANDARD",
                 E.EXPLORATION: "SIMPLE"}
_USD = {E.RELIABILITY_REPAIR: 2.0, E.APPROVED_IMPROVEMENT: 3.0, E.EXPLORATION: 1.0}
_WALL_S = {E.RELIABILITY_REPAIR: 1800, E.APPROVED_IMPROVEMENT: 3600, E.EXPLORATION: 1800}
_RISK = {"Z1": "low", "Z2": "medium", "Z3": "medium", "Z4": "high"}


@dataclass(frozen=True)
class Trigger:
    """What happened. `subject` names the capability, tool or path; `count` is how
    often it happened; `observed` is the milliseconds (slow) or dollars (costly) of
    one call; `component` is the file or area a fix would touch, when it is known."""
    kind: str
    subject: str
    count: int = 1
    observed: float = 0.0
    component: str = ""
    evidence: dict = field(default_factory=dict)


@dataclass
class Decision:
    proposed: bool
    created: bool
    reason: str
    proposal: dict | None = None


def _normal(subject: str) -> str:
    return " ".join(str(subject).split()).casefold()


def _validate(trigger: Trigger) -> str:
    """The normalised subject, or ValueError: a trigger is untrusted input."""
    if trigger.kind not in TRIGGER_KINDS:
        raise ValueError(f"unknown trigger kind {trigger.kind!r}; known: {TRIGGER_KINDS}")
    if not isinstance(trigger.subject, str):
        raise ValueError("trigger subject must be text")
    subject = _normal(trigger.subject)
    if not subject:
        raise ValueError("a trigger needs the subject it is about")
    if len(subject) > MAX_SUBJECT:
        raise ValueError(f"trigger subject is longer than {MAX_SUBJECT} characters")
    if not isinstance(trigger.count, int) or isinstance(trigger.count, bool) or trigger.count < 1:
        raise ValueError("trigger count must be a whole number of at least 1")
    observed = trigger.observed
    if (isinstance(observed, bool) or not isinstance(observed, (int, float))
            or not math.isfinite(observed) or observed < 0):
        raise ValueError("trigger observed value must be a finite number of at least 0")
    if not isinstance(trigger.component, str) or len(trigger.component) > MAX_SUBJECT:
        raise ValueError(f"trigger component must be text of at most {MAX_SUBJECT} characters")
    if not isinstance(trigger.evidence, dict):
        raise ValueError("trigger evidence must be a dict")
    try:
        text = json.dumps(trigger.evidence, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"trigger evidence must be plain JSON data ({exc})") from None
    if len(text) > MAX_EVIDENCE_BYTES:
        raise ValueError(f"trigger evidence is larger than {MAX_EVIDENCE_BYTES} bytes")
    return subject


def _threshold(trigger: Trigger) -> str:
    """Why this trigger is not yet worth a proposal, or "" when it is."""
    if trigger.kind == MISSING_CAPABILITY:
        return ""
    if trigger.count < MIN_REPEATS:
        return f"{trigger.kind} has not repeated enough ({trigger.count} of {MIN_REPEATS})"
    if trigger.kind == SLOW_PATH and trigger.observed < SLOW_MS:
        return f"path is not slow enough ({trigger.observed:g} ms under {SLOW_MS:g} ms)"
    if trigger.kind == COSTLY_PATH and trigger.observed < COSTLY_USD:
        return f"path is not costly enough (${trigger.observed:g} under ${COSTLY_USD:g})"
    return ""


def _class_of(trigger: Trigger) -> str:
    return E.RELIABILITY_REPAIR if trigger.kind == REPEATED_FAILURE else E.APPROVED_IMPROVEMENT


def _path_like(subject: str) -> bool:
    """A subject that names a file (friday/policy.py) says what a fix touches."""
    return "/" in subject or "\\" in subject or bool(re.search(r"\.(py|js|ts|json|toml|ya?ml|md|sh|ps1|sql|cfg|ini)$", subject))


def _components(trigger: Trigger, subject: str) -> list[str]:
    if trigger.component.strip():
        found = [trigger.component.strip()]
        # the component is the caller's claim; a path-like subject is the trigger's own
        # evidence of what is touched, and the zone must see both
        return found + [subject] if _path_like(subject) and subject not in found else found
    if trigger.kind == MISSING_CAPABILITY:
        slug = re.sub(r"[^a-z0-9_]+", "-", subject).strip("-")
        if not slug:
            raise ValueError("a missing capability needs a name with letters or digits")
        return [f"skills/{slug}/"]
    return [subject]


def _body(trigger: Trigger, subject: str, klass: str, zone: str, components: list[str]) -> dict:
    limits = B.CLASS_LIMITS[_BUDGET_CLASS[klass]]
    budget = {"max_usd": _USD[klass], "max_wall_s": _WALL_S[klass],
              "max_tool_calls": limits["max_tool_calls"], "max_turns": limits["max_turns"]}
    what = {MISSING_CAPABILITY: f"FRIDAY cannot do {subject!r} and was asked to",
            REPEATED_FAILURE: f"{subject!r} failed {trigger.count} times",
            SLOW_PATH: f"{subject!r} took {trigger.observed:g} ms, {trigger.count} times",
            COSTLY_PATH: f"{subject!r} cost ${trigger.observed:g} a call, {trigger.count} times"}
    return {
        "trigger": {"kind": trigger.kind, "subject": subject, "count": trigger.count,
                    "observed": trigger.observed, "evidence": trigger.evidence},
        "benefit": {"summary": what[trigger.kind] + "; the change removes that cause"},
        "risk": {"level": _RISK[zone], "zone": zone,
                 "note": "a trust root is proposal-only" if zone == "Z4"
                         else "verified independently and rolled back automatically on regression"},
        "cost": {"estimate_usd": budget["max_usd"], "components": len(components)},
        "components": components,
        "acceptance_tests": [
            f"a test that reproduces: {what[trigger.kind]}, failing before the change and passing after",
            "the existing tests of every touched component still pass",
            "an independent verifier, not the builder, confirms both",
        ],
        "budget": budget,
        "termination": {"max_wall_s": budget["max_wall_s"],
                        "stop_when": ["acceptance tests pass", "any budget dimension is exhausted",
                                      "the trajectory shows no progress"]},
    }


def _view(row: dict) -> dict:
    """A stored row as one flat proposal."""
    return {**row["body"], **{k: row[k] for k in (
        "proposal_id", "dedupe_key", "trigger_kind", "subject", "klass", "zone", "status",
        "occurrences", "run_id", "lease", "created_at", "updated_at")},
        "proposal_only": row["zone"] == P.Z4}


def proposals(store, status: str | None = None) -> list[dict]:
    return [_view(row) for row in store.evolution_proposals(status)]


def consider(store, trigger: Trigger | None) -> Decision:
    """Turn a trigger into a proposal. No trigger, or one below its threshold,
    records nothing. A repeat of a recorded trigger returns the same proposal."""
    if trigger is None:
        return Decision(False, False, "no trigger: nothing to propose")
    subject = _validate(trigger)
    why = _threshold(trigger)
    if why:
        return Decision(False, False, why)
    components = _components(trigger, subject)
    zone = P.zone_of(components)
    klass = _class_of(trigger)
    key = hashlib.sha256(f"{trigger.kind}\0{subject}".encode("utf-8")).hexdigest()[:32]
    row, created = store.evolution_proposal_raise(
        f"evo-{key}", key, trigger_kind=trigger.kind, subject=subject, klass=klass,
        zone=zone, body=_body(trigger, subject, klass, zone, components))
    return Decision(True, created,
                    "proposal created" if created else "already proposed; repeat counted",
                    _view(row))


def admit(store, gov, proposal_id: str, run_id: str, *, mode: str, now=None) -> E.Admission:
    """Start the experiment for a pending proposal through EVO-006's admission.
    A higher class still waiting holds this one back; a Z4 proposal never starts."""
    row = store.evolution_proposal(proposal_id)
    if row is None:
        return E.Admission(False, f"unknown proposal {proposal_id!r}")
    if row["zone"] == P.Z4:
        return E.Admission(False, "Z4 trust root: proposal only, the owner applies it")
    if row["status"] != PENDING:
        return E.Admission(False, f"proposal is {row['status']}, not pending")
    waiting: dict[str, int] = {}
    for other in store.evolution_proposals(PENDING):
        if other["proposal_id"] != proposal_id and other["zone"] != P.Z4:
            waiting[other["klass"]] = waiting.get(other["klass"], 0) + 1
    if not store.evolution_proposal_move(proposal_id, PENDING, ADMITTED):
        return E.Admission(False, "proposal was admitted by another caller")
    try:
        admission = E.admit(store, gov, run_id, klass=row["klass"], mode=mode,
                            trigger=f"{row['trigger_kind']}:{row['subject']}",
                            waiting=waiting, now=now)
    except BaseException:
        store.evolution_proposal_move(proposal_id, ADMITTED, PENDING)
        raise
    if not admission.admitted:
        store.evolution_proposal_move(proposal_id, ADMITTED, PENDING)
    else:
        store.evolution_proposal_link(proposal_id, run_id, admission.lease)
    return admission
