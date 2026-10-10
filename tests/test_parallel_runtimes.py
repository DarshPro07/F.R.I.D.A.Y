"""
FAB-002: one task, two runtimes, isolated worktrees, receipts, integration.

The coordinator, the worktree manager and git are real. The runtimes are
either small in-process stand-ins (to pin each rule on its own) or the real
Codex and Claude Code adapters over a faked CLI process (the end-to-end test).
"""
from __future__ import annotations

import subprocess
import sys
import threading
from pathlib import Path

import pytest

from friday.executors import claude_code as CC
from friday.executors import cli
from friday.executors import work_packet as wp
from friday.executors.claude_packet import ClaudeCodeRuntime
from friday.executors.parallel import run_parallel
from friday.executors.worktrees import WorktreeManager
from friday.store import Store

FAKE = str(Path(__file__).parent / "fake_coding_cli.py")
HAS_CACHE = (sys.executable, "-c",
             "import pathlib, sys; "
             "sys.exit(0 if 'cache' in pathlib.Path('friday/uploader.py').read_text() else 1)")


def _git(path, *args):
    return subprocess.run(["git", *args], cwd=str(path), capture_output=True,
                          text=True, check=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    path = tmp_path / "repo"
    (path / "friday").mkdir(parents=True)
    (path / "friday" / "uploader.py").write_text("def upload(): ...\n", encoding="utf-8")
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.name", "ADA")
    _git(path, "config", "user.email", "ada@example.invalid")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "initial")
    return path


@pytest.fixture
def manager(repo):
    return WorktreeManager(repo)


def _packet(workspace, *, budget_usd=0.0, checks=(HAS_CACHE,)):
    bundle = CC.TaskBundle(goal="add a TTL cache to the uploader",
                           workspace=str(workspace), isolate=False,
                           allowed_paths=("friday/uploader.py",),
                           acceptance=("entries expire",))
    return wp.WorkPacket(bundle, wp.Budget(usd=budget_usd, seconds=60.0), checks)


class Stub:
    """A runtime stand-in: writes `files` into the workspace it is given."""

    def __init__(self, name, files=None, *, status=wp.DONE, cost=0.0, barrier=None,
                 boom=None):
        self.runtime, self.files, self.status = name, files or {}, status
        self.cost, self.barrier, self.boom = cost, barrier, boom
        self.workspace = ""

    def run(self, packet, workspace):
        self.workspace = workspace
        if self.barrier is not None:
            self.barrier.wait()          # both runtimes must be running at once
        if self.boom:
            raise RuntimeError(self.boom)
        for rel, text in self.files.items():
            target = Path(workspace) / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        return wp.Receipt(self.runtime, self.status, model="stub", result="done",
                          cost_usd=self.cost)


CACHE = {"friday/uploader.py": "cache = {}\n"}


def test_both_runtimes_run_at_once_in_their_own_worktrees_and_one_is_integrated(
        repo, manager):
    barrier = threading.Barrier(2, timeout=30)
    first = Stub("alpha", CACHE, barrier=barrier)
    second = Stub("beta", {"friday/uploader.py": "cache = {1: 2}\n"}, barrier=barrier)

    outcome = run_parallel(_packet(repo), [first, second], manager, target="main")

    # simultaneous: a sequential coordinator would time the barrier out
    assert [r.status for r in outcome.receipts] == [wp.DONE, wp.DONE]
    # isolated: two different worktrees, neither the main checkout
    assert first.workspace != second.workspace
    assert Path(first.workspace).resolve() != repo.resolve()
    assert Path(second.workspace).resolve() != repo.resolve()
    # integrated by FRIDAY: the first qualifying receipt landed on main
    assert outcome.integrated.runtime == "alpha"
    assert (repo / "friday" / "uploader.py").read_text() == "cache = {}\n"
    assert _git(repo, "log", "-1", "--format=%P").count(" ") == 1     # a merge commit
    assert outcome.integrated.commit
    # receipts: evidence for both, and why the other one did not land
    for receipt in outcome.receipts:
        assert receipt.files_changed == ["friday/uploader.py"]
        assert [a["passed"] for a in receipt.acceptance] == [True]
    assert any("alpha was integrated instead" in w for w in outcome.receipts[1].warnings)
    # nothing left behind for work that landed or was discarded as a duplicate
    assert not Path(first.workspace).exists() and not Path(second.workspace).exists()


