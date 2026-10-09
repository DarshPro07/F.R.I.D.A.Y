"""
Garbage collection for the skill catalog (SKILL-007).

A skill that nobody has used or touched for `STALE_DAYS` and that duplicates
a better one (same overlap measure the curator merges on) is archived: moved
to DEPRECATED, "superseded, kept for history", with the winner named in the
history row. Nothing is deleted. A fresh near-duplicate is kept - it may be
a deliberate variant - and so is an idle skill that is the only one of its
kind. DISABLED and QUARANTINED belong to the owner and the safety gate, not
to this pass.

"Last activity" is the registry's `updated_at`, which every recorded use,
transition and edit refreshes.
"""

from __future__ import annotations

import calendar
import time

from friday.skills import contracts as K
from friday.skills.curator import MERGE_THRESHOLD, _fingerprint, similarity

#: No use or change for this long counts as stale.
STALE_DAYS = 90

#: States a duplicate may be archived from (each has a legal move to DEPRECATED
#: except NEEDS_REVALIDATION, which is still compared but left for revalidation).
_POOL = (K.ACTIVE, K.NEEDS_REVALIDATION, K.DEGRADED, K.STALE, K.BROKEN)


def _epoch(iso: str) -> int:
    return calendar.timegm(time.strptime(iso, "%Y-%m-%dT%H:%M:%SZ"))


def _rank(record: K.SkillRecord) -> tuple:
    """Higher is the one to keep: selectable, then used, then good, then recent."""
    return (record.state in K.SELECTABLE, record.usage_count, record.quality_score, record.updated_at)


def collect(registry, *, now: str | None = None, apply: bool = True, actor: str = "gc") -> dict:
    """Archive stale duplicates. With apply=False report what would be archived."""
    at = _epoch(now or K.now_iso())
    pool = sorted((r for r in registry.list() if r.state in _POOL), key=lambda r: r.name)
    pool.sort(key=_rank, reverse=True)                      # best first; ties keep name order
    kept: list[K.SkillRecord] = []
    archived: list[dict] = []
    for record in pool:
        idle_days = (at - _epoch(record.updated_at)) // 86400
        twin = None
        if idle_days >= STALE_DAYS and K.DEPRECATED in K.TRANSITIONS[record.state]:
            twin = next((k for k in kept
                         if similarity(_fingerprint(k), _fingerprint(record)) >= MERGE_THRESHOLD), None)
        if twin is None:
            kept.append(record)
            continue
        if apply:
            registry.transition(record.name, K.DEPRECATED, actor=actor,
                                reason=f"stale {idle_days} days and duplicate of {twin.name}")
        archived.append({"name": record.name, "duplicate_of": twin.name, "idle_days": idle_days})
    return {"archived": archived, "applied": apply}
