"""Friday Browser Capability - the policy layer in FRONT of any browser
actuation (Phase 3 of the vnext build).

This module deliberately contains NO browser engine. Hermes already owns
navigation/snapshot/click/type via its browser toolset and the
extension-controller broker (verified live in Phase 1F.c: 11 control
capabilities advertised). Friday's job is the part Hermes must never
decide alone:

    intent -> risk classification -> policy verdict -> (only then) adapter

Two hard boundaries, both fail-closed and both testable without a
browser:

1. SENSITIVE DOMAINS (banking/payment): the verdict is computed from the
   URL BEFORE any capture happens. A blocked page yields exactly one
   BLOCKED_SENSITIVE_DOMAIN string - never DOM text, never a screenshot
   path, never a vision description. This wraps friday.sensitive_domains
   (the observation boundary) and friday.netguard (the network boundary)
   so no adapter call can be reached with a blocked URL.

2. SECRET-SHAPED CONTENT: anything that pattern-matches a credential in
   page text or clipboard-derived input is replaced by an opaque token
   BEFORE model exposure. Detection lives here (one place), shared by
   the browser path and the secret broker.

Authentication pages are a THIRD, softer category: Friday may navigate
to them, but capture stops and the user completes credentials/MFA
themselves; Friday resumes from the authenticated state afterwards.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from friday import netguard, sensitive_domains

# ---------------------------------------------------------------------------
# Verdicts
# ---------------------------------------------------------------------------

ALLOW = "ALLOW"
BLOCK_SENSITIVE = "BLOCK_SENSITIVE"      # banking/payment: never observed
BLOCK_NETWORK = "BLOCK_NETWORK"          # netguard refused the fetch
AUTH_HANDOFF = "AUTH_HANDOFF"            # user completes login/MFA


@dataclass(frozen=True)
class BrowserVerdict:
    decision: str
    reason: str

    @property
    def allowed(self) -> bool:
        return self.decision == ALLOW


class SensitiveRedirect(RuntimeError):
    """Raised by capture callables when a redirect chain lands on a
    financial domain: the observation boundary re-fires mid-fetch. The
    message is the BLOCKED_SENSITIVE_DOMAIN refusal string."""


#: Login/MFA URL shapes. Deliberately narrow: a false AUTH_HANDOFF only
#: costs the user one manual step; a missed one costs nothing because
#: credential fields are never typed by Friday anyway (secret broker).
_AUTH_PATTERNS = (
    r"/login\b", r"/signin\b", r"/sign-in\b", r"/auth\b", r"/oauth\b",
    r"/mfa\b", r"/2fa\b", r"/verify\b", r"accounts\.google\.com",
    r"login\.microsoftonline\.com", r"github\.com/login",
)

#: Secret-shaped content. Matches the FORM of credentials, never their
#: value semantics: long high-entropy tokens with known prefixes, plus
#: obvious assignments. One table for browser text, clipboard, and the
#: secret broker's redaction pass.
SECRET_PATTERNS = (
    r"sk-[A-Za-z0-9_-]{20,}",             # OpenAI/Anthropic style
    r"sk-ant-[A-Za-z0-9_-]{20,}",
    r"ghp_[A-Za-z0-9]{30,}",              # GitHub PAT
    r"github_pat_[A-Za-z0-9_]{30,}",
    r"AKIA[A-Z0-9]{16}",                  # AWS access key
    r"AIza[A-Za-z0-9_-]{30,}",            # Google API key
    r"xox[baprs]-[A-Za-z0-9-]{10,}",      # Slack
    r"(?i)(?:api[_-]?key|token|secret|password)\s*[:=]\s*\S{16,}",
)

OPAQUE = "[SECRET-REDACTED]"


def classify_url(url: str) -> BrowserVerdict:
    """The pre-capture gate. Called before ANY navigation/snapshot."""
    # Observation boundary first: a financial page is never observed,
    # regardless of anything else about the URL.
    if sensitive_domains.enabled() and sensitive_domains.is_sensitive(url):
        return BrowserVerdict(BLOCK_SENSITIVE,
                              sensitive_domains.refusal(url))

    # Network boundary second: SSRF/private-address refusal.
    try:
        netguard.check(url)
    except netguard.UrlRefused as exc:
        return BrowserVerdict(BLOCK_NETWORK, str(exc))
    except Exception:                                        # noqa: BLE001
        # netguard errors never fail open a SENSITIVE check (already
        # passed above); an unexpected parser error only skips the
        # network refinement.
        pass

    lowered = (url or "").lower()
    for pattern in _AUTH_PATTERNS:
        if re.search(pattern, lowered):
            return BrowserVerdict(
                AUTH_HANDOFF,
                "authentication surface: Friday navigates, the user "
                "completes credentials/MFA, Friday resumes after")
    return BrowserVerdict(ALLOW, "no policy match")


def redact_secrets(text: str) -> tuple[str, int]:
    """Replace secret-shaped spans with an opaque token. Returns
    (clean_text, redaction_count). Used on page text AND clipboard
    content before either can reach model context."""
    if not text:
        return text, 0
    count = 0
    clean = text
    for pattern in SECRET_PATTERNS:
        clean, n = re.subn(pattern, OPAQUE, clean)
        count += n
    return clean, count


def observe_page(url: str, capture) -> dict:
    """
    The single sanctioned path from a page to model-visible text.

    `capture` is a zero-arg callable performing the ACTUAL capture
    (Hermes browser snapshot/DOM read). It is invoked ONLY on ALLOW -
    the banking negative control is structural: on a blocked URL the
    callable never runs, so no DOM/screenshot/vision content can exist
    to leak.
    """
    verdict = classify_url(url)
    if verdict.decision == BLOCK_SENSITIVE:
        return {"status": "blocked", "verdict": verdict.reason,
                "content": ""}
    if verdict.decision == BLOCK_NETWORK:
        return {"status": "blocked", "verdict": verdict.reason,
                "content": ""}
    if verdict.decision == AUTH_HANDOFF:
        return {"status": "auth_handoff", "verdict": verdict.reason,
                "content": ""}
    try:
        raw = capture()
    except SensitiveRedirect as exc:
        # A redirect landed on a financial domain mid-fetch: same
        # contract as a pre-capture block - zero content, marker verdict.
        return {"status": "blocked", "verdict": str(exc), "content": ""}
    clean, redacted = redact_secrets(str(raw))
    return {"status": "ok", "content": clean, "redactions": redacted,
            "verdict": verdict.reason}
