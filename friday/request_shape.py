"""
What kind of thing the boss just said, before anything asks what it means.

The local reflex path exists to act in milliseconds on simple commands. Almost
everything that makes that dangerous is visible in the *shape* of the sentence
rather than in its vocabulary, and shape is cheap to read:

    "pause the music"                COMMAND      act
    "what is playing?"               QUESTION     answer, do not act
    "should I restart my PC?"        QUESTION     and emphatically not a restart
    "what if I shut down?"           HYPOTHETICAL a description, not a request
    "don't close Chrome"             NEGATED      the action named is forbidden
    "open Chrome and find the news"  COMPOUND     two outcomes, not one reflex
    "why is my computer slow?"       REASONING    Friday's brain, not its reflex

Every one of those contains the words of an action. Four of the seven must
never produce one. Vocabulary alone cannot tell them apart - "restart" is in
both "restart the song" and "should I restart my PC?" - so this runs first and
its answer is a gate, not a weight.

## Negation is scoped, not matched

A single regex for negation has already failed in this codebase once: a `\\b`
that became a literal backspace byte left `_NEGATION` completely inert for a
whole test run, and nothing noticed because an inert filter looks exactly like
a filter with nothing to do.

So negation here is two steps that fail independently. Finding the cue is a
pattern; deciding *what the cue covers* is a scope walk to the next clause
boundary. A capability is refused only when its own words sit inside that
span, which means "don't close Chrome, open Paint instead" forbids the close
and permits the open - and a broken cue pattern shows up as a scoping test
failure rather than as silence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


#: What was said, structurally.
COMMAND = "COMMAND"            # do this
QUESTION = "QUESTION"          # tell me
REASONING = "REASONING"        # think about this
HYPOTHETICAL = "HYPOTHETICAL"  # suppose this
COMPOUND = "COMPOUND"          # do these
CONTROL = "CONTROL"            # stop / continue / never mind

SHAPES = (COMMAND, QUESTION, REASONING, HYPOTHETICAL, COMPOUND, CONTROL)

#: Shapes a reflex may act on. The other four are answers, not actions.
ACTIONABLE = (COMMAND,)


@dataclass
class Shape:
    """The reading, and the evidence for it."""

    kind: str = COMMAND
    #: Spans of the sentence that are under a negation cue.
    forbidden: tuple[str, ...] = ()
    #: What made it this shape, for the error taxonomy.
    because: str = ""
    #: Clauses, when it is compound.
    parts: tuple[str, ...] = field(default_factory=tuple)
    #: The instruction under the politeness, when there was one.
    command: str = ""

    @property
    def actionable(self) -> bool:
        return self.kind in ACTIONABLE


# ---------------------------------------------------------------------------
# Questions
# ---------------------------------------------------------------------------

#: An opening that makes the whole sentence a question, whatever verbs follow.
#:
#: Anchored at the start rather than searched, because "tell me what is
#: playing" is a request for an answer and "play what I was listening to" is
#: not, and the difference is entirely position.
_ASKS = re.compile(
    r"^\s*(?:so\s+|and\s+|but\s+|ok(?:ay)?[,\s]+|friday[,\s]+|hey[,\s]+)*"
    r"(?:what|which|who|whose|where|when|how|why|is|are|was|were|do|does|"
    r"did|can|could|should|would|will|shall|am|have|has|had)\b",
    re.IGNORECASE)

# Restored from the .pyc oracle. Each pattern is a LOAD_CONST string
# and each flag a LOAD_ATTR on `re` in the compiled module - primary
# evidence from the running system, not inference.
_POLITE_IMPERATIVE = re.compile("^\\s*(?:so\\s+|and\\s+|ok(?:ay)?[,\\s]+|friday[,\\s]+|hey[,\\s]+|please\\s+)*(?:can|could|would|will|won'?t)\\s+(?:you|u)\\s+(?:please\\s+|just\\s+)?(?!be\\b|have\\b|do\\b)(\\w+)", re.IGNORECASE)

#: A request for an answer that does not open with a question word.
_ASKS_LATER = re.compile(
    r"\b(?:tell me|let me know|show me|remind me|give me)\s+"
    r"(?:what|which|who|where|when|why|how|if|whether)\b",
    re.IGNORECASE)


def _is_question(text: str) -> bool:
    if text.rstrip().endswith("?"):
        return True
    if _ASKS_LATER.search(text):
        return True
    return bool(_ASKS.match(text))


# ---------------------------------------------------------------------------
# Reasoning
# ---------------------------------------------------------------------------

#: Verbs that ask Friday to think rather than to act. Not a reflex under any
#: circumstances - the fast path has no opinion about anything.
_THINKING = re.compile(
    r"\b(?:why|compare|analys[ei]|analyz[ei]|research|design|plan out|"
    r"explain|debug|diagnose|recommend|suggest|advis[ei]|evaluate|assess|"
    r"review|investigate|figure out|work out|think about|look into|"
    r"help me (?:decide|choose|pick|understand)|what do you think|"
    r"your (?:view|opinion|take))\b",
    re.IGNORECASE)


# ---------------------------------------------------------------------------
# Hypotheticals
# ---------------------------------------------------------------------------

#: Framing that describes an action instead of asking for it.
#:
#: "What if I shut down the computer" names a shutdown and requests a
#: conversation about one. The words are identical to the command; only the
#: frame differs, and the frame is at the front.
_SUPPOSING = re.compile(
    r"\b(?:what if|what would happen|suppose|supposing|imagine|if i (?:were to|was to)|hypothetically|in theory|would it (?:help|work|be)|could (?:it|that|this) (?:help|work|fix)|(?:could|would|should)\s+\w+ing\b|is it (?:worth|a good idea|safe|wise|ok(?:ay)?)\b|(?:are|were) you able to\b|do you know how to\b|can you tell me how\b|what happens (?:when|if)\b|what does .{0,20}\bdo\b|how (?:do|would|can) i\b|should i|do i need to|would (?:closing|stopping|restarting|deleting|opening|killing))\b",
    re.IGNORECASE)


# ---------------------------------------------------------------------------
# Negation
# ---------------------------------------------------------------------------

#: The cue. Deliberately only the cue - what it covers is decided separately,
#: so a broken pattern here fails a scoping test rather than going quiet.
_NEGATION_CUE = re.compile(
    r"\b(?:do not|don'?t|never|no need to|rather not|"
    r"without|except|other than|apart from|instead of|"
    r"avoid|refrain from|leave|skip)\b",
    re.IGNORECASE)

#: Where a negated span stops. A clause boundary ends the cue's reach, which
#: is what lets "don't close Chrome, open Paint" forbid one and permit the
#: other.
_CLAUSE_END = re.compile(
    r"[,;.!?]|\b(?:but|however|instead|just|only|and then|then)\b",
    re.IGNORECASE)


def forbidden_spans(text: str) -> tuple[str, ...]:
    """
    The parts of the sentence a negation cue covers.

    A span runs from the cue to the next clause boundary. Returned as text so
    the caller can ask whether a capability's own words fall inside it -
    which is a different question from "was there a negation somewhere", and
    the difference is why "don't close Chrome, open Paint instead" is not a
    refusal to do anything at all.
    """
    spans: list[str] = []
    for cue in _NEGATION_CUE.finditer(text or ""):
        rest = text[cue.end():]
        boundary = _CLAUSE_END.search(rest)
        spans.append(rest[:boundary.start()] if boundary else rest)
    return tuple(span.strip() for span in spans if span.strip())


def is_forbidden(text: str, words) -> bool:
    """Whether any of `words` sits inside a negated span of `text`."""
    spans = [span.lower() for span in forbidden_spans(text)]
    if not spans:
        return False
    for word in words:
        cleaned = str(word).lower().strip()
        if len(cleaned) < 3:
            continue
        if any(cleaned in span for span in spans):
            return True
    return False


# ---------------------------------------------------------------------------
# Compound
# ---------------------------------------------------------------------------


def _parts(text: str) -> tuple[str, ...]:
    """The independent outcomes in the sentence, per the planner."""
    from friday import planner as P

    try:
        return tuple(goal.intent for goal in P.interpret(text or "").goals)
    except Exception:                                       # noqa: BLE001
        return ()


# ---------------------------------------------------------------------------
# Control
# ---------------------------------------------------------------------------

#: Managing the conversation rather than the machine. Never a capability.
_CONTROL = re.compile(
    r"^\s*(?:never mind|forget it|forget that|cancel that|stop( it)?|wait|"
    r"hold on|hang on|no,? wait|scratch that|as you were|carry on|"
    r"go on|continue|nothing|no thanks|that'?s all|thanks|thank you)"
    r"\s*[.!]?\s*$",
    re.IGNORECASE)


def _without(text: str, spans: tuple[str, ...]) -> str:
    """The sentence with its forbidden spans taken out."""
    for span in spans:
        text = text.replace(span, " ")
    return text


# ---------------------------------------------------------------------------
# The reading
# ---------------------------------------------------------------------------


def read(text: str) -> Shape:
    """
    What kind of thing this is. First match wins, and the order is the
    argument: a hypothetical that is also a question is still a hypothetical,
    and a question that mentions research is still a question.
    """
    text = (text or "").strip()
    forbidden = forbidden_spans(text)

    if not text:
        return Shape(kind=CONTROL, because="nothing was said")

    if _CONTROL.match(text):
        return Shape(kind=CONTROL, forbidden=forbidden,
                     because="managing the conversation, not the machine")

    # Before questions: "should I restart my PC?" is both, and reading it as a
    # question would let a question-shaped gate pass it to a router that sees
    # the word restart. It is a request for an opinion about an action.
    if _SUPPOSING.search(text):
        return Shape(kind=HYPOTHETICAL, forbidden=forbidden,
                     because="describes an action rather than asking for one")

    if _THINKING.search(text):
        return Shape(kind=REASONING, forbidden=forbidden,
                     because="asks Friday to think, which the reflex cannot")

    # "Can you close the browser?" is an instruction wearing a question's
    # clothes. The command is what follows the politeness, and it is what
    # the router should see.
    polite = _POLITE_IMPERATIVE.match(text)
    if polite:
        command = text[polite.start(1):].strip().rstrip("?").strip()
        parts = _parts(_without(command, forbidden))
        if len(parts) > 1:
            return Shape(kind=COMPOUND, forbidden=forbidden, parts=parts,
                         command=command, because=f"{len(parts)} outcomes")
        return Shape(kind=COMMAND, forbidden=forbidden, parts=parts,
                     command=command,
                     because="a polite instruction, not a question")

    if _is_question(text):
        return Shape(kind=QUESTION, forbidden=forbidden,
                     because="asks for an answer, not an action")

    # Clauses are counted with the forbidden spans taken out: "close the
    # browser but not the editor" is one outcome, not two.
    parts = _parts(_without(text, forbidden))
    if len(parts) > 1:
        return Shape(kind=COMPOUND, forbidden=forbidden, parts=parts,
                     command=text, because=f"{len(parts)} independent outcomes")

    return Shape(kind=COMMAND, forbidden=forbidden, parts=parts,
                 command=text, because="one thing to do")
