"""
FR-COD-003: a coding CLI's 1/2/3 question must not be guessed at or ignored.

A CLI run headless (`-p`, see cli.py) cannot use its own interactive prompt -
`AskUserQuestion` is not in the tool list and the permission hook never fires
- so when Claude Code, a sub-tool it started, or Hermes needs a decision, it
says so in its own text output: "1) ... 2) ... (recommended)", "Overwrite the
lockfile? [y/N]", "4) provide your own answer". Two failures are possible
once that line exists: FRIDAY quietly answers something consequential on
guesswork, or FRIDAY blocks a run forever on something trivial because no one
is watching. This module is the fix for both, and it is deliberately narrow
so it can be trusted: a parser (`detect`) plus a decision function (`decide`)
with no model call and no subprocess of its own. Pure and deterministic means
a test can drive every branch, including the one that must always refuse -
see `decide`'s CONSEQUENTIAL rule.

This module never talks to the CLI. It has no opinion on *where* the text
came from or what happens to its answer; the caller (`claude_code.py`) owns
reading the stream, owns the objective text, and owns logging the decision -
see `Progress._watch_for_prompt` there. Answering the CLI back is a different,
harder problem this module does not attempt: by the time a Claude Code run's
stdout is being read, its stdin is already closed (cli.py writes the prompt
once and closes the pipe), so nothing downstream of a detected question can
be fed back into that same process. What this module guarantees is narrower
and load-bearing on its own: every question the CLI raises in text is
recognised, scored and either answered with a stated reason or handed to a
person - never silently skipped and never silently guessed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# detect()
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Option:
    """One choice offered by the prompt."""

    index: str            # what you would type back: "1", "2", "y", "n"
    label: str             # the option text, markers and all - kept verbatim
                           # so evidence can quote exactly what the CLI said
    recommended: bool = False   # "(recommended)", "(default)", or the
                                 # capitalised half of [Y/n] / [y/N]
    free_text: bool = False     # "provide your own answer" / "other" / "custom"


@dataclass(frozen=True)
class Prompt:
    """One interactive question, as found in a block of CLI output."""

    question: str
    options: tuple[Option, ...]
    free_text_allowed: bool = False

    def as_dict(self) -> dict:
        return {
            "question": self.question,
            "options": [o.label for o in self.options],
            "free_text_allowed": self.free_text_allowed,
        }


#: `1) label`, `1. label`, `[1] label` - a line that is only ever an option
#: marker at the start of a line, never a version number or a list price
#: buried in prose.
_OPTION_LINE = re.compile(
    r"^[ \t]*(?:\[(?P<idxb>\d{1,2})\]|(?P<idxp>\d{1,2})[.)])[ \t]+(?P<label>\S.*?)[ \t]*$",
    re.MULTILINE,
)

_RECOMMENDED_MARK = re.compile(r"\((?:recommended|default)\)", re.IGNORECASE)

#: The fourth option the owner described: an explicit escape hatch to type
#: something that was not on the list. Matched narrowly - "other" or "custom"
#: has to be the option's own answer, not a word inside a real choice like
#: "custom domain setup", which is why these anchor at the start of the label.
_FREE_TEXT_MARK = re.compile(
    r"provide your own answer|^other\b|^custom\b", re.IGNORECASE)

#: A single-line yes/no with the CLI's own default shown by capitalisation.
_YES_NO = re.compile(
    r"^(?P<question>.*?\S)[ \t]*\[(?P<y>[Yy])/(?P<n>[Nn])\][ \t]*$", re.MULTILINE)

#: Whether the text around a numbered list reads as a question at all, so a
#: plain numbered plan ("1. Read the config  2. Update the schema") is not
#: mistaken for a choice nobody was actually offered.
_QUESTION_CUE = re.compile(
    r"\?[ \t]*$|\b(?:choose|select|pick|which|enter (?:a|your) choice"
    r"|your choice|type a number)\b",
    re.IGNORECASE,
)


def _last_nonempty_line(text: str) -> str:
    for line in reversed(text.splitlines()):
        if line.strip():
            return line.strip()
    return ""


def _first_nonempty_line(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return ""


def _detect_yes_no(text: str) -> Prompt | None:
    matches = list(_YES_NO.finditer(text))
    if not matches:
        return None
    # The last one: a block of narration ending in the live question is the
    # realistic shape, and the question is whichever bracket is nearest the
    # end of what the CLI just said.
    match = matches[-1]
    question = match.group("question").strip(" \t:-")
    if not question:
        return None
    options = (
        Option(index="y", label="Yes", recommended=match.group("y") == "Y"),
        Option(index="n", label="No", recommended=match.group("n") == "N"),
    )
    return Prompt(question=question, options=options, free_text_allowed=False)


def _detect_numbered(text: str) -> Prompt | None:
    matches = list(_OPTION_LINE.finditer(text))
    if len(matches) < 2:
        return None       # one numbered line is not a choice between anything

    options = []
    for m in matches:
        idx = m.group("idxb") or m.group("idxp")
        label = m.group("label").strip()
        options.append(Option(
            index=idx, label=label,
            recommended=bool(_RECOMMENDED_MARK.search(label)),
            free_text=bool(_FREE_TEXT_MARK.search(label))))

    # A recommended/default/free-text marker is itself strong evidence this
    # is a question. Absent that, only take it as one if the text around the
    # list actually reads like a question - otherwise this is indistinguishable
    # from an ordinary numbered plan, and firing on that is the exact failure
    # the detector must not have: answering a question nobody asked.
    has_marker = any(o.recommended or o.free_text for o in options)
    lead = _last_nonempty_line(text[:matches[0].start()])
    trail = _first_nonempty_line(text[matches[-1].end():])
    if not has_marker and not _QUESTION_CUE.search(lead) \
            and not _QUESTION_CUE.search(trail):
        return None

    return Prompt(question=lead, options=tuple(options),
                  free_text_allowed=any(o.free_text for o in options))


def detect(text: str) -> Prompt | None:
    """
    Find one interactive question in a block of CLI output, or None.

    Tries the single-line yes/no shape first, then a numbered list - the two
    do not overlap in the wild, but a block could in principle contain a
    stray `[Y/n]`-looking fragment inside a longer numbered explanation, and
    yes/no is the narrower, more specific pattern so it is checked first.
    """
    if not text or not text.strip():
        return None
    return _detect_yes_no(text) or _detect_numbered(text)


# ---------------------------------------------------------------------------
# classify()
# ---------------------------------------------------------------------------

TRIVIAL = "TRIVIAL"
ORDINARY = "ORDINARY"
CONSEQUENTIAL = "CONSEQUENTIAL"

#: The load-bearing set. Matched against the question and every option label,
#: never against `objective` - what the run is *for* must never be able to
#: talk a destructive option down to merely ordinary. Phrased as stems so
#: "delete"/"deleting"/"deletes" and "overwrite"/"overwriting" all match, and
#: as two-part patterns for the two phrases that are only dangerous together
#: ("grant" alone is nothing; "grant it permission" is the thing PRD 17 means).
_CONSEQUENTIAL = re.compile(
    r"\bdelet\w*\b"
    r"|\boverwrit\w*\b"
    r"|\bforce[- ]?push\w*\b"
    r"|\breset\w*\b"
    r"|\bpublish\w*\b"
    r"|\bdeploy\w*\b"
    r"|\bpay(?:ing|ment|s)?\b"
    r"|\bgrant\w*\b.{0,25}\bpermission\w*\b"
    r"|\bpermission\w*\b.{0,25}\bgrant\w*\b"
    r"|\bdisabl\w*\b.{0,25}\bcheck\w*\b"
    r"|\brewrit\w*\b.{0,25}\bhistory\b",
    re.IGNORECASE,
)

#: Genuinely low-stakes: style, naming, which of two equivalent things, or a
#: yes/no whose default `detect()` already read off the prompt itself.
_TRIVIAL_HINTS = re.compile(
    r"\bformat\w*\b|\bindent\w*\b|\bwhitespace\b|\btabs? or spaces\b"
    r"|\bnaming\b|\bwhich file\b|\bcode style\b|\blint(?:ing)?\b"
    r"|\bcomment style\b|\bequivalent\b",
    re.IGNORECASE,
)


def classify(prompt: Prompt) -> str:
    """
    Risk band for one prompt: TRIVIAL, ORDINARY or CONSEQUENTIAL.

    Consequential is checked first and wins outright - a destructive option
    does not become safer for also offering a harmless one next to it.
    """
    haystack = " ".join([prompt.question, *(o.label for o in prompt.options)])
    if _CONSEQUENTIAL.search(haystack):
        return CONSEQUENTIAL

    is_yes_no = {o.index for o in prompt.options} == {"y", "n"}
    if is_yes_no or _TRIVIAL_HINTS.search(haystack):
        return TRIVIAL

    return ORDINARY


# ---------------------------------------------------------------------------
# decide()
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Decision:
    """What to do about one prompt, and why - never written to a log itself."""

    answer: str | None     # an Option.index, or None when nothing was decided
    escalate: bool
    confidence: float
    reason: str            # one sentence, for a person
    evidence: str           # what in the prompt or objective supported it

    def as_dict(self) -> dict:
        return {"answer": self.answer, "escalate": self.escalate,
                "confidence": self.confidence, "reason": self.reason,
                "evidence": self.evidence}


_STOPWORDS = frozenset({
    "the", "and", "for", "with", "should", "would", "which", "what", "this",
    "that", "are", "you", "use", "using", "want", "need", "have", "does",
    "can", "our", "your", "any", "how", "why", "when", "from", "into", "will",
    "please", "provide", "answer", "own",
})


def _words(text: str) -> set[str]:
    return {w for w in re.split(r"[^a-z0-9]+", (text or "").lower())
            if len(w) > 2 and w not in _STOPWORDS}


def decide(prompt: Prompt, *, objective: str = "",
           confidence_floor: float = 0.6) -> Decision:
    """
    Answer a prompt, or say it must be escalated - never both, never neither.

    In this order, and no other:

      1. CONSEQUENTIAL always escalates, whatever the confidence would have
         been - checked before anything else is even scored.
      2. An explicit recommended/default option on a TRIVIAL or ORDINARY
         prompt is taken, with high confidence.
      3. An option whose words overlap the objective is taken, if that
         overlap clears `confidence_floor`.
      4. Otherwise: escalate. Guessing between options nothing recommended
         and nothing in the objective favours is not a decision, it is a coin
         flip wearing a decision's clothes.

    A free-text option ("provide your own answer") is never auto-selected by
    rule 2 or 3, consequential or not: this module has no model call, so it
    has no way to write the text such an answer would need, and a made-up
    answer is worse than an escalated question.
    """
    band = classify(prompt)

    if band == CONSEQUENTIAL:
        return Decision(
            answer=None, escalate=True, confidence=0.0,
            reason="the options touch a consequential action, so this is "
                   "never decided without the user",
            evidence=f"classify() returned CONSEQUENTIAL for {prompt.question!r}")

    choices = [o for o in prompt.options if not o.free_text]

    recommended = [o for o in choices if o.recommended]
    if recommended:
        chosen = recommended[0]
        return Decision(
            answer=chosen.index, escalate=False, confidence=0.95,
            reason=f"option {chosen.index} is the prompt's own "
                   f"recommended/default choice",
            evidence=f"{chosen.label!r} carried an explicit "
                     f"recommended/default marker")

    objective_words = _words(objective)
    best, best_overlap = None, 0
    for option in choices:
        overlap = len(_words(option.label) & objective_words)
        if overlap > best_overlap:
            best, best_overlap = option, overlap

    confidence = min(0.5 + 0.15 * best_overlap, 0.95) if best is not None else 0.0

    if best is not None and confidence >= confidence_floor:
        return Decision(
            answer=best.index, escalate=False, confidence=confidence,
            reason=f"option {best.index} is the one that matches the "
                   f"stated objective",
            evidence=f"{best.label!r} shares {best_overlap} word(s) with "
                     f"the objective {objective!r}"[:300])

    return Decision(
        answer=None, escalate=True, confidence=confidence,
        reason="no option is recommended and none matches the objective "
               "clearly enough to clear the confidence floor",
        evidence=f"{len(prompt.options)} option(s) offered; best objective "
                 f"overlap was {best_overlap} word(s) (confidence "
                 f"{confidence:.2f} < floor {confidence_floor:.2f})")
