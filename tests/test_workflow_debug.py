"""UCW-005: the Autonomous Debugging workflow (friday.debug v1).

A seeded bug lives in a throwaway git repo. Fake agents stand in for the
models; the red/green proof runs real pytest on the tmp trees."""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import textwrap

import pytest

from friday.ultracode import debug_workflow as DW
from friday.ultracode import definition as D

_ENV = {"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTEST_ADDOPTS": "",
        "PYTHONDONTWRITEBYTECODE": "1"}
_GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
        "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false"]

_CALC_BUG = "def average(values):\n    return sum(values) / (len(values) - 1)\n"
_CALC_OK = "def average(values):\n    return sum(values) / len(values)\n"
_TESTS = textwrap.dedent('''\
    from calc import average


    def test_a_total_is_unaffected():
        assert sum([1, 2]) == 3


    def test_average_of_two():
        assert average([2, 4]) == 3
    ''')
_REGRESSION = textwrap.dedent('''\


    def test_average_of_one_value():
        assert average([5]) == 5
    ''')
_RED_NODE = "tests/test_calc.py::test_average_of_one_value"
_FIRST = "tests/test_calc.py::test_average_of_two"


def git(root, *args):
    done = subprocess.run([*_GIT, *args], cwd=root, capture_output=True, text=True,
                          env={**os.environ, **_ENV})
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    (root / "tests").mkdir(parents=True)
    (root / "calc.py").write_text(_CALC_BUG)
    (root / "tests" / "test_calc.py").write_text(_TESTS)
    # a project that ignores build output and, oddly, a local conftest
    (root / ".gitignore").write_text("build/\ntests/conftest.py\n")
    git(root, "init", "-q")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "seed")
    return root


def write(root, rel, text):
    (root / rel).write_text(text)


def good_fix(root):
    write(root, "calc.py", _CALC_OK)
    with open(root / "tests" / "test_calc.py", "a") as handle:
        handle.write(_REGRESSION)
    return {"hypothesis": "the divisor is off by one", "files": ["calc.py"],
            "regression_tests": [_RED_NODE]}


class Fake:
    """A scripted agent: `fixes[n]` edits the tree on attempt n+1."""

    def __init__(self, root, fixes, tokens=10):
        self.root, self.fixes, self.tokens = root, list(fixes), tokens
        self.calls: list[tuple[str, str | None, dict]] = []
        self.fix_calls = 0

    async def __call__(self, step, handed, model):
        self.calls.append((step.id, model, handed.get("debug", {})))
        if step.id == "hypothesize":
            return {"hypotheses": [{"cause": "the divisor is off by one", "file": "calc.py"}],
                    "tokens_used": self.tokens}
        assert step.id == "fix"
        script = self.fixes[min(self.fix_calls, len(self.fixes) - 1)]
        self.fix_calls += 1
        out = script(self.root)
        return {**out, "tokens_used": self.tokens}


def run(repo, agent, **kw):
    kw.setdefault("env", _ENV)
    kw.setdefault("test_args", ["tests"])
    return asyncio.run(DW.autonomous_debug(repo, agent=agent, **kw))


def clean(repo):
    return git(repo, "status", "--porcelain", "-uall") == ""


def pytest_rc(root):
    return subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"],
                          cwd=root, capture_output=True, text=True,
                          env={**os.environ, **_ENV}).returncode


def test_the_definition_loads_compiles_and_names_no_model():
    definition = DW.load_definition()
    assert (definition.workflow_id, definition.version) == ("friday.debug", 1)
    compiled = D.compile_definition(definition)
    assert compiled.order == ("reproduce", "locate", "hypothesize", "fix", "verify")
    kinds = {s.id: s.kind for s in definition.steps}
    assert kinds["reproduce"] == kinds["locate"] == kinds["verify"] == "deterministic"


