"""
Onboarding trigger for newly seen gateway routes (MODEL-004).

`friday.model_profiling.profile_route` measures a route and
`friday.gateway_completer.gateway_completer` lets it reach a real one; this is
the step that decides *which* routes to measure. Given the (provider, model)
pairs a caller has seen, it probes only those with neither a declaration nor a
stored profile, so a declaration (the owner's word) is never contradicted and
a profiled route is never paid for twice.

A route that was unreachable or unusable stores nothing, so the next pass
retries it. With `max_age_s`, a stored profile older than that is measured
again; the new profile replaces it, and a failed refresh keeps the earlier
one. Without it a profile is never refreshed. Gateway providers are the capability registry's backends
(`model_capabilities.get(provider, model)`), so provider is passed as backend.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Iterable

from friday import gateway_completer as GC
from friday import model_capabilities as MC
from friday import model_profiling as MP

DECLARED = "declared"
ALREADY_PROFILED = "already_profiled"
PROFILED = "profiled"
UNREACHABLE = "unreachable"
UNUSABLE = "unusable"


@dataclass(frozen=True)
class Onboarding:
    provider: str
    model: str
    status: str
    profile: MP.RouteProfile | None = None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def onboard_route(gw, provider: str, model: str, *, timeout_s: float = 30.0,
                  max_age_s: float | None = None,
                  now: Callable[[], datetime] = _utcnow) -> Onboarding:
    """Profile one route through `gw` unless it is declared or has a profile
    no older than `max_age_s` (any stored profile when `max_age_s` is None)."""
    if MC.is_declared(provider, model):
        return Onboarding(provider, model, DECLARED)
    stored = MC.profiled(provider, model)
    if stored is not None and (
            max_age_s is None
            or (now() - datetime.fromisoformat(stored.profiled_at)).total_seconds() <= max_age_s):
        return Onboarding(provider, model, ALREADY_PROFILED)
    profile = MP.profile_route(
        provider, model, GC.gateway_completer(gw, provider, model, timeout_s=timeout_s))
    return Onboarding(provider, model, profile.status, profile)


def onboard_new_routes(gw, routes: Iterable[tuple[str, str]], *,
                       timeout_s: float = 30.0, max_age_s: float | None = None,
                       now: Callable[[], datetime] = _utcnow) -> list[Onboarding]:
    """`onboard_route` for each distinct (provider, model), in first-seen order."""
    seen: dict[tuple[str, str], Onboarding] = {}
    for provider, model in routes:
        if (provider, model) not in seen:
            seen[(provider, model)] = onboard_route(gw, provider, model, timeout_s=timeout_s,
                                                    max_age_s=max_age_s, now=now)
    return list(seen.values())
