"""First-run contract - adaptive wizard, no questionnaire prison,
policy unification."""
from friday import first_run as fr
from friday.user_policy import UserPolicy


def make(tmp_path):
    policy = UserPolicy(tmp_path / 'policy.sqlite3')
    return (fr.FirstRunContract(tmp_path, policy=policy), policy)


def test_fresh_profile_asks_all_nine(tmp_path):
    contract, _ = make(tmp_path)
    assert not contract.exists()
    assert len(contract.pending_questions()) == 9


def test_known_context_skips_questions(tmp_path):
    contract, _ = make(tmp_path)
    pending = contract.pending_questions(known={'call_me': 'Boss'})
    assert all((q['id'] != 'name' for q in pending))
    assert len(pending) == 8


def test_answers_persist_and_stop_reasking(tmp_path):
    contract, _ = make(tmp_path)
    contract.record({'call_me': 'Boss', 'token_mode': 'automatic'})
    assert contract.exists()
    pending = contract.pending_questions()
    ids = {q['id'] for q in pending}
    assert 'name' not in ids and 'cost' not in ids
    assert len(pending) == 7


def test_permission_answers_reach_user_policy(tmp_path):
    """The unification rule: contract answers and runtime permission
    checks can never disagree because they are the same store."""
    contract, policy = make(tmp_path)
    out = contract.record({'computer_authority': 'yes', 'auto_comms': 'email and social'})
    assert 'workspace_access' in out['applied_domains']
    assert policy.state_of('workspace_access') == 'AUTO'
    assert policy.state_of('customer_email') == 'AUTO'
    assert policy.state_of('social_publish') == 'AUTO'
    trail = policy.audit_trail('workspace_access')
    assert 'first-run contract' in trail[0]['reason']


def test_restrictive_answers_stay_confirm(tmp_path):
    contract, policy = make(tmp_path)
    contract.record({'computer_authority': 'ask-first', 'auto_comms': 'none'})
    assert policy.state_of('workspace_access') == 'CONFIRM'
    assert policy.state_of('customer_email') == 'CONFIRM'
    assert policy.state_of('social_publish') == 'CONFIRM'


def test_natural_adaptation_updates_later(tmp_path):
    """'I trust you to publish without asking' - months later, in plain
    conversation."""
    contract, policy = make(tmp_path)
    contract.record({'auto_comms': 'none'})
    assert policy.state_of('social_publish') == 'CONFIRM'
    contract.adapt('auto_comms', 'social publishing is fine now', reason='user said: I trust you to publish without asking')
    assert policy.state_of('social_publish') == 'AUTO'


def test_deleted_contract_reasks(tmp_path):
    contract, _ = make(tmp_path)
    contract.record({'call_me': 'Boss'})
    contract.path.unlink()
    assert len(contract.pending_questions()) == 9