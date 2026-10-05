"""
Local expert residency on small hardware (PRD v7 section 29, MODEL-005).

The pool is proven against a simulated 8 GB machine: a sampler whose available
RAM is what the operating system, other programs and the loaded experts have
left of 8 GB, so a load really takes memory and an unload really gives it back.
The governor is the real one, built from injected Thresholds and a Baseline as
in tests/test_governor.py; only the model loaders are fakes (no model, no network).

On that machine the governor's lines fall at 2.24 GB (ELEVATED), 3.36 GB (HIGH)
and 4.48 GB (CRITICAL) of experts above an idle 5.6 GB available.
"""
from __future__ import annotations

import random

import pytest

from friday import governor as G
from friday import local_experts as L

TOTAL_GB = 8.0
OS_IDLE_GB = 2.4                               # the machine at rest: 5.6 GB available
BASELINE = G.Baseline(ram_available_gb=TOTAL_GB - OS_IDLE_GB, cpu_percent=10.0,
                      readings=12, provisional=False)

SIZES = {"needle": 1.0, "embedding": 0.6, "reranker": 0.4, "ocr": 0.8,
         "vision": 2.0, "heavy": 3.0, "huge": 4.0}


class Machine:
    """An 8 GB machine. Callable as the governor's sampler; the clock advances
    one second per reading so the governor's hysteresis can be exercised."""

    def __init__(self) -> None:
        self.os_gb = OS_IDLE_GB
        self.experts: dict[str, float] = {}
        self.at = 1000.0
        self.ooms = 0
        self.loads: list[str] = []
        self.unloads: list[str] = []

    def expert_gb(self) -> float:
        return sum(self.experts.values())

    def free_gb(self) -> float:
        return TOTAL_GB - self.os_gb - self.expert_gb()

    def advance(self, seconds: float) -> None:
        self.at += seconds

    def __call__(self) -> G.Sample:
        self.at += 1.0
        free = self.free_gb()
        return G.Sample(at=self.at, cpu_percent=10.0, ram_percent=100.0 * (1.0 - free / TOTAL_GB),
                        ram_available_gb=free, disk_free_gb=100.0, browser_processes=0,
                        friday_rss_mb=200.0, ram_total_gb=TOTAL_GB)

    def loader(self, name: str, gb: float):
        def load():
            if self.free_gb() < gb:             # the pool must never let this happen
                self.ooms += 1
                raise MemoryError(f"{name}: {gb} GB wanted, {self.free_gb():.2f} GB free")
            self.experts[name] = gb
            self.loads.append(name)
            return f"handle:{name}"
        return load

    def unloader(self, name: str):
        def unload():
            del self.experts[name]
            self.unloads.append(name)
        return unload


def build(*, cap: int = 3, trend: bool = False, names=tuple(SIZES)):
    """A machine, its governor, and a pool with the named experts registered.
    The trend is off for the scenarios that judge the level by headroom alone;
    the random-use stress test keeps it on."""
    m = Machine()
    extra = {} if trend else {"trend_min_samples": 10_000}
    gov = G.Governor(thresholds=G.Thresholds(max_resident_models=cap, **extra),
                     sampler=m, sample_ttl_s=0.0, baseline=BASELINE)
    pool = L.ExpertPool(gov)
    for n in names:
        pool.register(n, SIZES[n], m.loader(n, SIZES[n]), m.unloader(n), kind="test")
    return m, gov, pool


def names(pool: L.ExpertPool) -> list[str]:
    return [r["name"] for r in pool.resident()]


# -- admission by headroom -------------------------------------------------


def test_small_experts_load_side_by_side_inside_the_8gb_budget():
    m, gov, pool = build()
    a, b = pool.load("needle"), pool.load("embedding")
    assert a.loaded and b.loaded and a.handle == "handle:needle"
    assert names(pool) == ["needle", "embedding"]
    assert pool.resident_gb() == m.expert_gb() == pytest.approx(1.6)
    assert gov.active(G.MODEL) == 2 and gov.pressure(fresh=True).level == G.NORMAL


def test_a_resident_expert_is_not_loaded_twice_and_loading_it_counts_as_use():
    m, gov, pool = build()
    pool.load("needle")
    pool.load("embedding")
    again = pool.load("needle")
    assert again.loaded and again.reason == "already resident" and m.loads == ["needle", "embedding"]
    assert names(pool) == ["embedding", "needle"]       # needle is now the most recently used


