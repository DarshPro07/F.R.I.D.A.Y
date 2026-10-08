"""
The gate, and every way round it.

The tests worth having are the ones that prove a refusal, because the failure
mode of a gate is that it opens.
"""
import sys
import pytest
from friday import build_identity
from friday import evaluation as E
from friday import promotion as P
from friday.epistemic import LedgerClaim


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("def main(): return 1\n",
                                             encoding="utf-8")
    _repo(tmp_path)
    return tmp_path


def _passed(workspace=None, detail: str = "3 passed", commit: str = "",
            fingerprint: str = "f00d"):
    """
    A pass by the verifier `fingerprint`, judged at `commit` - by default
    the commit `workspace` is on now, read the way `graded` reads it.
    """
    if not commit and workspace is not None:
        commit = build_identity.workspace_build(workspace).commit
    return E.Attempt(task="t", agent="claude", verdict=E.PASSED,
                     exit_code=0, detail=detail, build_commit=commit,
                     verifier_fingerprint=fingerprint)


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
    decision = P.decide(workspace, ["src/app.py"], attempt=_passed(workspace))
    assert not decision.allowed
    assert decision.reason == P.NOT_APPROVED


def test_a_run_that_changed_nothing_is_refused(workspace):
    decision = P.decide(workspace, [], attempt=_passed(workspace), approved=True)
    assert not decision.allowed
    assert decision.reason == P.NOTHING_TO_PROMOTE


def test_a_verified_approved_in_scope_run_is_allowed(workspace):
    decision = P.decide(workspace, ["src/app.py"], attempt=_passed(workspace),
                        allowed_paths=("src/",), approved=True)
    assert decision.allowed, decision.detail
    assert decision.reason == ""


def test_a_file_outside_the_scope_is_refused(workspace):
    decision = P.decide(workspace, ["src/app.py", "deploy/prod.yaml"],
                        attempt=_passed(workspace), allowed_paths=("src/",),
                        approved=True)
    assert not decision.allowed
    assert decision.reason == P.OUT_OF_SCOPE
    assert "deploy/prod.yaml" in decision.detail


def test_an_unscoped_run_is_not_blocked_by_the_scope_check(workspace):
    """
    The check can only be as strict as the scope it was given. Refusing every
    run that never declared one would just mean nobody declares one.
    """
    decision = P.decide(workspace, ["anywhere/thing.py"], attempt=_passed(workspace),
                        allowed_paths=(), approved=True)
    assert decision.allowed


def test_a_forbidden_file_is_refused_even_when_in_scope(workspace):
    """
    A run that edited its own permissions is not a run whose result can be
    trusted to say whether it should have been allowed to.
    """
    decision = P.decide(workspace, ["src/app.py", ".env"], attempt=_passed(workspace),
                        allowed_paths=(), approved=True)
    assert not decision.allowed
    assert decision.reason == P.OUT_OF_SCOPE
    assert ".env" in decision.detail


