"""
A mode in which nothing leaves the machine, and an honest account of it.

    PRIVATE_ONLY means refused, never quietly rerouted.

The failure this exists to prevent is not a leak; it is a *silent* leak. A
mode that promises privacy and then falls back to a cloud model when the
local one cannot answer is worse than having no mode at all, because the
person stopped watching. So the contract is narrow and absolute: under
`PRIVATE_ONLY`, a request that cannot be served locally is declined, out
loud, with the reason.

The registry already knows which capabilities reach outside - every one
carries an `execution_scope`, and `network` is the one that matters. Of 127
capabilities, 13 are network-scoped, so the mode costs about a tenth of what
Friday can do rather than crippling it.

What it cannot currently promise is the harder half. Friday thinks with a
cloud model. Until a local one is installed, `PRIVATE_ONLY` keeps *data*
capabilities on the machine - no web search, no fetching, no cloud OCR - but
the conversation itself still goes to the model provider. That is a real hole
and `guarantee()` reports it in those words rather than letting the mode
imply something it has not earned. It is the same discipline
`Sandbox.strength()` follows: say which promise is actually being made.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

logger = logging.getLogger("friday-agent")

#: Everything is available. The default.
OPEN = "OPEN"

#: Nothing may leave this machine. Capabilities that reach the network are
#: refused rather than being attempted and failing, so the refusal is a
#: policy decision with a reason rather than a timeout.
PRIVATE_ONLY = "PRIVATE_ONLY"

MODES = (OPEN, PRIVATE_ONLY)

#: The scope that means "this goes outside".
NETWORK = "network"

#: Model providers that run on this machine. Checked by name because that is
#: what `providers.py` is configured with. Empty of cloud names on purpose -
#: a provider absent from this tuple is treated as remote, so a new cloud
#: backend is refused by default rather than allowed by omission.
LOCAL_BACKENDS = ("ollama", "llamacpp", "llama_cpp", "vllm", "localai",
                  "lmstudio", "local")

#: Where the mode is read from, so it survives a restart and can be set
#: without code. Checked at call time, not import time: the boss may turn it
#: on mid-session and the next request must honour it.
ENV_VAR = "FRIDAY_PRIVACY_MODE"


class Refused(PermissionError):
    """A request that privacy mode declined. Carries why, for the reply."""

    def __init__(self, capability: str, reason: str) -> None:
        super().__init__(reason)
        self.capability = capability
        self.reason = reason


def mode() -> str:
    """The mode in force right now."""
    raw = (os.getenv(ENV_VAR) or OPEN).strip().upper()
    return raw if raw in MODES else OPEN


def private() -> bool:
    return mode() == PRIVATE_ONLY


def refuses(capability, *, current: str | None = None) -> str:
    """
    Why this capability may not run, or "" if it may.

    `capability` is either a capability object with an `execution_scope`, or
    an id to look up. Accepting both because the runtime has the object and
    most callers have only the name.
    """
    if (current or mode()) != PRIVATE_ONLY:
        return ""

    scope = getattr(capability, "execution_scope", None)
    name = getattr(capability, "id", None) or str(capability)
    if scope is None:
        scope = _scope_of(name)
    if scope != NETWORK:
        return ""
    usable, total = available_under(PRIVATE_ONLY)
    return (f"{name} reaches the network, and privacy mode is PRIVATE_ONLY. "
            f"Nothing was sent. {usable} of {total} capabilities still work. "
            f"{guarantee().summary} Turn the mode off if you want this looked up "
            f"outside the machine.")


def _scope_of(capability_id: str) -> str | None:
    """The registry's own answer, or None when it has never heard of it."""
    try:
        from friday import capabilities as C

        found = C.CAPABILITIES.get(capability_id)
        return getattr(found, "execution_scope", None) if found else None
    except Exception:                                       # noqa: BLE001
        logger.exception("could not read the capability registry")
        return None


def guard(capability, *, current: str | None = None) -> None:
    """Raise if privacy mode forbids this. The one-liner for a call site."""
    reason = refuses(capability, current=current)
    if reason:
        name = getattr(capability, "id", None) or str(capability)
        logger.info("privacy.refused capability=%s", name)
        raise Refused(name, reason)


def backend_allowed(backend: str, *, current: str | None = None) -> bool:
    """
    Whether a model backend may be used under the current mode.

    Unknown backends are treated as remote. A cloud provider added later and
    forgotten here must fail closed, not open.
    """
    if (current or mode()) != PRIVATE_ONLY:
        return True
    return (backend or "").strip().lower() in LOCAL_BACKENDS


@dataclass(frozen=True)
class Guarantee:
    """What the current mode actually promises, given what is installed."""

    mode: str
    #: Network capabilities are refused.
    data_stays_local: bool
    #: The model doing the reasoning runs here too.
    thinking_stays_local: bool
    backend: str
    #: Plain words, for saying out loud.
    summary: str

    @property
    def complete(self) -> bool:
        return self.data_stays_local and self.thinking_stays_local


def guarantee(backend: str = "") -> Guarantee:
    """
    An honest account of what privacy mode is currently worth.

    Called before promising anything. A mode that says "private" while the
    conversation goes to a cloud model has told the boss something untrue,
    and he would have no way to find out.
    """
    backend = backend or os.getenv("LLM_BACKEND", "") or ""
    current = mode()

    if current != PRIVATE_ONLY:
        return Guarantee(
            mode=current, data_stays_local=False, thinking_stays_local=False,
            backend=backend,
            summary="Privacy mode is off. Requests may reach the network.")

    thinking_local = backend_allowed(backend, current=current)
    if thinking_local:
        summary = (f"PRIVATE_ONLY: network capabilities are refused and "
                   f"thinking runs on {backend}. Nothing leaves this machine.")
    else:
        summary = (
            f"PRIVATE_ONLY covers data, not thinking. Network capabilities "
            f"are refused, so nothing is looked up outside - but the model "
            f"answering is {backend or 'a cloud provider'}, so the "
            f"conversation itself still leaves the machine. Install a local "
            f"model to close that.")

    return Guarantee(mode=current, data_stays_local=True,
                     thinking_stays_local=thinking_local, backend=backend,
                     summary=summary)


def available_under(mode_name: str = "") -> tuple[int, int]:
    """
    How much of Friday still works. Returns (available, total).

    Useful for saying "114 of 127 still work" rather than leaving the boss to
    wonder whether the mode has switched everything off.
    """
    from friday import capabilities as C

    caps = list(C.CAPABILITIES.values())
    if (mode_name or mode()) != PRIVATE_ONLY:
        return len(caps), len(caps)
    usable = sum(1 for c in caps if c.execution_scope != NETWORK)
    return usable, len(caps)
