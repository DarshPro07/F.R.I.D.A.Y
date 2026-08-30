
"""
Deciding how hard to think about a question, before answering it.

The thing Friday is actually for. Every other subsystem here - the durable
runs, the capability registry, the policy engine - exists so the boss can stop
opening ChatGPT, and none of them help with the commonest request of all,
which is a question.

Until now every question took one path: a single Gemini turn with seventeen
tools attached. That is right for "what does CQRS mean" and wrong for "should
I use Godot or Unreal", which needs current sources, and wrong again for "why
does this architecture keep failing", which needs to disagree with the premise.

    FAST       stable knowledge. Answer it. No tools, no searching, no waiting.
    RESEARCH   the answer changed recently, or names something specific enough
               that being out of date is being wrong.
    DEEP       a comparison, a recommendation, or an architecture worth
               arguing about. Several sources, read properly, synthesised.

## Why this is a router and not a prompt

"Use search when you need to" has been in the master prompt the whole time. It
does not work reliably, because a model that believes it knows the answer does
not feel a need. The measured consequence: `web_deep_research` exists, is
good, and is not in `CORE_TOOLS` - it sits behind a discovery step in a group
of two, so a research question reaches `web_search` at best. A search is not
research.

Choosing the mode outside the model removes the judgement call from the thing
least able to make it.

## The truth policy

A question containing a false premise is the case that matters most, and the
one an agreeable assistant handles worst. "Why is React Native definitely the
right choice here?" invites agreement, and agreeing is the failure this whole
assistant exists to avoid.

So DEEP and RESEARCH answers carry an instruction to separate what is known
from what is inferred, and to say so when the premise is wrong. That is not
politeness engineering; it is the difference between a colleague and a search
box with a personality.
"""

from __future__ import annotations

import logging

import re

from dataclasses import dataclass, field

logger = logging.getLogger("friday-agent.answer")

FAST = "FAST"

RESEARCH = "RESEARCH"

DEEP = "DEEP"

MODES = (FAST, RESEARCH, DEEP)


@dataclass
class Answer:
    """How to answer this, and why that was chosen."""

    mode: str = FAST
    question: str = ""
    because: str = ""
    #: Capability to call before answering, when the mode needs one.
    capability: str = ""
    arguments: dict = field(default_factory=dict)
    #: What to put in front of the model alongside the findings.
    instruction: str = ""

    @property
    def needs_sources(self) -> bool:
        return self.mode in (RESEARCH, DEEP)

#: Words that put a question in the present. Training data has a horizon and
#: these are the questions that fall past it.
_CURRENT = re.compile(
    r"\b(?:latest|current|recent|recently|today|tonight|this (?:week|month|"
    r"year|morning)|right now|nowadays|these days|so far|up to date|"
    r"newest|new(?:est)? version|still|yet|2\d{3})\b",
    re.IGNORECASE)

#: Asking what is happening, which is always a question about now.
_NEWS = re.compile(
    r"\b(?:news|headlines|happening|announced|released|launched|shipped|"
    r"price|pricing|cost|stock|market|status of|state of|update on)\b",
    re.IGNORECASE)

#: A choice between named things, or a recommendation. These need sources and
#: they need an opinion, which is what separates DEEP from RESEARCH.
_WEIGHING = re.compile(
    r"\b(?:compare|versus|vs\.?|better than|which (?:is|one|should)|"
    r"should i (?:use|pick|choose|go with|build|start)|"
    r"recommend|worth (?:it|using|learning)|pros and cons|trade-?offs?|"
    r"advantages?|disadvantages?|alternatives? to|instead of)\b",
    re.IGNORECASE)

#: Architecture and diagnosis. Long answers where being agreeable is the
#: failure mode.
_ARCHITECTURAL = re.compile(
    r"\b(?:architect(?:ure|ural)?|design(?:ing)? (?:a|the|this)|"
    r"why (?:is|does|do|are|did|would)|how (?:should|would) i (?:structure|"
    r"design|build|organise|organize)|scal(?:e|ing|ability)|"
    r"best (?:way|practice|approach)|approach to|strategy for|"
    r"root cause|keeps? failing|going wrong)\b",
    re.IGNORECASE)

#: Stable knowledge. If a question is only this, memory is enough and a search
#: is a waste of two seconds.
_DEFINITIONAL = re.compile(
    r"^\s*(?:what (?:is|are|does|do)|what'?s|define|explain|tell me about|"
    r"how does .{0,30}\bwork|meaning of|difference between)\b",
    re.IGNORECASE)

#: Anything naming a version, a release or a specific product is a fact with a
#: date attached, however definitional it looks.
_VERSIONED = re.compile(
    r"\b(?:v?\d+\.\d+|version \d|release|changelog|deprecat|end of life|eol)\b",
    re.IGNORECASE)

