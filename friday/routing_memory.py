"""
friday/routing_memory.py -- what the router learned from being wrong.

The pieces for a self-improving router were already here and not joined up.
`shadow_predictions` has recorded, for months, which capability was predicted
against which one actually ran and whether the intent was right.
`routing_corrections` holds the times the boss said "no, I meant X". Both were
written and neither was ever read back: `capability_router.search()` scored
every request from static metadata alone, so the same sentence that was
mis-routed on Monday was mis-routed identically on Friday.

This is the read side. `prior(text)` returns capability -> score adjustment for
the shape of a request, built from what actually happened last time it was
asked. Two sources, weighted by how much they are worth:

  correction   the boss said the answer. Strongest signal there is, and the
               only one carrying intent rather than outcome.
  outcome      a settled shadow row. Weaker and noisier, so it needs to have
               happened more than once before it moves anything.

Deliberately a *prior*, not a rule. It nudges the existing score rather than
short-circuiting it, because a fingerprint is a coarse key and the request that
matches it today may genuinely want something else. Static metadata still
decides; this decides the close calls, which is where the mistakes were.
"""
from __future__ import annotations

import os
import time

#: How long a computed prior is reused. The table changes when the boss
#: corrects something, which is rare and never urgent-to-the-millisecond, and a
#: voice turn cannot afford a query per candidate capability.
TTL_SECONDS = float(os.getenv("FRIDAY_ROUTING_PRIOR_TTL", "60"))

#: A correction is worth more than a bare tool-name match (10) and less than a
#: confident intent-example hit (40): it should win a tie, not overrule the
#: request's own words.
CORRECTION_WEIGHT = 24
#: One good outcome proves little; three is a habit. Capped so a well-trodden
#: path cannot drown out a request that plainly says otherwise.
OUTCOME_WEIGHT = 4
OUTCOME_CAP = 12
#: Below this many observations an outcome is noise, not evidence.
MIN_OBSERVATIONS = 2

_CACHE: dict[str, tuple[float, dict]] = {}


def _store():
    from friday.toolsets.memory import store
    return store()


def fingerprint(text: str) -> str:
    """The request shape. Reuses shadow's, so both halves key alike."""
    from friday import shadow
    return shadow.fingerprint(text or "")


def _corrections(db, fp: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in db.routing_corrections(limit=500):
        if row.get("fingerprint") != fp:
            continue
        good = row.get("corrected_capability")
        bad = row.get("previous_capability")
        if good:
            out[good] = out.get(good, 0) + CORRECTION_WEIGHT
        if bad and bad != good:
            # Being told "not that one" is as useful as being told which one,
            # and it is the half a positive-only prior throws away.
            out[bad] = out.get(bad, 0) - CORRECTION_WEIGHT
    return out


def _outcomes(db, fp: str) -> dict[str, int]:
    """Settled shadow rows for this shape: right ones up, wrong ones down."""
    try:
        rows = db._conn.execute(
            "SELECT predicted_capability AS cap, intent_correct AS ok "
            "FROM shadow_predictions WHERE fingerprint=? AND settled_at IS NOT NULL "
            "AND predicted_capability IS NOT NULL LIMIT 200", (fp,))
        rows = [dict(r) for r in rows]
    except Exception:  # noqa: BLE001
        return {}
    tally: dict[str, list[int]] = {}
    for row in rows:
        seen = tally.setdefault(row["cap"], [0, 0])
        seen[0] += 1
        if row.get("ok"):
            seen[1] += 1
    out: dict[str, int] = {}
    for cap, (total, good) in tally.items():
        if total < MIN_OBSERVATIONS:
            continue
        # Net evidence, so five rights and five wrongs is worth nothing rather
        # than worth five.
        net = good - (total - good)
        if net:
            out[cap] = max(-OUTCOME_CAP, min(OUTCOME_CAP, net * OUTCOME_WEIGHT))
    return out


def prior(text: str) -> dict[str, int]:
    """capability -> score adjustment for this request. {} when nothing learned.

    Never raises: a router that cannot rank is worse than one that ranks
    without its memory.
    """
    fp = ""
    try:
        fp = fingerprint(text)
    except Exception:  # noqa: BLE001
        return {}
    if not fp:
        return {}
    hit = _CACHE.get(fp)
    if hit and (time.monotonic() - hit[0]) < TTL_SECONDS:
        return hit[1]
    try:
        db = _store()
        merged = _outcomes(db, fp)
        for cap, delta in _corrections(db, fp).items():
            merged[cap] = merged.get(cap, 0) + delta
    except Exception:  # noqa: BLE001
        merged = {}
    _CACHE[fp] = (time.monotonic(), merged)
    return merged


def forget() -> None:
    """Drop the cache. For tests, and for after a correction is recorded."""
    _CACHE.clear()


def explain(text: str) -> dict:
    """Why the router is leaning the way it is, for the doctor and the UI."""
    learned = prior(text)
    return {
        "fingerprint": fingerprint(text) if text else "",
        "adjustments": dict(sorted(learned.items(), key=lambda kv: -abs(kv[1]))),
        "learned": bool(learned),
    }
