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


# ---- measured report (RULING 2026-10-13: real outcomes, not labels) --------

from friday import task_class as TC  # noqa: E402

E, S, D = ee.TIER_ECONOMY, ee.TIER_STANDARD, ee.TIER_DEEP


def _rec(outcomes, run_id, tier, *, cls=TC.STANDARD, status='COMPLETE',
         verification='tests-pass', rework=0, tokens=1000, provider='anthropic'):
    outcomes.record(run_id, task_class=cls, route_level=ee.HERMES_SINGLE,
                    tier=tier, provider=provider, prompt_tokens=tokens,
                    status=status, verification=verification, rework=rework)


def test_measured_report_ignores_synthetic_rows_and_label(tmp_path):
    db = tmp_path / 'm.sqlite3'
    outcomes = ee.RouteOutcomes(db)
    for i in range(3):
        _rec(outcomes, f'golden/{i}', E, provider='golden-eval')
    _rec(outcomes, 'real/0', E)
    _rec(outcomes, 'real/0/verify', E)
    _rec(outcomes, 'real/1/verify', E)
    report = gate.measured_report(db)
    std = report[TC.STANDARD]
    assert std['tiers'][E]['n'] == 1
    assert std['status'] == 'UNMEASURED'
    assert std['measured_min_tier'] is None
    assert set(report) == set(TC.TASK_CLASSES)
    # the human labels are stratification only: nothing measured, no verdict
    assert gate.label_vs_measured(report) == []


def test_measured_report_is_unmeasured_below_min_runs(tmp_path):
    db = tmp_path / 'm.sqlite3'
    report = gate.measured_report(db)
    assert {c['status'] for c in report.values()} == {'UNMEASURED'}
    outcomes = ee.RouteOutcomes(db)
    for i in range(ee.MIN_RANKED_RUNS - 1):
        _rec(outcomes, f'r/{i}', E)
    std = gate.measured_report(db)[TC.STANDARD]
    assert std['status'] == 'UNMEASURED'
    assert std['measured_min_tier'] is None
    assert std['tiers'][E]['first_pass_rate'] == 1.0


def test_unverified_rows_are_not_success(tmp_path):
    db = tmp_path / 'm.sqlite3'
    outcomes = ee.RouteOutcomes(db)
    for i in range(3):
        _rec(outcomes, f'r/{i}', E, verification='')
    std = gate.measured_report(db)[TC.STANDARD]
    tier = std['tiers'][E]
    assert std['status'] == 'UNVERIFIED'
    assert std['measured_min_tier'] is None
    assert tier['verified'] == 0 and tier['first_pass_rate'] == 0.0
    assert tier['completion_rate'] == 1.0
    assert tier['cost_per_success'] == float('inf')


def test_measured_min_tier_picks_cheapest_tier_meeting_threshold(tmp_path):
    db = tmp_path / 'm.sqlite3'
    outcomes = ee.RouteOutcomes(db)
    _rec(outcomes, 'e/0', E)
    _rec(outcomes, 'e/1', E)
    _rec(outcomes, 'e/2', E, status='FAILED')
    for tier in (S, D):
        for i in range(3):
            _rec(outcomes, f'{tier}/{i}', tier)
    report = gate.measured_report(db)
    std = report[TC.STANDARD]
    assert std['status'] == 'MEASURED'
    assert std['measured_min_tier'] == S
    suite = (gate.Task('upd', 'update the retry limit', S),
             gate.Task('imp', 'remove the unused import', E))
    diff = gate.label_vs_measured(report, suite=suite)
    assert diff == [{'task': 'imp', 'task_class': TC.STANDARD,
                     'label': E, 'measured': S}]


def test_rework_and_verify_rows_raise_measured_cost(tmp_path):
    db = tmp_path / 'm.sqlite3'
    outcomes = ee.RouteOutcomes(db)
    for i in range(3):
        _rec(outcomes, f'r/{i}', E)
    assert gate.measured_report(db)[TC.STANDARD]['tiers'][E]['cost_per_success'] == 1000
    _rec(outcomes, 'r/0', E, rework=1)
    _rec(outcomes, 'r/0/verify', E, tokens=300)
    tier = gate.measured_report(db)[TC.STANDARD]['tiers'][E]
    assert tier['n'] == 3 and tier['verified'] == 3
    assert tier['verification_cost'] == 300
    assert tier['first_pass_rate'] == 2 / 3
    assert tier['cost_per_success'] == (3000 + 1000 + 1000 + 300) / 3
