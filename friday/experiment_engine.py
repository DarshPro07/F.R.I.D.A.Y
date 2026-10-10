"""
Bounded experiments: buy the cheapest evidence that would change your mind,
and stop when it has.

The pattern this replaces is the speculative loop. A worker holds one
hypothesis ("the retry path is what's broken"), acts on it, runs the whole
suite to find out, and on failure tries again with the same hypothesis until
the attempt budget dies. It is expensive in exactly the way that is hardest
to see: every attempt pays for the deepest evidence available, and no attempt
is chosen because it would settle anything.

An `Experiment` here is the other shape. It names a hypothesis AND the
competing hypothesis it would lose to, the variables it holds still, a ladder
of measurements with a price and a discriminating power each, a budget, and
stop criteria. Its loop picks the measurement with the most expected
information per unit cost, updates its belief on what it observes, and stops
the moment the belief is resolved either way - or the moment nothing
affordable would move it, which is a result too and a much cheaper one than
finding out at attempt four.

Two seams it reuses rather than reinvents:

- `execution_economics.verification_depth()` already owns "how much evidence
  is enough for this risk". It sizes the ladder: a tiny bounded change may
  not buy an integration run, a core change may. This module never decides
  that itself.
- `continuous.failure_fingerprint()` already owns "the same failure happened
  again". Two attempts whose evidence fingerprints the same under the same
  hypothesis bought nothing, whatever the arithmetic says; the loop stops
  there (STUCK) instead of double-counting a repeat as new information.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

from friday import execution_economics as ee
from friday.continuous import failure_fingerprint

# ---------------------------------------------------------------------------
# Priced evidence - the ladder, sized by execution_economics
# ---------------------------------------------------------------------------

#: Relative price of one run at each verification depth, in units of one
#: targeted run. The absolute numbers do not matter; the shape does - deeper
#: evidence is more conclusive and super-linearly dearer, which is what a real
#: suite costs (a targeted module's tests against the whole regression).
DEPTH_COST = {
    ee.TARGETED: 1.0,
    ee.AFFECTED: 3.0,
    ee.INTEGRATION: 8.0,
    ee.FULL: 25.0,
}

#: How strongly a run at each depth separates the hypothesis from its
#: competitor: 0 is a coin flip, 1 is decisive. A targeted run can be right
#: about its own module and silent about the consumer that actually broke;
#: a full regression is nearly decisive and never quite is (flakes).
DEPTH_DISCRIMINATION = {
    ee.TARGETED: 0.6,
    ee.AFFECTED: 0.75,
    ee.INTEGRATION: 0.9,
    ee.FULL: 0.97,
}

#: Shallowest first. The one ordering of depth in this module.
DEPTH_LADDER = (ee.TARGETED, ee.AFFECTED, ee.INTEGRATION, ee.FULL)

# Terminal statuses of a run.
RESOLVED = "resolved"            # the hypothesis held, to the confidence asked
REFUTED = "refuted"              # the competing hypothesis held instead
INCONCLUSIVE = "inconclusive"    # nothing affordable would move the belief
STUCK = "stuck"                  # the same evidence again: no information left
OVER_BUDGET = "over_budget"      # the evidence budget ran out undecided

#: A speculative loop ends here. It has no notion of being wrong, so when the
#: thing it keeps asserting is false it simply runs out of attempts.
EXHAUSTED = "exhausted"


def depth_cap(econ: ee.TaskEconomics) -> str:
    """The deepest evidence this task's risk justifies buying.

    `verification_depth` is the owner of this rule; this is the thin read of
    it that the ladder needs."""
    scope, _reason = ee.verification_depth(econ)
    return scope


def ladder_for(cap: str) -> tuple[str, ...]:
    """Every depth up to and including the cap, shallowest first."""
    if cap not in DEPTH_LADDER:
        raise ValueError(f"unknown verification depth: {cap!r}")
    return DEPTH_LADDER[:DEPTH_LADDER.index(cap) + 1]


def evidence_budget(cap: str) -> float:
    """What the whole sanctioned ladder costs, climbed once.

    This is the bound: an experiment may buy every rung its risk justifies,
    and then it is done. Attempt five at full depth is not in the budget,
    which is the difference between this and a retry loop."""
    return sum(DEPTH_COST[d] for d in ladder_for(cap))


# ---------------------------------------------------------------------------
# Information, in bits
# ---------------------------------------------------------------------------

def entropy(p: float) -> float:
    """Shannon entropy of a two-outcome belief, in bits. 1.0 at p=0.5."""
    if p <= 0.0 or p >= 1.0:
        return 0.0
    return -(p * math.log2(p) + (1 - p) * math.log2(1 - p))


def _likelihoods(d: float) -> tuple[float, float]:
    """P(observation supports H | H) and P(observation supports H | not H),
    for a measurement of discriminating power `d`."""
    return (1.0 + d) / 2.0, (1.0 - d) / 2.0


def update(belief: float, d: float, supported: bool) -> float:
    """Bayes on one observation. `belief` is P(hypothesis), `d` the
    measurement's discriminating power, `supported` what it came back as."""
    hit, miss = _likelihoods(d)
    if not supported:
        hit, miss = miss, hit
    numerator = belief * hit
    denominator = numerator + (1 - belief) * miss
    if denominator <= 0.0:
        return belief
    return numerator / denominator


