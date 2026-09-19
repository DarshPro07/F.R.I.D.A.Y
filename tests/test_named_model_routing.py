"""Named-model routing (FR-030 / FR-033): the owner picks the model by
saying it, and the catalog decides whether that is possible.

Measured live 2026-09-19 (scripts/route_delegation_probe.py): "use gemini
for this", "send this to opus" and "use gpt-5.6-terra" all routed to the
tier's haiku/sonnet - a spoken model was ignored unless it was one of the
tier words. Here the profile home is a temp dir with a catalog and a
credential pool of the test's own, so nothing depends on this machine's
provider cache.
"""
from __future__ import annotations

import json

import pytest

import friday.execution_economics as ee


CATALOG = {
    "anthropic": {"models": ["claude-opus-4-8", "claude-opus-5", "claude-sonnet-5",
                             "claude-haiku-4-5-20251001", "claude-fable-5"]},
    "gemini": {"models": ["gemini-3.1-pro-preview", "gemini-3.5-flash", "gemini-3.8-flash",
                          "gemini-3.6-flash-lite"]},
    "openai-api": {"models": ["gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.5"]},
    # listed in the catalog but NOT credentialed: must never be chosen
    "nvidia": {"models": ["nvidia/nemotron-3-ultra-550b-a55b"]},
}
POOL = {"anthropic": {}, "gemini": {}, "openai-api": {}}


@pytest.fixture
def profile(tmp_path, monkeypatch):
    home = tmp_path / "friday-profile"
    home.mkdir()
    (home / "provider_models_cache.json").write_text(json.dumps(CATALOG), encoding="utf-8")
    (home / "auth.json").write_text(json.dumps({"credential_pool": POOL}), encoding="utf-8")
    (home / "config.yaml").write_text("model:\n  default: claude-opus-5\n  provider: anthropic\n", encoding="utf-8")
    monkeypatch.setenv(ee.__dict__.get("ENV_PROFILE_HOME", "FRIDAY_HERMES_PROFILE_HOME"), str(home))
    from friday import hermes_bridge as hb
    monkeypatch.setenv(hb.ENV_PROFILE_HOME, str(home))
    ee._tier_table.cache_clear()
    ee.known_models.cache_clear()
    yield home
    ee._tier_table.cache_clear()
    ee.known_models.cache_clear()


class TestModelFromRequirements:

    @pytest.mark.parametrize("text,expected", [
        ("use gemini for this: summarise the README", ("gemini", "gemini-3.8-flash")),
        ("ask gemini flash to summarise", ("gemini", "gemini-3.8-flash")),
        ("use gemini pro", ("gemini", "gemini-3.1-pro-preview")),
        ("this to gemini 3.5 flash", ("gemini", "gemini-3.5-flash")),
        ("send this to opus: design the plugin lifecycle", ("anthropic", "claude-opus-5")),
        ("send this to opus 4.8", ("anthropic", "claude-opus-4-8")),
        ("route this to sonnet please", ("anthropic", "claude-sonnet-5")),
        ("with haiku, rename foo", ("anthropic", "claude-haiku-4-5-20251001")),
        ("use gpt-5.6-terra for this one: write the tests", ("openai-api", "gpt-5.6-terra")),
        ("give it to 5.6 terra", ("openai-api", "gpt-5.6-terra")),
        ("hand it to terra", ("openai-api", "gpt-5.6-terra")),
        ("let codex write the tests", ("openai-api", "gpt-5.6-terra")),
    ])
    def test_a_named_model_resolves_through_the_catalog(self, profile, text, expected):
        provider, model, family = ee.model_from_requirements(text)
        assert (provider, model) == expected, (text, family)

    @pytest.mark.parametrize("text", [
        "the gemini adapter is broken, fix it",
        "fix the opus fallback bug",
        "write a note about how sonnet compares to haiku",
        "add a one-line docstring to _busy",
    ])
    def test_a_mentioned_model_is_not_a_request(self, profile, text):
        assert ee.model_from_requirements(text) == ("", "", ""), text

    def test_the_profile_default_wins_inside_its_own_family(self, profile):
        """"send this to opus" on a profile whose default is claude-opus-5
        means that opus, not the highest-numbered sibling by accident."""
        assert ee.model_from_requirements("send this to opus")[1] == "claude-opus-5"

    def test_an_uncredentialed_provider_is_never_chosen(self, profile):
        """nvidia lists nemotron and holds no credential: the request is
        named back to the caller as unhonoured, never routed to nvidia."""
        provider, model, family = ee.model_from_requirements("use nemotron for the summary")
        assert (provider, model) == ("", "")
        assert family == "nemotron"

    def test_a_version_nobody_lists_is_unhonoured_not_substituted(self, profile):
        provider, model, family = ee.model_from_requirements("use gemini 9.9 flash")
        assert (provider, model) == ("", "")
        assert family.startswith("gemini")

    def test_no_credential_file_means_no_cross_provider_route(self, profile):
        (profile / "auth.json").unlink()
        assert ee.model_from_requirements("use gemini for this")[:2] == ("", "")


