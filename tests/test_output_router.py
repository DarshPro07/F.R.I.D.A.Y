"""
ORCH-007: output reaching a model is compressed where exact text is not needed,
with the raw output recoverable.

Test, git and search output reaches a model as counts and failures inside a
size cap, the full output stays in the artifact vault behind a pointer, a
patch or unknown text passes raw, and nothing is compressed twice.
"""
from __future__ import annotations

import pytest

from friday import compression as C
from friday import output_router as R
from scripts import pytest_brief

CAP = 600


@pytest.fixture()
def coordinator(tmp_path):
    return C.CompressionCoordinator(C.ArtifactVault(tmp_path / "vault"))


def blobs(coordinator):
    root = coordinator.vault.root / "blobs"
    return [p for p in root.rglob("*") if p.is_file()] if root.exists() else []


def pytest_output(failed=2, passed=300):
    tb = "".join("tests/test_x.py:%d: in test_body\n    assert helper() == %d\n" % (i, i) * 3
                 for i in range(40))
    fails = "".join("FAILED tests/test_x.py::test_case_%d - AssertionError: boom %d\n" % (i, i)
                    for i in range(failed))
    return ("." * 200 + "\n" + tb + "=== short test summary info ===\n" + fails
            + "=== %d failed, %d passed in 3.21s ===\n" % (failed, passed))


def git_status(n=80):
    return "".join(" M friday/mod_%d.py\n" % i for i in range(n)) + \
        "".join("?? data/run_%d.json\n" % i for i in range(n // 2))


def diff_stat(n=70):
    body = "".join(" friday/mod_%d.py | %d ++++----\n" % (i, i + 1) for i in range(n))
    return body + " %d files changed, 900 insertions(+), 400 deletions(-)\n" % n


def search_hits(files=40, per=5):
    return "".join("friday/mod_%d.py:%d:    needle = compute(%d)\n" % (f, ln, ln)
                   for f in range(files) for ln in range(1, per + 1))


def test_pytest_output_becomes_counts_and_failures_within_the_cap(coordinator):
    raw = pytest_output()
    out = R.route(raw, cap=CAP, coordinator=coordinator)
    assert out.kind == "pytest" and out.compressed
    assert len(out.text) <= CAP + 200  # the cap bounds the brief; the pointer footer rides on top
    assert "2 failed, 300 passed in 3.21s" in out.text
    assert "tests/test_x.py::test_case_0 - AssertionError: boom 0" in out.text
    assert "test_body" not in out.text and "...." not in out.text


def test_the_pytest_brief_is_the_one_in_scripts_pytest_brief(coordinator):
    raw = pytest_output(failed=25)
    out = R.route(raw, cap=5000, coordinator=coordinator)
    for line in pytest_brief.digest(raw):
        assert line in out.text


def test_the_full_output_is_kept_and_recoverable_by_the_pointer(coordinator):
    raw = pytest_output()
    out = R.route(raw, cap=CAP, coordinator=coordinator)
    assert out.recovery.startswith(C.POINTER) and out.recovery in out.text
    assert R.full_output(out.recovery, coordinator=coordinator) == raw
    assert len(blobs(coordinator)) == 1


def test_git_status_becomes_counts(coordinator):
    out = R.route(git_status(), cap=CAP, coordinator=coordinator)
    assert out.kind == "git" and out.compressed
    assert "80 modified" in out.text and "40 untracked" in out.text
    assert len(out.text) <= CAP + 200


def test_git_diff_stat_keeps_the_summary_line(coordinator):
    out = R.route(diff_stat(), cap=CAP, coordinator=coordinator)
    assert out.kind == "git" and out.compressed
    assert "70 files changed, 900 insertions(+), 400 deletions(-)" in out.text
    assert len(out.text) <= CAP + 200


def test_git_log_becomes_a_count_and_the_newest_commits(coordinator):
    raw = "".join("%07x commit message number %d\n" % (0xabc0000 + i, i) for i in range(120))
    out = R.route(raw, cap=CAP, coordinator=coordinator)
    assert out.kind == "git" and "120 commits" in out.text
    assert "commit message number 0" in out.text and "commit message number 119" not in out.text


def test_search_output_becomes_match_and_file_counts(coordinator):
    out = R.route(search_hits(), cap=CAP, coordinator=coordinator)
    assert out.kind == "search" and out.compressed
    assert "200 matches in 40 files" in out.text
    assert len(out.text) <= CAP + 200
    assert "friday/mod_0.py (5)" in out.text


def test_output_under_the_cap_passes_raw_and_stores_nothing(coordinator):
    raw = "2 passed in 0.10s\n"
    out = R.route(raw, cap=CAP, coordinator=coordinator)
    assert out.text == raw and not out.compressed and out.recovery == ""
    assert blobs(coordinator) == []


def test_a_patch_is_exact_text_and_passes_raw(coordinator):
    raw = "diff --git a/f.py b/f.py\n--- a/f.py\n+++ b/f.py\n" + "".join(
        "@@ -%d +%d @@\n-old line %d\n+new line %d\n" % (i, i, i, i) for i in range(80))
    out = R.route(raw, cap=CAP, coordinator=coordinator)
    assert out.text == raw and not out.compressed
    assert blobs(coordinator) == []


def test_text_it_cannot_recognise_passes_raw(coordinator):
    raw = "the worker rambled about nothing in particular " * 60
    out = R.route(raw, cap=CAP, coordinator=coordinator)
    assert out.text == raw and out.kind == "" and not out.compressed


def test_a_caller_tag_that_marks_text_exact_keeps_it_raw(coordinator):
    out = R.route(pytest_output(), cap=CAP, tags=("exact_literal",), coordinator=coordinator)
    assert not out.compressed and "test_body" in out.text


def test_routed_output_is_never_compressed_a_second_time(coordinator):
    first = R.route(pytest_output(), cap=CAP, coordinator=coordinator)
    second = R.route(first.text, cap=10, coordinator=coordinator)
    assert second.text == first.text and not second.compressed
    assert len(blobs(coordinator)) == 1


def test_exactly_one_transformation_is_recorded(coordinator):
    out = R.route(pytest_output(), cap=CAP, coordinator=coordinator)
    assert out.record.parent_sha256 == "" and out.record.backend == "output_router.pytest"
    assert out.record.origin_sha256 == C.sha256(pytest_output())


def test_a_vault_that_cannot_write_returns_the_raw_output(tmp_path):
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("x")
    coordinator = C.CompressionCoordinator(C.ArtifactVault(blocked))
    raw = pytest_output()
    out = R.route(raw, cap=CAP, coordinator=coordinator)
    assert out.text == raw and not out.compressed and out.record.refusal
