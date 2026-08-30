"""
Shared-brain capabilities in the ActionResult contract.

The executor-facing half of friday/brain.py: `brain_recall` inside a
durable objective runs THROUGH here (the MCP tools in
tools/brain_control.py are the conversational surface). Domain-prefix
rule binds brain_recall -> toolsets.brain.recall.
"""

from __future__ import annotations

from friday import contracts as c
from friday.policy import PolicyEngine, default_engine

_adapter = None


def adapter():
    from friday.brain import SharedBrainAdapter

    global _adapter
    if _adapter is None:
        _adapter = SharedBrainAdapter()
    return _adapter


def reset_adapter(new=None) -> None:
    global _adapter
    _adapter = new


def recall(run: c.Run, query: str = "", *, entity: str = "",
           budget: str = "bounded",
           engine: PolicyEngine = default_engine) -> c.ActionResult:
    """What the shared brain already knows - evidence, not synthesis."""
    started = c.started(run.run_id, "brain.recall")
    try:
        answer = adapter().recall(query, entity=entity, budget=budget)
    except Exception as exc:                                 # noqa: BLE001
        # The brain being down never blocks an objective (GJ6): this is
        # an OBSERVED miss, not a failure - the run continues on files.
        return run.record(started.finish(
            status=c.OBSERVED,
            output={"available": False,
                    "note": f"shared brain unreachable "
                            f"({type(exc).__name__}); continue without"},
        ))
    return run.record(started.finish(
        status=c.OBSERVED, output=answer.compact(),
        verification=c.Verification(
            method="brain_recall",
            evidence=f"{len(answer.facts)} facts, "
                     f"{len(answer.results)} snippets, "
                     f"budget_used={answer.budget_used}"),
    ))


def remember(run: c.Run, fact: str, *, provenance: str, entity: str = "",
             kind: str = "fact", ttl: str = "",
             engine: PolicyEngine = default_engine) -> c.ActionResult:
    """One durable fact, admission-filtered, provenance required."""
    from friday.brain import AdmissionRefused

    started = c.started(run.run_id, "brain.remember")
    try:
        out = adapter().remember(fact, provenance=provenance,
                                 entity=entity, kind=kind, ttl=ttl)
    except AdmissionRefused as refusal:
        return run.record(c.failed(started, str(refusal)))
    except ValueError as exc:
        return run.record(c.failed(started, str(exc)))
    except Exception as exc:                                 # noqa: BLE001
        return run.record(c.failed(
            started, f"shared brain unreachable: {type(exc).__name__} - "
                     f"the fact was NOT saved"))
    return run.record(c.succeeded(
        started,
        output={"status": out.get("status"), "fact_id": out.get("id")},
        side_effects=(f"brain fact {out.get('id')} written",),
        verification=c.Verification(
            method="brain_write",
            evidence=f"status={out.get('status')} id={out.get('id')} "
                     f"provenance={provenance[:80]!r}"),
    ))


def entity(run: c.Run, name: str, *,
           engine: PolicyEngine = default_engine) -> c.ActionResult:
    """One entity card, zero model calls."""
    started = c.started(run.run_id, "brain.entity")
    try:
        card = adapter().entity(name)
    except Exception as exc:                                 # noqa: BLE001
        return run.record(started.finish(
            status=c.OBSERVED, output={"available": False,
                             "note": f"unreachable: {type(exc).__name__}"}))
    return run.record(started.finish(status=c.OBSERVED, output=card))


def forget(run: c.Run, fact_id: str, *, reason: str = "",
           engine: PolicyEngine = default_engine) -> c.ActionResult:
    """Expire one fact by id, audit kept."""
    started = c.started(run.run_id, "brain.forget")
    try:
        out = adapter().forget(fact_id, reason=reason)
    except Exception as exc:                                 # noqa: BLE001
        return run.record(c.failed(
            started, f"shared brain unreachable: {type(exc).__name__}"))
    return run.record(c.succeeded(
        started, output=out,
        side_effects=(f"brain fact {fact_id} expired",)))
