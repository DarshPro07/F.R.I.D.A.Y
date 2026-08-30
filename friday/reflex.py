"""
A local reflex for simple commands, so "pause the music" costs no cloud tokens.

Most of what the boss says to a machine is not thinking. "Open Paint." "Pause
it." "Show my windows." Sending those to Gemini spends thousands of input
tokens deliberating over a request that a 14MB model on this machine can read
in a quarter of a second - and Friday's own measurements are that the routine
commands are most of the traffic.

So there is a fast path. It is **off by default**, it may never be the brain,
and it is not a planner: Needle 2 has a 256-token window and no free-text
answer, which is exactly right for "which tool" and exactly wrong for "should
I use Godot or Unreal".

    reflex        one command, one tool, now          Needle, locally
    reasoning     anything that needs thinking        Gemini and up
    objective     durable multi-step work             the semantic planner

## Why the model's own confidence is not the gate

Needle carries a calibrated confidence head, and Cactus's recommended shape is
"act above a threshold, escalate below it". Two measurements say that is not
sufficient here.

First, in this codebase's own smoke test, the base model with four tools
declared answered:

    "restart the song"  ->  power_restart   confidence 0.197
    "pause the music"   ->  no call at all  confidence 0.016

The first is the exact false action that must never happen - a reboot for a
media command - and it scored *higher* than the trivially correct one it
missed. A numeric threshold that admits the first would have rebooted the
machine.

Second, `needle/__init__.py` sets `response["confidence"] = None` whenever
tuned weights are loaded, because LoRA does not update the confidence head.
So the moment anybody fine-tunes this on Friday's own speech - the obvious
next step - the number stops existing.

Therefore confidence is a *floor*, not the decision. What decides is Friday's
own semantics: the proposal has to survive the same operation-and-target
reading that the planner uses, and a `None` confidence escalates rather than
being treated as certainty.

## What may be a reflex at all

Deliberately narrow, and the narrowness is the safety argument:

    one command     a sentence carrying three goals answered with one of them
                    is not a fast path, it is two thirds of the request
                    dropped in silence. The planner is the oracle
    registered      it must be a real capability, not a name the model made up
    AUTO            a reflex is weaker than autonomy. It skipped the reasoning
                    model entirely, so it may not satisfy an ASK and must
                    never satisfy a CONFIRM
    LOW risk        `files_recycle` is MEDIUM and `power_shutdown` is
                    IRREVERSIBLE. Neither is a reflex, however confident
                    anything is
    semantic match  the operation the sentence asks for, and the kind of thing
                    it is about, must both agree with what the capability does
    arguments       every required parameter present, or there is nothing to
                    call

Anything that fails any of them escalates. Escalation is the ordinary
outcome and costs exactly what Friday costs today.
"""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass, field

from friday import capabilities as C
from friday import semantics as S


logger = logging.getLogger("friday-agent.reflex")

#: Pinned. Needle 2 is a different model from the ~26M Needle 1 that older
#: material still describes, and the `.cact` weight format is tied to the
#: engine version - so the package this was measured against is named here
#: rather than resolved to "whatever is installed".
NEEDLE_PACKAGE = "cactus-needle==2.0.9"

#: Off. Turned on with FRIDAY_REFLEX=1, and it stays off until the benchmark
#: in `scripts/benchmark_reflex.py` says the false-action rate is zero.
ENV_FLAG = "FRIDAY_REFLEX"

#: The model's own score has to clear this before Friday's gate even looks at
#: the proposal. It is a floor and not the decision - see the module note.
#: Measured against the base model, whose scores on correct answers sit far
#: below what a naive reading of "confidence" would suggest.
CONFIDENCE_FLOOR = float(os.getenv("FRIDAY_REFLEX_FLOOR", "0.15"))

#: The only risk level a reflex may carry.
ALLOWED_RISK = "LOW"

