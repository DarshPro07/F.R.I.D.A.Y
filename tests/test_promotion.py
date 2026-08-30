"""
The gate, and every way round it.

The tests worth having are the ones that prove a refusal, because the failure
mode of a gate is that it opens.
"""
import sys
import pytest
from friday import evaluation as E
from friday import promotion as P


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("def main(): return 1\n",
                                             encoding="utf-8")
    return tmp_path


def _passed(detail: str = "3 passed"):
    return E.Attempt(task="t", agent="claude", verdict=E.PASSED,
                     exit_code=0, detail=detail)


def _failed():
    return E.Attempt(task="t", agent="claude", verdict=E.FAILED,
                     exit_code=1, detail="1 failed")


def _inconclusive():
    return E.Attempt(task="t", agent="claude", verdict=E.INCONCLUSIVE,
                     exit_code=None, detail="pytest: not found")


def test_an_unverified_run_is_refused(workspace):
    """A run reporting success is a claim, not a result."""
    decision = P.decide(workspace, ["src/app.py"], attempt=None, approved=True)
    assert not decision.allowed
    assert decision.reason == P.NOT_VERIFIED


def test_a_failed_verifier_is_refused(workspace):
    decision = P.decide(workspace, ["src/app.py"], attempt=_failed(),
                        approved=True)
    assert not decision.allowed
    assert decision.reason == P.NOT_VERIFIED


def test_an_inconclusive_verifier_is_not_a_pass(workspace):
    """
    "The tests say no" and "we never found out" must never be averaged
    together, and neither is a reason to promote.
    """
    decision = P.decide(workspace, ["src/app.py"], attempt=_inconclusive(),
                        approved=True)
    assert not decision.allowed
    assert decision.reason == P.VERIFIER_INCONCLUSIVE


def test_a_verified_run_nobody_approved_is_refused(workspace):
    decision = P.decide(workspace, ["src/app.py"], attempt=_passed())
    assert not decision.allowed
    assert decision.reason == P.NOT_APPROVED


def test_a_run_that_changed_nothing_is_refused(workspace):
    decision = P.decide(workspace, [], attempt=_passed(), approved=True)
    assert not decision.allowed
    assert decision.reason == P.NOTHING_TO_PROMOTE


def test_a_verified_approved_in_scope_run_is_allowed(workspace):
    decision = P.decide(workspace, ["src/app.py"], attempt=_passed(),
                        allowed_paths=("src/",), approved=True)
    assert decision.allowed, decision.detail
    assert decision.reason == ""


def test_a_file_outside_the_scope_is_refused(workspace):
    decision = P.decide(workspace, ["src/app.py", "deploy/prod.yaml"],
                        attempt=_passed(), allowed_paths=("src/",),
                        approved=True)
    assert not decision.allowed
    assert decision.reason == P.OUT_OF_SCOPE
    assert "deploy/prod.yaml" in decision.detail


def test_an_unscoped_run_is_not_blocked_by_the_scope_check(workspace):
    """
    The check can only be as strict as the scope it was given. Refusing every
    run that never declared one would just mean nobody declares one.
    """
    decision = P.decide(workspace, ["anywhere/thing.py"], attempt=_passed(),
                        allowed_paths=(), approved=True)
    assert decision.allowed


def test_a_forbidden_file_is_refused_even_when_in_scope(workspace):
    """
    A run that edited its own permissions is not a run whose result can be
    trusted to say whether it should have been allowed to.
    """
    decision = P.decide(workspace, ["src/app.py", ".env"], attempt=_passed(),
                        allowed_paths=(), approved=True)
    assert not decision.allowed
    assert decision.reason == P.OUT_OF_SCOPE
    assert ".env" in decision.detail


@pytest.mark.parametrize("name", ["id_rsa", ".npmrc", "credentials.json"])
def test_the_never_list_holds(workspace, name):
    decision = P.decide(workspace, [name], attempt=_passed(), approved=True)
    assert not decision.allowed


