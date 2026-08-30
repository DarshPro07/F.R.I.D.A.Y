
"""
Asking a model what a request means, without letting it choose the tools.

The deterministic interpreter in `friday/planner.py` reads instructions well -
verbs it knows, objects it knows, "then" meaning order. It does not read
people. "I want to make a game where you play as a lighthouse keeper" contains
no verb it recognises and no target it can name, and it will say so honestly
rather than guess. That honesty is correct and it is not enough: the boss
wants to describe an idea and have Friday understand it.

So a model is asked, under three constraints that are the whole design:

    it never sees the capabilities

        It receives the operation and target vocabulary and one line per
        domain - about 900 characters. Not 125 tool schemas. That is the
        token and accuracy problem CORE-02B removed from the conversational
        path, and putting it back inside the planner would be the same
        mistake in a different file. LiveKit's own guidance is the same:
        beyond roughly ten tools, selection accuracy falls.

    it never names a capability

        It returns goals - an operation, a target, an entity - and the
        registry resolver picks the capability afterwards, by machine
        semantics. A model that could name tools would invent them, and
        `magic_super_tool` is not a thing that can fail gracefully.

    it is not trusted

        The output is schema-constrained, which makes it well-formed, and
        well-formed is not the same as sensible. Every field is checked
        against the real vocabulary and the plan still goes through
        `validate` before anything is persisted.

And it is only asked when the deterministic pass could not do the job, because
most of what the boss says is an instruction and instructions do not need a
model to understand.
"""

from __future__ import annotations

import json

import logging

import os

import re

from friday import planner as P

from friday import semantics as S

logger = logging.getLogger("friday-agent.planner")

#: What the model is asked to produce. Names only, so a change to the
#: vocabulary reaches the prompt without anybody remembering to edit it.
GOAL_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "goals": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "intent": {"type": "STRING"},
                    "operation": {"type": "STRING", "enum": list(S.OPERATIONS)},
                    "target": {"type": "STRING", "enum": list(S.TARGETS)},
                    "entity": {"type": "STRING"},
                    "follows_previous": {"type": "BOOLEAN"},
                },
                "required": ["intent", "operation", "target"],
            },
        },
        "constraints": {"type": "ARRAY", "items": {"type": "STRING"}},
        "reporting": {"type": "ARRAY", "items": {"type": "STRING"}},
        "safety": {"type": "ARRAY", "items": {"type": "STRING"}},
        "questions": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "required": ["goals"],
}

#: One line per domain, built from the registry rather than written down, so
#: it cannot drift from what Friday can actually do.
def domain_summary() -> str:
    """What Friday can act on, in a few hundred characters."""
    from collections import defaultdict

    from friday import capabilities as C

    by_target: dict[str, set[str]] = defaultdict(set)
    for capability in C._ALL:
        operation, target = S.for_capability(capability.id)
        by_target[target].add(operation)
    return "\n".join(
        f"  {target}: {', '.join(sorted(operations))}"
        for target, operations in sorted(by_target.items()))

INSTRUCTIONS = """\
You read a request and say what the person wants, as goals.

A goal is one outcome they want to be true. Give each an operation and a
target from the lists below, the words they used as `intent`, and the thing
being acted on as `entity` when there is one (an application name, a file, a
subject to research).

Set `follows_previous` only when a goal genuinely has to happen after the one
before it - "then", or because it acts on something the previous goal creates.
Independent goals must stay independent.

Do NOT name tools, functions or capabilities. You are saying what is wanted,
not how to do it; something else chooses that afterwards.

Everything that is not an outcome goes in its own list rather than becoming a
goal: how the work should be done (`constraints`), what to say afterwards
(`reporting`), what must not happen (`safety`). Being addressed by name is
none of those and belongs nowhere - drop it.

If something material is genuinely unclear, put it in `questions` rather than
guessing at a goal.

operations: {operations}
targets:    {targets}

what Friday can act on:
{domains}
"""


def _model() -> tuple[object, str] | None:
    """The client and model id, or None when no provider is configured."""
    key = os.getenv("GOOGLE_API_KEY")
    if not key:
        # The agent calls load_dotenv at startup, but the planner is also
        # reached from scripts, tests and the MCP server, and a planner that
        # silently reports "no provider" because nobody loaded a file is
        # indistinguishable from one whose provider is down.
        try:
            from dotenv import load_dotenv

            load_dotenv()
            key = os.getenv("GOOGLE_API_KEY")
        except ImportError:                                 # pragma: no cover
            pass
    if not key:
        return None
    try:
        from google import genai
    except ImportError:                                     # pragma: no cover
        return None
    from friday import providers

    try:
        name = providers.resolve_llm_model("google", os.getenv(
            "ADA_PLANNER_ROLE", "DEEP"))
    except Exception:                                       # noqa: BLE001
        name = "gemini-2.5-flash"
    return genai.Client(api_key=key), name

