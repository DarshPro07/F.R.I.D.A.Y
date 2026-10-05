"""The local model policy (PRD v7 section 29, MODEL-007; Amendment A3's 2 GB).

What a local model may be, as numbers in one place:

- an artifact in the default pack is at most `ARTIFACT_CAP_GB`; a larger one is
  onboarded only on an owner override, and the override is recorded with its reason;
- at most `max_resident_general` general model is resident, and a vision model
  is loaded on demand and let go when idle;
- context defaults to 4K and never exceeds 8K; it is the first thing given up
  and the last thing bought back, so a larger model is considered only with
  context at the floor.

The policy only judges and computes. `local_experts.ExpertPool` enforces it at
registration and at load, reading the device's own memory through the governor;
`hardware_profile.derive_policy` takes the default context from the measured room.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

#: Amendment A3: no artifact in the default pack is larger than this.
ARTIFACT_CAP_GB = 2.0

GENERAL = "general"
VISION = "vision"
EMBEDDING = "embedding"
#: Kinds loaded for the call and unloaded when idle, not kept resident.
ON_DEMAND_KINDS = (VISION,)

MIN_CTX = 4096
DEFAULT_CTX = 4096
MAX_CTX = 8192
CTX_STEP = 1024
#: KV cache cost per 1K tokens of context, for planning where a model does not
#: say: about a 3B-parameter model's fp16 cache, rounded up.
KV_GB_PER_1K = 0.10
#: A vision model idle this long is unloaded.
ON_DEMAND_IDLE_S = 60.0


@dataclass(frozen=True)
class Verdict:
    """The outcome of onboarding one artifact. `override` is true when it was
    accepted only because the owner overrode the cap; `reason` says so."""

    name: str
    accepted: bool
    reason: str
    size_gb: float
    cap_gb: float
    override: bool = False

    def to_dict(self) -> dict:
        return {"name": self.name, "accepted": self.accepted, "reason": self.reason,
                "size_gb": self.size_gb, "cap_gb": self.cap_gb, "override": self.override}


@dataclass(frozen=True)
class Candidate:
    """A model the device might run: its RAM once loaded and what one more 1K
    tokens of context costs it."""

    name: str
    size_gb: float
    kv_gb_per_1k: float = KV_GB_PER_1K


@dataclass(frozen=True)
class Choice:
    name: str
    size_gb: float
    ctx: int


@dataclass(frozen=True)
class LocalModelPolicy:
    artifact_cap_gb: float = ARTIFACT_CAP_GB
    max_resident_general: int = 1
    default_ctx: int = DEFAULT_CTX
    min_ctx: int = MIN_CTX
    max_ctx: int = MAX_CTX
    on_demand_idle_s: float = ON_DEMAND_IDLE_S

    def __post_init__(self) -> None:
        if not (0 < self.artifact_cap_gb and self.max_resident_general >= 1
                and 0 < self.min_ctx <= self.default_ctx <= self.max_ctx):
            raise ValueError("a local model policy needs a positive cap, at least one resident "
                             "general model, and min_ctx <= default_ctx <= max_ctx")

    # -- onboarding ---------------------------------------------------------

    def check_onboarding(self, name: str, size_gb: float, kind: str = "",
                         owner_override: bool = False, override_reason: str = "") -> Verdict:
        """Accept or refuse one artifact for the default pack. `kind` is not
        judged: the cap holds for every kind."""
        cap = self.artifact_cap_gb
        if not isinstance(size_gb, (int, float)) or not math.isfinite(size_gb) or size_gb <= 0:
            return Verdict(name, False, f"{size_gb!r} is not a size in GB", 0.0, cap)
        size = float(size_gb)
        if size <= cap:
            return Verdict(name, True, f"{size:g} GB is within the {cap:g} GB default-pack cap", size, cap)
        over = f"{size:g} GB exceeds the {cap:g} GB default-pack cap"
        if owner_override:
            note = f": {override_reason.strip()}" if override_reason.strip() else ""
            return Verdict(name, True, f"{over}; accepted on owner override{note}", size, cap, True)
        return Verdict(name, False, f"{over}; it enters the default pack only on an owner override",
                       size, cap)

    # -- context ------------------------------------------------------------

    def context_for(self, requested: int | None = None) -> int:
        """The context a model runs with: the default when none is asked for,
        otherwise the request held to the policy's range."""
        if requested is None:
            return self.default_ctx
        return max(self.min_ctx, min(int(requested), self.max_ctx))

    def fit_context(self, *, size_gb: float, headroom_gb: float,
                    kv_gb_per_1k: float = KV_GB_PER_1K, requested: int | None = None) -> int | None:
        """The largest context, down from the request to the floor, whose KV
        cache fits in `headroom_gb` beside the model; None when even the floor
        does not. Context is reduced here, before any caller reaches for a
        larger model."""
        ctx = self.context_for(requested)
        while ctx >= self.min_ctx:
            if size_gb + kv_gb_per_1k * ctx / 1024 <= headroom_gb:
                return ctx
            ctx -= CTX_STEP
        return None

    def context_for_room(self, room_gb: float, model_gb: float) -> int:
        """The default context on a machine that can spend `room_gb`: as much
        as the room holds beside the model budget, never below the floor."""
        fit = self.fit_context(size_gb=model_gb, headroom_gb=room_gb, requested=self.max_ctx)
        return self.min_ctx if fit is None else fit

    def choose_model(self, candidates: Sequence[Candidate], headroom_gb: float,
                     requested: int | None = None) -> Choice | None:
        """The largest default-pack candidate that fits `headroom_gb` with
        context at the floor, then the context its remaining room buys back, up
        to the request. A larger model is raised only after context has been
        minimised, never the other way round. None when nothing fits."""
        fits = [c for c in candidates
                if c.size_gb <= self.artifact_cap_gb
                and self.fit_context(size_gb=c.size_gb, headroom_gb=headroom_gb,
                                     kv_gb_per_1k=c.kv_gb_per_1k, requested=self.min_ctx) is not None]
        if not fits:
            return None
        best = max(fits, key=lambda c: c.size_gb)
        ctx = self.fit_context(size_gb=best.size_gb, headroom_gb=headroom_gb,
                               kv_gb_per_1k=best.kv_gb_per_1k, requested=requested)
        return Choice(best.name, best.size_gb, ctx if ctx is not None else self.min_ctx)

    def to_dict(self) -> dict:
        return {"artifact_cap_gb": self.artifact_cap_gb, "max_resident_general": self.max_resident_general,
                "default_ctx": self.default_ctx, "max_ctx": self.max_ctx,
                "on_demand_kinds": list(ON_DEMAND_KINDS), "on_demand_idle_s": self.on_demand_idle_s}
