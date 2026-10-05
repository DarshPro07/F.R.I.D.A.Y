"""
A vendor claim alone cannot promote a route (EVAL-003, PRD Model Arena).

`capability_verdict` keeps a route that its registry entry declares capable.
That is a claim, not a measurement. These tests pin the rule that routing also
consults measured evidence - a `golden.Report` recorded against the route -
and that only a report whose every gate passed promotes it.

Two behaviours are asserted together because enforcing only one fails in
opposite directions: an unmeasured route must stay USABLE (the registry and
the benchmark store are both mostly empty today, so refusing it would take
the product offline), and it must stay UNPROMOTED (or the declaration is
promoting it after all). A caller that needs proof opts in with
`require_measured`.
"""
from __future__ import annotations

import pytest

from friday import golden
from friday import model_capabilities as MC
from friday import model_gateway as MG
from friday import route_benchmarks as RB


@pytest.fixture
def declared(monkeypatch):
    """One route declared fully capable, so any difference in how it is
    treated can only come from the benchmark store."""
    table = {("able", "m-able"): MC.ModelCapabilities(
        model_id="m-able", provider="able", backend="able", vision=True,
        tools=True, structured_output=True, realtime_audio=False,
        max_context=None, relative_latency="low", relative_cost="low")}
    monkeypatch.setattr(MC, "CAPABILITIES", table)
    return table


@pytest.fixture
def bench(tmp_path):
    """An isolated benchmark store, never the process's real one."""
    s = RB.BenchmarkStore(tmp_path / "route_benchmarks.json")
    RB.reset_store(s)
    yield s
    RB.reset_store(None)


def _report(passed: int, failed: int = 0) -> golden.Report:
    """A real `golden.Report`, so these tests break if its shape changes."""
    def result(i: int, ok: bool) -> golden.CaseResult:
        return golden.CaseResult(
            case_id=f"c{i}", category="files", passed=ok, correctness=ok,
            evidence=ok, policy=True, latency=True, interventions_ok=True,
            false_completion=False, unauthorized=False, seconds=0.1,
            model_calls=0, status="COMPLETED", run_id=f"r{i}")
    results = ([result(i, True) for i in range(passed)]
               + [result(passed + i, False) for i in range(failed)])
    return golden.Report(results=results,
                         provenance={"commit": "abc1234", "date": "2026-10-05T00:00:00+00:00"})


def _decide(**kw) -> MG.RouteDecision:
    gateway = MG.ModelGateway.__new__(MG.ModelGateway)
    request = MG.ModelGatewayRequest(
        objective_id="OBJ-1", task_class="SIMPLE",
        context_package=[{"role": "user", "content": "hello"}], **kw)
    return MG.ModelGateway.route_decision(gateway, request,
                                          [("standard", "able", "m-able")])


# --- the store reads a golden report ----------------------------------------

def test_a_report_that_met_every_gate_is_recorded_as_passed(bench):
    m = bench.record_report("able", "m-able", _report(passed=150))
    assert m.passed and m.failed_gates == ()
    assert (m.cases, m.commit) == (150, "abc1234")
    assert bench.get("able", "m-able") == m


def test_a_report_below_the_case_minimum_is_recorded_as_failed(bench):
    """A filtered `--id` run can score 100% and still prove nothing."""
    m = bench.record_report("able", "m-able", _report(passed=10))
    assert not m.passed
    assert "minimum_cases" in m.failed_gates


def test_a_report_under_the_success_target_names_the_gate_it_missed(bench):
    m = bench.record_report("able", "m-able", _report(passed=120, failed=30))
    assert not m.passed and "success_rate" in m.failed_gates


def test_pass_is_derived_from_the_gates_not_from_the_report_flag(bench):
    """A report whose `all_gates_pass` says yes while a gate says no must not
    promote anything."""
    payload = _report(passed=150).to_dict()
    payload["summary"]["gates"]["success_rate"] = False
    payload["summary"]["all_gates_pass"] = True
    assert not bench.record_report("able", "m-able", payload).passed


def test_a_report_with_no_gates_is_refused(bench):
    payload = _report(passed=150).to_dict()
    payload["summary"]["gates"] = {}
    with pytest.raises(ValueError):
        bench.record_report("able", "m-able", payload)


def test_a_latest_report_replaces_the_earlier_one(bench):
    bench.record_report("able", "m-able", _report(passed=150))
    bench.record_report("able", "m-able", _report(passed=100, failed=50))
    assert not bench.get("able", "m-able").passed


