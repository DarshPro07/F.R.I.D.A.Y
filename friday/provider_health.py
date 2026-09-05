"""
Provider health from evidence (PRD Requirement 9 / audit A-008, A-024).

"Usable" from Hermes means authenticated: a credential exists. The live
suite (2026-09-05) showed how little that says - seven providers were
"usable", three actually answered, one answered with nothing. Requirement 9
is explicit: a route is only labelled usable after it has passed a current
probe for the intended API mode, an empty/unsupported/404/auth failure marks
it degraded or unavailable WITH the reason, and stale health is revalidated
rather than trusted.

This module derives that verdict from the one place the truth is already
written: `GatewayTelemetry`, the ledger every real call through
`ModelGateway.infer` lands in. Nothing here calls a provider. `probe()` is
the deliberate exception - it makes one tiny attributed call so the verdict
can be refreshed - and it is only reached from an explicit request (the
live suite, a `model_providers(probe=True)` call, an operator), never from
routing.

    UNPROBED     no evidence at all: authenticated, never seen to answer.
                 Routing may TRY it (it is a candidate), the label is not
                 "healthy".
    HEALTHY      the most recent evidence is a successful answer with
                 visible content, inside `max_age_s`.
    DEGRADED     the most recent evidence is a transient failure (rate
                 limit, overload, gateway hiccup, truncation) - the route
                 can be retried, with the reason attached.
    UNAVAILABLE  the most recent evidence is a durable failure: auth,
                 credits, unsupported/missing model, no route. It stays
                 unavailable until a fresh probe says otherwise.
    STALE        there is evidence, but older than `max_age_s`: revalidate
                 before relying on it (Requirement 9, third clause).

Evidence never expires into "healthy"; it expires into STALE.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

UNPROBED = "UNPROBED"
HEALTHY = "HEALTHY"
DEGRADED = "DEGRADED"
UNAVAILABLE = "UNAVAILABLE"
STALE = "STALE"

#: How long a piece of evidence stays current. Provider state changes on
#: the order of hours (quota resets, outages), not seconds; a day is long
#: enough that the live suite's morning run covers the day and short enough
#: that yesterday's outage is not today's routing fact.
DEFAULT_MAX_AGE_S = 24 * 3600.0

#: Failure classes that say "this route will not work until something
#: changes": credentials, money, the model itself, the route itself.
DURABLE_FAILURES = frozenset({
    "AUTH_FAILED", "INSUFFICIENT_CREDIT", "SUBSCRIPTION_REQUIRED",
    "MODEL_UNAVAILABLE", "NOT_CONFIGURED", "PROVIDER_DISABLED", "NO_ROUTE",
    "BAD_REQUEST", "EMPTY_RESPONSE",
})

#: Failure classes that say "not right now": retryable with the reason.
TRANSIENT_FAILURES = frozenset({
    "RATE_LIMITED", "QUOTA_EXCEEDED", "OUTPUT_TRUNCATED",
    "GATEWAY_UNAVAILABLE", "PROVIDER_ERROR",
})


@dataclass
class Verdict:
    provider: str
    state: str
    reason: str = ""
    model: str = ""
    observed_at: str = ""            # ISO, from the ledger row
    age_s: float = -1.0
    code: str = ""                   # entitlement_state of the deciding row
    evidence: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"provider": self.provider, "state": self.state, "reason": self.reason,
                "model": self.model, "observed_at": self.observed_at,
                "age_s": round(self.age_s, 1), "code": self.code}


def _parse(ts: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def latest_by_provider(rows: list[dict]) -> dict[str, dict]:
    """The most recent ledger row per provider that actually reached a
    provider. Refusals (`status=refused`: budget/growth guard) never touched
    a route and say nothing about its health; rows with no provider are the
    gateway's own bookkeeping (NO_ROUTE) and are kept only as NO_ROUTE."""
    latest: dict[str, dict] = {}
    for row in rows:
        provider = str(row.get("provider") or "")
        if not provider or row.get("status") == "refused":
            continue
        current = latest.get(provider)
        if current is None or int(row.get("id") or 0) > int(current.get("id") or 0):
            latest[provider] = row
    return latest