# Why a request was not handled locally. Every one of these is an ordinary
# outcome, not an error: the request goes to Gemini exactly as it does today.
NOT_ENABLED = "NOT_ENABLED"
NO_MODEL = "NO_MODEL"
NO_CALL = "NO_CALL"                  # Needle said this is not a tool call
UNKNOWN_CAPABILITY = "UNKNOWN_CAPABILITY"
NEEDS_APPROVAL = "NEEDS_APPROVAL"
TOO_RISKY = "TOO_RISKY"
WRONG_OPERATION = "WRONG_OPERATION"
WRONG_TARGET = "WRONG_TARGET"
MISSING_ARGUMENTS = "MISSING_ARGUMENTS"
UNGROUNDED = 'UNGROUNDED'
LOW_CONFIDENCE = "LOW_CONFIDENCE"
NO_CONFIDENCE = "NO_CONFIDENCE"      # tuned weights: the head is not updated
# Restored from the .pyc oracle: proven by a LOAD_CONST/STORE_NAME
# pair in the running system's bytecode, present in no source candidate.
COMPOUND = 'COMPOUND'

ESCALATIONS = (NOT_ENABLED, NO_MODEL, NO_CALL, UNKNOWN_CAPABILITY, NEEDS_APPROVAL,
               TOO_RISKY, WRONG_OPERATION, WRONG_TARGET, MISSING_ARGUMENTS,
               LOW_CONFIDENCE, NO_CONFIDENCE, COMPOUND, UNGROUNDED)

COMPOUND_GOALS = 2

# Restored from the .pyc oracle. Each pattern is a LOAD_CONST string
# and each flag a LOAD_ATTR on `re` in the compiled module - primary
# evidence from the running system, not inference.
_ENDING_VERB = re.compile('\\b(?:close|stop|end|kill|delete|remove|terminate|cancel|quit|shut|restart|reboot|exit|wipe|erase|trash|bin|discard|get rid of)\\b', re.IGNORECASE)


def is_dangerous(capability_id: str) -> bool:
    """
    Whether acting on this by mistake costs something asking again cannot fix.

    Every capability a reflex may reach is LOW risk, so `risk` cannot make
    this distinction - and it should not, because LOW is correct for all of
    them. `browser_close` closes Playwright's session and not the boss's
    Chrome; that is genuinely low risk and still the wrong thing to do to
    somebody who said "it is frozen, end it".

    Derived rather than listed: an ending verb in the capability's own name or
    description, plus a side effect outside this process. A hand-kept list
    would be wrong the first time somebody added a capability and forgot,
    which is the failure mode this codebase keeps meeting. `test_reflex.py`
    pins what it currently catches, so a registry change shows up as a test
    rather than as a quiet loss of coverage.
    """
    capability = C.by_id(capability_id)
    if capability is None:
        return False
    if capability.side_effect not in ("write", "external_action"):
        return False
    subject = f"{capability.id} {capability.description}".lower()
    return bool(_ENDING_VERB.search(subject))


_PRONOUN = re.compile('\\b(?:it|this|that|them|those|these)\\b', re.IGNORECASE)


@dataclass
class Reflex:
    """What the local router decided, and why."""

    capability: str = ""
    arguments: dict = field(default_factory=dict)
    confidence: float | None = None
    #: Empty when this may be acted on locally; otherwise why it escalated.
    escalated: str = ""
    #: What the model proposed before the gate, kept so a refusal can be read.
    proposed: str = ""
    milliseconds: float = 0.0

    @property
    def acts(self) -> bool:
        return bool(self.capability) and not self.escalated


def enabled() -> bool:
    """
    Whether the fast path may *act*. Not whether it may observe.

    Deferred to `shadow.mode()` so there is one canonical setting rather than
    a boolean that has to mean three things. OFF, SHADOW and DIRECT are
    genuinely different states and only the last one executes anything -
    collapsing SHADOW into "on" is how a telemetry deployment becomes a
    production one by accident.
    """
    from friday import shadow as SH

    return SH.may_act()


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


