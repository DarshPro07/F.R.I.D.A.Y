"""Phase 2 — SharedBrainAdapter and the brain toolset.

The narrow question this phase answers (boss): can Friday and Hermes
share what they already learned and materially reduce rediscovery
without weakening continuity or privacy? These are the unit-level
guarantees; the live Golden Journeys are recorded in
docs/architecture/GBRAIN_PHASE2.md.
"""
from __future__ import annotations
import json
import pytest
from friday import brain as B


class FakeTransport(B.SharedBrainAdapter):
    """Adapter with the subprocess replaced; verb + args are captured."""

    def __init__(self):
        super().__init__()
        self.calls = []
        self.answers = {}

    def _call(self, verb, arguments):
        self.calls.append((verb, dict(arguments)))
        if isinstance(self.answers.get(verb), Exception):
            raise self.answers[verb]
        return self.answers.get(verb, {})


@pytest.fixture
def fake():
    return FakeTransport()


def test_recall_always_carries_a_server_side_budget(fake):
    fake.answers['recall'] = {'facts': [], 'results': [], 'budget_used': 0, 'dropped_count': 0}
    fake.recall('anything')
    verb, arguments = fake.calls[0]
    assert verb == 'recall'
    assert arguments['budget_tokens'] == B.BUDGETS['bounded']
    fake.recall('small', budget='trivial')
    assert fake.calls[1][1]['budget_tokens'] == B.BUDGETS['trivial']


def test_budget_report_is_surfaced_not_recomputed(fake):
    fake.answers['recall'] = {'facts': [{'fact': 'x'}], 'results': [], 'budget_used': 123, 'dropped_count': 4}
    answer = fake.recall('q')
    assert answer.budget_used == 123
    assert answer.dropped_count == 4


def test_secret_shapes_are_refused_before_ingestion(fake):
    for bad in ('the key is sk-abc1234567890123456789', 'api_key: 0123456789abcdef', 'Authorization: bearer abcdefghijklmnopqrstuv1234', '-----BEGIN RSA ' 'PRIVATE KEY-----', 'password = hunter2secret'):
        with pytest.raises(B.AdmissionRefused):
            fake.remember(bad, provenance='test')
    assert not any((v == 'remember' for v, _ in fake.calls)), 'a refused fact still reached the brain transport'


def test_banking_shapes_are_refused_before_ingestion(fake):
    with pytest.raises(B.AdmissionRefused):
        fake.remember('boss card number 4111 1111', provenance='x')
    assert fake.calls == []


def test_provenance_is_mandatory(fake):
    with pytest.raises(ValueError):
        fake.remember('a true fact', provenance='   ')


def test_admissible_fact_passes_through_with_entity(fake):
    fake.answers['remember'] = {'status': 'inserted', 'id': '9'}
    out = fake.remember('the planner shortlist is registry order', provenance='wiring skill', entity='friday')
    assert out['status'] == 'inserted'
    _, arguments = fake.calls[0]
    assert arguments['entity'] == 'friday'
    assert arguments['provenance'] == 'wiring skill'


def test_available_false_never_raises(fake):
    fake.answers['recall'] = RuntimeError('brain down')
    assert fake.available() is False


def test_toolset_recall_degrades_to_observed_miss(fake, monkeypatch):
    """GJ6 at the executor seam: brain down -> OBSERVED, run continues."""
    from friday import contracts as c
    from friday.toolsets import brain as T
    fake.answers['recall'] = RuntimeError('unreachable')
    monkeypatch.setattr(T, '_adapter', fake)
    run = c.Run(run_id='RUN-brain-degrade', request='brain degrade test')
    result = T.recall(run, 'anything')
    assert result.status == c.OBSERVED
    assert result.output['available'] is False


def test_toolset_remember_refusal_is_a_failed_result(fake, monkeypatch):
    from friday import contracts as c
    from friday.toolsets import brain as T
    monkeypatch.setattr(T, '_adapter', fake)
    run = c.Run(run_id='RUN-brain-refuse', request='brain refuse test')
    result = T.remember(run, 'api_key = sk-123456789012345678', provenance='x')
    assert result.status == c.FAILED
    assert 'refused before ingestion' in result.error