def test_making_room_evicts_the_least_recently_used_expert_and_only_as_many_as_needed():
    m, gov, pool = build()
    for n in ("needle", "embedding", "reranker"):
        assert pool.load(n).loaded
    assert pool.touch("needle") and not pool.touch("ocr")
    assert names(pool) == ["embedding", "reranker", "needle"]
    out = pool.load("ocr")                              # 2.8 GB of experts reads ELEVATED
    assert out.loaded and out.evicted == ("embedding",)
    assert names(pool) == ["reranker", "needle", "ocr"]
    assert set(m.experts) == {"reranker", "needle", "ocr"} and m.unloads == ["embedding"]
    assert gov.active(G.MODEL) == 3


def test_a_heavy_expert_loads_alone_and_displaces_the_small_ones():
    m, gov, pool = build()
    pool.load("needle")
    pool.load("embedding")
    out = pool.load("heavy")                            # 3 GB reads ELEVATED: it may be the only one
    assert out.loaded and out.evicted == ("needle", "embedding")
    assert names(pool) == ["heavy"] and m.free_gb() == pytest.approx(2.6)
    assert gov.pressure(fresh=True).level == G.ELEVATED and names(pool) == ["heavy"]
    back = pool.load("needle")                          # heavy would read HIGH beside it
    assert back.loaded and back.evicted == ("heavy",) and set(m.experts) == {"needle"}


def test_an_expert_that_cannot_fit_even_alone_is_refused_before_its_loader_runs():
    m, gov, pool = build()
    out = pool.load("huge")                             # 4 GB leaves 1.6 GB: HIGH
    assert out.status == L.UNAVAILABLE and "HIGH" in out.reason
    assert m.loads == [] and gov.active(G.MODEL) == 0 and names(pool) == []


def test_an_unknown_expert_is_unavailable_not_an_error():
    _, _, pool = build()
    assert pool.load("nope").status == L.UNAVAILABLE


def test_the_governors_cap_is_honoured_by_evicting_the_least_recently_used():
    m, gov, pool = build(cap=1)
    assert pool.load("needle").loaded
    out = pool.load("embedding")
    assert out.loaded and out.evicted == ("needle",) and set(m.experts) == {"embedding"}
    assert gov.active(G.MODEL) == 1


def test_a_slot_held_by_something_else_is_not_taken():
    m, gov, pool = build(cap=1)
    held = gov.admit(G.MODEL, label="gateway call")     # not the pool's: it cannot be evicted
    out = pool.load("needle")
    assert out.status == L.UNAVAILABLE and "cap 1" in out.reason and m.loads == []
    assert gov.active(G.MODEL) == 1 and held.admitted


# -- pressure drives unloads ------------------------------------------------


def test_elevated_pressure_unloads_the_least_recently_used_down_to_one():
    m, gov, pool = build()
    pool.load("needle")
    pool.load("embedding")
    pool.touch("needle")
    m.os_gb += 1.0                                      # another program takes 1 GB: 3.0 GB left
    assert gov.pressure(fresh=True).level == G.ELEVATED
    assert names(pool) == ["needle"] and m.unloads == ["embedding"]
    assert gov.active(G.MODEL) == 1
    gov.pressure(fresh=True)
    assert names(pool) == ["needle"]                    # the last one is not trimmed


def test_high_pressure_evicts_every_expert_through_its_lease_and_refuses_loads():
    m, gov, pool = build()
    pool.load("needle")
    pool.load("embedding")
    m.os_gb += 2.0                                      # 2.0 GB left of a 5.6 GB idle: 64% used
    assert gov.pressure(fresh=True).level == G.HIGH
    assert names(pool) == [] and m.experts == {} and sorted(m.unloads) == ["embedding", "needle"]
    assert gov.active(G.MODEL) == 0 and gov.owned_processes() == []
    assert [r["released"] for r in gov.reductions] == [True, True]
    loads = len(m.loads)
    out = pool.load("reranker")
    assert out.status == L.UNAVAILABLE and "HIGH" in out.reason and len(m.loads) == loads


def test_critical_pressure_loads_nothing():
    m, gov, pool = build()
    pool.load("needle")
    m.os_gb = 7.0                                       # 1.0 GB left (the floor is 0.5)
    assert gov.pressure(fresh=True).level == G.CRITICAL and names(pool) == []
    out = pool.load("reranker")
    assert out.status == L.UNAVAILABLE and "CRITICAL" in out.reason
    assert m.experts == {} and gov.active(G.MODEL) == 0


