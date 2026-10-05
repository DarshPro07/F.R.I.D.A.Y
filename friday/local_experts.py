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

from friday import governor as G

logger = logging.getLogger(__name__)

LOADED = "LOADED"
UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True)
class Expert:
    """What the pool needs to know about one local model. `size_gb` is its RAM
    cost once loaded. `load()` returns the handle callers use and must release
    anything it took if it raises; `unload()` gives the memory back."""

    name: str
    size_gb: float
    load: Callable[[], object]
    unload: Callable[[], None]
    kind: str = ""


@dataclass(frozen=True)
class Outcome:
    name: str
    status: str
    reason: str
    evicted: tuple[str, ...] = ()
    handle: object = None

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

    def __init__(self, gov: G.Governor | None = None) -> None:
        self.gov = gov or G.governor()
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

    # -- registration -------------------------------------------------------

    def register(self, name: str, size_gb: float, load: Callable[[], object],
                 unload: Callable[[], None], *, kind: str = "") -> None:
        if not name or size_gb <= 0 or not callable(load) or not callable(unload):
            raise ValueError("an expert needs a name, a positive size_gb, and load/unload callables")
        with self._lock:
            if name in self._resident:
                raise ValueError(f"{name!r} is resident; unload it before registering it again")
            self._experts[name] = Expert(name, float(size_gb), load, unload, kind)

    # -- reading ------------------------------------------------------------

    def resident(self) -> list[dict]:
        """Resident experts, least recently used first."""
        with self._lock:
            return [{"name": n, "kind": self._experts[n].kind, "size_gb": self._experts[n].size_gb,
                     "lease": r.lease, "loaded_at": r.loaded_at, "last_used": r.last_used}
                    for n, r in sorted(self._resident.items(), key=lambda kv: kv[1].order)]

    def resident_gb(self) -> float:
        with self._lock:
            return sum(self._experts[n].size_gb for n in self._resident)

    def status(self) -> dict:
        with self._lock:
            return {"registered": sorted(self._experts), "resident": self.resident(),
                    "resident_gb": self.resident_gb(), "unload_failures": list(self.failures)}

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
                return Outcome(name, LOADED, "already resident", handle=self._resident[name].handle)
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
        their leases first; this covers a lease it could not stop). Returns
        the names unloaded."""
        if not self._lock.acquire(blocking=False):
            return []                       # a load is running; the next reading will do
        try:
            if self._busy or not self._resident:
                return []
            if p.level in (G.HIGH, G.CRITICAL):
                victims = [n for n, _ in self._lru()]
            elif p.level == G.ELEVATED and len(self._resident) > 1:
                victims = [self._lru()[0][0]]
            else:
                return []
            return [n for n in victims if self._evict(n, f"{p.level} pressure")]
        finally:
            self._lock.release()

    # -- internals ----------------------------------------------------------

    def _lru(self) -> list[tuple[str, _Resident]]:
        return sorted(self._resident.items(), key=lambda kv: kv[1].order)

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
        p = gov.pressure(fresh=True)         # runs the governor's own reductions first
        if p.level in (G.HIGH, G.CRITICAL):
            return Outcome(expert.name, UNAVAILABLE,
                           f"{p.level} pressure: " + (", ".join(p.reasons) or "machine under strain"))
        if p.sample is None or not p.sample.total_gb():
            return Outcome(expert.name, UNAVAILABLE, "no reading of the machine's memory to judge by")
        evicted: list[str] = []
        freed = 0.0
        stuck: set[str] = set()              # an unload that failed freed nothing
        while True:
            projected = self._project(p, freed, expert.size_gb)
            # NORMAL fits. ELEVATED fits only as the one resident expert; HIGH
            # and CRITICAL never do, the governor would evict the expert at once.
            fits = projected.level == G.NORMAL or (projected.level == G.ELEVATED
                                                   and not self._resident)
            full = gov.active(G.MODEL) >= gov.thresholds.max_resident_models
            if fits and not full:
                break
            victims = [n for n, _ in self._lru() if n not in stuck]
            if not victims:
                why = (f"resident model cap {gov.thresholds.max_resident_models} reached"
                       if fits else f"would read {projected.level}: " + ", ".join(projected.reasons))
                return Outcome(expert.name, UNAVAILABLE, why, tuple(evicted))
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
            return Outcome(expert.name, UNAVAILABLE, f"governor {decision.decision}: {decision.reason}",
                           tuple(evicted))
        try:
            handle = expert.load()
        except Exception as exc:  # noqa: BLE001 - reported as UNAVAILABLE with the cause; the lease is returned
            logger.warning("load of %s failed: %s", expert.name, exc)
            gov.release(decision.lease, retire_queue=False)
            return Outcome(expert.name, UNAVAILABLE, f"load failed: {exc}", tuple(evicted))
        self._order += 1
        now = time.time()
        self._resident[expert.name] = _Resident(decision.lease, handle, now, now, self._order)
        return Outcome(expert.name, LOADED, "loaded", tuple(evicted), handle)
