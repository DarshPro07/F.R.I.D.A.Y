"""MODEL-003: the baseline a learned model router has to beat.

No learned router exists. These tests pin the harness a future one is judged
by: the production router is the baseline, the labelled suite is split into a
development half and a locked holdout, and `arena.promote` refuses any
candidate that does not clear every gate on that evidence.
"""
import copy
import json

import pytest

from friday import arena
from friday import execution_economics as ee
from scripts import golden_effort as effort
from scripts import golden_model_router as gate

PROV = {"commit": "test", "date": "2026-10-05"}
E, S, D = ee.TIER_ECONOMY, ee.TIER_STANDARD, ee.TIER_DEEP


@pytest.fixture(scope="module")
def report():
    return gate.build_report(provenance=PROV)


@pytest.fixture
def store(tmp_path):
    return arena.ArenaStore(tmp_path / "arena_reports.json")


def _labels(tasks):
    by_text = {t.text: t.min_tier for t in tasks}
    return lambda text: by_text[text]


def _synthetic(per_tier=4):
    """A suite with headroom: tasks the labels say need each tier."""
    return tuple(effort.Task(f"{tier}-{n}", f"{tier} task number {n}", tier)
                 for tier in (E, S, D) for n in range(per_tier))


def _record(store, report):
    store.record(report)
    return report


def test_the_split_is_deterministic_disjoint_and_complete():
    first, second = gate.split_suite(), gate.split_suite()
    assert first == second
    dev, holdout = first[gate.DEVELOPMENT], first[gate.HOLDOUT]
    assert not {t.id for t in dev} & {t.id for t in holdout}
    assert {t.id for t in dev} | {t.id for t in holdout} == \
        {t.id for t in effort.SUITE}


def test_the_holdout_meets_the_minimum_and_covers_every_tier():
    holdout = gate.split_suite()[gate.HOLDOUT]
    assert len(holdout) >= gate.MINIMUM_CASES
    assert {t.min_tier for t in holdout} == {E, S, D}


def test_a_task_keeps_its_split_when_the_suite_grows():
    extra = effort.Task("brand-new", "a task added later", S)
    before = gate.split_suite()
    after = gate.split_suite(effort.SUITE + (extra,))
    for name in (gate.DEVELOPMENT, gate.HOLDOUT):
        assert set(before[name]) <= set(after[name])


def test_the_baseline_is_the_production_router():
    for task in effort.SUITE:
        assert gate.baseline_router(task.text) == \
            ee.choose_route(ee.classify_task(task.text)).tier


def test_the_baseline_only_report_is_deterministic(report):
    assert gate.build_report(provenance=PROV) == report


def test_the_baseline_only_report_has_no_learned_candidate(report):
    for name in (gate.DEVELOPMENT, gate.HOLDOUT):
        ids = [r["candidate_id"] for r in report["splits"][name]["results"]]
        assert ids == ["baseline", "always_deep", "always_economy"]
    assert report["learned_router"] == "none built"


def test_the_frozen_baseline_matches_a_fresh_run(report):
    frozen = json.loads(gate.BASELINE.read_text(encoding="utf-8"))
    assert gate.measurements(frozen) == gate.measurements(report)
    assert gate.compare_to_baseline(report, frozen) == []


def test_the_frozen_baseline_names_the_commit_and_date_it_was_cut_at():
    frozen = json.loads(gate.BASELINE.read_text(encoding="utf-8"))
    assert frozen["suite_version"] == gate.SUITE_VERSION
    assert frozen["provenance"]["commit"] and frozen["provenance"]["date"]


def test_the_development_report_does_not_open_the_holdout():
    only_dev = gate.build_report(splits=(gate.DEVELOPMENT,), provenance=PROV)
    assert list(only_dev["splits"]) == [gate.DEVELOPMENT]


def test_the_holdout_is_locked_on_the_command_line(tmp_path, monkeypatch):
    target = tmp_path / "baseline.json"
    monkeypatch.setattr(gate, "BASELINE", target)
    assert gate.main(["--freeze"]) == 2
    assert gate.main(["--holdout"]) == 2
    assert not target.exists()


def test_the_default_run_passes_against_the_frozen_baseline(capsys):
    assert gate.main([]) == 0
    out = capsys.readouterr().out
    assert "development" in out and "holdout" not in out


def test_the_baseline_cannot_promote_itself(store):
    fresh = _record(store, gate.compare_routers({}, tasks=_synthetic(),
                                                provenance=PROV))
    assert fresh.winner == ""
    with pytest.raises(arena.PromotionRefused, match="cost_below_baseline"):
        arena.promote(arena.ArenaKind.MODEL_ROUTE, "baseline",
                      report_id=fresh.report_id, store=store)


def test_a_candidate_that_only_matches_the_baseline_is_refused(store):
    holdout = gate.split_suite()[gate.HOLDOUT]
    fresh = _record(store, gate.compare_routers(
        {"copycat": gate.baseline_router}, tasks=holdout, provenance=PROV))
    assert fresh.result("copycat").failed_gates == ("cost_below_baseline",)
    with pytest.raises(arena.PromotionRefused, match="cost_below_baseline"):
        arena.promote(arena.ArenaKind.MODEL_ROUTE, "copycat",
                      report_id=fresh.report_id, store=store)


