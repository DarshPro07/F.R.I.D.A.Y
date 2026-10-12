"""
INTAKE-006: codebase-aware skill memory. A generated coding skill stores each
verified project fact with the file, glob or package version it depends on and
the fingerprint and commit it was verified at; a later change marks only the
affected facts and skills for revalidation.
"""
from __future__ import annotations

import importlib.metadata

import pytest

from friday.skills import contracts as K
from friday.skills import dependencies as D
from friday.skills.fact_memory import FACT_ACTIVE, FACT_STALE, Evidence, SkillFactMemory
from friday.skills.registry import SkillRegistry

COMMIT = "abc1234"
CODEBASE = "https://example.invalid/repo.git"
PASSED = Evidence(kind="test", ref="tests/test_a.py::test_one", passed=True)


@pytest.fixture
def world(tmp_path):
    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg" / "a.py").write_text("A = 1\n", encoding="utf-8")
    (repo / "pkg" / "b.py").write_text("B = 1\n", encoding="utf-8")
    (repo / "docs").mkdir()
    (repo / "docs" / "readme.md").write_text("hi\n", encoding="utf-8")
    registry = SkillRegistry(tmp_path / "r.sqlite3")
    for name in ("alpha", "beta"):
        registry.upsert(K.SkillRecord(name=name, state=K.ACTIVE, path=str(tmp_path / name)))
    graph = D.SkillDependencyGraph(registry, repo_root=repo)
    memory = SkillFactMemory(registry, graph)
    return repo, registry, memory


def _add(memory, skill, fact, depends_on, evidence=PASSED):
    return memory.add_fact(skill, fact, depends_on=depends_on, evidence=evidence,
                           commit=COMMIT, codebase_id=CODEBASE)


def test_a_fact_is_stored_with_its_dependencies_fingerprint_and_commit(world):
    repo, _, memory = world
    fact_id = _add(memory, "alpha", "A is 1", ["pkg/a.py", "pkg/*.py", "pkg:pytest"])
    fact = memory.facts("alpha")[0]
    assert fact["id"] == fact_id and fact["status"] == FACT_ACTIVE
    assert fact["verified_commit"] == COMMIT and fact["codebase_id"] == CODEBASE
    assert fact["evidence_kind"] == "test" and fact["evidence_ref"] == "tests/test_a.py::test_one"
    deps = {(d["kind"], d["target"]): d["fingerprint"] for d in fact["depends_on"]}
    assert set(deps) == {(D.FILE, "pkg/a.py"), (D.GLOB, "pkg/*.py"), (D.PACKAGE, "pytest")}
    assert deps[(D.PACKAGE, "pytest")] == importlib.metadata.version("pytest")
    assert deps[(D.FILE, "pkg/a.py")] not in ("", D.MISSING)


@pytest.mark.parametrize("evidence", [
    Evidence(kind="test", ref="tests/test_a.py::test_one", passed=False),
    Evidence(kind="guess", ref="vibes", passed=True),
    Evidence(kind="readback", ref="", passed=True),
])
def test_a_fact_without_passing_verification_is_refused(world, evidence):
    _, _, memory = world
    with pytest.raises(K.SkillError):
        _add(memory, "alpha", "A is 1", ["pkg/a.py"], evidence=evidence)
    assert memory.facts("alpha") == []


def test_a_fact_must_depend_on_something_that_can_change(world):
    _, _, memory = world
    with pytest.raises(K.SkillError):
        _add(memory, "alpha", "free floating", [])
    with pytest.raises(K.SkillError):
        _add(memory, "alpha", "only an api", ["api:livekit-agents"])
    with pytest.raises(K.SkillError):
        _add(memory, "nosuchskill", "A is 1", ["pkg/a.py"])