@pytest.mark.parametrize("name", ["id_rsa", ".npmrc", "credentials.json"])
def test_the_never_list_holds(workspace, name):
    decision = P.decide(workspace, [name], attempt=_passed(workspace), approved=True)
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
    decision = P.decide(workspace, ["src/config.py"], attempt=_passed(workspace),
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
    decision = P.decide(workspace, ["src/config.py"], attempt=_passed(workspace),
                        approved=False)
    assert decision.reason == P.SECRET_IN_DIFF, \
        "it refused for want of approval before noticing the secret"


def test_ordinary_code_is_not_called_a_secret(workspace):
    """A scanner that cries wolf gets bypassed."""
    (workspace / "src" / "app.py").write_text(
        "TOKEN_NAME = 'authorization'\n"
        "def sign(key): return f'Bearer {key}'\n", encoding="utf-8")
    decision = P.decide(workspace, ["src/app.py"], attempt=_passed(workspace),
                        approved=True)
    assert decision.allowed, decision.detail


def test_a_binary_or_missing_file_does_not_break_the_scan(workspace):
    decision = P.decide(workspace, ["src/app.py", "src/gone.py"],
                        attempt=_passed(workspace), approved=True)
    assert decision.allowed


def test_every_check_that_ran_is_recorded(workspace):
    decision = P.decide(workspace, ['src/app.py'], attempt=_passed(workspace), allowed_paths=('src/',), approved=True)
    names = [c['check'] for c in decision.checks]
    # FR-012 added 'independent_review' between verified and scope: a one-file
    # change records it as not required, and it still shows in the trail.
    assert names == ['changes', 'verified', 'independent_review', 'scope',
                     'reviewable', 'secrets', 'approved']
    assert all((c['ok'] for c in decision.checks))


def test_a_refusal_says_which_check_failed(workspace):
    decision = P.decide(workspace, ["src/app.py"], attempt=_failed(),
                        approved=True)
    failed = [c for c in decision.checks if not c["ok"]]
    assert failed and failed[0]["check"] == "verified"


def test_the_decision_serialises_for_a_report(workspace):
    decision = P.decide(workspace, ["src/app.py"], attempt=_passed(workspace),
                        approved=True)
    as_dict = decision.as_dict()
    assert as_dict["allowed"] is True
    assert as_dict["changed"] == ["src/app.py"]
    assert as_dict["claims"] == []


def test_a_decision_links_the_ledger_claims_it_relied_on(workspace):
    relied_on = LedgerClaim(claim="the vendor API is stable", status="inferred")

    decision = P.decide(workspace, ["src/app.py"], attempt=_passed(workspace),
                        approved=True, claims=[relied_on, "clm_0123456789ab"])

    assert decision.allowed
    assert decision.claims == (relied_on.id, "clm_0123456789ab")
    assert decision.as_dict()["claims"] == [relied_on.id, "clm_0123456789ab"]


def test_a_refused_decision_still_links_its_claims(workspace):
    relied_on = LedgerClaim(claim="the vendor API is stable", status="inferred")

    decision = P.decide(workspace, ["src/app.py"], attempt=_failed(),
                        approved=True, claims=[relied_on])

    assert not decision.allowed
    assert decision.claims == (relied_on.id,)


def test_a_claim_link_that_is_not_an_id_is_rejected(workspace):
    with pytest.raises(TypeError):
        P.decide(workspace, ["src/app.py"], attempt=_passed(workspace),
                 approved=True, claims=[{"claim": "not an id"}])


def test_a_bare_claim_id_string_is_rejected_not_shredded_into_characters(workspace):
    """`claims` is documented as a collection of ids; a bare id string is
    iterable too, and silently splitting it into one-character "ids" would
    corrupt the link instead of raising. Reject it the same way a malformed
    entry in a list is rejected."""
    with pytest.raises(TypeError):
        P.decide(workspace, ["src/app.py"], attempt=_passed(workspace),
                 approved=True, claims="clm_0123456789ab")


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
    decision = P.decide(workspace, ['src/app.py', path], attempt=_passed(workspace), approved=True)
    assert not decision.allowed
    assert decision.reason == P.DEBRIS_IN_DIFF


@pytest.mark.parametrize('path', ['.mcp.json', 'AGENTS.md', '.gitconfig'])
def test_machine_configuration_is_refused(workspace, path):
    """
    A run may change what it is building. It may not change what it is
    allowed to do next time - which is what Graft's own `--no-global` exists
    to prevent.
    """
    decision = P.decide(workspace, ['src/app.py', path], attempt=_passed(workspace), approved=True)
    assert not decision.allowed
    assert decision.reason == P.GLOBAL_CONFIG_CHANGED


def test_an_unreviewed_binary_is_refused(workspace):
    blob = workspace / 'src' / 'vendor.dll'
    blob.write_bytes(b'\x00' * (P.SMALL_BINARY + 1))
    decision = P.decide(workspace, ['src/app.py', 'src/vendor.dll'], attempt=_passed(workspace), approved=True)
    assert not decision.allowed
    assert decision.reason == P.UNEXPECTED_BINARY


def test_a_small_asset_is_allowed(workspace):
    """A small icon in a web app is normal, and refusing it teaches nothing."""
    (workspace / 'src' / 'favicon.ico').write_bytes(b'\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00')
    decision = P.decide(workspace, ['src/app.py', 'src/favicon.ico'], attempt=_passed(workspace), approved=True)
    assert decision.allowed, decision.detail


def test_debris_is_caught_before_the_secret_scan(workspace):
    """
    Cheapest first, and nothing noisy should reach a person ahead of the
    interesting part.
    """
    (workspace / 'src' / 'config.py').write_text('KEY = "ghp' '_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"\n', encoding='utf-8')
    decision = P.decide(workspace, ['src/config.py', 'src/__pycache__/x.pyc'], attempt=_passed(workspace), approved=True)
    assert decision.reason == P.DEBRIS_IN_DIFF


def _repo(path):
    import subprocess

    def git(*args):
        return subprocess.run(['git', '-C', str(path), *args], capture_output=True, text=True, check=True).stdout
    # The `workspace` fixture already made it a repository: a second call
    # reports that commit rather than failing to commit nothing.
    if not (path / '.git').exists():
        git('init', '--quiet')
        git('config', 'user.email', 't@t.invalid')
        git('config', 'user.name', 't')
        git('add', '-A')
        git('commit', '--quiet', '-m', 'one')
    return (git('rev-parse', 'HEAD').strip(), git)


def test_the_same_base_is_accepted(workspace):
    head, _ = _repo(workspace)
    decision = P.decide(workspace, ['src/app.py'], attempt=_passed(commit=head[:7]), base_commit=head, approved=True)
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
    decision = P.decide(workspace, ['src/app.py'], attempt=_passed(commit=head[:7]), base_commit=head, approved=True)
    assert not decision.allowed
    assert decision.reason == P.WRONG_BASE
    assert head[:12] in decision.detail


def test_no_base_given_is_not_checked(workspace):
    """The check can only be as strict as what it was told."""
    decision = P.decide(workspace, ['src/app.py'], attempt=_passed(workspace), approved=True)
    assert decision.allowed


def test_a_short_commit_prefix_still_matches(workspace):
    head, _ = _repo(workspace)
    decision = P.decide(workspace, ['src/app.py'], attempt=_passed(commit=head[:7]), base_commit=head[:8], approved=True)
    assert decision.allowed, decision.detail


def test_an_unversioned_pass_is_refused(workspace):
    """A pass that names no build or verifier cannot be re-run."""
    unstamped = E.Attempt(task="t", agent="claude", verdict=E.PASSED,
                          exit_code=0, detail="3 passed")
    decision = P.decide(workspace, ['src/app.py'], attempt=unstamped, approved=True)
    assert not decision.allowed
    assert decision.reason == P.UNVERSIONED_EVAL
    outside_a_repo = _passed(commit="unknown")
    decision = P.decide(workspace, ['src/app.py'], attempt=outside_a_repo, approved=True)
    assert decision.reason == P.UNVERSIONED_EVAL


def test_a_pass_judged_at_another_build_is_refused(workspace):
    """The verdict is about the code it judged, not whatever is there now."""
    head, _ = _repo(workspace)
    decision = P.decide(workspace, ['src/app.py'], attempt=_passed(commit="deadbee"),
                        base_commit=head, approved=True)
    assert not decision.allowed
    assert decision.reason == P.EVAL_OF_ANOTHER_BUILD
    # Stamps are short commits (build_identity), so that is what the message names.
    assert "deadbee" in decision.detail and head[:7] in decision.detail


def test_the_gate_records_the_build_and_verifier_it_trusted(workspace):
    """A graded pass links the promotion to the build it judged."""
    head, _ = _repo(workspace)
    attempt = E.graded("t", "claude", workspace,
                       E.Verifier(command=(sys.executable, "-c", "pass")),
                       record=E.Record())
    decision = P.decide(workspace, ['src/app.py'], attempt=attempt,
                        base_commit=head, approved=True)
    assert decision.allowed, decision.detail
    verified = next(c for c in decision.checks if c["check"] == "verified")
    assert verified["detail"].startswith(f"{head[:7]}")
    assert attempt.verifier_fingerprint in verified["detail"]


def _commit_worker_change(workspace, git):
    """What a worker that commits its own work leaves: HEAD one commit past the base."""
    (workspace / 'src' / 'app.py').write_text('def main(): return 2\n', encoding='utf-8')
    git('add', '-A')
    git('commit', '--quiet', '-m', 'worker')
    return git('rev-parse', 'HEAD').strip()


def test_a_pass_stamped_with_the_workers_committed_head_is_accepted(workspace):
    """
    A worker that commits moves HEAD off the base it started from. The verifier
    ran on that committed head and the stamp names it, so the pass is about the
    code that would land, and refusing it for not being the base would refuse
    every worker that commits.
    """
    base, git = _repo(workspace)
    head = _commit_worker_change(workspace, git)
    assert head != base
    attempt = E.graded("t", "claude", workspace,
                       E.Verifier(command=(sys.executable, "-c", "pass")),
                       record=E.Record())
    assert head.startswith(attempt.build_commit)
    decision = P.decide(workspace, ['src/app.py'], attempt=attempt, approved=True)
    assert decision.allowed, decision.detail
    verified = next(c for c in decision.checks if c["check"] == "verified")
    assert verified["detail"].startswith(attempt.build_commit)


def test_a_pass_stamped_with_a_different_commit_is_refused_after_the_worker_commits(workspace):
    """
    The verdict was reached on the base; the worker then committed on top. The
    stamp names a commit that is no longer the workspace's head, so the pass
    speaks for code that is not the code being promoted.
    """
    base, git = _repo(workspace)
    attempt = E.graded("t", "claude", workspace,
                       E.Verifier(command=(sys.executable, "-c", "pass")),
                       record=E.Record())
    assert base.startswith(attempt.build_commit)
    head = _commit_worker_change(workspace, git)
    decision = P.decide(workspace, ['src/app.py'], attempt=attempt, approved=True)
    assert not decision.allowed
    assert decision.reason == P.EVAL_OF_ANOTHER_BUILD
    assert base[:7] in decision.detail and head[:7] in decision.detail


def test_a_directory_that_is_not_a_repository_is_refused_not_assumed(tmp_path):
    ok, detail = P.base_matches(tmp_path, 'abc123')
    assert not ok
    assert detail