class SignedOut:
    runtime = "alpha"

    def run(self, packet, workspace):
        return wp.Receipt("alpha", wp.UNAVAILABLE, warnings=["signed out: run `alpha login`"])


class Liar(Stub):
    def run(self, packet, workspace):
        receipt = super().run(packet, workspace)
        receipt.runtime = "claude-code"
        return receipt


def test_a_runtime_that_is_not_signed_in_is_reported_never_impersonated(repo, manager):
    outcome = run_parallel(_packet(repo), [SignedOut(), Stub("beta", CACHE)], manager,
                           target="main", run_id="t1")

    by_runtime = {r.runtime: r for r in outcome.receipts}
    assert by_runtime["alpha"].status == wp.UNAVAILABLE
    assert by_runtime["alpha"].warnings == ["signed out: run `alpha login`"]
    assert by_runtime["alpha"].files_changed == [] and by_runtime["alpha"].commit == ""
    assert outcome.integrated.runtime == "beta"      # not relabelled as alpha's work
    assert not (repo / ".claude" / "worktrees" / "t1-alpha").exists()


def test_a_receipt_cannot_claim_to_be_another_runtime(repo, manager):
    outcome = run_parallel(_packet(repo), [Liar("alpha", CACHE)], manager, target="main")
    assert [r.runtime for r in outcome.receipts] == ["alpha"]


def test_a_runtime_that_failed_acceptance_is_not_integrated_and_is_kept(repo, manager):
    failing = (sys.executable, "-c", "raise SystemExit(1)")
    outcome = run_parallel(_packet(repo, checks=(failing,)),
                           [Stub("alpha", CACHE)], manager, target="main")
    (receipt,) = outcome.receipts
    assert receipt.status == wp.FAILED
    assert [a["passed"] for a in receipt.acceptance] == [False]
    assert any("acceptance tests failed" in w for w in receipt.warnings)
    assert outcome.integrated is None
    assert (repo / "friday" / "uploader.py").read_text() == "def upload(): ...\n"
    assert Path(receipt.workspace).is_dir(), "the failing work is the evidence"


def test_a_runtime_that_touches_files_outside_the_packet_is_not_integrated(repo, manager):
    outcome = run_parallel(
        _packet(repo), [Stub("alpha", {**CACHE, "friday/secrets.py": "x = 1\n"})],
        manager, target="main")
    (receipt,) = outcome.receipts
    assert receipt.status == wp.FAILED
    assert any("friday/secrets.py" in w for w in receipt.warnings)
    assert outcome.integrated is None


def test_a_new_file_inside_a_listed_directory_is_in_scope(repo, manager):
    bundle = CC.TaskBundle(goal="add a cache", workspace=str(repo), isolate=False,
                           allowed_paths=("friday/",))
    packet = wp.WorkPacket(bundle, wp.Budget(0.0, 60.0), (HAS_CACHE,))
    files = {**CACHE, "friday/newpkg/helper.py": "x = 1\n"}
    outcome = run_parallel(packet, [Stub("alpha", files)], manager, target="main")
    assert outcome.integrated is not None, outcome.receipts[0].warnings
    assert "friday/newpkg/helper.py" in outcome.receipts[0].files_changed


def test_a_cost_over_the_budget_is_not_integrated(repo, manager):
    outcome = run_parallel(_packet(repo, budget_usd=0.0),
                           [Stub("alpha", CACHE, cost=1.25)], manager, target="main")
    assert outcome.receipts[0].status == wp.OVER_BUDGET
    assert outcome.integrated is None


def test_done_with_nothing_changed_is_not_done(repo, manager):
    outcome = run_parallel(_packet(repo), [Stub("alpha", {})], manager, target="main")
    assert outcome.receipts[0].status == wp.FAILED
    assert any("nothing changed" in w for w in outcome.receipts[0].warnings)


