"""FAB-007: the live typed-request path reaches the Claude Code + FRIDAY MCP route.

When every tool-capable API route is unavailable or rate-limited, a request that
needs a capability is completed through the signed-in Claude Code CLI acting with
FRIDAY's own MCP tools, instead of answered without tools or not at all. The failure
path is unchanged when that route is unavailable too.

Everything is a stand-in: the fake worker answers (or fails) for the API routes, the
router's `usable()` is pinned, and the MCP route is an injected coroutine - no real
`claude`, no network, nothing from the machine.
"""
from __future__ import annotations

import pytest

from friday import claude_mcp_route as route
from friday import executor_router
from friday import model_gateway as mg
from test_cli_model_provider import FakeClaude, env, gateway, runs  # noqa: F401

TOOL = {"type": "function", "function": {"name": "use_capability", "description": "d",
                                         "parameters": {"type": "object", "properties": {}}}}
RATE_LIMITED = "anthropic:QUOTA_EXCEEDED,openai-codex:QUOTA_EXCEEDED"


class StandIn:
    """The MCP route: records what it was asked and answers as scripted."""

    def __init__(self, result=None):
        self.calls: list[str] = []
        self.result = result or route.RouteResult(
            True, text="sent via FRIDAY MCP", tools_used=("mcp__friday__use_capability",))

    async def __call__(self, request, **kw):
        self.calls.append(request)
        return self.result


def usable(monkeypatch, *ids):
    monkeypatch.setattr(executor_router, "usable", lambda: tuple(ids))


def build(tmp_path, mcp, *, backends=()):
    base = gateway(tmp_path, backends=backends)
    return mg.ModelGateway(worker=base.worker, telemetry=base.telemetry,
                           tier_table=base._tier_table, cli_backends=backends,
                           mcp_route=mcp)


def ask(text="send the report to Dana", *, tools=(TOOL,), **kw):
    return mg.ModelGatewayRequest(
        objective_id="obj-mcp", task_class=mg.SIMPLE, tools=tools,
        context_package=mg.compile_context(system="You are Friday.", user=text), **kw)


def test_every_api_route_rate_limited_completes_through_claude_and_friday_mcp(
        env, tmp_path, monkeypatch):
    env.setenv("FAKE_GW_FAIL_PROVIDERS", RATE_LIMITED)
    usable(monkeypatch, "claude")
    mcp = StandIn()
    out = build(tmp_path, mcp).infer(ask())
    assert out.status == "ok" and out.response == "sent via FRIDAY MCP"
    assert out.provider == "claude-mcp" and out.route_kind == "cli"
    assert out.billing_class == mg.SUBSCRIPTION_CLI and out.tools_carried
    assert out.dispatch == "FALLBACK"
    assert len(mcp.calls) == 1 and "send the report to Dana" in mcp.calls[0]
    assert {a["provider"] for a in out.attempts if a["status"] == "failed"} == {
        "anthropic", "openai-codex"}
    assert out.failover_count == len(out.attempts)


def test_it_takes_the_place_of_the_toolless_cli_completion(env, tmp_path, monkeypatch):
    env.setenv("FAKE_GW_FAIL_PROVIDERS", RATE_LIMITED)
    usable(monkeypatch, "claude")
    mcp = StandIn()
    out = build(tmp_path, mcp, backends=(FakeClaude(),)).infer(ask())
    assert out.provider == "claude-mcp" and out.response == "sent via FRIDAY MCP"
    assert runs(tmp_path) == []          # the tool-less claude-cli route never ran


def test_a_request_asking_for_the_tools_capability_counts_too(env, tmp_path, monkeypatch):
    env.setenv("FAKE_GW_FAIL_PROVIDERS", RATE_LIMITED)
    usable(monkeypatch, "claude")
    mcp = StandIn()
    out = build(tmp_path, mcp).infer(ask(tools=(), required_capabilities=("tools",)))
    assert out.provider == "claude-mcp" and len(mcp.calls) == 1


def test_a_working_api_route_is_used_and_the_cli_is_not(env, tmp_path, monkeypatch):
    usable(monkeypatch, "claude")
    mcp = StandIn()
    out = build(tmp_path, mcp).infer(ask())
    assert out.status == "ok" and out.provider == "anthropic" and mcp.calls == []


