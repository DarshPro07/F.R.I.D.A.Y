"""
Capability fitness as a routing input, and the decision as a record
(FR-MDL-006, PRD section 11).

Two defects this covers, both of the same kind - a declaration with no
consumer:

* `ModelGatewayRequest.required_capabilities` was a field nothing read. A
  caller could ask for a route that supports tools, be given one that does
  not, and learn about it from the provider's error.
* `friday/model_capabilities.py` declared vision, tools, structured output
  and context for every model it knew, and no production code imported it.

The rule under test is the one that makes wiring them together safe: fail
CLOSED on a declaration that says no, fail OPEN on no declaration at all.
The registry deliberately declares only what has been confirmed, so a filter
that rejected everything undeclared would take the product offline to enforce
a field nobody had filled in. Both halves are asserted, because only
enforcing one of them is how this becomes either useless or catastrophic.
"""
from __future__ import annotations

import pytest

from friday import model_capabilities as MC
from friday import model_gateway as MG


@pytest.fixture
def declared(monkeypatch):
    """A synthetic registry: one capable route, one that cannot use tools,
    and one with a small context. Synthetic because the real registry
    declares only the voice backends today, and a test that depended on
    which providers happen to be declared would break the day one is added.
    """
    def cap(model_id, backend, **kw):
        base = dict(vision=True, tools=True, structured_output=True,
                    realtime_audio=False, max_context=None,
                    relative_latency="low", relative_cost="low")
        base.update(kw)
        return MC.ModelCapabilities(model_id=model_id, provider=backend,
                                    backend=backend, **base)

    table = {
        ("able", "m-able"): cap("m-able", "able"),
        ("toolless", "m-toolless"): cap("m-toolless", "toolless", tools=False),
        ("blind", "m-blind"): cap("m-blind", "blind", vision=False),
        ("small", "m-small"): cap("m-small", "small", max_context=1000),
    }
    monkeypatch.setattr(MC, "CAPABILITIES", table)
    return table


def _request(**kw):
    kw.setdefault("objective_id", "OBJ-1")
    kw.setdefault("task_class", "SIMPLE")
    kw.setdefault("context_package", [{"role": "user", "content": "hello"}])
    return MG.ModelGatewayRequest(**kw)


# --- fail closed on a declaration that says no ------------------------------

def test_a_route_declared_without_tools_is_refused_for_a_tool_using_request(declared):
    fit, reason = MG.capability_verdict("toolless", "m-toolless",
                                        required=("tools",))
    assert not fit
    assert "without tools" in reason


def test_a_route_declared_without_vision_is_refused_for_an_image_request(declared):
    fit, reason = MG.capability_verdict("blind", "m-blind", required=("images",))
    assert not fit, reason


def test_a_capable_route_is_kept_and_says_why(declared):
    """The green half. A filter that rejected the capable route too would
    satisfy the tests above and break the product."""
    fit, reason = MG.capability_verdict("able", "m-able",
                                        required=("tools", "vision"))
    assert fit
    assert reason == "declared capable"


def test_a_request_too_large_for_a_declared_context_is_refused(declared):
    fit, reason = MG.capability_verdict("small", "m-small", input_tokens=5000)
    assert not fit
    assert "1000-token context" in reason


def test_a_request_inside_a_declared_context_is_kept(declared):
    assert MG.capability_verdict("small", "m-small", input_tokens=10)[0]


# --- fail open on no declaration -------------------------------------------

def test_an_undeclared_route_is_kept_and_the_reason_says_it_was_not_checked(declared):
    """The registry declares what has been confirmed, and today that is not
    the providers reached through Hermes. Rejecting them would enforce an
    unfilled field by taking the product offline; keeping them silently
    would hide the gap. So it is kept, and the reason records which it is.
    """
    fit, reason = MG.capability_verdict("anthropic", "claude-opus-5",
                                        required=("tools",))
    assert fit
    assert "no declared capabilities" in reason, reason