@pytest.mark.parametrize("secret", [
    "sk-ant-" "api03-aaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "ghp" "_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "AKIA" "IOSFODNN7EXAMPLE",
    "-----BEGIN RSA " "PRIVATE KEY-----",
])
def test_a_secret_in_a_changed_file_is_refused(workspace, secret):
    (workspace / "src" / "config.py").write_text(f'KEY = "{secret}"\n',
                                                 encoding="utf-8")
    decision = P.decide(workspace, ["src/config.py"], attempt=_passed(),
                        approved=True)
    assert not decision.allowed
    assert decision.reason == P.SECRET_IN_DIFF


def test_the_secret_scan_runs_before_anyone_is_asked_to_approve(workspace):
    """
    Nobody should ever be shown a diff containing a key and asked to sign it
    off. The scan is ordered before the approval check for that reason.
    """
    (workspace / "src" / "config.py").write_text(
        'KEY = "ghp' '_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"\n', encoding="utf-8")
    decision = P.decide(workspace, ["src/config.py"], attempt=_passed(),
                        approved=False)
    assert decision.reason == P.SECRET_IN_DIFF, \
        "it refused for want of approval before noticing the secret"


def test_ordinary_code_is_not_called_a_secret(workspace):
    """A scanner that cries wolf gets bypassed."""
    (workspace / "src" / "app.py").write_text(
        "TOKEN_NAME = 'authorization'\n"
        "def sign(key): return f'Bearer {key}'\n", encoding="utf-8")
    decision = P.decide(workspace, ["src/app.py"], attempt=_passed(),
                        approved=True)
    assert decision.allowed, decision.detail


def test_a_binary_or_missing_file_does_not_break_the_scan(workspace):
    decision = P.decide(workspace, ["src/app.py", "src/gone.py"],
                        attempt=_passed(), approved=True)
    assert decision.allowed


def test_every_check_that_ran_is_recorded(workspace):
    decision = P.decide(workspace, ['src/app.py'], attempt=_passed(), allowed_paths=('src/',), approved=True)
    names = [c['check'] for c in decision.checks]
    assert names == ['changes', 'verified', 'scope', 'reviewable', 'secrets', 'approved']
    assert all((c['ok'] for c in decision.checks))


def test_a_refusal_says_which_check_failed(workspace):
    decision = P.decide(workspace, ["src/app.py"], attempt=_failed(),
                        approved=True)
    failed = [c for c in decision.checks if not c["ok"]]
    assert failed and failed[0]["check"] == "verified"


def test_the_decision_serialises_for_a_report(workspace):
    decision = P.decide(workspace, ["src/app.py"], attempt=_passed(),
                        approved=True)
    as_dict = decision.as_dict()
    assert as_dict["allowed"] is True
    assert as_dict["changed"] == ["src/app.py"]


def test_a_passing_verifier_passes(workspace):
    verdict, code, _ = E.verify(
        workspace, E.Verifier(command=(sys.executable, "-c", "raise SystemExit(0)")))
    assert verdict == E.PASSED
    assert code == 0


def test_a_failing_verifier_fails(workspace):
    verdict, code, _ = E.verify(
        workspace, E.Verifier(command=(sys.executable, "-c", "raise SystemExit(1)")))
    assert verdict == E.FAILED
    assert code == 1


def test_a_missing_command_is_inconclusive_not_failed(workspace):
    """Blaming the code for a missing interpreter is a lie about the code."""
    verdict, _, _ = E.verify(
        workspace, E.Verifier(command=("definitely-not-a-real-binary-xyz",)))
    assert verdict == E.INCONCLUSIVE


def test_a_hanging_verifier_is_inconclusive(workspace):
    verdict, _, detail = E.verify(workspace, E.Verifier(
        command=(sys.executable, "-c", "import time; time.sleep(30)"),
        seconds=2))
    assert verdict == E.INCONCLUSIVE
    assert "exceeded" in detail


def test_the_verifier_runs_in_the_workspace(workspace):
    verdict, _, detail = E.verify(workspace, E.Verifier(
        command=(sys.executable, "-c",
                 "import pathlib,sys; sys.exit(0 if pathlib.Path('src/app.py').is_file() else 9)")))
    assert verdict == E.PASSED, detail


def test_graded_records_the_attempt(workspace):
    record = E.Record()
    attempt = E.graded("build", "claude", workspace,
                       E.Verifier(command=(sys.executable, "-c", "pass")),
                       record=record)
    assert attempt.passed
    assert len(record.attempts) == 1