def test_failure_is_unchanged_when_the_cli_route_is_unavailable_too(
        env, tmp_path, monkeypatch):
    env.setenv("FAKE_GW_FAIL_PROVIDERS", RATE_LIMITED)
    usable(monkeypatch)                    # no claude on this machine
    mcp = StandIn()
    out = build(tmp_path, mcp).infer(ask())
    from friday import provider_cooldowns as PC
    monkeypatch.setattr(PC, "COOLDOWNS_FILE", tmp_path / "fresh-cooldowns.json")
    plain = build(tmp_path, None).infer(ask())      # the same gateway without the route
    assert out.status == "failed" and mcp.calls == []
    assert (out.status, out.entitlement_state, out.warnings) == (
        plain.status, plain.entitlement_state, plain.warnings)


def test_a_failed_mcp_run_leaves_the_failure_path_and_says_why(env, tmp_path, monkeypatch):
    env.setenv("FAKE_GW_FAIL_PROVIDERS", RATE_LIMITED)
    usable(monkeypatch, "claude")
    mcp = StandIn(route.RouteResult(False, error="the CLI did not finish within 60s"))
    out = build(tmp_path, mcp).infer(ask())
    assert out.status == "failed" and len(mcp.calls) == 1
    tried = [a for a in out.attempts if a["provider"] == "claude-mcp"]
    assert len(tried) == 1 and "did not finish" in tried[0]["error"]


def test_a_failed_mcp_run_still_falls_to_the_toolless_cli_route(env, tmp_path, monkeypatch):
    env.setenv("FAKE_GW_FAIL_PROVIDERS", RATE_LIMITED)
    usable(monkeypatch, "claude")
    mcp = StandIn(route.RouteResult(False, error="exit 1"))
    out = build(tmp_path, mcp, backends=(FakeClaude(),)).infer(ask())
    assert out.status == "ok" and out.provider == "claude-cli" and not out.tools_carried
    assert len(mcp.calls) == 1             # tried once, not again after the loop


def test_a_request_that_needs_no_capability_never_reaches_it(env, tmp_path, monkeypatch):
    env.setenv("FAKE_GW_FAIL_PROVIDERS", RATE_LIMITED)
    usable(monkeypatch, "claude")
    mcp = StandIn()
    out = build(tmp_path, mcp).infer(ask(tools=()))
    assert out.status == "failed" and mcp.calls == []


@pytest.mark.parametrize("kw", [{"privacy_policy": "local_only"},
                                {"provider_denylist": ("claude-mcp",)},
                                {"allow_failover": False}])
def test_it_respects_the_requests_own_limits(env, tmp_path, monkeypatch, kw):
    env.setenv("FAKE_GW_FAIL_PROVIDERS", RATE_LIMITED)
    usable(monkeypatch, "claude")
    mcp = StandIn()
    out = build(tmp_path, mcp).infer(ask(**kw))
    assert out.status == "failed" and mcp.calls == []


def test_a_gateway_built_without_the_route_never_spawns_one(env, tmp_path, monkeypatch):
    env.setenv("FAKE_GW_FAIL_PROVIDERS", RATE_LIMITED)
    usable(monkeypatch, "claude")
    out = gateway(tmp_path, backends=()).infer(ask())
    assert out.status == "failed"


# -- only when the API routes are unavailable or rate-limited ------------------


def test_a_bad_request_is_not_an_unavailable_route(env, tmp_path, monkeypatch):
    """BAD_REQUEST stops failover on purpose: the request is wrong, not the route."""
    env.setenv("FAKE_GW_FAIL_PROVIDERS", "anthropic:BAD_REQUEST")
    usable(monkeypatch, "claude")
    mcp = StandIn()
    out = build(tmp_path, mcp).infer(ask())
    assert out.status == "failed" and out.entitlement_state == "BAD_REQUEST"
    assert mcp.calls == []


