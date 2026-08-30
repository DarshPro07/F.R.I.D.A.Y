"""
The user model, as capabilities rather than as an MCP adapter.

Nothing here is new behaviour. `friday/profile.py` already held the whole
domain - extraction, conflict detection, resolution - and
`friday/tools/profile_control.py` already called it. What was missing is that
the adapter shaped the results into plain dicts, so the only way to reach any
of this was to arrive over MCP. A durable objective could not learn a fact
about the boss, and nothing said so; the capability was registered and simply
did not resolve.

So this is transport extraction. The domain service is unchanged and still
lives in `friday/profile.py`; this layer supplies the run, the policy gate and
the evidence, and the adapter becomes a thin caller of it.

Writes are verified by reading back, like every other write in this project: a
`learn_from_turn` that returned outcomes is not evidence that anything reached
the database, in the same way that a `write()` that did not raise was not
evidence that bytes reached the disk.
"""

from __future__ import annotations

import os

from friday import contracts as c
from friday import profile as P
from friday.policy import PolicyEngine, default_engine
from friday.store import DEFAULT_DB, Store
from friday.toolsets.system import APPROVAL_PREFIX

EXECUTION_SCOPE = "local_machine"

_store: Store | None = None


def store() -> Store:
    global _store
    if _store is None:
        _store = Store(os.getenv("ADA_DB") or DEFAULT_DB)
    return _store


def reset_store(new: Store | None = None) -> None:
    global _store
    _store = new


def _gate(run: c.Run, tool_id: str, engine: PolicyEngine) -> c.ActionResult | None:
    verdict = engine.decide(tool_id)
    if verdict.allowed:
        return None
    return run.record(c.started(run.run_id, tool_id).finish(
        status=c.CANCELLED,
        error=f"{APPROVAL_PREFIX}: {verdict.reason} [{verdict.decision}]",
    ))


def _scoped(payload: dict) -> dict:
    return {"execution_scope": EXECUTION_SCOPE, **payload}


def profile_get(
    run: c.Run, *, engine: PolicyEngine = default_engine
) -> c.ActionResult:
    """Everything known about the boss, grouped, plus the short brief."""
    tool_id = "profile.get"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    grouped = P.profile(store())
    brief = P.brief(store())
    held = sum(len(v) for v in grouped.values() if isinstance(v, list))
    return run.record(c.succeeded(
        started,
        output=_scoped({"profile": grouped, "brief": brief}),
        verification=c.Verification(
            method="store_read",
            evidence=f"{held} belief(s) across {len(grouped)} dimension(s) "
                     f"read from {store().path}",
        ),
    ))


def profile_explain(
    run: c.Run, subject: str, *, engine: PolicyEngine = default_engine
) -> c.ActionResult:
    """Why Friday believes something: the words it came from, and what it replaced."""
    tool_id = "profile.explain"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    if not (subject or "").strip():
        return run.record(c.failed(started, "explain what? no subject given"))

    account = P.explain(store(), subject)
    return run.record(c.succeeded(
        started,
        output=_scoped(dict(account)),
        verification=c.Verification(
            method="store_read",
            evidence=f"provenance for {subject!r} read from {store().path}: "
                     f"{len(account.get('history') or ())} recorded change(s)",
        ),
    ))


def profile_learn_from_turn(
    run: c.Run, user_said: str, you_replied: str = "", *,
    engine: PolicyEngine = default_engine,
) -> c.ActionResult:
    """
    Learn durable facts from a turn, then read them back.

    A conflict is not a failure and not something to resolve here: it means
    the boss has said two things that cannot both be true, and the only
    correct next step is to ask which. `needs_user_decision` carries that up.
    """
    tool_id = "profile.learn"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    try:
        outcomes = P.learn_from_turn(store(), user_said,
                                     assistant_text=you_replied)
    except P.ExtractionError as exc:
        return run.record(c.failed(started, str(exc)))

    def of(action: str) -> list[dict]:
        return [o.as_dict() for o in outcomes if o.action == action]

    stored, conflicts = of("stored"), of("conflict")

    # Read back. The store is the thing that has to have changed, and the
    # outcome objects are only a report of what was attempted.
    subjects = [s.get("subject") for s in stored if s.get("subject")]
    persisted = [s for s in subjects if P.explain(store(), s).get("belief")]

    payload = _scoped({
        "learned": stored,
        "reinforced": of("reinforced"),
        "rejected": of("rejected"),
        "conflicts": conflicts,
        "needs_user_decision": bool(conflicts),
    })

    if subjects and len(persisted) != len(subjects):
        missing = sorted(set(subjects) - set(persisted))
        return run.record(c.partial(
            started,
            f"{len(missing)} learned fact(s) did not read back: {missing}",
            output=payload,
        ))

    return run.record(c.succeeded(
        started,
        output=payload,
        verification=c.Verification(
            method="store_readback",
            evidence=f"{len(persisted)} of {len(subjects)} new belief(s) "
                     f"read back from {store().path}; "
                     f"{len(conflicts)} conflict(s) awaiting the boss"
            if subjects else
            f"nothing durable in this turn; {len(conflicts)} conflict(s) open",
        ),
    ))


def profile_open_conflicts(
    run: c.Run, *, engine: PolicyEngine = default_engine
) -> c.ActionResult:
    """Contradictions waiting on the boss. Ask which is right; do not choose."""
    tool_id = "profile.get"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    pending = store().contradictions(resolution="pending")
    return run.record(c.succeeded(
        started,
        output=_scoped({"count": len(pending), "conflicts": pending}),
        verification=c.Verification(
            method="store_read",
            evidence=f"{len(pending)} unresolved contradiction(s) in "
                     f"{store().path}",
        ),
    ))


def profile_resolve_conflict(
    run: c.Run, conflict_id: int, keep: str, rationale: str = "user decided",
    *, engine: PolicyEngine = default_engine,
) -> c.ActionResult:
    """
    Settle a contradiction the boss has now answered.

    `keep` is "new" or "existing". Only reachable after they have actually
    said which - the whole point of raising a conflict is that Friday does not
    pick.
    """
    tool_id = "profile.resolve"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    try:
        outcome = P.resolve(store(), conflict_id, keep=keep, rationale=rationale)
    except (ValueError, KeyError) as exc:
        return run.record(c.failed(started, str(exc)))

    still_open = [row for row in store().contradictions(resolution="pending")
                  if row.get("id") == conflict_id]
    if still_open:
        return run.record(c.partial(
            started, f"conflict {conflict_id} is still pending after resolving",
            output=_scoped(dict(outcome))))

    return run.record(c.succeeded(
        started,
        output=_scoped(dict(outcome)),
        verification=c.Verification(
            method="store_readback",
            evidence=f"conflict {conflict_id} no longer listed as pending in "
                     f"{store().path}; kept the {keep} value",
        ),
    ))
