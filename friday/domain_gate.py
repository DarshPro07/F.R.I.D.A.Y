"""
Provider-safeguard domain gate for work packets (DOMAIN-001, Amendment A5).

Work is classified before it is delegated. A packet in a restricted domain is
never sent down the general build's route: it is answered BLOCKED_EXTERNAL
(it belongs to its own bounded program) and a safeguard event is recorded.

A provider safety check that keeps failing is a route-health signal, not
something to retry: after `MAX_SAFEGUARD_FAILURES` on one route the gate
refuses that route and says to switch route or start a fresh session.

Deterministic on purpose: a fixed phrase list, no model call. The list is an
assumption (offensive cyber, bio/chem weapons, CBRN) pending the owner's
ruling; it is one tuple per domain so the ruling is a one-line edit. Phrases
are deliberately specific so ordinary security *defence* work still passes.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

BLOCKED_EXTERNAL = "BLOCKED_EXTERNAL"

#: Failures one route may take before it is refused (bounded retry).
MAX_SAFEGUARD_FAILURES = 2

RESTRICTED_DOMAINS: dict[str, tuple[str, ...]] = {
    "cbrn": (
        "nerve agent", "chemical weapon", "biological weapon", "bioweapon",
        "nuclear weapon", "radiological weapon", "dirty bomb",
        "weaponize anthrax", "weaponise anthrax", "sarin synthesis",
        "enhance pathogen transmissibility", "gain-of-function",
    ),
    "offensive_cyber": (
        "write ransomware", "build ransomware", "deploy ransomware",
        "develop malware", "write a keylogger", "botnet command and control",
        "weaponized exploit", "weaponised exploit", "exploit for unpatched",
        "zero-day exploit chain", "credential stuffing campaign",
    ),
}


@dataclass(frozen=True)
class DomainVerdict:
    restricted: bool
    domain: str = ""
    matched: str = ""


GENERAL = DomainVerdict(False)


def classify_text(text: str) -> DomainVerdict:
    low = " ".join(text.lower().split())
    for domain, phrases in RESTRICTED_DOMAINS.items():
        for phrase in phrases:
            if phrase in low:
                return DomainVerdict(True, domain, phrase)
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

    def count(self, route: str, kind: str | None = None) -> int:
        sql = "SELECT COUNT(*) FROM safeguard_events WHERE route=?"
        args: list = [route]
        if kind:
            sql += " AND kind=?"
            args.append(kind)
        with self._lock, self._connect() as db:
            return db.execute(sql, args).fetchone()[0]

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
    #: "program" (own bounded program), "switch_route" or "fresh_session".
    next_step: str = ""

    def as_result(self) -> dict:
        return {"status": self.status, "blocked": True, "domain": self.domain,
                "reason": self.reason, "next_step": self.next_step}


ALLOW = GateDecision(True)


def check(bundle, route: str, *, led: SafeguardLedger | None = None) -> GateDecision:
    """Decide whether this packet may go down this route. Never raises."""
    led = led or ledger()
    verdict = classify(bundle)
    digest = _digest(bundle)
    if verdict.restricted:
        repeat = led.seen(route, digest)
        led.record(route, "restricted_packet", domain=verdict.domain,
                   packet=digest, detail=verdict.matched)
        why = (f"restricted domain '{verdict.domain}': belongs to its own bounded "
               "program, not the general build")
        if repeat:
            why += "; already refused on this route, not retried"
        return GateDecision(False, BLOCKED_EXTERNAL, verdict.domain, why, "program")
    failures = led.count(route, "provider_refusal")
    if failures >= MAX_SAFEGUARD_FAILURES:
        return GateDecision(
            False, BLOCKED_EXTERNAL, "route_health",
            f"{failures} provider safety refusals on {route}: route unhealthy, "
            "state persisted; switch route or start a fresh session",
            "switch_route")
    return ALLOW


def record_provider_refusal(route: str, bundle=None, *, detail: str = "",
                            led: SafeguardLedger | None = None) -> GateDecision:
    """
    Count a provider safety failure against a route. Returns what to do next:
    allowed (retry once more) while under the bound, else switch_route.
    """
    led = led or ledger()
    led.record(route, "provider_refusal",
               packet=_digest(bundle) if bundle is not None else "", detail=detail)
    failures = led.count(route, "provider_refusal")
    if failures >= MAX_SAFEGUARD_FAILURES:
        return GateDecision(
            False, BLOCKED_EXTERNAL, "route_health",
            f"{failures} provider safety refusals on {route}; retry bound reached",
            "switch_route")
    return ALLOW


def route_name(*parts: str) -> str:
    return ":".join(p for p in parts if p) or "default"
