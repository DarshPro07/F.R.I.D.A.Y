"""Phase H - execution economics. The gate's own acceptance table:

  simple QA         -> Friday direct, no worker
  mechanical        -> deterministic, ZERO model calls
  tiny bounded      -> economy tier
  auth/one-line     -> DEEP tier + FULL verification (risk beats size -
                       the spec's "change one authentication header" case)
  research/deep     -> deep tier
  multi-stream      -> parallel workers
  explicit override -> honored untouched
  no routing calls  -> classify/choose are pure functions (no model)

Plus H6: outcome records, rework economics, and the anti-pattern guard.
"""
import sqlite3
from friday import execution_economics as ee


def classify_and_route(text, **kw):
    econ = ee.classify_task(text, **kw)
    return (econ, ee.choose_route(econ))


def test_simple_question_stays_out_of_hermes():
    econ, route = classify_and_route('Explain in two sentences what a Python dictionary is.')
    assert route.level == ee.FRIDAY_DIRECT


def test_mechanical_work_uses_no_model():
    econ, route = classify_and_route('Count how many Python files exist under the friday directory.')
    assert econ.kind == 'mechanical'
    assert route.level == ee.DETERMINISTIC


def test_tiny_change_routes_economy():
    econ, route = classify_and_route('Fix the import path in tests/test_widgets.py - one line.')
    assert econ.blast_radius == 'tiny'
    assert route.tier == ee.TIER_ECONOMY
    assert route.level == ee.HERMES_SINGLE


def test_auth_change_is_high_consequence_despite_tiny_size():
    """The spec's decisive example: 'change one authentication header'
    is a tiny edit and must still route deep with full verification -
    risk weighting, not size weighting."""
    econ, route = classify_and_route('Change one authentication header in the login request.')
    assert econ.consequence == 'high'
    assert route.tier == ee.TIER_DEEP
    scope, reason = ee.verification_depth(econ)
    assert scope == ee.FULL
    assert reason


def test_architecture_reasoning_routes_deep():
    econ, route = classify_and_route('Evaluate whether the WorkCompletionBroker can produce duplicate semantic delivery after a crash - construct the failure timeline and challenge the exactly-once claim.')
    assert route.tier == ee.TIER_DEEP


def test_multi_stream_routes_parallel():
    econ, route = classify_and_route('Run two independent workstreams in parallel: Worker A inspects delivery, Worker B inspects token economy.')
    assert econ.kind == 'multi_stream'
    assert route.level == ee.HERMES_MULTI


def test_verification_is_priced_not_uniform():
    tiny, _ = classify_and_route('Fix a typo in the docstring.')
    local, _ = classify_and_route('Fix the fallback handling bug in the delivery drain loop.')
    assert ee.verification_depth(tiny)[0] == ee.TARGETED
    assert ee.verification_depth(local)[0] == ee.AFFECTED


def test_router_never_invents_models():
    """resolve_model returns a configured mapping or '' (profile
    default) - never a fabricated model name."""
    assert ee.resolve_model('nonexistent-tier') == ''
    for tier in (ee.TIER_ECONOMY, ee.TIER_STANDARD, ee.TIER_DEEP):
        model = ee.resolve_model(tier)
        assert isinstance(model, str)


def test_routing_costs_zero_model_calls():
    """The prohibited anti-pattern: an LLM call to decide which LLM to
    call. classify_task/choose_route are pure functions over strings -
    importable and callable with no gateway, no network, no model."""
    import inspect
    for fn in (ee.classify_task, ee.choose_route, ee.verification_depth, ee.resolve_model):
        source = inspect.getsource(fn)
        for banned in ('request(', 'prompt.submit', 'delegate(', 'session.create', 'urllib', 'httpx'):
            assert banned not in source, (fn.__name__, banned)


def test_outcomes_record_and_rework_economics(tmp_path):
    outcomes = ee.RouteOutcomes(tmp_path / 'o.sqlite3')
    outcomes.record('hermes-cheap', task_class='code_change', route_level='hermes_single', tier='economy', calls=3, prompt_tokens=20000, output_tokens=500, duration_s=30, status='COMPLETE')
    outcomes.record('hermes-solid', task_class='code_change', route_level='hermes_single', tier='deep', calls=5, prompt_tokens=45000, output_tokens=900, duration_s=60, status='COMPLETE')
    outcomes.mark_rework('hermes-cheap')
    rows = {r['work_run_id']: r for r in outcomes.by_class('code_change')}
    cheap = outcomes.execution_value(rows['hermes-cheap'])
    solid = outcomes.execution_value(rows['hermes-solid'])
    assert solid > cheap, '20k+rework must be worth less than 45k done right'


def test_failed_runs_have_zero_value(tmp_path):
    outcomes = ee.RouteOutcomes(tmp_path / 'f.sqlite3')
    outcomes.record('hermes-f', task_class='x', route_level='hermes_single', tier='economy', prompt_tokens=10, status='FAILED')
    row = outcomes.by_class('x')[0]
    assert outcomes.execution_value(row) == 0.0