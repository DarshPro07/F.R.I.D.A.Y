"""
Phase 0 regression tests.

Scope: the modernization contract. Providers resolve correctly, unsupported
models cannot start the app, deprecated LiveKit constructor arguments are
gone, and no tool can lie about what it did.

These run offline. Nothing here contacts a model provider or a live MCP
server, and no test asserts on a credential value.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import sys
import threading
from pathlib import Path

import pytest

import agent_friday
from friday import capabilities, config, providers

ROOT = Path(__file__).resolve().parent.parent


def call_tool(register_fn, name: str, arguments: dict | None = None):
    """
    Register a tool module onto a real FastMCP and invoke one tool.

    FastMCP serialises a dict return into a single TextContent holding JSON,
    so decode that back to get at the structured payload the model sees.
    """
    from mcp.server.fastmcp import FastMCP

    server = FastMCP(name="test")
    register_fn(server)
    result = asyncio.run(server.call_tool(name, arguments or {}))
    if isinstance(result, tuple) and len(result) == 2 and isinstance(result[1], dict):
        return result[1]
    if isinstance(result, list) and len(result) == 1 and hasattr(result[0], "text"):
        return json.loads(result[0].text)
    return result


# ---------------------------------------------------------------------------
# Provider registry: role -> model resolution
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("backend", sorted(providers.LLM_ROLE_MODELS))
@pytest.mark.parametrize("role", providers.ROLES)
def test_every_role_resolves_to_an_installed_model(backend, role):
    model = providers.resolve_llm_model(backend, role)
    assert model in providers.installed_llm_models(backend)


def test_backends_use_different_ids_for_the_same_role():
    """The reason roles exist: backends spell the same model differently."""
    google = providers.resolve_llm_model("google", "NORMAL")
    livekit = providers.resolve_llm_model("livekit", "NORMAL")
    assert google != livekit
    assert livekit.startswith("google/")  # Inference namespaces its IDs


def test_unknown_role_is_rejected():
    with pytest.raises(providers.ProviderError, match="unknown LLM role"):
        providers.resolve_llm_model("google", "TURBO")


def test_unknown_backend_is_rejected():
    with pytest.raises(providers.ProviderError, match="unknown LLM backend"):
        providers.resolve_llm_model("bogus", "FAST")


def test_model_missing_from_installed_package_is_rejected(monkeypatch):
    """A model ID the installed package does not expose must not start."""
    monkeypatch.setitem(
        providers.LLM_ROLE_MODELS["google"], "NORMAL", "gemini-3.6-flash"
    )
    with pytest.raises(providers.ProviderError, match="does not expose it"):
        providers.resolve_llm_model("google", "NORMAL")


def test_gemini_35_and_36_are_not_installed():
    """Guard against the doc-driven IDs that this version does not have."""
    installed = providers.installed_llm_models("livekit") | providers.installed_llm_models("google")
    assert not {m for m in installed if "3.5" in m or "3.6" in m}


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------


def test_groq_stt_requires_its_own_credential(monkeypatch):
    """Groq STT is a plugin, not LiveKit Inference: it needs GROQ_API_KEY."""
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    assert providers.missing_credentials("stt", "groq") == ("GROQ_API_KEY",)
    with pytest.raises(providers.ProviderError, match="GROQ_API_KEY"):
        providers.build_stt("groq")


def test_livekit_backend_needs_livekit_credentials_not_google(monkeypatch):
    for name in ("LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "LIVEKIT_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", "irrelevant")
    missing = providers.missing_credentials("llm", "livekit")
    assert set(missing) == {"LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "LIVEKIT_URL"}


def test_missing_credentials_reports_names_never_values(monkeypatch):
    monkeypatch.setenv("SARVAM_API_KEY", "sk_supersecret_value")
    assert providers.missing_credentials("stt", "sarvam") == ()
    monkeypatch.setenv("SARVAM_API_KEY", "   ")  # blank counts as missing
    missing = providers.missing_credentials("stt", "sarvam")
    assert missing == ("SARVAM_API_KEY",)
    assert not any("supersecret" in name for name in missing)


def test_unknown_provider_is_rejected():
    with pytest.raises(providers.ProviderError, match="unknown stt provider"):
        providers.missing_credentials("stt", "nope")


# ---------------------------------------------------------------------------
# Plugin registration threading
#
# livekit.plugins.* call Plugin.register_plugin() as an import side effect,
# which raises off the main thread. The job runner calls the build_* functions
# from a worker thread, so a lazy import inside them fails at the first job
# while the worker still logs as healthy. Regression: that shipped once.
# ---------------------------------------------------------------------------


def test_plugins_are_imported_at_module_scope_not_lazily():
    """Importing friday.providers must already have registered the plugins."""
    for module in (
        "livekit.plugins.sarvam",
        "livekit.plugins.openai",
        "livekit.plugins.google",
        "livekit.plugins.groq",
    ):
        assert module in sys.modules, f"{module} not imported by friday.providers"


@pytest.mark.parametrize(
    "build, args",
    [
        (providers.build_stt, ("sarvam",)),
        (providers.build_stt, ("whisper",)),
        (providers.build_stt, ("groq",)),
        (providers.build_tts, ("openai",)),
        (providers.build_llm, ("google", "NORMAL")),
    ],
)
def test_build_functions_work_off_the_main_thread(build, args, monkeypatch):
    """Exactly how the LiveKit job runner calls them."""
    for name in (
        "SARVAM_API_KEY", "OPENAI_API_KEY", "GROQ_API_KEY", "GOOGLE_API_KEY",
    ):
        monkeypatch.setenv(name, "test-key-not-a-real-credential")

    box: dict = {}

    def run():
        try:
            box["result"] = build(*args)
        except BaseException as exc:  # noqa: BLE001 - re-raised on main thread
            box["error"] = exc

    thread = threading.Thread(target=run)
    thread.start()
    thread.join(timeout=60)

    assert not thread.is_alive(), "build hung on the worker thread"
    if "error" in box:
        raise AssertionError(
            f"{build.__name__}{args} failed off the main thread: {box['error']!r}"
        )
    assert box["result"] is not None


# ---------------------------------------------------------------------------
# LiveKit API modernization
# ---------------------------------------------------------------------------


def test_turn_handling_preserves_previous_behaviour():
    """Same values the deprecated kwargs carried, in the new shape."""
    assert agent_friday.turn_handling_for("sarvam") == {
        "turn_detection": "stt",
        "endpointing": {"min_delay": 0.07},
    }
    assert agent_friday.turn_handling_for("whisper") == {
        "turn_detection": "vad",
        "endpointing": {"min_delay": 0.3},
    }


def test_turn_handling_keys_are_valid_for_installed_livekit():
    from livekit.agents import EndpointingOptions, TurnHandlingOptions

    handling = agent_friday.turn_handling_for("sarvam")
    assert set(handling) <= set(TurnHandlingOptions.__annotations__)
    assert set(handling["endpointing"]) <= set(EndpointingOptions.__annotations__)


def test_agent_session_accepts_turn_handling_without_deprecated_args():
    from livekit.agents.voice import AgentSession

    session = AgentSession(turn_handling=agent_friday.turn_handling_for("sarvam"))
    assert session.turn_detection == "stt"


def test_mcp_toolset_is_constructible_with_a_stable_id():
    from livekit.agents.llm import mcp

    toolset = mcp.MCPToolset(
        id=agent_friday.CLOUD_TOOLSET_ID,
        mcp_server=mcp.MCPServerHTTP(
            url="http://127.0.0.1:8000/sse", transport_type="sse"
        ),
    )
    assert toolset.id == "ada-cloud"


def test_agent_source_has_no_deprecated_constructor_arguments():
    """
    livekit-agents 1.5.1 warns on all of these at runtime. Assert on source so
    the test fails at review time rather than in a log nobody reads.
    """
    source = inspect.getsource(agent_friday)
    for deprecated in ("mcp_servers=", "turn_detection=", "min_endpointing_delay="):
        assert deprecated not in source, f"deprecated argument still present: {deprecated}"


def test_dead_host_resolution_helper_is_gone():
    assert not hasattr(agent_friday, "_get_windows_host_ip")


def test_mcp_url_is_configurable(monkeypatch):
    monkeypatch.setenv("MCP_URL", "http://10.0.0.5:9000/")
    assert agent_friday.mcp_sse_url() == "http://10.0.0.5:9000/sse"
    monkeypatch.delenv("MCP_URL", raising=False)
    assert agent_friday.mcp_sse_url() == "http://127.0.0.1:8000/sse"


# ---------------------------------------------------------------------------
# Truthfulness
# ---------------------------------------------------------------------------


def test_the_search_stub_is_gone_not_merely_reworded():
    """
    Phase 0 replaced the stub's text; Phase 1B deleted the tool. Assert the
    name is unregistered so nothing can route back to it, and that no
    registered tool advertises stub-shaped output.
    """
    from mcp.server.fastmcp import FastMCP

    from friday.tools import register_all_tools

    server = FastMCP(name="test")
    register_all_tools(server)
    names = {tool.name for tool in asyncio.run(server.list_tools())}
    assert "search_web" not in names, "the stub tool is still registered"
    assert "web_search" in names, "the real search tool is missing"

    for tool in asyncio.run(server.list_tools()):
        assert "[stub]" not in (tool.description or "").lower()


def test_system_info_declares_it_is_not_the_users_pc():
    from friday.tools import system

    result = call_tool(system.register, "get_system_info")
    assert result["execution_scope"] == "agent_runtime"
    assert "not the user" in result["describes"]


def test_current_time_declares_scope():
    from friday.tools import system

    result = call_tool(system.register, "get_current_time")
    assert result["execution_scope"] == "agent_runtime"
    assert result["iso8601"]


# ---------------------------------------------------------------------------
# Capability metadata
# ---------------------------------------------------------------------------


def test_every_capability_declares_valid_scope_and_side_effect():
    for cap in capabilities.CAPABILITIES.values():
        assert cap.execution_scope in capabilities.SCOPES
        assert cap.side_effect in capabilities.SIDE_EFFECTS


def test_browser_opening_tools_are_flagged_as_requiring_edge():
    edge = {cap.id for cap in capabilities.requiring_edge()}
    assert {"open_world_monitor", "open_finance_world_monitor"} <= edge


def test_requires_edge_and_user_device_scope_agree():
    """
    The invariant, rather than a frozen list: anything acting on the physical
    machine needs the Edge Controller, and vice versa. Phase 1A added 12 tools
    to this set; the rule is what matters, not the count.
    """
    for cap in capabilities.CAPABILITIES.values():
        assert cap.requires_edge == (cap.execution_scope == "user_device"), (
            f"{cap.id}: requires_edge={cap.requires_edge} but scope="
            f"{cap.execution_scope}"
        )


def test_malformed_capability_is_rejected():
    with pytest.raises(ValueError, match="bad execution_scope"):
        capabilities.Capability(
            id="x", description="", execution_scope="mars", side_effect="none"
        )
    with pytest.raises(ValueError, match="bad side_effect"):
        capabilities.Capability(
            id="x", description="", execution_scope="network", side_effect="explode"
        )


def test_declared_capabilities_cover_every_registered_tool():
    """A new tool must not ship without capability metadata."""
    from mcp.server.fastmcp import FastMCP

    from friday.tools import register_all_tools

    server = FastMCP(name="test")
    register_all_tools(server)
    registered = {tool.name for tool in asyncio.run(server.list_tools())}
    assert registered == set(capabilities.CAPABILITIES)


# ---------------------------------------------------------------------------
# Configuration validation
# ---------------------------------------------------------------------------


def test_config_classifies_every_variable():
    valid = {config.USED, config.RESERVED, config.DEAD}
    for name, (classification, why) in config.VARIABLES.items():
        assert classification in valid, name
        assert why, f"{name} has no explanation"


def test_removed_variables_are_classified_dead():
    dead = set(config.by_classification(config.DEAD))
    assert {"SEARCH_API_KEY", "SUPABASE_URL", "DEEPGRAM_API_KEY"} <= dead


def test_groq_key_is_reserved_not_dead():
    assert "GROQ_API_KEY" in config.by_classification(config.RESERVED)


def test_env_example_documents_only_live_variables():
    """.env.example must not reintroduce a variable classified DEAD."""
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    assignments = {
        line.split("=", 1)[0].strip()
        for line in text.splitlines()
        if "=" in line and not line.strip().startswith("#")
    }
    dead = set(config.by_classification(config.DEAD))
    assert not (assignments & dead), f"dead vars in .env.example: {assignments & dead}"


def test_unknown_env_names_detected():
    assert config.unknown_env_names({"LIVEKIT_URL": "x", "MYSTERY_KEY": "y"}) == (
        "MYSTERY_KEY",
    )


# ---------------------------------------------------------------------------
# Health report
# ---------------------------------------------------------------------------


def test_health_report_never_returns_credential_values(monkeypatch):
    from friday import health

    secret = "sk_this_must_never_appear_anywhere"
    for name in ("SARVAM_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY", "GROQ_API_KEY"):
        monkeypatch.setenv(name, secret)
    assert secret not in str(health.report())


def test_health_report_flags_unresolvable_model(monkeypatch):
    from friday import health

    monkeypatch.setenv("LLM_BACKEND", "bogus")
    data = health.report()
    assert not data["healthy"]
    assert any("bogus" in problem for problem in data["problems"])


def test_fastmcp_dependency_removed_from_pyproject():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    deps = text.split("[project.optional-dependencies]", 1)[0]
    assert '"fastmcp"' not in deps
    assert '"mcp"' in deps