def admit(capability_id: str, arguments: dict, text: str,
          confidence: float | None) -> str:
    """
    Empty string if this may run locally, otherwise the reason it escalates.

    Pure and free of the model, so the whole safety argument is testable
    without downloading anything - which matters, because this is the half
    that has to be right.
    """
    if not is_one_command(text):
        return COMPOUND

    if not is_grounded(text):
        return UNGROUNDED

    capability = C.by_id(capability_id)
    if capability is None:
        return UNKNOWN_CAPABILITY

    # A reflex skipped the reasoning model, so it carries less authority than
    # autonomy does - not more. Anything a person would have been asked about
    # goes the long way round.
    if capability.requires_approval:
        return NEEDS_APPROVAL
    if capability.risk != ALLOWED_RISK:
        return TOO_RISKY

    # Friday's own reading of the sentence, which is the thing the model's
    # number cannot replace.
    wanted = S.for_request(text)
    if wanted is not None and not S.compatible(wanted, capability_id):
        return WRONG_OPERATION

    # The discriminator for the measured false action. `for_request` cannot
    # read "restart the song" - restart is one of the verbs whose operation
    # lives in its object - so the operation filter passes `power_restart`
    # happily. The *target* does not: the sentence is about MEDIA and
    # `power_restart` acts on POWER, which scores zero affinity.
    #
    # Used as a filter here rather than the weight the planner uses. Ranking
    # can afford to let a wrong candidate score low; a gate that admits it
    # reboots the machine.
    about = S.target_for_request(text)
    if about is not None and S.target_affinity(about, capability_id) <= 0:
        return WRONG_TARGET

    missing = _missing_arguments(capability_id, arguments)
    if missing:
        return MISSING_ARGUMENTS

    if confidence is None:
        return NO_CONFIDENCE
    if confidence < CONFIDENCE_FLOOR:
        return LOW_CONFIDENCE
    return ""


def is_one_command(text: str) -> bool:
    """
    Whether this sentence asks for a single thing.

    Deterministic and free: the planner reads goals out of sentence structure
    without a model, so this costs a regex pass rather than a cloud turn.
    Failing open - treating an unreadable sentence as one command - would put
    the decision back on the model that cannot see past 256 tokens, so an
    error escalates instead.
    """
    from friday import planner as P

    try:
        return len(P.interpret(text or "").goals) < COMPOUND_GOALS
    except Exception:                                        # noqa: BLE001
        logger.exception("reflex.compound_check_failed; escalating")
        return False


def is_grounded(text: str) -> bool:
    """
    Whether a sentence that ends something says what it is ending.

    Only ending verbs are held to this. A mistaken "show me that" is a wasted
    read and the boss asks again; a mistaken "close that" is somebody's
    unsaved work, and no amount of asking again brings it back.

    Grounded means the sentence names its object - either without a pronoun at
    all ("close the browser") or with a target Friday can read out of the
    vocabulary ("shut down the computer" -> SYSTEM). A bare pronoun and no
    readable target is half a command, and half a command is not a reflex.
    """
    if not _ENDING_VERB.search(text or ""):
        return True
    if not _PRONOUN.search(text or ""):
        return True
    return S.target_for_request(text) is not None


def _missing_arguments(capability_id: str, arguments: dict) -> list[str]:
    from friday import capability_runtime as R

    required = R.required_arguments(capability_id)
    if required is None:
        return ["<unreachable>"]
    return [name for name in required
            if name not in arguments or arguments[name] in (None, "")]