def expected_info_gain(belief: float, d: float) -> float:
    """Expected bits of entropy a measurement removes, before running it.

    This is what makes the cheap rung the right first buy: a shallow test
    asked when you know nothing removes most of the uncertainty a deep one
    would, at a fraction of the price."""
    before = entropy(belief)
    hit, miss = _likelihoods(d)
    p_supports = belief * hit + (1 - belief) * miss
    after = (p_supports * entropy(update(belief, d, True))
             + (1 - p_supports) * entropy(update(belief, d, False)))
    return max(0.0, before - after)


# ---------------------------------------------------------------------------
# The Experiment object
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Measurement:
    """One thing you can run to learn something, with its price."""

    name: str
    depth: str                    # an execution_economics verification depth
    cost: float = 0.0             # DEPTH_COST[depth] when left at zero
    discriminates: float = 0.0    # DEPTH_DISCRIMINATION[depth] when zero

    def __post_init__(self) -> None:
        if self.depth not in DEPTH_COST:
            raise ValueError(f"unknown verification depth: {self.depth!r}")
        if not self.cost:
            object.__setattr__(self, "cost", DEPTH_COST[self.depth])
        if not self.discriminates:
            object.__setattr__(self, "discriminates",
                               DEPTH_DISCRIMINATION[self.depth])
        if self.cost <= 0:
            raise ValueError("a measurement that costs nothing is not evidence")
        if not 0.0 <= self.discriminates < 1.0:
            raise ValueError("discriminates must be in [0, 1): nothing is "
                             "decisive, and a coin flip is not evidence")


@dataclass(frozen=True)
class StopCriteria:
    """When to stop buying evidence."""

    #: Belief at which the question is settled, either way: `confidence` for
    #: the hypothesis, `1 - confidence` for the competitor.
    confidence: float = 0.9
    #: Bits per unit cost below which a measurement is not worth running. The
    #: floor that turns "no affordable evidence would change my mind" into a
    #: result instead of another attempt.
    min_info_gain: float = 0.01
    #: Hard cap on measurements, independent of cost.
    max_attempts: int = 6

    def __post_init__(self) -> None:
        if not 0.5 < self.confidence < 1.0:
            raise ValueError("confidence must be in (0.5, 1.0)")
        if self.max_attempts < 1:
            raise ValueError("an experiment that runs nothing proves nothing")


@dataclass(frozen=True)
class Attempt:
    """One measurement, run. The per-attempt record the loop reasons over."""

    measurement: str
    depth: str
    cost: float
    supported: bool
    info_gain: float          # expected, at the belief it was chosen under
    belief_before: float
    belief_after: float
    hypothesis: str
    fingerprint: str


@dataclass(frozen=True)
class ExperimentResult:
    status: str
    conclusion: str           # the hypothesis that held, or "" when none did
    belief: float
    cost: float
    calls: int
    reason: str
    attempts: tuple[Attempt, ...] = ()

    @property
    def decided(self) -> bool:
        return self.status in (RESOLVED, REFUTED)


