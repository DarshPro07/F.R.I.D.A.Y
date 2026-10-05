"""Model selection from the spoken requirement, validated against the cache."""

from __future__ import annotations

from friday import execution_economics as ee


def test_a_named_requirement_picks_the_tier_but_not_the_level():
    cheap = ee.plan_delegation("use the cheapest model: redesign the auth core")
    deep = ee.plan_delegation("think hard: redesign the auth core")
    plain = ee.plan_delegation("redesign the auth core")
    assert cheap["tier"] == ee.TIER_ECONOMY
    assert deep["tier"] == ee.TIER_DEEP
    assert cheap["level"] == plain["level"]      # consequence still routes
    assert "requested in the goal" in cheap["reason"]


def test_an_explicit_model_beats_the_requirement():
    p = ee.plan_delegation("cheapest possible", model="claude-opus-5")
    assert p["model"] == "claude-opus-5" and "pinned by caller" in p["reason"]


def test_unknown_tier_models_fall_back_to_the_profile_default(monkeypatch):
    monkeypatch.setattr(ee, "known_models", lambda: frozenset({"claude-opus-5"}))
    monkeypatch.setattr(ee, "DEFAULT_TIERS", {ee.TIER_ECONOMY: "claude-does-not-exist",
                                              ee.TIER_STANDARD: "", ee.TIER_DEEP: ""})
    ee._tier_table.cache_clear()
    try:
        assert ee.resolve_model(ee.TIER_ECONOMY) == ""
    finally:
        ee._tier_table.cache_clear()


def test_the_cache_reader_understands_both_shapes(tmp_path, monkeypatch):
    home = tmp_path
    (home / "provider_models_cache.json").write_text(
        '{"a": {"models": ["m1", {"id": "m2"}]}, "b": [{"id": "m3"}]}',
        encoding="utf-8")
    monkeypatch.setenv("FRIDAY_HERMES_PROFILE_HOME", str(home))
    ee.known_models.cache_clear()
    try:
        assert ee.known_models() == {"m1", "m2", "m3"}
    finally:
        ee.known_models.cache_clear()


def _profile(tmp_path, monkeypatch, config: str):
    (tmp_path / "config.yaml").write_text(config, encoding="utf-8")
    monkeypatch.setenv("FRIDAY_HERMES_PROFILE_HOME", str(tmp_path))
    monkeypatch.setattr(ee, "known_models", lambda: frozenset())
    ee._tier_table.cache_clear()


def test_anthropic_defaults_are_not_offered_to_another_main_provider(tmp_path, monkeypatch):
    # FRIDAY.exe 2026-10-05: a fresh friday profile with main provider openai-codex
    # was asked for claude-haiku-4-5 on every fast call.
    _profile(tmp_path, monkeypatch, "model:\n  provider: openai-codex\n  default: gpt-5.5\n")
    try:
        assert ee._tier_table() == {ee.TIER_ECONOMY: "", ee.TIER_STANDARD: "", ee.TIER_DEEP: ""}
    finally:
        ee._tier_table.cache_clear()


def test_a_profiles_own_tiers_still_win_on_any_provider(tmp_path, monkeypatch):
    _profile(tmp_path, monkeypatch, "model:\n  provider: openai-codex\n"
                                    "routing:\n  tiers:\n    economy: gpt-5.5-mini\n")
    try:
        assert ee._tier_table()[ee.TIER_ECONOMY] == "gpt-5.5-mini"
        assert ee._tier_table()[ee.TIER_STANDARD] == ""
    finally:
        ee._tier_table.cache_clear()


def test_an_anthropic_profile_keeps_the_defaults(tmp_path, monkeypatch):
    _profile(tmp_path, monkeypatch, "model:\n  provider: anthropic\n  default: claude-opus-5\n")
    try:
        assert ee._tier_table() == dict(ee.DEFAULT_TIERS)
    finally:
        ee._tier_table.cache_clear()