def test_a_load_waits_out_the_governors_hold_after_pressure_eases():
    m, gov, pool = build()
    pool.load("needle")
    pool.load("embedding")
    m.os_gb += 2.0
    assert gov.pressure(fresh=True).level == G.HIGH
    m.os_gb = OS_IDLE_GB                                # the other program is gone
    assert pool.load("needle").status == L.UNAVAILABLE  # the governor holds HIGH for dwell_s
    m.advance(gov.thresholds.dwell_s + 1)
    gov.pressure(fresh=True)
    assert pool.load("needle").loaded


# -- every unload releases its lease ---------------------------------------


def test_every_unload_releases_its_lease():
    m, gov, pool = build()
    for n in ("needle", "embedding", "reranker"):
        pool.load(n)
    leases = {r["lease"] for r in pool.resident()}
    assert leases == {o["lease"] for o in gov.owned_processes()}
    assert pool.unload("embedding") and not pool.unload("embedding")
    assert gov.active(G.MODEL) == 2 and "embedding" not in m.experts
    for n in names(pool):
        pool.unload(n)
    assert gov.active(G.MODEL) == 0 and gov.owned_processes() == [] and m.experts == {}


def test_a_failed_load_reports_unavailable_and_holds_no_lease():
    m, gov, pool = build(names=())

    def broken():
        raise RuntimeError("weights missing")

    pool.register("needle", 1.0, broken, m.unloader("needle"))
    out = pool.load("needle")
    assert out.status == L.UNAVAILABLE and "weights missing" in out.reason
    assert gov.active(G.MODEL) == 0 and names(pool) == []


def test_an_unload_that_fails_keeps_the_expert_accounted_for():
    m, gov, pool = build(cap=1, names=())

    def stuck():
        raise RuntimeError("handle still in use")

    pool.register("needle", 1.0, m.loader("needle", 1.0), stuck)
    pool.register("embedding", 0.6, m.loader("embedding", 0.6), m.unloader("embedding"))
    assert pool.load("needle").loaded
    out = pool.load("embedding")                        # needs the slot; needle will not go
    assert out.status == L.UNAVAILABLE and "cap 1" in out.reason
    assert names(pool) == ["needle"] and gov.active(G.MODEL) == 1
    assert pool.status()["unload_failures"][0]["name"] == "needle"
    m.os_gb += 3.0                                      # HIGH: the governor tries, and reports it
    assert gov.pressure(fresh=True).level == G.HIGH
    assert names(pool) == ["needle"] and gov.reductions[-1]["released"] is False
    assert "handle still in use" in gov.reductions[-1]["reason"]


def test_registration_is_validated():
    _, _, pool = build(names=())
    for bad in (("", 1.0), ("x", 0.0), ("x", -1.0)):
        with pytest.raises(ValueError):
            pool.register(*bad, lambda: None, lambda: None)
    with pytest.raises(ValueError):
        pool.register("x", 1.0, None, lambda: None)


# -- the stress test ---------------------------------------------------------


def test_stress_random_use_on_8gb_never_overcommits_and_keeps_the_accounts_straight():
    """400 loads, touches, memory swings and idle waits on an 8 GB machine,
    with the governor's trend on. After every step the pool, the machine and
    the governor agree on what is resident; no loader was ever asked for memory
    that was not there; and every successful load left the machine below HIGH."""
    rng = random.Random(8)
    m, gov, pool = build(trend=True)
    experts = list(SIZES)
    high_line = BASELINE.ram_available_gb * (1.0 - gov.thresholds.ram_orange)
    loaded = refused = evictions = 0
    for _ in range(400):
        roll = rng.random()
        if roll < 0.55:
            out = pool.load(rng.choice(experts))
            if out.status == L.LOADED:
                loaded += 1
                evictions += len(out.evicted)
                assert m.free_gb() > high_line, "a load left the machine at HIGH"
            else:
                refused += 1
        elif roll < 0.75:
            pool.touch(rng.choice(experts))
        elif roll < 0.90:
            m.os_gb = OS_IDLE_GB + rng.choice([0.0, 0.0, 0.5, 1.0, 2.0, 4.5])
            gov.pressure(fresh=True)
        else:
            m.advance(rng.choice([5.0, 40.0]))
        assert m.ooms == 0
        assert set(names(pool)) == set(m.experts)
        assert pool.resident_gb() == pytest.approx(m.expert_gb())
        assert gov.active(G.MODEL) == len(m.experts) == len(gov.owned_processes())
        assert m.expert_gb() <= TOTAL_GB - OS_IDLE_GB
    assert loaded > 20 and refused > 5 and evictions > 5 and gov.reductions
    for n in names(pool):
        pool.unload(n)
    assert gov.active(G.MODEL) == 0 and gov.owned_processes() == [] and m.experts == {}