#: Shorter than this is a command, not a description.
#:
#: A model asked about six words of nonsense answers confidently, because
#: answering is what it does - measured: "flurb the wibble, then open Paint"
#: came back with a real goal for the wibble. A described idea is long; a
#: command that cannot be read is just a command that cannot be read, and
#: should reach the graph saying so.
MODEL_ASSIST_WORDS = int(os.getenv("ADA_MODEL_ASSIST_WORDS", "12"))

#: A request the deterministic pass clearly handled: every segment placed, and
#: at least one goal. Anything else is worth a second opinion.
def needs_model(text: str, plan: P.Plan) -> bool:
    """
    Whether the deterministic reading left anything on the table.

    Deliberately conservative. Most of what the boss says is an instruction
    with a verb in it, and paying for a model call to be told what
    `files_create` already knew is the waste this whole architecture is
    trying to avoid.
    """
    words = len(text.split())

    # Below this it is a command, however unreadable. "flurb the wibble, then
    # open Paint" is six words of nonsense, and asking a model produced a
    # confident goal for the nonsense - which is exactly the guessing this is
    # meant to prevent, arriving one level up. Short and unplaceable stays
    # unplaceable, and reaches the graph saying so.
    if words < MODEL_ASSIST_WORDS:
        return False

    if plan.unresolved:
        return True
    if not plan.goals:
        return True
    # Long, and almost nothing came out of it: the shape of a described idea
    # rather than a list of commands.
    return words > 25 and len(plan.goals) < 2

#: The last few model readings, by request text.
#:
#: Admission plans a request to decide whether it is compound, and
#: `objective_start` plans it again to persist it. That is one model call per
#: admission wasted, and the two must agree anyway - a request admitted on one
#: reading and compiled from a different one is a bug waiting for a slow day.
#: Small and unbounded-in-time on purpose: it is a within-turn memo, not a
#: cache with a policy.
_RECENT: dict[str, dict] = {}

_RECENT_LIMIT = 8


def interpret(text: str, *, context: str = "") -> P.Plan | None:
    """
    A model's reading of the request, as goals. None if it could not be asked.

    Returns a Plan in exactly the same shape the deterministic interpreter
    produces, so everything downstream - resolution, validation, task specs -
    is unchanged and does not know or care which one read the sentence.
    """
    memo = f"{text}\x00{context}"
    if memo in _RECENT:
        logger.info("planner.model reused=1")
        return _as_plan(_RECENT[memo])

    configured = _model()
    if configured is None:
        logger.info("planner.model skipped=no_provider")
        return None
    client, name = configured

    from google.genai import types

    prompt = INSTRUCTIONS.format(
        operations=", ".join(S.OPERATIONS),
        targets=", ".join(S.TARGETS),
        domains=domain_summary(),
    )
    if context:
        prompt += f"\nwhat is already going on:\n{context}\n"

    try:
        answer = client.models.generate_content(
            model=name,
            contents=f"{prompt}\n\nthe request:\n{text}",
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=GOAL_SCHEMA,
                system_instruction="Return only the JSON the schema describes.",
            ),
        )
        raw = json.loads(answer.text or "{}")
    except Exception as exc:                                # noqa: BLE001
        # A planning model that is down is not a reason to plan badly. The
        # caller keeps the deterministic reading; it does not fall back to
        # the clause splitter, which is the thing this replaced.
        logger.info("planner.model failed=%s", type(exc).__name__)
        return None

    if len(_RECENT) >= _RECENT_LIMIT:
        _RECENT.pop(next(iter(_RECENT)))
    _RECENT[memo] = raw

    plan = _as_plan(raw)
    logger.info("planner.model model=%s goals=%d constraints=%d questions=%d",
                name, len(plan.goals), len(plan.constraints),
                len(raw.get("questions") or []))
    return plan