def test_changing_a_fingerprinted_file_marks_only_the_affected_skill_and_facts(world):
    repo, registry, memory = world
    hit = _add(memory, "alpha", "A is 1", ["pkg/a.py"])
    safe = _add(memory, "alpha", "B is 1", ["pkg/b.py"])
    other = _add(memory, "beta", "readme says hi", ["docs/readme.md"])
    assert memory.sweep() == []
    (repo / "pkg" / "a.py").write_text("A = 2\n", encoding="utf-8")
    marked = memory.sweep()
    assert [m["skill"] for m in marked] == ["alpha"]
    assert [f["id"] for f in marked[0]["stale_facts"]] == [hit]
    assert registry.get("alpha").state == K.NEEDS_REVALIDATION
    assert registry.get("beta").state == K.ACTIVE
    status = {f["id"]: f["status"] for s in ("alpha", "beta") for f in memory.facts(s)}
    assert status == {hit: FACT_STALE, safe: FACT_ACTIVE, other: FACT_ACTIVE}
    assert memory.stale_facts("alpha")[0]["stale_reason"].startswith("changed: pkg/a.py")
    assert memory.sweep() == []                                        # idempotent


def test_a_glob_dependency_notices_a_new_matching_file(world):
    repo, registry, memory = world
    fact_id = _add(memory, "alpha", "pkg modules are tiny", ["pkg/*.py"])
    (repo / "pkg" / "c.py").write_text("C = 1\n", encoding="utf-8")
    assert [f["id"] for m in memory.sweep() for f in m["stale_facts"]] == [fact_id]


def test_an_upgraded_package_marks_the_facts_that_depend_on_it(world, monkeypatch):
    _, registry, memory = world
    pkg_fact = _add(memory, "alpha", "pytest supports X", ["pkg:pytest"])
    file_fact = _add(memory, "alpha", "A is 1", ["pkg/a.py"])
    real = importlib.metadata.version
    monkeypatch.setattr(importlib.metadata, "version",
                        lambda name: "999.0.0" if name == "pytest" else real(name))
    marked = memory.sweep()
    assert [f["id"] for m in marked for f in m["stale_facts"]] == [pkg_fact]
    assert "pytest" in memory.stale_facts("alpha")[0]["stale_reason"]
    assert registry.get("alpha").state == K.NEEDS_REVALIDATION
    assert {f["id"]: f["status"] for f in memory.facts("alpha")}[file_fact] == FACT_ACTIVE


def test_a_stale_fact_returns_only_with_new_passing_evidence(world):
    repo, _, memory = world
    fact_id = _add(memory, "alpha", "A is 1", ["pkg/a.py"])
    (repo / "pkg" / "a.py").write_text("A = 2\n", encoding="utf-8")
    memory.sweep()
    with pytest.raises(K.SkillError):
        memory.revalidate_fact(fact_id, Evidence(kind="test", ref="t", passed=False), commit="def5678")
    assert memory.facts("alpha")[0]["status"] == FACT_STALE
    memory.revalidate_fact(fact_id, Evidence(kind="readback", ref="pkg/a.py:1", passed=True), commit="def5678")
    fact = memory.facts("alpha")[0]
    assert fact["status"] == FACT_ACTIVE and fact["verified_commit"] == "def5678" and fact["stale_reason"] == ""
    assert memory.sweep() == []                                        # the new fingerprint is the baseline


def test_an_intake_records_the_upstream_commit_and_package_versions(world):
    _, _, memory = world
    assert memory.intake_basis("alpha") is None
    memory.record_intake("alpha", upstream_commit="0123456789abcdef", upstream_url="https://example.invalid/up.git",
                         packages={"pytest": "8.0.0", "pyyaml": "6.0.1"})
    basis = memory.intake_basis("alpha")
    assert basis["upstream_commit"] == "0123456789abcdef"
    assert basis["upstream_url"] == "https://example.invalid/up.git"
    assert basis["packages"] == {"pytest": "8.0.0", "pyyaml": "6.0.1"}
    with pytest.raises(K.SkillError):
        memory.record_intake("alpha", upstream_commit="", packages={})
    with pytest.raises(K.SkillError):
        memory.record_intake("nosuchskill", upstream_commit="0123456789abcdef", packages={})