def test_a_runtime_that_raises_fails_alone_with_its_reason(repo, manager):
    outcome = run_parallel(
        _packet(repo), [Stub("alpha", boom="gateway exploded"), Stub("beta", CACHE)],
        manager, target="main")
    assert outcome.receipts[0].status == wp.FAILED
    assert "RuntimeError: gateway exploded" in outcome.receipts[0].warnings[0]
    assert outcome.integrated.runtime == "beta"


def test_duplicate_runtimes_are_refused(repo, manager):
    with pytest.raises(ValueError, match="distinct"):
        run_parallel(_packet(repo), [Stub("alpha"), Stub("alpha")], manager, target="main")


# -- the real adapters, over a faked CLI process -------------------------------


class _FakeRun:
    """The claude CLI process, faked: it edits the file and says it is done."""

    def __init__(self, launch, sandbox=None):
        self.launch, self.process, self.session_id = launch, None, ""

    async def events(self, *, timeout: float = 1800.0):
        target = Path(self.launch.cwd) / "friday" / "uploader.py"
        target.write_text("cache = {'claude': True}\n", encoding="utf-8")
        yield cli.Event("init", {"session_id": "s-1"})
        yield cli.Event("tool", {"type": "assistant"}, name="Edit")
        yield cli.Event("result", {"result": "Added the cache.", "is_error": False,
                                   "num_turns": 2, "total_cost_usd": 0.0})


class _SignedIn:
    def locate(self):
        return "claude"

    def signed_in(self):
        return True, "logged in"


def _codex(tmp_path, mode):
    from friday.executors.codex import CodexExecutor

    class FakeCodex(CodexExecutor):
        prefix = [FAKE, "codex", mode, "friday/uploader.py", str(tmp_path / "codex.json")]

        def locate(self):
            return sys.executable

        def status_command(self, path):
            return [path, *self.prefix, "login", "status"]

        def work_command(self, path, model):
            return [path, *self.prefix, *super().work_command(path, model)[1:]]

    return FakeCodex()


def test_claude_code_and_codex_complete_one_task_and_friday_integrates_it(
        tmp_path, repo, manager, monkeypatch):
    monkeypatch.setattr(cli, "available", lambda: True)
    monkeypatch.setattr(cli, "Run", _FakeRun)
    claude = ClaudeCodeRuntime(CC.ClaudeCodeExecutor(Store(tmp_path / "ada.sqlite3")),
                               backend=_SignedIn())

    outcome = run_parallel(_packet(repo), [claude, _codex(tmp_path, "ok")], manager,
                           target="main")

    by_runtime = {r.runtime: r for r in outcome.receipts}
    assert set(by_runtime) == {"claude-code", "codex"}
    for receipt in by_runtime.values():
        assert receipt.status == wp.DONE, receipt.warnings
        assert receipt.result == "Added the cache."
        assert [a["passed"] for a in receipt.acceptance] == [True]
    assert by_runtime["codex"].usage["input_tokens"] == 120
    assert outcome.integrated.runtime == "claude-code"
    assert (repo / "friday" / "uploader.py").read_text() == "cache = {'claude': True}\n"


def test_a_signed_out_codex_next_to_claude_code_is_reported_not_replaced(
        tmp_path, repo, manager, monkeypatch):
    monkeypatch.setattr(cli, "available", lambda: True)
    monkeypatch.setattr(cli, "Run", _FakeRun)
    claude = ClaudeCodeRuntime(CC.ClaudeCodeExecutor(Store(tmp_path / "ada.sqlite3")),
                               backend=_SignedIn())

    outcome = run_parallel(_packet(repo), [_codex(tmp_path, "signed_out"), claude],
                           manager, target="main")

    codex, claude_receipt = outcome.receipts
    assert codex.runtime == "codex" and codex.status == wp.UNAVAILABLE
    assert any("codex login" in w for w in codex.warnings)
    assert outcome.integrated is claude_receipt
    assert not (tmp_path / "codex.json").exists(), "a signed-out CLI was launched"
