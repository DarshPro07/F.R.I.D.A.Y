"""
The epistemic ledger record: PRD section 35 (EPI-001).

An important claim is stored with what it is, why it is believed, and how far
to trust it: `status`, `sources`, `evidence`, `confidence`, `freshness`,
`assumptions`, `contradictions`, `scope`. That lets Friday know not only what
it believes but why.

Two types already held half of this each - `product.Claim` (what the boss
asserted and what reading about it found) and `research_cache.Artifact`
(what research found, how sure, how long it stays true). Each converts into
`LedgerClaim` through its own `to_ledger()`, so the shape is defined once
here and neither module learns the other's internals. This module imports
nothing from Friday, so anything may depend on it.

`LedgerClaim.id` is derived from the claim and its scope, not assigned, so a
record rebuilt from either source and a link written long ago agree on it
without a lookup. `promotion.Decision.claims` holds these ids.
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass

#: How a claim is known. Ordered roughly from direct to least direct; the
#: names are the PRD's, so a ledger row can be read against section 35.
OBSERVED = "observed"          # seen or said first-hand
MEASURED = "measured"          # a run, a benchmark, an instrument
SOURCED = "sourced"            # read from a source that can be cited
INFERRED = "inferred"          # reasoned from other claims
SIMULATED = "simulated"        # a model said so, not the world
SPECULATIVE = "speculative"    # asserted with nothing behind it

STATUSES = (OBSERVED, MEASURED, SOURCED, INFERRED, SIMULATED, SPECULATIVE)


def _tuple(values) -> tuple[str, ...]:
    if isinstance(values, str):
        values = (values,)
    return tuple(str(v) for v in (values or ()))


def _normal(text: str) -> str:
    """The claim with case, spacing and closing punctuation taken out."""
    return " ".join(str(text).lower().split()).rstrip(".!?;:")


def claim_id(claim: str, scope: str = "") -> str:
    """Stable id for a claim in a scope; the same words mean the same claim."""
    digest = hashlib.sha1(
        f"{_normal(scope)}\x1f{_normal(claim)}".encode("utf-8")).hexdigest()
    return "clm_" + digest[:12]


@dataclass(frozen=True)
class LedgerClaim:
    """One claim, with the reasons it is believed. Immutable: a changed
    belief is a new record, not an edit of the old one."""

    claim: str
    status: str
    sources: tuple[str, ...] = ()
    #: What the sources actually said - the reason, not a count of it.
    evidence: str = ""
    #: 0.0-1.0. 0.0 means no confidence was ever assessed, not "certainly
    #: false"; `contradictions` is where a claim that failed is recorded.
    confidence: float = 0.0
    #: The freshness class (research_cache.FRESHNESS), "" when not classified.
    freshness: str = ""
    #: When the evidence was gathered and when it stops being good enough,
    #: ISO-8601 UTC; "" when unknown.
    as_of: str = ""
    expires_at: str = ""
    #: What has to stay true for the claim to hold.
    assumptions: tuple[str, ...] = ()
    #: What was found against it.
    contradictions: tuple[str, ...] = ()
    #: Where it applies (a workspace, a project); "" is global.
    scope: str = ""

    def __post_init__(self) -> None:
        set_ = object.__setattr__
        for name in ("sources", "assumptions", "contradictions"):
            set_(self, name, _tuple(getattr(self, name)))
        if not str(self.claim).strip():
            raise ValueError("a ledger claim needs the claim itself")
        if self.status not in STATUSES:
            raise ValueError(
                f"unknown status {self.status!r}; expected one of {STATUSES}")
        if self.status == SOURCED and not self.sources:
            raise ValueError("a 'sourced' claim must name its sources")
        confidence = float(self.confidence)
        if math.isnan(confidence) or not 0.0 <= confidence <= 1.0:
            raise ValueError(f"confidence {self.confidence!r} is not in 0..1")
        set_(self, "confidence", confidence)

    @property
    def id(self) -> str:
        return claim_id(self.claim, self.scope)

    def as_dict(self) -> dict:
        return {"id": self.id, "claim": self.claim, "status": self.status,
                "sources": list(self.sources), "evidence": self.evidence,
                "confidence": self.confidence, "freshness": self.freshness,
                "as_of": self.as_of, "expires_at": self.expires_at,
                "assumptions": list(self.assumptions),
                "contradictions": list(self.contradictions),
                "scope": self.scope}