def test_a_candidate_strictly_better_than_the_baseline_promotes(store):
    tasks = _synthetic()

    def always_standard(text):                  # a weak baseline with rework
        return S

    fresh = _record(store, gate.compare_routers(
        {"learned": _labels(tasks)}, tasks=tasks, baseline=always_standard,
        provenance=PROV))
    result = fresh.result("learned")
    assert result.passed, result.failed_gates
    assert fresh.winner == "learned"
    promotion = arena.promote(arena.ArenaKind.MODEL_ROUTE, "learned",
                              report_id=fresh.report_id, store=store,
                              exclusive=True)
    assert promotion.report_id == fresh.report_id


def test_a_candidate_tuned_on_development_fails_the_locked_holdout(store):
    split = gate.split_suite()
    tuned_on = {t.text: t.min_tier for t in split[gate.DEVELOPMENT]}

    def overfit(text):
        return tuned_on.get(text, E)            # memorised dev, guesses cheap

    dev = gate.compare_routers({"overfit": overfit}, tasks=split[gate.DEVELOPMENT],
                               provenance=PROV)
    held = _record(store, gate.compare_routers(
        {"overfit": overfit}, tasks=split[gate.HOLDOUT], provenance=PROV))
    assert dev.result("overfit").passed         # looks like a winner on dev
    assert not held.result("overfit").passed
    with pytest.raises(arena.PromotionRefused):
        arena.promote(arena.ArenaKind.MODEL_ROUTE, "overfit",
                      report_id=held.report_id, store=store)


def test_a_cheaper_candidate_that_underroutes_a_deep_task_fails_the_floor(store):
    tasks = _synthetic()
    labels = _labels(tasks)
    skipped = next(t.text for t in tasks if t.min_tier == D)

    def sloppy_baseline(text):                  # wrong on every standard task
        return E if labels(text) == S else labels(text)

    def drops_one_deep(text):
        return S if text == skipped else labels(text)

    fresh = _record(store, gate.compare_routers(
        {"cheap": drops_one_deep}, tasks=tasks, baseline=sloppy_baseline,
        provenance=PROV))
    # Better quality and cheaper than the sloppy baseline, yet not promotable.
    assert fresh.result("cheap").failed_gates == ("no_deep_task_below_deep",)
    with pytest.raises(arena.PromotionRefused, match="no_deep_task_below_deep"):
        arena.promote(arena.ArenaKind.MODEL_ROUTE, "cheap",
                      report_id=fresh.report_id, store=store)


def test_a_candidate_below_baseline_quality_is_refused():
    tasks = _synthetic()
    fresh = gate.compare_routers({"all_cheap": lambda text: E}, tasks=tasks,
                                 baseline=lambda text: D, provenance=PROV)
    assert "quality_not_below_baseline" in fresh.result("all_cheap").failed_gates


@pytest.mark.parametrize("tasks", [(), _synthetic(per_tier=1)],
                         ids=["empty", "too-few"])
def test_an_empty_or_too_small_suite_cannot_promote(tasks, store):
    fresh = _record(store, gate.compare_routers(
        {"learned": _labels(_synthetic())}, tasks=tasks, provenance=PROV))
    assert "minimum_cases" in fresh.result("learned").failed_gates
    with pytest.raises(arena.PromotionRefused, match="minimum_cases"):
        arena.promote(arena.ArenaKind.MODEL_ROUTE, "learned",
                      report_id=fresh.report_id, store=store)


def test_a_suite_missing_a_tier_cannot_promote():
    only_cheap = tuple(effort.Task(f"e{n}", f"cheap task {n}", E)
                       for n in range(gate.MINIMUM_CASES + 2))
    fresh = gate.compare_routers({"learned": lambda text: E},
                                 tasks=only_cheap, provenance=PROV)
    assert "covers_every_tier" in fresh.result("learned").failed_gates


def test_a_router_naming_no_tier_fails_alone_and_the_rest_are_still_measured():
    fresh = gate.compare_routers({"broken": lambda text: "gpt-9"},
                                 tasks=_synthetic(), provenance=PROV)
    assert fresh.result("broken").failed_gates == ("scored",)
    assert fresh.result("baseline").cases == len(_synthetic())


@pytest.mark.parametrize("name", ["baseline", "always_deep", "always_economy"])
def test_a_candidate_cannot_borrow_a_reference_name(name):
    with pytest.raises(ValueError, match="reserved"):
        gate.compare_routers({name: gate.baseline_router}, tasks=_synthetic(),
                             provenance=PROV)


def test_headroom_says_how_much_a_better_router_could_save():
    weak = gate.compare_routers({}, tasks=_synthetic(),
                                baseline=lambda text: D, provenance=PROV)
    assert weak.result("baseline").metrics["cost_adjusted"] == \
        len(_synthetic()) * effort.TOKENS_PER_TASK * effort.TIER_PRICE[D]
    head = gate.headroom(_synthetic(), lambda text: D)
    assert head["oracle_cost"] < head["baseline_cost"]
    assert head["headroom_pct"] > 0
    assert gate.headroom(_synthetic(), _labels(_synthetic()))["headroom_pct"] == 0


def test_a_worse_baseline_is_reported_as_a_regression(report):
    frozen = copy.deepcopy(report)
    worse = copy.deepcopy(report)
    base = worse["splits"][gate.HOLDOUT]["results"][0]
    assert base["candidate_id"] == "baseline"
    base["metrics"]["cost_adjusted"] += 1.0
    base["metrics"]["quality"] -= 0.01
    problems = gate.compare_to_baseline(worse, frozen)
    assert any("cost rose" in p for p in problems)
    assert any("quality fell" in p for p in problems)
    stale = copy.deepcopy(frozen)
    stale["suite_version"] += 1
    assert "suite version" in gate.compare_to_baseline(report, stale)[0]
