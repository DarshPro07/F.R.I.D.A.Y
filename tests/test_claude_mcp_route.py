"""FAB-007: when no tool-capable API or Hermes route is left, the signed-in
Claude Code CLI acts through FRIDAY's own MCP server - never a raw shell.

No real `claude` runs and nothing touches the network: the router is pinned
with a patched `usable()`, the CLI path is pinned, and the CLI itself is a
Python stand-in script run by the real `cli.Run` (so the emergency-stop
registration and the process-tree teardown are the production ones).
"""
from __future__ import annotations

import asyncio
import json
import sys

import pytest

from friday import claude_mcp_route as route
from friday import control, executor_router
from friday.executors import cli


@pytest.fixture
def pinned_cli(monkeypatch):
    monkeypatch.setattr(cli, "claude_path", lambda: "/pinned/claude")


def _usable(monkeypatch, *ids):
    monkeypatch.setattr(executor_router, "usable", lambda: tuple(ids))


# -- part 1: routing ---------------------------------------------------------

def test_every_api_route_down_and_no_hermes_falls_back_to_claude_cli(monkeypatch):
    _usable(monkeypatch, "claude")
    choice = executor_router.choose_for_capability(
        "send the report", api_route_available=False)
    assert choice.executor == "claude"
    assert choice.via_friday_mcp is True
    assert "FRIDAY's MCP" in choice.because


def test_api_route_available_means_no_cli_fallback(monkeypatch):
    _usable(monkeypatch, "claude", "hermes")
    choice = executor_router.choose_for_capability(
        "send the report", api_route_available=True)
    assert choice.executor == executor_router.ROUTE_API
    assert choice.via_friday_mcp is False


def test_hermes_outranks_the_cli_when_its_own_route_works(monkeypatch):
    _usable(monkeypatch, "claude", "hermes")
    choice = executor_router.choose_for_capability(
        "send the report", api_route_available=False,
        hermes_route_available=True)
    assert choice.executor == "hermes"
    assert choice.via_friday_mcp is False


def test_hermes_installed_but_rate_limited_falls_to_the_cli(monkeypatch):
    _usable(monkeypatch, "claude", "hermes")
    choice = executor_router.choose_for_capability(
        "send the report", api_route_available=False,
        hermes_route_available=False)
    assert choice.executor == "claude"
    assert choice.via_friday_mcp is True


def test_hermes_is_assumed_to_share_the_api_verdict(monkeypatch):
    # 2026-10-08: openrouter 429, openai-codex exhausted, Hermes installed.
    _usable(monkeypatch, "claude", "hermes")
    choice = executor_router.choose_for_capability(
        "send the report", api_route_available=False)
    assert choice.executor == "claude" and choice.via_friday_mcp is True


def test_nothing_usable_is_an_honest_empty_choice(monkeypatch):
    _usable(monkeypatch)
    choice = executor_router.choose_for_capability(
        "send the report", api_route_available=False)
    assert choice.executor == ""
    assert "no tool-capable route" in choice.because


# -- part 2: the launch ------------------------------------------------------

def test_config_points_at_the_local_friday_mcp_endpoint(tmp_path):
    path = route.write_mcp_config(tmp_path, url="http://127.0.0.1:9/sse")
    doc = json.loads(open(path, encoding="utf-8").read())
    assert doc == {"mcpServers": {"friday": {
        "type": "sse", "url": "http://127.0.0.1:9/sse"}}}
    default = route.write_mcp_config(tmp_path / "d")
    assert json.loads(open(default, encoding="utf-8").read())[
        "mcpServers"]["friday"]["url"] == control.SSE_URL


def test_launch_carries_only_friday_tools_and_no_shell(pinned_cli, tmp_path):
    cfg = route.write_mcp_config(tmp_path, url="http://127.0.0.1:9/sse")
    argv = route.launch_for("book a table", config_path=cfg,
                            cwd=str(tmp_path)).argv()
    assert argv[argv.index("--mcp-config") + 1] == cfg
    assert "--strict-mcp-config" in argv
    # built-in tools (shell, editor, web) are switched off entirely
    assert argv[argv.index("--tools") + 1] == ""
    assert argv[argv.index("--permission-mode") + 1] == "dontAsk"
    allowed = argv[argv.index("--allowedTools") + 1:]
    assert allowed[0] == "mcp__friday"
    denied = argv[argv.index("--disallowedTools") + 1:]
    assert "Bash" in denied and "PowerShell" in denied
    assert "book a table" not in argv                 # the prompt goes on stdin


# -- part 2: running ---------------------------------------------------------

def _stream(*events):
    return "\n".join(json.dumps(e) for e in events) + "\n"


