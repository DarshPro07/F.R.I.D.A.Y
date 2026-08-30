
"""
Turning an idea into something that can be built, by asking about it.

Friday can now read what a request means and choose capabilities for it. That
covers instructions. It does not cover the thing the boss actually wants to
stop using ChatGPT for:

    "I want to make a game where you play a lighthouse keeper."

There is no plan in that sentence, and there should not be. What is missing is
not capabilities, it is *decisions* - and the only place they exist is in the
boss's head. So Friday asks.

Three rules shape the asking, and each one is a way of getting it wrong:

    only what is material

        A question is material when the answer changes what gets built. "Which
        engine" changes everything downstream. "Tabs or spaces" changes
        nothing anybody will remember. Small reversible choices are decided
        here and recorded as assumptions, so the boss can overrule them
        without having been interrogated about them - that is §16, and it is
        the difference between a colleague and a form.

    never twice

        A question whose answer is already a recorded decision is not asked
        again. This is the whole point of the durable store: the boss said it
        once, in some conversation that has since been closed, and Friday is
        not allowed to have forgotten.

    say when it looks wrong

        "React Native will definitely be best for this" is a claim, and
        agreeing with it because the boss said it is the failure mode this
        assistant exists to avoid. Concerns are raised alongside the
        questions, with a reason - not as a refusal, and not as a lecture.

The model is used to read the domain, under the same constraints as the
planner: schema-constrained output, no capability names, and nothing it
returns is trusted until it has been checked here.
"""

from __future__ import annotations

import json

import logging

import re

from dataclasses import dataclass, field

logger = logging.getLogger("friday-agent.requirements")

#: The answer changes what gets built.
MATERIAL = "MATERIAL"

#: Small and reversible. Friday decides, records it, and moves on.
SMALL = "SMALL"

MATERIALITY = (MATERIAL, SMALL)


@dataclass(frozen=True)
class Question:
    """Something Friday needs to know, and what turns on it."""

    question: str
    why: str
    options: tuple[str, ...] = ()
    materiality: str = MATERIAL
    #: What Friday would choose if it had to. Present for SMALL questions,
    #: which are decided rather than asked.
    proposed: str = ""

    @property
    def key(self) -> str:
        """A stable identity for "have I asked this already"."""
        return _key(self.question)


@dataclass(frozen=True)
class Concern:
    """Something the boss said that looks weaker than they think."""

    claim: str
    concern: str


@dataclass
class Understanding:
    """What Friday knows about an idea, and what it still needs."""

    subject: str = ""
    known: list[str] = field(default_factory=list)
    questions: list[Question] = field(default_factory=list)
    concerns: list[Concern] = field(default_factory=list)
    #: SMALL questions Friday answered itself, kept so they can be overruled.
    assumptions: list[Question] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        """Whether anything still has to be asked before work can start."""
        return not self.questions

    def spoken(self, limit: int = 3) -> str:
        """
        The questions as one thing a person would actually say.

        Bundled rather than asked one at a time, because being asked eleven
        questions in a row is an interrogation and nobody finishes one.
        """
        if not self.questions:
            return ""
        asking = self.questions[:limit]
        lines = [f"{index}. {question.question}"
                 for index, question in enumerate(asking, start=1)]
        remaining = len(self.questions) - len(asking)
        if remaining:
            lines.append(f"(and {remaining} more once these are settled)")
        return "\n".join(lines)


def _key(text: str) -> str:
    """A question's identity: its words, without the punctuation or politeness."""
    words = re.findall(r"[a-z]+", (text or "").lower())
    return " ".join(word for word in words if word not in _NOISE)

_NOISE = frozenset({
    "what", "which", "who", "how", "do", "does", "you", "your", "the", "a",
    "an", "is", "are", "want", "would", "should", "like", "for", "to", "of",
    "and", "or", "in", "on", "it", "this", "that", "prefer", "any", "there",
    # Pronouns and framing: "which engine are we using" and "what engine
    # should I use" are one question, and the difference is entirely in words
    # that carry none of it.
    "we", "us", "our", "i", "my", "me", "us", "be", "will", "am", "using",
    "use", "used", "going", "go", "need", "needs", "have", "has", "s",
})


def recorded(project: str, *, db=None) -> list[dict]:
    """Decisions already made about this project, newest first."""
    from friday.toolsets import memory as M

    store = db or M.store()
    try:
        return [row for row in store.decisions(project)
                if not row.get("superseded")]
    except Exception:                                       # noqa: BLE001
        return []


