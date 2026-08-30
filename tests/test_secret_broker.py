"""Secret broker - the no-plaintext-anywhere contract.

Promotion-blocking assertions:
- raw value never appears in any model-facing return;
- scratch file is shredded after ingestion;
- store on disk is encrypted (raw value not greppable);
- clipboard-shaped keys are dead on arrival;
- machine path (env injection) works without exposing the value.
"""
import json
from friday import secret_broker as sb
FAKE_KEY = 'sk-ant-' 'api03-THISISAFAKETESTKEY1234567890abcd'


def make(tmp_path):
    return sb.SecretBroker(tmp_path / 'vault')


def test_ingest_returns_metadata_never_value(tmp_path):
    broker = make(tmp_path)
    scratch = broker.scratch_file('anthropic-primary')
    scratch.write_text(FAKE_KEY, encoding='utf-8')
    out = broker.ingest_scratch('anthropic-primary', provider='anthropic', purpose='model API')
    assert out['status'] == 'stored'
    assert FAKE_KEY not in json.dumps(out)


def test_scratch_is_shredded(tmp_path):
    broker = make(tmp_path)
    scratch = broker.scratch_file('k')
    scratch.write_text(FAKE_KEY, encoding='utf-8')
    broker.ingest_scratch('k')
    assert not scratch.exists()


def test_store_on_disk_is_encrypted(tmp_path):
    broker = make(tmp_path)
    broker.scratch_file('k').write_text(FAKE_KEY, encoding='utf-8')
    broker.ingest_scratch('k')
    on_disk = (tmp_path / 'vault' / 'secrets.enc.json').read_text(encoding='utf-8')
    assert FAKE_KEY not in on_disk


def test_model_facing_surfaces_carry_no_value(tmp_path):
    broker = make(tmp_path)
    broker.scratch_file('k').write_text(FAKE_KEY, encoding='utf-8')
    broker.ingest_scratch('k', provider='anthropic')
    listing = json.dumps(broker.list_aliases())
    meta = json.dumps(broker.connection_metadata('k'))
    assert FAKE_KEY not in listing
    assert FAKE_KEY not in meta
    assert 'anthropic' in meta


def test_machine_path_round_trips(tmp_path):
    broker = make(tmp_path)
    broker.scratch_file('k').write_text(FAKE_KEY, encoding='utf-8')
    broker.ingest_scratch('k')
    env = {}
    out = broker.inject_env('k', 'ANTHROPIC_API_KEY', env)
    assert env['ANTHROPIC_API_KEY'] == FAKE_KEY
    assert FAKE_KEY not in json.dumps(out)


def test_empty_scratch_stores_nothing(tmp_path):
    broker = make(tmp_path)
    broker.scratch_file('k')
    out = broker.ingest_scratch('k')
    assert out['status'] == 'empty'
    assert broker.connection_metadata('k')['status'] == 'absent'


def test_remove(tmp_path):
    broker = make(tmp_path)
    broker.scratch_file('k').write_text(FAKE_KEY, encoding='utf-8')
    broker.ingest_scratch('k')
    assert broker.remove('k')['status'] == 'removed'
    assert broker.connection_metadata('k')['status'] == 'absent'


def test_clipboard_key_is_dead():
    assert sb.clipboard_is_dead(f"copied: {FAKE_KEY}")
    assert not sb.clipboard_is_dead('just an ordinary sentence')


def test_alias_traversal_is_refused(tmp_path):
    """Adversarial finding (Phase 12): '../../evil' as an alias escaped
    the vault directory. Aliases are filename-safe or refused."""
    import pytest
    broker = make(tmp_path)
    for bad in ('../../evil', '..', 'a/b', 'a\\b', '', 'xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx'):
        with pytest.raises(ValueError):
            broker.scratch_file(bad)