def _as_plan(raw: dict) -> P.Plan:
    """
    Model output as a Plan, with every field checked against the real thing.

    Schema-constrained output is well-formed, and well-formed is not correct:
    an enum can hold a value that exists and is wrong for this goal, and a
    string field can hold a capability name the model was told not to give.
    """
    plan = P.Plan()
    previous: P.Goal | None = None

    for index, item in enumerate(raw.get("goals") or [], start=1):
        intent = str(item.get("intent") or "").strip()
        if not intent:
            continue
        operation = str(item.get("operation") or "").upper()
        target = str(item.get("target") or "").upper()
        if operation not in S.OPERATIONS or target not in S.TARGETS:
            # Outside the vocabulary it was given. Recorded, not repaired -
            # guessing at what it meant is how a wrong tool gets chosen.
            plan.unresolved.append(intent)
            continue

        goal = P.Goal(
            goal_id=f"g{index}",
            intent=intent,
            operation=operation,
            target=target,
            entity=_entity(item.get("entity")),
            depends_on=((previous.goal_id,)
                        if previous is not None and item.get("follows_previous")
                        else ()),
        )
        plan.goals.append(goal)
        previous = goal

    for field, into in (("constraints", plan.constraints),
                        ("reporting", plan.reporting),
                        ("safety", plan.safety)):
        into.extend(str(item).strip() for item in (raw.get(field) or [])
                    if str(item).strip())
    return plan

#: A capability id, which the model was told not to produce and may anyway.
_LOOKS_LIKE_A_TOOL = re.compile(r"^[a-z][a-z0-9]*_[a-z0-9_]+$")


def _entity(value) -> str:
    text = str(value or "").strip()
    if not text or _LOOKS_LIKE_A_TOOL.match(text):
        # It answered with a tool name in the entity field. The registry
        # chooses capabilities; a model that could name one would eventually
        # name one that does not exist.
        return ""
    return text


def what_was_decided(*, db=None) -> str:
    """
    Decisions already made about the project in hand, as planning context.

    Without this the loop stops short of being useful: Friday asks good
    questions, records the answers durably, and then plans as though the
    conversation never happened. The boss said Godot on Tuesday and would be
    planned for as if the engine were still an open question.

    Only the project currently being asked about, and only the most recent
    handful - the whole point of the durable store is that relevant things are
    retrieved, not that everything is sent.
    """
    from friday import requirements as REQ

    project = REQ.current_project(db=db)
    if not project:
        return ""
    decided = REQ.recorded(project, db=db)
    if not decided:
        return ""
    lines = [f"already decided about {project}:"]
    lines += [f"  - {row['decision']}" for row in decided[:8]]
    return "\n".join(lines)


def plan_objective(text: str, *, context: str = "") -> P.Plan:
    """
    The hybrid planner: deterministic first, a model only when needed.

    The deterministic reading is kept whenever it placed everything, because
    it is free, instant, and works with the provider down. The model is asked
    about the rest, and whatever comes back is resolved and validated by the
    same code either way.
    """
    from friday import audit_planner as A

    if A.is_an_audit_request(text):
        # "Audit everything you can do" is not a list of errands, and reading
        # it as one is how 1967 words became 205 tasks. What can be audited
        # is in the registry, so it is read from there.
        audit = A.plan_audit()
        plan = P.Plan(goals=A.as_goals(audit))
        plan.constraints.extend(P.interpret(text).constraints)
        logger.info("planner.audit registered=%d groups=%d testable=%d",
                    audit.registered, len(audit.groups),
                    audit.counts().get(A.SAFE_REAL_TEST, 0)
                    + audit.counts().get(A.READ_ONLY, 0))
        return plan

    deterministic = P.interpret(text)
    if not needs_model(text, deterministic):
        logger.info("planner.deterministic goals=%d", len(deterministic.goals))
        return P.resolve(deterministic)

    decided = what_was_decided()
    assisted = interpret(text, context="\n\n".join(
        part for part in (context, decided) if part))
    if assisted is None or not assisted.goals:
        logger.info("planner.deterministic goals=%d reason=model_unavailable",
                    len(deterministic.goals))
        return P.resolve(deterministic)

    # Anything the deterministic pass understood and the model did not is
    # kept: it read the constraints out of the sentence structure, and losing
    # "do not ask me to continue" because a model did not repeat it would be
    # a regression dressed as an upgrade.
    for item in deterministic.constraints:
        if item not in assisted.constraints:
            assisted.constraints.append(item)
    for item in deterministic.reporting:
        if item not in assisted.reporting:
            assisted.reporting.append(item)
    assisted.discarded.extend(deterministic.discarded)

    return P.resolve(assisted)