@dataclass(frozen=True)
class Experiment:
    """A bounded question: two hypotheses, priced evidence, and a stop rule."""

    hypothesis: str
    competing_hypothesis: str
    measurements: tuple[Measurement, ...]
    variables: tuple[str, ...] = ()        # what is held still across attempts
    prior: float = 0.5
    budget: float = 0.0                    # the ladder's cost when left at zero
    stop: StopCriteria = field(default_factory=StopCriteria)

    def __post_init__(self) -> None:
        if not self.hypothesis or not self.competing_hypothesis:
            raise ValueError("an experiment needs something to lose to: give "
                             "a competing hypothesis")
        if self.hypothesis == self.competing_hypothesis:
            raise ValueError("the hypotheses are the same string: that is a "
                             "belief, not an experiment")
        if not self.measurements:
            raise ValueError("an experiment with no measurement is a guess")
        if not 0.0 < self.prior < 1.0:
            raise ValueError("a prior of 0 or 1 cannot be updated")
        if not self.budget:
            object.__setattr__(
                self, "budget", sum(m.cost for m in self.measurements))
        if self.budget <= 0:
            raise ValueError("an experiment needs an evidence budget")

    # -- selection ------------------------------------------------------

    def next_measurement(self, belief: float, spent: float,
                         done: frozenset[str]) -> tuple[Measurement | None,
                                                        float, str]:
        """(measurement, expected gain, reason). The affordable, unused
        measurement with the most expected information per unit cost."""
        best: Measurement | None = None
        best_gain = 0.0
        best_key = (0.0, 0.0)
        cheapest_unaffordable = 0.0
        for m in self.measurements:
            if m.name in done:
                continue
            if spent + m.cost > self.budget:
                if not cheapest_unaffordable or m.cost < cheapest_unaffordable:
                    cheapest_unaffordable = m.cost
                continue
            gain = expected_info_gain(belief, m.discriminates)
            # Ties go to the cheaper rung, and a dead tie to the one declared
            # first, so the same experiment always runs the same way.
            key = (gain / m.cost, -m.cost)
            if best is None or key > best_key:
                best, best_gain, best_key = m, gain, key
        if best is None:
            if cheapest_unaffordable:
                return None, 0.0, (
                    f"{spent:.1f} of {self.budget:.1f} evidence units spent "
                    f"and the cheapest measurement left costs "
                    f"{cheapest_unaffordable:.1f}: out of evidence budget")
            return None, 0.0, "every measurement has been run"
        best_rate = best_key[0]
        if best_rate <= self.stop.min_info_gain:
            return None, best_gain, (
                f"the best evidence left ({best.name}, {best.cost:.1f}) "
                f"would buy {best_gain:.3f} bits: below the floor, so more "
                f"attempts are speculation")
        return best, best_gain, (
            f"{best.name} buys {best_gain:.3f} bits for {best.cost:.1f} "
            f"({best_rate:.3f} bits/unit): the best rate affordable")

    # -- the loop -------------------------------------------------------

    def run(self, observe) -> ExperimentResult:
        """Buy evidence until the question is settled or the budget is.

        `observe(measurement)` runs one measurement and returns either a bool
        (did it support the hypothesis?) or `(supported, evidence_text)`. The
        text is fingerprinted, so an observation that is the same failure as
        the last one stops the loop instead of being counted twice.
        """
        belief = self.prior
        spent = 0.0
        attempts: list[Attempt] = []
        done: set[str] = set()

        while True:
            settled = self._settled(belief)
            if settled is not None:
                status, conclusion = settled
                return self._result(status, conclusion, belief, spent,
                                    attempts,
                                    f"belief {belief:.3f} past the "
                                    f"{self.stop.confidence:.2f} threshold "
                                    f"after {len(attempts)} measurement(s)")
            if len(attempts) >= self.stop.max_attempts:
                return self._result(OVER_BUDGET, "", belief, spent, attempts,
                                    f"attempt cap {self.stop.max_attempts} "
                                    f"reached undecided at {belief:.3f}")

            measurement, gain, reason = self.next_measurement(
                belief, spent, frozenset(done))
            if measurement is None:
                status = (OVER_BUDGET if "budget" in reason else INCONCLUSIVE)
                return self._result(status, "", belief, spent, attempts,
                                    reason)

            supported, evidence = _observation(observe(measurement))
            done.add(measurement.name)
            spent += measurement.cost
            after = update(belief, measurement.discriminates, supported)
            # Keyed on the depth and the evidence, deliberately NOT on which
            # measurement produced it: "I changed the probe and got the same
            # error" is the case worth catching. With no evidence text there
            # is nothing to compare, so the name keeps those runs distinct
            # rather than falsely identical.
            fingerprint = failure_fingerprint(
                measurement.depth, evidence or f"<no evidence:{measurement.name}>")
            attempt = Attempt(
                measurement=measurement.name, depth=measurement.depth,
                cost=measurement.cost, supported=supported, info_gain=gain,
                belief_before=belief, belief_after=after,
                hypothesis=self.hypothesis, fingerprint=fingerprint)

            # continuous.py's rule, applied to evidence: the same failure
            # under the same hypothesis is not a second data point. Counting
            # it would walk the belief to confidence on one observation seen
            # twice, which is how a loop talks itself into being right.
            if attempts and _same_failure(attempts[-1], attempt):
                attempts.append(replace(attempt, belief_after=belief,
                                        info_gain=0.0))
                return self._result(
                    STUCK, "", belief, spent, attempts,
                    f"{measurement.name} returned the same evidence as "
                    f"{attempts[-2].measurement} under the same hypothesis: "
                    "no information left on this question")

            attempts.append(attempt)
            belief = after

    def _settled(self, belief: float) -> tuple[str, str] | None:
        if belief >= self.stop.confidence:
            return RESOLVED, self.hypothesis
        if belief <= 1.0 - self.stop.confidence:
            return REFUTED, self.competing_hypothesis
        return None

    def _result(self, status: str, conclusion: str, belief: float,
                spent: float, attempts: list[Attempt],
                reason: str) -> ExperimentResult:
        return ExperimentResult(status=status, conclusion=conclusion,
                                belief=belief, cost=spent,
                                calls=len(attempts), reason=reason,
                                attempts=tuple(attempts))


