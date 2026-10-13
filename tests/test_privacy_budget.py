"""PRIV-001: an objective declares a privacy budget in its execution envelope
(PRD v7 SEC-005), and the gateway intersects it with the global data-class
policy at the one seam a request leaves for a provider.

A payload outside the budget is refused a provider - asserted on the ledger
rows and on the result, not on a verdict function alone.
"""
from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import pytest

from friday import contracts as c
from friday import data_class as DC
from friday import model_gateway as mg
from friday import objectives as O
from friday import privacy_budget as PB
from friday.store import Store

ROOT = Path(__file__).resolve().parent.parent
FAKE_GW = str(ROOT / "tests" / "fake_model_gateway_worker.py")
MANIFEST = [{"id": "a", "description": "a"}]
#: Cloud-bound CONFIDENTIAL content: the global policy minimises it (REDACT);
#: only an objective's own budget can make it a refusal.
CONFIDENTIAL_TEXT = "CONFIDENTIAL board minutes for the quarter"


def _envelope(budget: PB.PrivacyBudget | None) -> c.Envelope:
    return c.Envelope(run_id="run-1", provenance=c.PERSON, attended=True,
                      expires_at="2099-01-01T00:00:00+00:00", privacy_budget=budget)


# -- the budget itself ------------------------------------------------------


def test_a_budget_that_declares_nothing_limits_nothing():
    budget = PB.PrivacyBudget()
    assert budget.refusal(DC.LOCAL_ONLY, "anthropic") == ""
    assert budget.permits_service("web.search") is True


def test_a_payload_above_the_class_ceiling_is_refused_off_a_local_backend():
    budget = PB.PrivacyBudget(max_data_class=DC.INTERNAL)
    assert DC.PERSONAL in budget.refusal(DC.PERSONAL, "anthropic")
    assert budget.refusal(DC.INTERNAL, "anthropic") == ""
    assert budget.refusal(DC.PUBLIC, "anthropic") == ""


def test_the_class_ceiling_does_not_stop_a_local_backend():
    """Nothing leaves the machine on a local backend, so a ceiling on what may
    leave does not apply to it (the global policy treats it the same way)."""
    budget = PB.PrivacyBudget(max_data_class=DC.PUBLIC)
    assert budget.refusal(DC.SECRET, "ollama") == ""


def test_a_provider_outside_the_budget_is_refused_even_for_public_content():
    budget = PB.PrivacyBudget(providers=("anthropic",))
    assert budget.refusal(DC.PUBLIC, "anthropic") == ""
    assert "openai-codex" in budget.refusal(DC.PUBLIC, "openai-codex")


def test_an_empty_provider_list_permits_no_provider_and_none_permits_all():
    assert PB.PrivacyBudget(providers=()).refusal(DC.PUBLIC, "anthropic") != ""
    assert PB.PrivacyBudget(providers=None).refusal(DC.PUBLIC, "anthropic") == ""


def test_an_external_service_outside_the_budget_is_not_permitted():
    budget = PB.PrivacyBudget(external_services=("web.search",))
    assert budget.permits_service("web.search") is True
    assert budget.permits_service("mail.send") is False
    assert PB.PrivacyBudget(external_services=()).permits_service("web.search") is False


def test_an_unknown_data_class_is_refused_at_construction():
    with pytest.raises(c.ContractError, match="data class"):
        PB.PrivacyBudget(max_data_class="TOP_SECRET")


def test_the_budget_survives_the_envelope_stored_form():
    budget = PB.PrivacyBudget(max_data_class=DC.PERSONAL, providers=("anthropic",),
                              external_services=())
    stored = _envelope(budget).to_dict()
    assert c.Envelope.from_dict(stored).privacy_budget == budget
    assert c.Envelope.from_dict(_envelope(None).to_dict()).privacy_budget is None


def test_an_envelope_from_before_budgets_reads_as_unbudgeted():
    stored = _envelope(None).to_dict()
    stored.pop("privacy_budget", None)
    assert c.Envelope.from_dict(stored).privacy_budget is None


