"""
Provider registry - the single place that decides which STT / LLM / TTS
implementation the agent runs, and which concrete model ID a role maps to.

Two rules this module exists to enforce:

1. Roles are separate from model IDs. Callers ask for NORMAL or DEEP, never
   for "gemini-2.5-flash". A LiveKit upgrade changes the table here, not the
   agent.
2. A model ID that the installed packages do not expose must fail loudly at
   startup, not silently at the first turn.

Model catalogues are read from the installed packages' own ``Literal`` types
via ``typing.get_args``, so this file cannot drift from what is installed.

IMPORTANT - do not make these plugin imports lazy. Every ``livekit.plugins.*``
package calls ``Plugin.register_plugin()`` as an import side effect, and that
raises "Plugins must be registered on the main thread" (agents/plugin.py:32).
The job runner calls ``build_stt`` / ``build_llm`` / ``build_tts`` from a worker
thread, so importing a plugin inside those functions blows up on the first job
while the worker still looks healthy. Module scope means registration happens
once, on the main thread, at process start.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import get_args

import logging

# Import for the registration side effect as much as for the names. See above.
from livekit.agents import inference
from livekit.plugins import google as lk_google
from livekit.plugins import groq as lk_groq
from livekit.plugins import openai as lk_openai
from livekit.plugins import sarvam

# ---------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------

logger = logging.getLogger("friday.providers")

ROLES = ("FAST", "NORMAL", "DEEP", "ULTRA")
DEFAULT_ROLE = "NORMAL"

# LLM backends.
#   "google"  - livekit-plugins-google, talks to Google directly (GOOGLE_API_KEY)
#   "livekit" - LiveKit Inference, routed through LiveKit (LiveKit credentials)
#
# The two backends spell the same model differently, which is the entire
# reason roles exist. Verified against the installed 1.5.1 packages.
LLM_ROLE_MODELS: dict[str, dict[str, str]] = {
    "google": {
        # livekit-plugins-google 1.5.1 exposes no 2.5-flash-lite, so FAST and
        # NORMAL resolve to the same model on this backend. Documented in
        # docs/phase0/PROVIDER_MATRIX.md rather than silently aliased.
        "FAST": "gemini-2.5-flash",
        "NORMAL": "gemini-2.5-flash",
        "DEEP": "gemini-3-flash-preview",
        "ULTRA": "gemini-3-pro-preview",
    },
    "livekit": {
        "FAST": "google/gemini-2.5-flash-lite",
        "NORMAL": "google/gemini-2.5-flash",
        "DEEP": "google/gemini-3-flash",
        "ULTRA": "google/gemini-3-pro",
    },
}

DEFAULT_LLM_BACKEND = "google"  # preserves pre-Phase-0 behaviour

# provider -> env vars that must be non-empty for it to work
STT_CREDENTIALS = {
    "sarvam": ("SARVAM_API_KEY",),
    "whisper": ("OPENAI_API_KEY",),
    "groq": ("GROQ_API_KEY",),
}
TTS_CREDENTIALS = {
    "openai": ("OPENAI_API_KEY",),
    "sarvam": ("SARVAM_API_KEY",),
}
LLM_CREDENTIALS = {
    "google": ("GOOGLE_API_KEY",),
    "livekit": ("LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "LIVEKIT_URL"),
}

DEFAULT_STT = "sarvam"
DEFAULT_TTS = "openai"


class ProviderError(RuntimeError):
    """Configuration is wrong in a way that must stop startup."""


# ---------------------------------------------------------------------------
# Installed-model introspection
# ---------------------------------------------------------------------------


def _flatten_literal(annotation) -> set[str]:
    """Collect string members from a Literal, or from a Union of Literals."""
    found: set[str] = set()
    for arg in get_args(annotation):
        if isinstance(arg, str):
            found.add(arg)
        else:
            found |= _flatten_literal(arg)
    return found


@lru_cache(maxsize=None)
def installed_llm_models(backend: str) -> frozenset[str]:
    """Model IDs the *installed* packages actually advertise for a backend."""
    if backend == "google":
        from livekit.plugins.google.models import ChatModels

        return frozenset(_flatten_literal(ChatModels))
    if backend == "livekit":
        from livekit.agents.inference.llm import LLMModels

        return frozenset(_flatten_literal(LLMModels))
    raise ProviderError(
        f"unknown LLM backend {backend!r}; known: {sorted(LLM_ROLE_MODELS)}"
    )


# ---------------------------------------------------------------------------
# Resolution + validation
# ---------------------------------------------------------------------------


def resolve_llm_model(backend: str, role: str) -> str:
    """Role -> concrete model ID, checked against what is installed."""
    if backend not in LLM_ROLE_MODELS:
        raise ProviderError(
            f"unknown LLM backend {backend!r}; known: {sorted(LLM_ROLE_MODELS)}"
        )
    table = LLM_ROLE_MODELS[backend]
    if role not in table:
        raise ProviderError(
            f"unknown LLM role {role!r} for backend {backend!r}; known: {sorted(table)}"
        )

    model = table[role]
    available = installed_llm_models(backend)
    if model not in available:
        raise ProviderError(
            f"role {role} maps to {model!r} on backend {backend!r}, but the "
            f"installed package does not expose it. Installed: {sorted(available)}"
        )
    return model


def missing_credentials(kind: str, provider: str) -> tuple[str, ...]:
    """Env vars this provider needs that are absent or blank. Never returns values."""
    table = {"stt": STT_CREDENTIALS, "tts": TTS_CREDENTIALS, "llm": LLM_CREDENTIALS}[kind]
    if provider not in table:
        raise ProviderError(
            f"unknown {kind} provider {provider!r}; known: {sorted(table)}"
        )
    return tuple(name for name in table[provider] if not os.getenv(name, "").strip())


def _require_credentials(kind: str, provider: str) -> None:
    missing = missing_credentials(kind, provider)
    if missing:
        raise ProviderError(
            f"{kind} provider {provider!r} needs {', '.join(missing)} "
            f"to be set in .env"
        )


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------


def build_stt(provider: str = DEFAULT_STT, *, http_session=None):
    """
    Build an STT.

    ``http_session``: LiveKit plugins normally take their aiohttp session from
    the job context. Outside a job - an eval harness, a script - that raises
    "Attempted to use an http session outside of a job context", so callers
    running standalone must pass their own session. Providers that manage
    their own client (OpenAI, via httpx) ignore it.
    """
    _require_credentials("stt", provider)

    if provider == "sarvam":
        # en-IN, not "unknown". Auto-detect was transcribing into whatever
        # language it guessed - the boss speaks English and Hinglish, and
        # en-IN is the code that covers both: Indian English with Hindi mixed
        # in is exactly what it is trained for. "unknown" makes every
        # utterance a language-identification problem before it is a
        # transcription problem, which is slower and wrong more often.
        return sarvam.STT(
            language=os.getenv("ADA_STT_LANGUAGE", "en-IN"),
            model="saaras:v3",
            mode="transcribe",
            flush_signal=True,
            sample_rate=16000,
            **({"http_session": http_session} if http_session is not None else {}),
        )
    if provider == "whisper":
        return lk_openai.STT(model="whisper-1")
    if provider == "groq":
        # Groq STT is a plugin, NOT part of LiveKit Inference in 1.5.1.
        # It always egresses to Groq with GROQ_API_KEY.
        return lk_groq.STT(model="whisper-large-v3-turbo")

    raise ProviderError(
        f"unknown STT provider {provider!r}; known: {sorted(STT_CREDENTIALS)}"
    )


def build_llm(backend: str = DEFAULT_LLM_BACKEND, role: str = DEFAULT_ROLE):
    model = resolve_llm_model(backend, role)  # validate before touching creds
    _require_credentials("llm", backend)

    if backend == "google":
        return lk_google.LLM(model=model, api_key=os.getenv("GOOGLE_API_KEY"))
    if backend == "livekit":
        return inference.LLM(model=model)

    raise ProviderError(f"unknown LLM backend {backend!r}")


#: Where to go when the primary model has genuinely failed its retries.
#:
#: A different role, not a different provider, because the failure being
#: covered is Gemini returning nothing on one serving path - a different model
#: is a different path. Set ADA_LLM_FALLBACK to a role name, or "off".
FALLBACK_ROLE = "DEEP"

#: Retries stay with the primary before the fallback is reached, so this is
#: strictly a net under existing behaviour rather than a change to it. The
#: timeout is generous on purpose: the default 5s would cut off a long but
#: perfectly healthy generation, which would be a regression dressed as a fix.
FALLBACK_RETRIES_PER_MODEL = 2
FALLBACK_ATTEMPT_TIMEOUT = 30.0


def fallback_role(role: str = DEFAULT_ROLE) -> str | None:
    """The role to fall back to, or None when fallback is off or pointless."""
    wanted = os.getenv("ADA_LLM_FALLBACK", FALLBACK_ROLE).strip()
    if wanted.lower() in ("off", "none", "0", "false"):
        return None
    wanted = wanted.upper()
    return None if wanted == role.upper() or wanted not in ROLES else wanted


def build_resilient_llm(backend: str = DEFAULT_LLM_BACKEND, role: str = DEFAULT_ROLE):
    """
    The primary model with a second one behind it.

    Returns the plain LLM when there is nothing sensible to fall back to -
    an adapter wrapping a single model is just indirection.
    """
    primary = build_llm(backend, role)
    secondary_role = fallback_role(role)
    if secondary_role is None:
        return primary

    try:
        secondary = build_llm(backend, secondary_role)
    except ProviderError:
        # The fallback role is not installed on this backend. Not a reason to
        # refuse to start; the primary is the one that matters.
        return primary

    from livekit.agents.llm import FallbackAdapter

    _share_thought_signatures(primary, secondary)
    return FallbackAdapter(
        [primary, secondary],
        max_retry_per_llm=FALLBACK_RETRIES_PER_MODEL,
        attempt_timeout=FALLBACK_ATTEMPT_TIMEOUT,
    )


def _share_thought_signatures(*models) -> bool:
    """
    Let the fallback finish a conversation the primary started.

    Gemini 2.5 and 3 require every function-call part in a multi-turn tool
    conversation to carry back the `thought_signature` the model produced with
    it. The installed plugin (livekit-plugins-google 1.5.1) does this, keyed by
    call id - in a dict that belongs to ONE LLM instance:

        llm.py:232   self._thought_signatures: dict[str, bytes] = {}
        llm.py:564   self._llm._thought_signatures[call_id] = part.thought_signature

    A FallbackAdapter holds two instances. The moment the primary fails and
    the second one takes the conversation over, it is rebuilding a request
    whose earlier function calls were made by the other model - and it has
    none of their signatures. Gemini answers:

        400 Function call is missing a thought_signature in functionCall
            parts ... `default_api:search_capabilities`, position 3

    with retryable=False, so the fallback exhausts too and the turn dies as
    "failed to generate LLM completion". That is the failure the boss has been
    seeing, and it gets *more* likely the longer a conversation runs, because
    it needs an earlier tool call to have happened.

    One dict between them fixes it: the signature belongs to the conversation,
    not to whichever model happened to produce it. Reaching into a private
    attribute is not free, so it is guarded and reported - and a plugin that
    renames it degrades to today's behaviour rather than crashing.
    """
    holders = [m for m in models if hasattr(m, "_thought_signatures")]
    if len(holders) < 2:
        if holders:
            logger.debug("thought signatures: only one model tracks them")
        return False
    shared = holders[0]._thought_signatures
    for other in holders[1:]:
        other._thought_signatures = shared
    return True


def build_tts(provider: str = DEFAULT_TTS, *, speed: float = 1.0, http_session=None):
    """``http_session``: see build_stt."""
    _require_credentials("tts", provider)

    if provider == "openai":
        return lk_openai.TTS(model="tts-1", voice="nova", speed=speed)
    if provider == "sarvam":
        return sarvam.TTS(
            target_language_code="en-IN",
            model="bulbul:v3",
            speaker="rahul",
            pace=speed,
            **({"http_session": http_session} if http_session is not None else {}),
        )

    raise ProviderError(
        f"unknown TTS provider {provider!r}; known: {sorted(TTS_CREDENTIALS)}"
    )
