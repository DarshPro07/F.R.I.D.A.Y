"""
Hard regression for the runtime path: capability_use -> fabric -> adapter.

The unit tests elsewhere prove each adapter in isolation. This proves the
thing that actually failed in production: that a request reaching the MCP
bridge routes to the right provider, that adversarial input is refused at the
bridge and not just deep in an adapter, and that a newly added adapter can be
picked up without a restart. These are the prompts a hostile or careless
caller would send, run through the real `fabric_control` tool functions.
"""

from __future__ import annotations

import importlib.util

import pytest

from friday import fabric


# --- capture the real @mcp.tool functions the server registers -------------

class _Registrar:
    """Minimal stand-in for FastMCP: keeps the functions register() decorates."""

    def __init__(self) -> None:
        self.tools: dict = {}

    def tool(self, *args, **kwargs):
        def take(fn):
            self.tools[fn.__name__] = fn
            return fn
        # support both @mcp.tool and @mcp.tool()
        if args and callable(args[0]):
            return take(args[0])
        return take


@pytest.fixture()
def tools():
    from friday.tools import fabric_control

    reg = _Registrar()
    fabric_control.register(reg)
    fabric.reload()
    yield reg.tools
    fabric.reload()


def use(tools, family, operation, **arguments):
    return tools["capability_use"](family, operation, arguments)


def _needs_pack(*upstreams: str):
    """These routing tests read real upstream packs through the bridge. On a
    fresh checkout the packs are empty gitlink placeholders (no submodule
    machinery fills them), so the honest outcome there is a skip that names
    the missing clone - not a failure that reads as broken routing."""
    from friday.fabric_adapters import _skillpack
    missing = [u for u in upstreams if not _skillpack.cloned(u)]
    return pytest.mark.skipif(
        bool(missing),
        reason=f"upstream pack(s) not cloned: {missing}; scripts/fabric_upstreams.py clone")


research_pack = _needs_pack("scientific-agent-skills")
roles_pack = _needs_pack("gstack")
security_pack = _needs_pack("anthropic-cybersecurity-skills")
scrapling_installed = pytest.mark.skipif(
    importlib.util.find_spec("scrapling") is None,
    reason="scrapling not installed (pip install -e .[web])")


# --- routing: the family reaches the right provider ------------------------


@research_pack
def test_research_routes_to_the_science_skills_provider(tools):
    out = use(tools, "research", "search", query="single cell rna sequencing")
    assert out["status"] == "succeeded", out
    names = {row["skill"] for row in out["output"]}
    assert names & {"scanpy", "anndata", "scvelo"}, names


@scrapling_installed
def test_scraping_routes_to_scrapling_and_extracts_fields(tools):
    html = ("<div class=c><h3 class=n>Widget</h3><span class=p>42</span></div>"
            "<div class=c><h3 class=n>Gadget</h3><span class=p>7</span></div>")
    out = use(tools, "scraping", "fields", html=html,
              fields={"name": "h3.n", "price": "span.p"})
    assert out["status"] == "succeeded", out
    assert out["output"] == {"name": ["Widget", "Gadget"], "price": ["42", "7"]}


@roles_pack
def test_roles_routes_to_gstack_and_ranks_the_right_workflow(tools):
    out = use(tools, "roles", "route", task="review this pr before landing")
    assert out["status"] == "succeeded", out
    assert out["output"][0]["skill"] == "review"


def test_the_family_is_named_by_outcome_not_by_brand(tools):
    """The caller says 'research', never 'science_skills'. Brands stay internal."""
    families = tools["capability_families"]()
    said = families["say"].lower()
    assert "science_skills" not in said and "scrapling" not in said


# --- adversarial input, refused at the bridge ------------------------------


def test_an_unknown_family_fails_honestly_rather_than_crashing(tools):
    out = use(tools, "telepathy", "read_minds", target="the boss")
    assert out["status"] == "failed"
    assert out["error"]
    assert out["output"] is None


def test_an_unknown_operation_in_a_real_family_is_refused(tools):
    out = use(tools, "research", "exfiltrate", query="secrets")
    assert out["status"] == "failed"


