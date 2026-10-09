"""ULTRA-001 / ULTRA-002: an ultracode workflow is validated data, and a stage
result is cached by what produced it. Pure Python: no model call is made."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from friday.store import Store
from friday.ultracode import cache as C
from friday.ultracode import spec as S

DEFAULT = Path(__file__).resolve().parent.parent / "docs/plans/ultracode/workflows/default.json"

_SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}},
           "required": ["ok"]}


def _good() -> dict:
    return {
        "id": "demo",
        "budget_tokens": 120000,
        "stages": [
            {"id": "scan", "template": "lookup", "barrier": False,
             "schema": copy.deepcopy(_SCHEMA), "tier": "economy",
             "effort": "low", "cap": 8000, "dry_rounds": 1},
            {"id": "judge", "template": "judge", "barrier": True,
             "schema": copy.deepcopy(_SCHEMA), "tier": "deep",
             "effort": "high", "cap": 30000, "dry_rounds": 0},
        ],
    }


def _bad(mutate) -> S.SpecError:
    raw = _good()
    mutate(raw)
    with pytest.raises(S.SpecError) as caught:
        S.parse_spec(raw)
    return caught.value


# --- the spec -----------------------------------------------------------------

def test_a_spec_round_trips_through_json():
    spec = S.parse_spec(_good())
    again = S.parse_spec(json.loads(json.dumps(spec.to_dict())))
    assert again == spec
    assert spec.to_dict() == _good()


def test_the_shipped_default_workflow_validates():
    spec = S.load_spec(DEFAULT)
    assert spec.budget_tokens > 0 and spec.stages


def test_one_spec_through_both_entry_points_compiles_to_equal_plans():
    by_path = S.compile_plan(S.load_spec(DEFAULT))
    by_text = S.compile_plan(S.parse_spec(DEFAULT.read_text(encoding="utf-8")))
    by_dict = S.compile_plan(S.parse_spec(json.loads(DEFAULT.read_text(encoding="utf-8"))))
    assert by_path == by_text == by_dict


def test_unknown_tier_names_the_field():
    err = _bad(lambda r: r["stages"][0].update(tier="ultra"))
    assert err.field == "stages[0].tier"


def test_missing_schema_names_the_field():
    err = _bad(lambda r: r["stages"][1].pop("schema"))
    assert err.field == "stages[1].schema"


def test_missing_budget_names_the_field():
    err = _bad(lambda r: r.pop("budget_tokens"))
    assert err.field == "budget_tokens"


@pytest.mark.parametrize("budget", [0, -5, True, "9"])
def test_a_budget_that_is_not_a_positive_integer_is_refused(budget):
    assert _bad(lambda r: r.update(budget_tokens=budget)).field == "budget_tokens"


def test_two_deep_stages_are_refused():
    def two(r):
        r["stages"][0].update(tier="deep", template="synthesis")
    assert _bad(two).field == "stages[1].tier"


def test_a_deep_stage_on_a_lookup_is_refused():
    err = _bad(lambda r: r["stages"][1].update(template="lookup"))
    assert err.field == "stages[1].tier"


@pytest.mark.parametrize("template", ["judge", "synthesis", "architecture"])
def test_deep_is_allowed_on_judge_synthesis_architecture(template):
    raw = _good()
    raw["stages"][1]["template"] = template
    assert S.parse_spec(raw).stages[1].tier == "deep"


def test_a_malformed_schema_names_the_field():
    err = _bad(lambda r: r["stages"][0]["schema"].update(type="blob"))
    assert err.field == "stages[0].schema.type"
    err = _bad(lambda r: r["stages"][0]["schema"].update(required=["nope"]))
    assert err.field == "stages[0].schema.required"
    err = _bad(lambda r: r["stages"][0]["schema"].update(pattern="x"))
    assert err.field == "stages[0].schema.pattern"


def test_duplicate_stage_ids_unknown_keys_and_bad_scalars_are_refused():
    assert _bad(lambda r: r["stages"][1].update(id="scan")).field == "stages[1].id"
    assert _bad(lambda r: r.update(extra=1)).field == "extra"
    assert _bad(lambda r: r["stages"][0].update(barrier="yes")).field == "stages[0].barrier"
    assert _bad(lambda r: r["stages"][0].update(effort="max")).field == "stages[0].effort"
    assert _bad(lambda r: r["stages"][0].update(cap=0)).field == "stages[0].cap"
    assert _bad(lambda r: r["stages"][0].update(dry_rounds=-1)).field == "stages[0].dry_rounds"
    assert _bad(lambda r: r.update(stages=[])).field == "stages"


def test_malformed_json_text_is_a_spec_error():
    with pytest.raises(S.SpecError) as caught:
        S.parse_spec("{not json")
    assert caught.value.field == "$"


# --- the result cache -----------------------------------------------------------

@pytest.fixture
def store(tmp_path):
    opened = Store(tmp_path / "ultracode.sqlite3")
    yield opened
    opened.close()


def _key(**over):
    args = dict(spec_id="demo", stage_id="scan", input_refs=["a", "b"],
                model="sonnet", schema=_SCHEMA)
    args.update(over)
    return C.result_key(**args)


def test_the_key_is_stable_and_moves_with_every_input():
    assert _key() == _key(schema=copy.deepcopy(_SCHEMA))
    assert _key() == _key(schema={"required": ["ok"], "properties": {"ok": {"type": "boolean"}},
                                  "type": "object"})
    assert len(_key()) == 64
    for change in ({"spec_id": "other"}, {"stage_id": "judge"}, {"input_refs": ["a", "c"]},
                   {"model": "opus"}, {"schema": {"type": "string"}}):
        assert _key(**change) != _key()


def test_a_hit_returns_without_dispatch_and_a_miss_dispatches_once(store):
    cache = C.ResultCache(store, workspace_id="w1")
    calls = []

    def dispatch():
        calls.append(1)
        return {"ok": True}

    assert cache.get(_key()) is None
    first = cache.get_or_run(_key(), dispatch)
    second = cache.get_or_run(_key(), dispatch)
    assert (first.hit, second.hit) == (False, True)
    assert first.result == second.result == {"ok": True}
    assert len(calls) == 1


def test_a_cache_hit_survives_reopening_the_store(tmp_path):
    path = tmp_path / "again.sqlite3"
    one = Store(path)
    C.ResultCache(one, workspace_id="w1").put(_key(), {"ok": True})
    one.close()
    two = Store(path)
    try:
        assert C.ResultCache(two, workspace_id="w1").get(_key()) == {"ok": True}
    finally:
        two.close()


def test_a_result_is_private_to_its_workspace(store):
    C.ResultCache(store, workspace_id="w1").put(_key(), {"ok": True})
    assert C.ResultCache(store, workspace_id="w2").get(_key()) is None
