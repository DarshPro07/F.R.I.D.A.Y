"""
CAP-FORGE-014: the scored relevance gate for external repositories.

CAP-FORGE-011 (`fabric.find_overlaps`) states the overlap rule; this module
turns it into a number and one of six decisions, per the owner directive
2026-10-03 (f): a repository is rejected when it adds no material new
capability or measurable improvement, or when its value does not exceed its
cost. Nothing here installs anything - a decision is data, and `installs` is
always False. Whatever is not rejected goes to quarantine and evaluation.

    CapabilityValueScore = (new_capability + quality + cost + latency
                            + reliability + user_value)
                         - (duplicate_overlap + maintenance + security
                            + runtime + context + integration)

`new_capability` and `duplicate_overlap` are derived from the operation names
the candidate shares with what FRIDAY already has (the registry's only
capability vocabulary, as in `fabric.find_overlaps`); the other ten are
supplied by the evaluator, 0..1 each, default 0 (unmeasured counts for
nothing). `score_value` is generic over dimension names so the Evolution Value
Gate (EVO-004) can reuse it with its own.

A candidate that adds nothing new is still kept when it is a *required
dependency* of a capability FRIDAY keeps (`Candidate.required_by`):
the materiality rejection and the duplicate-overlap charge do not
apply, the net-value rule still does. A candidate
whose fit is `ideas` is REFERENCE (ideas recorded, no code taken) even when
existing code already covers it - that is the directive's definition of it.

`intake_decider` is the seam into `friday.intake.IntakeRun` (the GitHub-URL
front door): its DECIDE stage calls a decider, and a REJECT there halts the
run before BUILD/REGISTER, so nothing is installed. The one vocabulary
difference, ADOPT here vs USE there, is mapped in `INTAKE_KIND`.

Imports nothing from `fabric`, which imports this module.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Iterable, Mapping

from friday import intake

ADOPT = "ADOPT"
EXTEND = "EXTEND"
WRAP = "WRAP"
EXTRACT = "EXTRACT"
REFERENCE = "REFERENCE"
REJECT = "REJECT"
DECISIONS = (ADOPT, EXTEND, WRAP, EXTRACT, REFERENCE, REJECT)

#: How the evaluator says the code would be used, if it is worth keeping.
#: Default is `behind_interface`: third-party code stays behind FRIDAY's
#: provider interface unless someone argues otherwise.
FITS = {
    "direct": ADOPT,            # solves a missing capability cleanly
    "hardening": EXTEND,        # useful, needs FRIDAY security/behaviour added
    "behind_interface": WRAP,   # good implementation, kept behind a provider
    "subsystem": EXTRACT,       # only one subsystem/algorithm is valuable
    "ideas": REFERENCE,         # ideas worth recording, no code taken
}
DEFAULT_FIT = "behind_interface"

GAINS = ("new_capability", "quality", "cost", "latency", "reliability",
         "user_value")
COSTS = ("duplicate_overlap", "maintenance", "security", "runtime", "context",
         "integration")
#: Gain dimensions that are a measured improvement over what exists.
IMPROVEMENTS = ("quality", "cost", "latency", "reliability")
_DERIVED = frozenset({"new_capability", "duplicate_overlap"})

#: A repository is material when this share of its operations is new...
MATERIAL_NEW_CAPABILITY = 0.25
#: ...or when it measurably improves one of `IMPROVEMENTS` by this much.
MATERIAL_IMPROVEMENT = 0.1

#: This module's decision -> `friday.intake.DECISIONS` (ADOPT is intake's USE).
INTAKE_KIND = {ADOPT: "USE", EXTEND: "EXTEND", WRAP: "WRAP",
               EXTRACT: "EXTRACT", REFERENCE: "REFERENCE", REJECT: "REJECT"}

REJECTED_NO_INSTALL = "rejected_no_install"
QUARANTINE_EVALUATE = "quarantine_evaluate"


class ValueGateError(ValueError):
    """A candidate or score the gate refuses to interpret."""


def _unit(name: str, value) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueGateError(f"{name}: expected a number 0..1, got {value!r}")
    value = float(value)
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise ValueGateError(f"{name}: {value!r} is outside 0..1")
    return value


def _dims(kind: str, given: Mapping[str, float] | None,
          allowed: Iterable[str]) -> dict[str, float]:
    given = dict(given or {})
    unknown = sorted(set(given) - set(allowed))
    if unknown:
        raise ValueGateError(f"unknown or derived {kind} dimension(s): {unknown}")
    return {name: _unit(name, value) for name, value in given.items()}


@dataclass(frozen=True)
class ValueScore:
    gain: float
    cost: float

    @property
    def net(self) -> float:
        return self.gain - self.cost


def score_value(gains: Mapping[str, float],
                costs: Mapping[str, float]) -> ValueScore:
    """Sum of gains against sum of costs, equally weighted. Names are free."""
    return ValueScore(gain=sum(gains.values()), cost=sum(costs.values()))


@dataclass(frozen=True)
class Candidate:
    """One external repository, as an evaluator describes it. Data only.

    `capabilities` are the operation names it would answer. `gains` may hold
    quality/cost/latency/reliability/user_value; `costs` maintenance/security/
    runtime/context/integration. The two overlap dimensions are derived.
    """

    repository: str
    capabilities: tuple[str, ...] = ()
    gains: Mapping[str, float] = field(default_factory=dict)
    costs: Mapping[str, float] = field(default_factory=dict)
    fit: str = DEFAULT_FIT
    #: ids of kept capabilities that need this repository to work. Non-empty
    #: makes it a required dependency (exempt from the materiality rule).
    required_by: tuple[str, ...] = ()

    def __post_init__(self):
        if not isinstance(self.repository, str) or not self.repository.strip():
            raise ValueGateError("repository must be a non-empty string")
        if self.fit not in FITS:
            raise ValueGateError(
                f"fit {self.fit!r} is not one of {sorted(FITS)}")
        if isinstance(self.capabilities, str):
            raise ValueGateError("capabilities must be a sequence of names")
        for field_name, names in (("capabilities", self.capabilities),
                                  ("required_by", self.required_by)):
            if isinstance(names, str) or not all(
                    isinstance(n, str) and n.strip() for n in names):
                raise ValueGateError(
                    f"{field_name} must be a sequence of non-empty strings")
        object.__setattr__(self, "capabilities",
                           tuple(dict.fromkeys(self.capabilities)))
        object.__setattr__(self, "required_by",
                           tuple(dict.fromkeys(self.required_by)))
        object.__setattr__(self, "gains", _dims(
            "gain", self.gains, set(GAINS) - _DERIVED))
        object.__setattr__(self, "costs", _dims(
            "cost", self.costs, set(COSTS) - _DERIVED))


@dataclass(frozen=True)
class ValueDecision:
    repository: str
    decision: str
    gains: Mapping[str, float]
    costs: Mapping[str, float]
    overlap_ratio: float
    #: existing capability id -> the candidate's operations it already answers
    overlap_evidence: Mapping[str, list[str]]
    reason: str

    #: The gate decides; it never installs.
    installs = False

    @property
    def net(self) -> float:
        return score_value(self.gains, self.costs).net

    @property
    def disposition(self) -> str:
        return (REJECTED_NO_INSTALL if self.decision == REJECT
                else QUARANTINE_EVALUATE)

    def to_intake_decision(self):
        """The same verdict as a `friday.intake.Decision` (reason capped)."""
        reason = f"value gate: {self.reason}"
        return intake.Decision(INTAKE_KIND[self.decision],
                               reason[:intake.REASON_MAX])

    def to_record(self) -> dict:
        return {
            "repository": self.repository,
            "decision": self.decision,
            "disposition": self.disposition,
            "gains": dict(self.gains),
            "costs": dict(self.costs),
            "net": self.net,
            "overlap_ratio": self.overlap_ratio,
            "overlap_evidence": {k: list(v)
                                 for k, v in self.overlap_evidence.items()},
            "reason": self.reason,
            "installs": self.installs,
        }


def find_overlap(capabilities: Iterable[str],
                 existing: Mapping[str, Iterable[str]],
                 ) -> tuple[float, dict[str, list[str]]]:
    """(share of `capabilities` already answered, who answers which)."""
    mine = tuple(dict.fromkeys(capabilities))
    evidence: dict[str, list[str]] = {}
    covered: set[str] = set()
    for existing_id, ops in existing.items():
        answered = set(ops)
        shared = [op for op in mine if op in answered]
        if shared:
            evidence[existing_id] = shared
            covered.update(shared)
    return (len(covered) / len(mine) if mine else 0.0), evidence


def _decide(candidate: Candidate, ratio: float, gains: Mapping[str, float],
            costs: Mapping[str, float]) -> tuple[str, str]:
    if candidate.fit == "ideas":
        # REFERENCE: useful ideas though existing code covers the function.
        # No code is taken, so overlap is no cost; an empty claim is no idea.
        if candidate.capabilities or any(candidate.gains.values()):
            return REFERENCE, "ideas worth recording; no code taken"
        return REJECT, "no capability or gain declared, nothing to reference"
    improvement = max(gains[name] for name in IMPROVEMENTS)
    required = bool(candidate.required_by)
    if (not required and gains["new_capability"] < MATERIAL_NEW_CAPABILITY
            and improvement < MATERIAL_IMPROVEMENT):
        if ratio > 0:
            why = (f"duplicate: {ratio:.0%} of its operations are already "
                   f"answered and no measured improvement")
        else:
            why = "no material new capability and no measured improvement"
        return REJECT, why
    net = score_value(gains, costs).net
    if net <= 0:
        return REJECT, f"value does not exceed cost (net {net:.2f})"
    why = f"net {net:.2f}; fit {candidate.fit}"
    if required:
        why += f"; required by {', '.join(candidate.required_by)}"
    return FITS[candidate.fit], why


def assess(candidate: Candidate,
           existing: Mapping[str, Iterable[str]]) -> ValueDecision:
    """Score one candidate against `existing` (capability id -> operations)."""
    ratio, evidence = find_overlap(candidate.capabilities, existing)
    supplied = {**candidate.gains,
                "new_capability": (1.0 - ratio) if candidate.capabilities
                else 0.0}
    # A required dependency is needed, not redundant: its overlap is recorded
    # as evidence but not charged as a duplicate cost.
    charged = {**candidate.costs,
               "duplicate_overlap": 0.0 if candidate.required_by else ratio}
    gains = {name: supplied.get(name, 0.0) for name in GAINS}
    costs = {name: charged.get(name, 0.0) for name in COSTS}
    decision, reason = _decide(candidate, ratio, gains, costs)
    return ValueDecision(candidate.repository, decision, gains, costs, ratio,
                         evidence, reason)


def evaluate(candidates: Iterable[Candidate],
             existing: Mapping[str, Iterable[str]]) -> list[ValueDecision]:
    """One decision per repository, in order. Pure: nothing is installed."""
    return [assess(c, existing) for c in candidates]


def intake_decider(candidates: Iterable[Candidate],
                   existing: Mapping[str, Iterable[str]],
                   ) -> Callable[[object], "intake.Decision"]:
    """A `decider` for `friday.intake.IntakeRun`, keyed by `owner/repo`.

    A repository nobody described is REJECTed: no evaluation, no pass.
    """
    by_repo = {c.repository.lower(): c for c in candidates}

    def decide(dossier) -> "intake.Decision":
        key = f"{dossier.owner}/{dossier.repo}".lower()
        candidate = by_repo.get(key)
        if candidate is None:
            return intake.Decision(
                "REJECT", "value gate: no candidate description supplied")
        return assess(candidate, existing).to_intake_decision()

    return decide
