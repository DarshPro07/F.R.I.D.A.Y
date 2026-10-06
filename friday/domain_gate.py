"""
Provider-safeguard domain gate for work packets (DOMAIN-001, Amendment A5).

Work is classified before it is delegated. A packet in a restricted domain is
never sent down the general build's route: it is answered BLOCKED_EXTERNAL
(it belongs to its own bounded program) and a safeguard event is recorded.

A provider safety check that keeps failing is a route-health signal, not
something to retry: after `MAX_SAFEGUARD_FAILURES` on one route the gate
refuses that route and says to switch route or start a fresh session.

Deterministic on purpose: the final routing decision is fixed policy over
phrase signals, never model prose. Owner ruling 2026-10-02: the top-level
domains are `cbrne` (`cbrn` is kept as an alias) and `cyber_sensitive`.
Cyber gets a second classification of intent and authority (defensive,
authorized_testing, malicious_or_abusive, unknown); a tool name such as
"nmap" or a phrase such as "offensive security" is only a signal, weighed
with the task intent, target ownership/authorization and requested action.
An optional injectable semantic classifier catches what the phrases miss; its
label is one more signal, and the routing is still decided here.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

BLOCKED_EXTERNAL = "BLOCKED_EXTERNAL"
NEEDS_SCOPE = "NEEDS_SCOPE"

#: Failures one route may take before it is refused (bounded retry).
MAX_SAFEGUARD_FAILURES = 2

CBRNE = "cbrne"
CYBER = "cyber_sensitive"
_ALIASES = {"cbrn": CBRNE, "cbrne": CBRNE, "cyber_sensitive": CYBER}

DEFENSIVE = "defensive"
AUTHORIZED_TESTING = "authorized_testing"
MALICIOUS = "malicious_or_abusive"
UNKNOWN = "unknown"

# Phrases are deliberately specific so ordinary defence work still passes.
CBRNE_SIGNALS: tuple[str, ...] = (
    "nerve agent", "chemical weapon", "biological weapon", "bioweapon",
    "nuclear weapon", "radiological weapon", "dirty bomb", "anthrax",
    "sarin", "enhance pathogen transmissibility", "gain-of-function",
    "cbrn",
)
#: Asking for the capability (not about it): with a signal this is enablement.
CBRNE_ENABLEMENT: tuple[str, ...] = (
    "synthesi", "manufactur", "weaponi", "produce", "enhance", "acquire",
    "culture ", "scale up", "deliver", "step-by-step", "build a", "make a",
)
INFORMATIONAL: tuple[str, ...] = (
    "history of", "treaty", "treaties", "arms control", "conference",
    "notes", "course", "glossary", "overview of", "policy analysis",
)

CYBER_SIGNALS: tuple[str, ...] = (
    "kali", "nmap", "metasploit", "pentest", "penetration test",
    "offensive security", "red team", "exploit", "sql injection", "xss",
    "ransomware", "malware", "keylogger", "botnet", "break into", "hack into",
    "credential stuffing", "brute force", "vulnerability scan",
)
CYBER_ABUSE: tuple[str, ...] = (
    "ransomware", "keylogger", "botnet", "break into", "hack into",
    "credential stuffing", "steal credentials", "without permission",
    "without authorization", "without authorisation", "third-party server",
    "someone else's", "their server", "develop malware", "weaponized exploit",
    "weaponised exploit", "exploit for unpatched", "zero-day exploit chain",
)
CYBER_TEST_ACTIONS: tuple[str, ...] = (
    "pentest", "penetration test", "red team", "nmap", "kali", "metasploit",
    "vulnerability scan", "exploit", "brute force",
)
CYBER_OWNERSHIP: tuple[str, ...] = (
    "my own", "our own", "we own", "i own", "my staging", "our staging",
)
CYBER_SCOPE: tuple[str, ...] = (
    "scope", "authorized targets", "authorised targets", "rules of engagement",
)
CYBER_DEFENSIVE: tuple[str, ...] = (
    "code review", "review", "harden", "fix", "patch", "mitigat", "sanitiz",
    "parameteriz", "parameteris", "detect", "defend", "regression test",
    "secure",
)


@dataclass(frozen=True)
class DomainVerdict:
    restricted: bool
    domain: str = ""
    matched: str = ""
    #: Cyber only: defensive | authorized_testing | malicious_or_abusive | unknown.
    intent: str = ""
    #: normal | restricted | cyber_lab | ask_scope
    route: str = "normal"


GENERAL = DomainVerdict(False)

#: packet text -> a domain name ("" when general). Injected, never a default.
SemanticClassifier = Callable[[str], str]
_semantic: SemanticClassifier | None = None


def set_semantic_classifier(fn: SemanticClassifier | None) -> None:
    global _semantic
    _semantic = fn


def _has(low: str, phrases: tuple[str, ...]) -> str:
    for phrase in phrases:
        if phrase in low:
            return phrase
    return ""


def _cbrne(low: str) -> DomainVerdict:
    signal = _has(low, CBRNE_SIGNALS)
    if not signal:
        return GENERAL
    if _has(low, INFORMATIONAL) and not _has(low, CBRNE_ENABLEMENT):
        return GENERAL
    return DomainVerdict(True, CBRNE, signal, route="restricted")


def cyber_intent(low: str) -> str:
    """The second classification: what is being asked, and with what authority."""
    if _has(low, CYBER_ABUSE):
        return MALICIOUS
    action = _has(low, CYBER_TEST_ACTIONS)
    if action and _has(low, CYBER_OWNERSHIP):
        return AUTHORIZED_TESTING if _has(low, CYBER_SCOPE) else UNKNOWN
    if _has(low, INFORMATIONAL) or _has(low, CYBER_DEFENSIVE):
        return DEFENSIVE
    return UNKNOWN


_CYBER_ROUTE = {DEFENSIVE: "normal", AUTHORIZED_TESTING: "cyber_lab",
                MALICIOUS: "restricted", UNKNOWN: "ask_scope"}


def _cyber(low: str, signal: str) -> DomainVerdict:
    intent = cyber_intent(low)
    route = _CYBER_ROUTE[intent]
    return DomainVerdict(route != "normal", CYBER, signal, intent, route)


def classify_text(text: str) -> DomainVerdict:
    low = " ".join(text.lower().split())
    verdict = _cbrne(low)
    if verdict.restricted:
        return verdict
    signal = _has(low, CYBER_SIGNALS)
    if signal:
        return _cyber(low, signal)
    if _semantic is not None:
        label = _ALIASES.get(str(_semantic(text) or "").strip().lower(), "")
        if label == CBRNE:
            return DomainVerdict(True, CBRNE, "semantic", route="restricted")
        if label == CYBER:
            return _cyber(low, "semantic")
    return GENERAL


_PACKET_FIELDS = ("goal", "user_outcome", "acceptance", "constraints",
                  "known_facts", "assumptions", "context", "role",
                  "verification")


def packet_text(bundle) -> str:
    """The prose of a work packet, whichever TaskBundle shape it is."""
    parts: list[str] = []
    for name in _PACKET_FIELDS:
        value = getattr(bundle, name, "")
        if isinstance(value, str):
            parts.append(value)
        elif value:
            parts.extend(str(v) for v in value)
    return "\n".join(p for p in parts if p)


def classify(bundle) -> DomainVerdict:
    return classify_text(packet_text(bundle))


def _digest(bundle) -> str:
    return hashlib.sha256(packet_text(bundle).encode("utf-8")).hexdigest()[:16]


def _default_path() -> Path:
    env = os.environ.get("FRIDAY_SAFEGUARD_DB")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent / "data" / "safeguard_events.sqlite3"


class SafeguardLedger:
    """Persisted safeguard events, so a refusal survives a restart."""

    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path else _default_path()
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS safeguard_events ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, "
                "route TEXT NOT NULL, kind TEXT NOT NULL, domain TEXT NOT NULL, "
                "packet TEXT NOT NULL, detail TEXT NOT NULL)")

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=10)

    def record(self, route: str, kind: str, *, domain: str = "",
               packet: str = "", detail: str = "") -> None:
        with self._lock, self._connect() as db:
            db.execute(
                "INSERT INTO safeguard_events (ts, route, kind, domain, packet, detail)"
                " VALUES (?,?,?,?,?,?)",
                (time.time(), route, kind, domain, packet, detail[:500]))

    def count(self, route: str, kind: str | None = None, *,
              domain: str | None = None) -> int:
        """Events on a route, optionally of one kind and/or one domain.

        `domain=None` does not filter; `domain=""` means events recorded with no
        domain. The (route, domain) form is the seam the model gateway reads to
        keep a route out of a task domain it has already refused (MODEL-006).
        """
        sql = "SELECT COUNT(*) FROM safeguard_events WHERE route=?"
        args: list = [route]
        if kind:
            sql += " AND kind=?"
            args.append(kind)
        if domain is not None:
            sql += " AND domain=?"
            args.append(domain)
        with self._lock, self._connect() as db:
            return db.execute(sql, args).fetchone()[0]

    def clear_refusals(self, route: str, *, domain: str | None = None) -> int:
        """Delete a route's provider-refusal events and return how many went.

        `domain=None` lifts every domain on the route. Only `provider_refusal`
        rows are removed, so the restricted-packet and scope audit trail stays.
        """
        sql = "DELETE FROM safeguard_events WHERE route=? AND kind='provider_refusal'"
        args: list = [route]
        if domain is not None:
            sql += " AND domain=?"
            args.append(domain)
        with self._lock, self._connect() as db:
            return db.execute(sql, args).rowcount

    def seen(self, route: str, packet: str) -> bool:
        with self._lock, self._connect() as db:
            return db.execute(
                "SELECT 1 FROM safeguard_events WHERE route=? AND packet=?"
                " AND kind='restricted_packet' LIMIT 1", (route, packet)
            ).fetchone() is not None


_ledger: SafeguardLedger | None = None


def ledger() -> SafeguardLedger:
    global _ledger
    if _ledger is None:
        _ledger = SafeguardLedger()
    return _ledger


@dataclass(frozen=True)
class GateDecision:
    allowed: bool
    status: str = ""
    domain: str = ""
    reason: str = ""
    #: "program", "deny", "cyber_lab", "ask_scope", "switch_route" or "fresh_session".
    next_step: str = ""
    intent: str = ""

    def as_result(self) -> dict:
        return {"status": self.status, "blocked": True, "domain": self.domain,
                "reason": self.reason, "next_step": self.next_step,
                "intent": self.intent}


ALLOW = GateDecision(True)


def check(bundle, route: str, *, led: SafeguardLedger | None = None) -> GateDecision:
    """Decide whether this packet may go down this route. Never raises."""
    led = led or ledger()
    verdict = classify(bundle)
    digest = _digest(bundle)
    if verdict.route == "restricted":
        repeat = led.seen(route, digest)
        led.record(route, "restricted_packet", domain=verdict.domain,
                   packet=digest, detail=verdict.intent or verdict.matched)
        why = (f"restricted domain '{verdict.domain}': belongs to its own bounded "
               "program, not the general build")
        if verdict.intent == MALICIOUS:
            why = "malicious or abusive cyber request: denied"
        if repeat:
            why += "; already refused on this route, not retried"
        return GateDecision(False, BLOCKED_EXTERNAL, verdict.domain, why,
                            "deny" if verdict.intent == MALICIOUS else "program",
                            verdict.intent)
    if verdict.route == "cyber_lab":
        led.record(route, "cyber_lab_routed", domain=verdict.domain,
                   packet=digest, detail=verdict.matched)
        return GateDecision(
            False, BLOCKED_EXTERNAL, verdict.domain,
            "authorized security testing: runs in the Cyber Lab under an "
            "authorization contract, not in the general build",
            "cyber_lab", verdict.intent)
    if verdict.route == "ask_scope":
        led.record(route, "scope_requested", domain=verdict.domain,
                   packet=digest, detail=verdict.matched)
        return GateDecision(
            False, NEEDS_SCOPE, verdict.domain,
            "security-sensitive request with no stated target ownership and "
            "scope: ask for scope before any work", "ask_scope", verdict.intent)
    failures = led.count(route, "provider_refusal")
    if failures >= MAX_SAFEGUARD_FAILURES:
        return GateDecision(
            False, BLOCKED_EXTERNAL, "route_health",
            f"{failures} provider safety refusals on {route}: route unhealthy, "
            "state persisted; switch route or start a fresh session",
            "switch_route")
    return ALLOW


def record_provider_refusal(route: str, bundle=None, *, detail: str = "",
                            led: SafeguardLedger | None = None,
                            domain: str = "") -> GateDecision:
    """
    Count a provider safety failure against a route. Returns what to do next:
    allowed (retry once more) while under the bound, else switch_route.

    `domain` names the task domain the refused request was in; with it the event
    also excludes the route for that domain (see `route_excluded`).
    """
    led = led or ledger()
    led.record(route, "provider_refusal", domain=domain,
               packet=_digest(bundle) if bundle is not None else "", detail=detail)
    failures = led.count(route, "provider_refusal")
    if failures >= MAX_SAFEGUARD_FAILURES:
        return GateDecision(
            False, BLOCKED_EXTERNAL, "route_health",
            f"{failures} provider safety refusals on {route}; retry bound reached",
            "switch_route")
    return ALLOW


def route_excluded(route: str, domain: str, *, led: SafeguardLedger | None = None) -> bool:
    """Whether a provider's safeguard has already refused this domain on this route.

    One recorded refusal is enough: the same route is not offered the same kind
    of request again, however long ago, until the ledger is cleared. A request
    with no domain (`""`) is never excluded.
    """
    if not domain:
        return False
    return (led or ledger()).count(route, "provider_refusal", domain=domain) > 0


def clear_route_exclusion(route: str, domain: str | None = None, *,
                          led: SafeguardLedger | None = None) -> int:
    """Lift a safeguard exclusion so the route is offered the domain again.

    The one way an exclusion ends (MODEL-006): it does not expire on its own. Pass
    the domain to lift one, or leave it out to lift every domain on the route. It
    also resets the route's refusal count, which `check` reads for route health.
    Returns how many refusal events were removed.
    """
    return (led or ledger()).clear_refusals(route, domain=domain)


def route_name(*parts: str) -> str:
    return ":".join(p for p in parts if p) or "default"


#: What a provider says when its own safety check stopped the run.
_REFUSAL_MARKERS = ("usage policy", "safety policy", "safety system",
                    "content policy", "violates our", "safeguard",
                    "cannot assist with that request", "stop_reason: refusal")


def is_provider_refusal(text: str) -> bool:
    low = (text or "").lower()
    return any(m in low for m in _REFUSAL_MARKERS)
