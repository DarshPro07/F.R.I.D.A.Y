"""
The skill layer's later additions: plugins (installed packages, never
files in a skill), the change-type classifier, usage statistics, the
codebase fingerprint, and built-in skills that are adopted and upgraded
without losing the owner's edits.
"""
from __future__ import annotations

import subprocess
import sys
import types
from pathlib import Path

import pytest

from friday.skills import change_type as CT
from friday.skills import contracts as K
from friday.skills import dependencies as D
from friday.skills import files as F
from friday.skills import plugins as P
from friday.skills.evaluator import SkillEvaluator
from friday.skills.lifecycle import SkillLifecycle
from friday.skills.registry import SkillRegistry
from test_skills_golden import use_stand_in_scanner


# ---------------------------------------------------------------------------
# plugins
# ---------------------------------------------------------------------------

class _EntryPoint:
    def __init__(self, name: str, value: str):
        self.name, self.value, self.group = name, value, P.GROUP
        self.module, self.attr = value.split(":")
        self.dist = types.SimpleNamespace(name="fake-dist", version="0.1")

    def load(self):
        import importlib

        return getattr(importlib.import_module(self.module), self.attr)


def test_plugins_are_discovered_without_import_and_loaded_only_when_allowed(monkeypatch):
    module = types.ModuleType("fake_friday_plugin")
    module.PLUGIN = {"name": "weather", "version": "1.0.0", "capabilities": ["weather.now"]}
    module.BAD = {"name": "bad"}
    monkeypatch.setitem(sys.modules, "fake_friday_plugin", module)
    points = [_EntryPoint("weather", "fake_friday_plugin:PLUGIN"), _EntryPoint("bad", "fake_friday_plugin:BAD")]

    def finder(group=None):
        return [p for p in points if group in (None, p.group)]

    found = P.discover(finder, allow=set())
    assert [p.name for p in found] == ["bad", "weather"]
    assert not any(p.loaded for p in found)
    assert found[1].distribution == "fake-dist"
    refused = P.load(found[1], finder, allow=set())
    assert not refused.loaded and "FRIDAY_PLUGINS_ALLOW" in refused.error
    ok = P.load(found[1], finder, allow={"weather"})
    assert ok.loaded and ok.version == "1.0.0" and ok.capabilities == ["weather.now"]
    bad = P.load(found[0], finder, allow={"bad"})
    assert not bad.loaded and "lacks" in bad.error


def test_plugin_shape_is_validated():
    assert P.validate({"name": "x", "version": "1", "capabilities": ["a"]}) is None
    assert "lacks" in P.validate({"name": "x"})
    assert "list" in P.validate({"name": "x", "version": "1", "capabilities": "a"})


# ---------------------------------------------------------------------------
# change type
# ---------------------------------------------------------------------------

def test_change_type_picks_the_layer():
    assert CT.classify("the delete action should need approval").change_type == CT.POLICY
    assert CT.classify("we keep rediscovering the steps to rebuild the exe").change_type == CT.SKILL
    assert CT.classify("the owner prefers evening meetings").change_type == CT.MEMORY
    assert CT.classify("the gateway raises KeyError on empty content").change_type == CT.CODE
    assert CT.classify("the default timeout is too short").change_type == CT.CONFIG
    assert CT.classify("this regression slipped through with no test").change_type == CT.TEST
    assert CT.classify("nobody knew where the release docs were").change_type == CT.DOCUMENTATION
    assert CT.classify("").change_type == CT.CODE
    assert CT.classify("the delete action should need approval, it is a bug").alternatives == [CT.CODE]


# ---------------------------------------------------------------------------
# usage statistics and the codebase fingerprint
# ---------------------------------------------------------------------------

def test_registry_stats_are_rates_over_recorded_uses(tmp_path):
    reg = SkillRegistry(tmp_path / "r.sqlite3")
    reg.upsert(K.SkillRecord(name="s", state=K.ACTIVE))
    assert reg.stats("s")["success_rate"] is None
    reg.record_usage("s", "success", latency_ms=100, cost=0.01)
    reg.record_usage("s", "failure", latency_ms=300)
    reg.record_usage("s", "corrected")
    stats = reg.stats("s")
    assert (stats["uses"], stats["corrections"]) == (3, 1)
    assert stats["success_rate"] == 0.5 and stats["failure_rate"] == 0.5
    assert stats["average_latency_ms"] == 200 and stats["average_cost"] == 0.01


def _git(args, cwd):
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=str(cwd), check=True,
                   capture_output=True)