def verdict_for(provider: str, row: dict | None, *, now: float | None = None,
                max_age_s: float = DEFAULT_MAX_AGE_S) -> Verdict:
    """One provider's state from its most recent evidence row (or none)."""
    if row is None:
        return Verdict(provider, UNPROBED,
                       reason="authenticated, never observed answering; probe before relying on it")
    now = time.time() if now is None else now
    observed = _parse(row.get("created_at") or "")
    age = (now - observed.timestamp()) if observed else float("inf")
    model = str(row.get("model") or "")
    code = str(row.get("entitlement_state") or "")
    stamp = str(row.get("created_at") or "")
    base = dict(model=model, observed_at=stamp, age_s=age, code=code, evidence=dict(row))
    if age > max_age_s:
        return Verdict(provider, STALE,
                       reason=f"last evidence {age / 3600:.1f}h old ({row.get('status')} {code}); revalidate",
                       **base)
    if row.get("status") == "ok":
        return Verdict(provider, HEALTHY,
                       reason=f"answered with content ({int(row.get('output_tokens') or 0)} output tokens)",
                       **base)
    error = str(row.get("error") or "")[:200]
    if code in DURABLE_FAILURES:
        return Verdict(provider, UNAVAILABLE, reason=f"{code}: {error}", **base)
    if code in TRANSIENT_FAILURES:
        return Verdict(provider, DEGRADED, reason=f"{code}: {error}", **base)
    # An unknown failure class is still a failure. Not knowing WHY is not
    # a reason to call it healthy; it is a reason to call it degraded and
    # keep the text.
    return Verdict(provider, DEGRADED, reason=f"{code or 'UNCLASSIFIED'}: {error}", **base)


def assess(telemetry, providers: list[str], *, now: float | None = None,
           max_age_s: float = DEFAULT_MAX_AGE_S, limit: int = 2000) -> dict[str, Verdict]:
    """Every named provider's verdict from the ledger's recent rows."""
    try:
        rows = telemetry.recent(limit=limit)
    except Exception as exc:  # noqa: BLE001 - a missing ledger is "unprobed", never "healthy"
        return {p: Verdict(p, UNPROBED, reason=f"telemetry unreadable: {exc}") for p in providers}
    latest = latest_by_provider(rows)
    return {p: verdict_for(p, latest.get(p), now=now, max_age_s=max_age_s) for p in providers}


def routable(verdicts: dict[str, Verdict]) -> list[str]:
    """Providers routing may try: healthy, degraded (retryable), unprobed
    (must be tried to become anything else) and stale (revalidation IS a
    try). Only UNAVAILABLE is excluded - it will not work until something
    outside Friday changes."""
    return [p for p, v in verdicts.items() if v.state != UNAVAILABLE]


PROBE_PROMPT = "Reply with exactly the single word: pong"


def probe(gateway, provider: str, *, objective_id: str = "", timeout_s: float = 90.0,
          worker: str = "health-probe") -> Verdict:
    """One tiny attributed call so this provider has CURRENT evidence.

    Costs a few tokens on paid routes, which is why nothing calls it
    implicitly. The verdict is read back from the ledger row the call
    wrote, so probe() cannot report anything the ledger does not say.
    """
    from friday.model_gateway import ModelGatewayRequest, compile_context
    request = ModelGatewayRequest(
        objective_id=objective_id or f"health-probe-{provider}", task_class="TRIVIAL",
        context_package=compile_context(user=PROBE_PROMPT),
        provider_allowlist=(provider,), allow_failover=False,
        timeout_s=timeout_s, worker=worker, max_output_tokens=16)
    gateway.infer(request)
    rows = gateway.telemetry.for_objective(request.objective_id)
    mine = [r for r in rows if str(r.get("provider") or "") == provider]
    if not mine:
        # NO_ROUTE writes a row with no provider: durable, and said so.
        last = rows[-1] if rows else None
        code = str((last or {}).get("entitlement_state") or "NO_ROUTE")
        return Verdict(provider, UNAVAILABLE, reason=f"{code}: {(last or {}).get('error') or 'no eligible route'}",
                       code=code, observed_at=str((last or {}).get("created_at") or ""), age_s=0.0)
    return verdict_for(provider, mine[-1])
