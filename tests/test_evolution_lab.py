"""
EVOLAB-001 slice 1: the Protected Evaluation Lab core.

Every test builds a real throwaway git repository, so "the pristine copy at the
base commit" is read by real `git show`, not by a stub. The only injected piece
is the runner, which stands in for pytest and records what it was given.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from friday import evolution_lab as lab_mod
from friday.evolution_lab import Lab, RunResult

CALC = "def add(a, b):\n    return a + b\n"
TESTS = (
    "from calc import add\n"
    "import pytest\n\n"
    "def test_add():\n    assert add(1, 2) == 3\n    assert add(0, 0) == 0\n\n"
    "def test_add_negative():\n    assert add(-1, 1) == 0\n\n"
    "def test_type():\n    with pytest.raises(TypeError):\n        add(1, None)\n"
)
CONFTEST = "import sys\n"
EVALUATOR = "def verify():\n    return True\n"


def _git(repo: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        cwd=str(repo), capture_output=True, text=True, timeout=60, check=True)
    return out.stdout.strip()


class Runner:
    """Stands in for pytest: records the call and what the test file looked like."""

    def __init__(self, exit_code: int = 0, counts: dict | None = None):
        self.calls: list[dict] = []
        self.result = RunResult(exit_code, counts or {"passed": 3})

    def __call__(self, command, cwd, env):
        cwd = Path(cwd)
        self.calls.append({
            "command": list(command), "cwd": cwd, "env": dict(env),
            "test_text": (cwd / "tests" / "test_calc.py").read_text(),
            "conftest_text": (cwd / "tests" / "conftest.py").read_text(),
        })
        return self.result


@pytest.fixture
def world(tmp_path):
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    (repo / "friday").mkdir()
    (repo / "calc.py").write_text(CALC)
    (repo / "tests" / "test_calc.py").write_text(TESTS)
    (repo / "tests" / "conftest.py").write_text(CONFTEST)
    (repo / "friday" / "evaluation.py").write_text(EVALUATOR)
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")
    sandbox = tmp_path / "sandbox"
    shutil.copytree(repo, sandbox, ignore=shutil.ignore_patterns(".git"))
    lab = Lab(repo, tmp_path / "archive")
    return lab, base, sandbox, repo, tmp_path / "archive"


TEST_ID = ["tests/test_calc.py"]


def _archive(archive: Path) -> list[dict]:
    path = archive / "rejections.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def test_honest_change_passes_and_gets_a_receipt(world):
    lab, base, sandbox, _repo, archive = world
    (sandbox / "calc.py").write_text(CALC + "\n\ndef sub(a, b):\n    return a - b\n")
    runner = Runner()
    j = lab.judge(base, sandbox, TEST_ID, runner)
    assert j.verdict == "PASS", j.reasons
    assert len(runner.calls) == 1
    assert j.receipt is not None and j.receipt["exit_code"] == 0
    assert _archive(archive) == []


def test_removed_assert_is_weakened_and_never_run(world):
    lab, base, sandbox, _repo, archive = world
    (sandbox / "tests" / "test_calc.py").write_text(
        TESTS.replace("    assert add(0, 0) == 0\n", ""))
    runner = Runner()
    j = lab.judge(base, sandbox, TEST_ID, runner)
    assert j.verdict == "WEAKENED"
    assert runner.calls == []
    assert any("assert" in r for r in j.reasons)
    rows = _archive(archive)
    assert len(rows) == 1 and rows[0]["verdict"] == "WEAKENED"


def test_removed_pytest_raises_is_weakened(world):
    lab, base, sandbox, _repo, _a = world
    weaker = TESTS.replace(
        "    with pytest.raises(TypeError):\n        add(1, None)\n",
        "    assert True\n")
    (sandbox / "tests" / "test_calc.py").write_text(weaker)
    j = lab.judge(base, sandbox, TEST_ID, Runner())
    assert j.verdict == "WEAKENED"


@pytest.mark.parametrize("marker", [
    "@pytest.mark.skip(reason='later')",
    "@pytest.mark.skipif(True, reason='later')",
    "@pytest.mark.xfail",
])
def test_added_skip_or_xfail_is_weakened(world, marker):
    lab, base, sandbox, _repo, _a = world
    (sandbox / "tests" / "test_calc.py").write_text(
        TESTS.replace("def test_add():", f"{marker}\ndef test_add():"))
    runner = Runner()
    j = lab.judge(base, sandbox, TEST_ID, runner)
    assert j.verdict == "WEAKENED"
    assert runner.calls == []


def test_added_importorskip_is_weakened(world):
    lab, base, sandbox, _repo, _a = world
    (sandbox / "tests" / "test_calc.py").write_text(
        TESTS.replace("def test_type():",
                      "def test_type():\n    pytest.importorskip('nothing_here')"))
    assert lab.judge(base, sandbox, TEST_ID, Runner()).verdict == "WEAKENED"


def test_fewer_collected_tests_is_weakened(world):
    lab, base, sandbox, _repo, _a = world
    (sandbox / "tests" / "test_calc.py").write_text(
        TESTS.split("def test_add_negative")[0])
    j = lab.judge(base, sandbox, TEST_ID, Runner())
    assert j.verdict == "WEAKENED"
    assert any("collected" in r for r in j.reasons)


def test_edited_conftest_is_tampered(world):
    lab, base, sandbox, _repo, archive = world
    (sandbox / "tests" / "conftest.py").write_text(CONFTEST + "collect_ignore_glob = ['*']\n")
    runner = Runner()
    j = lab.judge(base, sandbox, TEST_ID, runner)
    assert j.verdict == "TAMPERED"
    assert runner.calls == []
    assert _archive(archive)[0]["verdict"] == "TAMPERED"


def test_added_conftest_that_base_lacks_is_tampered(world):
    lab, base, sandbox, _repo, _a = world
    (sandbox / "conftest.py").write_text("collect_ignore_glob = ['*']\n")
    assert lab.judge(base, sandbox, TEST_ID, Runner()).verdict == "TAMPERED"


def test_edited_pytest_config_is_tampered(world, tmp_path):
    lab, base, sandbox, _repo, _a = world
    # the base has no pytest.ini; a candidate that adds one is changing the judge
    (sandbox / "pytest.ini").write_text("[pytest]\naddopts = --deselect tests\n")
    assert lab.judge(base, sandbox, TEST_ID, Runner()).verdict == "TAMPERED"


def test_edited_evaluator_is_tampered(world):
    lab, base, sandbox, _repo, _a = world
    (sandbox / "friday" / "evaluation.py").write_text("def verify():\n    return True  # always\n")
    assert lab.judge(base, sandbox, TEST_ID, Runner()).verdict == "TAMPERED"


def test_the_runner_sees_the_pristine_tests_not_the_candidates(world):
    lab, base, sandbox, _repo, _a = world
    candidate = TESTS + "\n\ndef test_extra():\n    assert add(2, 2) == 4\n"
    (sandbox / "tests" / "test_calc.py").write_text(candidate)
    runner = Runner()
    j = lab.judge(base, sandbox, TEST_ID, runner)
    assert j.verdict == "PASS", j.reasons
    assert runner.calls[0]["test_text"] == TESTS
    # and the candidate's sandbox itself was not written to
    assert (sandbox / "tests" / "test_calc.py").read_text() == candidate


def test_the_run_happens_in_a_copy_and_the_live_checkout_is_untouched(world):
    lab, base, sandbox, repo, _a = world
    runner = Runner()
    before = (repo / "tests" / "test_calc.py").read_bytes()
    lab.judge(base, sandbox, TEST_ID, runner)
    cwd = runner.calls[0]["cwd"]
    assert cwd.resolve() not in (repo.resolve(), sandbox.resolve())
    assert repo.resolve() not in cwd.resolve().parents
    assert (repo / "tests" / "test_calc.py").read_bytes() == before


def test_a_sandbox_that_is_the_live_checkout_is_refused(world):
    lab, base, _sandbox, repo, _a = world
    with pytest.raises(ValueError):
        lab.judge(base, repo, TEST_ID, Runner())


def test_a_failing_run_is_FAIL_with_a_receipt(world):
    lab, base, sandbox, _repo, _a = world
    j = lab.judge(base, sandbox, TEST_ID, Runner(exit_code=1, counts={"failed": 1}))
    assert j.verdict == "FAIL"
    assert j.receipt is not None and j.receipt["exit_code"] == 1


def test_the_run_environment_is_scrubbed_of_secrets(world, monkeypatch):
    lab, base, sandbox, _repo, _a = world
    monkeypatch.setenv("OPENAI_API_KEY", "sk-not-real")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "not-real")
    monkeypatch.setenv("SOME_PLAIN_VAR", "x")
    runner = Runner()
    lab.judge(base, sandbox, TEST_ID, runner)
    env = runner.calls[0]["env"]
    assert "OPENAI_API_KEY" not in env and "LIVEKIT_API_SECRET" not in env
    assert "SOME_PLAIN_VAR" not in env


def test_a_receipt_this_lab_issued_is_accepted_as_evidence(world):
    lab, base, sandbox, _repo, _a = world
    first = lab.judge(base, sandbox, TEST_ID, Runner())
    second = lab.judge(base, sandbox, TEST_ID, Runner(), claimed=first.receipt)
    assert second.verdict == "PASS", second.reasons


def test_a_forged_receipt_is_fabricated(world):
    lab, base, sandbox, _repo, archive = world
    forged = {"run_id": "deadbeef", "command": ["pytest"], "exit_code": 0,
              "counts": {"passed": 99}, "overlay_hash": "0" * 64,
              "digest": "f" * 64}
    runner = Runner()
    j = lab.judge(base, sandbox, TEST_ID, runner, claimed=forged)
    assert j.verdict == "FABRICATED"
    assert runner.calls == []
    assert _archive(archive)[0]["verdict"] == "FABRICATED"


def test_an_edited_real_receipt_is_fabricated(world):
    lab, base, sandbox, _repo, _a = world
    real = lab.judge(base, sandbox, TEST_ID, Runner(exit_code=1, counts={"failed": 1})).receipt
    edited = dict(real, exit_code=0)
    assert lab.judge(base, sandbox, TEST_ID, Runner(), claimed=edited).verdict == "FABRICATED"
    # a receipt whose digest was recomputed by the forger is still not in the ledger
    edited["digest"] = lab_mod.receipt_digest(edited)
    assert lab.judge(base, sandbox, TEST_ID, Runner(), claimed=edited).verdict == "FABRICATED"


def test_test_paths_cannot_escape_the_sandbox(world):
    lab, base, sandbox, _repo, _a = world
    for bad in (["../outside.py"], ["/etc/passwd"]):
        with pytest.raises(ValueError):
            lab.judge(base, sandbox, bad, Runner())


def test_a_base_commit_that_looks_like_an_option_is_refused(world):
    lab, _base, sandbox, _repo, _a = world
    with pytest.raises(ValueError):
        lab.judge("--output=x", sandbox, TEST_ID, Runner())


def test_the_judge_is_kernel():
    from friday import self_upgrade
    assert self_upgrade.is_kernel_path("friday/evolution_lab.py")
    assert self_upgrade.is_kernel_path("tests/test_evolution_lab.py")
