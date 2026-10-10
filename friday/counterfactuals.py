"""
The decision record: PRD section 95 (CF-001).

An important plan is never judged alone. The record holds the plan beside
doing nothing, deferring, and running an experiment first (plus any further
alternatives), each option with the claims that speak for it, and each
claim's evidence class - the epistemic ledger's `status` (EPI-001) - kept
apart: an option shows how many of its claims are measured, sourced,
inferred and so on, never one blended score, so a plan resting on
speculation cannot look as solid as one resting on a measurement.

Pure data and validation; nothing here chooses. The chooser states `chosen`
and `rationale`, and the record refuses to exist without the comparison.
"""
from __future__ import annotations

from dataclasses import dataclass

from friday.epistemic import STATUSES, LedgerClaim

PROCEED = "proceed"        # the plan itself
DO_NOTHING = "do_nothing"  # leave things as they are
DEFER = "defer"            # decide or act later
EXPERIMENT = "experiment"  # a small trial before committing
ALTERNATIVE = "alternative"  # any other way to reach the goal

KINDS = (PROCEED, DO_NOTHING, DEFER, EXPERIMENT, ALTERNATIVE)
#: Every record must carry these once, whatever else it compares.
REQUIRED = (PROCEED, DO_NOTHING, DEFER, EXPERIMENT)


@dataclass(frozen=True)
class Option:
    """One course of action and the claims for it."""

    kind: str
    summary: str
    claims: tuple[LedgerClaim, ...]
    #: Names an ALTERNATIVE (several may exist); the other kinds are named
    #: by their kind.
    name: str = ""

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"unknown option kind {self.kind!r}; expected one of {KINDS}")
        if not str(self.summary).strip():
            raise ValueError("an option needs a summary of what it is")
        claims = tuple(self.claims or ())
        if not claims:
            raise ValueError(f"option {self.key!r} lists no claims; an option "
                             "with nothing said for it is not compared")
        if not all(isinstance(c, LedgerClaim) for c in claims):
            raise ValueError("option claims must be epistemic LedgerClaim records")
        object.__setattr__(self, "claims", claims)
        if self.kind == ALTERNATIVE and not str(self.name).strip():
            raise ValueError("an alternative needs a name")

    @property
    def key(self) -> str:
        return self.name.strip() if self.kind == ALTERNATIVE else self.kind

    def evidence_classes(self) -> dict[str, int]:
        """Claims per evidence class, in the ledger's order; classes with no
        claim are left out."""
        counts = {s: 0 for s in STATUSES}
        for claim in self.claims:
            counts[claim.status] += 1
        return {s: n for s, n in counts.items() if n}

    def as_dict(self) -> dict:
        return {"kind": self.kind, "key": self.key, "summary": self.summary,
                "claims": [{"id": c.id, "claim": c.claim,
                            "evidence_class": c.status, "evidence": c.evidence,
                            "sources": list(c.sources),
                            "contradictions": list(c.contradictions)}
                           for c in self.claims],
                "evidence_classes": self.evidence_classes()}


@dataclass(frozen=True)
class DecisionRecord:
    """The comparison and the choice made from it."""

    plan: str
    options: tuple[Option, ...]
    #: The `key` of the option taken.
    chosen: str
    rationale: str = ""

    def __post_init__(self) -> None:
        if not str(self.plan).strip():
            raise ValueError("a decision record needs the plan it is about")
        options = tuple(self.options or ())
        kinds = [o.kind for o in options]
        for required in REQUIRED:
            if required not in kinds:
                raise ValueError(
                    f"the plan is not compared against {required!r}; a decision "
                    f"record needs all of {REQUIRED}")
        keys = [o.key for o in options]
        if len(set(keys)) != len(keys):
            raise ValueError(f"option keys must be distinct, got {keys}")
        if self.chosen not in keys:
            raise ValueError(f"chosen {self.chosen!r} is not one of the options {keys}")
        object.__setattr__(self, "options", options)

    def as_dict(self) -> dict:
        return {"plan": self.plan, "chosen": self.chosen,
                "rationale": self.rationale,
                "options": [o.as_dict() for o in self.options]}