@pytest.mark.parametrize('path', ['src/__pycache__/app.cpython-311.pyc', '.pytest_cache/v/cache/lastfailed', 'node_modules/left-pad/index.js', '.graft/graph.json'])
def test_tool_debris_is_refused(workspace, path):
    """
    A reviewer who has to skip six leftovers skims the seventh, which is where
    the real problem is hiding.
    """
    decision = P.decide(workspace, ['src/app.py', path], attempt=_passed(), approved=True)
    assert not decision.allowed
    assert decision.reason == P.DEBRIS_IN_DIFF


@pytest.mark.parametrize('path', ['.mcp.json', 'AGENTS.md', '.gitconfig'])
def test_machine_configuration_is_refused(workspace, path):
    """
    A run may change what it is building. It may not change what it is
    allowed to do next time - which is what Graft's own `--no-global` exists
    to prevent.
    """
    decision = P.decide(workspace, ['src/app.py', path], attempt=_passed(), approved=True)
    assert not decision.allowed
    assert decision.reason == P.GLOBAL_CONFIG_CHANGED


def test_an_unreviewed_binary_is_refused(workspace):
    blob = workspace / 'src' / 'vendor.dll'
    blob.write_bytes(b'\x00' * (P.SMALL_BINARY + 1))
    decision = P.decide(workspace, ['src/app.py', 'src/vendor.dll'], attempt=_passed(), approved=True)
    assert not decision.allowed
    assert decision.reason == P.UNEXPECTED_BINARY


def test_a_small_asset_is_allowed(workspace):
    """A small icon in a web app is normal, and refusing it teaches nothing."""
    (workspace / 'src' / 'favicon.ico').write_bytes(b'\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00')
    decision = P.decide(workspace, ['src/app.py', 'src/favicon.ico'], attempt=_passed(), approved=True)
    assert decision.allowed, decision.detail


def test_debris_is_caught_before_the_secret_scan(workspace):
    """
    Cheapest first, and nothing noisy should reach a person ahead of the
    interesting part.
    """
    (workspace / 'src' / 'config.py').write_text('KEY = "ghp' '_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"\n', encoding='utf-8')
    decision = P.decide(workspace, ['src/config.py', 'src/__pycache__/x.pyc'], attempt=_passed(), approved=True)
    assert decision.reason == P.DEBRIS_IN_DIFF


def _repo(path):
    import subprocess

    def git(*args):
        return subprocess.run(['git', '-C', str(path), *args], capture_output=True, text=True, check=True).stdout
    git('init', '--quiet')
    git('config', 'user.email', 't@t.invalid')
    git('config', 'user.name', 't')
    git('add', '-A')
    git('commit', '--quiet', '-m', 'one')
    return (git('rev-parse', 'HEAD').strip(), git)


def test_the_same_base_is_accepted(workspace):
    head, _ = _repo(workspace)
    decision = P.decide(workspace, ['src/app.py'], attempt=_passed(), base_commit=head, approved=True)
    assert decision.allowed, decision.detail


def test_a_moved_base_is_refused(workspace):
    """
    A patch verified against one base and applied to another is how a clean
    review lands broken code. He may well have committed something himself
    while the agent worked.
    """
    head, git = _repo(workspace)
    (workspace / 'other.txt').write_text('his own work\n', encoding='utf-8')
    git('add', '-A')
    git('commit', '--quiet', '-m', 'two')
    decision = P.decide(workspace, ['src/app.py'], attempt=_passed(), base_commit=head, approved=True)
    assert not decision.allowed
    assert decision.reason == P.WRONG_BASE
    assert head[:12] in decision.detail


def test_no_base_given_is_not_checked(workspace):
    """The check can only be as strict as what it was told."""
    decision = P.decide(workspace, ['src/app.py'], attempt=_passed(), approved=True)
    assert decision.allowed


def test_a_short_commit_prefix_still_matches(workspace):
    head, _ = _repo(workspace)
    decision = P.decide(workspace, ['src/app.py'], attempt=_passed(), base_commit=head[:8], approved=True)
    assert decision.allowed, decision.detail


def test_a_directory_that_is_not_a_repository_is_refused_not_assumed(tmp_path):
    ok, detail = P.base_matches(tmp_path, 'abc123')
    assert not ok
    assert detail
