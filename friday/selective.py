"""
A router that would rather say nothing than say the wrong thing.

The deterministic router handles 23.7% of requests in 16 milliseconds and gets
13.6% of them wrong, two of those dangerously. Those numbers are not a router
that needs tuning; they are a router that has never been allowed to decline.

    the goal is not          handle as much as possible locally
    the goal is              act locally only where the evidence is strong

Selective classification is the name for this: let the thing abstain, and the
accuracy of what it does answer goes up. Twelve percent coverage with zero
dangerous errors beats forty percent with three, for a system holding the keys
to somebody's computer.

## Abstention is an outcome, not a failure

`ABSTAIN` costs exactly what Friday costs today - the request goes to the
cloud, which is where it goes now. So the fast path's worst case is the status
quo, and every rule here can be written to fail towards it.

    "pause it"  + music is playing      ->  local, referent grounded
    "pause it"  + nothing known         ->  ABSTAIN(AMBIGUOUS_REFERENT)
    "restart the song"                  ->  local, if the evidence is strong
    "restart it"                        ->  ABSTAIN(AMBIGUOUS_REFERENT)
    "should I restart it?"              ->  ABSTAIN(REASONING_REQUIRED)
    "restart the computer"              ->  ABSTAIN(RISK_REQUIRES_CLOUD)

The last one matters most: the reflex may *identify* `power_restart`
perfectly well. Identifying a capability and being allowed to run it are
different questions, and this module only answers the first.

## Positive evidence, never elimination

The rule that would have prevented both dangerous false actions measured so
far. `browser_close` won "minimise the browser" by being the only BROWSER
capability left after an unreliable filter, and "nothing else matched" is not
evidence for anything. A capability runs locally only when the sentence
contains something that *points at it*: a verb Friday can read, a noun naming
the thing, a grounded referent, a phrase that matches its own examples.

## Uncertainty is not one number

Kept per facet on purpose. A strong operation with an unreadable target is not
"70% confident", it is *certain about the verb and ignorant of the object* -
and those two states want different answers. Collapsing them into a single
score is how a confident-looking number gets attached to a guess.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from friday import capabilities as C
from friday import request_shape as RS
from friday import semantics as S


logger = logging.getLogger("friday-agent.selective")

# ---------------------------------------------------------------------------
# Why it declined
# ---------------------------------------------------------------------------

NO_DOMAIN = "NO_DOMAIN"                        # nothing said what this is about
AMBIGUOUS_DOMAIN = "AMBIGUOUS_DOMAIN"          # it could be two things
AMBIGUOUS_TARGET = "AMBIGUOUS_TARGET"
AMBIGUOUS_REFERENT = "AMBIGUOUS_REFERENT"      # "it" - which it?
LOW_MARGIN = "LOW_MARGIN"                      # two candidates too close
CONFLICTING_OPERATION = "CONFLICTING_OPERATION"
OUT_OF_DOMAIN = "OUT_OF_DOMAIN"                # not a capability request
COMPOUND_REQUEST = "COMPOUND_REQUEST"
REASONING_REQUIRED = "REASONING_REQUIRED"
RISK_REQUIRES_CLOUD = "RISK_REQUIRES_CLOUD"
NEGATED = "NEGATED"                            # the sentence forbids it
NO_POSITIVE_EVIDENCE = "NO_POSITIVE_EVIDENCE"  # it won by elimination
NOT_ALLOWLISTED = "NOT_ALLOWLISTED"            # right answer, wrong path
MISSING_ARGUMENTS = "MISSING_ARGUMENTS"

ABSTENTIONS = (
    NO_DOMAIN, AMBIGUOUS_DOMAIN, AMBIGUOUS_TARGET, AMBIGUOUS_REFERENT,
    LOW_MARGIN, CONFLICTING_OPERATION, OUT_OF_DOMAIN, COMPOUND_REQUEST,
    REASONING_REQUIRED, RISK_REQUIRES_CLOUD, NEGATED, NO_POSITIVE_EVIDENCE,
    NOT_ALLOWLISTED, MISSING_ARGUMENTS,
)

# ---------------------------------------------------------------------------
# Where a failure came from, so fixes stay mechanical
# ---------------------------------------------------------------------------

E_REQUEST_SHAPE = "REQUEST_SHAPE"
E_OPERATION = "OPERATION"
E_TARGET = "TARGET"
E_REFERENT = "REFERENT"
E_DOMAIN = "DOMAIN"
E_CANDIDATE_FILTER = "CANDIDATE_FILTER"
E_RANKING = "RANKING"
E_ARGUMENT = "ARGUMENT"
E_POLICY = "POLICY"
E_NEGATION = "NEGATION"
E_HYPOTHETICAL = "HYPOTHETICAL"
E_MULTI_INTENT = "MULTI_INTENT"
E_OOD = "OOD"

TAXONOMY = (E_REQUEST_SHAPE, E_OPERATION, E_TARGET, E_REFERENT, E_DOMAIN,
            E_CANDIDATE_FILTER, E_RANKING, E_ARGUMENT, E_POLICY, E_NEGATION,
            E_HYPOTHETICAL, E_MULTI_INTENT, E_OOD)

#: Which part of the sequence an abstention came from. Used by the evaluation
#: to say *where* coverage is being lost rather than only how much.
BLAMED_ON = {
    REASONING_REQUIRED: E_REQUEST_SHAPE,
    OUT_OF_DOMAIN: E_OOD,
    COMPOUND_REQUEST: E_MULTI_INTENT,
    NEGATED: E_NEGATION,
    NO_DOMAIN: E_TARGET,
    AMBIGUOUS_DOMAIN: E_DOMAIN,
    AMBIGUOUS_TARGET: E_TARGET,
    AMBIGUOUS_REFERENT: E_REFERENT,
    CONFLICTING_OPERATION: E_OPERATION,
    LOW_MARGIN: E_RANKING,
    NO_POSITIVE_EVIDENCE: E_CANDIDATE_FILTER,
    RISK_REQUIRES_CLOUD: E_POLICY,
    NOT_ALLOWLISTED: E_POLICY,
    MISSING_ARGUMENTS: E_ARGUMENT,
}


# ---------------------------------------------------------------------------
# What was said, per facet
# ---------------------------------------------------------------------------


@dataclass
class Evidence:
    """
    What is known about the request, kept apart on purpose.

    A strong operation with an unreadable target is not a middling score, it
    is certainty about the verb and ignorance of the object - and no single
    number can say that.
    """

    shape: str = RS.COMMAND
    operation: str = ""
    target: str = ""
    referent: str = ""
    referent_grounded: bool = False
    #: The things in the sentence that point at the chosen capability. Empty
    #: means it won by elimination, which is not winning.
    positive: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "shape": self.shape, "operation": self.operation,
            "target": self.target, "referent": self.referent,
            "referent_grounded": self.referent_grounded,
            "positive": list(self.positive),
        }


@dataclass
class Decision:
    """Route or abstain. Never both, never neither."""

    capability: str = ""
    arguments: dict = field(default_factory=dict)
    abstained: str = ""
    because: str = ""
    evidence: Evidence = field(default_factory=Evidence)
    winner_score: float = 0.0
    runner_up: str = ""
    runner_up_score: float = 0.0
    margin: float = 0.0
    milliseconds: float = 0.0

    @property
    def routes(self) -> bool:
        return bool(self.capability) and not self.abstained

    @property
    def blame(self) -> str:
        return BLAMED_ON.get(self.abstained, "")

    def to_dict(self) -> dict:
        return {
            "capability": self.capability, "arguments": self.arguments,
            "abstained": self.abstained, "because": self.because,
            "evidence": self.evidence.to_dict(),
            "winner_score": self.winner_score, "runner_up": self.runner_up,
            "runner_up_score": self.runner_up_score, "margin": self.margin,
            "milliseconds": round(self.milliseconds, 2),
        }


def route(capability: str, arguments: dict, evidence: Evidence, **rest) -> Decision:
    return Decision(capability=capability, arguments=arguments,
                    evidence=evidence, **rest)


def abstain(reason: str, because: str, evidence: Evidence | None = None,
            **rest) -> Decision:
    return Decision(abstained=reason, because=because,
                    evidence=evidence or Evidence(), **rest)


# ---------------------------------------------------------------------------
# What the conversation already knows
# ---------------------------------------------------------------------------


@dataclass
class Context:
    """
    Resources this conversation has established, so "it" can mean something.

    Deliberately tiny and explicit. A referent is grounded when the previous
    turn *made* something - opened an app, started music - not when a model
    thinks it can guess what was meant. Guessing destructive referents is the
    failure this whole module is arranged around.
    """

    #: target kind -> what it refers to. {"MEDIA": "daft punk", "APPLICATION": "Paint"}
    active: dict[str, str] = field(default_factory=dict)

    def resolve(self, target: str) -> str:
        return self.active.get(target or "", "")


# ---------------------------------------------------------------------------
# Thresholds. Chosen on the calibration set, never on the holdout.
# ---------------------------------------------------------------------------

#: How far ahead the winner must be before a difference counts as a decision.
#: A score of .61 against .60 is a tie that happens to have an ordering.
MARGIN = 6.0

#: Risk levels a reflex may execute. Identifying a capability and being
#: allowed to run it are different questions; this module answers the first
#: and `reflex_direct_allowed` answers the second.
EXECUTABLE_RISK = ("LOW",)


def reflex_direct_allowed(capability_id: str) -> bool:
    """
    Whether this capability may be executed straight off the fast path.

    Derived from the policy metadata that already exists rather than a second
    table beside it - a second copy of a security decision drifts, and this
    codebase has said so about `requires_approval` already.

    Reversible and low-risk only. `power_shutdown` may be *identified* by the
    reflex router perfectly well; it is never run by it.
    """
    capability = C.by_id(capability_id)
    if capability is None:
        return False
    if capability.requires_approval:
        return False
    if capability.risk not in EXECUTABLE_RISK:
        return False
    from friday import reflex as X

    return not X.is_dangerous(capability_id)


# ---------------------------------------------------------------------------
# The sequence
# ---------------------------------------------------------------------------


def decide(text: str, *, context: Context | None = None) -> Decision:
    """
    Route this locally, or say which step could not carry it.

    Nine steps, in the order that lets the cheapest refusal come first: shape
    before meaning, meaning before ranking, ranking before risk. A sentence
    that is a question never reaches the scorer, which is both faster and the
    only way "should I restart my PC?" reliably fails to become a restart.
    """
    started = time.monotonic()
    context = context or Context()
    text = (text or "").strip()

    def done(decision: Decision) -> Decision:
        decision.milliseconds = (time.monotonic() - started) * 1000
        return decision

    # 1. shape ------------------------------------------------------------
    shape = RS.read(text)
    evidence = Evidence(shape=shape.kind)

    if shape.kind == RS.REASONING:
        return done(abstain(REASONING_REQUIRED,
                            f"asks Friday to think: {shape.because}", evidence))
    if shape.kind == RS.HYPOTHETICAL:
        return done(abstain(REASONING_REQUIRED,
                            f"hypothetical: {shape.because}", evidence))
    if shape.kind == RS.CONTROL:
        return done(abstain(OUT_OF_DOMAIN, shape.because, evidence))
    if shape.kind == RS.COMPOUND:
        return done(abstain(COMPOUND_REQUEST,
                            f"{len(shape.parts)} outcomes, not one reflex",
                            evidence))

    if shape.forbidden and not _asks_for_anything(text, shape.forbidden):
        return done(abstain(NEGATED,
                            "forbids something and asks for nothing",
                            evidence))

    text = shape.command or text

    # 2. operation --------------------------------------------------------
    operation = S.for_request(text)
    evidence.operation = operation or ""

    # 3. target -----------------------------------------------------------
    target = S.target_for_request(text)
    evidence.target = target or ""

    if not operation and not target:
        # Nothing in the sentence points anywhere. This is where elimination
        # used to take over, and elimination is what produced `browser_close`
        # for "minimise the browser".
        return done(abstain(NO_DOMAIN,
                            "neither a readable verb nor a named object",
                            evidence))

    # 4. referent ---------------------------------------------------------
    from friday import reflex as X

    needs_referent = bool(X._PRONOUN.search(text)) and not target
    if needs_referent:
        resolved = _resolve_referent(text, context)
        evidence.referent = resolved
        evidence.referent_grounded = bool(resolved)
        if not resolved:
            return done(abstain(AMBIGUOUS_REFERENT,
                                "a pronoun with nothing behind it", evidence))
        target = evidence.target = _target_of(resolved, context)

    # An ending verb with an ungrounded object is the one case where guessing
    # costs something no later turn can undo.
    if not X.is_grounded(text) and not evidence.referent_grounded:
        return done(abstain(AMBIGUOUS_REFERENT,
                            "ends something without saying what", evidence))

    # 5. candidates -------------------------------------------------------
    from friday import planner as P

    found = P.candidates(operation or S.READ, target or "")
    if not found:
        return done(abstain(NO_DOMAIN,
                            f"no capability does {operation or 'that'} to a "
                            f"{target or 'thing unnamed'}", evidence))

    # 6. score ------------------------------------------------------------
    scored = sorted(((_fit(text, cid), cid) for cid in found), reverse=True)
    winner_score, winner = scored[0]
    runner_up_score, runner_up = (scored[1] if len(scored) > 1 else (0.0, ""))
    margin = winner_score - runner_up_score

    common = dict(winner_score=float(winner_score), runner_up=runner_up,
                  runner_up_score=float(runner_up_score), margin=float(margin))

    # 7. positive evidence ------------------------------------------------
    evidence.positive = _positive_evidence(text, winner, operation, target,
                                           evidence)
    if not _points_at_one_capability(evidence.positive):
        return done(abstain(NO_POSITIVE_EVIDENCE,
                            f"{winner} matches the domain, not the request",
                            evidence, **common))

    if (shape.kind == RS.QUESTION
            and S.for_capability(winner)[0] not in _INFORMATIONAL):
        return done(abstain(CONFLICTING_OPERATION,
                            f"a question cannot be answered by {winner}",
                            evidence, **common))

    if not operation and S.for_capability(winner)[0] in _INFORMATIONAL:
        return done(abstain(CONFLICTING_OPERATION,
                            f"the verb was not read and {winner} only observes",
                            evidence, **common))

    # 8. margin -----------------------------------------------------------
    if runner_up and margin < MARGIN:
        return done(abstain(LOW_MARGIN,
                            f"{winner} beat {runner_up} by {margin:.0f}",
                            evidence, **common))

    # 9. negation, risk, arguments ----------------------------------------
    if RS.is_forbidden(text, _words_of(winner)):
        return done(abstain(NEGATED, f"the sentence forbids {winner}",
                            evidence, **common))

    if not reflex_direct_allowed(winner):
        return done(abstain(RISK_REQUIRES_CLOUD,
                            f"{winner} is identified but not reflex-executable",
                            evidence, **common))

    arguments = _arguments(text, winner, evidence, context)
    missing = _missing(winner, arguments)
    if missing:
        return done(abstain(MISSING_ARGUMENTS,
                            f"{winner} needs {', '.join(missing)}",
                            evidence, **common))

    return done(route(winner, arguments, evidence, **common))

_INFORMATIONAL = (S.READ, S.LIST, S.SEARCH, S.FOLLOW_UP)

# Restored from the .pyc oracle: proven by a LOAD_CONST/STORE_NAME
# pair in the running system's bytecode, present in no source candidate.
_SPECIFIC = ('operation:', 'phrase', 'name:', 'referent')


def _points_at_one_capability(positive: tuple[str, ...]) -> bool:
    return any(item.startswith(_SPECIFIC) for item in positive)


def _asks_for_anything(text: str, forbidden: tuple[str, ...]) -> bool:
    """Whether anything survives once the forbidden spans are removed."""
    from friday import capability_router as R

    remaining = text
    for span in forbidden:
        remaining = remaining.replace(span, " ")
    return len(R._content(remaining.lower())) > 0


# ---------------------------------------------------------------------------
# The pieces
# ---------------------------------------------------------------------------


def _fit(text: str, capability_id: str) -> float:
    """How much the words of the request point at this capability."""
    from friday import planner as P

    return float(P._fit(text, capability_id))


def _positive_evidence(text: str, capability_id: str, operation: str | None,
                       target: str | None, evidence: Evidence) -> tuple[str, ...]:
    """
    What in the sentence points at this capability.

    Never "everything else was filtered out". Elimination is how
    `browser_close` won a request to minimise a window: it was the only
    BROWSER capability in MOVE's neighbourhood, and being alone is not being
    right.
    """
    from friday import capability_router as R

    found: list[str] = []
    meta = C.by_id(capability_id)
    lowered = text.lower()

    if operation:
        capability_operation, _ = S.for_capability(capability_id)
        if capability_operation == operation:
            found.append(f"operation:{operation}")
    if target:
        capability_target = S.for_capability(capability_id)[1]
        if capability_target == target:
            found.append(f"target:{target}")
    if meta and R._phrase_score(lowered, meta.intent_examples) > 0:
        found.append("phrase")
    # A word of the capability's own name in the sentence.
    name_words = set(capability_id.lower().split("_"))
    said = set(R._content(lowered))
    shared = name_words & said
    if shared:
        found.append("name:" + ",".join(sorted(shared)))
    if evidence.referent_grounded:
        found.append("referent")
    return tuple(found)


def _resolve_referent(text: str, context: Context) -> str:
    """
    What "it" stands for, from what this conversation established.

    Only from `context.active`. Not from a guess about what is probably on
    the machine, and not from what a model reckons - a resource nobody in
    this conversation created is not a referent, it is a hope.
    """
    if len(context.active) == 1:
        return next(iter(context.active.values()))
    # More than one thing is live. The verb may still say which.
    operation = S.for_request(text)
    for kind, value in context.active.items():
        if operation and S.compatible(operation, _any_capability_for(kind)):
            return value
    return ""


def _any_capability_for(target: str) -> str:
    for capability in C._ALL:
        if S.for_capability(capability.id)[1] == target:
            return capability.id
    return ""


def _target_of(referent: str, context: Context) -> str:
    for kind, value in context.active.items():
        if value == referent:
            return kind
    return ""


def _words_of(capability_id: str) -> tuple[str, ...]:
    meta = C.by_id(capability_id)
    words = list(capability_id.split("_"))
    if meta:
        words += [word for word in meta.description.lower().split()[:6]]
    return tuple(words)


def _arguments(text: str, capability_id: str, evidence: Evidence,
               context: Context) -> dict:
    from friday import planner as P

    goal = P.Goal(goal_id="r1", intent=text,
                  operation=evidence.operation or S.READ,
                  target=evidence.target, entity=P._entity(text),
                  capability=capability_id)
    arguments = dict(P.arguments_for(goal))
    if evidence.referent and not arguments:
        for name in ("name", "pattern", "query"):
            required = _required(capability_id)
            if name in required:
                arguments[name] = evidence.referent
                break
    return arguments


def _required(capability_id: str) -> tuple[str, ...]:
    from friday import capability_runtime as R

    return R.required_arguments(capability_id) or ()


def _missing(capability_id: str, arguments: dict) -> list[str]:
    return [name for name in _required(capability_id)
            if not str(arguments.get(name) or "").strip()]
