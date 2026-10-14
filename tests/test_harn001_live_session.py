"""
HARN-001 live-session driver (scripts/harn001_live_session.py), owner RULING 2026-10-13.

Every test runs against a tmp profile and a fake Hermes runner: nothing here
starts Hermes, reaches a model, or touches the real `friday` profile.
"""
from __future__ import annotations

import ast
import copy
import importlib.util
import json
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from friday import harness_sync as H

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "harn001_live_session.py"
_spec = importlib.util.spec_from_file_location("harn001_live_session", SCRIPT)
L = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(L)

CONFIG = {
    "model": {"provider": "openai-codex", "default": "gpt-5.5"},
    "fallback_providers": [],
    "agent": {"max_turns": 90, "reasoning_effort": "medium"},
    "auxiliary": {"vision": {"provider": "auto", "timeout": 30}, "mcp": {"provider": "auto"}},
    "skills": {"external_dirs": []},
    "hooks_auto_accept": False,
    "mcp_servers": {"claude_mem": {"command": "node", "args": ["mem.js"]}},
}


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def _tree(root: Path) -> dict:
    """Every file's bytes and every directory (None) under root."""
    return {p.relative_to(root).as_posix(): (p.read_bytes() if p.is_file() else None)
            for p in root.rglob("*")}


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "repo"
    c = root / ".claude"
    _write(c / "rules" / "00-team-rules.md", "# Team rules (compact)\n- Plan first.\n")
    _write(c / "agents" / "friday-qa-engineer.md",
           '---\nname: friday-qa-engineer\ndescription: "Writes the failing test first."\n---\n'
           "# QA\nTest first.\n")
    _write(c / "skills" / "find-skills" / "SKILL.md",
           "---\nname: find-skills\ndescription: Finds skills.\n---\n# Find Skills\n")
    return root


@pytest.fixture
def profile(tmp_path, monkeypatch):
    home = tmp_path / "profiles" / "friday"
    _write(home / "config.yaml", "# owner comment survives the restore\n"
           + yaml.safe_dump(CONFIG, sort_keys=False))
    _write(home / "SOUL.md", "I am Friday.\r\n")
    _write(home / "auth.json", '{"token": "SECRET-AUTH-VALUE"}')
    _write(home / ".skills_prompt_snapshot.json", "{}")
    _write(home / "skills" / "other" / "SKILL.md", "keep me\n")
    monkeypatch.setenv("FRIDAY_HERMES_PROFILE_HOME", str(home))
    monkeypatch.delenv("HERMES_ACCEPT_HOOKS", raising=False)
    return home


class FakeHermes:
    """Stands in for the `hermes` CLI: answers `mcp test`, records hook consent, replies to -z."""

    def __init__(self, turn_raises: BaseException | None = None):
        self.calls: list[dict] = []
        self.turn_raises = turn_raises

    def __call__(self, cmd, env, timeout):
        self.calls.append({"cmd": list(cmd), "env": dict(env), "timeout": timeout})
        home = Path(env["HERMES_HOME"])
        if cmd[3:5] == ["mcp", "test"]:
            return 0, "Tools discovered: 2\n    get_current_time   Return the current date\n"
        (home / "shell-hooks-allowlist.json").write_text("{}", encoding="utf-8")
        if self.turn_raises:
            raise self.turn_raises
        nonce = cmd[cmd.index("-z") + 1].split()[-1]
        Path(cmd[cmd.index("--usage-file") + 1]).write_text(
            json.dumps({"total_tokens": 900, "auxiliary": {"by_task": {}}}), encoding="utf-8")
        return 0, f"The time is {datetime.now(timezone.utc).isoformat()}. {nonce}\n"

    def turns(self) -> list[dict]:
        return [c for c in self.calls if "-z" in c["cmd"]]


def _kw(source, tmp_path, runner):
    return {"source": source, "live_dir": tmp_path / "live", "runner": runner,
            "probe": lambda: True, "hermes": "hermes"}


def _execute(source, tmp_path, runner):
    return L.main(["--execute", "--ack", "HARN-001"], **_kw(source, tmp_path, runner))


def _report(tmp_path) -> dict:
    (path,) = (tmp_path / "live").glob("*/report.json")
    return json.loads(path.read_text(encoding="utf-8"))


