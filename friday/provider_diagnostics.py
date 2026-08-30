"""
Saying what the provider actually did, instead of that it did nothing.

`livekit-plugins-google` raises one sentence for a whole family of unrelated
failures:

    google/llm.py:486   if not candidate.content or not candidate.content.parts:
                            continue
    google/llm.py:510   if not response_generated:
                            raise APIStatusError("no response generated", ...)

Every chunk with no parts is skipped, and if none of them had any, the loop
ends and reports that nothing was generated. Which is true, and useless: the
model returning empty text after a normal stop, a tool call arriving where no
tool was declared, a safety block and a stream whose only content was a
thought signature all arrive under the same eight words.

The reason is not lost - it is in `body` as `finish reason: X` and in the
status code - it is just not read. This reads it, and classifies the failure
into the four kinds that want different responses:

    TRANSIENT     429, 5xx, timeouts. Retrying is the right answer.
    STRUCTURAL    the request cannot succeed as built - a missing thought
                  signature, an orphan function result, a tool call with no
                  tools. Retrying it unchanged is pointless by construction.
    NO_CONTENT    a candidate arrived carrying nothing usable. Sub-classified
                  by finish_reason, because STOP and MAX_TOKENS are different
                  problems.
    SAFETY        blocked or recited. Not a hiccup, and not to be retried
                  into.

Nothing here logs a prompt, a key, or the bytes of a signature. Signature
*presence* is a diagnostic; signature contents are not.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

TRANSIENT = "TRANSIENT_PROVIDER"
STRUCTURAL = "STRUCTURAL_PROVIDER"
NO_CONTENT = "NO_CONTENT_PROVIDER"
SAFETY = "SAFETY_PROVIDER"
UNKNOWN = "UNKNOWN_PROVIDER"

#: Substrings that identify a request which cannot succeed as it is built.
#: Retrying one of these unchanged asks the same impossible question again.
_STRUCTURAL_MARKERS = (
    "thought_signature",
    "unexpected_tool_call",
    "malformed_function_call",
    "function call is missing",
    "invalid_argument",
    "function response",
)

_SAFETY_MARKERS = ("safety", "recitation", "blocked", "prohibited_content")

_TRANSIENT_MARKERS = (
    "deadline_exceeded", "unavailable", "resource_exhausted", "timeout",
    "gateway", "connection", "temporarily",
)

_FINISH = re.compile(r"finish reason:\s*(?:FinishReason\.)?([A-Z_]+)",
                     re.IGNORECASE)


@dataclass(frozen=True)
class Diagnosis:
    """What went wrong, in terms that decide what to do next."""

    kind: str
    finish_reason: str = ""
    status_code: int | None = None
    detail: str = ""

    @property
    def worth_retrying(self) -> bool:
        """
        Whether submitting the same request again could plausibly work.

        STRUCTURAL is the important False. A request missing a thought
        signature is missing it on the second attempt too, and the retry costs
        a round trip, tokens, and a second of silence.
        """
        return self.kind in (TRANSIENT, NO_CONTENT)

    def __str__(self) -> str:
        bits = [self.kind]
        if self.finish_reason:
            bits.append(f"finish_reason={self.finish_reason}")
        if self.status_code:
            bits.append(f"status={self.status_code}")
        if self.detail:
            bits.append(self.detail)
        return " ".join(bits)


def diagnose(error: object) -> Diagnosis:
    """Classify a provider failure from what it carries, not from its sentence."""
    message = f"{type(error).__name__}: {error}"
    body = str(getattr(error, "body", "") or "")
    status = getattr(error, "status_code", None)
    haystack = f"{message} {body}".lower()

    match = _FINISH.search(body) or _FINISH.search(message)
    finish = (match.group(1).upper() if match else "")

    if finish in ("SAFETY", "RECITATION", "PROHIBITED_CONTENT", "BLOCKLIST"):
        return Diagnosis(SAFETY, finish, status, "generation was blocked")
    if any(marker in haystack for marker in _SAFETY_MARKERS) and finish != "STOP":
        return Diagnosis(SAFETY, finish, status, "generation was blocked")

    if finish in ("UNEXPECTED_TOOL_CALL", "MALFORMED_FUNCTION_CALL"):
        return Diagnosis(STRUCTURAL, finish, status,
                         "the model produced a tool call the request did not "
                         "allow; retrying it unchanged cannot help")
    if any(marker in haystack for marker in _STRUCTURAL_MARKERS):
        detail = ("a function call is missing its thought signature - the "
                  "history was rebuilt without it"
                  if "thought_signature" in haystack or
                     "function call is missing" in haystack
                  else "the request is not valid as built")
        return Diagnosis(STRUCTURAL, finish, status, detail)

    if isinstance(status, int) and (status == 429 or 500 <= status < 600):
        return Diagnosis(TRANSIENT, finish, status, "the service was unavailable")
    if any(marker in haystack for marker in _TRANSIENT_MARKERS):
        return Diagnosis(TRANSIENT, finish, status, "the service was unavailable")

    if "no response generated" in haystack or "failed to generate" in haystack:
        if finish == "MAX_TOKENS":
            return Diagnosis(NO_CONTENT, finish, status,
                             "the answer was cut off before any text arrived")
        return Diagnosis(
            NO_CONTENT, finish or "UNSET", status,
            "a candidate arrived with no usable parts" if finish == "STOP"
            else "the stream produced no content")

    return Diagnosis(UNKNOWN, finish, status, message[:160])


def describe_failure(error: object) -> str:
    """One safe line for a log or a gate, naming the cause rather than the symptom."""
    return str(diagnose(error))


# ---------------------------------------------------------------------------
# Not sending the same impossible request twice
# ---------------------------------------------------------------------------


def request_fingerprint(*, model: str, tool_count: int, tool_choice: object,
                        history_length: int, last_role: str = "") -> str:
    """
    Structural identity of a request, with nothing secret in it.

    Deliberately not the prompt. Two turns of the same conversation differ in
    content and are the same *shape*, and shape is what a structural failure
    is about: this model, this many tools, this tool_choice, a history ending
    this way. If that shape has just failed structurally, sending it again is
    a round trip spent to be told the same thing.
    """
    import hashlib

    material = (f"{model}|{tool_count}|{tool_choice}|{history_length}|"
                f"{last_role}")
    return hashlib.sha256(material.encode()).hexdigest()[:16]
