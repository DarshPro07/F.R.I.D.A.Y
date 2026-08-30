"""Self-upgrade loop - staged, tested, rolled back; kernel refused.

Uses a REAL throwaway git repo per test (not the working repo), so
rollback behavior is proven against actual git operations.
"""
import subprocess
from pathlib import Path
import pytest
from friday import self_upgrade as su


def make_repo(tmp_path) -> Path:
    repo = tmp_path / 'repo'
    (repo / 'pkg').mkdir(parents=True)
    (repo / 'tests').mkdir()
    (repo / 'pkg' / 'mod.py').write_text('VALUE = 1\n', encoding='utf-8')
    (repo / 'tests' / 'test_mod.py').write_text('import sys, pathlib\nsys.path.insert(0, str(pathlib.Path(__file__).parent.parent))\nfrom pkg.mod import VALUE\ndef test_value():\n    assert VALUE in (1, 2)\n', encoding='utf-8')
    for args in (('init',), ('add', '.'), ('-c', 'user.email=t@t', '-c', 'user.name=t', 'commit', '-m', 'base')):
        subprocess.run(['git', '-C', str(repo).replace('\\', '/'), *args], capture_output=True, timeout=60, check=True)
    return repo


def make(tmp_path):
    repo = make_repo(tmp_path)
    upgrader = su.SelfUpgrade(repo, journal=tmp_path / 'journal.jsonl')
    import sys

    def run_tests(tests, timeout=300):
        result = subprocess.run([sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', *tests], cwd=str(repo), capture_output=True, text=True, timeout=timeout)
        tail = '\n'.join((result.stdout or '').splitlines()[-3:])
        upgrader._log('tests', passed=result.returncode == 0, tail=tail)
        return (result.returncode == 0, tail)
    upgrader.run_tests = run_tests
    return (upgrader, repo)


def test_kernel_surface_is_refused(tmp_path):
    upgrader, _repo = make(tmp_path)
    with pytest.raises(su.UpgradeRefused):
        upgrader.guard_kernel(['friday/policy.py'])
    with pytest.raises(su.UpgradeRefused):
        upgrader.guard_kernel(['E:/x/friday/sensitive_domains.py'])
    assert 'refused_kernel' in upgrader.journal_path.read_text(encoding='utf-8')


def test_successful_upgrade_promotes(tmp_path):
    upgrader, repo = make(tmp_path)

    def apply_change():
        (repo / 'pkg' / 'mod.py').write_text('VALUE = 2\n', encoding='utf-8')
    out = upgrader.upgrade(description='bump the constant, one line', files=['pkg/mod.py'], apply_change=apply_change, affected_tests=['tests/test_mod.py'])
    assert out['status'] == 'promoted'
    assert (repo / 'pkg' / 'mod.py').read_text(encoding='utf-8') == 'VALUE = 2\n'


def test_failing_tests_roll_back(tmp_path):
    upgrader, repo = make(tmp_path)

    def apply_bad_change():
        (repo / 'pkg' / 'mod.py').write_text('VALUE = 99\n', encoding='utf-8')
    out = upgrader.upgrade(description='bad change, one line', files=['pkg/mod.py'], apply_change=apply_bad_change, affected_tests=['tests/test_mod.py'])
    assert out['status'] == 'rolled_back'
    assert out['stage'] == 'tests'
    assert (repo / 'pkg' / 'mod.py').read_text(encoding='utf-8') == 'VALUE = 1\n'


def test_failed_live_check_rolls_back(tmp_path):
    upgrader, repo = make(tmp_path)

    def apply_change():
        (repo / 'pkg' / 'mod.py').write_text('VALUE = 2\n', encoding='utf-8')
    out = upgrader.upgrade(description='ok tests, sick runtime', files=['pkg/mod.py'], apply_change=apply_change, affected_tests=['tests/test_mod.py'], live_check=lambda: False)
    assert out['status'] == 'rolled_back'
    assert out['stage'] == 'live_check'
    assert (repo / 'pkg' / 'mod.py').read_text(encoding='utf-8') == 'VALUE = 1\n'


def test_crash_in_apply_rolls_back(tmp_path):
    upgrader, repo = make(tmp_path)

    def apply_crashing():
        (repo / 'pkg' / 'mod.py').write_text('VALUE = 3\n', encoding='utf-8')
        raise RuntimeError('editor crashed mid-change')
    out = upgrader.upgrade(description='crash mid-apply', files=['pkg/mod.py'], apply_change=apply_crashing)
    assert out['status'] == 'rolled_back'
    assert (repo / 'pkg' / 'mod.py').read_text(encoding='utf-8') == 'VALUE = 1\n'


def test_journal_records_the_whole_story(tmp_path):
    upgrader, repo = make(tmp_path)
    upgrader.upgrade(description='bump constant, one line', files=['pkg/mod.py'], apply_change=lambda: (repo / 'pkg' / 'mod.py').write_text('VALUE = 2\n', encoding='utf-8'), affected_tests=['tests/test_mod.py'])
    journal = upgrader.journal_path.read_text(encoding='utf-8')
    for step in ('checkpoint', 'verification_plan', 'tests', 'promoted'):
        assert step in journal