def already_answered(question: Question, decisions: list[dict]) -> bool:
    """
    Whether this has been settled before.

    Matched on the substantive words rather than the exact sentence, because
    "which engine are you using?" and "what engine should we use" are the same
    question and asking the second one after the first was answered is how an
    assistant stops feeling like it was listening.
    """
    wanted = set(_key(question.question).split())
    if not wanted:
        return False
    for row in decisions:
        said = set(_key(f"{row.get('decision', '')} "
                        f"{row.get('rationale', '')}").split())
        if not said:
            continue
        overlap = wanted & said
        # A proportion rather than a count, because the count punishes exactly
        # the questions most likely to have been asked before: "which engine?"
        # carries one distinctive word, and requiring two of it means the
        # shortest, commonest questions are the ones that get asked twice.
        if len(overlap) / len(wanted) >= _ENOUGH_OVERLAP:
            return True
    return False

#: How much of a question's substance has to appear in a decision before it
#: counts as settled. High enough that "audience" does not match "engine".
_ENOUGH_OVERLAP = 0.4


def remember_answer(project: str, question: Question, answer: str, *,
                    source: str = "the boss answered", db=None) -> None:
    """Record an answer as a decision about the project, so it is not re-asked."""
    from friday.toolsets import memory as M

    store = db or M.store()
    store.record_decision(
        project=project,
        decision=f"{question.question} - {answer}".strip(" -"),
        rationale=question.why,
        source=source,
    )
    logger.info("requirements.answered project=%s key=%s", project,
                question.key[:40])

SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "subject": {"type": "STRING"},
        "known": {"type": "ARRAY", "items": {"type": "STRING"}},
        "questions": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "question": {"type": "STRING"},
                    "why": {"type": "STRING"},
                    "options": {"type": "ARRAY", "items": {"type": "STRING"}},
                    "materiality": {"type": "STRING",
                                    "enum": list(MATERIALITY)},
                    "proposed": {"type": "STRING"},
                },
                "required": ["question", "why", "materiality"],
            },
        },
        "concerns": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "claim": {"type": "STRING"},
                    "concern": {"type": "STRING"},
                },
                "required": ["claim", "concern"],
            },
        },
    },
    "required": ["subject", "questions"],
}

INSTRUCTIONS = """\
Someone has described something they want to build. Work out what you would
have to know before it could be built, and what they have already told you.

For each thing you do not know, give a question and say what turns on the
answer. Mark it:

  MATERIAL  the answer changes what gets built - the platform, the audience,
            the shape of the thing, what it has to do
  SMALL     reversible, and nobody will remember the decision. Give your
            `proposed` answer; it will be recorded and can be overruled.

Ask about what is missing, not about what they said. If they told you the
platform, do not ask about the platform.

Do not ask more MATERIAL questions than the idea genuinely needs. Three good
ones beat eleven thorough ones, because eleven do not get answered.

If something they said looks weaker than they seem to think, say so in
`concerns`: what they claimed, and what the concern is. Be specific and be
brief. Do not agree with a claim just because they made it, and do not
lecture.

Do not name tools, functions or software you would call. This is about what
they want, not how you would do it.
"""


def _model():
    from friday import planner_model as PM

    return PM._model()


def understand(text: str, *, project: str = "", db=None) -> Understanding:
    """
    Read an idea into what is known, what must be asked, and what looks wrong.

    Questions already settled for this project are dropped before they reach
    the boss, and SMALL ones are decided rather than asked.
    """
    configured = _model()
    if configured is None:
        logger.info("requirements.model skipped=no_provider")
        return Understanding(subject=text[:80])
    client, name = configured

    from google.genai import types

    known_already = recorded(project, db=db) if project else []
    context = ""
    if known_already:
        context = "\n\nalready decided about this project:\n" + "\n".join(
            f"  - {row['decision']}" for row in known_already[:12])

    try:
        answer = client.models.generate_content(
            model=name,
            contents=f"{INSTRUCTIONS}{context}\n\nwhat they said:\n{text}",
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=SCHEMA,
                system_instruction="Return only the JSON the schema describes.",
            ),
        )
        raw = json.loads(answer.text or "{}")
    except Exception as exc:                                # noqa: BLE001
        logger.info("requirements.model failed=%s", type(exc).__name__)
        return Understanding(subject=text[:80])

    found = _as_understanding(raw)
    before = len(found.questions)
    found.questions = [question for question in found.questions
                       if not already_answered(question, known_already)]

    logger.info(
        "requirements.read model=%s subject=%r material=%d asked=%d "
        "assumed=%d concerns=%d dropped_as_known=%d",
        name, found.subject[:40], before, len(found.questions),
        len(found.assumptions), len(found.concerns),
        before - len(found.questions))
    return found


