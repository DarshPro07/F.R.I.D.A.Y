"""
Dynamic toolsets: a dozen tools in front of the model, not seventy-four.

74 tools meant ~22,700 characters of JSON schema on every request. Gemini
started returning empty completions - FinishReason.STOP with no content, four
retries, then failure. These tests pin the split and, importantly, that no
tool becomes unreachable.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from friday import capability_router as R


def registered_tool_names() -> list[str]:
    from mcp.server.fastmcp import FastMCP

    from friday.tools import register_all_tools

    server = FastMCP(name="test")
    register_all_tools(server)
    return [t.name for t in asyncio.run(server.list_tools())]


# ---------------------------------------------------------------------------
# Nothing may become unreachable
# ---------------------------------------------------------------------------


def test_every_registered_tool_is_core_or_grouped():
    """
    The failure mode of a router is a tool nobody can reach. Every registered
    tool must be in CORE_TOOLS or exactly one group.
    """
    missing = R.unassigned(registered_tool_names())
    assert missing == [], f"these tools are in no group: {missing}"


def test_no_tool_is_in_two_groups():
    seen: dict[str, str] = {}
    for group, names in R.GROUPS.items():
        for name in names:
            assert name not in seen, f"{name} is in both {seen[name]} and {group}"
            seen[name] = group


def test_core_and_groups_do_not_overlap():
    for group, names in R.GROUPS.items():
        clash = set(names) & set(R.CORE_TOOLS)
        assert not clash, f"{group} repeats core tools: {clash}"


def test_every_group_has_a_purpose_line():
    """The model picks a group from these descriptions."""
    for group in R.GROUPS:
        assert R.GROUP_PURPOSE.get(group), f"{group} has no description"


def test_groups_reference_tools_that_exist():
    registered = set(registered_tool_names())
    for group, names in R.GROUPS.items():
        unknown = set(names) - registered
        assert not unknown, f"{group} lists tools that do not exist: {unknown}"


def test_core_tools_all_exist():
    registered = set(registered_tool_names())
    assert not (set(R.CORE_TOOLS) - registered)


# ---------------------------------------------------------------------------
# The payload actually shrinks
# ---------------------------------------------------------------------------


def test_the_active_surface_is_a_fraction_of_the_whole():
    from mcp.server.fastmcp import FastMCP

    from friday.tools import register_all_tools

    server = FastMCP(name="test")
    register_all_tools(server)
    tools = asyncio.run(server.list_tools())

    def payload(subset) -> int:
        return sum(len(t.name) + len(t.description or "")
                   + len(json.dumps(t.inputSchema)) for t in subset)

    router = R.Router()
    router.all_tools = {t.name: t for t in tools}
    active = {t.name for t in tools if t.name in set(router.active_names())}
    starting = [t for t in tools if t.name in active]

    full, reduced = payload(tools), payload(starting)
    assert reduced < full * 0.45, (
        f"starting payload {reduced} is not much smaller than {full}"
    )
    assert len(starting) < len(tools) / 2


# ---------------------------------------------------------------------------
# Router behaviour
# ---------------------------------------------------------------------------


class FakeInfo:
    def __init__(self, name):
        self.name = name


class FakeTool:
    def __init__(self, name):
        self.info = FakeInfo(name)


@pytest.fixture
def router():
    r = R.Router()
    names = list(R.CORE_TOOLS) + [n for names in R.GROUPS.values() for n in names]
    r.load([FakeTool(n) for n in names])
    return r


def test_starts_with_only_the_core(router):
    active = set(router.active_names())
    assert set(R.CORE_TOOLS) <= active
    assert "files_read" not in active
    assert "vision_inspect_camera" not in active


def test_enabling_a_group_adds_exactly_that_group(router):
    changed, message = router.enable("files")
    assert changed and "files_read" in message
    active = set(router.active_names())
    assert "files_read" in active
    assert "browser_open" not in active


def test_groups_are_additive_not_exclusive(router):
    router.enable("files")
    router.enable("vision")
    active = set(router.active_names())
    assert "files_read" in active and "vision_inspect_camera" in active
    assert set(R.CORE_TOOLS) <= active, "core must never be dropped"


def test_enabling_twice_is_harmless(router):
    router.enable("files")
    changed, message = router.enable("files")
    assert not changed
    assert "already enabled" in message


def test_unknown_group_lists_the_real_ones(router):
    changed, message = router.enable("telepathy")
    assert not changed
    assert "unknown group" in message
    assert "files" in message and "vision" in message


def test_all_enables_everything(router):
    changed, _ = router.enable("all")
    assert changed
    assert len(router.active_names()) == len(router.all_tools)


def test_case_and_whitespace_are_forgiven(router):
    changed, _ = router.enable("  FILES  ")
    assert changed
    assert "files_read" in router.active_names()


def test_active_tools_returns_objects_not_names(router):
    router.enable("files")
    tools = router.active_tools()
    assert tools and all(hasattr(t, "info") for t in tools)


def test_describe_reports_the_split(router):
    router.enable("files")
    described = router.describe()
    assert described["enabled_groups"] == ["files"]
    assert described["active_tools"] < described["total_tools"]
    assert "vision" in described["available_groups"]


def test_group_of_finds_the_owner():
    assert R.group_of("files_read") == "files"
    assert R.group_of("vision_inspect_camera") == "vision"
    assert R.group_of("get_current_time") is None  # core, not a group


def test_catalogue_lists_every_group():
    text = R.catalogue()
    for group in R.GROUPS:
        assert group in text


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------


def test_the_prompt_tells_the_model_the_tools_exist():
    import agent_friday as A

    text = A.build_instructions()
    assert "search_capabilities" in text
    assert "use_capability" in text
    assert "more tools than you can see" in text.lower()


def test_the_prompt_forbids_claiming_a_missing_capability():
    import agent_friday as A

    lowered = A.build_instructions().lower()
    assert "before a search is simply wrong" in lowered


# ---------------------------------------------------------------------------
# Search: the proxy half
# ---------------------------------------------------------------------------


class SchemaTool:
    """A tool with the raw_schema shape MCPTool actually has."""

    def __init__(self, name, description="", schema=None):
        self.info = type("I", (), {
            "name": name,
            "raw_schema": {"name": name, "description": description,
                           "parameters": schema or {"properties": {}, "required": []}},
        })()


@pytest.fixture
def searchable():
    from mcp.server.fastmcp import FastMCP

    from friday.tools import register_all_tools

    server = FastMCP(name="test")
    register_all_tools(server)
    tools = asyncio.run(server.list_tools())
    router = R.Router()
    router.load([SchemaTool(t.name, t.description or "", t.inputSchema)
                 for t in tools])
    return router


@pytest.mark.parametrize("query, expected", [
    ("read a file", "files_read"),
    ("look at the screen", "vision_inspect_screen"),
    ("what am I holding", "vision_inspect_camera"),
    ("set a reminder", "reminders_create"),
    ("change the volume", "volume_set"),
    ("open a web page", "browser_open"),
    ("close an app", "apps_close"),
    ("copy to clipboard", "clipboard_write"),
    ("skip this track", "music_next"),
    ("what did we do yesterday", "memory_session_recap"),
    ("what do you know about me", "profile_get"),
])
def test_plain_words_find_the_right_capability(searchable, query, expected):
    """
    The model gets several results and picks, so top-3 is the bar that
    matters, not top-1.
    """
    found = [hit["capability"] for hit in searchable.search(query, limit=3)]
    assert expected in found, f"{query!r} returned {found}"


def test_search_returns_the_arguments_the_model_must_supply(searchable):
    hit = next(h for h in searchable.search("read a file", limit=5)
               if h["capability"] == "files_read")
    assert "path" in hit["arguments"]
    assert hit["arguments"]["path"]["required"] is True
    assert hit["what_it_does"]


def test_search_is_bounded(searchable):
    assert len(searchable.search("file", limit=3)) <= 3


def test_search_on_nonsense_returns_nothing(searchable):
    assert searchable.search("xyzzy plugh frobnicate") == []


def test_empty_query_returns_nothing(searchable):
    assert searchable.search("") == []
    assert searchable.search("   ") == []


def test_invocable_returns_the_tool_object(searchable):
    assert searchable.invocable("files_read") is not None
    assert searchable.invocable("no_such_tool") is None


def test_every_tool_is_searchable_not_just_grouped_ones(searchable):
    """Core tools must be findable too - the model may not recall it has them."""
    found = [h["capability"] for h in searchable.search("remember something", limit=5)]
    assert any(name.startswith("memory_") for name in found)


def test_the_flagship_news_tools_are_always_active():
    """
    Regression for the 2026-08-29 live finding. The persona calls
    get_world_news and open_world_monitor directly and immediately, and rule 1
    forbids the model from naming a tool or running search_capabilities first.
    So these must be reachable with no group enable - i.e. in CORE_TOOLS - or a
    news request comes back "unknown AI function" and Friday says the feed is
    unresponsive. web_news stays grouped as the generic backend.
    """
    flagship = {"get_world_news", "get_world_finance_news",
                "open_world_monitor", "open_finance_world_monitor"}
    assert flagship <= set(R.CORE_TOOLS), (
        "flagship news tools must be core so the persona can call them without "
        "a search_capabilities -> use_capability round-trip")
    assert "web_news" not in R.CORE_TOOLS, "the generic backend stays grouped"

    # And prove it end to end: with nothing enabled, they are active.
    router = R.Router()
    router.load([_tool(n) for n in R.CORE_TOOLS])
    active = set(router.active_names())
    assert flagship <= active, "news must be active with no group enabled"


def _tool(name: str):
    """A minimal stand-in for an MCPTool with just the .info.name the router reads."""
    from types import SimpleNamespace

    return SimpleNamespace(info=SimpleNamespace(name=name))


def test_the_fabric_entry_point_is_always_reachable():
    """
    A live regression (2026-08-29) found the fabric skills reachable but never
    reached: capability_use sat in a group behind a discovery hop the model
    would not take. The entry point - capability_families to see outcomes,
    capability_use to run one - must be in core so a skill request is one call
    away, not three.
    """
    assert "capability_families" in R.CORE_TOOLS
    assert "capability_use" in R.CORE_TOOLS
    # and not also grouped, or it is registered in two places
    assert not any("capability_use" in names for names in R.GROUPS.values())