def test_git_identity_is_empty_without_git_and_filled_inside_a_repo(tmp_path, monkeypatch):
    # The release gate's basetemp sits inside the FRIDAY repository: without a ceiling, git walked
    # up from tmp_path and reported the parent repo's identity (release gate on bd7c026).
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    assert D.git_identity(tmp_path) == ("", "")
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-q"], repo)
    (repo / "a.txt").write_text("a", encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-q", "-m", "base"], repo)
    identity, commit = D.git_identity(repo)
    assert len(commit) >= 7
    assert identity.replace("/", "").endswith("repo")


def _measure(registry, name: str) -> None:
    """Recorded outcomes showing a lift: SKILL-005 activates only a skill with measured value."""
    for _ in range(4):
        registry.record_usage(name, "success")
        registry.record_baseline(name, "failure")


def test_activation_stamps_the_codebase_fingerprint(tmp_path, monkeypatch):
    use_stand_in_scanner(tmp_path / "scanner", monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-q"], repo)
    (repo / "a.txt").write_text("a", encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-q", "-m", "base"], repo)
    reg = SkillRegistry(tmp_path / "r.sqlite3")
    life = SkillLifecycle(reg, snapshots_dir=tmp_path / "v",
                          evaluator=SkillEvaluator(reg, repo_root=repo, runner=lambda a, c, t: (0, "ok")),
                          graph=D.SkillDependencyGraph(reg, repo_root=repo))
    skill = F.SkillFiles(tmp_path / "skills" / "stamped")
    skill.write({"name": "stamped", "description": "d", "version": "1.0.0",
                 "metadata": {"friday": {"scope": "repository", "depends_on": ["a.txt"], "tests": ["pytest x"]}}},
                "# S\n\n## When to use\nx\n\n## Procedure\n1. x\n\n## Verification\nok\n")
    life.register(skill, tier=K.PROJECT_TIER, trust=K.TRUST_OWNER)
    life.stage("stamped")
    _measure(reg, "stamped")
    report, record = life.evaluate_and_activate("stamped")
    assert report.passed and len(record.codebase_commit) >= 7 and record.codebase_id


# ---------------------------------------------------------------------------
# built-in skills: adopt, then upgrade without losing the owner's edits
# ---------------------------------------------------------------------------

def _stub_runner(argv, cwd, timeout):
    return (0, "1 passed") if "test_ok" in " ".join(argv) else (1, "1 failed")


def _write_builtin(root: Path, version: str, extra_steps=(), tests=("pytest tests/test_ok.py",)) -> F.SkillFiles:
    skill = F.SkillFiles(root / "guide")
    steps = ["1. Run the first step"] + list(extra_steps)
    skill.write({"name": "guide", "description": "A shipped guide", "version": version, "license": "MIT",
                 "metadata": {"friday": {"scope": "global", "tests": list(tests), "triggers": ["use the guide"]}}},
                "# Guide\n\n## When to use\n\nwhenever\n\n## Procedure\n\n" + "\n".join(steps)
                + "\n\n## Verification\n\n- ok\n")
    return skill


def test_builtin_adoption_and_upgrade_replace_untouched_and_merge_edited(tmp_path, monkeypatch):
    use_stand_in_scanner(tmp_path / "scanner", monkeypatch)
    roots = {K.BUILT_IN: tmp_path / "builtin", K.LOCAL_USER: tmp_path / "user", K.PROJECT_TIER: tmp_path / "proj"}
    for path in roots.values():
        path.mkdir()
    reg = SkillRegistry(tmp_path / "r.sqlite3")
    graph = D.SkillDependencyGraph(reg, repo_root=tmp_path)
    life = SkillLifecycle(reg, snapshots_dir=tmp_path / "v",
                          evaluator=SkillEvaluator(reg, repo_root=tmp_path, runner=_stub_runner), graph=graph)
    _write_builtin(roots[K.BUILT_IN], "1.0.0")

    record = life.adopt_builtin("guide", roots=roots)
    assert record.state == K.DRAFT and record.source.startswith("builtin:")
    with pytest.raises(K.SkillError):
        life.adopt_builtin("guide", roots=roots)                     # never twice
    life.stage("guide")
    _measure(reg, "guide")
    report, record = life.evaluate_and_activate("guide")
    assert report.passed and record.state == K.ACTIVE and record.version == "1.0.0"
    assert life.builtin_updates(roots=roots) == [{"skill": "guide", "upstream_changed": False,
                                                  "locally_modified": False, "state": K.ACTIVE, "version": "1.0.0"}]
    assert life.upgrade_builtin("guide", roots=roots)["outcome"] == "current"

    # the shipped skill improves; the owner's copy was untouched -> replaced
    _write_builtin(roots[K.BUILT_IN], "1.1.0", extra_steps=["2. Run the second step"])
    assert life.builtin_updates(roots=roots)[0]["upstream_changed"] is True
    result = life.upgrade_builtin("guide", roots=roots)
    local = F.SkillFiles(roots[K.LOCAL_USER] / "guide")
    assert result["outcome"] == "activated" and result["version"] == "1.1.0", result
    assert result["merge"].startswith("replaced") and "2. Run the second step" in local.text()

    # the owner edits the copy; the shipped skill improves again -> merged, edits kept
    frontmatter, body = local.read()
    local.write(frontmatter, body + "\n## Owner notes\n\n3. The owner's own step\n")
    _write_builtin(roots[K.BUILT_IN], "1.2.0", extra_steps=["2. Run the second step", "4. Run the fourth step"])
    assert life.builtin_updates(roots=roots)[0]["locally_modified"] is True
    result = life.upgrade_builtin("guide", roots=roots)
    text = local.text()
    assert result["outcome"] == "activated" and result["version"] == "1.2.0", result   # shipped version, kept
    assert "merged" in result["merge"]
    assert "3. The owner's own step" in text and "4. Run the fourth step" in text

    # a shipped version whose test fails rolls back; the owner keeps the last verified copy
    _write_builtin(roots[K.BUILT_IN], "1.3.0",
                   extra_steps=["2. Run the second step", "4. Run the fourth step", "5. Broken"],
                   tests=("pytest tests/test_broken.py",))
    result = life.upgrade_builtin("guide", roots=roots)
    assert result["outcome"] == "rolled_back", result
    assert "3. The owner's own step" in local.text() and "5. Broken" not in local.text()
    assert reg.get("guide").state == K.ACTIVE


def test_the_shipped_friday_operate_skill_lints_and_its_check_runs():
    from friday.skills import lint as L

    skill = F.SkillFiles(F.BUILTIN_DIR / "friday-operate")
    assert skill.exists()
    report = L.lint(skill)
    assert report.verdict != L.FAIL, report.to_dict()
    completed = subprocess.run([sys.executable, str(skill.dir / "scripts" / "check.py")],
                               capture_output=True, text=True, timeout=120)
    assert completed.returncode == 0 and completed.stdout.startswith("FRIDAY"), completed.stderr[-300:]
