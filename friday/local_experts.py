"""Hardware-aware residency for local expert models (PRD v7 section 29, MODEL-005).

On a small machine the heavier experts (a Needle model, an embedding model, a
vision model) load on demand and unload again. This pool keeps the loaded ones
resident between calls and decides, from the governor's own reading of the
machine, whether the next one may load.

It is not a second registry of what is running. Every resident expert holds a
`governor.MODEL` lease whose cleanup is the expert's unload, so the governor
still counts it, caps it and evicts it (all of them, at HIGH and CRITICAL). What
the governor does not do, and this pool does:

- judge a load by its size: the reading is projected as if the expert were
  already loaded and judged by `governor.assess` against the machine's own
  baseline. A load that would put the machine at HIGH is refused, because the
  governor would evict it at the next reading;
- evict the least recently used expert, not every model, to make room;
- act at ELEVATED: trim down to the one most recently used expert.

The local model policy (MODEL-007, `local_model_policy`) is enforced here too:
an artifact over the default-pack cap is refused at registration unless the
owner overrides it, at most one general model is resident (a second one swaps
the first out), a vision model evicts a resident general model before any
other, and a vision model is unloaded once it has sat idle.

A load that cannot be made is reported as UNAVAILABLE with the reason, never
raised: an absent local expert is a route that fails over, not a crash.
"""
from __future__ import annotations

import dataclasses
import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable

from friday import confirmation
from friday import governor as G
from friday import local_model_policy as LMP

logger = logging.getLogger(__name__)

#: The action an owner's yes is bound to: onboarding one named artifact over
#: the default-pack cap, at one size (`confirmation.fingerprint`).
OVERRIDE_ACTION = "local_model.onboard_over_cap"
OVERRIDE_RUN = "local-experts"

LOADED = "LOADED"
UNAVAILABLE = "UNAVAILABLE"


class OnboardingRefused(ValueError):
    """The policy refused an artifact; `verdict` says why."""

    def __init__(self, verdict: LMP.Verdict) -> None:
        super().__init__(verdict.reason)
        self.verdict = verdict


@dataclass(frozen=True)
class Expert:
    """What the pool needs to know about one local model. `size_gb` is its RAM
    cost once loaded. `load()` returns the handle callers use and must release
    anything it took if it raises; `unload()` gives the memory back. `ctx` is
    the context it is to run with, within the policy's 4K-8K; `override` is
    the reason it was onboarded over the artifact cap, empty otherwise."""

    name: str
    size_gb: float
    load: Callable[[], object]
    unload: Callable[[], None]
    kind: str = ""
    ctx: int = LMP.DEFAULT_CTX
    override: str = ""


@dataclass(frozen=True)
class Outcome:
    name: str
    status: str
    reason: str
    evicted: tuple[str, ...] = ()
    handle: object = None
    ctx: int = 0
    override: str = ""

    @property
    def loaded(self) -> bool:
        return self.status == LOADED


@dataclass
class _Resident:
    lease: str
    handle: object
    loaded_at: float
    last_used: float
    order: int          # LRU order is a counter: clock ticks tie, counters do not


