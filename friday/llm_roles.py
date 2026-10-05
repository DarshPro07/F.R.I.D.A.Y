"""Which model each LLM role resolves to, per backend - data only.

Kept apart from friday.providers, which imports the LiveKit plugins: a plugin
registers itself at import time and LiveKit allows that only on the main
thread. The model gateway reads this table while it routes, and the control
room's typed turn routes on a worker thread, so reaching it through providers
crashed /api/ask with "Plugins must be registered on the main thread"
(FRIDAY.exe, 2026-10-05). friday.providers re-exports it unchanged.
"""

from __future__ import annotations

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
