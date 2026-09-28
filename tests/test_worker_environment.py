"""
V7-D18 (PRD NON_NEGOTIABLE 4; SEC-005): a coding worker on the HOST backend -
the default, by the owner's decision - was started with `env=None`, which is
FRIDAY's whole environment: every provider key FRIDAY loaded from .env, one
`env` in the worker's shell away from its model. Only the SANDBOX backend
built an allowlisted environment.

HOST stays HOST: the worker's reach is unchanged. What changes is that it
gets the same allowlist a sandboxed worker gets, from the one rule in
`execution.py`, and so never FRIDAY's own secrets.
"""
from __future__ import annotations

import asyncio

import pytest

from friday import execution as EX
from friday.executors import cli


def test_the_worker_environment_is_an_allowlist(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-not-for-workers")
    monkeypatch.setenv("SOME_FUTURE_PROVIDER_TOKEN", "never-listed-anywhere")
    env = EX.worker_environment("worker")
    assert "OPENAI_API_KEY" not in env
    assert "SOME_FUTURE_PROVIDER_TOKEN" not in env, "a denylist, not an allowlist"
    assert "PATH" in env


def test_a_host_coding_run_does_not_inherit_friday_secrets(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-not-for-workers")
    # Not on any scrub list, so only an allowlist keeps it out.
    monkeypatch.setenv("SOME_FUTURE_PROVIDER_TOKEN", "never-listed-anywhere")
    captured = {}

    async def fake_exec(*argv, **kwargs):
        captured.update(kwargs)
        raise RuntimeError("stopped before a real process started")

    monkeypatch.setattr(cli.asyncio, "create_subprocess_exec", fake_exec)
    run = cli.Run(cli.Launch(prompt="fix the test", cwd=str(tmp_path)))
    with pytest.raises(RuntimeError, match="stopped before"):
        asyncio.run(run.start())
    env = captured["env"]
    assert env is not None, "the worker inherited FRIDAY's whole environment"
    assert "OPENAI_API_KEY" not in env
    assert "SOME_FUTURE_PROVIDER_TOKEN" not in env
    assert {"USERPROFILE", "HOME", "APPDATA"} & set(env), (
        "the CLI needs its profile to find its own login")


def test_the_sandbox_environment_is_built_on_the_same_rule(monkeypatch, tmp_path):
    monkeypatch.setenv("SOME_FUTURE_PROVIDER_TOKEN", "never-listed-anywhere")
    with EX.NativeExecutionEnvironment(tmp_path, name="T") as box:
        assert "SOME_FUTURE_PROVIDER_TOKEN" not in box.environment()


def test_the_cli_keeps_its_own_configuration(monkeypatch):
    """Claude Code on Windows finds Git Bash through CLAUDE_CODE_GIT_BASH_PATH;
    the worker's own namespace crosses, and so do names a launch asks for."""
    monkeypatch.setenv("CLAUDE_CODE_GIT_BASH_PATH", r"C:\Program Files\Git\bin\bash.exe")
    monkeypatch.setenv("WANTED_BY_THE_LAUNCH", "yes")
    env = EX.worker_environment("w", passthrough=("WANTED_BY_THE_LAUNCH",),
                                prefixes=cli.CLI_CONFIG_PREFIXES)
    assert env["CLAUDE_CODE_GIT_BASH_PATH"].endswith("bash.exe")
    assert env["WANTED_BY_THE_LAUNCH"] == "yes"


def test_a_secret_shaped_name_never_crosses_even_when_asked_for(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "never")
    monkeypatch.setenv("MY_SERVICE_API_KEY", "never")
    env = EX.worker_environment("w", passthrough=("MY_SERVICE_API_KEY",),
                                prefixes=cli.CLI_CONFIG_PREFIXES)
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in env
    assert "MY_SERVICE_API_KEY" not in env