class Deterministic:
    """
    Friday's own semantic router, offered as a reflex backend. No model.

    Not a reimplementation - this is `planner.interpret` and `planner.resolve`,
    the same pass that reads "pause the music" as CONTROL on MEDIA and picks a
    capability for it. It costs a regex sweep rather than a cloud turn or a
    45MB engine, and it is the arm the local model has to beat rather than the
    arm it replaces.

    Shaped as a Needle response so all three arms go through one gate and one
    benchmark. `confidence` is 1.0 because a deterministic router has no
    calibrated score to report: its accuracy is the measurement, not a number
    it emits, and the gate's other rules apply to it unchanged.
    """

    def complete(self, text, max_new_tokens=256):
        from friday import planner as P

        try:
            plan = P.resolve(P.interpret(text or ""))
        except Exception:                                    # noqa: BLE001
            logger.exception("reflex.deterministic_failed; escalating")
            return {"type": "respond", "function_calls": [], "confidence": 0.0}
        goals = [goal for goal in plan.goals if goal.capability]
        if len(goals) != 1:
            return {"type": "respond", "function_calls": [],
                    "confidence": 0.0}
        goal = goals[0]
        return {
            "type": "call",
            "function_calls": [{"name": goal.capability,
                                "arguments": P.arguments_for(goal)}],
            "confidence": 1.0,
        }


class DomainGated:
    """
    Needle, shown only the tools for the domain the sentence is about.

    The measured problem with the flat catalogue was both halves at once:
    ninety-five tools cost a median of 2.2 seconds, and choosing five of them
    produced a 5.3% false-action rate in a catalogue full of near-neighbours.
    Narrowing first addresses both - the exact-target domains have a median of
    five capabilities, which is where Needle bypasses its retrieval stage
    entirely.

    A sentence whose domain cannot be read escalates rather than being handed
    to the model with everything declared. That is the case the flat catalogue
    handled worst: "it is frozen, end it" names no domain, and a 45M model
    choosing between `browser_close`, `process_terminate`, `apps_close` and
    `music_stop` is guessing.
    """

    def __init__(self) -> None:
        self._by_domain = {}

    def complete(self, text, max_new_tokens=256):
        target = domain_of(text)
        if not target:
            return {"type": "respond", "function_calls": [],
                    "confidence": 0.0}
        agent = self._for(target)
        if agent is None:
            return {"type": "respond", "function_calls": [],
                    "confidence": 0.0}
        return agent.complete(text, max_new_tokens)

    def _for(self, target: str):
        if target in self._by_domain:
            return self._by_domain[target]
        schemas = tool_schemas(target)
        agent = None
        if schemas:
            try:
                from needle import Needle

                agent = Needle(tools=schemas)
            except Exception:                                # noqa: BLE001
                logger.exception("reflex.domain_agent_failed target=%s", target)
        self._by_domain[target] = agent
        return agent


# ---------------------------------------------------------------------------
# The model
# ---------------------------------------------------------------------------

_AGENT = None
_UNAVAILABLE = False


def tool_schemas(target: str = "") -> list[dict]:
    """
    The capabilities a reflex may reach, as Needle tool schemas.

    Only the ones that could pass the gate anyway. Declaring the other
    seventy-odd would let the retrieval head spend its five slots on tools
    this router is never allowed to call, which is a way of making the model
    look worse than it is.

    `target` narrows to one domain and its family. Measured, and the reason
    this parameter exists: the whole 95-tool catalogue costs a median of
    2.2 seconds per call, against 265-820ms for four tools. Needle bypasses
    retrieval entirely at five tools or fewer and runs a selection pass above
    that, so the size of what is declared is the latency.
    """
    from friday import capability_runtime as R

    reachable = set(R.reachable())
    schemas = []
    for capability in sorted(C._ALL, key=lambda item: item.id):
        if capability.id not in reachable:
            continue
        if capability.risk != ALLOWED_RISK or capability.requires_approval:
            continue
        if target and S.target_affinity(target, capability.id) < 10:
            # Outside the family the sentence is about: declaring it would
            # only give the retrieval head something to get wrong.
            continue
        required = R.required_arguments(capability.id) or ()
        schemas.append({
            "name": capability.id,
            "description": capability.description,
            "parameters": {
                "type": "object",
                "properties": {name: {"type": "string"} for name in required},
                "required": list(required),
            },
        })
    return schemas