def test_a_seeded_bug_is_fixed_with_a_regression_test_that_was_red_before(repo):
    head = git(repo, "rev-parse", "HEAD")
    assert pytest_rc(repo) == 1                                  # the bug is real
    agent = Fake(repo, [good_fix])
    report = run(repo, agent)
    assert report["status"] == "FIXED"
    assert report["reproduction"]["first_critical"]["task_id"] == _FIRST
    assert report["reproduction"]["first_critical"]["failure_step"] == 5
    verdict = report["attempts"][-1]["verdict"]
    assert verdict["accepted"] is True
    assert verdict["red"] == {_RED_NODE: "FAILED"}                # failed on the pre-fix tree
    assert verdict["green"] == {_RED_NODE: "PASSED"}              # passes on the fixed tree
    assert pytest_rc(repo) == 0
    assert git(repo, "rev-parse", "HEAD") == head                # the loop never commits
    assert (repo / "calc.py").read_text() == _CALC_OK


def _fix_without_test(root):
    write(root, "calc.py", _CALC_OK)
    return {"hypothesis": "off by one", "files": ["calc.py"], "regression_tests": []}


def _fix_with_a_test_that_never_failed(root):
    write(root, "calc.py", _CALC_OK)
    with open(root / "tests" / "test_calc.py", "a") as handle:
        # [0, 0] averages to 0 with or without the bug, so this test never failed.
        handle.write("\n\ndef test_average_of_zeros():\n    assert average([0, 0]) == 0\n")
    return {"hypothesis": "off by one", "files": ["calc.py"],
            "regression_tests": ["tests/test_calc.py::test_average_of_zeros"]}


def _fix_naming_an_old_test(root):
    write(root, "calc.py", _CALC_OK)
    return {"hypothesis": "off by one", "files": ["calc.py"], "regression_tests": [_FIRST]}


def _fix_weakening_a_test(root):
    write(root, "calc.py", _CALC_OK)
    path = root / "tests" / "test_calc.py"
    path.write_text(path.read_text().replace("assert average([2, 4]) == 3", "assert True")
                    + _REGRESSION)
    return {"hypothesis": "off by one", "files": ["calc.py"], "regression_tests": [_RED_NODE]}


def _fix_skipping_a_test(root):
    write(root, "calc.py", _CALC_OK)
    path = root / "tests" / "test_calc.py"
    path.write_text(path.read_text().replace(
        "def test_average_of_two", "import pytest\n\n\n@pytest.mark.skip\ndef test_average_of_two")
        + _REGRESSION)
    return {"hypothesis": "off by one", "files": ["calc.py"], "regression_tests": [_RED_NODE]}


def _fix_deleting_a_test(root):
    write(root, "calc.py", _CALC_OK)
    (root / "tests" / "test_calc.py").unlink()
    write(root, "tests/test_new.py", "from calc import average\n\n\n"
          "def test_average_of_one_value():\n    assert average([5]) == 5\n")
    return {"hypothesis": "off by one", "files": ["calc.py"],
            "regression_tests": ["tests/test_new.py::test_average_of_one_value"]}


def _fix_editing_test_config(root):
    good_fix(root)
    write(root, "pytest.ini", "[pytest]\naddopts = --deselect tests/test_calc.py::test_average_of_two\n")
    return {"hypothesis": "off by one", "files": ["calc.py"], "regression_tests": [_RED_NODE]}


@pytest.mark.parametrize("fix, code", [
    (_fix_without_test, "NO_REGRESSION_TEST"),
    (_fix_with_a_test_that_never_failed, "NOT_RED_BEFORE"),
    (_fix_naming_an_old_test, "NOT_A_NEW_TEST"),
    (_fix_weakening_a_test, "TEST_WEAKENED"),
    (_fix_skipping_a_test, "TEST_WEAKENED"),
    (_fix_deleting_a_test, "TEST_DELETED"),
    (_fix_editing_test_config, "TEST_CONFIG_CHANGED"),
], ids=lambda v: getattr(v, "__name__", v))
def test_a_fix_without_a_failing_before_test_or_that_weakens_one_is_rejected(repo, fix, code):
    report = run(repo, Fake(repo, [fix]), max_attempts=1)
    assert report["status"] == "REJECTED"
    assert report["attempts"][-1]["verdict"]["code"] == code
    assert report["attempts"][-1]["verdict"]["accepted"] is False
    assert clean(repo)                                           # the rejected edit is undone
    assert (repo / "calc.py").read_text() == _CALC_BUG