def test_a_malformed_stored_budget_is_not_an_envelope():
    stored = _envelope(None).to_dict()
    stored["privacy_budget"] = {"max_data_class": "TOP_SECRET"}
    with pytest.raises(c.ContractError):
        c.Envelope.from_dict(stored)


@pytest.mark.parametrize("child", [
    PB.PrivacyBudget(max_data_class=DC.PERSONAL),            # raises the ceiling
    PB.PrivacyBudget(max_data_class=DC.INTERNAL),            # drops the provider limit
    PB.PrivacyBudget(max_data_class=DC.INTERNAL, providers=("anthropic", "openai-codex")),
    PB.PrivacyBudget(max_data_class=DC.INTERNAL, providers=("anthropic",)),  # drops services
    None,                                                      # drops the budget
])
def test_a_child_envelope_cannot_widen_its_parents_budget(child):
    parent = _envelope(PB.PrivacyBudget(max_data_class=DC.INTERNAL, providers=("anthropic",),
                                        external_services=("web.search",)))
    assert _envelope(child).wider_than(parent)


def test_a_child_envelope_may_narrow_its_parents_budget():
    parent = _envelope(PB.PrivacyBudget(max_data_class=DC.INTERNAL,
                                        providers=("anthropic", "openai-codex")))
    child = _envelope(PB.PrivacyBudget(max_data_class=DC.PUBLIC, providers=("anthropic",),
                                       external_services=()))
    assert child.wider_than(parent) == []
    assert _envelope(PB.PrivacyBudget(max_data_class=DC.PUBLIC)).wider_than(_envelope(None)) == []


# -- the gateway enforces it --------------------------------------------------


@pytest.fixture
def gateway(tmp_path, monkeypatch):
    for key in ("FAKE_GW_FAIL_PROVIDERS", "FAKE_GW_HANG", "FAKE_GW_DIE", "FAKE_GW_ECHO",
                "FAKE_GW_EMPTY_PROVIDERS"):
        monkeypatch.delenv(key, raising=False)
    from friday import provider_cooldowns as PC
    from friday import privacy
    monkeypatch.setattr(PC, "COOLDOWNS_FILE", tmp_path / "cooldowns.json")
    monkeypatch.setattr(privacy, "mode", lambda: privacy.OPEN)
    store = Store(tmp_path / "objectives.sqlite3")
    worker = mg.ModelGatewayWorker(command=[sys.executable, FAKE_GW], profile="")
    telemetry = mg.GatewayTelemetry(tmp_path / "gateway.sqlite3")
    gw = mg.ModelGateway(worker=worker, telemetry=telemetry, objective_store=store,
                         tier_table={mg.TIER_FAST: ("anthropic", "fake-fast"),
                                     mg.TIER_STANDARD: ("anthropic", "fake-standard"),
                                     mg.TIER_DEEP: ("openai-codex", "fake-deep")})
    try:
        yield gw, store, telemetry
    finally:
        gw.close()


def _run(store: Store, budget: PB.PrivacyBudget | None) -> str:
    run_id = "run-priv"
    minted = O.envelope_for(run_id, ["a"], provenance=c.PERSON, attended=True)
    O.compile_objective(store, request="do a thing", tasks=[{"capability": "a", "arguments": {}}],
                        manifest=MANIFEST, objective_summary="do a thing",
                        provenance=c.PERSON, attended=True, run_id=run_id,
                        envelope=replace(minted, privacy_budget=budget))
    return run_id


def _request(objective: str, text: str) -> mg.ModelGatewayRequest:
    return mg.ModelGatewayRequest(
        objective_id=objective, task_class=mg.SIMPLE,
        context_package=mg.compile_context(system="You are Friday.", user=text))


def _tool(name: str) -> dict:
    return {"type": "function",
            "function": {"name": name, "description": name,
                         "parameters": {"type": "object", "properties": {}}}}


def _tool_with_function_meta(name: str, **meta) -> dict:
    tool = _tool(name)
    tool["function"].update(meta)
    return tool