def _observation(observed) -> tuple[bool, str]:
    """Accept a bool or a (bool, evidence text) pair from `observe`."""
    if isinstance(observed, tuple):
        supported, evidence = observed
        return bool(supported), str(evidence or "")
    return bool(observed), ""


def _same_failure(previous: Attempt, current: Attempt) -> bool:
    """The `continuous.py` comparison: same fingerprint, same hypothesis.
    Only failing evidence can be stuck - two passes are two passes."""
    return (not current.supported and not previous.supported
            and current.fingerprint == previous.fingerprint
            and current.hypothesis == previous.hypothesis)


# ---------------------------------------------------------------------------
# The baseline this exists to beat
# ---------------------------------------------------------------------------

def speculative_loop(experiment: Experiment, observe, *,
                     max_attempts: int = 4,
                     depth: str = ee.FULL) -> ExperimentResult:
    """What an unsized retry loop costs, measured the same way.

    This is today's shape, not a straw man. Its one evidence setting is
    `depth`, and the default is FULL because that is what "run the tests"
    means to a worker nobody sized: no caller in this tree hands
    `verification_depth` to an executor, so a tiny local change and a core
    rewrite both pay for the whole regression. It has no competing
    hypothesis, so evidence against it reads as "not yet" and it retries
    until the attempt budget is gone. And it has no cost bound, because
    nobody is adding the attempts up.

    It does stop when the evidence supports the hypothesis, and `depth` can
    be lowered to model a loop that somehow guessed the right scope - a
    baseline that ignored a pass, or always overpaid, would prove nothing.
    """
    deepest = Measurement(name=f"{depth}-run", depth=depth)
    belief = experiment.prior
    spent = 0.0
    attempts: list[Attempt] = []
    for _ in range(max_attempts):
        supported, evidence = _observation(observe(deepest))
        spent += deepest.cost
        after = update(belief, deepest.discriminates, supported)
        attempts.append(Attempt(
            measurement=deepest.name, depth=deepest.depth, cost=deepest.cost,
            supported=supported,
            info_gain=expected_info_gain(belief, deepest.discriminates),
            belief_before=belief, belief_after=after,
            hypothesis=experiment.hypothesis,
            fingerprint=failure_fingerprint(
                deepest.depth, evidence or f"<no evidence:{deepest.name}>")))
        belief = after
        if supported:
            return ExperimentResult(
                status=RESOLVED, conclusion=experiment.hypothesis,
                belief=belief, cost=spent, calls=len(attempts),
                reason=f"the {deepest.depth} run passed on attempt "
                       f"{len(attempts)}",
                attempts=tuple(attempts))
    return ExperimentResult(
        status=EXHAUSTED, conclusion="", belief=belief, cost=spent,
        calls=len(attempts),
        reason=f"{max_attempts} attempts at {deepest.depth} depth and no "
               "conclusion: the loop has no hypothesis to fall back on",
        attempts=tuple(attempts))


