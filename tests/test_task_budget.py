"""The adaptive execution policy in the TaskBundle.

Guards the Phase 1G finding: the waste was reasoning BREADTH, not context
size, and the cure must stay an information-value stop condition rather
than a tool-call quota (a quota would overfit one benchmark and cripple
investigations that honestly need twenty reads).
"""
from friday import hermes_bridge as hb


def test_policy_is_included_by_default():
    rendered = hb.TaskBundle(goal='inspect the project').render()
    assert 'EXECUTION POLICY' in rendered
    assert 'low expected information value' in rendered


def test_no_open_ended_completeness_clause():
    """Measured twice and rejected: any "have I missed something bigger"
    clause licenses unbounded search (38 calls / 3.6M open-ended, 23 calls
    / 2.1M bounded, vs 5 calls / 296k without). Severity ranking belongs in
    the caller's ACCEPTANCE, which is checkable."""
    policy = hb.TaskBundle.EXECUTION_POLICY.lower()
    for banned in ('no unresolved evidence', 'already seen', 'to be sure', 'make sure you have not missed'):
        assert banned not in policy


def test_policy_permits_going_deeper():
    """A stop condition that cannot escalate is a quota in disguise."""
    rendered = hb.TaskBundle(goal='g').render()
    assert 'Go deeper only when' in rendered
    assert 'evidence conflicts' in rendered
    assert 'confidence is genuinely insufficient' in rendered


def test_policy_is_not_a_call_quota():
    """No fixed number of reads/steps may appear in the policy."""
    import re
    policy = hb.TaskBundle.EXECUTION_POLICY
    assert not re.search('\\b(?:at most|exactly|no more than)\\s+\\w+\\s+(?:reads?|files?|calls?|steps?)', policy.lower())


def test_policy_can_be_switched_off():
    rendered = hb.TaskBundle(goal='g', adaptive_budget=False).render()
    assert 'EXECUTION POLICY' not in rendered


def test_policy_keeps_the_bundle_small():
    """The cure must not become its own context problem."""
    bundle = hb.TaskBundle(goal='Inspect this project and name the three most important architectural problems.', acceptance=('three problems with concrete evidence',), constraints=('read-only',))
    measured = bundle.measure()
    assert measured['oversized'] is False
    assert measured['chars'] < 1500