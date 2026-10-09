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

from friday import confirmation as CF
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

    def __init__(self, total_gb: float = TOTAL_GB, os_gb: float = OS_IDLE_GB) -> None:
        self.total_gb = total_gb
        self.os_gb = os_gb
        self.experts: dict[str, float] = {}
        self.at = 1000.0
        self.ooms = 0
        self.loads: list[str] = []
        self.unloads: list[str] = []
        self.events: list[str] = []             # "+name" / "-name" in the order they happened

    def expert_gb(self) -> float:
        return sum(self.experts.values())

    def free_gb(self) -> float:
        return self.total_gb - self.os_gb - self.expert_gb()

    def advance(self, seconds: float) -> None:
        self.at += seconds

    def __call__(self) -> G.Sample:
        self.at += 1.0
        free = self.free_gb()
        return G.Sample(at=self.at, cpu_percent=10.0, ram_percent=100.0 * (1.0 - free / self.total_gb),
                        ram_available_gb=free, disk_free_gb=100.0, browser_processes=0,
                        friday_rss_mb=200.0, ram_total_gb=self.total_gb)

    def loader(self, name: str, gb: float):
        def load():
            if self.free_gb() < gb:             # the pool must never let this happen
                self.ooms += 1
                raise MemoryError(f"{name}: {gb} GB wanted, {self.free_gb():.2f} GB free")
            self.experts[name] = gb
            self.loads.append(name)
            self.events.append(f"+{name}")
            return f"handle:{name}"
        return load

    def unloader(self, name: str):
        def unload():
            del self.experts[name]
            self.unloads.append(name)
            self.events.append(f"-{name}")
        return unload


def approved_override(pool: L.ExpertPool, name: str, size_gb: float, reason: str) -> str:
    """The owner's yes, as the person gives it: asked through the pool, answered on the book."""
    asked = pool.request_override(name, size_gb, reason)
    assert pool.book.approve(asked.nonce, answered_by="owner").ok
    return asked.nonce


def build(*, cap: int = 3, trend: bool = False, names=tuple(SIZES), total_gb: float = TOTAL_GB,
          os_gb: float = OS_IDLE_GB, sizes: dict[str, float] | None = None,
          kinds: dict[str, str] | None = None):
    """A machine, its governor, and a pool with the named experts registered.
    The trend is off for the scenarios that judge the level by headroom alone;
    the random-use stress test keeps it on. The governor's baseline is the
    machine's own idle headroom, so a 16 GB machine is judged by its own room.
    An expert over the policy's artifact cap (the 3 and 4 GB ones) is registered
    on an owner override, as the cap would otherwise refuse it."""
    sizes = sizes or SIZES
    m = Machine(total_gb, os_gb)
    extra = {} if trend else {"trend_min_samples": 10_000}
    baseline = G.Baseline(ram_available_gb=total_gb - os_gb, cpu_percent=10.0,
                          readings=12, provisional=False)
    gov = G.Governor(thresholds=G.Thresholds(max_resident_models=cap, **extra),
                     sampler=m, sample_ttl_s=0.0, baseline=baseline)
    pool = L.ExpertPool(gov, book=CF.Book())
    for n in names:
        nonce = (approved_override(pool, n, sizes[n], "test fixture: a heavy expert")
                 if sizes[n] > pool.policy.artifact_cap_gb else None)
        pool.register(n, sizes[n], m.loader(n, sizes[n]), m.unloader(n),
                      kind=(kinds or {}).get(n, "test"), override_nonce=nonce,
                      override_reason="test fixture: a heavy expert")
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


# -- the local model policy (MODEL-007) ---------------------------------------

GENERAL_PACK = {"reranker": 0.4, "gen": 1.0, "vision": 2.0}
GENERAL_KINDS = {"reranker": "embedding", "gen": "general", "vision": "vision"}


