"""
The low-overhead loop for a simple, bounded request (COG-005).

A request the complexity classifier calls SIMPLE does not need the Objective
OS: no planner, no task graph, no workers. It needs one action, a look at what
happened, and either "done" or one more action. That is this loop - the
act, observe, update, repeat pattern of MiniAGI, none of its runtime.

The shape is fixed so that nothing in it is a model's to decide:

* An action is one strict `ActionSpec` (intent, capability, arguments, expected
  postcondition, confidence). Anything that does not validate is not run.
* Every action goes through the dispatch port `capability_runtime.build_dispatch`
  returns, so the execution envelope, the emergency stop and the policy engine
  apply exactly as they do for a conversation. There is no other path out of
  this module; it starts no process.
* The postcondition is checked by code against what the capability returned,
  never by the proposer's say-so.
* The bounds are enforced before a step runs: `MAX_STEPS` actions,
  `MAX_TOKENS` and `MAX_USD` across the proposer's calls, and the same
  action at most `1 + MAX_REPEATS` times.
* A task found to be complex is handed to the Objective OS, whole, and the
  loop ends. It never adds steps in place: the first plan that outgrows the
  bounds is exactly the case the planner exists for. The planner sees only the
  request, so if an action that cannot safely be repeated has already run, the
  request is not handed over: the loop stops BLOCKED and a person decides.

What a step costs is what the proposer reports (`Proposal.tokens` and
`Proposal.cost_usd`; for a model call, the gateway's `cost_usd` telemetry).
The loop has its own ceilings on purpose: `objective_budget.CLASS_LIMITS` and
the gateway's SIMPLE ceiling are for objectives and for single calls, and a
loop made of both would be bounded by neither.
"""
from __future__ import annotations

import dataclasses
import inspect
import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping

from friday import capabilities as K
from friday import contracts as c
from friday import objectives as O
from friday import task_class as TC

MAX_STEPS = 8
MAX_TOKENS = 6000
MAX_USD = 0.10
#: The same (capability, arguments) may run once more, not twice more.
MAX_REPEATS = 1
#: Below this the proposer is guessing; a guess is not run.
MIN_CONFIDENCE = 0.5
#: A loop is minutes of work. Its envelope expires well before an objective's.
LOOP_LIFETIME = timedelta(minutes=10)

_INTENT_CHARS = 200
_CAPABILITY_CHARS = 100
_ARGUMENT_CHARS = 4000
_NEEDLE_CHARS = 200
_OBSERVATION_CHARS = 500

DONE = "done"
ESCALATED = "escalated"
BLOCKED = "blocked"
OUTCOMES = (DONE, ESCALATED, BLOCKED)

_ACTION_FIELDS = ("intent", "capability", "arguments", "postcondition",
                  "confidence")
_POSTCONDITION_FIELDS = ("status", "output_contains")
#: Approval is parked and answered by a person, never supplied by a proposer.
_FORBIDDEN_ARGUMENTS = ("approved_by",)


class SchemaError(ValueError):
    """A proposed action does not match the schema. Nothing ran."""


@dataclass(frozen=True)
class LoopBounds:
    max_steps: int = MAX_STEPS
    max_tokens: int = MAX_TOKENS
    max_usd: float = MAX_USD
    max_repeats: int = MAX_REPEATS
    min_confidence: float = MIN_CONFIDENCE


