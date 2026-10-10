"""
EVO-004: the Evolution Value Gate. A proposal needs a measurable reason - task
success, manual effort, reliability or cost - before any experiment spends money.
A new repository or model is not a reason by itself. A refusal is archived in
EVO-002's Evolution Archive and happens before any paid call: the model gateway
is booby-trapped so a call would fail the test.

Real Store and real archive on tmp_path; deciding is plain code.
"""
from __future__ import annotations

import pytest

from friday import evolution_controller as C
from friday import evolution_value_gate as V
from friday import forge_value_gate as F
from friday import model_gateway as MG
from friday.evolution_archive import REJECT, EvolutionArchive
from friday.store import Store


@pytest.fixture(autouse=True)
def no_paid_call(monkeypatch):
    """Any route to a paid model fails the test."""
    def boom(*args, **kwargs):
        raise AssertionError("a paid model call was attempted")
    for name in ("start", "call", "_request", "_launch_plan", "__init__"):
        monkeypatch.setattr(MG.ModelGatewayWorker, name, boom)


def _reason(kind=V.COST, baseline=0.40, target=0.20, metric="usd per retrieval"):
    return {"kind": kind, "metric": metric, "baseline": baseline, "target": target}


def _release(reasons, subject="shiny-agent-framework", **kw):
    evidence = {"origin": "new_repository", "reasons": reasons}
    return C.Trigger(C.NEW_RELEASE, subject, component=kw.pop("component", "friday/retrieval.py"),
                     evidence=evidence, **kw)


def _env(tmp_path):
    return Store(tmp_path / "s.sqlite3"), EvolutionArchive(tmp_path / "archive")


@pytest.mark.parametrize("reasons", [
    [],                                                      # a new repository, nothing measured
    [{"kind": "popularity", "metric": "stars", "baseline": 1, "target": 9000}],   # not a reason kind
    [_reason(metric="")],                                    # nothing says what was measured
    [_reason(baseline=0.40, target=0.40)],                   # no change
    [_reason(baseline=0.40, target=0.39)],                   # below the materiality floor
    [_reason(baseline=0.40, target=0.80)],                   # cost got worse
    [_reason(kind=V.RELIABILITY, baseline=0.9, target=0.5)], # reliability got worse
    [_reason(baseline="a lot", target=0.1)],                 # not a number
    [_reason(baseline=True, target=0.1)],
    [{"kind": V.COST}],                                      # no numbers at all
    "it is faster",                                          # prose instead of data
])
def test_a_proposal_without_a_measurable_reason_is_refused_and_archived(tmp_path, reasons):
    store, archive = _env(tmp_path)
    decision = C.consider(store, _release(reasons), archive=archive)
    assert decision.proposed is False and decision.created is False
    assert "measurable" in decision.reason
    assert C.proposals(store) == []                          # nothing was recorded to run
    rows = archive.query(decision=REJECT)
    assert len(rows) == 1
    assert rows[0]["evaluation"]["gate"] == "evolution_value_gate"
    assert rows[0]["evaluation"]["origin"] == "new_repository"
    assert rows[0]["scope"] == ["friday/retrieval.py"]


def test_a_new_repository_or_model_is_not_a_reason_by_itself(tmp_path):
    store, archive = _env(tmp_path)
    for origin in ("new_repository", "new_model"):
        trigger = C.Trigger(C.NEW_RELEASE, f"brand-new-{origin}", component="friday/router.py",
                            evidence={"origin": origin, "reasons": [],
                                      "note": "released yesterday, 20k stars"})
        assert C.consider(store, trigger, archive=archive).proposed is False
    assert len(archive.query(decision=REJECT)) == 2
    assert C.proposals(store) == []


def test_a_measured_gain_is_admitted_as_one_proposal_and_not_archived(tmp_path):
    store, archive = _env(tmp_path)
    trigger = _release([_reason(V.COST, 0.40, 0.20), _reason(V.TASK_SUCCESS, 0.80, 0.90, "pass rate")])
    first = C.consider(store, trigger, archive=archive)
    again = C.consider(store, trigger, archive=archive)
    assert first.proposed and first.created and not again.created
    assert first.proposal["proposal_id"] == again.proposal["proposal_id"]
    assert first.proposal["components"] == ["friday/retrieval.py"]
    assert archive.query() == []                             # only refusals are archived here


def test_the_same_refused_proposal_is_archived_once(tmp_path):
    store, archive = _env(tmp_path)
    trigger = _release([])
    first = C.consider(store, trigger, archive=archive)
    second = C.consider(store, trigger, archive=archive)
    assert first.proposed is False and second.proposed is False
    assert len(archive.query(decision=REJECT)) == 1
    assert "already refused" in second.reason


def test_a_release_trigger_must_name_what_it_would_change(tmp_path):
    store, archive = _env(tmp_path)
    with pytest.raises(ValueError):
        C.consider(store, C.Trigger(C.NEW_RELEASE, "x", evidence={"reasons": [_reason()]}),
                   archive=archive)
    assert archive.query() == []