def domain_of(text: str) -> str:
    """
    The kind of thing this sentence is about, read without a model.

    The narrowing half of the three-way router: `semantics.target_for_request`
    already reads MEDIA out of "pause the music" and WINDOW out of "move this
    to the right", so the local model can be handed six tools instead of
    ninety-five. An unreadable sentence returns "", which means the caller
    should escalate rather than let a 45M model choose between
    `browser_close`, `process_terminate`, `apps_close` and `music_stop`.
    """
    return S.target_for_request(text or "") or ""


def _agent():
    """The local model, or None. Built once; a failure is remembered."""
    global _AGENT, _UNAVAILABLE

    if _AGENT is not None or _UNAVAILABLE:
        return _AGENT
    try:
        from needle import Needle
    except ImportError:
        logger.info("reflex.unavailable reason=not_installed pin=%s",
                    NEEDLE_PACKAGE)
        _UNAVAILABLE = True
        return None
    try:
        # First construction fetches the engine from Hugging Face and caches
        # it; measured at 29s once, then nothing. Never at request time in a
        # voice turn - `warm()` is what the agent calls at startup.
        _AGENT = Needle(tools=tool_schemas())
    except Exception:                                       # noqa: BLE001
        logger.exception("reflex.unavailable reason=init_failed")
        _UNAVAILABLE = True
        return None
    return _AGENT


def warm() -> bool:
    """Build the model now rather than inside somebody's sentence."""
    return enabled() and _agent() is not None


def reset() -> None:
    """Forget the model, so a test can build a different one."""
    global _AGENT, _UNAVAILABLE

    _AGENT = None
    _UNAVAILABLE = False


# ---------------------------------------------------------------------------
# The one entry point
# ---------------------------------------------------------------------------


def route(text: str, *, agent=None) -> Reflex:
    """
    Handle this locally, or say why not.

    Never raises: the fast path failing is not the boss's problem, and every
    way it can fail ends in escalation, which is what would have happened
    anyway.
    """
    if agent is None:
        if not enabled():
            return Reflex(escalated=NOT_ENABLED)
        agent = _agent()
        if agent is None:
            return Reflex(escalated=NO_MODEL)

    started = time.monotonic()
    try:
        # `complete`, never `run`. `run` executes the tools itself, and
        # nothing outside `CapabilityRuntime` may execute anything - that is
        # where policy, provenance and the evidence trail live.
        response = agent.complete(text)
    except Exception:                                       # noqa: BLE001
        logger.exception("reflex.failed; escalating")
        return Reflex(escalated=NO_MODEL,
                      milliseconds=(time.monotonic() - started) * 1000)

    elapsed = (time.monotonic() - started) * 1000
    calls = response.get("function_calls") or []
    confidence = response.get("confidence")
    if response.get("type") != "call" or not calls:
        # Needle's designed way of saying "not a local reflex". An empty call
        # list is the escalation signal, not a failure.
        return Reflex(confidence=confidence, escalated=NO_CALL,
                      milliseconds=elapsed)

    proposed = str(calls[0].get("name") or "")
    arguments = dict(calls[0].get("arguments") or {})
    refused = admit(proposed, arguments, text, confidence)
    if refused:
        logger.info("reflex.escalated reason=%s proposed=%s confidence=%s "
                    "ms=%.0f", refused, proposed, confidence, elapsed)
        return Reflex(proposed=proposed, arguments=arguments,
                      confidence=confidence, escalated=refused,
                      milliseconds=elapsed)

    logger.info("reflex.local capability=%s confidence=%.3f ms=%.0f",
                proposed, confidence, elapsed)
    return Reflex(capability=proposed, arguments=arguments,
                  confidence=confidence, proposed=proposed,
                  milliseconds=elapsed)