def _text(value: Any, *, field_name: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SchemaError(f"{field_name} must be a non-empty string")
    if len(value) > limit:
        raise SchemaError(f"{field_name} is longer than {limit} characters")
    return value.strip()


@dataclass(frozen=True)
class ActionSpec:
    """One action: what for, which capability, with what, and how to tell."""

    intent: str
    capability: str
    arguments: Mapping[str, Any]
    postcondition: Mapping[str, str]
    confidence: float

    @classmethod
    def from_dict(cls, raw: Any) -> "ActionSpec":
        if not isinstance(raw, Mapping):
            raise SchemaError("an action must be an object")
        missing = [name for name in _ACTION_FIELDS if name not in raw]
        if missing:
            raise SchemaError(f"an action is missing {', '.join(missing)}")
        extra = sorted(str(name) for name in raw if name not in _ACTION_FIELDS)
        if extra:
            raise SchemaError(f"an action has unknown fields: {', '.join(extra)}")

        intent = _text(raw["intent"], field_name="intent", limit=_INTENT_CHARS)
        capability = _text(raw["capability"], field_name="capability",
                           limit=_CAPABILITY_CHARS)
        if not all(ch.isalnum() or ch in "_.-" for ch in capability):
            raise SchemaError("capability is a registered id, not free text")

        arguments = raw["arguments"]
        if not isinstance(arguments, Mapping) or not all(
                isinstance(key, str) for key in arguments):
            raise SchemaError("arguments must be an object with string keys")
        forbidden = [name for name in _FORBIDDEN_ARGUMENTS if name in arguments]
        if forbidden:
            raise SchemaError(f"arguments may not carry {forbidden[0]}: an "
                              "approval is answered by a person, not proposed")
        try:
            encoded = json.dumps(arguments, sort_keys=True)
        except (TypeError, ValueError) as exc:
            raise SchemaError(f"arguments must be plain JSON: {exc}") from exc
        if len(encoded) > _ARGUMENT_CHARS:
            raise SchemaError(f"arguments are larger than {_ARGUMENT_CHARS} characters")

        expected = raw["postcondition"]
        if not isinstance(expected, Mapping):
            raise SchemaError("postcondition must be an object")
        unknown = sorted(str(name) for name in expected
                         if name not in _POSTCONDITION_FIELDS)
        if unknown:
            raise SchemaError(f"postcondition has unknown fields: {', '.join(unknown)}")
        if expected.get("status") not in c.CLAIMABLE:
            raise SchemaError(
                f"postcondition.status must be one of {list(c.CLAIMABLE)}")
        postcondition = {"status": expected["status"]}
        if "output_contains" in expected:
            postcondition["output_contains"] = _text(
                expected["output_contains"], field_name="postcondition.output_contains",
                limit=_NEEDLE_CHARS)

        confidence = raw["confidence"]
        if (isinstance(confidence, bool) or not isinstance(confidence, (int, float))
                or not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0):
            raise SchemaError("confidence must be a number from 0 to 1")

        return cls(intent=intent, capability=capability,
                   arguments=json.loads(encoded), postcondition=postcondition,
                   confidence=float(confidence))

    def fingerprint(self) -> str:
        """Two actions with the same one are the same action."""
        return self.capability + "\0" + json.dumps(self.arguments, sort_keys=True)

    def to_dict(self) -> dict:
        return {"intent": self.intent, "capability": self.capability,
                "arguments": dict(self.arguments),
                "postcondition": dict(self.postcondition),
                "confidence": self.confidence}


@dataclass(frozen=True)
class Proposal:
    """What the proposer hands back for one step, and what it cost."""

    #: An `ActionSpec` or the raw object a model produced; the loop validates it.
    action: Any = None
    tokens: int = 0
    cost_usd: float = 0.0
    #: Non-empty when the proposer judges the task too big for this loop.
    complex_because: str = ""

    def __post_init__(self) -> None:
        if isinstance(self.tokens, bool) or not isinstance(self.tokens, int) \
                or self.tokens < 0:
            raise ValueError("a proposal's tokens are a count, not negative")
        if isinstance(self.cost_usd, bool) or not isinstance(self.cost_usd, (int, float)) \
                or not math.isfinite(self.cost_usd) or self.cost_usd < 0:
            raise ValueError("a proposal's cost_usd is a finite amount, not negative")


@dataclass(frozen=True)
class Step:
    """One action and what was observed of it."""

    index: int
    action: ActionSpec
    status: str
    error: str
    output: str
    met: bool

    def to_dict(self) -> dict:
        return {"index": self.index, "action": self.action.to_dict(),
                "status": self.status, "error": self.error,
                "output": self.output, "postcondition_met": self.met}


@dataclass
class LoopState:
    """What the proposer sees before each step."""

    request: str
    steps: list[Step] = field(default_factory=list)
    tokens: int = 0
    cost_usd: float = 0.0


@dataclass
class LoopResult:
    outcome: str
    reason: str
    steps: list[Step]
    tokens: int
    cost_usd: float
    run_id: str
    #: What the Objective OS answered, when the loop handed the request on.
    escalation: dict | None = None

    def to_dict(self) -> dict:
        return {"outcome": self.outcome, "reason": self.reason,
                "steps": [step.to_dict() for step in self.steps],
                "tokens": self.tokens, "cost_usd": self.cost_usd,
                "run_id": self.run_id, "escalation": self.escalation}


def loop_envelope(run: c.Run, bounds: LoopBounds = LoopBounds()) -> c.Envelope:
    """
    The envelope a simple loop runs inside: the one the Objective OS would mint
    for this caller, narrowed to a few minutes, the loop's own ceilings, and
    without the objective control plane - starting an objective is this loop's
    escalation, never something a proposer asks for as an action.
    """
    minted = O.envelope_for(run.run_id, (), provenance=run.provenance,
                            attended=run.attended)
    expires = (datetime.now(timezone.utc) + LOOP_LIFETIME).isoformat(timespec="seconds")
    return dataclasses.replace(
        minted,
        allowed_capabilities=(c.ANY_CAPABILITY,),
        blocked_capabilities=tuple(sorted(O.OBJECTIVE_CONTROL_CAPABILITIES)),
        token_limit=bounds.max_tokens, tool_call_limit=bounds.max_steps,
        expires_at=expires)


def postcondition_met(action: ActionSpec, observed: Mapping[str, Any]) -> bool:
    """The code's answer to "did it happen", from what the capability returned."""
    expected = action.postcondition
    if observed.get("status") != expected["status"]:
        return False
    needle = expected.get("output_contains")
    if needle is None:
        return True
    return needle.lower() in _preview(observed.get("output"), limit=None).lower()


def _preview(value: Any, *, limit: int | None = _OBSERVATION_CHARS) -> str:
    if value is None:
        return ""
    text = value if isinstance(value, str) else json.dumps(value, default=str,
                                                           sort_keys=True)
    return text if limit is None or len(text) <= limit else text[:limit] + "..."


async def _settled(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


#: An action that ended in one of these changed nothing, so it cannot be done twice.
_NO_EFFECT = (c.FAILED, c.CANCELLED, c.NOT_PERMITTED, c.UNSUPPORTED,
              c.NOT_CARRIED_OUT, c.NOT_CONFIGURED)


def _may_repeat_an_effect(step: Step) -> bool:
    """
    True when running this step's capability again could do the thing twice.

    Read-only and idempotent capabilities are safe to run again; a capability
    the registry does not declare is not known to be, so it counts as unsafe.
    """
    if step.status in _NO_EFFECT:
        return False
    declared = K.by_id(step.action.capability)
    return declared is None or not (declared.read_only or declared.idempotent)


def escalate_to_objective_os(run: c.Run, request: str) -> dict:
    """
    Hand the original request to the Objective OS as a normal objective: it is
    planned, compiled and run by `objectives.start`, with the caller's lineage
    and under the same policy gate as any objective started in conversation.
    """
    from friday.toolsets import objectives as objective_tools

    handoff = c.Run.create(request, "objectives.start", provenance=run.provenance)
    handoff.attended = run.attended
    return objective_tools.objective_start(handoff, request).to_dict()


async def run_simple(request: str, dispatch: Callable[..., Any],
                     propose: Callable[[LoopState], Any], *,
                     run: c.Run | None = None,
                     escalate: Callable[[c.Run, str], Any] = escalate_to_objective_os,
                     bounds: LoopBounds = LoopBounds()) -> LoopResult:
    """
    Run one simple request as act, observe, update, repeat.

    `dispatch` is the port `capability_runtime.build_dispatch()` returns;
    `propose(state)` returns a `Proposal`. The loop ends in exactly one of:
    DONE (the postcondition held), BLOCKED (a gate - policy, envelope, the
    emergency stop - refused an action; asking again would only repeat the
    question the gate already answered; or the request needs the Objective OS
    but an action that cannot safely be repeated has already run) or ESCALATED
    (the request is handed to the Objective OS and nothing more runs here).
    """
    text = (request or "").strip()
    run = run or c.Run.create(text[:200])
    if run.envelope is None:
        run.envelope = loop_envelope(run, bounds)
    state = LoopState(request=text)
    seen: dict[str, int] = {}

    async def hand_over(reason: str) -> LoopResult:
        # The Objective OS plans from the request alone, so it cannot be told
        # what already ran. If that could be done again, a person decides.
        done = next((step for step in state.steps if _may_repeat_an_effect(step)), None)
        if done is not None:
            return LoopResult(
                BLOCKED,
                f"SIDE EFFECT: step {done.index} ({done.action.capability}) "
                "already ran and cannot safely be repeated, and the Objective "
                f"OS would plan the whole request again; not handed over "
                f"({reason})",
                state.steps, state.tokens, state.cost_usd, run.run_id)
        answer = await _settled(escalate(run, text))
        return LoopResult(ESCALATED, reason, state.steps, state.tokens,
                          state.cost_usd, run.run_id, escalation=answer)

    klass = TC.classify(text)
    if klass.task_class != TC.SIMPLE:
        return await hand_over(
            f"not a simple task: the classifier says {klass.task_class} "
            f"({klass.reason})")

    for index in range(1, bounds.max_steps + 1):
        if state.tokens >= bounds.max_tokens or state.cost_usd >= bounds.max_usd:
            return await hand_over(
                f"the loop's budget is spent ({state.tokens} tokens, "
                f"${state.cost_usd:.4f}) and the postcondition is unmet")

        proposal = await _settled(propose(state))
        state.tokens += proposal.tokens
        state.cost_usd = round(state.cost_usd + proposal.cost_usd, 6)
        if state.tokens > bounds.max_tokens or state.cost_usd > bounds.max_usd:
            return await hand_over(
                f"proposing step {index} took the loop past its budget "
                f"({state.tokens} tokens, ${state.cost_usd:.4f}); it was not run")
        if proposal.complex_because:
            return await hand_over(f"the task is complex: {proposal.complex_because}")

        try:
            action = (proposal.action if isinstance(proposal.action, ActionSpec)
                      else ActionSpec.from_dict(proposal.action))
        except SchemaError as exc:
            return await hand_over(f"step {index} was not a valid action: {exc}")
        if action.confidence < bounds.min_confidence:
            return await hand_over(
                f"step {index} was proposed at confidence {action.confidence}, "
                f"below {bounds.min_confidence}; a guess is not run")
        if action.capability in O.OBJECTIVE_CONTROL_CAPABILITIES:
            return await hand_over(
                f"step {index} asks for {action.capability}: that is an "
                "objective, not a simple action")
        refusal = run.envelope.permits(action.capability)
        if refusal:
            return LoopResult(BLOCKED, f"ENVELOPE: {refusal}", state.steps,
                              state.tokens, state.cost_usd, run.run_id)

        key = action.fingerprint()
        if seen.get(key, 0) > bounds.max_repeats:
            return await hand_over(
                f"step {index} repeats {action.capability} with the same "
                f"arguments a third time: the loop is not converging")
        seen[key] = seen.get(key, 0) + 1

        try:
            observed = await _settled(dispatch(
                action.capability, dict(action.arguments), run=run))
        except LookupError as exc:
            # A capability id that is not registered anywhere: the proposer's
            # mistake, observed like any failure so it can pick another.
            observed = {"status": c.FAILED, "error": str(exc), "output": None}

        status = str(observed.get("status") or "")
        error = str(observed.get("error") or "")
        met = postcondition_met(action, observed)
        state.steps.append(Step(index, action, status, error,
                                _preview(observed.get("output")), met))
        if met:
            return LoopResult(
                DONE, f"postcondition met after {index} action(s)", state.steps,
                state.tokens, state.cost_usd, run.run_id)
        if status in (c.NOT_PERMITTED, c.CANCELLED):
            return LoopResult(
                BLOCKED, error or f"{action.capability} was {status}", state.steps,
                state.tokens, state.cost_usd, run.run_id)

    return await hand_over(
        f"{bounds.max_steps} actions ran and the postcondition is still unmet")


async def route_simple(request: str, dispatch: Callable[..., Any],
                       propose: Callable[[LoopState], Any], *,
                       run: c.Run | None = None,
                       bounds: LoopBounds = LoopBounds()) -> LoopResult | None:
    """
    Run `request` through the simple loop only if the classifier calls it
    SIMPLE; otherwise return None, having proposed and dispatched nothing, so
    the caller keeps the request on its own (Objective OS) path.
    """
    if TC.classify((request or "").strip()).task_class != TC.SIMPLE:
        return None
    return await run_simple(request, dispatch, propose, run=run, bounds=bounds)


def model_proposer(complete: Callable[[LoopState], Any]) -> Callable[[LoopState], Any]:
    """
    Build a `propose` from a model transport the caller supplies.

    `complete(state)` returns `(text, tokens, cost_usd)` (or an awaitable of
    it). The text is a JSON action; an object with a `complex_because` key
    says the task is too big for this loop. Text that is not JSON becomes a
    proposal with no action, which `run_simple` rejects like any malformed
    one. The tokens and cost are passed on so the loop's bounds meter them.
    """
    async def propose(state: LoopState) -> Proposal:
        text, tokens, cost_usd = await _settled(complete(state))
        try:
            raw = json.loads(text)
        except (TypeError, ValueError):
            return Proposal(action=None, tokens=tokens, cost_usd=cost_usd)
        if isinstance(raw, dict) and "complex_because" in raw:
            return Proposal(tokens=tokens, cost_usd=cost_usd,
                            complex_because=str(raw["complex_because"]))
        return Proposal(action=raw, tokens=tokens, cost_usd=cost_usd)

    return propose