def test_a_3_7_gb_artifact_is_refused_for_the_default_pack_and_accepted_only_on_owner_override():
    m, gov, pool = build(names=(), total_gb=16.0, os_gb=4.0)
    with pytest.raises(L.OnboardingRefused) as refused:
        pool.register("big", 3.7, m.loader("big", 3.7), m.unloader("big"), kind="general")
    assert "3.7 GB" in str(refused.value) and "owner override" in str(refused.value)
    assert refused.value.verdict.accepted is False
    assert pool.status()["registered"] == [] and pool.load("big").status == L.UNAVAILABLE

    verdict = pool.register("big", 3.7, m.loader("big", 3.7), m.unloader("big"), kind="general",
                            override_nonce=approved_override(pool, "big", 3.7, "owner wants the 3.7 GB model"),
                            override_reason="owner wants the 3.7 GB model")
    assert verdict.accepted and verdict.override
    assert "owner override" in verdict.reason and "owner wants the 3.7 GB model" in verdict.reason
    status = pool.status()
    assert status["registered"] == ["big"]
    assert [(o["name"], o["size_gb"], o["cap_gb"]) for o in status["overrides"]] == [("big", 3.7, 2.0)]
    assert "owner wants the 3.7 GB model" in status["overrides"][0]["reason"]
    out = pool.load("big")                                   # the override shows on the outcome too
    assert out.loaded and "owner wants the 3.7 GB model" in out.override
    assert m.ooms == 0


# -- the owner override is a confirmation (MODEL-007) ------------------------


def _over_cap(pool, m, name="big", size=3.7, **kw):
    return pool.register(name, size, m.loader(name, size), m.unloader(name), kind="general", **kw)


def test_an_unconfirmed_over_cap_registration_is_refused_whatever_the_caller_claims():
    m, _, pool = build(names=())
    with pytest.raises(TypeError):                 # the bare owner_override flag no longer exists
        _over_cap(pool, m, owner_override=True)
    with pytest.raises(L.OnboardingRefused):       # no nonce at all
        _over_cap(pool, m)
    with pytest.raises(L.OnboardingRefused) as bogus:
        _over_cap(pool, m, override_nonce="made-up")
    assert "made-up" in str(bogus.value) and pool.status()["registered"] == []
    asked = pool.request_override("big", 3.7, "wants it")          # asked, not yet answered
    with pytest.raises(L.OnboardingRefused) as waiting:
        _over_cap(pool, m, override_nonce=asked.nonce)
    assert "not been approved" in str(waiting.value) and pool.status()["registered"] == []


def test_a_refused_confirmation_never_onboards_the_artifact():
    m, _, pool = build(names=())
    asked = pool.request_override("big", 3.7, "wants it")
    pool.book.refuse(asked.nonce)
    with pytest.raises(L.OnboardingRefused):
        _over_cap(pool, m, override_nonce=asked.nonce)
    assert pool.status()["registered"] == []


def test_the_owners_yes_covers_one_artifact_one_size_and_one_use():
    m, _, pool = build(names=())
    nonce = approved_override(pool, "big", 3.7, "wants it")
    with pytest.raises(L.OnboardingRefused):                       # another model's name
        _over_cap(pool, m, name="other", override_nonce=nonce)
    with pytest.raises(L.OnboardingRefused):                       # the same name at a bigger size
        _over_cap(pool, m, size=5.0, override_nonce=nonce)
    # the two refusals above did not spend it
    assert _over_cap(pool, m, override_nonce=nonce, override_reason="wants it").override
    with pytest.raises(L.OnboardingRefused) as reused:             # spent: a yes is used once
        _over_cap(pool, m, override_nonce=nonce)
    assert "already been used" in str(reused.value)
    assert pool.status()["registered"] == ["big"]


def test_a_nonce_is_not_asked_for_or_spent_by_an_artifact_within_the_cap():
    m, _, pool = build(names=())
    nonce = approved_override(pool, "big", 3.7, "wants it")
    pool.register("small", 1.0, m.loader("small", 1.0), m.unloader("small"), override_nonce=nonce)
    assert pool.overrides() == []
    assert pool.book.pending[nonce].state == CF.APPROVED           # untouched, still the owner's to use
    with pytest.raises(ValueError):
        pool.request_override("small", 1.0, "no need")             # nothing to override


def test_the_pool_uses_the_process_confirmation_book_unless_given_one():
    assert L.ExpertPool(book=CF.Book()).book is not CF.book
    pool = L.ExpertPool()
    try:
        assert pool.book is CF.book
    finally:
        pool.close()


def test_the_artifact_cap_is_inclusive_and_a_size_that_is_not_a_number_is_refused():
    m, _, pool = build(names=())
    pool.register("edge", 2.0, m.loader("edge", 2.0), m.unloader("edge"))
    assert pool.overrides() == []                           # within the cap: no override recorded
    for size in (2.01, float("inf"), float("nan")):
        with pytest.raises(L.OnboardingRefused):
            pool.register("bad", size, m.loader("bad", 1.0), m.unloader("bad"))


