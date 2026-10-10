"""
FAB-003: routes are ranked by cost per VERIFIED COMPLETED task, measured from
the route outcomes already on record (`execution_economics.RouteOutcomes`),
not by cost per token and not by who is popular.

    - two routes serving the same model under different providers are
      measured separately
    - a cheaper route that fails more loses to a dearer one that completes
    - a route with no evidence, or too few runs to count, keeps its place
    - ranking is automatic; a request may opt out, and the store is read once
      per window, so the voice hot path does no per-request work
    - Hermes records its runs under the gateway's task classes, so the
      evidence it writes is evidence a request can find
"""
from __future__ import annotations

import json
import math

import pytest

from friday import execution_economics as ee
from friday import hermes_bridge as hb
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
    kw.setdefault("task_class", mg.SIMPLE)
    return mg.ModelGatewayRequest(
        objective_id="o",
        context_package=mg.compile_context(system="s", user="hi"), **kw)


def _measure(outcomes, provider, model, *, completed, failed=0, tokens,
             task_class="code_change"):
    """`completed` COMPLETE runs then `failed` FAILED ones, `tokens` each."""
    for i in range(completed + failed):
        _run(outcomes, f"{provider}-{model}-{i}", provider=provider, model=model,
             tokens=tokens, task_class=task_class,
             status="COMPLETE" if i < completed else "FAILED")


def test_a_route_below_the_minimum_runs_is_not_measured(tmp_path):
    outcomes = ee.RouteOutcomes(tmp_path / "o.sqlite3")
    _measure(outcomes, "few", "f", completed=2, tokens=1_000)
    _measure(outcomes, "many", "m", completed=3, tokens=1_000)
    assert set(outcomes.cost_per_completion("code_change")) == {("few", "f"), ("many", "m")}
    assert set(outcomes.cost_per_completion("code_change", min_runs=3)) == {("many", "m")}


def test_gateway_ranks_by_cost_per_completion_unless_a_request_opts_out(tmp_path, monkeypatch):
    routes = [("fast", "cheap", "c"), ("standard", "dear", "d")]
    outcomes = ee.RouteOutcomes(tmp_path / "o.sqlite3")
    # cheap completes one run in three, dear completes every run.
    _measure(outcomes, "cheap", "c", completed=1, failed=2, tokens=10_000, task_class=mg.SIMPLE)
    _measure(outcomes, "dear", "d", completed=3, tokens=20_000, task_class=mg.SIMPLE)
    gw = _gateway(tmp_path, monkeypatch, routes)
    gw._outcomes = outcomes
    assert [r[1] for r in gw.candidates(_req())] == ["dear", "cheap"]
    assert [r[1] for r in gw.candidates(_req(rank_by_outcomes=False))] == ["cheap", "dear"]


def test_a_route_with_too_few_runs_keeps_its_place(tmp_path, monkeypatch):
    routes = [("fast", "old", "o"), ("standard", "new", "n")]
    outcomes = ee.RouteOutcomes(tmp_path / "o.sqlite3")
    # one cheap completion is not evidence enough to jump ahead of `old`.
    _measure(outcomes, "new", "n", completed=1, tokens=1_000, task_class=mg.SIMPLE)
    _measure(outcomes, "old", "o", completed=3, tokens=20_000, task_class=mg.SIMPLE)
    gw = _gateway(tmp_path, monkeypatch, routes)
    gw._outcomes = outcomes
    assert [r[1] for r in gw.candidates(_req())] == ["old", "new"]


def test_a_request_that_opts_out_never_opens_the_outcome_store(tmp_path, monkeypatch):
    gw = _gateway(tmp_path, monkeypatch, [("fast", "a", "m"), ("deep", "b", "n")])

    class Boom:
        def cost_per_completion(self, *a, **k):
            raise AssertionError("opted-out request touched the outcome store")

    gw._outcomes = Boom()
    assert len(gw.candidates(_req(rank_by_outcomes=False))) == 2


class _CountingStore:
    """An outcome store that counts reads; raises when `error` is set."""

    def __init__(self, costs=None, error=None):
        self.calls, self._costs, self._error = 0, costs or {}, error

    def cost_per_completion(self, *a, **k):
        self.calls += 1
        if self._error:
            raise self._error
        return self._costs


def test_the_outcome_store_is_read_once_per_window_not_per_request(tmp_path, monkeypatch):
    gw = _gateway(tmp_path, monkeypatch, [("fast", "a", "m"), ("deep", "b", "n")])
    gw._outcomes = _CountingStore()
    for _ in range(3):
        gw.candidates(_req())
    assert gw._outcomes.calls == 1
    monkeypatch.setattr(mg.ModelGateway, "_OUTCOME_CACHE_S", 0.0)
    gw.candidates(_req())
    assert gw._outcomes.calls == 2


def test_an_unreadable_outcome_store_keeps_the_original_order(tmp_path, monkeypatch):
    gw = _gateway(tmp_path, monkeypatch, [("fast", "a", "m"), ("deep", "b", "n")])
    gw._outcomes = _CountingStore(error=RuntimeError("disk gone"))
    assert [r[1] for r in gw.candidates(_req())] == ["a", "b"]
    # the failure is held for the window too: a dead store is not retried
    # (and warned about) on every request.
    gw.candidates(_req())
    assert gw._outcomes.calls == 1


def test_a_hermes_run_is_recorded_under_a_class_a_gateway_request_can_carry(
        tmp_path, monkeypatch):
    """The writer and the ranker share one vocabulary: Hermes outcomes land
    under a gateway task class, so a request of that class finds them."""
    log = hb.WorkRunLog(tmp_path / "bridge.sqlite3")
    sup = hb.HermesSupervisor.__new__(hb.HermesSupervisor)
    sup.log = log
    runs = [("cheap", "c", "COMPLETE"), ("cheap", "c", "FAILED"), ("cheap", "c", "FAILED")]
    runs += [("dear", "d", "COMPLETE")] * 3
    for i, (provider, model, status) in enumerate(runs):
        sup._record_route_outcome({
            "work_run_id": f"hermes-rank{i}", "task": "fix the login bug",
            "route_reason": "", "provider": provider, "model": model,
            "status": status, "started_at": 1.0, "last_event_at": 2.0,
            "usage_json": json.dumps({"usage": {"calls": 1, "prompt": 10_000,
                                                "completion": 0}})})
    rows = ee.RouteOutcomes(log._path).by_run_prefix("hermes-rank")
    assert {r["task_class"] for r in rows} == {mg.STANDARD}
    gw = _gateway(tmp_path, monkeypatch, [("fast", "cheap", "c"), ("standard", "dear", "d")])
    gw._outcomes = ee.RouteOutcomes(log._path)
    ranked = gw.candidates(_req(task_class=mg.STANDARD))
    assert [r[1] for r in ranked] == ["dear", "cheap"]
