"""
Hermes MODEL_GATEWAY as capability-runtime implementations.

`friday/tools/model_gateway_control.py` is the MCP face; these are the
`run`-first functions the objective engine binds to, so an objective task
can ask for one bounded inference or read the provider inventory without
going through the model's tool surface. Same mechanism underneath
(`friday.model_gateway`), same contracts: every call is an ActionResult
with verification evidence, never a bare string.
"""
from __future__ import annotations

from friday import contracts as c
from friday import model_gateway as mg
from friday import provider_health as PH
from friday.policy import PolicyEngine, default_engine


def model_providers(run: c.Run, *, engine: PolicyEngine = default_engine) -> c.ActionResult:
    """The provider inventory Hermes can broker right now (FR-071/072),
    with each route's EVIDENCE-based health beside its credential state
    (Requirement 9): `usable` means a key exists; `health[provider]` is
    what the call ledger last saw that route do."""
    started = c.started(run.run_id, "model.providers")
    try:
        gw = mg.gateway()
        inv = gw.providers(max_age_s=0.0)
    except mg.GatewayUnavailable as exc:
        return run.record(c.failed(started, f"model gateway unavailable: {exc}"))
    usable = inv.get("usable", [])
    verdicts = gw.provider_health(list(usable))
    healthy = [p for p, v in verdicts.items() if v.state == PH.HEALTHY]
    unavailable = [p for p, v in verdicts.items() if v.state == PH.UNAVAILABLE]
    return run.record(c.succeeded(
        started,
        output={"usable": usable, "main": inv.get("main"),
                "healthy": healthy, "unavailable": unavailable,
                "health": {p: v.to_dict() for p, v in verdicts.items()},
                "providers": [{"id": p["id"], "route_kind": p.get("route_kind", ""),
                               "authenticated": bool(p.get("authenticated")),
                               "default_model": p.get("default_model", "")}
                              for p in inv.get("providers", [])]},
        verification=c.Verification(
            method="hermes_provider_inventory_with_ledger_evidence",
            evidence=(f"{len(usable)} authenticated route(s); "
                      f"{len(healthy)} seen answering: {', '.join(healthy[:6]) or 'none'}; "
                      f"{len(unavailable)} unavailable: {', '.join(unavailable[:6]) or 'none'}"))))


def _objective_facts(objective_id: str):
    """(facts, bound): the critical facts of the objective a call works for
    (CTX-003's set), and whether they bind the call, or (None, False) when it
    names no objective the store holds. An objective that came from read
    material has nothing the person gave, so none of it binds. An objective
    whose record cannot be read raises (a ContractError is a ValueError), so
    the call fails rather than running without its constraints."""
    if not objective_id:
        return None, False
    from friday import compaction
    from friday.toolsets.objectives import store as objective_store

    db = objective_store()
    try:
        facts = compaction.for_objective(db, objective_id)
    except LookupError:
        return None, False
    return facts, (db.objective_run(objective_id) or {}).get("provenance") == c.PERSON


