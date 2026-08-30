"""User policy - delegation, revocation, constitutional immunity, audit."""
from friday import user_policy as up


def make(tmp_path):
    return up.UserPolicy(tmp_path / 'policy.sqlite3')


def test_defaults_match_north_star(tmp_path):
    policy = make(tmp_path)
    assert policy.state_of('research') == 'AUTO'
    assert policy.state_of('customer_email') == 'AUTO'
    assert policy.state_of('social_publish') == 'AUTO'
    assert policy.state_of('spend_money') == 'CONFIRM'
    assert policy.state_of('deploy_website') == 'CONFIRM'
    assert policy.state_of('buy_domain') == 'CONFIRM'


def test_grant_then_act_then_revoke(tmp_path):
    """The boss's runtime trust change: 'I trust you now - you may
    access my workspace' ... later ... 'actually, ask first again'."""
    policy = make(tmp_path)
    assert policy.state_of('workspace_access') == 'CONFIRM'
    policy.grant('workspace_access', 'AUTO', reason='I trust you now, you may access my workspace')
    assert policy.state_of('workspace_access') == 'AUTO'
    policy.grant('workspace_access', 'CONFIRM', reason='actually ask me first again')
    assert policy.state_of('workspace_access') == 'CONFIRM'
    trail = policy.audit_trail('workspace_access')
    assert len(trail) == 2
    assert trail[0]['reason'] == 'actually ask me first again'


def test_constitutional_classes_cannot_be_granted(tmp_path):
    policy = make(tmp_path)
    out = policy.grant('banking_observation', 'AUTO', reason='I trust you, banking is fine now')
    assert out['status'] == 'refused'
    assert policy.state_of('banking_observation') == 'DENY'
    trail = policy.audit_trail('banking_observation')
    assert trail and trail[0]['state'] == 'REFUSED'


def test_unknown_domain_rejected(tmp_path):
    out = make(tmp_path).grant('nuclear_launch', 'AUTO', reason='x')
    assert out['status'] == 'failed'


def test_versioning_never_destroys(tmp_path):
    policy = make(tmp_path)
    for i in range(5):
        policy.grant('spend_money', 'AUTO' if i % 2 else 'CONFIRM', reason=f"change {i}")
    assert len(policy.audit_trail('spend_money')) == 5


def test_snapshot_covers_all_domains(tmp_path):
    snap = make(tmp_path).snapshot()
    assert set(snap) == set(up.DOMAINS)


def test_no_envelope_means_confirm(tmp_path):
    policy = make(tmp_path)
    out = policy.can_spend(platform='google-ads', amount=500)
    assert out['decision'] == 'CONFIRM'


def test_spend_inside_envelope_is_auto(tmp_path):
    policy = make(tmp_path)
    env = policy.authorize_envelope(platform='google-ads', purpose='watch-launch', daily_cap=1000, total_cap=5000)
    out = policy.can_spend(platform='google-ads', amount=800, purpose='watch-launch')
    assert out['decision'] == 'AUTO'
    assert out['envelope_id'] == env['envelope_id']


def test_exceeding_daily_cap_reverts_to_confirm(tmp_path):
    policy = make(tmp_path)
    policy.authorize_envelope(platform='google-ads', daily_cap=1000, total_cap=5000)
    assert policy.can_spend(platform='google-ads', amount=1500)['decision'] == 'CONFIRM'


def test_total_cap_depletes(tmp_path):
    policy = make(tmp_path)
    env = policy.authorize_envelope(platform='meta-ads', total_cap=1000)
    assert policy.can_spend(platform='meta-ads', amount=900)['decision'] == 'AUTO'
    policy.record_spend(env['envelope_id'], 900)
    assert policy.can_spend(platform='meta-ads', amount=200)['decision'] == 'CONFIRM'


def test_expired_envelope_is_dead(tmp_path):
    import time
    policy = make(tmp_path)
    policy.authorize_envelope(platform='x-ads', total_cap=1000, expires_at=time.time() - 10)
    assert policy.can_spend(platform='x-ads', amount=1)['decision'] == 'CONFIRM'


def test_envelope_creation_is_audited(tmp_path):
    policy = make(tmp_path)
    policy.authorize_envelope(platform='google-ads', total_cap=5000)
    trail = policy.audit_trail('budget_envelope_change')
    assert trail and 'envelope authorized' in trail[0]['reason']


def test_negative_and_zero_amounts_never_auto(tmp_path):
    """Adversarial finding (Phase 12): amount=-50 fell through every cap
    check and returned AUTO. Non-positive spend is always CONFIRM."""
    policy = make(tmp_path)
    policy.authorize_envelope(platform='ads', total_cap=100)
    assert policy.can_spend(platform='ads', amount=-50)['decision'] == 'CONFIRM'
    assert policy.can_spend(platform='ads', amount=0)['decision'] == 'CONFIRM'