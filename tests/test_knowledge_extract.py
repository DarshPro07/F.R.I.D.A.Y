"""
BIN-001 slice 1: the knowledge-extraction core.

A fixture repo (tests/fixtures/re_repo) stands in for the Apache-2.0 reverse
engineering course. The point of the module: tutorials become attributed,
quarantined skills, and nothing in the tree is ever executed or even opened
unless it was explicitly selected.
"""

from __future__ import annotations

import builtins
import io
import shutil
from pathlib import Path

import pytest

from friday import knowledge_extract as ke
from friday.skills import contracts as K
from friday.skills import files as F

FIXTURE = Path(__file__).parent / "fixtures" / "re_repo"
COMMIT = "0123456789abcdef0123456789abcdef01234567"
SELECTION = {
    "binary_analysis_skill": ["tutorials/binary_basics.md"],
    "windows_internals_skill": ["tutorials/windows_peb.md"],
    "firmware_debugging_skill": ["tutorials/firmware_uart.md"],
}


@pytest.fixture()
def repo(tmp_path):
    root = tmp_path / "repo"
    shutil.copytree(FIXTURE, root)
    (root / "bin").mkdir()
    (root / "bin" / "tool.exe").write_bytes(b"MZ must never be opened")
    return root


def run(repo, out, selection=None, commit=COMMIT):
    return ke.extract(repo, commit, selection or SELECTION, out)


def test_three_skills_with_provenance(repo, tmp_path):
    skills = run(repo, tmp_path / "out")
    assert sorted(s.name for s in skills) == sorted(SELECTION)
    for skill in skills:
        frontmatter, body = F.parse_skill_md((skill.path / "SKILL.md").read_text(encoding="utf-8"))
        assert frontmatter["license"] == "Apache-2.0"
        assert frontmatter["source_url"] == ke.UPSTREAM_URL
        assert frontmatter["commit"] == COMMIT
        assert ke.UPSTREAM_URL in body and "Apache-2.0" in body
        assert ke.lint(skill.path) == []
    body = (tmp_path / "out" / "windows_internals_skill" / "SKILL.md").read_text(encoding="utf-8")
    assert "tutorials/windows_peb.md" in body and COMMIT in body
    assert "process environment block" in body


def test_skills_are_quarantined_without_a_scan(repo, tmp_path):
    assert {s.state for s in run(repo, tmp_path / "out")} == {K.QUARANTINED}


def test_wrong_license_refused(repo, tmp_path):
    (repo / "LICENSE").write_text("MIT License\n\nPermission is hereby granted, free of charge, to any person\n")
    with pytest.raises(ke.ExtractRefused, match="license"):
        run(repo, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_missing_license_refused(repo, tmp_path):
    (repo / "LICENSE").unlink()
    with pytest.raises(ke.ExtractRefused, match="license"):
        run(repo, tmp_path / "out")


@pytest.mark.parametrize("commit", ["", "main", "0123abc", "G" * 40, COMMIT.upper(), COMMIT + "0"])
def test_unpinned_commit_refused(repo, tmp_path, commit):
    with pytest.raises(ke.ExtractRefused, match="commit"):
        run(repo, tmp_path / "out", commit=commit)


@pytest.mark.parametrize("bad", ["../outside.md", "/etc/passwd", "tutorials/../../x.md",
                                 "examples/hello.asm", "bin/tool.exe", "tutorials/missing.md"])
def test_path_outside_the_allowlist_refused(repo, tmp_path, bad):
    (tmp_path / "outside.md").write_text("secret")
    selection = {**SELECTION, "binary_analysis_skill": ["tutorials/binary_basics.md", bad]}
    with pytest.raises(ke.ExtractRefused, match="selection"):
        run(repo, tmp_path / "out", selection)


def test_unknown_or_missing_skill_refused(repo, tmp_path):
    with pytest.raises(ke.ExtractRefused, match="selection"):
        run(repo, tmp_path / "out", {**SELECTION, "other_skill": ["tutorials/binary_basics.md"]})
    with pytest.raises(ke.ExtractRefused, match="selection"):
        run(repo, tmp_path / "out", {"binary_analysis_skill": ["tutorials/binary_basics.md"]})


def test_oversize_file_refused(repo, tmp_path):
    (repo / "tutorials" / "binary_basics.md").write_text("x" * (ke.MAX_FILE_BYTES + 1))
    with pytest.raises(ke.ExtractRefused, match="byte cap"):
        run(repo, tmp_path / "out")


def test_symlink_escape_refused(repo, tmp_path):
    outside = tmp_path / "outside.md"
    outside.write_text("secret")
    link = repo / "tutorials" / "link.md"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks unavailable")
    selection = {**SELECTION, "binary_analysis_skill": ["tutorials/link.md"]}
    with pytest.raises(ke.ExtractRefused, match="selection"):
        run(repo, tmp_path / "out", selection)


def test_unselected_files_are_never_opened(repo, tmp_path, monkeypatch):
    opened: list[str] = []
    real_open, real_io_open = builtins.open, io.open

    def spy(real):
        def opener(file, *args, **kwargs):
            opened.append(str(file))
            return real(file, *args, **kwargs)
        return opener

    monkeypatch.setattr(builtins, "open", spy(real_open))
    monkeypatch.setattr(io, "open", spy(real_io_open))
    run(repo, tmp_path / "out")
    inside = [Path(p).as_posix() for p in opened if str(repo) in str(p) or Path(p).as_posix().startswith(repo.as_posix())]
    assert inside, "the selected tutorials must be read"
    assert not [p for p in inside if p.endswith((".exe", ".asm"))]
    assert {Path(p).name for p in inside} <= {"LICENSE", "binary_basics.md", "windows_peb.md", "firmware_uart.md"}


def test_two_runs_are_identical(repo, tmp_path):
    run(repo, tmp_path / "a")
    run(repo, tmp_path / "b")
    for name in SELECTION:
        assert (tmp_path / "a" / name / "SKILL.md").read_bytes() == (tmp_path / "b" / name / "SKILL.md").read_bytes()


@pytest.mark.parametrize("drop", ["license", "source_url", "commit"])
def test_lint_fails_a_skill_missing_provenance(repo, tmp_path, drop):
    skill = run(repo, tmp_path / "out")[0]
    frontmatter, body = F.parse_skill_md((skill.path / "SKILL.md").read_text(encoding="utf-8"))
    del frontmatter[drop]
    (skill.path / "SKILL.md").write_text(F.render_skill_md(frontmatter, body), encoding="utf-8")
    assert any(drop in problem for problem in ke.lint(skill.path))


def test_lint_fails_a_section_without_a_source(repo, tmp_path):
    skill = run(repo, tmp_path / "out")[0]
    text = (skill.path / "SKILL.md").read_text(encoding="utf-8")
    (skill.path / "SKILL.md").write_text(text.replace("Source: ", "From: "), encoding="utf-8")
    assert any("source" in problem for problem in ke.lint(skill.path))
