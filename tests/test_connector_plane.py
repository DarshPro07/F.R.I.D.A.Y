"""ConnectorControlPlane — discovery, flows, isolation of secrets.

The product contract: "Jarvis, connect X" leaves the boss exactly one
human step, and no secret value ever reaches model-visible space (tool
results, logs, state rows). Providers are DISCOVERED from the Hermes
registry, never memorized.
"""
from __future__ import annotations
import json
import pytest
from friday.connectors import plane as P
REGISTRY = {'providers': [{'slug': 'anthropic', 'name': 'Anthropic', 'auth_type': 'oauth_external', 'authenticated': False, 'key_env': '', 'models': [{'id': 'claude-opus-5'}], 'is_current': False, 'warning': ''}, {'slug': 'fireworks', 'name': 'Fireworks AI', 'auth_type': 'api_key', 'authenticated': False, 'key_env': 'FIREWORKS_API_KEY', 'models': [], 'is_current': False, 'warning': 'paste FIREWORKS_API_KEY to activate'}, {'slug': 'openai-codex', 'name': 'Codex Subscription', 'auth_type': 'oauth_device_code', 'authenticated': True, 'key_env': '', 'models': [{'id': 'gpt-5.3-codex'}], 'is_current': True, 'warning': ''}]}


@pytest.fixture
def fake_registry(monkeypatch):
    calls = {'set': []}

    def fake_api(path, payload=None, timeout=15.0):
        if path == '/api/model/options':
            return json.loads(json.dumps(REGISTRY))
        if path == '/api/model/set':
            calls['set'].append(payload)
            return {'ok': True}
        raise AssertionError(f"unexpected API path {path}")
    monkeypatch.setattr(P, '_api', fake_api)
    return calls


@pytest.fixture
def control(fake_registry, tmp_path, monkeypatch):
    config = tmp_path / 'config.yaml'
    config.write_text('model:\n  provider: x\n  default: y\n', encoding='utf-8')
    monkeypatch.setenv('HERMES_PROFILE_CONFIG', str(config))
    state = P.ConnectorState(tmp_path / 'connectors.db')
    return P.ConnectorControlPlane(state)


def test_discovery_is_dynamic_not_memorized(control):
    found = {c['connector'] for c in control.discover_connectors()}
    assert found == {'openai-codex', 'anthropic', 'fireworks'}
    REGISTRY['providers'].append({'slug': 'brand-new-ai', 'name': 'Brand New', 'auth_type': 'api_key', 'authenticated': False, 'key_env': 'BRAND_NEW_KEY', 'models': [], 'is_current': False, 'warning': ''})
    try:
        found = {c['connector'] for c in control.discover_connectors()}
        assert 'brand-new-ai' in found
    finally:
        REGISTRY['providers'].pop()


def test_each_connector_states_its_one_human_step(control):
    described = {c['connector']: c for c in control.discover_connectors()}
    assert 'sign-in' in described['anthropic']['human_step']
    assert 'secure entry' in described['fireworks']['human_step']
    assert described['openai-codex']['human_step'].startswith('none')


def test_oauth_flow_launches_and_asks_exactly_one_human_step(control, monkeypatch):
    launches = []
    monkeypatch.setattr(P.ConnectorControlPlane, '_launch_auth_surface', lambda self, c: launches.append(c.id) or {'launched': True, 'auth_pid': 4242, 'hint': 'Your browser is opening.'})
    step = control.begin_connection('anthropic', model='claude-opus-5')
    assert step.action == 'human_step'
    assert launches == ['anthropic'], 'Jarvis must OPEN the flow itself'
    assert 'opened the official sign-in' in step.say
    record = control.state.get('anthropic')
    assert record['status'] == 'AUTH_IN_PROGRESS'
    assert record['credential_ref'] == 'hermes-auth:anthropic'


def test_oauth_launch_failure_degrades_to_clear_instruction(control, monkeypatch):
    monkeypatch.setattr(P.ConnectorControlPlane, '_launch_auth_surface', lambda self, c: {'launched': False, 'note': 'no CLI'})
    step = control.begin_connection('anthropic')
    assert step.action == 'human_step'
    assert control.state.get('anthropic')['status'] == 'AUTH_REQUIRED'


def test_authenticated_provider_connects_without_any_human_step(control, fake_registry, tmp_path):
    step = control.begin_connection('openai-codex', model='gpt-5.3-codex')
    assert step.action == 'done'
    import yaml
    written = yaml.safe_load((tmp_path / 'config.yaml').read_text())
    assert written['model']['provider'] == 'openai-codex'
    assert written['model']['default'] == 'gpt-5.3-codex'
    record = control.state.get('openai-codex')
    assert record['status'] == 'AUTHENTICATED'
    assert record['health'] == 'AUTH_OK'


def test_api_key_flow_stores_only_an_opaque_ref(control, monkeypatch):
    seen = {}

    def fake_request_secret(*, title, credential_name, timeout=300.0):
        seen['credential_name'] = credential_name
        return {'status': 'stored', 'credential_ref': f"wincred:hermes/{credential_name}", 'length': 42}
    from friday.connectors import secure_entry
    monkeypatch.setattr(secure_entry, 'request_secret', fake_request_secret)
    step = control.begin_connection('fireworks')
    assert seen['credential_name'] == 'FIREWORKS_API_KEY'
    record = control.state.get('fireworks')
    assert record['credential_ref'] == 'wincred:hermes/FIREWORKS_API_KEY'
    assert 'sk-' not in json.dumps(record)
    assert 'sk-' not in json.dumps({'say': step.say, **step.detail})


def test_cancelled_entry_window_is_a_calm_human_step_not_an_error(control, monkeypatch):
    from friday.connectors import secure_entry
    monkeypatch.setattr(secure_entry, 'request_secret', lambda **kw: {'status': 'cancelled'})
    step = control.begin_connection('fireworks')
    assert step.action == 'human_step'
    assert 'again' in step.say


def test_verify_after_signin_flips_to_connected(control, monkeypatch):
    control.begin_connection('anthropic')
    REGISTRY['providers'][0]['authenticated'] = True
    try:
        step = control.verify_connection('anthropic', expected_model='claude-opus-5')
        assert step.action == 'done'
        assert control.state.get('anthropic')['status'] == 'AUTHENTICATED'
    finally:
        REGISTRY['providers'][0]['authenticated'] = False


def test_repair_reauths_only_when_credential_is_genuinely_absent(control):
    step = control.repair('openai-codex')
    assert step.action == 'done'
    step = control.repair('anthropic')
    assert step.action == 'human_step'


def test_unknown_connector_names_the_known_ones(control):
    described = control.describe_connector('nonexistent-ai')
    assert described['status'] == 'unknown_connector'
    assert 'anthropic' in described['known']


def test_status_never_contains_secret_material(control, monkeypatch):
    from friday.connectors import secure_entry
    monkeypatch.setattr(secure_entry, 'request_secret', lambda **kw: {'status': 'stored', 'credential_ref': 'wincred:hermes/FIREWORKS_API_KEY', 'length': 42})
    control.begin_connection('fireworks')
    dashboard = json.dumps(control.status())
    assert 'wincred:' in dashboard
    assert 'api_key": "sk' not in dashboard
    assert 'value' not in dashboard.lower() or 'sk-' not in dashboard