class ExpertPool:
    """Resident local experts, bounded by the machine's headroom.

    One lock covers the pool, held across a load so the governor's eviction of
    an expert that is still loading waits for it to finish and then unloads it.
    The governor calls `reconcile` from its pressure reading and skips the
    reading, not the caller, when the pool is busy."""

    def __init__(self, gov: G.Governor | None = None, policy: LMP.LocalModelPolicy | None = None,
                 book=None) -> None:
        self.gov = gov or G.governor()
        self.policy = policy or LMP.LocalModelPolicy()
        self._book = book
        self._experts: dict[str, Expert] = {}
        self._resident: dict[str, _Resident] = {}
        self._lock = threading.RLock()
        self._order = 0
        self._busy = False
        #: Unloads that raised: the expert is still accounted for, and still resident.
        self.failures: list[dict] = []
        self.gov.add_observer(self.reconcile)

    def close(self) -> None:
        """Stop acting on the governor's readings; resident experts stay."""
        self.gov.remove_observer(self.reconcile)

    @property
    def book(self):
        """The confirmation book the owner's override is asked and spent on:
        the process-wide one unless the pool was given its own."""
        return self._book if self._book is not None else confirmation.book

    # -- registration -------------------------------------------------------

    def request_override(self, name: str, size_gb: float, reason: str = "") -> confirmation.Confirmation:
        """Ask the owner to let `name` into the pool over the artifact cap. The
        returned confirmation is answered by the person on the book; its nonce
        goes to `register(override_nonce=...)`, which spends it for this
        artifact at this size only."""
        verdict = self.policy.check_onboarding(name, size_gb)
        if verdict.accepted or verdict.size_gb <= 0:
            raise ValueError(f"{name!r} needs no owner override: {verdict.reason}")
        return self.book.ask(
            OVERRIDE_RUN, OVERRIDE_ACTION, name,
            f"Onboard {name} ({verdict.size_gb:g} GB) over the {verdict.cap_gb:g} GB default-pack cap?"
            + (f" Reason: {reason.strip()}" if reason.strip() else ""),
            arguments={"size_gb": verdict.size_gb})

    def register(self, name: str, size_gb: float, load: Callable[[], object],
                 unload: Callable[[], None], *, kind: str = "", ctx: int | None = None,
                 override_nonce: str | None = None, override_reason: str = "") -> LMP.Verdict:
        """Onboard one expert. An artifact over the policy's cap raises
        `OnboardingRefused` unless `override_nonce` is the owner's approved
        confirmation for this artifact (`request_override`); it is spent here.
        The override and its reason are kept, and shown by `status()` and the
        load `Outcome`."""
        if not name or size_gb <= 0 or not callable(load) or not callable(unload):
            raise ValueError("an expert needs a name, a positive size_gb, and load/unload callables")
        kind = (kind or "").strip().lower()
        verdict = self.policy.check_onboarding(name, size_gb, kind)
        if not verdict.accepted and verdict.size_gb > 0 and override_nonce:
            spent = self.book.consume(override_nonce, run_id=OVERRIDE_RUN, action=OVERRIDE_ACTION,
                                      target=name, arguments={"size_gb": verdict.size_gb})
            if spent.ok:
                verdict = self.policy.check_onboarding(name, size_gb, kind, True, override_reason)
            else:
                verdict = dataclasses.replace(
                    verdict, reason=f"{verdict.reason}; the override was not applied: {spent.reason}")
        if not verdict.accepted:
            logger.warning("onboarding of %s refused: %s", name, verdict.reason)
            raise OnboardingRefused(verdict)
        with self._lock:
            if name in self._resident:
                raise ValueError(f"{name!r} is resident; unload it before registering it again")
            self._experts[name] = Expert(name, float(size_gb), load, unload, kind,
                                         self.policy.context_for(ctx),
                                         verdict.reason if verdict.override else "")
        return verdict

    # -- reading ------------------------------------------------------------

    def resident(self) -> list[dict]:
        """Resident experts, least recently used first."""
        with self._lock:
            return [{"name": n, "kind": self._experts[n].kind, "size_gb": self._experts[n].size_gb,
                     "ctx": self._experts[n].ctx, "lease": r.lease,
                     "loaded_at": r.loaded_at, "last_used": r.last_used}
                    for n, r in sorted(self._resident.items(), key=lambda kv: kv[1].order)]

    def resident_gb(self) -> float:
        with self._lock:
            return sum(self._experts[n].size_gb for n in self._resident)

    def overrides(self) -> list[dict]:
        """Experts onboarded over the artifact cap, with the owner's reason."""
        with self._lock:
            return [{"name": e.name, "size_gb": e.size_gb, "cap_gb": self.policy.artifact_cap_gb,
                     "reason": e.override} for _, e in sorted(self._experts.items()) if e.override]

    def status(self) -> dict:
        with self._lock:
            return {"registered": sorted(self._experts), "resident": self.resident(),
                    "resident_gb": self.resident_gb(), "unload_failures": list(self.failures),
                    "overrides": self.overrides(), "policy": self.policy.to_dict()}

    # -- use ----------------------------------------------------------------

    def touch(self, name: str) -> bool:
        """Mark an expert as just used. False when it is not resident."""
        with self._lock:
            r = self._resident.get(name)
            if r is None:
                return False
            self._order += 1
            r.order, r.last_used = self._order, time.time()
            return True

    def load(self, name: str) -> Outcome:
        """Make `name` resident, evicting the least recently used experts as
        far as the machine needs, or say why it cannot be."""
        with self._lock:
            expert = self._experts.get(name)
            if expert is None:
                return Outcome(name, UNAVAILABLE, "not a registered expert")
            if name in self._resident:
                self.touch(name)
                return Outcome(name, LOADED, "already resident", handle=self._resident[name].handle,
                               ctx=expert.ctx, override=expert.override)
            self._busy = True
            try:
                return self._load(expert)
            finally:
                self._busy = False

    def unload(self, name: str) -> bool:
        """Unload one expert and release its lease. False when it was not
        resident or its unload failed (it is then still resident)."""
        with self._lock:
            return name in self._resident and self._evict(name, "unloaded on request")

    def reconcile(self, p: G.Pressure) -> list[str]:
        """Act on a pressure reading; the governor calls this after each fresh
        one. ELEVATED: unload the least recently used expert while more than
        one is resident, one per reading, so the next reading shows what that
        freed. HIGH and CRITICAL: unload all (the governor evicts them through
        their leases first; this covers a lease it could not stop). At every
        level an on-demand expert that has sat idle is unloaded first. Returns
        the names unloaded."""
        if not self._lock.acquire(blocking=False):
            return []                       # a load is running; the next reading will do
        try:
            if self._busy or not self._resident:
                return []
            gone = self._reap_idle(time.time())
            if p.level in (G.HIGH, G.CRITICAL):
                victims = [n for n, _ in self._lru()]
            elif p.level == G.ELEVATED and len(self._resident) > 1:
                victims = [self._lru()[0][0]]
            else:
                return gone
            return gone + [n for n in victims if self._evict(n, f"{p.level} pressure")]
        finally:
            self._lock.release()

    def reap_idle(self, now: float | None = None) -> list[str]:
        """Unload every on-demand expert (vision) idle for the policy's
        `on_demand_idle_s`. Returns the names unloaded."""
        with self._lock:
            return self._reap_idle(time.time() if now is None else now)

    # -- internals ----------------------------------------------------------

    def _lru(self) -> list[tuple[str, _Resident]]:
        return sorted(self._resident.items(), key=lambda kv: kv[1].order)

    def _reap_idle(self, now: float) -> list[str]:
        idle = [n for n, r in self._lru()
                if self._experts[n].kind in LMP.ON_DEMAND_KINDS
                and now - r.last_used >= self.policy.on_demand_idle_s]
        return [n for n in idle if self._evict(n, "idle on-demand expert")]

    def _general(self) -> list[str]:
        """Resident general models, least recently used first."""
        return [n for n, _ in self._lru() if self._experts[n].kind == LMP.GENERAL]

    def _victims(self, expert: Expert, stuck: set[str]) -> list[str]:
        """Residents to evict for `expert`, best victim first: least recently
        used, except that an on-demand expert (vision) takes a resident general
        model's memory before anything else's."""
        names = [n for n, _ in self._lru() if n not in stuck]
        if expert.kind in LMP.ON_DEMAND_KINDS:
            names.sort(key=lambda n: self._experts[n].kind != LMP.GENERAL)   # stable: LRU within each
        return names

    def _unload_now(self, name: str) -> None:
        """Run the expert's unload and forget it. Raises when the unload does,
        leaving the expert resident: its memory was not given back."""
        self._experts[name].unload()
        self._resident.pop(name, None)

    def _governor_cleanup(self, name: str) -> None:
        """The lease's cleanup. The governor releases the lease once this
        returns, and records a failure (and keeps the lease) when it raises."""
        with self._lock:
            if name in self._resident:
                self._unload_now(name)

    def _evict(self, name: str, why: str) -> bool:
        r = self._resident[name]
        try:
            self._unload_now(name)
        except Exception as exc:  # noqa: BLE001 - recorded in `failures` and logged; the expert stays accounted for
            logger.warning("unload of %s failed: %s", name, exc)
            self.failures.append({"name": name, "reason": why, "error": str(exc), "at": time.time()})
            del self.failures[:-50]
            return False
        self.gov.release(r.lease, retire_queue=False)
        return True

    def _project(self, p: G.Pressure, freed_gb: float, size_gb: float) -> G.Pressure:
        """The level the machine would read at if this expert were loaded now,
        counting the memory already given back by evictions since `p`."""
        s = p.sample
        total = s.total_gb()
        avail = s.ram_available_gb + freed_gb - size_gb
        commit = (None if s.commit_available_gb is None
                  else s.commit_available_gb + freed_gb - size_gb)
        projected = dataclasses.replace(
            s, ram_available_gb=avail, commit_available_gb=commit,
            ram_percent=100.0 * (1.0 - avail / total))
        return G.assess(projected, self.gov.thresholds, baseline=self.gov.baseline)

    def _load(self, expert: Expert) -> Outcome:
        gov = self.gov

        def out(status: str, reason: str, evicted=(), handle=None) -> Outcome:
            return Outcome(expert.name, status, reason, tuple(evicted), handle,
                           expert.ctx, expert.override)

        p = gov.pressure(fresh=True)         # runs the governor's own reductions first
        if p.level in (G.HIGH, G.CRITICAL):
            return out(UNAVAILABLE, f"{p.level} pressure: " + (", ".join(p.reasons) or "machine under strain"))
        if p.sample is None or not p.sample.total_gb():
            return out(UNAVAILABLE, "no reading of the machine's memory to judge by")
        evicted: list[str] = []
        freed = 0.0
        stuck: set[str] = set()              # an unload that failed freed nothing
        if expert.kind == LMP.GENERAL:
            # The policy's one resident general model: a new one swaps the old
            # one out whatever the machine's headroom, so a general model's
            # memory is never held twice.
            held = self._general()
            for victim in held[:max(0, len(held) - self.policy.max_resident_general + 1)]:
                size = self._experts[victim].size_gb
                if not self._evict(victim, f"one resident general model, for {expert.name}"):
                    return out(UNAVAILABLE, f"general model {victim} would not unload", evicted)
                evicted.append(victim)
                freed += size
        while True:
            projected = self._project(p, freed, expert.size_gb)
            # NORMAL fits. ELEVATED fits only as the one resident expert; HIGH
            # and CRITICAL never do, the governor would evict the expert at once.
            fits = projected.level == G.NORMAL or (projected.level == G.ELEVATED
                                                   and not self._resident)
            full = gov.active(G.MODEL) >= gov.thresholds.max_resident_models
            if fits and not full:
                break
            victims = self._victims(expert, stuck)
            if not victims:
                why = (f"resident model cap {gov.thresholds.max_resident_models} reached"
                       if fits else f"would read {projected.level}: " + ", ".join(projected.reasons))
                return out(UNAVAILABLE, why, evicted)
            victim = victims[0]
            size = self._experts[victim].size_gb
            if self._evict(victim, f"room for {expert.name}"):
                evicted.append(victim)
                freed += size
            else:
                stuck.add(victim)
        decision = gov.admit(G.MODEL, label=expert.name[:60], owner="local_experts",
                             cleanup=lambda name=expert.name: self._governor_cleanup(name))
        if not decision.admitted:
            return out(UNAVAILABLE, f"governor {decision.decision}: {decision.reason}", evicted)
        try:
            handle = expert.load()
        except Exception as exc:  # noqa: BLE001 - reported as UNAVAILABLE with the cause; the lease is returned
            logger.warning("load of %s failed: %s", expert.name, exc)
            gov.release(decision.lease, retire_queue=False)
            return out(UNAVAILABLE, f"load failed: {exc}", evicted)
        self._order += 1
        now = time.time()
        self._resident[expert.name] = _Resident(decision.lease, handle, now, now, self._order)
        return out(LOADED, "loaded", evicted, handle)
