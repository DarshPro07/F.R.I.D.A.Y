"""
EVO-004: the Evolution Value Gate.

Owner directive 2026-10-08 (docs/plans/build-harness/00-owner-directive-2026-10-08.md, section 13):
"If it cannot identify a measurable reason, it should not evolve that subsystem just
because a new repository or model was released." The 2026-10-08c ruling made evolution
automatic (triggers, overnight, idle), so this gate is what keeps automatic from meaning
aimless: before an experiment is proposed, never mind run, the proposal must carry at
least one measured reason in one of four kinds - task success, manual effort,
reliability or cost - each a named metric with a baseline and a target that is
materially better. Where the proposal came from (a new repository, a new model) is
recorded as `origin` and never counts as a reason.

Scoring is CAP-FORGE-014's `forge_value_gate.score_value`, not a second scorer; the
materiality floor is its `MATERIAL_IMPROVEMENT`. A reason's gain is its improvement
(0..1, capped; see `_gain` for the scale); a proposal is allowed when the summed gain reaches that
floor through at least one material reason.

A refusal is plain code - no model runs, so nothing paid happens - and is archived in
EVO-002's Evolution Archive through its public `record` API as a REJECT, so the same
unmeasured idea is not re-examined (`find_rejected`) and the owner can see what was
turned away. An allowed proposal writes nothing here: the archive holds experiments'
outcomes, and the controller records the proposal itself.

Assumptions, stated rather than asked (revisit if wrong):
  * latency is not one of the four reasons the owner named, so it is not accepted on
    its own;
  * success and reliability are read as rates when baseline and target both lie in [0, 1]
    (gain = share of the shortfall removed); a number above 1 on either side means a count
    or a duration and uses relative change, with a zero baseline refused as unmeasurable;
  * a reason needs a baseline and a target that are finite, non-negative numbers, and a
    metric name saying what was measured; the gate takes them as the proposer's claim
    to be verified by the experiment, it does not re-measure;
  * the same hypothesis against the same parent and scope is the same proposal (the
    archive's fingerprint), so a repeat refusal is not archived twice.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable

from friday import evolution_archive as A
from friday import forge_value_gate as F

TASK_SUCCESS = "task_success"
MANUAL_EFFORT = "manual_effort"
RELIABILITY = "reliability"
COST = "cost"
REASON_KINDS = (TASK_SUCCESS, MANUAL_EFFORT, RELIABILITY, COST)
#: For these a bigger number is better; for effort and cost a smaller one is.
HIGHER_IS_BETTER = frozenset({TASK_SUCCESS, RELIABILITY})

#: Smallest relative improvement that counts as a reason (CAP-FORGE-014's floor).
MATERIAL_GAIN = F.MATERIAL_IMPROVEMENT

GATE = "evolution_value_gate"
MAX_REASONS = 16


@dataclass
class GateDecision:
    allowed: bool
    reason: str
    score: F.ValueScore | None = None
    #: Relative improvement per reason kind, for the material reasons only.
    gains: dict = field(default_factory=dict)
    problems: list = field(default_factory=list)
    #: The archive record of the refusal ("" when allowed).
    archived_as: str = ""


def _number(value) -> bool:
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and math.isfinite(value) and value >= 0)


def _gain(kind: str, baseline: float, target: float) -> tuple[float, str]:
    """(gain, why-not) for `target` against `baseline`; the gain is 0 when it did not improve.

    Success and reliability are usually rates in [0, 1], where relative change is the wrong
    scale (0.95 -> 0.999 removes 98% of the failures, but is a 5% relative change), so when
    both numbers lie in [0, 1] the gain is the share of the shortfall removed,
    (target - baseline) / (1 - baseline). On any other scale it is relative change, and a
    zero baseline cannot be measured relatively (any target would be an infinite gain), so
    it is refused rather than guessed. Effort and cost are relative reductions."""
    if kind in HIGHER_IS_BETTER:
        if target <= baseline:
            return 0.0, ""
        if baseline <= 1.0 and target <= 1.0:
            return min((target - baseline) / (1.0 - baseline), 1.0), ""
        if baseline == 0:
            return 0.0, "baseline 0 on a scale above 1 cannot be measured relatively"
        return min((target - baseline) / baseline, 1.0), ""
    if target >= baseline or baseline <= 0:
        return 0.0, ""
    return min((baseline - target) / baseline, 1.0), ""


def _assess(raw) -> tuple[dict, list[str]]:
    """Per-kind best gain over the material reasons, and why the others do not count.
    The reasons are the proposer's data: anything malformed is a problem, never a guess."""
    if not isinstance(raw, (list, tuple)):
        return {}, ["reasons must be a list of {kind, metric, baseline, target}"]
    if len(raw) > MAX_REASONS:
        return {}, [f"more than {MAX_REASONS} reasons"]
    gains: dict[str, float] = {}
    problems: list[str] = []
    for i, item in enumerate(raw):
        where = f"reason {i + 1}"
        if not isinstance(item, dict):
            problems.append(f"{where}: not a mapping")
            continue
        kind = item.get("kind")
        if kind not in REASON_KINDS:
            problems.append(f"{where}: {kind!r} is not one of {REASON_KINDS}")
            continue
        metric = item.get("metric")
        if not isinstance(metric, str) or not metric.strip():
            problems.append(f"{where}: no metric names what was measured")
            continue
        baseline, target = item.get("baseline"), item.get("target")
        if not (_number(baseline) and _number(target)):
            problems.append(f"{where}: baseline and target must be finite numbers of at least 0")
            continue
        gain, why = _gain(kind, float(baseline), float(target))
        if why:
            problems.append(f"{where}: {why}")
            continue
        if gain < MATERIAL_GAIN:
            problems.append(f"{where}: {kind} improves by {gain:.0%}, under the {MATERIAL_GAIN:.0%} floor")
            continue
        gains[kind] = max(gain, gains.get(kind, 0.0))
    return gains, problems