def test_dry_run_default_touches_nothing(source, profile, tmp_path, capsys):
    before, runner = _tree(profile), FakeHermes()
    assert L.main([], **_kw(source, tmp_path, runner)) == 0
    out = capsys.readouterr().out
    assert "DRY RUN" in out and str(profile / "config.yaml") in out and "SOUL.md" in out
    assert _tree(profile) == before
    assert not (tmp_path / "live").exists()
    assert runner.calls == []


def test_execute_without_ack_refuses(source, profile, tmp_path):
    before, runner = _tree(profile), FakeHermes()
    assert L.main(["--execute"], **_kw(source, tmp_path, runner)) == 2
    assert L.main(["--execute", "--ack", "yes"], **_kw(source, tmp_path, runner)) == 2
    assert _tree(profile) == before
    assert not (tmp_path / "live").exists()
    assert runner.calls == []


def test_second_execute_refused_once_spent_marker_exists(source, profile, tmp_path):
    before, runner = _tree(profile), FakeHermes()
    assert _execute(source, tmp_path, runner) == 0
    spent = json.loads((tmp_path / "live" / "SPENT.json").read_text(encoding="utf-8"))
    assert spent["script_sha256"] and spent["utc"] and spent["run_dir"]
    assert _tree(profile) == before
    live, calls = _tree(tmp_path / "live"), len(runner.calls)
    assert _execute(source, tmp_path, runner) == 2
    assert len(runner.calls) == calls
    assert _tree(tmp_path / "live") == live
    assert _tree(profile) == before


def test_spent_marker_created_before_deploy_even_when_deploy_raises(source, profile, tmp_path, monkeypatch):
    before, seen = _tree(profile), {}
    real = L.H.render

    def render(*args, allow_live=False, **kwargs):
        if allow_live:
            seen["spent"] = (tmp_path / "live" / "SPENT.json").is_file()
            raise RuntimeError("deploy failed")
        return real(*args, **kwargs)

    monkeypatch.setattr(L.H, "render", render)
    with pytest.raises(RuntimeError, match="deploy failed"):
        _execute(source, tmp_path, FakeHermes())
    assert seen == {"spent": True}
    assert (tmp_path / "live" / "SPENT.json").is_file()
    assert _tree(profile) == before
    report = _report(tmp_path)
    assert "deploy failed" in report["error"] and report["pass"] is False and report["restored"] is True


def test_allow_live_not_a_cli_flag_or_persisted(source, profile, tmp_path):
    src = SCRIPT.read_text(encoding="utf-8")
    assert src.count("allow_live=True") == 1 and "--allow-live" not in src
    tree = ast.parse(src)
    (deploy,) = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "deploy"]
    uses = [(n, k) for n in ast.walk(tree) if isinstance(n, ast.Call)
            for k in n.keywords if k.arg == "allow_live"]
    assert len(uses) == 1
    call, kw = uses[0]
    assert isinstance(kw.value, ast.Constant) and kw.value.value is True
    assert deploy.lineno <= call.lineno <= deploy.end_lineno
    with pytest.raises(SystemExit):
        L.main(["--allow-live"], **_kw(source, tmp_path, FakeHermes()))
    assert _execute(source, tmp_path, FakeHermes()) == 0
    persisted = re.compile(rb'allow_live["\s]*[:=]')   # a key or assignment; tmp_path itself names the test
    for root in (tmp_path / "live", profile):
        assert not any(persisted.search(data) for data in _tree(root).values() if data)


def test_snapshot_restore_is_byte_exact_even_when_runner_raises(source, profile, tmp_path):
    before = _tree(profile)
    runner = FakeHermes(turn_raises=subprocess.TimeoutExpired(cmd="hermes", timeout=300))
    with pytest.raises(subprocess.TimeoutExpired):
        _execute(source, tmp_path, runner)
    assert len(runner.turns()) == 1           # no retry
    assert _tree(profile) == before
    report = _report(tmp_path)
    assert report["restored"] is True and report["pass"] is False and "TimeoutExpired" in report["error"]
    assert not list((tmp_path / "live").glob("*/snapshot/auth.json"))   # hashed, never copied


def test_restore_removes_hook_allowlist_and_manifest_files_then_restores_soul(source, profile, tmp_path, capsys):
    before = _tree(profile)
    snap = tmp_path / "run" / "snapshot"
    L.snapshot(profile, snap, L.dry_manifest(source))
    L.deploy(source, profile, tmp_path / "claude")
    (profile / "shell-hooks-allowlist.json").write_text("{}", encoding="utf-8")
    assert (profile / "skills" / H.SKILL_GROUP).is_dir()
    assert H.BEGIN_MARK in (profile / "SOUL.md").read_text(encoding="utf-8")
    # the run was killed here; the owner repairs it with --restore
    assert L.main(["--restore", str(snap)]) == 0
    assert _tree(profile) == before
    assert "restored" in capsys.readouterr().out