def model_infer(run: c.Run, prompt: str, *, task_class: str = mg.STANDARD,
                quality_tier: str = "", objective_id: str = "",
                system: str = "", escalate: bool = False,
                engine: PolicyEngine = default_engine) -> c.ActionResult:
    """One inference-only request (FR-070/076/079). The objective's own id
    is the attribution key when the task graph supplies it."""
    started = c.started(run.run_id, "model.infer")
    try:
        # CTX-001: the call gets a compiled working set; a step of an
        # objective carries that objective's critical facts, which the
        # compiler keeps whole (CTX-003).
        facts, bound = _objective_facts(objective_id)
        # The task class's input ceiling is the working set's budget, so an
        # over-large context sheds its least needed sections here, not later;
        # a context whose facts alone do not fit is refused, not shortened.
        working_set = mg.compile_working_set(
            system=system, user=prompt, facts=facts, facts_bind=bound,
            budget_tokens=mg.budget_for(task_class or mg.STANDARD).max_input_tokens)
        request = mg.ModelGatewayRequest(
            objective_id=objective_id or run.run_id,
            task_class=task_class or mg.STANDARD,
            context_package=working_set.messages,
            context_telemetry=working_set.telemetry(),
            preferred_quality_tier=quality_tier or "", escalate=bool(escalate))
        result = mg.gateway().infer(request)
    except (mg.BudgetExceeded, mg.GrowthStopped) as exc:
        return run.record(c.failed(started, f"refused: {exc}"))
    except (mg.GatewayUnavailable, ValueError) as exc:
        return run.record(c.failed(started, str(exc)))
    if result.status != "ok":
        return run.record(c.failed(
            started, f"{result.entitlement_state}: "
                     f"{'; '.join(result.warnings) or 'no provider answered'}"))
    return run.record(c.succeeded(
        started,
        output={"response": result.response, "provider": result.provider,
                "model": result.model, "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "latency_ms": result.latency_ms, "boundary": result.boundary,
                "failover_count": result.failover_count, "call_id": result.call_id,
                "context": working_set.telemetry()},
        verification=c.Verification(
            method="gateway_telemetry_row",
            evidence=f"gateway_calls#{result.call_id} {result.provider}/{result.model} "
                     f"{result.input_tokens}+{result.output_tokens} tokens "
                     f"{result.latency_ms}ms boundary={result.boundary}")))


def model_usage(run: c.Run, objective_id: str = "", *, limit: int = 20,
                engine: PolicyEngine = default_engine) -> c.ActionResult:
    """Gateway telemetry per objective (FR-080/055)."""
    started = c.started(run.run_id, "model.usage")
    telemetry = mg.gateway().telemetry
    if objective_id:
        out = {"summary": telemetry.summary(objective_id),
               "calls": telemetry.for_objective(objective_id)[-limit:]}
    else:
        out = {"recent": telemetry.recent(limit), "spikes": telemetry.spikes()}
    return run.record(started.finish(status=c.OBSERVED, output=out))


def system_pressure(run: c.Run, *, engine: PolicyEngine = default_engine) -> c.ActionResult:
    """The resource governor's status (FR-056), as an observation."""
    from friday import governor as G
    started = c.started(run.run_id, "system.pressure")
    return run.record(started.finish(status=c.OBSERVED, output=G.governor().status()))


def system_diagnostics(run: c.Run, *, sections: str = "",
                       engine: PolicyEngine = default_engine) -> c.ActionResult:
    """The one operational view (PRD 12.3): build, stores, providers, Hermes,
    browser, voice, MCP/capabilities, queue, pressure, recent failures.
    Redacted by default; `sections` narrows it (comma-separated)."""
    from friday import observability as OB
    started = c.started(run.run_id, "system.diagnostics")
    wanted = tuple(s.strip() for s in (sections or "").split(",") if s.strip()) or None
    report = OB.diagnostics(sections=wanted)
    return run.record(started.finish(status=c.OBSERVED, output=report))


def objective_trace(run: c.Run, run_id: str = "", *, as_text: bool = False,
                    engine: PolicyEngine = default_engine) -> c.ActionResult:
    """FR-054: one trace reconstructs what happened to an objective - state
    transitions, tool calls, workers, model calls, policy decisions,
    latency, retries, errors and verification - from durable state only."""
    from friday import objectives as O
    from friday import observability as OB
    from friday.toolsets.objectives import store as objective_store
    started = c.started(run.run_id, "objectives.trace")
    db = objective_store()
    found = O.active_run(db, run_id=(run_id or "").strip())
    if found is None:
        return run.record(c.failed(
            started, f"no objective run {run_id!r}" if run_id.strip()
            else "no objective has been started"))
    if as_text:
        out = {"run_id": found["run_id"], "text": OB.trace_text(found["run_id"], store=db)}
    else:
        out = OB.trace(found["run_id"], store=db)
    return run.record(c.succeeded(
        started, output=out,
        verification=c.Verification(
            method="store_read",
            evidence=f"trace assembled from {sum(out.get('sources', {}).values()) if not as_text else 'ledger'} rows")))
