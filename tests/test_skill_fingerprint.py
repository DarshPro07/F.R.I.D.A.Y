"""FR-012: a code change invalidates the skills that depend on it - and ONLY those.

The property under test is mostly the negative one. A skill that goes
NEEDS_REVALIDATION on every commit is a skill nobody trusts; a skill that
never does is one that quietly rots. So:

    README typo          -> provider-debugging skill stays VALIDATED
    model_gateway.py     -> provider-debugging skill -> NEEDS_REVALIDATION
    unrelated skill      -> untouched by either

Built on a real SkillLadder in a real SQLite file over a real temp tree,
not mocks. Every digest is computed from bytes on disk.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, ".")

from friday.skill_ladder import SkillLadder
from friday.skill_fingerprint import (
    Dependency, SkillFingerprints, NEEDS_REVALIDATION,
)


@pytest.fixture()
def repo(tmp_path):
    """A tiny repo: two source files, a docs dir, a README."""
    (tmp_path / "friday").mkdir()
    (tmp_path / "friday" / "model_gateway.py").write_text("def route(): return 'a'\n")
    (tmp_path / "friday" / "audio.py").write_text("def devices(): return []\n")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "guide.md").write_text("# guide\n")
    (tmp_path / "README.md").write_text("# friday\n")
    return tmp_path


@pytest.fixture()
def ladder(tmp_path):
    return SkillLadder(tmp_path / "skills.sqlite3")


@pytest.fixture()
def fp(ladder, repo):
    return SkillFingerprints(ladder, root=repo, schemas={"route_outcomes": "v3"})


def validated_skill(ladder, name, procedure="do the thing"):
    r = ladder.capture(name, procedure, criteria=["expensive_rediscovery"],
                       evidence="cost 40 minutes twice")
    assert r["status"] == "captured", r
    ladder.validate(name, passed=True, validation="replayed ok")
    assert ladder.current(name)["state"] == "VALIDATED"


class TestTheNegativeProperty:
    """Unrelated changes invalidate nothing."""

    def test_a_readme_typo_leaves_the_provider_skill_alone(self, ladder, fp, repo):
        validated_skill(ladder, "provider-debugging")
        fp.record("provider-debugging", [Dependency("file", "friday/model_gateway.py"),
                                         Dependency("schema", "route_outcomes")])
        (repo / "README.md").write_text("# Friday\n")           # the typo fix
        result = fp.sweep(changed_paths=["README.md"])
        assert result["stale"] == []
        assert "provider-debugging" in result["untouched"]
        assert ladder.current("provider-debugging")["state"] == "VALIDATED"

    def test_a_docs_change_does_not_touch_a_code_skill(self, ladder, fp, repo):
        validated_skill(ladder, "provider-debugging")
        fp.record("provider-debugging", [Dependency("file", "friday/model_gateway.py")])
        (repo / "docs" / "guide.md").write_text("# Guide, revised\n")
        assert fp.affected_by(["docs/guide.md"]) == {}

    def test_two_skills_with_disjoint_dependencies_are_independent(self, ladder, fp, repo):
        validated_skill(ladder, "provider-debugging")
        validated_skill(ladder, "audio-debugging")
        fp.record("provider-debugging", [Dependency("file", "friday/model_gateway.py")])
        fp.record("audio-debugging", [Dependency("file", "friday/audio.py")])
        (repo / "friday" / "model_gateway.py").write_text("def route(): return 'b'\n")
        result = fp.sweep(changed_paths=["friday/model_gateway.py"])
        assert [s["skill"] for s in result["stale"]] == ["provider-debugging"]
        assert ladder.current("audio-debugging")["state"] == "VALIDATED"
        assert "audio-debugging" in result["untouched"]


class TestThePositiveProperty:
    """A relevant change is caught, with the reason named."""

    def test_editing_a_declared_file_marks_the_skill_stale(self, ladder, fp, repo):
        validated_skill(ladder, "provider-debugging")
        fp.record("provider-debugging", [Dependency("file", "friday/model_gateway.py")])
        (repo / "friday" / "model_gateway.py").write_text("def route(): return 'b'\n")
        chk = fp.check("provider-debugging")
        assert chk.stale
        assert chk.drift[0].target == "friday/model_gateway.py"
        fp.mark_stale("provider-debugging", chk)
        cur = ladder.current("provider-debugging")
        assert cur["state"] == NEEDS_REVALIDATION
        assert "model_gateway.py changed" in cur["validation"]

    def test_deleting_a_declared_file_reads_as_missing_not_unchanged(self, ladder, fp, repo):
        validated_skill(ladder, "provider-debugging")
        fp.record("provider-debugging", [Dependency("file", "friday/model_gateway.py")])
        (repo / "friday" / "model_gateway.py").unlink()
        chk = fp.check("provider-debugging")
        assert chk.stale and chk.drift[0].now == "MISSING"
        assert "no longer exists" in chk.drift[0].describe()

    def test_a_directory_dependency_catches_a_file_added_beneath_it(self, ladder, fp, repo):
        validated_skill(ladder, "docs-skill")
        fp.record("docs-skill", [Dependency("directory", "docs")])
        (repo / "docs" / "new.md").write_text("new\n")
        assert fp.check("docs-skill").stale
        assert fp.affected_by(["docs/new.md"]) == {"docs-skill": ["docs/new.md"]}

    def test_a_schema_bump_is_drift(self, ladder, fp):
        validated_skill(ladder, "provider-debugging")
        fp.record("provider-debugging", [Dependency("schema", "route_outcomes")])
        fp.schemas["route_outcomes"] = "v4"
        chk = fp.check("provider-debugging")
        assert chk.stale and chk.drift[0].was == "v3" and chk.drift[0].now == "v4"

    def test_a_skill_depending_on_another_skill_goes_stale_when_it_revalidates(self, ladder, fp):
        validated_skill(ladder, "base-skill")
        validated_skill(ladder, "derived-skill")
        fp.record("derived-skill", [Dependency("skill", "base-skill")])
        # base-skill gets a new version and is validated again
        ladder.capture("base-skill", "improved", criteria=["expensive_rediscovery"],
                       evidence="better path found")
        ladder.validate("base-skill", passed=True, validation="ok")
        assert ladder.current("base-skill")["version"] == 2
        assert fp.check("derived-skill").stale


class TestStateMachineDiscipline:
    def test_only_a_validated_skill_can_go_stale(self, ladder, fp, repo):
        """A CANDIDATE was never trusted; marking it stale is meaningless."""
        ladder.capture("fresh", "p", criteria=["expensive_rediscovery"], evidence="e")
        fp.record("fresh", [Dependency("file", "friday/audio.py")])
        (repo / "friday" / "audio.py").write_text("changed\n")
        chk = fp.check("fresh")
        assert chk.stale
        cur = fp.mark_stale("fresh", chk)
        assert cur["state"] == "CANDIDATE"

    def test_marking_stale_is_idempotent(self, ladder, fp, repo):
        validated_skill(ladder, "s")
        fp.record("s", [Dependency("file", "friday/audio.py")])
        (repo / "friday" / "audio.py").write_text("changed\n")
        chk = fp.check("s")
        a = fp.mark_stale("s", chk)
        b = fp.mark_stale("s", chk)
        assert a["state"] == b["state"] == NEEDS_REVALIDATION

    def test_no_drift_means_no_transition(self, ladder, fp):
        validated_skill(ladder, "s")
        fp.record("s", [Dependency("file", "friday/audio.py")])
        chk = fp.check("s")
        assert not chk.stale and chk.unchanged == 1
        assert fp.mark_stale("s", chk)["state"] == "VALIDATED"

    def test_recording_reports_dependencies_that_do_not_exist(self, ladder, fp):
        """Declaring a path that is not there is a skill authoring bug, and it
        must be visible at record time - not discovered as 'MISSING -> MISSING'
        (no drift!) forever after."""
        validated_skill(ladder, "s")
        r = fp.record("s", [Dependency("file", "friday/does_not_exist.py")])
        assert r["missing"] == ["friday/does_not_exist.py"]

    def test_unknown_dependency_kind_is_refused(self):
        with pytest.raises(ValueError, match="unknown dependency kind"):
            Dependency("vibes", "x")


class TestDigestsAreHonest:
    def test_directory_digest_ignores_pycache(self, fp, repo):
        d = repo / "docs"
        before = fp.check  # noqa: F841 - just exercising import
        from friday.skill_fingerprint import _digest_directory
        a = _digest_directory(repo, "docs")
        (d / "__pycache__").mkdir()
        (d / "__pycache__" / "x.pyc").write_bytes(b"\x00")
        assert _digest_directory(repo, "docs") == a

    def test_directory_digest_sees_a_rename(self, fp, repo):
        from friday.skill_fingerprint import _digest_directory
        a = _digest_directory(repo, "docs")
        (repo / "docs" / "guide.md").rename(repo / "docs" / "handbook.md")
        assert _digest_directory(repo, "docs") != a

    def test_file_digest_is_content_not_mtime(self, fp, repo):
        from friday.skill_fingerprint import _digest_file
        import os, time
        a = _digest_file(repo, "README.md")
        os.utime(repo / "README.md", (time.time() + 100, time.time() + 100))
        assert _digest_file(repo, "README.md") == a
