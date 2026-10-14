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


def test_a_word_containing_count_is_not_mechanical():
    """'counter' is not the literal command 'count': a code fix must not
    take the zero-model route."""
    econ, route = classify_and_route('Fix the off-by-one error in the retry counter.')
    assert econ.kind != 'mechanical'
    assert route.level != ee.DETERMINISTIC


def test_make_and_stop_requests_are_work_not_questions():
    """'make X survive Y' and 'stop X from Y' carry no verb the first
    classifier listed, so they fell through to 'question' and were answered
    on the economy tier with no worker."""
    for text in ('Make the voice reconnect logic survive a flaky network.',
                 'Stop the weather cache from serving stale results after midnight.',
                 'Can you please make the digest loop retry on timeout?'):
        econ, route = classify_and_route(text)
        assert econ.kind == 'code_change', text
        assert route.level != ee.FRIDAY_DIRECT, text


def test_make_and_stop_inside_a_question_stay_direct_answers():
    for text in ('What makes a good cache key?',
                 'How do I stop a thread in Python?',
                 'Why does the cache stop working at midnight?'):
        econ, route = classify_and_route(text)
        assert econ.kind == 'question', text
        assert route.level == ee.FRIDAY_DIRECT, text


def test_update_remove_replace_requests_are_work_not_questions():
    """'update/remove/replace/...' at a clause start are edits to something,
    not questions: they carried no verb the classifier listed, so they fell
    through to 'question' and were answered with no worker."""
    for text in ('update the retry limit in the weather tool',
                 'replace the stale cache key',
                 'remove the unused import',
                 'Rename the helper to something clearer.',
                 'Extend the digest loop to cover weekends.',
                 'Migrate the retry helper to the shared client.',
                 'Move the retry helper into the shared module.',
                 'Can you please update the retry limit?'):
        assert ee.wants_a_change(text), text
        econ, route = classify_and_route(text)
        assert econ.kind == 'code_change', text
        assert route.level != ee.FRIDAY_DIRECT, text


def test_update_remove_replace_inside_a_question_stay_direct_answers():
    for text in ('what does update do',
                 'how do I remove a file',
                 'Why does replace return a copy?'):
        assert not ee.wants_a_change(text), text
        econ, route = classify_and_route(text)
        assert econ.kind == 'question', text
        assert route.level == ee.FRIDAY_DIRECT, text


def test_a_literal_rename_or_move_file_command_stays_mechanical():
    """'rename file' / 'move file' are in the mechanical table as commands;
    the work verbs 'rename'/'move' must not swallow them."""
    for text in ('rename file notes.txt', 'move file notes.txt'):
        econ, route = classify_and_route(text)
        assert econ.kind == 'mechanical', text
        assert route.level == ee.DETERMINISTIC, text


def test_a_work_verb_beats_a_mechanical_phrase():
    """'count' is a mechanical command only when it is the request; as part
    of what is being fixed it is a code change, not a zero-model count."""
    for text in ('Fix the word count display.',
                 'Write a script to count the files in the docs folder.'):
        econ, route = classify_and_route(text)
        assert econ.kind == 'code_change', text
        assert route.level != ee.DETERMINISTIC, text
        assert econ.blast_radius != 'tiny', text


def test_a_mechanical_command_that_mentions_a_work_word_stays_mechanical():
    econ, route = classify_and_route('How many files in the docs folder have a fix in the name?')
    assert econ.kind == 'mechanical'
    assert route.level == ee.DETERMINISTIC


def test_reasoning_heavy_question_is_not_answered_directly():
    """No work verb makes a request a 'question', but its architecture /
    proof signals still mark it integration scope - the router must honour
    that instead of answering at the economy tier."""
    for text in ('Design the architecture for exactly-once delivery of objective events.',
                 'Prove the confirmation nonce cannot be replayed across actions.'):
        econ, route = classify_and_route(text)
        assert econ.kind == 'question' and econ.blast_radius == 'integration'
        assert route.level == ee.HERMES_SINGLE
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


def test_by_run_prefix_is_a_literal_namespace_match(tmp_path):
    outcomes = ee.RouteOutcomes(tmp_path / 'p.sqlite3')
    for run in ('eval-v1/b', 'eval-v1/a', 'eval-v2/a', 'evalXv1/a', 'other'):
        outcomes.record(run, task_class='x', route_level='hermes_single', tier='economy', status='COMPLETE')
    assert [r['work_run_id'] for r in outcomes.by_run_prefix('eval-v1/')] == ['eval-v1/a', 'eval-v1/b']
    assert outcomes.by_run_prefix('eval%') == []
    assert outcomes.by_run_prefix('eval-v1/', limit=1)[0]['work_run_id'] == 'eval-v1/a'


def test_failed_runs_have_zero_value(tmp_path):
    outcomes = ee.RouteOutcomes(tmp_path / 'f.sqlite3')
    outcomes.record('hermes-f', task_class='x', route_level='hermes_single', tier='economy', prompt_tokens=10, status='FAILED')
    row = outcomes.by_class('x')[0]
    assert outcomes.execution_value(row) == 0.0


def test_removal_is_classified_by_target_not_verb():
    """COG-002 RULING 2026-10-13: the target decides, and an unknown target
    fails closed to high consequence."""
    assert ee.removal_consequence('remove the unused import') == 'low'
    assert ee.removal_consequence('delete the debug print from server.py') == 'low'
    assert ee.removal_consequence('remove the foo') == 'high'
    assert ee.removal_consequence('remove the production database') == 'high'
    assert ee.removal_consequence('remove the unused import and delete the backups') == 'high'
    assert ee.removal_consequence('i want you to delete the production database') == 'high'
    assert ee.removal_consequence('can you delete the old backups') == 'high'
    assert ee.removal_consequence('how do I remove a file') == ''
    assert ee.removal_consequence('update the retry limit') == ''
    econ = ee.classify_task('remove the production database')
    assert 'removal' in econ.signals and econ.consequence == 'high'
    econ = ee.classify_task('delete the unused import')
    assert 'removal' not in econ.signals and econ.consequence != 'high'