def test_evidence_survives_a_new_store_on_the_same_file(bench):
    bench.record_report("able", "m-able", _report(passed=150))
    assert RB.BenchmarkStore(bench.path).get("able", "m-able").passed


def test_an_unreadable_store_file_raises_instead_of_reading_as_empty(bench):
    """Reading garbage as "no evidence" would silently erase a recorded
    failure."""
    bench.path.write_text("{not json", encoding="utf-8")
    with pytest.raises(RB.BenchmarkStoreError):
        bench.get("able", "m-able")


# --- routing consults it -----------------------------------------------------

def test_a_vendor_claim_alone_does_not_promote_a_route(declared, bench):
    """The acceptance criterion. The route is declared capable for the very
    capability asked for, and that is still not promotion."""
    decision = _decide(required_capabilities=("tools",))
    judgement = decision.judgements[0]
    assert judgement.fit, "an unmeasured route must stay usable"
    assert not judgement.promoted
    assert decision.promoted == []
    assert "declared capable" in judgement.reason
    assert "not benchmarked" in judgement.reason


def test_a_passing_benchmark_promotes_the_route(declared, bench):
    bench.record_report("able", "m-able", _report(passed=150))
    decision = _decide(required_capabilities=("tools",))
    assert decision.promoted == [("standard", "able", "m-able")]
    assert "every gate passed" in decision.judgements[0].reason


def test_a_failing_benchmark_does_not_promote_but_keeps_the_route_usable(declared, bench):
    bench.record_report("able", "m-able", _report(passed=100, failed=50))
    decision = _decide()
    assert decision.routes == [("standard", "able", "m-able")]
    assert decision.promoted == []
    assert "measured below target" in decision.judgements[0].reason
    assert "success_rate" in decision.judgements[0].reason


def test_requiring_measurement_refuses_a_route_known_only_by_declaration(declared, bench):
    decision = _decide(require_measured=True)
    assert decision.routes == []
    assert "a declaration alone does not qualify" in decision.rejected[0].reason


def test_requiring_measurement_refuses_a_route_that_measured_below_target(declared, bench):
    bench.record_report("able", "m-able", _report(passed=100, failed=50))
    assert _decide(require_measured=True).routes == []


def test_requiring_measurement_keeps_a_route_that_passed(declared, bench):
    bench.record_report("able", "m-able", _report(passed=150))
    assert _decide(require_measured=True).routes == [("standard", "able", "m-able")]


def test_an_unreadable_store_never_promotes_and_says_so(declared, bench):
    bench.path.write_text("{not json", encoding="utf-8")
    kept = _decide()
    assert kept.routes and kept.promoted == []
    assert "benchmark store unreadable" in kept.judgements[0].reason
    assert _decide(require_measured=True).routes == []


def test_a_capability_refusal_still_comes_first(declared, bench):
    """Benchmarks add to the declared filter; they do not replace it."""
    bench.record_report("able", "m-able", _report(passed=150))
    MC.CAPABILITIES[("able", "m-able")] = MC.ModelCapabilities(
        model_id="m-able", provider="able", backend="able", vision=True,
        tools=False, structured_output=True, realtime_audio=False,
        max_context=None, relative_latency="low", relative_cost="low")
    decision = _decide(required_capabilities=("tools",))
    assert decision.routes == [] and decision.promoted == []
    assert decision.rejected[0].reason == "declared without tools"


def test_the_decision_record_lists_promoted_routes(declared, bench):
    bench.record_report("able", "m-able", _report(passed=150))
    assert _decide().to_dict()["promoted"] == [("standard", "able", "m-able")]


def test_a_declaration_would_promote_if_routing_did_not_consult_the_store(declared, bench, monkeypatch):
    """Negative control for the acceptance test: with the benchmark pass
    replaced by one that trusts the declaration, the unmeasured route IS
    promoted - so the passing test above is detecting the benchmark
    consult, not an unrelated default."""
    monkeypatch.setattr(MG, "benchmark_verdict",
                        lambda *a, **k: (True, True, "declaration trusted"))
    assert _decide(required_capabilities=("tools",)).promoted == [
        ("standard", "able", "m-able")]


# --- golden runs feed the store ---------------------------------------------

def test_a_golden_run_for_a_route_is_recorded_against_that_route(bench):
    golden.record_route_benchmark(_report(passed=150), "able/m-able")
    assert bench.get("able", "m-able").passed


def test_a_route_name_without_a_model_is_refused(bench):
    with pytest.raises(ValueError):
        golden.record_route_benchmark(_report(passed=150), "able")