class TestPlanDelegation:

    def test_a_named_model_becomes_the_pin_with_its_provider(self, profile):
        plan = ee.plan_delegation("use gemini for this: summarise the README")
        assert (plan["provider"], plan["model"]) == ("gemini", "gemini-3.8-flash")
        assert "gemini requested in the goal" in plan["reason"]
        assert plan["unhonoured"] == ""

    def test_a_named_model_does_not_change_the_route_level(self, profile):
        """"use haiku" on an auth change: the model is his choice, the
        verification depth is not - consequence still decides the level."""
        plan = ee.plan_delegation("with haiku, rotate the session secret in the auth middleware")
        assert plan["model"] == "claude-haiku-4-5-20251001"
        assert plan["level"] == ee.HERMES_DEEP

    def test_an_unhonourable_name_falls_to_the_tier_and_says_so(self, profile):
        plan = ee.plan_delegation("use nemotron for this: write the tests")
        assert plan["model"] and plan["model"] != ""
        assert "nemotron" in plan["unhonoured"]
        assert plan["reason"].startswith("no credentialed provider lists a nemotron model")

    def test_a_caller_pin_still_beats_everything(self, profile):
        plan = ee.plan_delegation("use gemini for this", model="claude-sonnet-5")
        assert plan["model"] == "claude-sonnet-5"
        assert plan["reason"].startswith("model pinned by caller")

    def test_routing_still_costs_zero_model_calls(self):
        import inspect
        for fn in (ee.model_from_requirements, ee._catalog, ee._credentialed_providers):
            source = inspect.getsource(fn)
            for banned in ("request(", "prompt.submit", "delegate(", "session.create", "urllib", "httpx"):
                assert banned not in source, (fn.__name__, banned)

    def test_the_credential_pool_is_read_as_names_only(self, profile):
        """The auth file's VALUES never reach the router's output."""
        (profile / "auth.json").write_text(json.dumps({"credential_pool": {"gemini": {"api_key": "sk-SECRET"}}}), encoding="utf-8")
        plan = ee.plan_delegation("use gemini for this")
        assert "SECRET" not in json.dumps(plan)
        assert plan["provider"] == "gemini"


class TestProviderTravelsWithTheModel:
    """The three delegation call sites hand Hermes the provider next to the
    model (FR-030: a named Gemini model sent under the anthropic default
    is a 404 at best and a cross-provider leak at worst)."""

    def test_every_delegate_call_passes_provider(self):
        import inspect
        from friday import voice_brain
        from friday.tools import hermes_control
        from friday.executors import hermes as hx
        for src in (inspect.getsource(voice_brain._run_hermes),
                    inspect.getsource(hermes_control),
                    inspect.getsource(hx)):
            for call in [s for s in src.split(".delegate(")[1:]]:
                head = call[:400]
                assert "provider=" in head, head[:200]