def evaluate(reasons) -> GateDecision:
    """The verdict on `reasons` alone: pure, archives nothing, runs nothing."""
    gains, problems = _assess(reasons)
    if gains:
        return GateDecision(True, "measurable reason: " + ", ".join(sorted(gains)),
                            F.score_value(gains, {}), gains, problems)
    reason = "no measurable reason (task success, manual effort, reliability or cost)"
    if problems:
        reason += ": " + "; ".join(problems)
    return GateDecision(False, reason, None, {}, problems)


def review(*, hypothesis: str, parent_version: str, scope: Iterable[str], origin: str,
           reasons, archive: A.EvolutionArchive) -> GateDecision:
    """Allow a proposal that carries a measurable reason; refuse and archive one that
    does not. Raises `evolution_archive.ArchiveError` when hypothesis, parent version
    or scope are not usable: a proposal that cannot even be described is not archived
    as a guess."""
    scope = list(scope)
    verdict = evaluate(reasons)
    if verdict.allowed:
        return verdict
    reason, problems = verdict.reason, verdict.problems
    prior = archive.find_rejected(parent_version=parent_version, hypothesis=hypothesis, scope=scope)
    if prior is not None:
        return GateDecision(False, f"{reason}; already refused as {prior['id']}", None, {},
                            problems, prior["id"])
    row = archive.record(
        objective=f"{GATE}: refuse an experiment with no measurable reason",
        parent_version=parent_version, candidate_version="not-built",
        hypothesis=hypothesis, scope=scope,
        baseline={"measurable_reasons": 0.0}, candidate={"measurable_reasons": 0.0},
        evaluation={"gate": GATE, "origin": str(origin), "refusal": reason,
                    "problems": problems, "paid_calls": 0},
        decision=A.REJECT, rollback_version=parent_version)
    return GateDecision(False, reason, None, {}, problems, row["id"])