def _use_capability_tool(family: str | None = None, operation: str | None = None) -> dict:
    family_schema = {"type": "string"}
    operation_schema = {"type": "string"}
    if family is not None:
        family_schema["const"] = family
    if operation is not None:
        operation_schema["const"] = operation
    return {"type": "function", "function": {
        "name": "use_capability",
        "description": "Run a capability.",
        "parameters": {"type": "object", "properties": {
            "family": family_schema,
            "operation": operation_schema,
            "arguments": {"type": "object"},
        }, "required": ["family", "operation"]}}}


def test_without_a_budget_the_global_policy_alone_decides(gateway):
    gw, store, _ = gateway
    assert DC.classify(CONFIDENTIAL_TEXT) == DC.CONFIDENTIAL
    run_id = _run(store, None)
    res = gw.infer(_request(run_id, CONFIDENTIAL_TEXT))
    assert res.status == "ok" and res.redactions >= 0


def test_a_payload_outside_the_objectives_budget_is_refused_a_provider(gateway):
    gw, store, telemetry = gateway
    run_id = _run(store, PB.PrivacyBudget(max_data_class=DC.INTERNAL))
    res = gw.infer(_request(run_id, CONFIDENTIAL_TEXT))
    assert res.status == "failed" and res.entitlement_state == "PRIVACY_BUDGET_REFUSED"
    assert res.response == ""
    rows = telemetry.for_objective(run_id)
    assert rows and all(r["status"] == "refused" for r in rows)
    assert all(r["entitlement_state"] == "PRIVACY_BUDGET_REFUSED" for r in rows)
    assert all(a["code"] == "PRIVACY_BUDGET_REFUSED" for a in res.attempts)


def test_a_payload_inside_the_budget_still_goes_through(gateway):
    gw, store, _ = gateway
    run_id = _run(store, PB.PrivacyBudget(max_data_class=DC.INTERNAL, providers=("anthropic",)))
    assert gw.infer(_request(run_id, "ping")).status == "ok"


def test_a_provider_outside_the_budget_is_never_called(gateway):
    """Failover may find an allowed provider, never a barred one: anthropic is
    outside the budget, so only openai-codex ever answers."""
    gw, store, telemetry = gateway
    run_id = _run(store, PB.PrivacyBudget(providers=("openai-codex",)))
    res = gw.infer(_request(run_id, "ping"))
    assert res.status == "ok" and res.provider == "openai-codex"
    rows = telemetry.for_objective(run_id)
    assert all(r["status"] == "refused" for r in rows if r["provider"] == "anthropic")
    assert [r["status"] for r in rows if r["provider"] == "openai-codex"] == ["ok"]


def test_a_budget_naming_no_available_provider_leaves_none_to_call(gateway):
    gw, store, telemetry = gateway
    run_id = _run(store, PB.PrivacyBudget(providers=()))
    res = gw.infer(_request(run_id, "ping"))
    assert res.status == "failed" and res.entitlement_state == "PRIVACY_BUDGET_REFUSED"
    assert all(r["status"] == "refused" for r in telemetry.for_objective(run_id))


def test_a_tool_outside_the_external_service_budget_is_never_sent(gateway):
    gw, store, telemetry = gateway
    run_id = _run(store, PB.PrivacyBudget(external_services=("web.search",)))
    req = _request(run_id, "send a status email")
    req.tools = (_tool("mail.send"),)
    res = gw.infer(req)
    assert res.status == "failed" and res.entitlement_state == "PRIVACY_BUDGET_REFUSED"
    assert res.response == ""
    assert all(a["code"] == "PRIVACY_BUDGET_REFUSED" for a in res.attempts)
    assert all(r["status"] == "refused" for r in telemetry.for_objective(run_id))


def test_a_budget_allows_the_real_web_search_tool_name(gateway):
    gw, store, telemetry = gateway
    run_id = _run(store, PB.PrivacyBudget(external_services=("web.search",)))
    req = _request(run_id, "search the web")
    req.tools = (_tool("web_search"),)
    res = gw.infer(req)
    assert res.status == "ok"
    assert not [r for r in telemetry.for_objective(run_id) if r["status"] == "refused"]