# ---------------------------------------------------------------------------
# Building an experiment from a task, and the gate continuous.py calls
# ---------------------------------------------------------------------------

#: Argument keys a task's text can arrive under. `hermes_bridge` records it as
#: "task"; the objective compiler and the voice path use the others.
TEXT_KEYS = ("task", "goal", "prompt", "requirements", "description", "text")


def task_text(task: dict) -> str:
    """The task's own words, for the risk classifier. Falls back to the
    capability id, which classifies as a bounded change - never to nothing,
    because an unclassifiable task must still get a ladder."""
    arguments = task.get("arguments") or {}
    for key in TEXT_KEYS:
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return str(task.get("capability") or "")


def for_task(text: str, *, hypothesis: str, competing_hypothesis: str,
             code_refs: int = 0, acceptance: int = 0,
             variables: tuple[str, ...] = (),
             prior: float = 0.5,
             stop: StopCriteria | None = None) -> Experiment:
    """An Experiment whose ladder is sized by the task's risk.

    The two hypotheses and the variables are the caller's to state - no
    classifier can infer what you think is wrong. Everything priced comes
    from `execution_economics`."""
    econ = ee.classify_task(text, code_refs=code_refs, acceptance=acceptance)
    cap = depth_cap(econ)
    ladder = ladder_for(cap)
    return Experiment(
        hypothesis=hypothesis, competing_hypothesis=competing_hypothesis,
        measurements=tuple(Measurement(name=f"{d}-run", depth=d)
                           for d in ladder),
        variables=variables, prior=prior, budget=evidence_budget(cap),
        stop=stop or StopCriteria())


@dataclass(frozen=True)
class EvidencePlan:
    """How much evidence this attempt should buy."""

    depth: str                # the verification depth to hand the worker
    cap: str                  # the deepest this task's risk justifies
    spent: float              # priced evidence the earlier attempts bought
    budget: float             # the whole sanctioned ladder, climbed once
    reason: str
    attempts: int = 0


def evidence_plan(task: dict) -> EvidencePlan:
    """Size the next attempt's evidence: cheap rung first, deeper per attempt,
    never past what the task's risk justifies.

    This deliberately does NOT decide whether the attempt happens. How many
    attempts a task gets is already owned, by `continuous.py`'s attempt budget
    and its strategy-change guard, and a second limiter here parks runs that
    their own owner was still correctly retrying (nine tests said so). The
    unowned question - and the whole of EXP-001's cheap-evidence route - is
    how much verification one attempt pays for. Nothing in this tree hands an
    executor a depth today, so every attempt buys the whole regression.

    Escalating with the attempt is `verification_depth`'s own rule carried
    across a loop: "targeted tests; escalate only if they surface consumers".
    The first attempt buys the cheapest evidence that could settle it; an
    attempt that failed anyway has earned the next rung up.
    """
    econ = ee.classify_task(task_text(task))
    cap = depth_cap(econ)
    ladder = ladder_for(cap)
    attempts = max(0, int(task.get("attempts") or 0))
    rung = min(attempts, len(ladder) - 1)
    depth = ladder[rung]
    spent = sum(DEPTH_COST[d] for d in ladder[:rung])
    held = ("" if rung == attempts else
            f"; held at the {cap} cap from attempt {len(ladder)} on")
    return EvidencePlan(
        depth=depth, cap=cap, spent=spent, budget=evidence_budget(cap),
        attempts=attempts,
        reason=(f"attempt {attempts + 1} buys {depth} evidence "
                f"({DEPTH_COST[depth]:.1f} units; cap {cap} from "
                f"{econ.blast_radius}/{econ.consequence} risk){held}"))