def _tool(name):
    return {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": name, "input": {}}]}}


def _result(text, error=False):
    return {"type": "result", "result": text, "is_error": error,
            "session_id": "s1"}


def _fake_cli(monkeypatch, tmp_path, body, argv_seen=None):
    """The real cli.Run, with the child swapped for a Python stand-in. The
    genuine argv is built first, so the launch flags stay observable."""
    script = tmp_path / "fake_claude.py"
    script.write_text(body, encoding="utf-8")
    real_argv = cli.Launch.argv

    def argv(self):
        built = real_argv(self)
        if argv_seen is not None:
            argv_seen.extend(built)
        return [sys.executable, str(script)]

    monkeypatch.setattr(cli.Launch, "argv", argv)


def _emitting(stdout, *, stderr="", code=0):
    return ("import sys\n"
            "sys.stdin.read()\n"
            f"sys.stdout.write({stdout!r})\n"
            f"sys.stderr.write({stderr!r})\n"
            f"sys.exit({code})\n")


_SLEEPER = "import sys, time\nsys.stdin.read()\ntime.sleep(120)\n"


def test_request_is_completed_through_friday_mcp_tools(pinned_cli, monkeypatch, tmp_path):
    argv = []
    _fake_cli(monkeypatch, tmp_path, _emitting(
        _stream(_tool("mcp__friday__memory_recall"), _result("done: 3 notes"))),
        argv_seen=argv)
    out = asyncio.run(route.run_request("what do I know about Ana?", timeout_s=60))
    assert out.ok and out.text == "done: 3 notes"
    assert out.tools_used == ("mcp__friday__memory_recall",)
    assert out.outside_friday == ()
    assert "--mcp-config" in argv and "what do I know about Ana?" not in argv


def test_the_request_reaches_the_cli_on_stdin(pinned_cli, monkeypatch, tmp_path):
    _fake_cli(monkeypatch, tmp_path,
              "import sys, json\n"
              "prompt = sys.stdin.read()\n"
              "print(json.dumps({'type': 'result', 'result': 'got ' + prompt}))\n")
    out = asyncio.run(route.run_request("hello there", timeout_s=60))
    assert out.ok and out.text == "got hello there"


def test_a_tool_outside_friday_mcp_fails_the_run(pinned_cli, monkeypatch, tmp_path):
    _fake_cli(monkeypatch, tmp_path, _emitting(
        _stream(_tool("Bash"), _result("ran it"))))
    out = asyncio.run(route.run_request("anything", timeout_s=60))
    assert not out.ok
    assert out.outside_friday == ("Bash",)
    assert "outside FRIDAY's MCP" in out.error


@pytest.mark.parametrize("code,stdout,stderr,needle", [
    (1, "", "Error: rate limit", "rate limit"),
    (0, _stream(_result("signed out", error=True)), "", "signed out"),
    (0, "", "", "no result"),
    (3, _stream(_result("partial")), "boom", "exited 3"),
])
def test_cli_failures_are_reported_not_swallowed(
        pinned_cli, monkeypatch, tmp_path, code, stdout, stderr, needle):
    _fake_cli(monkeypatch, tmp_path, _emitting(stdout, stderr=stderr, code=code))
    out = asyncio.run(route.run_request("x", timeout_s=60))
    assert not out.ok and needle in out.error


def test_missing_cli_is_reported(monkeypatch):
    monkeypatch.setattr(cli, "claude_path", lambda: None)
    out = asyncio.run(route.run_request("x"))
    assert not out.ok and "not on PATH" in out.error


def test_the_worker_is_registered_with_the_emergency_stop(
        pinned_cli, monkeypatch, tmp_path):
    from friday import stop

    owned, disowned = [], []
    monkeypatch.setattr(stop, "own_process", owned.append)
    monkeypatch.setattr(stop, "disown_process", disowned.append)
    _fake_cli(monkeypatch, tmp_path, _emitting(_stream(_result("ok"))))
    assert asyncio.run(route.run_request("x", timeout_s=60)).ok
    assert len(owned) == 1 and disowned == owned


def test_a_timeout_tears_the_process_down_and_is_reported(
        pinned_cli, monkeypatch, tmp_path):
    made = []

    def factory(launch):
        run = cli.Run(launch)
        made.append(run)
        return run

    _fake_cli(monkeypatch, tmp_path, _SLEEPER)
    out = asyncio.run(route.run_request("x", timeout_s=1, run_factory=factory))
    assert not out.ok and "did not finish within 1s" in out.error
    assert made[0].cancelled and made[0].process.returncode is not None


def test_a_cancelled_caller_leaves_no_process_running(
        pinned_cli, monkeypatch, tmp_path):
    made = []

    def factory(launch):
        run = cli.Run(launch)
        made.append(run)
        return run

    _fake_cli(monkeypatch, tmp_path, _SLEEPER)

    async def scenario():
        task = asyncio.ensure_future(route.run_request(
            "x", timeout_s=60, run_factory=factory))
        for _ in range(100):
            await asyncio.sleep(0.1)
            if made and made[0].process is not None:
                break
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert made[0].process.returncode is not None


def test_the_worker_never_inherits_friday_secrets(pinned_cli, monkeypatch, tmp_path):
    # the worker gets exactly the allowlisted environment (V7-D18)
    monkeypatch.setenv("FRIDAY_TEST_SECRET_FAB007", "s3cret")
    _fake_cli(monkeypatch, tmp_path,
              "import sys, os, json\n"
              "sys.stdin.read()\n"
              "print(json.dumps({'type': 'result', 'result': "
              "str('FRIDAY_TEST_SECRET_FAB007' in os.environ)}))\n")
    out = asyncio.run(route.run_request("x", timeout_s=60))
    assert out.ok and out.text == "False"