def test_a_commit_made_by_the_agent_is_rejected(repo):
    def commits(root):
        out = good_fix(root)
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "agent")
        return out

    report = run(repo, Fake(repo, [commits]), max_attempts=1)
    assert report["attempts"][-1]["verdict"]["code"] == "HEAD_MOVED"
    assert report["status"] == "REJECTED"


def test_after_k_attempts_without_progress_the_strategy_and_model_change_and_it_is_recorded(repo):
    agent = Fake(repo, [_fix_without_test, _fix_without_test, good_fix])
    report = run(repo, agent, k=2, strategies=("minimal_patch", "rewrite_unit"),
                 models=("model-a", "model-b"))
    assert report["status"] == "FIXED"
    assert [a["strategy"] for a in report["attempts"]] == [
        "minimal_patch", "minimal_patch", "rewrite_unit"]
    assert [a["model"] for a in report["attempts"]] == ["model-a", "model-a", "model-b"]
    assert report["changes"] == [{
        "after_attempt": 2, "from_strategy": "minimal_patch", "to_strategy": "rewrite_unit",
        "from_model": "model-a", "to_model": "model-b",
        "reason": "2 attempts without progress"}]
    fixers = [call for call in agent.calls if call[0] == "fix"]
    assert [(m, ctx["strategy"], ctx["attempt"]) for _, m, ctx in fixers] == [
        ("model-a", "minimal_patch", 1), ("model-a", "minimal_patch", 2),
        ("model-b", "rewrite_unit", 3)]
    assert fixers[2][2]["tried"] == ["off by one", "off by one"]


def test_with_nothing_new_to_try_the_loop_stops_instead_of_repeating(repo):
    report = run(repo, Fake(repo, [_fix_without_test]), k=1, strategies=("only",), models=())
    assert report["status"] == "NO_NEW_STRATEGY"
    assert len(report["attempts"]) == 1


def test_the_loop_stops_at_its_token_budget_and_leaves_the_evidence(repo, tmp_path):
    evidence = tmp_path / "evidence" / "debug.json"
    report = run(repo, Fake(repo, [_fix_without_test], tokens=60000), budget_tokens=100000,
                 evidence_path=evidence, max_attempts=9)
    assert report["status"] == "BUDGET_TOKENS"
    assert report["tokens_spent"] >= 100000
    assert report["budget_tokens"] == 100000
    saved = json.loads(evidence.read_text())
    assert saved["status"] == "BUDGET_TOKENS"
    assert saved["attempts"] and saved["reproduction"]["failing"] == [_FIRST]
    assert clean(repo)


def test_the_loop_stops_at_its_time_budget(repo):
    ticks = iter(range(0, 10_000, 40))
    report = run(repo, Fake(repo, [_fix_without_test]), budget_seconds=100,
                 clock=lambda: float(next(ticks)), max_attempts=9)
    assert report["status"] == "BUDGET_TIME"
    assert report["budget_seconds"] == 100
    assert clean(repo)


def test_sibling_tasks_keep_running_while_the_debug_loop_runs(repo):
    async def main():
        ticks = 0
        stop = asyncio.Event()

        async def sibling():
            nonlocal ticks
            while not stop.is_set():
                ticks += 1
                await asyncio.sleep(0.005)

        task = asyncio.ensure_future(sibling())
        report = await DW.autonomous_debug(repo, agent=Fake(repo, [good_fix]), env=_ENV,
                                           test_args=["tests"])
        stop.set()
        await task
        return report, ticks

    report, ticks = asyncio.run(main())
    assert report["status"] == "FIXED"
    assert ticks >= 10          # the event loop was never blocked by the pytest runs


def test_a_failure_that_does_not_reproduce_spends_no_model_call(repo):
    write(repo, "calc.py", _CALC_OK)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "fixed already")
    agent = Fake(repo, [good_fix])
    report = run(repo, agent)
    assert report["status"] == "NOT_REPRODUCED"
    assert agent.calls == []