def test_a_tool_cannot_spoof_an_allowed_external_service(gateway):
    gw, store, telemetry = gateway
    run_id = _run(store, PB.PrivacyBudget(external_services=("web.search",)))
    req = _request(run_id, "send a status email")
    req.tools = (_tool_with_function_meta("mail_send", x_external_service="web.search"),)
    res = gw.infer(req)
    assert res.status == "failed" and res.entitlement_state == "PRIVACY_BUDGET_REFUSED"
    assert "mail.send" in res.attempts[0]["error"]
    assert all(r["status"] == "refused" for r in telemetry.for_objective(run_id))


def test_a_budget_allows_a_narrowed_use_capability_schema_for_that_service(gateway):
    gw, store, _ = gateway
    run_id = _run(store, PB.PrivacyBudget(external_services=("web.search",)))
    req = _request(run_id, "search the web")
    req.tools = (_use_capability_tool("web", "search"),)
    assert gw.infer(req).status == "ok"


def test_a_budget_refuses_unconstrained_use_capability(gateway):
    gw, store, telemetry = gateway
    run_id = _run(store, PB.PrivacyBudget(external_services=("web.search",)))
    req = _request(run_id, "use a capability")
    req.tools = (_use_capability_tool(),)
    res = gw.infer(req)
    assert res.status == "failed" and res.entitlement_state == "PRIVACY_BUDGET_REFUSED"
    assert "unnamed tool" in res.attempts[0]["error"]
    assert all(r["status"] == "refused" for r in telemetry.for_objective(run_id))


def test_the_global_policy_still_refuses_what_the_budget_would_allow(gateway):
    """The budget only narrows: SECRET is refused off a local backend whatever
    the objective declared."""
    gw, store, _ = gateway
    run_id = _run(store, PB.PrivacyBudget(max_data_class=DC.LOCAL_ONLY))
    res = gw.infer(_request(run_id, "api_key: " + "sk-" + "abcdefghijklmnopqrstuvwx"))
    assert res.status == "failed" and res.entitlement_state == "DATA_CLASS_REFUSED"


def _corrupt_only_the_budget(store: Store, run_id: str) -> None:
    """Leave every other key of the stored envelope valid, so the spend check
    passes and only the privacy budget is unreadable."""
    import json
    row = store._conn.execute(
        "SELECT envelope FROM objective_runs WHERE run_id=?", (run_id,)).fetchone()
    stored = json.loads(row[0])
    stored["privacy_budget"] = 7
    store._conn.execute("UPDATE objective_runs SET envelope=? WHERE run_id=?",
                        (json.dumps(stored), run_id))
    store._conn.commit()


def test_an_unreadable_stored_budget_permits_no_provider(gateway, caplog):
    """Fail closed (model_gateway._privacy_budget): a budget that is stored but
    malformed is read as permitting no provider, and the gateway says so."""
    gw, store, _ = gateway
    run_id = _run(store, PB.PrivacyBudget())
    _corrupt_only_the_budget(store, run_id)
    with caplog.at_level("WARNING", logger=mg.logger.name):
        budget = gw._privacy_budget(run_id)
    assert budget == PB.PrivacyBudget(providers=())
    for provider in ("anthropic", "openai-codex", "ollama"):
        assert budget.refusal(DC.PUBLIC, provider)
    assert any(run_id in r.getMessage() and "unreadable" in r.getMessage()
               for r in caplog.records)


def test_an_unreadable_stored_budget_reaches_no_provider(gateway):
    """End to end: the same envelope read fails the objective's spend check
    first (it parses the whole envelope, strictly), so the run is stopped before
    any provider is called. The privacy budget's own fail-closed branch is the
    test above."""
    gw, store, telemetry = gateway
    run_id = _run(store, PB.PrivacyBudget())
    _corrupt_only_the_budget(store, run_id)
    with pytest.raises(mg.BudgetExceeded, match="cannot be read"):
        gw.infer(_request(run_id, "ping"))
    assert not [r for r in telemetry.for_objective(run_id) if r["status"] == "ok"]
