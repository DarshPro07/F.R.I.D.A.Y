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
    CAPPED        a usage limit, not an outage - 429/quota wording that
                  names WHEN it clears. Worth retrying, but not on this
                  provider until the reset, and not with a backoff timer
                  guessed at when the message already said when.

Nothing here logs a prompt, a key, or the bytes of a signature. Signature
*presence* is a diagnostic; signature contents are not.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

TRANSIENT = "TRANSIENT_PROVIDER"
STRUCTURAL = "STRUCTURAL_PROVIDER"
NO_CONTENT = "NO_CONTENT_PROVIDER"
SAFETY = "SAFETY_PROVIDER"
CAPPED = "CAPPED_PROVIDER"
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

#: A cap names itself - "rate limit", "quota" - which a plain 429/5xx does
#: not. Checked before the generic transient markers so a capped message
#: is not swallowed as an ordinary retry-immediately outage.
_CAP_MARKERS = (
    "rate limit", "quota", "usage limit", "limit reached",
    "weekly", "daily", "5-hour", "too many requests",
)

_FINISH = re.compile(r"finish reason:\s*(?:FinishReason\.)?([A-Z_]+)",
                     re.IGNORECASE)

_RESETS_AT = re.compile(r"resets? at\s*([0-9]{1,2}:[0-9]{2}\s*(?:am|pm)?)",
                        re.IGNORECASE)
_RETRY_AFTER = re.compile(r"retry after\s*(\d+)", re.IGNORECASE)


def _cap_reset_at(haystack: str, *, now: datetime | None = None) -> str:
    """
    When a cap clears, as an ISO timestamp - the message's own wording when
    it has one, else the ceiling the wording implies.

    "5-hour" and "weekly" limits do not actually reset in one hour / one
    day; those are floors that unblock the NEXT candidate quickly rather
    than claims about the true reset. A provider that keeps saying CAPPED
    just gets re-marked with a fresh floor each time - it never becomes a
    silent permanent ban.
    """
    now = now or datetime.now()
    match = _RETRY_AFTER.search(haystack)
    if match:
        return (now + timedelta(seconds=int(match.group(1)))).isoformat()
    match = _RESETS_AT.search(haystack)
    if match:
        try:
            clock = match.group(1).strip().lower()
            fmt = "%I:%M%p" if clock.endswith(("am", "pm")) else "%H:%M"
            parsed = datetime.strptime(clock.replace(" ", ""), fmt)
            candidate = now.replace(hour=parsed.hour, minute=parsed.minute,
                                    second=0, microsecond=0)
            if candidate <= now:
                candidate += timedelta(days=1)
            return candidate.isoformat()
        except ValueError:
            pass
    if "5-hour" in haystack:
        return (now + timedelta(hours=1)).isoformat()
    if "weekly" in haystack:
        return (now + timedelta(hours=24)).isoformat()
    if "daily" in haystack:
        midnight = (now + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0)
        return midnight.isoformat()
    return (now + timedelta(minutes=30)).isoformat()


@dataclass(frozen=True)
class Diagnosis:
    """What went wrong, in terms that decide what to do next."""

    kind: str
    finish_reason: str = ""
    status_code: int | None = None
    detail: str = ""
    reset_at: str = ""

    @property
    def worth_retrying(self) -> bool:
        """
        Whether submitting the same request again could plausibly work.

        STRUCTURAL is the important False. A request missing a thought
        signature is missing it on the second attempt too, and the retry costs
        a round trip, tokens, and a second of silence. CAPPED is worth
        retrying too - just not against this provider before `reset_at`.
        """
        return self.kind in (TRANSIENT, NO_CONTENT, CAPPED)

    def __str__(self) -> str:
        bits = [self.kind]
        if self.finish_reason:
            bits.append(f"finish_reason={self.finish_reason}")
        if self.status_code:
            bits.append(f"status={self.status_code}")
        if self.reset_at:
            bits.append(f"reset_at={self.reset_at}")
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

    if any(marker in haystack for marker in _CAP_MARKERS):
        return Diagnosis(CAPPED, finish, status, "a usage limit was reached",
                         reset_at=_cap_reset_at(haystack))

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