def test_a_dirty_tree_is_refused_because_the_loop_resets_it(repo):
    write(repo, "notes.txt", "mine")
    with pytest.raises(DW.DebugError, match="not clean"):
        run(repo, Fake(repo, [good_fix]))
    assert (repo / "notes.txt").read_text() == "mine"


# --- the review's bypasses: what git hides, what a test silently stops asserting --------

_PASS_ALL = textwrap.dedent('''\
    import pytest


    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_makereport(item, call):
        outcome = yield
        report = outcome.get_result()
        if report.failed:
            report.outcome = "passed"
    ''')
# fixes the one-value case but leaves average([2, 4]) wrong (6.0)
_HALF_FIX = ("def average(values):\n    if len(values) == 1:\n        return values[0]\n"
             "    return sum(values) / (len(values) - 1)\n")


def _half_fix(root):
    write(root, "calc.py", _HALF_FIX)
    return {"hypothesis": "one value", "files": ["calc.py"], "regression_tests": [_RED_NODE]}


def _fix_with_a_conftest_hidden_by_the_projects_gitignore(root):
    out = _half_fix(root)
    write(root, "tests/conftest.py", _PASS_ALL)          # ignored: git status cannot see it
    with open(root / "tests" / "test_calc.py", "a") as handle:
        handle.write(_REGRESSION)
    return out


def _fix_with_a_conftest_hidden_through_info_exclude(root):
    out = _fix_with_a_conftest_hidden_by_the_projects_gitignore(root)
    with open(root / ".git" / "info" / "exclude", "a") as handle:
        handle.write("\nconftest.py\n")
    return out


def _fix_with_a_conftest_hidden_through_a_new_gitignore(root):
    out = _fix_with_a_conftest_hidden_by_the_projects_gitignore(root)
    with open(root / ".gitignore", "a") as handle:
        handle.write("conftest.py\n")
    return out


def _fix_hiding_an_edit_with_assume_unchanged(root):
    out = _half_fix(root)
    with open(root / "tests" / "test_calc.py", "a") as handle:
        handle.write(_REGRESSION)
    git(root, "update-index", "--assume-unchanged", "tests/test_calc.py")
    return out


def _fix_with_an_early_return_inserted_in_an_old_test(root):
    out = _half_fix(root)
    path = root / "tests" / "test_calc.py"
    path.write_text(path.read_text().replace(
        "def test_average_of_two():\n", "def test_average_of_two():\n    return\n") + _REGRESSION)
    return out


def _fix_with_a_shadowing_assignment_in_an_old_test(root):
    out = _half_fix(root)
    path = root / "tests" / "test_calc.py"
    path.write_text(path.read_text().replace(
        "def test_average_of_two():\n",
        "def test_average_of_two():\n    average = lambda values: 3\n") + _REGRESSION)
    return out


@pytest.mark.parametrize("fix, code", [
    (_fix_with_a_conftest_hidden_by_the_projects_gitignore, "HIDDEN_FILE"),
    (_fix_with_a_conftest_hidden_through_info_exclude, "GIT_CONFIG_CHANGED"),
    (_fix_with_a_conftest_hidden_through_a_new_gitignore, "HIDDEN_FILE"),
    (_fix_hiding_an_edit_with_assume_unchanged, "HIDDEN_CHANGE"),
    (_fix_with_an_early_return_inserted_in_an_old_test, "TEST_WEAKENED"),
    (_fix_with_a_shadowing_assignment_in_an_old_test, "TEST_WEAKENED"),
], ids=lambda v: getattr(v, "__name__", v))
def test_a_fix_that_hides_a_file_or_silences_an_old_test_is_rejected(repo, fix, code):
    report = run(repo, Fake(repo, [fix]), max_attempts=1)
    assert report["status"] == "REJECTED"
    assert report["attempts"][-1]["verdict"]["code"] == code
    assert report["attempts"][-1]["verdict"]["accepted"] is False
    assert (repo / "calc.py").read_text() == _CALC_BUG        # the rejected edit is undone