def test_merge_keeps_claude_mem_and_hooks_auto_accept_false_and_pins_session_overlay():
    config = copy.deepcopy(CONFIG)
    fragment = {"skills": {"external_dirs": ["/p/skills"]},
                "mcp_servers": {"friday": {"url": "http://127.0.0.1:8000/sse", "transport": "sse"}},
                "hooks": {"on_session_start": [{"command": "x"}]},
                "hooks_auto_accept": True, "fallback_providers": ["openrouter"]}
    merged = L.merge_config(config, fragment)
    assert config == CONFIG                                       # input untouched
    assert merged["mcp_servers"]["claude_mem"] == CONFIG["mcp_servers"]["claude_mem"]
    assert merged["mcp_servers"]["friday"]["transport"] == "sse"
    assert merged["skills"]["external_dirs"] == ["/p/skills"]
    assert merged["hooks"] == fragment["hooks"]
    assert merged["hooks_auto_accept"] is False and merged["fallback_providers"] == []
    assert merged["agent"] == {"max_turns": 6, "reasoning_effort": "medium"}
    assert {t: v["provider"] for t, v in merged["auxiliary"].items()} == {"vision": "main", "mcp": "main"}
    assert merged["auxiliary"]["vision"]["timeout"] == 30
    assert merged["model"] == CONFIG["model"]


def test_child_env_has_no_provider_keys_and_sets_accept_hooks_only_in_child(source, profile, tmp_path, monkeypatch):
    env = L.child_env({"OPENAI_API_KEY": "a", "openrouter_api_key": "b", "HERMES_SESSION_ID": "s",
                       "HERMES_HOME": "/old", "MSYSTEM": "MINGW64", "SHLVL": "2", "PATH": "/bin"},
                      Path("/prof"))
    assert env == {"PATH": "/bin", "HERMES_HOME": str(Path("/prof")), "HERMES_ACCEPT_HOOKS": "1"}
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    runner = FakeHermes()
    assert _execute(source, tmp_path, runner) == 0
    for call in runner.calls:
        assert call["env"]["HERMES_ACCEPT_HOOKS"] == "1" and call["env"]["HERMES_HOME"] == str(profile)
        assert not [k for k in call["env"] if k.upper().endswith("_API_KEY")]
    import os
    assert "HERMES_ACCEPT_HOOKS" not in os.environ


def test_one_turn_restricted_to_mcp_friday_toolset_with_explicit_provider(source, profile, tmp_path):
    runner = FakeHermes()
    assert _execute(source, tmp_path, runner) == 0
    assert runner.calls[0]["cmd"] == ["hermes", "-p", "friday", "mcp", "test", "friday"]
    (turn,) = runner.turns()
    cmd = turn["cmd"]
    nonce = _report(tmp_path)["nonce"]
    assert cmd == ["hermes", "-p", "friday", "-z", L.PROMPT.format(nonce=nonce),
                   "--provider", "openai-codex", "--model", "gpt-5.5",
                   "--toolsets", "mcp-friday", "--usage-file", cmd[-1]]
    assert turn["timeout"] == 300 and L.TOKEN_CAP == 20000


NOW = datetime(2026, 10, 13, 12, 0, 0, tzinfo=timezone.utc)
GOOD = {"total_tokens": 900, "total_including_auxiliary": {"total_tokens": 1000},
        "auxiliary": {"by_task": {"title_generation": {"api_calls": 1}}}}


