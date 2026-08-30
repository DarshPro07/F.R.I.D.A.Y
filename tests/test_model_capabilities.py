"""
§22: capability metadata must be declared, never inferred from model IDs.

The two tests that matter are the sync tests: every role-reachable model has a
declaration, and no declaration outlives the model it describes.
"""

from __future__ import annotations

import pytest

from friday import model_capabilities as mc
from friday.providers import LLM_ROLE_MODELS, ROLES


def test_every_role_reachable_model_has_declared_capabilities():
    assert mc.undeclared_models() == [], (
        "these models are reachable via a role but have no declaration: "
        f"{mc.undeclared_models()}"
    )


def test_no_declaration_describes_an_uninstalled_model():
    assert mc.stale_declarations() == [], (
        "these declarations describe models the installed packages do not "
        f"expose: {mc.stale_declarations()}"
    )


@pytest.mark.parametrize("backend", sorted(LLM_ROLE_MODELS))
@pytest.mark.parametrize("role", ROLES)
def test_for_role_resolves_on_every_backend(backend, role):
    caps = mc.for_role(backend, role)
    assert caps.backend == backend
    assert caps.relative_latency in mc.LATENCY
    assert caps.relative_cost in mc.COST


def test_undeclared_model_raises_rather_than_guessing():
    with pytest.raises(mc.CapabilityError, match="do not infer"):
        mc.get("google", "gemini-9-omega")


def test_capabilities_are_not_inferred_from_the_name():
    """
    'lite' is cheaper than 'flash' here only because it is declared so. The
    guard is that a plausible-sounding but undeclared model still fails.
    """
    lite = mc.get("livekit", "google/gemini-2.5-flash-lite")
    flash = mc.get("livekit", "google/gemini-2.5-flash")
    assert lite.relative_cost == "very_low"
    assert flash.relative_cost == "low"
    with pytest.raises(mc.CapabilityError):
        mc.get("livekit", "google/gemini-2.5-flash-lite-preview")


def test_max_context_is_absent_rather_than_guessed():
    """A wrong context window is worse than a missing one."""
    for caps in mc.CAPABILITIES.values():
        assert caps.max_context is None or caps.max_context > 0


def test_supports_reads_declared_fields():
    assert mc.supports("google", "NORMAL", "vision") is True
    assert mc.supports("google", "NORMAL", "realtime_audio") is False
    with pytest.raises(mc.CapabilityError, match="unknown capability field"):
        mc.supports("google", "NORMAL", "telepathy")


def test_invalid_declaration_is_rejected():
    with pytest.raises(ValueError, match="relative_latency"):
        mc.ModelCapabilities(
            model_id="x", provider="google", backend="google", vision=True,
            tools=True, structured_output=True, realtime_audio=False,
            max_context=None, relative_latency="blazing", relative_cost="low",
        )
    with pytest.raises(ValueError, match="max_context"):
        mc.ModelCapabilities(
            model_id="x", provider="google", backend="google", vision=True,
            tools=True, structured_output=True, realtime_audio=False,
            max_context=0, relative_latency="low", relative_cost="low",
        )


def test_preview_channel_models_are_flagged_unknown_health():
    """Preview models must not be presented as proven."""
    for model_id in ("gemini-3-flash-preview", "gemini-3-pro-preview"):
        assert mc.get("google", model_id).health == "unknown"