def _general_then_vision(total_gb: float, os_gb: float):
    m, gov, pool = build(names=tuple(GENERAL_PACK), total_gb=total_gb, os_gb=os_gb,
                         sizes=GENERAL_PACK, kinds=GENERAL_KINDS)
    assert pool.load("reranker").loaded and pool.load("gen").loaded
    return m, gov, pool, pool.load("vision")


def test_a_vision_load_unloads_the_resident_general_model_first_when_memory_requires():
    m, gov, pool, vision = _general_then_vision(8.0, 2.4)
    assert vision.loaded and m.ooms == 0
    # The general model is the more recently used, so plain LRU would have gone
    # to the reranker first; the policy takes the general model's memory first.
    assert vision.evicted == ("gen", "reranker")
    assert m.events == ["+reranker", "+gen", "-gen", "-reranker", "+vision"]
    assert names(pool) == ["vision"] and gov.active(G.MODEL) == 1          # leases released with the unloads


def test_a_vision_load_leaves_the_general_model_alone_when_memory_does_not_require_it():
    m, _, pool, vision = _general_then_vision(16.0, 4.0)
    assert vision.loaded and vision.evicted == ()
    assert names(pool) == ["reranker", "gen", "vision"]


def test_residency_follows_the_actual_devices_memory_not_a_reference_profile():
    """The same pool code, the same experts, two machines: only the sampler's
    total and available RAM differ, and the load and evict decisions with them."""
    small = _general_then_vision(8.0, 2.4)
    large = _general_then_vision(16.0, 4.0)
    assert small[3].evicted != large[3].evicted
    assert (names(small[2]), names(large[2])) == (["vision"], ["reranker", "gen", "vision"])
    assert small[0].ooms == large[0].ooms == 0


def test_only_one_general_model_is_resident_a_second_swaps_the_first_out():
    sizes = {"g1": 1.0, "g2": 1.5, "emb": 0.4}
    m, gov, pool = build(names=tuple(sizes), total_gb=16.0, os_gb=4.0, sizes=sizes,
                         kinds={"g1": "general", "g2": "general", "emb": "embedding"})
    assert pool.load("emb").loaded and pool.load("g1").loaded
    swap = pool.load("g2")                                   # plenty of room: the cap, not memory, decides
    assert swap.loaded and swap.evicted == ("g1",)
    assert names(pool) == ["emb", "g2"] and m.events[-2:] == ["-g1", "+g2"]
    assert gov.active(G.MODEL) == 2


def test_a_vision_expert_is_not_kept_resident_when_idle():
    m, gov, pool = build(names=("vision",), sizes=GENERAL_PACK, kinds=GENERAL_KINDS,
                         total_gb=16.0, os_gb=4.0)
    assert pool.load("vision").loaded
    used = pool.resident()[0]["last_used"]
    idle = pool.policy.on_demand_idle_s
    assert pool.reap_idle(now=used + idle - 1) == [] and names(pool) == ["vision"]
    assert pool.reap_idle(now=used + idle) == ["vision"]
    assert names(pool) == [] and m.experts == {} and gov.active(G.MODEL) == 0
    # The governor's own reading reaps it too, at NORMAL, without the pool being asked.
    assert pool.load("vision").loaded
    pool._resident["vision"].last_used -= idle + 1
    gov.pressure(fresh=True)                                 # the governor's reading calls reconcile
    assert names(pool) == [] and m.experts == {} and gov.active(G.MODEL) == 0


def test_a_general_model_is_not_reaped_for_being_idle():
    m, _, pool = build(names=("gen",), sizes=GENERAL_PACK, kinds=GENERAL_KINDS, total_gb=16.0, os_gb=4.0)
    assert pool.load("gen").loaded
    assert pool.reap_idle(now=pool.resident()[0]["last_used"] + 10_000) == [] and names(pool) == ["gen"]


def test_context_defaults_to_4k_and_is_held_between_4k_and_8k():
    m, _, pool = build(names=(), total_gb=16.0, os_gb=4.0)
    for ctx in (None, 6144, 32768, 512):
        pool.register(f"e{ctx}", 1.0, m.loader(f"e{ctx}", 1.0), m.unloader(f"e{ctx}"), ctx=ctx)
    got = {n: pool.load(n).ctx for n in ("eNone", "e6144", "e32768", "e512")}
    assert got == {"eNone": 4096, "e6144": 6144, "e32768": 8192, "e512": 4096}


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
