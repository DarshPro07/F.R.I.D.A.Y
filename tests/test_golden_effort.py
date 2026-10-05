"""COG-002: the adaptive reasoning-effort policy is measured against an
always-deep baseline on a fixed labelled suite (scripts/golden_effort.py)."""
import json

import pytest

from friday import execution_economics as ee
from scripts import golden_effort as gate


@pytest.fixture(scope='module')
def report():
    return gate.build_report()


def test_adaptive_beats_always_deep_at_the_quality_threshold(report):
    adaptive = report['policies']['adaptive']
    deep = report['policies']['always_deep']
    verdict = report['verdict']
    assert adaptive['cost_adjusted'] < deep['cost_adjusted']
    assert adaptive['quality'] >= gate.QUALITY_THRESHOLD
    assert adaptive['floor_violations'] == []
    assert verdict['beats_always_deep'] is True
    assert verdict['savings_pct'] > 0


def test_always_deep_is_the_quality_ceiling(report):
    deep = report['policies']['always_deep']
    assert deep['quality'] == 1.0
    assert deep['under_provisioned'] == []


def test_the_threshold_has_teeth_an_all_economy_policy_fails_it(report):
    """Cheap-first without adapting is rejected: the gate is not vacuous."""
    economy = report['policies']['always_economy']
    assert economy['quality'] < gate.QUALITY_THRESHOLD
    assert economy['floor_violations']
    assert not gate.judge(economy, report['policies']['always_deep'])['beats_always_deep']


def test_adaptive_actually_adapts_effort(report):
    efforts = report['policies']['adaptive']['efforts']
    assert set(efforts) == {ee.resolve_effort(t) for t in (ee.TIER_ECONOMY, ee.TIER_STANDARD, ee.TIER_DEEP)}
    assert report['policies']['always_deep']['efforts'] == {ee.resolve_effort(ee.TIER_DEEP): len(gate.SUITE)}


def test_a_policy_blind_to_consequence_is_caught():
    """Routing everything to standard saves money by under-serving every
    deep-labelled task: the safety floor must fail it."""
    def always_standard(task):
        return ee.HERMES_SINGLE, ee.TIER_STANDARD

    summary = gate.evaluate(always_standard)['summary']
    deep_ids = {t.id for t in gate.SUITE if t.min_tier == ee.TIER_DEEP}
    assert set(summary['floor_violations']) == deep_ids
    deep = gate.evaluate('always_deep')['summary']
    assert not gate.judge(summary, deep)['beats_always_deep']


@pytest.mark.parametrize('ladder', [(1, 2, 3), (1, 3, 5), (1, 5, 25)])
def test_the_saving_does_not_hang_on_one_price_ladder(ladder):
    prices = dict(zip((ee.TIER_ECONOMY, ee.TIER_STANDARD, ee.TIER_DEEP), ladder))
    adaptive = gate.evaluate('adaptive', prices=prices)['summary']
    deep = gate.evaluate('always_deep', prices=prices)['summary']
    assert adaptive['cost_adjusted'] < deep['cost_adjusted']


def test_the_suite_is_well_formed():
    ids = [t.id for t in gate.SUITE]
    assert len(ids) == len(set(ids))
    assert {t.min_tier for t in gate.SUITE} == set(gate.TIER_RANK)
    assert all(t.text.strip() for t in gate.SUITE)


def test_outcomes_are_recorded_and_read_back_from_the_ledger(tmp_path):
    db = tmp_path / 'golden.sqlite3'
    report = gate.build_report(db)
    outcomes = ee.RouteOutcomes(db)
    prefix = f'golden-effort-v{gate.SUITE_VERSION}/'
    for name in gate.POLICIES:
        records = outcomes.by_run_prefix(prefix + name + '/')
        assert len(records) == len(gate.SUITE)
        assert {r['provider'] for r in records} == {'golden-eval'}
    adaptive = outcomes.by_run_prefix(prefix + 'adaptive/')
    failed = {r['work_run_id'].rsplit('/', 1)[1] for r in adaptive if r['status'] == 'FAILED'}
    assert failed == set(report['policies']['adaptive']['under_provisioned'])
    assert all(outcomes.execution_value(r) == 0.0 for r in adaptive if r['status'] == 'FAILED')
    assert report['policies']['adaptive']['ledger_value'] > 0


def test_the_default_run_never_touches_the_live_ledger(tmp_path, monkeypatch):
    monkeypatch.setenv('ADA_DB', str(tmp_path / 'live.sqlite3'))
    gate.build_report()
    assert not (tmp_path / 'live.sqlite3').exists()


def test_the_frozen_baseline_matches_a_fresh_run(report):
    baseline = json.loads(gate.BASELINE.read_text(encoding='utf-8'))
    assert gate.compare_to_baseline(report, baseline) == []
    assert baseline == json.loads(json.dumps(gate.frozen(report)))


def test_a_worse_adaptive_policy_is_reported_as_a_regression(report):
    baseline = json.loads(gate.BASELINE.read_text(encoding='utf-8'))
    worse = json.loads(json.dumps(report))
    worse['policies']['adaptive']['quality'] -= 0.1
    worse['policies']['adaptive']['cost_adjusted'] += 1
    worse['policies']['adaptive']['floor_violations'] = ['scheduler-crash']
    problems = gate.compare_to_baseline(worse, baseline)
    assert len(problems) == 3
    worse['suite_version'] += 1
    assert 're-freeze' in gate.compare_to_baseline(worse, baseline)[0]
