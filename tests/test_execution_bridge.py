"""The execution bridge's tool functions, exercised directly.

The MCP transport is the official SDK's; what needs proving here is OUR
surface: jail refusals, forbidden-command refusals, honest errors, and
that outputs stay bounded.
"""
import pytest
from friday import execution
from friday.tools import execution_bridge as bridge


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    (tmp_path / 'hello.txt').write_text('first line\nsecond line\n', encoding='utf-8')
    (tmp_path / 'sub').mkdir()
    (tmp_path / 'sub' / 'inner.txt').write_text('needle here\n', encoding='utf-8')
    monkeypatch.setenv('FRIDAY_BRIDGE_WORKSPACE', str(tmp_path))
    monkeypatch.setattr(bridge, '_env', None)
    yield tmp_path
    if bridge._env is not None:
        bridge._env.terminate()
        bridge._env = None


def test_read_inside_workspace(workspace):
    out = bridge.bridge_read_file('hello.txt', limit=1)
    assert 'first line' in out
    assert '2 lines total' in out


def test_read_escape_is_refused_not_errored(workspace):
    out = bridge.bridge_read_file('../escape.txt')
    assert out.startswith(bridge.REFUSED)
    assert 'outside the sandbox' in out


def test_write_then_read_roundtrip(workspace):
    assert 'wrote' in bridge.bridge_write_file('made.txt', 'content!')
    assert 'content!' in bridge.bridge_read_file('made.txt')


def test_write_escape_refused(workspace):
    out = bridge.bridge_write_file('..\\evil.txt', 'nope')
    assert out.startswith(bridge.REFUSED)


def test_listing_and_search(workspace):
    listing = bridge.bridge_list_files('.')
    assert 'hello.txt' in listing
    hits = bridge.bridge_search_files('needle')
    assert 'sub' in hits and 'needle here' in hits


def test_run_command_real_execution(workspace):
    out = bridge.bridge_run_command('python -c "print(\'bridge-exec-ok\')"')
    assert 'exit=0' in out
    assert 'bridge-exec-ok' in out


def test_shells_are_refused_by_name(workspace):
    for cmd in ('powershell Get-Date', 'cmd /c dir', 'bash -c ls', 'C:\\Windows\\System32\\cmd.exe /c echo hi'):
        out = bridge.bridge_run_command(cmd)
        assert out.startswith(bridge.REFUSED), cmd


def test_timeout_is_bounded_and_reported(workspace):
    out = bridge.bridge_run_command('python -c "import time; time.sleep(30)"', timeout=2)
    assert 'TIMED_OUT' in out


def test_missing_workspace_env_fails_loudly(monkeypatch):
    monkeypatch.delenv('FRIDAY_BRIDGE_WORKSPACE', raising=False)
    monkeypatch.setattr(bridge, '_env', None)
    with pytest.raises(RuntimeError):
        bridge._workspace()