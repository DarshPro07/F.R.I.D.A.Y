"""
InputArbiter: route new input while an objective run is active.

New input must never be destructive by default. A status question is
answered from durable state and never cancels the run; a side conversation
does not touch it; only an explicit stop cancels it. Modification requests
change the active run's inputs; a fresh request (no active run) starts a
new objective.

Classification is deterministic and priority-ordered:

1. CANCEL             explicit stop ("stop", "cancel", "abort", ...)
2. QUERY_ABOUT_RUN    status/progress question ("status", "progress",
                      "how's it going", "where are you up to", ...)
3. MODIFICATION       changes an active run ("change", "instead", "skip",
                      "redo", ...)
4. NEW_OBJECTIVE      a fresh request ("research", "set up", "objective",
                      "check", ...)
5. SIDE_CONVERSATION  anything else - never cancels or mutates the run

Ambiguity favours stopping: a phrase that reads as both cancel and query
classifies as CANCEL, never the reverse.

The first three are things a person says *while work is running*, and they
are short. That is not a stylistic observation, it is load-bearing - see
`_CANCEL_WORDS`.
"""

from __future__ import annotations

import re



#: The full intent vocabulary; classification always returns one of these.
INTENTS = ("CANCEL", "QUERY_ABOUT_RUN", "SIDE_CONVERSATION",
           "MODIFICATION", "NEW_OBJECTIVE")

_CANCEL = re.compile(
    r"\b(?:stop|cancel|abort|forget it|never mind|enough)\b", re.IGNORECASE)

_QUERY = re.compile(
    r"\b(?:status|progress|happening.*run)\b|"
    r"how.*(?:going|far)|where.*(?:up to|are you)", re.IGNORECASE)

_MODIFICATION = re.compile(
    r"\b(?:change|instead|skip|redo|don't|dont|replace|modify|switch)\b",
    re.IGNORECASE)

_NEW_OBJECTIVE = re.compile(
    r"\b(?:research|set up|objective|check|create|build|new)\b",
    re.IGNORECASE)

#: How long a control utterance can be.
#:
#: Nobody cancels a run in eighty words. Without a length bound the patterns
#: above match anywhere in a text of any size, and a long instruction contains
#: every trigger word - almost always negated, because a careful request spends
#: its length saying what *not* to do.
#:
#: A real one, dictated as a single request to audit every capability,
#: contained all four:
#:
#:     "Do not stop because a model turn ended."      -> CANCEL
#:     "query your own status"                        -> QUERY_ABOUT_RUN
#:     "modify one not-yet-started audit task"        -> MODIFICATION
#:     "skip another harmless pending audit task"     -> MODIFICATION
#:
#: CANCEL matched first, so the largest objective the system had ever been
#: given was classified as an instruction to stop. There was no run to stop,
#: so it became a side conversation: nothing was admitted, no capability was
#: claimed, and the whole audit ran in the conversational loop until it hit
#: the tool-step ceiling and the provider gave out.
_CANCEL_WORDS = 8
_QUERY_WORDS = 14
_MODIFICATION_WORDS = 20

#: "Do not stop", "don't skip", "without stopping" - the word is there and the
#: instruction is its opposite. Checked only in the run-up to the match, so
#: "don't stop" is caught while "stop the music, and don't worry about the
#: volume" is left alone.
_NEGATION = re.compile(
    r"\b(?:do not|don't|dont|never|without|rather than|instead of)\s+"
    r"(?:\w+\s+){0,2}$",
    re.IGNORECASE)


def _is_command(pattern: "re.Pattern[str]", text: str, limit: int) -> bool:
    """Whether this is that instruction, rather than a text mentioning it."""
    if len(text.split()) > limit:
        return False
    match = pattern.search(text)
    if match is None:
        return False
    return _NEGATION.search(text[:match.start()]) is None


def classify_input(text: str) -> str:
    """
    Deterministic intent classification.

    CANCEL and QUERY_ABOUT_RUN match first; MODIFICATION before NEW_OBJECTIVE;
    everything else is a side conversation. The three control intents must
    also *look* like control - short, and not negated - because a long request
    that happens to contain the word "stop" is a request, not a stop.
    """
    text = text or ""
    if _is_command(_CANCEL, text, _CANCEL_WORDS):
        return "CANCEL"
    if _is_command(_QUERY, text, _QUERY_WORDS):
        return "QUERY_ABOUT_RUN"
    if _is_command(_MODIFICATION, text, _MODIFICATION_WORDS):
        return "MODIFICATION"
    if _NEW_OBJECTIVE.search(text):
        return "NEW_OBJECTIVE"
    return "SIDE_CONVERSATION"
