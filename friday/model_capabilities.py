"""
Model capability manifest.

Phase 0's registry answers "does this model exist?" by introspecting the
installed package's Literal types. That is the right source for *existence*
and it cannot drift. It says nothing about what a model can DO.

Nothing here is inferred from the model ID string. "flash" does not imply
fast, "pro" does not imply vision, and a name that contains "3" tells you
nothing about tool support. Every field is declared, and a model with no
declaration is a startup error rather than a silent assumption.

Kept separate from providers.py on purpose: existence is discovered,
capability is asserted. Mixing them would let a package upgrade silently
change what the router believes a model can do.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from friday.providers import LLM_ROLE_MODELS, installed_llm_models

LATENCY = ("very_low", "low", "medium", "high")
COST = ("very_low", "low", "medium", "high")
HEALTH = ("ok", "degraded", "unknown", "unavailable")


@dataclass(frozen=True)
class ModelCapabilities:
    model_id: str
    provider: str
    backend: str
    vision: bool
    tools: bool
    structured_output: bool
    realtime_audio: bool
    max_context: int | None
    relative_latency: str
    relative_cost: str
    health: str = "unknown"
    notes: str = ""

    def __post_init__(self) -> None:
        if self.relative_latency not in LATENCY:
            raise ValueError(f"{self.model_id}: bad relative_latency")
        if self.relative_cost not in COST:
            raise ValueError(f"{self.model_id}: bad relative_cost")
        if self.health not in HEALTH:
            raise ValueError(f"{self.model_id}: bad health")
        if self.max_context is not None and self.max_context <= 0:
            raise ValueError(f"{self.model_id}: max_context must be positive")


def _cap(model_id, backend, **kw) -> ModelCapabilities:
    return ModelCapabilities(model_id=model_id, provider="google", backend=backend, **kw)


# Declared, not derived. max_context is None where not independently confirmed -
# a wrong number is worse than an absent one, because the router would trust it.
_ALL: tuple[ModelCapabilities, ...] = (
    # --- backend "google": livekit-plugins-google, direct to Google ---------
    _cap("gemini-2.5-flash", "google", vision=True, tools=True,
         structured_output=True, realtime_audio=False, max_context=None,
         relative_latency="low", relative_cost="low",
         notes="Phase 0 default for NORMAL, and FAST (no lite variant in this plugin)"),
    _cap("gemini-3-flash-preview", "google", vision=True, tools=True,
         structured_output=True, realtime_audio=False, max_context=None,
         relative_latency="medium", relative_cost="medium",
         health="unknown", notes="preview channel; treat availability as unproven"),
    _cap("gemini-3-pro-preview", "google", vision=True, tools=True,
         structured_output=True, realtime_audio=False, max_context=None,
         relative_latency="high", relative_cost="high",
         health="unknown", notes="preview channel; ULTRA role only"),
    # --- backend "livekit": LiveKit Inference ------------------------------
    _cap("google/gemini-2.5-flash-lite", "livekit", vision=True, tools=True,
         structured_output=True, realtime_audio=False, max_context=None,
         relative_latency="very_low", relative_cost="very_low",
         notes="cheapest FAST tier; Inference-only"),
    _cap("google/gemini-2.5-flash", "livekit", vision=True, tools=True,
         structured_output=True, realtime_audio=False, max_context=None,
         relative_latency="low", relative_cost="low"),
    _cap("google/gemini-3-flash", "livekit", vision=True, tools=True,
         structured_output=True, realtime_audio=False, max_context=None,
         relative_latency="medium", relative_cost="medium"),
    _cap("google/gemini-3-pro", "livekit", vision=True, tools=True,
         structured_output=True, realtime_audio=False, max_context=None,
         relative_latency="high", relative_cost="high"),
)

CAPABILITIES: dict[tuple[str, str], ModelCapabilities] = {
    (cap.backend, cap.model_id): cap for cap in _ALL
}


class CapabilityError(KeyError):
    """A model is in use with no declared capabilities."""


def get(backend: str, model_id: str) -> ModelCapabilities:
    key = (backend, model_id)
    if key not in CAPABILITIES:
        raise CapabilityError(
            f"no declared capabilities for {model_id!r} on backend {backend!r}. "
            "Declare them in friday/model_capabilities.py - do not infer them "
            "from the model name."
        )
    return CAPABILITIES[key]


def for_role(backend: str, role: str) -> ModelCapabilities:
    return get(backend, LLM_ROLE_MODELS[backend][role])


def supports(backend: str, role: str, capability: str) -> bool:
    """e.g. supports('google', 'NORMAL', 'vision')."""
    caps = for_role(backend, role)
    if not hasattr(caps, capability):
        raise CapabilityError(f"unknown capability field {capability!r}")
    return bool(getattr(caps, capability))


def undeclared_models() -> list[tuple[str, str]]:
    """Any (backend, model) reachable via a role but lacking a declaration."""
    missing = []
    for backend, roles in LLM_ROLE_MODELS.items():
        for model_id in roles.values():
            if (backend, model_id) not in CAPABILITIES:
                missing.append((backend, model_id))
    return sorted(set(missing))


def stale_declarations() -> list[tuple[str, str]]:
    """Declarations for models the installed packages no longer expose."""
    stale = []
    for (backend, model_id) in CAPABILITIES:
        try:
            if model_id not in installed_llm_models(backend):
                stale.append((backend, model_id))
        except Exception:  # unknown backend - reported by providers instead
            stale.append((backend, model_id))
    return sorted(stale)


def as_dicts() -> list[dict]:
    return [asdict(cap) for cap in _ALL]
