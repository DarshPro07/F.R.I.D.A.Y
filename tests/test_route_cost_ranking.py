"""
FAB-003: routes are ranked by cost per VERIFIED COMPLETED task, measured from
the route outcomes already on record (`execution_economics.RouteOutcomes`),
not by cost per token and not by who is popular.

    - two routes serving the same model under different providers are
      measured separately
    - a cheaper route that fails more loses to a dearer one that completes
    - a route with no evidence keeps its place; the gateway only ranks when a
      request opts in, so the voice hot path does no extra work
"""
from __future__ import annotations

import math

import pytest

from friday import execution_economics as ee
from friday import model_gateway as mg


def _run(outcomes, run_id, *, provider, model, tokens, status="COMPLETE",
         rework=0, task_class="code_change"):
    outcomes.record(run_id, task_class=task_class, route_level="hermes_single",
                    tier="standard", model=model, provider=provider, calls=1,
                    prompt_tokens=tokens, status=status, rework=rework)


def test_cost_per_completion_is_keyed_by_provider_and_model(tmp_path):
    outcomes = ee.RouteOutcomes(tmp_path / "o.sqlite3")
    # The same model behind two providers: one completes at 10k, one at 30k.
    _run(outcomes, "a1", provider="direct", model="m1", tokens=10_000)
    _run(outcomes, "b1", provider="reseller", model="m1", tokens=30_000)
    costs = outcomes.cost_per_completion("code_change")
    assert set(costs) == {("direct", "m1"), ("reseller", "m1")}
    assert costs[("direct", "m1")] < costs[("reseller", "m1")]


def test_failed_attempts_are_paid_for_by_the_completions(tmp_path):
    outcomes = ee.RouteOutcomes(tmp_path / "o.sqlite3")
    # cheap fails twice for every completion; dear completes every time.
    _run(outcomes, "c1", provider="cheap", model="c", tokens=10_000)
    _run(outcomes, "c2", provider="cheap", model="c", tokens=10_000, status="FAILED")
    _run(outcomes, "c3", provider="cheap", model="c", tokens=10_000, status="FAILED")
    _run(outcomes, "d1", provider="dear", model="d", tokens=20_000)
    costs = outcomes.cost_per_completion("code_change")
    assert costs[("cheap", "c")] == pytest.approx(30_000, rel=0.01)
    assert costs[("dear", "d")] == pytest.approx(20_000, rel=0.01)


def test_a_route_that_never_completes_costs_infinity(tmp_path):
    outcomes = ee.RouteOutcomes(tmp_path / "o.sqlite3")
    _run(outcomes, "x1", provider="p", model="m", tokens=1, status="FAILED")
    assert math.isinf(outcomes.cost_per_completion("code_change")[("p", "m")])


def test_other_task_classes_do_not_count(tmp_path):
    outcomes = ee.RouteOutcomes(tmp_path / "o.sqlite3")
    _run(outcomes, "o1", provider="p", model="m", tokens=5, task_class="research")
    assert outcomes.cost_per_completion("code_change") == {}


def test_rank_routes_puts_the_cheaper_completer_first_and_never_the_failer():
    routes = [("fast", "cheap", "c"), ("standard", "dear", "d"), ("deep", "dead", "x")]
    costs = {("cheap", "c"): 30_000.0, ("dear", "d"): 20_000.0, ("dead", "x"): math.inf}
    ranked = ee.rank_routes(routes, costs)
    assert ranked == [("standard", "dear", "d"), ("fast", "cheap", "c"), ("deep", "dead", "x")]


def test_rank_routes_leaves_unmeasured_routes_where_they_were():
    routes = [("a", "p1", "m1"), ("b", "unknown", "u"), ("c", "p2", "m2")]
    costs = {("p1", "m1"): 50.0, ("p2", "m2"): 10.0}
    ranked = ee.rank_routes(routes, costs)
    assert ranked == [("c", "p2", "m2"), ("b", "unknown", "u"), ("a", "p1", "m1")]
    assert ee.rank_routes(routes, {}) == routes


def _gateway(tmp_path, monkeypatch, routes):
    gw = mg.ModelGateway(telemetry=mg.GatewayTelemetry(tmp_path / "gw.sqlite3"),
                         tier_table={})
    monkeypatch.setattr(gw, "_raw_candidates", lambda request: list(routes))
    return gw


def _req(**kw):
    return mg.ModelGatewayRequest(
        objective_id="o", task_class=mg.SIMPLE,
        context_package=mg.compile_context(system="s", user="hi"), **kw)


def test_gateway_ranks_by_cost_per_completion_only_when_asked(tmp_path, monkeypatch):
    routes = [("fast", "cheap", "c"), ("standard", "dear", "d")]
    outcomes = ee.RouteOutcomes(tmp_path / "o.sqlite3")
    _run(outcomes, "c1", provider="cheap", model="c", tokens=10_000, task_class=mg.SIMPLE)
    _run(outcomes, "c2", provider="cheap", model="c", tokens=10_000, task_class=mg.SIMPLE,
         status="FAILED")
    _run(outcomes, "c3", provider="cheap", model="c", tokens=10_000, task_class=mg.SIMPLE,
         status="FAILED")
    _run(outcomes, "d1", provider="dear", model="d", tokens=20_000, task_class=mg.SIMPLE)
    gw = _gateway(tmp_path, monkeypatch, routes)
    gw._outcomes = outcomes
    assert [r[1] for r in gw.candidates(_req())] == ["cheap", "dear"]
    assert [r[1] for r in gw.candidates(_req(rank_by_outcomes=True))] == ["dear", "cheap"]


def test_the_default_path_never_opens_the_outcome_store(tmp_path, monkeypatch):
    gw = _gateway(tmp_path, monkeypatch, [("fast", "a", "m"), ("deep", "b", "n")])

    class Boom:
        def cost_per_completion(self, *a, **k):
            raise AssertionError("hot path touched the outcome store")

    gw._outcomes = Boom()
    assert len(gw.candidates(_req())) == 2


def test_an_unreadable_outcome_store_keeps_the_original_order(tmp_path, monkeypatch):
    gw = _gateway(tmp_path, monkeypatch, [("fast", "a", "m"), ("deep", "b", "n")])

    class Broken:
        def cost_per_completion(self, *a, **k):
            raise RuntimeError("disk gone")

    gw._outcomes = Broken()
    assert [r[1] for r in gw.candidates(_req(rank_by_outcomes=True))] == ["a", "b"]