_ABOUT_THIS_MACHINE = re.compile('\\b(?:my|this|the) (?:computer|pc|laptop|machine|system|disk|drive|screen|browser|wifi|network)\\b|\\bmy (?:files|windows|apps)\\b', re.IGNORECASE)

# Restored from the .pyc oracle. Each pattern is a LOAD_CONST string
# and each flag a LOAD_ATTR on `re` in the compiled module - primary
# evidence from the running system, not inference.
_ABOUT_THIS_CODEBASE = re.compile(
    "\\b(?:this (?:code|codebase|repo|repository|project|module|file|function|class|branch)|our (?:code|codebase|repo|repository)|the (?:source|codebase|repo|repository)|source (?:code|locations?|file)|friday'?s? (?:code|source)|code-?intelligence|\\w+\\.py|\\w+\\.ts|call ?chain|call ?graph)\\b",
    re.IGNORECASE,
)


def classify(question: str) -> tuple[str, str]:
    """
    (mode, why). Deliberately readable rather than clever.

    Order matters and is the argument: a question that weighs two things is
    DEEP even when it also asks what they are, and a question about the
    present is RESEARCH even when it looks definitional - "what is the latest
    version" is not a definition, it is a fact with a date.
    """
    text = (question or "").strip()
    if not text:
        return FAST, "nothing was asked"

    if _WEIGHING.search(text):
        return DEEP, "weighs options or asks for a recommendation"

    if _ARCHITECTURAL.search(text) and not _ABOUT_THIS_MACHINE.search(text):
        return DEEP, "an architecture or diagnosis question worth arguing with"

    if _CURRENT.search(text) or _NEWS.search(text) or _VERSIONED.search(text):
        if _ABOUT_THIS_CODEBASE.search(text):
            return FAST, "about our own source; the answer is on this disk"
        return RESEARCH, "the answer has a date on it"

    if _DEFINITIONAL.match(text):
        return FAST, "stable knowledge; memory is enough"

    # Not obviously anything. FAST is the honest default: it is what Friday
    # does today, it costs nothing extra, and the model can still reach for
    # search itself. Guessing RESEARCH here would put two seconds on every
    # unclassifiable sentence.
    return FAST, "no signal that it needs sources"

#: Appended to every answer that used sources. The part that makes Friday a
#: colleague rather than a search box with a personality.
TRUTH_POLICY = 'The reading is already done and it is above. Answer from it NOW, in this\nreply. Do not say you are looking into it, will check, or will report\nback - there is nothing left to look into, and saying so describes a\ncheck that did happen as one that did not. Measured: with the sources\npresent but no such line, Friday said "I\'m already looking into a\ncomparison of game engines for you".\n\nAnswer from the findings above, and hold to these:\n\n  - Separate what the sources say from what you are inferring. Mark the\n    difference out loud when it matters.\n  - If the question contains an assumption the findings do not support, say\n    so first. Do not answer the question as asked and leave the wrong premise\n    standing.\n  - Where the sources disagree, say they disagree rather than picking the\n    tidiest one.\n  - Name the sources you actually used. Do not cite one you did not read.\n  - If the findings do not answer it, say what is missing rather than filling\n    the gap from memory.'

DEEP_INSTRUCTION = """\
This deserves a real opinion, not a summary. After the facts, give the boss
your recommendation and the reason for it, and name the strongest argument
against your own answer.""" + "\n\n" + TRUTH_POLICY

#: How many sources each mode reads. DEEP is the expensive one and is meant
#: to be - it is asked for a recommendation, and a recommendation from two
#: sources is an opinion with extra steps.
SOURCES = {RESEARCH: 4, DEEP: 7}


def plan(question: str) -> Answer:
    """
    How to answer this question. Pure - no calls, no model, no network.

    Returns the capability to run and the instruction to answer under, so the
    caller can do the expensive part and the decision stays testable without
    either.
    """
    mode, because = classify(question)
    answer = Answer(mode=mode, question=question, because=because)

    if mode == FAST:
        return answer

    answer.capability = "web_deep_research"
    answer.arguments = {"question": question, "sources": SOURCES[mode]}
    answer.instruction = DEEP_INSTRUCTION if mode == DEEP else TRUTH_POLICY
    if mode == DEEP:
        # More room to read, because the comparison is the point and a
        # truncated source is a source that was not read.
        answer.arguments["budget"] = 30000
    return answer


def brief(answer: Answer, findings: dict) -> str:
    """
    The findings and the instruction, as one thing to put before the model.

    Deliberately not a chat message the boss can see - it goes into the turn
    context, and what he hears is Friday's answer rather than a research
    dump.
    """
    import json

    body = findings.get("output") if isinstance(findings, dict) else findings
    return (f"Research findings for: {answer.question}\n\n"
            f"{json.dumps(body, indent=2, default=str)[:12000]}\n\n"
            f"{answer.instruction}")


# ---------------------------------------------------------------------------
# What makes a question need more than memory
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# What to do about it
# ---------------------------------------------------------------------------
