"""
SKMKT-001 slice 1: the Vercel skills CLI as a skill-distribution backend.

The runner is injected, so no process starts and nothing touches the network.
What is under test is the adapter's contract: fixed argument lists, refusals
before any process, a credential-free environment, a failure that is never a
pass, and a download that only ever lands in a fresh quarantine directory.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from friday import forge
from friday.skills import skills_cli as S


class FakeRunner:
    """Records every call; optionally writes a fixture skill into cwd."""

    def __init__(self, stdout="", returncode=0, write_skill=False, raises=None):
        self.calls = []
        self.stdout, self.returncode = stdout, returncode
        self.write_skill, self.raises = write_skill, raises

    def __call__(self, argv, *, env, cwd, timeout):
        self.calls.append({"argv": list(argv), "env": dict(env),
                           "cwd": cwd, "timeout": timeout})
        if self.raises:
            raise self.raises
        if self.write_skill:
            skill = Path(cwd) / "demo"
            skill.mkdir(parents=True, exist_ok=True)
            (skill / "SKILL.md").write_text("---\nname: demo\n---\nbody\n")
        return SimpleNamespace(returncode=self.returncode,
                               stdout=self.stdout, stderr="")


@pytest.fixture
def work(tmp_path):
    return tmp_path / "work"


def backend(work, runner):
    return S.SkillsCliBackend(work, runner=runner, timeout=7)


# --- argv per verb -----------------------------------------------------------

def test_find_argv(work):
    r = FakeRunner(stdout="hit")
    res = backend(work, r).find("pdf tools")
    assert r.calls[0]["argv"] == ["npx", "skills", "find", "pdf tools"]
    assert r.calls[0]["timeout"] == 7
    assert res.status == S.OK and res.stdout == "hit" and res.drop is None


def test_list_repo_argv(work):
    r = FakeRunner()
    backend(work, r).list_repo("acme/skills")
    assert r.calls[0]["argv"] == ["npx", "skills", "add", "acme/skills", "--list"]


def test_add_argv_and_drop(work):
    r = FakeRunner(write_skill=True)
    res = backend(work, r).add("acme/skills", "pdf-fill", ["claude-code", "cursor"],
                               ref="v1.2")
    call = r.calls[0]
    assert call["argv"] == ["npx", "skills", "add", "acme/skills#v1.2",
                            "--skill", "pdf-fill",
                            "--agent", "claude-code", "cursor", "--yes"]
    assert res.status == S.OK
    drop = res.drop
    assert (drop.repo, drop.ref, drop.skill) == ("acme/skills", "v1.2", "pdf-fill")
    assert drop.sha256 == forge.tree_digest(drop.path)
    assert call["cwd"] == str(drop.path)


def test_update_argv(work):
    r = FakeRunner()
    backend(work, r).update("pdf-fill")
    assert r.calls[0]["argv"] == ["npx", "skills", "update", "pdf-fill"]


def test_use_argv_and_drop(work):
    r = FakeRunner(write_skill=True)
    res = backend(work, r).use("acme/skills", "pdf-fill")
    assert r.calls[0]["argv"] == ["npx", "skills", "use", "acme/skills",
                                  "--skill", "pdf-fill"]
    assert res.drop is not None and res.drop.skill == "pdf-fill"


# --- refusals happen before any process --------------------------------------

@pytest.mark.parametrize("repo", [
    "acme/skills;rm -rf /", "acme/skills && calc", "-acme/skills", "--list/x",
    "acme", "acme/skills/extra", "", "acme/sk ills", "a|b/c", "acme/skills\n"])
def test_bad_repo_refused(work, repo):
    r = FakeRunner()
    with pytest.raises(S.SkillsCliRefused):
        backend(work, r).add(repo, "pdf-fill", ["claude-code"])
    with pytest.raises(S.SkillsCliRefused):
        backend(work, r).list_repo(repo)
    assert r.calls == []


@pytest.mark.parametrize("skill", ["", "-x", "A", "a b", "a;b", "a&&b", "../x", "a/b"])
def test_bad_skill_refused(work, skill):
    r = FakeRunner()
    with pytest.raises(S.SkillsCliRefused):
        backend(work, r).add("acme/skills", skill, ["claude-code"])
    with pytest.raises(S.SkillsCliRefused):
        backend(work, r).use("acme/skills", skill)
    with pytest.raises(S.SkillsCliRefused):
        backend(work, r).update(skill)
    assert r.calls == []


@pytest.mark.parametrize("agents", [[], ["--global"], ["a;b"], ["Claude"], "claude-code"])
def test_bad_agents_refused(work, agents):
    r = FakeRunner()
    with pytest.raises(S.SkillsCliRefused):
        backend(work, r).add("acme/skills", "pdf-fill", agents)
    assert r.calls == []


@pytest.mark.parametrize("query", ["", "-h", "a;b", "a && b", "a|b", "x" * 200, "a\nb"])
def test_bad_query_refused(work, query):
    r = FakeRunner()
    with pytest.raises(S.SkillsCliRefused):
        backend(work, r).find(query)
    assert r.calls == []


@pytest.mark.parametrize("ref", ["-x", "a;b", "a b", ""])
def test_bad_ref_refused(work, ref):
    r = FakeRunner()
    with pytest.raises(S.SkillsCliRefused):
        backend(work, r).add("acme/skills", "pdf-fill", ["claude-code"], ref=ref)
    assert r.calls == []


def test_no_other_verb_exists(work):
    b = backend(work, FakeRunner())
    with pytest.raises(S.SkillsCliRefused):
        b._run("remove", ["x"], cwd=work)


# --- environment -------------------------------------------------------------

def test_env_is_credential_free(work, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-leak")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_should-not-leak")
    r = FakeRunner()
    backend(work, r).find("pdf")
    env = r.calls[0]["env"]
    assert forge.env_leaks(env) == []
    assert "OPENAI_API_KEY" not in env and "GITHUB_TOKEN" not in env
    assert env == forge.scrubbed_env()


# --- failures are never passes ------------------------------------------------

def test_missing_npx_is_unavailable(work):
    res = backend(work, FakeRunner(raises=FileNotFoundError("npx"))).find("pdf")
    assert res.status == S.UNAVAILABLE and res.drop is None


def test_missing_npx_on_add_leaves_no_drop(work):
    res = backend(work, FakeRunner(raises=FileNotFoundError("npx"))).add(
        "acme/skills", "pdf-fill", ["claude-code"])
    assert res.status == S.UNAVAILABLE and res.drop is None


def test_timeout_is_error(work):
    exc = subprocess.TimeoutExpired(cmd="npx", timeout=7)
    res = backend(work, FakeRunner(raises=exc)).add(
        "acme/skills", "pdf-fill", ["claude-code"])
    assert res.status == S.ERROR and res.drop is None


def test_nonzero_exit_is_error(work):
    res = backend(work, FakeRunner(returncode=2, write_skill=True)).use(
        "acme/skills", "pdf-fill")
    assert res.status == S.ERROR and res.drop is None


def test_empty_download_is_error(work):
    res = backend(work, FakeRunner()).add("acme/skills", "pdf-fill", ["claude-code"])
    assert res.status == S.ERROR and res.drop is None


def test_oserror_is_error(work):
    res = backend(work, FakeRunner(raises=PermissionError("denied"))).find("pdf")
    assert res.status == S.ERROR


# --- quarantine ---------------------------------------------------------------

def test_each_add_gets_a_fresh_dir_inside_quarantine(work):
    r = FakeRunner(write_skill=True)
    b = backend(work, r)
    a = b.add("acme/skills", "pdf-fill", ["claude-code"]).drop
    c = b.add("acme/skills", "pdf-fill", ["claude-code"]).drop
    root = (work / "quarantine").resolve()
    for d in (a, c):
        assert d.path.resolve().parent == root
    assert a.path != c.path


def test_module_has_no_registry_or_home_writers():
    src = Path(S.__file__).read_text(encoding="utf-8")
    for banned in ("registry", "HERMES_HOME", "shell=True"):
        assert banned not in src
