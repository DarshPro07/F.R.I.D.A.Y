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


def names() -> tuple[str, ...]:
    return tuple(sorted(_REGISTRY))


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


def record_offline(provider: str, host: str, action: str, output: dict, *,
                   intensity: str = T.PASSIVE, allowed: bool = True, reason: str = "",
                   now: datetime | None = None) -> LabEvidence:
    """Evidence for work done at the offline tier: bytes already on this machine.

    `execute` gates a request against a `SecurityAuthorization` because a
    target host has to be in someone's scope. An offline run has no target and
    no network, so there is no scope to check; what the tier still fixes is the
    intensity, and anything above passive is recorded as refused. The record is
    the same hash-bound `LabEvidence`, so `verify_evidence` accepts it. `host`
    is the subject (a file path), not an address.
    """
    permitted = _ORDER.get(intensity, 99) <= _ORDER[TIER_MAX_INTENSITY[TIER_OFFLINE]]
    if allowed and not permitted:
        allowed = False
        reason = f"intensity {intensity} exceeds what sandbox tier {TIER_OFFLINE} permits"
    rec = LabEvidence(
        provider=provider, tier=TIER_OFFLINE, host=host, action=action,
        intensity=intensity, allowed=allowed,
        reason=reason or "offline analysis of a local file: no target, no network",
        approval_id="", at=(now or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
        output=output if allowed else {},
    ).to_dict()
    rec["digest"] = _digest(rec)
    return LabEvidence(**rec)

def record_refusal(provider: str, tier: str, host: str, action: str, reason: str, *,
                   intensity: str = T.PASSIVE, approval_id: str = "",
                   now: datetime | None = None) -> LabEvidence:
    """Hash-bound evidence that a request was refused before any provider ran.

    For refusals the contract gate does not make itself (BIN-004: a provider
    tier a sample may not use, or a request to run on the host). `tier` is the
    provider's declared tier, or "" when there is no provider.
    """
    rec = LabEvidence(
        provider=provider, tier=tier, host=host, action=action, intensity=intensity,
        allowed=False, reason=reason, approval_id=approval_id,
        at=(now or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
    ).to_dict()
    rec["digest"] = _digest(rec)
    return LabEvidence(**rec)


def record_withheld(evidence: LabEvidence, reason: str) -> LabEvidence:
    """The same run's evidence with its output replaced by a marker, re-bound.

    For a run that happened but whose output must not be released (BIN-004: it
    touched a canary). Provider, tier, host, intensity, `allowed` and approval
    stay as the run recorded them, so the record still says the lab ran.
    """
    rec = evidence.to_dict()
    rec["output"] = {"withheld": reason}
    rec["digest"] = _digest(rec)
    return LabEvidence(**rec)


def verify_evidence(rec: LabEvidence) -> bool:
    return rec.digest == _digest(rec.to_dict())


def run_routed(decision, provider_name: str, auth: T.SecurityAuthorization | None,
               request: LabRequest, *, now: datetime | None = None) -> LabEvidence:
    """Consume a `domain_gate.GateDecision`: only a `cyber_lab` route may reach a provider.

    The gate label says the request is authorized testing; the contract still
    decides, via `execute`. Any other route, or an unregistered provider, is a
    refused evidence record rather than an exception, so callers keep one shape.
    """
    provider = get(provider_name)
    step = getattr(decision, "next_step", "")
    if step == "cyber_lab" and provider is not None:
        return execute(provider, auth, request, now=now)
    reason = (f"route {step or 'none'!r} is not the cyber_lab route"
              if step != "cyber_lab" else f"no lab provider named {provider_name!r}")
    rec = LabEvidence(
        provider=provider_name, tier=getattr(provider, "tier", ""), host=request.host,
        action=request.action, intensity=request.intensity, allowed=False,
        reason=reason, approval_id=auth.approval_id if auth else "",
        at=(now or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
    ).to_dict()
    rec["digest"] = _digest(rec)
    return LabEvidence(**rec)