def test_the_gate_reuses_the_forge_scorer(tmp_path, monkeypatch):
    seen = []
    real = F.score_value

    def spy(gains, costs):
        seen.append(dict(gains))
        return real(gains, costs)
    monkeypatch.setattr(F, "score_value", spy)
    decision = V.review(hypothesis="h", parent_version="current", scope=["friday/a.py"],
                        origin="new_model", reasons=[_reason(V.COST, 0.40, 0.20)],
                        archive=EvolutionArchive(tmp_path / "a"))
    assert decision.allowed is True
    assert seen == [{V.COST: pytest.approx(0.5)}]
    assert decision.score.net == pytest.approx(0.5)


def test_direction_is_per_kind(tmp_path):
    arch = EvolutionArchive(tmp_path / "a")

    def allowed(reason):
        return V.review(hypothesis=str(reason), parent_version="current", scope=["friday/a.py"],
                        origin="trigger", reasons=[reason], archive=arch).allowed
    assert allowed(_reason(V.TASK_SUCCESS, 0.5, 0.7))
    assert allowed(_reason(V.RELIABILITY, 0.8, 0.9))
    assert allowed(_reason(V.MANUAL_EFFORT, 10, 4, "minutes per report"))
    assert not allowed(_reason(V.MANUAL_EFFORT, 4, 10, "minutes per report"))
    assert not allowed(_reason(V.TASK_SUCCESS, 0.7, 0.5))


def test_a_non_finite_measurement_is_never_a_reason(tmp_path):
    arch = EvolutionArchive(tmp_path / "a")
    for bad in (float("nan"), float("inf"), -1.0):
        decision = V.review(hypothesis=f"h{bad}", parent_version="current", scope=["friday/a.py"],
                            origin="new_model", reasons=[_reason(baseline=bad, target=0.1)],
                            archive=arch)
        assert decision.allowed is False and decision.archived_as
    assert len(arch.query(decision=REJECT)) == 3


def _allowed(tmp_path, reason):
    return V.review(hypothesis=str(reason), parent_version="current", scope=["friday/a.py"],
                    origin="trigger", reasons=[reason],
                    archive=EvolutionArchive(tmp_path / "a")).allowed


def test_a_rate_near_one_is_scored_by_the_shortfall_it_removes(tmp_path):
    # 0.95 -> 0.999 removes 98% of the failures; plain relative change would call it 5%.
    assert _allowed(tmp_path, _reason(V.RELIABILITY, 0.95, 0.999, "success rate"))
    assert _allowed(tmp_path, _reason(V.TASK_SUCCESS, 0.92, 0.99, "pass rate"))
    gains = V.evaluate([_reason(V.RELIABILITY, 0.95, 0.999, "success rate")]).gains
    assert gains[V.RELIABILITY] == pytest.approx(0.98)
    # a rate that was already perfect has no headroom, and a tiny step is still tiny
    assert not _allowed(tmp_path, _reason(V.RELIABILITY, 1.0, 1.0, "success rate"))
    assert not _allowed(tmp_path, _reason(V.RELIABILITY, 0.95, 0.951, "success rate"))


def test_a_zero_baseline_never_turns_a_tiny_target_into_a_material_gain(tmp_path):
    assert not _allowed(tmp_path, _reason(V.RELIABILITY, 0, 1e-9, "success rate"))
    assert not _allowed(tmp_path, _reason(V.TASK_SUCCESS, 0, 0.01, "pass rate"))
    # off the rate scale a zero baseline is unmeasurable, not an infinite gain
    zero = V.evaluate([_reason(V.TASK_SUCCESS, 0, 5, "tasks finished per day")])
    assert zero.allowed is False and "cannot be measured relatively" in zero.reason
    # from zero to a real share of a rate is a real gain
    assert _allowed(tmp_path, _reason(V.TASK_SUCCESS, 0, 0.5, "pass rate"))
    # above one the metric is a count: relative change applies
    assert _allowed(tmp_path, _reason(V.TASK_SUCCESS, 10, 15, "tasks finished per day"))


def test_a_repeat_release_without_reasons_does_not_reject_an_admitted_proposal(tmp_path):
    store, archive = _env(tmp_path)
    admitted = C.consider(store, _release([_reason()]), archive=archive)
    assert admitted.proposed and admitted.created
    repeat = C.consider(store, _release([], component="friday/other.py"), archive=archive)
    assert repeat.proposed is False and "already proposed" in repeat.reason
    assert archive.query() == []                              # no REJECT for a live hypothesis
    kept = C.proposals(store)
    assert len(kept) == 1 and kept[0]["components"] == ["friday/retrieval.py"]


def test_the_gate_never_reaches_a_model_or_paid_client():
    """The trap above only bites a path that goes through the gateway; this pins that the
    gate and the controller hook cannot even import one."""
    import ast
    import inspect
    banned = {"model_gateway", "openai", "anthropic", "requests", "httpx", "urllib", "socket"}
    for module in (V, C):
        for node in ast.walk(ast.parse(inspect.getsource(module))):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""] + [a.name for a in node.names]
            for name in names:
                assert not banned & set(name.split(".")), (module.__name__, name)