def test_a_measured_only_request_is_not_widened_to_an_unmeasured_route(
        env, tmp_path, monkeypatch):
    usable(monkeypatch, "claude")
    mcp = StandIn()
    out = build(tmp_path, mcp).infer(ask(require_measured=True))
    assert out.status == "failed" and out.entitlement_state == "NO_ROUTE"
    assert mcp.calls == []


def test_no_route_because_every_route_is_cooling_still_reaches_it(env, tmp_path, monkeypatch):
    env.setenv("FAKE_GW_FAIL_PROVIDERS", RATE_LIMITED)
    usable(monkeypatch, "claude")
    mcp = StandIn()
    gw = build(tmp_path, mcp)
    assert gw.infer(ask()).provider == "claude-mcp"       # marks both routes cooling
    again = gw.infer(ask())
    assert again.provider == "claude-mcp" and len(mcp.calls) == 2


def test_the_result_says_which_tools_ran_and_what_they_returned(env, tmp_path, monkeypatch):
    env.setenv("FAKE_GW_FAIL_PROVIDERS", RATE_LIMITED)
    usable(monkeypatch, "claude")
    run = {"name": "mcp__friday__use_capability", "arguments": {"family": "files"},
           "output": "wrote p.txt", "is_error": False}
    mcp = StandIn(route.RouteResult(True, text="done", calls=(run,),
                                    tools_used=("mcp__friday__use_capability",)))
    out = build(tmp_path, mcp).infer(ask())
    assert out.tools_used == ["mcp__friday__use_capability"] and out.tool_runs == [run]
    assert out.outside_friday == []


def test_the_escalations_of_the_failed_attempt_survive(env, tmp_path, monkeypatch):
    env.setenv("FAKE_GW_FAIL_PROVIDERS", RATE_LIMITED)
    usable(monkeypatch, "claude")
    gw = build(tmp_path, StandIn())
    esc = [{"from_tier": "fast", "to_tier": "deep"}]
    real = gw._infer_routed

    def routed(request):
        out = real(request)
        out.escalations = list(esc)
        return out

    gw._infer_routed = routed
    assert gw.infer(ask()).escalations == esc


# -- the typed turn: what ran through MCP is what the honesty gate judges ------


def test_a_turn_completed_through_the_mcp_route_is_not_called_unbacked(
        env, tmp_path, monkeypatch):
    from test_gateway_llm import _ui_turn
    env.setenv("FAKE_GW_FAIL_PROVIDERS", RATE_LIMITED)
    usable(monkeypatch, "claude")
    run = {"name": "mcp__friday__use_capability",
           "arguments": {"family": "files", "operation": "write",
                         "arguments": {"path": "p.txt", "content": "hi"}},
           "output": "wrote p.txt sha256=" + "ab" * 32 + " readback=ok", "is_error": False}
    said = "I have saved p.txt for you, sir."
    mcp = StandIn(route.RouteResult(True, text=said, calls=(run,),
                                    tools_used=(run["name"],)))
    V, _, _ = _ui_turn(monkeypatch)
    gw = build(tmp_path, mcp)
    monkeypatch.setattr(mg, "gateway", lambda: gw)
    out = V.reply("write hi into p.txt")
    assert len(mcp.calls) == 1
    assert "not actually done that" not in out["reply"].lower()
    assert out["reply"] == said


def test_a_turn_whose_mcp_call_failed_does_not_claim_success(env, tmp_path, monkeypatch):
    from test_gateway_llm import _ui_turn
    env.setenv("FAKE_GW_FAIL_PROVIDERS", RATE_LIMITED)
    usable(monkeypatch, "claude")
    run = {"name": "mcp__friday__use_capability",
           "arguments": {"family": "files", "operation": "write",
                         "arguments": {"path": "p.txt", "content": "hi"}},
           "output": "denied by policy", "is_error": True}
    mcp = StandIn(route.RouteResult(True, text="I have saved p.txt for you, sir.",
                                    calls=(run,), tools_used=(run["name"],)))
    V, _, _ = _ui_turn(monkeypatch)
    gw = build(tmp_path, mcp)
    monkeypatch.setattr(mg, "gateway", lambda: gw)
    out = V.reply("write hi into p.txt")
    assert out["reply"] != "I have saved p.txt for you, sir."
    assert "saved p.txt" not in out["reply"]
