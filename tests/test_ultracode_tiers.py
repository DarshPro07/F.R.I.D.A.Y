"""ULTRA-006: each ultracode stage gets the cheapest capable model tier, and
within a tier the model is ranked by measured cost per verified completion
through the one ranker `build_kernel.pick_model` also uses (FAB-003)."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from friday import execution_economics as EE
from friday.ultracode import spec as S
from friday.ultracode import tiers as T

DEFAULT = Path(__file__).resolve().parent.parent / "docs/plans/ultracode/workflows/default.json"
_SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}


def _stage(sid: str, template: str, tier: str) -> dict:
    return {"id": sid, "template": template, "schema": copy.deepcopy(_SCHEMA),
            "tier": tier, "cap": 1000}


def _spec(*stages: dict) -> dict:
    return {"id": "t", "budget_tokens": 50000, "stages": list(stages)}


def _plan(*stages: dict) -> dict:
    return S.compile_plan(S.parse_spec(_spec(*stages)))


# --- tier resolution ------------------------------------------------------------

def test_default_spec_resolves_economy_standard_and_one_deep():
    spec = S.load_spec(DEFAULT)
    tiers = T.resolve_tiers(spec)
    assert tiers == {"scan": "economy", "build": "standard", "verdict": "deep"}
    assert list(tiers.values()).count("deep") == 1
    assert S.compile_plan(spec)["stages"][0]["tier"] == "economy"


@pytest.mark.parametrize("template,tier", [
    ("lookup", "economy"), ("check", "economy"), ("skeptic", "economy"),
    ("builder", "standard"), ("code", "standard"), ("producer", "standard"),
    ("judge", "deep"), ("synthesis", "deep"), ("architecture", "deep"),
])
def test_each_template_has_one_cheapest_capable_tier(template, tier):
    assert T.tier_for(template) == tier
    assert _plan(_stage("a", template, tier))["stages"][0]["tier"] == tier


def test_an_unknown_template_keeps_the_tier_the_spec_declares():
    assert _plan(_stage("a", "triage", "standard"))["stages"][0]["tier"] == "standard"


@pytest.mark.parametrize("template,declared", [
    ("lookup", "standard"), ("skeptic", "standard"), ("check", "standard"),
    ("builder", "economy"),
])
def test_a_tier_other_than_the_cheapest_capable_is_rejected(template, declared):
    with pytest.raises(S.SpecError) as caught:
        _plan(_stage("a", template, declared))
    assert caught.value.field == "stages[0].tier"
    assert T.tier_for(template) in str(caught.value)


def test_deep_on_a_lookup_stage_is_rejected():
    with pytest.raises(S.SpecError) as caught:
        _plan(_stage("a", "lookup", "deep"))
    assert caught.value.field == "stages[0].tier"


def test_two_deep_agents_are_rejected():
    with pytest.raises(S.SpecError) as caught:
        _plan(_stage("a", "judge", "deep"), _stage("b", "synthesis", "deep"))
    assert caught.value.field == "stages[1].tier"


@pytest.mark.parametrize("first,second", [
    ("judge", "synthesis"), ("synthesis", "judge"), ("architecture", "judge"),
])
def test_deep_is_a_ceiling_so_one_deep_and_one_standard_deep_template_compile(first, second):
    plan = _plan(_stage("a", first, "deep"), _stage("b", second, "standard"))
    assert [s["tier"] for s in plan["stages"]] == ["deep", "standard"]
    plan = _plan(_stage("a", first, "standard"), _stage("b", second, "deep"))
    assert [s["tier"] for s in plan["stages"]] == ["standard", "deep"]


def test_two_standard_judge_stages_compile_with_no_deep_agent():
    plan = _plan(_stage("a", "judge", "standard"), _stage("b", "synthesis", "standard"))
    assert [s["tier"] for s in plan["stages"]] == ["standard", "standard"]


@pytest.mark.parametrize("template", ["judge", "synthesis", "architecture"])
def test_a_deep_template_never_runs_on_economy(template):
    with pytest.raises(S.SpecError) as caught:
        _plan(_stage("a", template, "economy"))
    assert caught.value.field == "stages[0].tier"
    assert "standard" in str(caught.value) and "deep" in str(caught.value)


def test_the_deep_limit_is_held_by_the_resolver_too():
    spec = S.parse_spec(_spec(_stage("a", "judge", "deep")))
    twice = S.Spec(id="t", budget_tokens=1, stages=spec.stages * 2)
    with pytest.raises(S.SpecError):
        T.resolve_tiers(twice)


# --- model choice within a tier -------------------------------------------------

def _row(n, wins, cost):
    return {"n": n, "wins": wins, "cost": cost}


def test_cheaper_model_of_equal_verified_quality_wins_and_cites_evidence():
    bench = {"haiku-4": _row(4, 4, 2.0), "haiku-3": _row(4, 4, 0.8)}
    choice = T.choose_model("economy", ["haiku-4", "haiku-3"], bench)
    assert choice.model == "haiku-3"
    assert choice.tier == "economy"
    by_model = {row["model"]: row for row in choice.evidence}
    assert by_model["haiku-3"]["cost_per_win"] == pytest.approx(0.2)
    assert by_model["haiku-4"]["cost_per_win"] == pytest.approx(0.5)
    assert by_model["haiku-3"]["wins"] == 4 and by_model["haiku-3"]["n"] == 4


def test_a_cheap_model_below_the_quality_floor_does_not_win():
    bench = {"dear": _row(5, 5, 10.0), "cheap": _row(5, 1, 0.5)}
    choice = T.choose_model("standard", ["dear", "cheap"], bench)
    assert choice.model == "dear"
    assert [row["model"] for row in choice.evidence] == ["dear"]


def test_a_model_with_too_few_outcomes_is_not_trusted():
    bench = {"a": _row(2, 2, 0.1), "b": _row(3, 3, 9.0)}
    choice = T.choose_model("standard", ["a", "b"], bench)
    assert choice.model == "b"


def test_with_no_evidence_the_first_candidate_is_chosen_and_says_so():
    choice = T.choose_model("deep", ["opus", "other"], {})
    assert choice.model == "opus" and choice.evidence == ()
    assert "no measured" in choice.reason


def test_no_candidates_is_an_error():
    with pytest.raises(ValueError):
        T.choose_model("economy", [], {})


def test_the_ranker_is_the_one_build_kernel_uses(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parent.parent / "scripts"))
    import build_kernel as K
    assert K.cheapest_capable is EE.cheapest_capable
    assert T.cheapest_capable is EE.cheapest_capable
