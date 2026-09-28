"""
The classification a piece of content earns, and what that earns it at the
gateway door.

The failure this exists to prevent is a model talking its way past a rule
that was never meant to be negotiable: a prompt that says "this is public,
send it anywhere" must not change what actually leaves the machine. So
classification is deterministic and structural - regexes over shapes a
credential, a card number, or an explicit marker actually has - never a
judgement the text itself gets to argue about. Nothing here is exposed as a
tool a model can call with its own claim about the content; the only inputs
are the compiled text and, optionally, a caller-supplied `hint` that can
only raise the sensitivity found, never lower it.

`friday.privacy` owns the mode (OPEN / PRIVATE_ONLY) and the list of local
backends; this module does not duplicate either. It owns the seven-class
vocabulary (FR-PRV-001), the deterministic classifier, and the routing rule
that turns (class, backend, mode) into ALLOW / REDACT / REFUSE.
`model_gateway.py` asks both, at the one seam a request leaves for a
provider - so a REFUSE never becomes a "provider was down" retry on the
next route, and a REDACT never means the raw text was tried first "just in
case".
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from friday import privacy

# ---------------------------------------------------------------------------
# Vocabulary (FR-PRV-001) - CLASSES is least to most sensitive
# ---------------------------------------------------------------------------

PUBLIC = "PUBLIC"
INTERNAL = "INTERNAL"
PERSONAL = "PERSONAL"
CUSTOMER_PII = "CUSTOMER_PII"
CONFIDENTIAL = "CONFIDENTIAL"
SECRET = "SECRET"
LOCAL_ONLY = "LOCAL_ONLY"

CLASSES = (PUBLIC, INTERNAL, PERSONAL, CUSTOMER_PII, CONFIDENTIAL, SECRET, LOCAL_ONLY)

_RANK = {cls: i for i, cls in enumerate(CLASSES)}


# ---------------------------------------------------------------------------
# Deterministic classifier - structure, never sentiment
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Rule:
    pattern: re.Pattern
    placeholder: str


def _rule(expr: str, placeholder: str, *, flags: int = 0) -> _Rule:
    return _Rule(re.compile(expr, flags), placeholder)


#: Structural shapes only, per class - never a keyword an ordinary paragraph
#: would trip by accident. Reused by both `classify` (detection) and
#: `redact` (removal), so what was found is exactly what gets stripped.
CLASS_PATTERNS: dict[str, tuple[_Rule, ...]] = {
    LOCAL_ONLY: (
        _rule(r"\bLOCAL[_ -]?ONLY\b", "[LOCAL-ONLY]", flags=re.IGNORECASE),
    ),
    SECRET: (
        _rule(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----", "[REDACTED-KEY]"),
        _rule(r"\bsk-[A-Za-z0-9]{20,}\b", "[REDACTED-APIKEY]"),
        _rule(r"\bAKIA[0-9A-Z]{16}\b", "[REDACTED-APIKEY]"),
        _rule(r"\bghp_[A-Za-z0-9]{36}\b", "[REDACTED-APIKEY]"),
        _rule(r"\b(?:api[_-]?key|secret|password|token)\b\s*[:=]\s*['\"]?"
              r"[A-Za-z0-9_\-/+]{12,}", "[REDACTED-SECRET]", flags=re.IGNORECASE),
    ),
    CONFIDENTIAL: (
        _rule(r"\bCONFIDENTIAL\b", "[CONFIDENTIAL]", flags=re.IGNORECASE),
    ),
    CUSTOMER_PII: (
        # Longest shape first: redact() applies these in order, and a
        # 16-digit card is also a superset match for the 12-digit Aadhaar
        # pattern - checking the longer shape first stops it from being
        # partially consumed by the shorter one before the card rule runs.
        _rule(r"\b(?:\d[ -]?){13,16}\b", "[REDACTED-CARD]"),        # card shape
        _rule(r"\b\d{3}-\d{2}-\d{4}\b", "[REDACTED-SSN]"),          # SSN shape
        _rule(r"\b\d{4}\s\d{4}\s\d{4}\b", "[REDACTED-ID]"),         # Aadhaar shape
    ),
    PERSONAL: (
        _rule(r"\b\d{3}[.-]\d{3}[.-]\d{4}\b", "[REDACTED-PHONE]"),
        _rule(r"\bDOB\s*[:=]\s*\S+", "[REDACTED-DOB]", flags=re.IGNORECASE),
    ),
    INTERNAL: (
        _rule(r"\bINTERNAL\b", "[INTERNAL]", flags=re.IGNORECASE),
    ),
    PUBLIC: (
        _rule(r"\bPUBLIC\b", "[PUBLIC]", flags=re.IGNORECASE),
    ),
}

#: An email address paired with a plausible two-word name is CUSTOMER_PII
#: even though neither shape alone is: an email alone is routine, a name
#: alone is routine, the pairing is what identifies a customer record.
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[A-Za-z]{2,}")
_NAME = re.compile(r"\b[A-Z][a-z]+\s[A-Z][a-z]+\b")


def classify(text: str, *, hint: str | None = None) -> str:
    """
    The most sensitive class TEXT itself justifies.

    Checked most-sensitive-first (`reversed(CLASSES)`) so the first match
    wins outright and nothing downstream can argue it back down. Unmatched
    text is PUBLIC - the least restrictive class, not a guess. `hint` may
    only RAISE the result (a trusted caller that already knows a field is
    CUSTOMER_PII, say); it can never lower what the text's own shape
    earned. That asymmetry is what keeps this safe if ever handed an
    untrusted hint: claiming "public" cannot launder sensitive content.
    """
    text = text or ""
    best = PUBLIC
    for cls in reversed(CLASSES):
        if cls == CUSTOMER_PII and _EMAIL.search(text) and _NAME.search(text):
            best = cls
            break
        rules = CLASS_PATTERNS.get(cls, ())
        if any(r.pattern.search(text) for r in rules):
            best = cls
            break
    if hint in _RANK and _RANK[hint] > _RANK[best]:
        best = hint
    return best


def redact(text: str, cls: str) -> tuple[str, int]:
    """The minimised text for CLS, and how many spans were removed."""
    text = text or ""
    count = 0
    for rule in CLASS_PATTERNS.get(cls, ()):
        text, n = rule.pattern.subn(rule.placeholder, text)
        count += n
    if cls == CUSTOMER_PII:
        text, n = _EMAIL.subn("[REDACTED-EMAIL]", text)
        count += n
        text, n = _NAME.subn("[REDACTED-NAME]", text)
        count += n
    return text, count


# ---------------------------------------------------------------------------
# Routing rule (FR-PRV-001 acceptance criteria 1 and 3)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Decision:
    verdict: str          # ALLOW | REDACT | REFUSE
    reason: str
    spans: tuple[str, ...] = ()


#: SECRET and LOCAL_ONLY are unroutable off a local backend. No
#: minimisation is offered because there is no safe partial version of a
#: private key or an explicit "keep this here" instruction.
_REFUSE_OFF_LOCAL = (SECRET, LOCAL_ONLY)

#: PERSONAL, CUSTOMER_PII and CONFIDENTIAL may reach a cloud backend only
#: with the identifying spans stripped first.
_REDACT_OFF_LOCAL = (PERSONAL, CUSTOMER_PII, CONFIDENTIAL)


def decision(cls: str, *, backend: str, mode: str) -> Decision:
    """
    ALLOW / REDACT / REFUSE for CLS going to BACKEND under MODE.

    Locality is `privacy.LOCAL_BACKENDS` by name - the same list
    `privacy.backend_allowed` checks - so a backend is never local here and
    remote there (Requirement 1: physically unroutable, not just declined).
    `mode` matters only when it is `privacy.PRIVATE_ONLY`: that mode's whole
    promise is that nothing leaves the machine, and a minimised payload is
    still a payload that left. So under PRIVATE_ONLY a class that would
    otherwise be redacted for the cloud is refused instead, the same as
    SECRET always is - redaction is a best-effort filter, not the guarantee
    that mode promises.
    """
    local = (backend or "").strip().lower() in privacy.LOCAL_BACKENDS
    if local:
        return Decision("ALLOW", f"{cls} content is going to {backend}, a local "
                                  f"backend; nothing leaves this machine.")

    if cls in _REFUSE_OFF_LOCAL:
        return Decision(
            "REFUSE",
            f"{cls} content cannot be sent to {backend or 'a cloud provider'}; "
            f"only a local backend may see it. Route this to a local model, or "
            f"remove the {cls.lower()} content and try again.")

    if cls in _REDACT_OFF_LOCAL:
        if mode == privacy.PRIVATE_ONLY:
            return Decision(
                "REFUSE",
                f"{cls} content is refused under PRIVATE_ONLY: minimisation is "
                f"a best effort, not the guarantee that mode promises, and "
                f"{backend or 'a cloud provider'} is not local.")
        spans = tuple(rule.placeholder for rule in CLASS_PATTERNS.get(cls, ()))
        if cls == CUSTOMER_PII:
            spans = spans + ("[REDACTED-EMAIL]", "[REDACTED-NAME]")
        return Decision(
            "REDACT",
            f"{cls} content is minimised before reaching "
            f"{backend or 'a cloud provider'}.", spans)

    return Decision("ALLOW", f"{cls} content is safe to send to "
                              f"{backend or 'a cloud provider'}.")
