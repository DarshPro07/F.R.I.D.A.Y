"""ULTRA-006: the cheapest capable model tier for each stage, and the model
within a tier.

A stage template fixes its tier (owner 2026-10-08): economy for lookups,
checks and skeptics; standard for code and producers; a judge, synthesis or
architecture stage may run on standard or, where it is needed, deep - deep is
a ceiling there, not a requirement, and at most one stage per run uses it. A
spec that declares any other tier for a known template is refused at compile
time, naming the field. Within a tier the model is ranked by measured cost per verified
completion through `execution_economics.cheapest_capable`, the ranker
`build_kernel.pick_model` also uses (FAB-003). Nothing here calls a model.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from friday.execution_economics import (
    TIER_DEEP, TIER_ECONOMY, TIER_STANDARD, cheapest_capable)

#: Outcomes before a model's record is trusted, and the share of the best
#: success rate it must keep: the same figures as build_kernel's.
BENCH_MIN = 3
RATE_FLOOR = 0.6

#: The highest tier a template may use (the tier it runs on unless its spec
#: says lower where lower is allowed).
TEMPLATE_TIER = {
    "lookup": TIER_ECONOMY, "check": TIER_ECONOMY, "skeptic": TIER_ECONOMY,
    "builder": TIER_STANDARD, "code": TIER_STANDARD, "producer": TIER_STANDARD,
    "judge": TIER_DEEP, "synthesis": TIER_DEEP, "architecture": TIER_DEEP,
}

#: Templates that may drop from deep to standard when deep is not needed.
DEEP_TEMPLATES = frozenset(t for t, tier in TEMPLATE_TIER.items() if tier == TIER_DEEP)


def tier_for(template: str, declared: str | None = None) -> str | None:
    """The top tier a template runs on; `declared` for a template not in the table."""
    return TEMPLATE_TIER.get(template, declared)


def allowed_tiers(template: str, declared: str | None = None) -> tuple[str, ...]:
    """Every tier a template may declare: exactly one for economy and standard
    templates, standard or deep for a deep template."""
    top = tier_for(template, declared)
    if template in DEEP_TEMPLATES:
        return (TIER_STANDARD, TIER_DEEP)
    return (top,)


def resolve_tiers(spec) -> dict[str, str]:
    """stage id -> tier. Raises SpecError for a tier that is not the cheapest
    capable one for its template, or for a second deep stage."""
    from friday.ultracode.spec import SpecError
    tiers: dict[str, str] = {}
    deep = 0
    for index, stage in enumerate(spec.stages):
        allowed = allowed_tiers(stage.template, stage.tier)
        tier = stage.tier
        if tier not in allowed:
            raise SpecError(f"stages[{index}].tier",
                            f"a {stage.template!r} stage runs on {' or '.join(allowed)}, "
                            f"not {stage.tier}")
        if tier == TIER_DEEP:
            deep += 1
            if deep > 1:
                raise SpecError(f"stages[{index}].tier", "at most one deep stage per spec")
        tiers[stage.id] = tier
    return tiers


@dataclass(frozen=True)
class Choice:
    """A model picked for a tier, with the rows the pick rests on."""
    tier: str
    model: str
    reason: str
    evidence: tuple[dict, ...] = ()


def choose_model(tier: str, candidates: Sequence[str], bench: Mapping[str, dict]) -> Choice:
    """The cheapest per verified win among `candidates` of one tier. `bench`
    maps model -> {n, wins, cost}; with no candidate that has enough outcomes,
    the first candidate is chosen and the reason says nothing was measured."""
    cands = list(candidates)
    if not cands:
        raise ValueError(f"no candidate models for tier {tier}")
    ranked = cheapest_capable(cands, dict(bench), min_n=BENCH_MIN, rate_floor=RATE_FLOOR)
    if ranked is None:
        return Choice(tier, cands[0], f"no measured outcomes (>= {BENCH_MIN} with a win); "
                                      "the first candidate", ())
    model, rows = ranked
    return Choice(tier, model, "cheapest per verified win among models keeping "
                               f">= {RATE_FLOOR:.0%} of the best success rate", tuple(rows))


def choose_models(spec, candidates: Mapping[str, Sequence[str]],
                  bench: Mapping[str, dict]) -> dict[str, Choice]:
    """stage id -> Choice for every stage of `spec`: the stage's tier from
    `resolve_tiers`, its model from `choose_model` over that tier's `candidates`
    (tier -> models). A tier with no candidate raises ValueError."""
    return {stage_id: choose_model(tier, candidates.get(tier, ()), bench)
            for stage_id, tier in resolve_tiers(spec).items()}