def _as_understanding(raw: dict) -> Understanding:
    """
    Model output as an Understanding, with every field checked.

    Schema-constrained output is well-formed. Well-formed is not the same as
    useful: a question with no `why` is a question nobody can judge the
    importance of, and a MATERIAL question about tabs and spaces is the model
    misjudging what matters.
    """
    found = Understanding(subject=str(raw.get("subject") or "").strip())
    found.known = [str(item).strip() for item in (raw.get("known") or [])
                   if str(item).strip()]

    for item in raw.get("questions") or []:
        text = str(item.get("question") or "").strip()
        why = str(item.get("why") or "").strip()
        if not text or not why:
            # A question that cannot say what turns on it is not one worth
            # spending the boss's attention on.
            continue
        materiality = str(item.get("materiality") or MATERIAL).upper()
        if materiality not in MATERIALITY:
            materiality = MATERIAL
        question = Question(
            question=text, why=why,
            options=tuple(str(option).strip()
                          for option in (item.get("options") or [])
                          if str(option).strip()),
            materiality=materiality,
            proposed=str(item.get("proposed") or "").strip(),
        )
        if question.materiality == SMALL and question.proposed:
            # Decided, not asked. Recorded so it can be overruled by somebody
            # who was never made to sit through the question.
            found.assumptions.append(question)
        else:
            found.questions.append(question)

    for item in raw.get("concerns") or []:
        claim = str(item.get("claim") or "").strip()
        concern = str(item.get("concern") or "").strip()
        if claim and concern:
            found.concerns.append(Concern(claim=claim, concern=concern))
    return found

#: Someone describing something they want, rather than telling Friday to do
#: something. "I want to build" is not an instruction; it is the start of a
#: conversation.
_AN_IDEA = re.compile(
    r"\b(?:i(?:'d| would)? (?:want|like|need)|i(?:'m| am) thinking|"
    r"i have an idea|what if we|we should build|help me (?:build|design|plan)|"
    r"i want to (?:make|build|create|design)|thinking about (?:making|building)|"
    r"can we (?:build|make|design))\b",
    re.IGNORECASE)


def is_an_idea(text: str) -> bool:
    """
    Whether this needs asking about before it needs planning.

    Narrow on purpose. "Open Paint" is not an idea, and treating it as one
    would replace a working assistant with a questionnaire.
    """
    if not _AN_IDEA.search(text or ""):
        return False
    # Long enough to be a description. "I want to open Paint" is a request
    # wearing an idea's opening words.
    return len(text.split()) >= 8

_VAGUE = frozenset(
    {
        'a',
        'an',
        'app',
        'game',
        'idea',
        'new',
        'project',
        'something',
        'system',
        'the',
        'thing',
        'tool',
    },
)


def project_name(subject: str) -> str:
    """
    A short, stable name for the thing being discussed.

    Stable matters more than pretty: it is the key everything else hangs off,
    and a name that changes between two turns about the same idea makes the
    decisions unfindable.
    """
    words = [word for word in re.findall(r"[a-z0-9]+", (subject or "").lower())
             if word not in _VAGUE]
    return "-".join(words[:4]) or "unnamed"


def current_project(*, db=None) -> str:
    """
    The project with questions outstanding, if there is exactly one.

    Deliberately narrow. With two ideas in flight, guessing which one an
    answer belongs to would file it against the wrong project - and a decision
    recorded under the wrong name is worse than one not recorded at all,
    because it will be found later and believed.
    """
    from friday.toolsets import memory as M

    store = db or M.store()
    projects = {row["project"] for row in store.open_questions()}
    return projects.pop() if len(projects) == 1 else ""


def ask(project: str, questions: list, *, db=None) -> list:
    """Record what was asked, so an answer can find it later."""
    from friday.toolsets import memory as M

    store = db or M.store()
    asked = []
    for question in questions:
        # A material question blocks; a small one is decided as an
        # assumption and travels with the work, labelled, so he can
        # overrule it without having been stopped for it.
        blocking = question.materiality == MATERIAL
        asked.append(store.ask_question(
            project, question.question, why=question.why,
            options=", ".join(question.options),
            blocking=blocking, impact=question.why,
            assumption="" if blocking else question.proposed,
            assumption_reason=("" if blocking else
                               "small and reversible; say if you would rather it were otherwise")))
    if asked:
        logger.info("requirements.asked project=%s questions=%d",
                    project, len(asked))
    return asked


def outstanding(project: str = '', *, db=None) -> list:
    from friday.toolsets import memory as M

    return (db or M.store()).open_questions(project)


def about_this_project(text: str, project: str) -> bool:
    """
    Whether this sentence is about that project.

    The head word, because that is what a project is called in conversation.
    `project_name` takes the subject's own words in order and drops the vague
    ones, so `lighthouse-keeper-storm` leads with the noun everything else
    hangs off - and "the lighthouse game" is what the boss will actually say.

    Requiring the full name means a project is only ever recognised when named
    in full, which is not how anybody talks. Accepting *any* of its words
    means "will there be a storm tomorrow" reopens the lighthouse game, and
    answering a weather question with an assumption about a game engine is
    worse than staying quiet.
    """
    words = [word for word in re.findall(r"[a-z0-9]+", (project or "").lower())
             if word not in _VAGUE]
    if not words:
        return False
    return words[0] in set(re.findall(r"[a-z0-9]+", (text or "").lower()))


