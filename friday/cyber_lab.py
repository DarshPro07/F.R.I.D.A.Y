"""Cyber Lab boundaries (CYBER-002): provider interface, sandbox tiers, evidence.

Generic only. No scanner or exploit code lives here; a provider is a thing that
can be asked to act on a host, and every request passes `trust.target_guard`
(the SecurityAuthorization contract) before the provider is reached. The
sandbox tier a provider declares must fit within what the contract's intensity
ceiling permits, so a provider cannot be more invasive than the person agreed.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Protocol, runtime_checkable

from friday import trust as T

# Sandbox tiers, least to most invasive. Each maps to the highest scan
# intensity that tier may be asked to perform.
TIER_OFFLINE = "offline"          # no network at all; fixtures and analysis
TIER_SCOPED_NETWORK = "scoped_network"   # only hosts the contract covers
TIER_ACTIVE = "active"            # active probing of covered hosts
SANDBOX_TIERS = (TIER_OFFLINE, TIER_SCOPED_NETWORK, TIER_ACTIVE)

TIER_MAX_INTENSITY = {
    TIER_OFFLINE: T.PASSIVE,
    TIER_SCOPED_NETWORK: T.LOW_ACTIVE,
    TIER_ACTIVE: T.EXPLOIT,
}
_ORDER = {T.PASSIVE: 0, T.LOW_ACTIVE: 1, T.HIGH_VOLUME: 2, T.EXPLOIT: 3}


@dataclass(frozen=True)
class LabRequest:
    host: str
    action: str
    intensity: str = T.LOW_ACTIVE


@dataclass(frozen=True)
class LabEvidence:
    """One record per request, allowed or refused. `digest` binds the fields."""

    provider: str
    tier: str
    host: str
    action: str
    intensity: str
    allowed: bool
    reason: str
    approval_id: str
    at: str
    output: dict = field(default_factory=dict)
    digest: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _digest(rec: dict) -> str:
    body = {k: v for k, v in rec.items() if k != "digest"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


@runtime_checkable
class LabProvider(Protocol):
    name: str
    tier: str

    def run(self, request: LabRequest) -> dict: ...


class MockProvider:
    """Deterministic provider: records calls, touches no network."""

    def __init__(self, name: str = "mock", tier: str = TIER_SCOPED_NETWORK) -> None:
        self.name = name
        self.tier = tier
        self.calls: list[LabRequest] = []

    def run(self, request: LabRequest) -> dict:
        self.calls.append(request)
        return {"host": request.host, "action": request.action, "findings": []}


_REGISTRY: dict[str, LabProvider] = {}


def register(provider: LabProvider) -> None:
    if provider.tier not in SANDBOX_TIERS:
        raise ValueError(f"unknown sandbox tier {provider.tier!r}; one of {SANDBOX_TIERS}")
    if provider.name in _REGISTRY:
        raise ValueError(f"lab provider {provider.name!r} is already registered")
    _REGISTRY[provider.name] = provider


def unregister(name: str) -> None:
    _REGISTRY.pop(name, None)


def get(name: str) -> LabProvider | None:
    return _REGISTRY.get(name)


def execute(provider: LabProvider, auth: T.SecurityAuthorization | None,
            request: LabRequest, *, now: datetime | None = None) -> LabEvidence:
    """Gate, then run. A refusal never reaches the provider."""
    verdict = T.target_guard(auth, host=request.host, action=request.action,
                             intensity=request.intensity, now=now)
    allowed, reason = verdict["allowed"], verdict["reason"]
    if allowed and _ORDER.get(request.intensity, 99) > _ORDER[TIER_MAX_INTENSITY[provider.tier]]:
        allowed = False
        reason = (f"intensity {request.intensity} exceeds what sandbox tier "
                  f"{provider.tier} permits")
    output = provider.run(request) if allowed else {}
    rec = LabEvidence(
        provider=provider.name, tier=provider.tier, host=request.host,
        action=request.action, intensity=request.intensity, allowed=allowed,
        reason=reason, approval_id=auth.approval_id if auth else "",
        at=(now or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
        output=output,
    ).to_dict()
    rec["digest"] = _digest(rec)
    return LabEvidence(**rec)


def verify_evidence(rec: LabEvidence) -> bool:
    return rec.digest == _digest(rec.to_dict())
