"""
friday/fabric_memory.py -- which providers actually work, learned from use.

`call_with_fallback()` ordered candidates by declared cost and current health.
Observed success rate was not an input, so a provider that failed nine calls in
ten was chosen exactly as readily as one that always worked, and the fallback
chain re-learned that the hard way on every single request.

This is the fabric's half of the loop `friday/routing_memory.py` closed for
capability routing. Same shape, same weights, deliberately: keyed on
(provider, operation) rather than a request fingerprint, requiring more than
one observation before it moves anything, counting net evidence so five
successes and five failures is worth nothing rather than worth five, and capped
so a well-trodden provider cannot outrank a health check saying the better one
is up right now.

Applied as a tie-breaker AFTER cost and health, never before. A cheap healthy
provider should still win on the merits; this decides the close calls, which is
where the wrong provider was being picked.
"""
from __future__ import annotations

import os
import threading
import time

#: One success proves little; three is a habit.
MIN_OBSERVATIONS = 2
#: Per net observation, and the ceiling. Small on purpose - this ranks, it does
#: not veto. A provider the health probe says is READY must stay reachable
#: however badly it has behaved, because "it is up but it has been failing" is
#: a thing the caller needs to be able to try.
WEIGHT = 4
CAP = 12

#: Rolling window. A provider fixed last week should not be judged on last
#: month, and an upstream that rotted should not coast on old successes.
WINDOW_SECONDS = float(os.getenv("FRIDAY_FABRIC_MEMORY_WINDOW", "604800"))

#: How long the tally is reused before it is re-read from the store. Ranking
#: asks per candidate, so this must not be a query each time.
CACHE_TTL = 60.0

#: (provider_id, operation) -> [total, good], rebuilt from the store.
_TALLY: dict[tuple[str, str], list] = {}
_LOADED_AT = 0.0
_LOCK = threading.Lock()


def _store():
    from friday.toolsets.memory import store
    return store()


def _cutoff_iso() -> str:
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc)
            - timedelta(seconds=WINDOW_SECONDS)).isoformat(timespec="seconds")


def _load(force: bool = False) -> dict:
    """The tally, from disk, cached.

    Read from the store rather than kept only in memory: an in-process tally is
    relearned from zero on every restart, and Friday restarts most days. A
    prior that forgets overnight is not a prior.
    """
    global _LOADED_AT
    with _LOCK:
        fresh = (time.monotonic() - _LOADED_AT) < CACHE_TTL
        if _TALLY and fresh and not force:
            return dict(_TALLY)
    tally: dict[tuple[str, str], list] = {}
    try:
        for row in _store().fabric_outcomes(since_iso=_cutoff_iso()):
            key = (row["provider_id"], row["operation"] or "")
            seen = tally.setdefault(key, [0, 0])
            seen[0] += 1
            if row["ok"]:
                seen[1] += 1
    except Exception:  # noqa: BLE001 - a router that cannot rank still ranks
        pass
    with _LOCK:
        _TALLY.clear()
        _TALLY.update(tally)
        _LOADED_AT = time.monotonic()
        return dict(_TALLY)


def record(provider_id: str, operation: str, ok: bool) -> None:
    """Note one outcome, durably. Called by `fabric.call()`; never raises."""
    if not provider_id:
        return
    key = (provider_id, operation or "")
    try:
        _store().record_fabric_outcome(provider_id, operation or "", bool(ok))
    except Exception:  # noqa: BLE001
        # A store that will not write must not cost the call its result. The
        # in-memory update below still makes this session smarter.
        pass
    with _LOCK:
        seen = _TALLY.setdefault(key, [0, 0])
        seen[0] += 1
        if ok:
            seen[1] += 1


def score(provider_id: str, operation: str = "") -> int:
    """Ranking adjustment for this provider/operation. 0 when nothing learned."""
    tally = _load()
    total, good = tally.get((provider_id, operation or ""), (0, 0))
    if not total and operation:
        # Fall back to the provider's overall record: a provider failing every
        # operation is worth demoting for a new one too.
        for (pid, _op), (count, ok_count) in tally.items():
            if pid == provider_id:
                total += count
                good += ok_count
    if total < MIN_OBSERVATIONS:
        return 0
    net = good - (total - good)
    if not net:
        return 0
    return max(-CAP, min(CAP, net * WEIGHT))


def rank(providers, operation: str = ""):
    """Stable re-sort of an already-ordered candidate tuple.

    `candidates()` has already applied cost, risk and model_required, and that
    ordering is the primary rule. Python's sort is stable, so sorting only by
    the learned score preserves every one of those decisions among providers
    that have learned nothing about each other.

    A declared fallback chain is left alone entirely. `fallbacks` is an
    ordering the provider's author chose - "if I cannot answer, try this one
    next" - and a learned prior quietly inverting it is overreach, not
    improvement. It also broke a real test: the backup provider was promoted
    ahead of the primary and ran for a request the primary would have served.
    So when any provider in the pool names another in the pool as its
    fallback, the author's order stands.
    """
    pool = tuple(providers)
    ids = {p.id for p in pool}
    for provider in pool:
        if ids & set(getattr(provider, "fallbacks", ()) or ()):
            return pool
    return tuple(sorted(pool, key=lambda p: -score(p.id, operation)))


def report() -> list[dict]:
    """What has been learned, for the doctor and the UI."""
    out = []
    for (provider_id, operation), (total, good) in _load().items():
        out.append({
            "provider": provider_id, "operation": operation,
            "calls": total, "succeeded": good, "failed": total - good,
            "adjustment": score(provider_id, operation),
        })
    return sorted(out, key=lambda row: (row["provider"], row["operation"]))


def prune() -> int:
    """Drop outcomes past the window. Rows are one per call, so unbounded
    without this; called by the doctor rather than on every write."""
    try:
        removed = _store().prune_fabric_outcomes(_cutoff_iso())
    except Exception:  # noqa: BLE001
        return 0
    _load(force=True)
    return removed


def forget() -> None:
    """Drop the cached tally so the next read comes from disk.

    Does NOT delete history: a test wanting a clean slate points ADA_DB at a
    temp file, the same way every other store-backed test does. A `forget()`
    that silently wiped what the fabric learned would be a very expensive
    convenience.
    """
    global _LOADED_AT
    with _LOCK:
        _TALLY.clear()
        _LOADED_AT = 0.0