def still_blocking(text: str, *, db=None) -> tuple[str, list[dict]]:
    """
    (project, questions) that this request needs answered and does not have.

    Re-raised here rather than on a timer, because *now* is when the answer
    is needed. "Which engine?" is not urgent while the idea is being talked
    about; it is blocking the moment somebody says "build it". A question
    brought back on any other schedule is nagging, and nobody answers nagging.

    Everything in `open_questions` is material by construction - the small
    reversible ones were decided as assumptions and never asked - so anything
    still open here genuinely changes what gets built.
    """
    project = current_project(db=db)
    if not project or not about_this_project(text, project):
        return "", []
    return project, outstanding(project, db=db)

ANSWER_SCHEMA = {
    'type': 'OBJECT',
    'properties': {
        'answers': {
            'type': 'ARRAY',
            'items': {
                'type': 'OBJECT',
                'properties': {
                    'question_id': {'type': 'INTEGER'},
                    'answer': {'type': 'STRING'},
                    'confident': {'type': 'BOOLEAN'},
                },
                'required': ['question_id', 'answer', 'confident'],
            },
        },
    },
    'required': ['answers'],
}

# Restored from the .pyc oracle: proven by a LOAD_CONST/STORE_NAME
# pair in the running system's bytecode, present in no source candidate.
ANSWER_INSTRUCTIONS = 'You asked someone these questions. They have replied. Work out which of the\nquestions their reply actually answers.\n\nOnly include a question if the reply genuinely settles it. A reply that talks\naround a question, or says they are not sure, or answers a different question,\nhas not answered it - leave it out. Being asked again is annoying; having an\nanswer invented for you is worse, because nobody finds out.\n\n`answer` is what they decided, in their words, short.\n\n`confident` is false when you are recording something they only implied. It\nwill still be kept, but marked as inferred rather than as something they said.\n'


def capture_answers(reply: str, project: str = '', *, db=None) -> list:
    """
    Which open questions this reply answered, recorded as decisions.

    Returns `(question_id, answer, confident)` for each one closed.

    Nothing captured answers before this: the loop asked well and forgot the
    reply, so the boss would have been asked the same thing tomorrow. That is
    the specific way an assistant stops feeling like it was listening.
    """
    from friday.toolsets import memory as M

    store = db or M.store()
    project = project or current_project(db=store)
    if not project:
        return []
    questions = store.open_questions(project)
    if not questions or not (reply or "").strip():
        return []

    configured = _model()
    if configured is None:
        logger.info("requirements.capture skipped=no_provider")
        return []
    client, name = configured

    from google.genai import types

    asked = "\n".join(
        f"  [{row['id']}] {row['question']}"
        + (f"  (options: {row['options']})" if row.get("options") else "")
        for row in questions)

    try:
        answer = client.models.generate_content(
            model=name,
            contents=f"{ANSWER_INSTRUCTIONS}\n\nthe questions:\n{asked}\n\ntheir reply:\n"
                     f"{reply}",
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=ANSWER_SCHEMA,
                system_instruction="Return only the JSON the schema describes.",
            ))
        raw = json.loads(answer.text or "{}")
    except Exception as exc:                                 # noqa: BLE001
        logger.info("requirements.capture failed=%s", type(exc).__name__)
        return []

    known = {int(row["id"]): row for row in questions}
    captured = []
    for item in raw.get("answers") or []:
        try:
            question_id = int(item.get("question_id"))
        except (TypeError, ValueError):
            continue
        row = known.get(question_id)
        text = str(item.get("answer") or "").strip()
        if row is None or not text:
            # An id the model invented, or an answer it did not actually
            # find in the reply: neither closes anything.
            continue
        confident = bool(item.get("confident"))
        # Closed in the store first; a decision recorded for a question
        # that is still open would be found twice.
        if not store.answer_question(question_id, text):
            # Somebody else closed it between the ask and now - a second
            # session, or the same reply captured twice. The decision was
            # recorded the first time.
            continue
        store.record_decision(
            project=row["project"],
            decision=f"{row['question']} - {text}",
            rationale=row.get("why") or "",
            source="the boss answered" if confident else "inferred from what the boss said")
        captured.append((question_id, text, confident))

    if captured:
        logger.info("requirements.captured project=%s answered=%d inferred=%d",
                    project, len(captured),
                    sum(1 for item in captured if not item[2]))
    return captured


# ---------------------------------------------------------------------------
# What is already known
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Reading the idea
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Is this even an idea
# ---------------------------------------------------------------------------