@pytest.mark.parametrize("rc,reply,usage,ok,failed", [
    (0, "It is 2026-10-13T12:00:30+00:00. n0nce", GOOD, True, set()),
    (0, "It is 2026-10-13T12:00:30Z.", GOOD, False, {"nonce_in_reply"}),
    (0, "It is 2026-10-13T11:50:00+00:00. n0nce", GOOD, False, {"fresh_iso_time"}),
    (0, "It is noon. n0nce", GOOD, False, {"fresh_iso_time"}),
    (0, "It is 2026-10-13T12:00:30+00:00. n0nce",
     {**GOOD, "total_including_auxiliary": {"total_tokens": 20001}}, False, {"tokens_within_cap"}),
    (0, "It is 2026-10-13T12:00:30+00:00. n0nce", {}, False, {"tokens_within_cap"}),
    (0, "It is 2026-10-13T12:00:30+00:00. n0nce",
     {**GOOD, "auxiliary": {"by_task": {"vision": {"provider": "openrouter"}}}}, False,
     {"aux_providers_main_only"}),
    (2, "It is 2026-10-13T12:00:30+00:00. n0nce", GOOD, False, {"rc_zero"}),
], ids=["pass", "no-nonce", "stale-time", "no-time", "over-cap", "no-usage", "foreign-aux", "rc2"])
def test_pass_requires_nonce_fresh_iso_time_token_cap_and_no_foreign_aux_provider(rc, reply, usage, ok, failed):
    verdict = L.judge(rc, reply, usage, "n0nce", "openai-codex", NOW)
    assert verdict["pass"] is ok
    assert {k for k, v in verdict["checks"].items() if not v} == failed


def test_report_has_no_secrets_or_env(source, profile, tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-SECRET-ENV-VALUE")
    runner = FakeHermes()
    assert _execute(source, tmp_path, runner) == 0
    (path,) = (tmp_path / "live").glob("*/report.json")
    text = path.read_text(encoding="utf-8")
    assert "sk-SECRET-ENV-VALUE" not in text and "SECRET-AUTH-VALUE" not in text
    report = json.loads(text)
    assert "env" not in report and "HERMES_HOME" not in text
    assert report["pass"] is True and report["restored"] is True and report["mismatched"] == []
    assert report["nonce"] in runner.turns()[0]["cmd"][4]
    assert report["tokens"] == 900
    assert report["steps"]["discovery"]["names_get_current_time"] is True
    assert report["hashes_before"]["config.yaml"] == report["hashes_after"]["config.yaml"]
    assert report["hashes_before"]["shell-hooks-allowlist.json"] == "ABSENT"


def test_stale_prior_manifest_files_are_snapshotted_and_restored(source, profile, tmp_path):
    # an earlier deploy left a manifest; the render unlinks what it names and no longer produces
    _write(profile / "skills" / "old" / "SKILL.md", "stale but the owner's\n")
    _write(profile / H.MANIFEST, json.dumps({"files": ["skills/old/SKILL.md"]}))
    before = _tree(profile)
    assert _execute(source, tmp_path, FakeHermes()) == 0
    assert _tree(profile) == before
    assert _report(tmp_path)["restored"] is True


@pytest.mark.parametrize("probe_ok, config", [(False, CONFIG), (True, {**CONFIG, "model": {}})])
def test_preflight_refusal_spends_nothing(source, profile, tmp_path, probe_ok, config):
    _write(profile / "config.yaml", yaml.safe_dump(config, sort_keys=False))
    before, runner = _tree(profile), FakeHermes()
    kw = {**_kw(source, tmp_path, runner), "probe": lambda: probe_ok}
    assert L.main(["--execute", "--ack", "HARN-001"], **kw) == 2
    assert not (tmp_path / "live" / "SPENT.json").exists()
    assert runner.calls == [] and _tree(profile) == before


@pytest.mark.parametrize("mcp_result", [(1, "get_current_time\n"), (0, "Tools discovered: 0\n")])
def test_discovery_failure_skips_the_model_turn(source, profile, tmp_path, mcp_result):
    class NoTool(FakeHermes):
        def __call__(self, cmd, env, timeout):
            if cmd[3:5] == ["mcp", "test"]:
                self.calls.append({"cmd": list(cmd)})
                return mcp_result
            return super().__call__(cmd, env, timeout)

    before, runner = _tree(profile), NoTool()
    assert _execute(source, tmp_path, runner) == 1
    assert runner.turns() == []
    report = _report(tmp_path)
    assert report["pass"] is False and report["restored"] is True and "turn" not in report["steps"]
    assert _tree(profile) == before


def test_restore_reports_mismatch_with_exit_1(source, profile, tmp_path, capsys):
    snap = tmp_path / "run" / "snapshot"
    L.snapshot(profile, snap, L.dry_manifest(source))
    (snap / "SOUL.md").write_text("tampered\n", encoding="utf-8")
    assert L.main(["--restore", str(snap)]) == 1
    assert "MISMATCH" in capsys.readouterr().out