def test_the_two_kept_cases_are_distinguishable(declared):
    """"Kept because declared capable" and "kept because nothing is known"
    are different facts. Developer Mode has to be able to tell them apart,
    or the second quietly reads as the first."""
    _, known = MG.capability_verdict("able", "m-able", required=("tools",))
    _, unknown = MG.capability_verdict("nobody", "nothing", required=("tools",))
    assert known != unknown


# --- caller errors surface --------------------------------------------------

def test_an_unknown_required_capability_raises_instead_of_passing(declared):
    """A requirement that cannot be checked must not be answered "fine".
    Silently ignoring it is how the field became a lie in the first place."""
    with pytest.raises(ValueError) as caught:
        MG.capability_verdict("able", "m-able", required=("telepathy",))
    assert "telepathy" in str(caught.value)


# --- the decision record ----------------------------------------------------

def test_the_decision_records_the_inputs_the_choice_and_every_rejection(declared):
    gateway = MG.ModelGateway.__new__(MG.ModelGateway)
    request = _request(required_capabilities=("tools",),
                       latency_budget_ms=800)
    decision = MG.ModelGateway.route_decision(
        gateway, request,
        [("fast", "toolless", "m-toolless"), ("standard", "able", "m-able")])

    assert decision.routes == [("standard", "able", "m-able")], \
        "the incapable route was offered for a tool-using request"
    assert [j.provider for j in decision.rejected] == ["toolless"]
    assert decision.rejected[0].reason == "declared without tools"

    payload = decision.to_dict()
    assert payload["chosen"] == ("standard", "able", "m-able")
    assert payload["required_capabilities"] == ["tools"]
    assert payload["latency_budget_ms"] == 800
    assert payload["task_class"] == "SIMPLE"
    assert payload["rejected"][0]["reason"] == "declared without tools"


def test_the_decision_records_a_preference_when_the_caller_set_one(declared):
    gateway = MG.ModelGateway.__new__(MG.ModelGateway)
    allow = MG.ModelGateway.route_decision(
        gateway, _request(provider_allowlist=("able",)),
        [("standard", "able", "m-able")])
    assert "allowlist: able" == allow.preference
    deny = MG.ModelGateway.route_decision(
        gateway, _request(provider_denylist=("toolless",)),
        [("standard", "able", "m-able")])
    assert "denylist: toolless" == deny.preference


def test_an_empty_route_list_produces_a_decision_with_no_chosen_route(declared):
    """No route is a legitimate outcome and must be legible, not an
    exception and not an empty dict."""
    gateway = MG.ModelGateway.__new__(MG.ModelGateway)
    decision = MG.ModelGateway.route_decision(gateway, _request(), [])
    assert decision.routes == []
    assert decision.to_dict()["chosen"] is None


# --- negative control -------------------------------------------------------

def test_removing_the_fitness_filter_would_let_the_incapable_route_through(declared):
    """The load-bearing test.

    It restores the pre-filter behaviour in this process, re-runs the same
    request, and requires the incapable route to be offered - which proves
    the test above is detecting the filter rather than an unrelated ordering
    effect. Restored in `finally`, so a failure cannot leave routing blind
    for the rest of the session.
    """
    gateway = MG.ModelGateway.__new__(MG.ModelGateway)
    request = _request(required_capabilities=("tools",))
    routes = [("fast", "toolless", "m-toolless"), ("standard", "able", "m-able")]

    original = MG.capability_verdict
    try:
        MG.capability_verdict = lambda *a, **k: (True, "filter removed")
        blind = MG.ModelGateway.route_decision(gateway, request, routes)
        assert ("fast", "toolless", "m-toolless") in blind.routes, (
            "with the fitness filter removed the incapable route was STILL "
            "dropped, so the passing test is not proving what it claims")
    finally:
        MG.capability_verdict = original

    restored = MG.ModelGateway.route_decision(gateway, request, routes)
    assert ("fast", "toolless", "m-toolless") not in restored.routes, \
        "the filter was not restored"
