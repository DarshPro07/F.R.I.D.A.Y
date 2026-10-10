"""MODEL-010: a preferred local model that cannot run degrades, visibly.

The pool is the real ExpertPool over the simulated 8 GB machine of
tests/test_local_experts.py. Only the loaders are fakes; a failing loader is an
injected one that raises, so the failure is a real load failure inside the pool.
"""
from __future__ import annotations

import pytest

from friday import governor as G
from friday import local_experts as L
from tests.test_local_experts import build


def failing(exc: Exception):
    def load():
        raise exc
    return load


def pool_with(*, fail: dict[str, Exception] | None = None, sizes: dict[str, float] | None = None):
    """needle (1.0 GB) and embedding (0.6 GB) load; names in `fail` raise on load."""
    sizes = sizes or {"big": 1.8, "mid": 1.0, "small": 0.6}
    m, gov, pool = build(names=(), sizes=sizes)
    for n, gb in sizes.items():
        load = failing(fail[n]) if fail and n in fail else m.loader(n, gb)
        pool.register(n, gb, load, m.unloader(n), kind="test")
    return m, gov, pool


def test_a_preferred_model_that_loads_serves_with_nothing_degraded():
    m, gov, pool = pool_with()
    res = pool.load_chain(["big", "mid", "small"])
    assert res.route == L.LOCAL and res.name == "big" and res.loaded and not res.degraded
    assert res.attempts == () and res.outcome.handle == "handle:big"
    assert pool.degrades == [] and m.loads == ["big"]


def test_a_load_failure_degrades_to_the_next_smaller_model_with_the_reason_recorded():
    m, gov, pool = pool_with(fail={"big": MemoryError("out of memory loading big")})
    res = pool.load_chain(["big", "mid", "small"])
    assert res.route == L.LOCAL and res.name == "mid" and res.degraded
    assert res.outcome.handle == "handle:mid"
    assert [(a.name, a.status) for a in res.attempts] == [("big", L.UNAVAILABLE)]
    assert "out of memory loading big" in res.attempts[0].reason
    assert "big" in res.reason and "mid" in res.reason          # what was asked, what served
    assert pool.degrades[-1]["preferred"] == "big" and pool.degrades[-1]["served"] == "mid"
    assert pool.degrades[-1]["route"] == L.LOCAL and "out of memory" in pool.degrades[-1]["attempts"][0]["reason"]
    assert [r["name"] for r in pool.resident()] == ["mid"]      # nothing else substituted
    assert gov.active(G.MODEL) == 1                              # the failed load's lease was returned
    assert pool.status()["degrades"] == pool.degrades


def test_the_chain_keeps_going_past_more_than_one_failure():
    m, gov, pool = pool_with(fail={"big": RuntimeError("bad weights"), "mid": MemoryError("oom")})
    res = pool.load_chain(["big", "mid", "small"])
    assert res.name == "small" and [a.name for a in res.attempts] == ["big", "mid"]
    assert "bad weights" in res.attempts[0].reason and "oom" in res.attempts[1].reason


def test_a_model_the_machine_has_no_room_for_degrades_too():
    m, gov, pool = pool_with(sizes={"big": 1.8, "small": 0.2})
    m.os_gb = 5.0                       # other programs took the room the baseline had
    res = pool.load_chain(["big", "small"])
    assert res.name == "small" and res.degraded and m.ooms == 0
    assert res.attempts[0].name == "big" and "would read" in res.attempts[0].reason


def test_when_every_local_model_fails_the_permitted_remote_route_serves_and_says_so():
    m, gov, pool = pool_with(fail={"big": MemoryError("oom"), "mid": MemoryError("oom"),
                                   "small": RuntimeError("corrupt")})
    res = pool.load_chain(["big", "mid", "small"], remote="remote-gemini")
    assert res.route == L.REMOTE and res.name == "remote-gemini" and res.available and res.degraded
    assert res.outcome is None and [a.name for a in res.attempts] == ["big", "mid", "small"]
    assert pool.degrades[-1]["route"] == L.REMOTE and pool.degrades[-1]["served"] == "remote-gemini"
    assert pool.resident() == [] and gov.active(G.MODEL) == 0


def test_without_a_remote_route_a_total_failure_is_reported_never_substituted():
    m, gov, pool = pool_with(fail={"big": MemoryError("oom"), "mid": MemoryError("oom"),
                                   "small": MemoryError("oom")})
    res = pool.load_chain(["big", "mid", "small"])
    assert res.route == L.NONE and res.name == "" and not res.available
    assert len(res.attempts) == 3 and pool.degrades[-1]["route"] == L.NONE


@pytest.mark.parametrize("chain", [[], ["mid", "big"], ["mid", "mid"]])
def test_a_chain_must_be_non_empty_and_strictly_smaller_each_step(chain):
    m, gov, pool = pool_with()
    with pytest.raises(ValueError):
        pool.load_chain(chain)
    assert m.loads == []


def test_an_unregistered_name_in_the_chain_is_an_attempt_not_a_crash():
    m, gov, pool = pool_with()
    res = pool.load_chain(["ghost", "small"])
    assert res.name == "small" and res.attempts[0].name == "ghost"
    assert "not a registered expert" in res.attempts[0].reason