def test_a_rejected_attempt_leaves_exactly_the_baseline_ignored_files_included(repo):
    exclude = repo / ".git" / "info" / "exclude"
    before = exclude.read_bytes() if exclude.exists() else None

    def leaves_junk(root):
        (root / "build").mkdir()
        write(root, "build/artifact.txt", "x")
        write(root, "tests/conftest.py", _PASS_ALL)
        with open(root / ".git" / "info" / "exclude", "a") as handle:
            handle.write("\nconftest.py\n")
        return _fix_without_test(root)

    report = run(repo, Fake(repo, [leaves_junk]), max_attempts=1)
    assert report["attempts"][-1]["verdict"]["accepted"] is False
    assert not (repo / "build").exists()
    assert not (repo / "tests" / "conftest.py").exists()
    assert (exclude.read_bytes() if exclude.exists() else None) == before
    assert git(repo, "ls-files", "-o", "-i", "--exclude-standard") == ""
    assert clean(repo)


def test_a_tree_holding_ignored_files_is_refused_because_the_loop_deletes_them(repo):
    (repo / "build").mkdir()
    write(repo, "build/keep.txt", "mine")
    with pytest.raises(DW.DebugError, match="ignored files"):
        run(repo, Fake(repo, [good_fix]))
    assert (repo / "build" / "keep.txt").read_text() == "mine"


def test_agent_code_runs_without_friday_credentials(repo, monkeypatch):
    monkeypatch.setenv("FAKE_PROVIDER_API_KEY", "sekrit")
    write(repo, "tests/test_env.py",
          "import os\n\n\ndef test_no_credential_is_visible():\n"
          "    assert 'FAKE_PROVIDER_API_KEY' not in os.environ\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "env probe")
    assert "FAKE_PROVIDER_API_KEY" not in DW.child_env()
    assert DW.child_env({"KEPT": "1"})["KEPT"] == "1"
    report = run(repo, Fake(repo, [_fix_without_test]), max_attempts=1)
    assert report["reproduction"]["failing"] == [_FIRST]        # the env probe passed


def test_no_test_run_starts_once_the_time_budget_is_spent(repo, monkeypatch):
    agent = Fake(repo, [good_fix])
    pytest_runs = []
    real = DW._run

    def counting(cmd, *args, **kw):
        if "pytest" in cmd:
            pytest_runs.append(cmd)
        return real(cmd, *args, **kw)

    monkeypatch.setattr(DW, "_run", counting)
    # the clock jumps past the budget the moment the fix step has run
    report = run(repo, agent, budget_seconds=100, max_attempts=3,
                 clock=lambda: 1000.0 if agent.fix_calls else 0.0)
    assert len(pytest_runs) == 1                                 # only the reproduction
    assert report["attempts"][0]["verdict"]["code"] == "TIMEOUT"
    assert report["status"] == "BUDGET_TIME"
    assert clean(repo)


def _dir_link(target, link):
    """A directory link no privilege is needed for: a junction on Windows, a
    symlink elsewhere."""
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
    else:
        os.symlink(target, link, target_is_directory=True)


@pytest.mark.parametrize("with_file", [True, False], ids=["link_with_a_file", "empty_link"])
def test_a_fix_that_leaves_a_link_out_of_the_tree_is_rejected(repo, with_file):
    outside = repo.parent / "outside"
    outside.mkdir()
    if with_file:                       # git follows the link and reports this as ours
        write(outside, "helper.py", "VALUE = 1\n")

    def links_out(root):
        out = good_fix(root)
        _dir_link(outside, root / "vendor")
        return out

    report = run(repo, Fake(repo, [links_out]), max_attempts=1)
    verdict = report["attempts"][-1]["verdict"]
    assert verdict["code"] == "LINK_IN_TREE" and verdict["accepted"] is False
    assert "vendor" in verdict["reason"]
    assert not os.path.lexists(repo / "vendor")                  # the link is undone
    assert (outside / "helper.py").exists() is with_file         # nothing behind it is
    assert (repo / "calc.py").read_text() == _CALC_BUG
    assert clean(repo)