def test_path_traversal_through_the_bridge_is_refused(tools):
    """
    The skill operations allowlist by catalogue. A caller asking for a skill
    named like a path must not read outside the pack.
    """
    out = use(tools, "research", "skill", name="../../../../.env")
    assert out["status"] == "failed"
    assert ".env" not in str(out["output"])


def test_a_licence_blocked_skill_cannot_be_reached_through_the_bridge(tools):
    """scientific-agent-skills' pdf skill is Anthropic-proprietary."""
    out = use(tools, "research", "skill", name="pdf")
    assert out["status"] == "failed"


def test_malformed_arguments_do_not_crash_the_bridge(tools):
    # fields wants a dict; hand it a string.
    out = use(tools, "scraping", "fields", html="<p>x</p>", fields="not a dict")
    assert out["status"] == "failed"
    assert out["output"] is None


# --- the security family stays gated through the bridge ---------------------


@security_pack
def test_a_security_procedure_needs_scope_even_through_the_bridge(tools):
    """
    The scope gate is the adapter's, and the bridge must not bypass it. Reading
    an offensive procedure with no authorized_scope is refused.
    """
    out = use(tools, "security", "skill",
              name="abusing-dpapi-for-credential-access")
    assert out["status"] == "failed"
    assert "authorized_scope" in (out["error"] or "").lower() or \
           "authorized_scope" in str(out["output"]).lower()


@security_pack
def test_security_search_is_open_because_knowing_is_not_doing(tools):
    out = use(tools, "security", "search", query="detect credential dumping")
    assert out["status"] == "succeeded", out
    assert out["output"]


# --- reload: a new adapter is reachable without a restart ------------------


def test_capability_reload_picks_up_a_newly_added_provider(tools, tmp_path,
                                                           monkeypatch):
    """
    The production failure this closes: the server cached its registry before
    an adapter existed, so capability_use said "no provider available" for a
    provider that was on disk. reload drops the cache and re-discovers.
    """
    import importlib
    import sys

    # A provider that is not in the registry until we add its module.
    before = tools["capability_reload"]()
    assert "diagnostic_probe" not in set(fabric.registry())

    module = type(sys)("friday.fabric_adapters.diagnostic_probe")
    module.DESCRIPTOR = fabric.Provider(
        id="diagnostic_probe", family="diagnostic", upstream="",
        operations=("ping",), risk="low",
        license_mode=fabric.BUILTIN_LICENSE, integration_mode=fabric.BUILTIN)
    module.health = lambda handle: {"state": fabric.READY, "detail": "ok"}
    module.call = lambda operation, handle, **kw: "pong"
    monkeypatch.setitem(sys.modules,
                        "friday.fabric_adapters.diagnostic_probe", module)

    # Make _discover list it among the package's modules. Real iter_modules
    # yields pkgutil.ModuleInfo namedtuples, and _discover reads .name, so the
    # fake must yield the same shape or it would not exercise the real loop.
    import pkgutil
    real_iter = pkgutil.iter_modules

    def fake_iter(path=None, prefix=""):
        yield from real_iter(path, prefix)
        yield pkgutil.ModuleInfo(None, "diagnostic_probe", False)

    monkeypatch.setattr(pkgutil, "iter_modules", fake_iter)

    result = tools["capability_reload"]()
    assert "diagnostic_probe" in result["added"], result
    assert "diagnostic_probe" in set(fabric.registry())


def test_reload_reports_what_changed_by_id_not_just_a_count(tools):
    result = tools["capability_reload"]()
    assert set(result) >= {"providers", "added", "removed", "say"}
    assert isinstance(result["added"], list)
    assert isinstance(result["removed"], list)


# --- an unavailable provider degrades, it does not crash -------------------


def test_a_family_whose_provider_is_unavailable_fails_without_crashing(tools,
                                                                       monkeypatch):
    """
    code_intelligence -> graft needs the graft CLI; when it and CBM are both
    down the bridge returns a failed result naming the layer, not an exception.
    """
    monkeypatch.setattr(fabric, "call_with_fallback",
                        lambda *a, **k: _failed("all providers down"))
    out = use(tools, "code_intelligence", "map")
    assert out["status"] == "failed"
    assert out["error"]


def _failed(msg):
    from friday import contracts as c
    r = c.started("t", "capability.use")
    return c.failed(r, msg)
