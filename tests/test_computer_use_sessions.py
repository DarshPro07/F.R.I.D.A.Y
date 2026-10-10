"""
CUA-008: concurrent computer-use sessions have an owner, a lease with an
expiry, a window set and resource limits, on the governor leases.

The governor is a real one fed a healthy scripted sample and the registry
clock is a test dial, so nothing depends on this machine or on wall time.
"""
from __future__ import annotations

import time

import pytest

from friday import computer_use_sessions as S
from friday import governor as G

TOTAL_GB = 32.0
IDLE = G.Baseline(ram_available_gb=TOTAL_GB * 0.7, cpu_percent=10.0,
                  readings=12, provisional=False)


class Clock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


def _gov(**thresholds) -> G.Governor:
    s = G.Sample(at=time.time(), cpu_percent=10.0, ram_percent=30.0,
                 ram_available_gb=TOTAL_GB * 0.7, disk_free_gb=100.0,
                 browser_processes=0, friday_rss_mb=200.0, ram_total_gb=TOTAL_GB)
    return G.Governor(thresholds=G.Thresholds(**thresholds), sampler=lambda: s,
                      sample_ttl_s=0.0, baseline=IDLE)


@pytest.fixture
def reg():
    clock = Clock()
    gov = _gov()
    registry = S.SessionRegistry(governor=gov, clock=clock)
    registry.clock = clock
    registry.gov = gov
    return registry


def _open(reg, objective="obj-1", worker="w-1", windows=("notepad:1",), apps=(), **kw):
    return reg.open(objective_id=objective, worker=worker, windows=windows,
                    apps=apps, **kw)


def test_a_session_records_owner_lease_expiry_windows_and_limits(reg):
    s = _open(reg, windows=("notepad:1", "calc:2"), ttl_s=60, max_actions=5)
    d = reg.describe(s.session_id)
    assert d["owner"] == {"objective_id": "obj-1", "worker": "w-1"}
    assert d["windows"] == ["calc:2", "notepad:1"]
    assert d["expires_at"] == reg.clock.now + 60
    assert d["limits"] == {"max_windows": S.DEFAULT_MAX_WINDOWS, "max_actions": 5}
    assert d["state"] == S.ACTIVE
    # the lease is a real governor lease, owned by the worker, for the objective
    leases = reg.gov.owned_processes()
    assert [o["lease"] for o in leases] == [d["lease"]]
    assert leases[0]["owner"] == "w-1" and leases[0]["objective_id"] == "obj-1"


def test_a_second_session_on_a_taken_window_is_refused_and_recorded(reg):
    first = _open(reg, windows=("notepad:1",))
    with pytest.raises(S.SessionRefused) as e:
        _open(reg, objective="obj-2", worker="w-2", windows=("Notepad:1 ",))
    assert e.value.record["reason_code"] == S.WINDOW_TAKEN
    assert e.value.record["held_by"] == first.session_id
    assert reg.refusals()[-1]["objective_id"] == "obj-2"
    assert reg.refusals()[-1]["resource"] == "notepad:1"
    # the refusal leaked no lease and left the first session untouched
    assert reg.gov.active(G.BROWSER) == 1
    assert reg.describe(first.session_id)["state"] == S.ACTIVE


def test_a_second_session_on_a_taken_application_is_refused(reg):
    _open(reg, windows=("chrome:1",), apps=("chrome",))
    with pytest.raises(S.SessionRefused) as e:
        _open(reg, objective="obj-2", worker="w-2", windows=("chrome:9",), apps=("Chrome",))
    assert e.value.record["reason_code"] == S.APP_TAKEN
    assert reg.gov.active(G.BROWSER) == 1


def test_sessions_on_disjoint_resources_run_concurrently(reg):
    a = _open(reg, windows=("a:1",), apps=("a",))
    b = _open(reg, objective="obj-2", worker="w-2", windows=("b:1",), apps=("b",))
    assert a.session_id != b.session_id
    assert reg.gov.active(G.BROWSER) == 2


def test_an_expired_lease_frees_the_window_and_stops_the_session(reg):
    a = _open(reg, windows=("notepad:1",), ttl_s=30)
    reg.authorize(a.session_id, "notepad:1")
    reg.clock.now += 31
    with pytest.raises(S.SessionStopped):
        reg.authorize(a.session_id, "notepad:1")
    assert reg.describe(a.session_id)["state"] == S.EXPIRED
    assert reg.gov.active(G.BROWSER) == 0          # the governor lease was returned
    b = _open(reg, objective="obj-2", worker="w-2", windows=("notepad:1",))
    assert reg.describe(b.session_id)["state"] == S.ACTIVE


def test_a_new_open_sweeps_an_expired_session_it_would_have_collided_with(reg):
    _open(reg, windows=("notepad:1",), ttl_s=30)
    reg.clock.now += 31
    b = _open(reg, objective="obj-2", worker="w-2", windows=("notepad:1",))
    assert reg.describe(b.session_id)["state"] == S.ACTIVE


def test_a_lease_the_governor_reaped_stops_the_session(reg):
    a = _open(reg, windows=("notepad:1",), ttl_s=30)
    reg.gov.release(a.lease)                       # the governor took the lease back
    with pytest.raises(S.SessionStopped):
        reg.authorize(a.session_id, "notepad:1")
    assert reg.describe(a.session_id)["state"] == S.REVOKED
    _open(reg, objective="obj-2", worker="w-2", windows=("notepad:1",))


def test_a_session_may_act_only_on_its_own_windows(reg):
    a = _open(reg, windows=("notepad:1",))
    b = _open(reg, objective="obj-2", worker="w-2", windows=("calc:2",))
    with pytest.raises(S.SessionRefused) as e:
        reg.authorize(a.session_id, "calc:2")
    assert e.value.record["reason_code"] == S.NOT_IN_SET
    assert reg.refusals()[-1]["held_by"] == b.session_id


def test_the_action_limit_stops_a_session_that_exceeds_it(reg):
    a = _open(reg, max_actions=2)
    reg.authorize(a.session_id, "notepad:1")
    reg.authorize(a.session_id, "notepad:1")
    with pytest.raises(S.SessionRefused) as e:
        reg.authorize(a.session_id, "notepad:1")
    assert e.value.record["reason_code"] == S.ACTION_LIMIT


def test_the_window_limit_is_enforced_at_open(reg):
    with pytest.raises(S.SessionRefused) as e:
        _open(reg, windows=("a:1", "a:2", "a:3"), max_windows=2)
    assert e.value.record["reason_code"] == S.WINDOW_LIMIT
    assert reg.gov.active(G.BROWSER) == 0


def test_a_governor_that_will_not_admit_refuses_the_session(reg):
    gov = _gov(max_browsers=1)
    registry = S.SessionRegistry(governor=gov, clock=reg.clock)
    registry.open(objective_id="o1", worker="w1", windows=("a:1",))
    with pytest.raises(S.SessionRefused) as e:
        registry.open(objective_id="o2", worker="w2", windows=("b:1",))
    assert e.value.record["reason_code"] == S.NOT_ADMITTED
    assert "browser cap" in e.value.record["detail"]


def test_close_releases_the_lease_and_the_window(reg):
    a = _open(reg)
    reg.close(a.session_id)
    assert reg.describe(a.session_id)["state"] == S.CLOSED
    assert reg.gov.active(G.BROWSER) == 0
    _open(reg, objective="obj-2", worker="w-2")


def test_a_session_needs_an_owner_and_a_window(reg):
    with pytest.raises(ValueError):
        reg.open(objective_id="", worker="w", windows=("a:1",))
    with pytest.raises(ValueError):
        reg.open(objective_id="o", worker="", windows=("a:1",))
    with pytest.raises(ValueError):
        reg.open(objective_id="o", worker="w", windows=())


@pytest.mark.parametrize("ttl", [float("nan"), float("inf"), float("-inf"), 0, -1])
def test_a_ttl_that_is_not_finite_and_positive_is_rejected(reg, ttl):
    with pytest.raises(ValueError):
        _open(reg, ttl_s=ttl)
    assert reg.gov.active(G.BROWSER) == 0
    assert reg.active() == []


@pytest.mark.parametrize("kw", [{"windows": "ab"}, {"apps": "chrome"}, {"windows": b"ab"}])
def test_a_bare_string_for_windows_or_apps_is_rejected(reg, kw):
    args = {"windows": ("a:1",), **kw}
    with pytest.raises(ValueError):
        reg.open(objective_id="o", worker="w", **args)
    assert reg.active() == []


def test_a_refused_open_leaves_no_phantom_governor_queue_entry():
    clock = Clock()
    gov = _gov(max_browsers=1)
    registry = S.SessionRegistry(governor=gov, clock=clock)
    a = registry.open(objective_id="o1", worker="w1", windows=("a:1",))
    with pytest.raises(S.SessionRefused):
        registry.open(objective_id="o2", worker="w2", windows=("b:1",))
    assert gov.queue_depth() == 1                  # the governor recorded a wait nobody has
    registry.close(a.session_id)
    assert gov.queue_depth() == 0


def test_an_expiry_sweep_also_retires_the_queue_entries_of_refused_opens():
    clock = Clock()
    gov = _gov(max_browsers=1)
    registry = S.SessionRegistry(governor=gov, clock=clock)
    registry.open(objective_id="o1", worker="w1", windows=("a:1",), ttl_s=30)
    with pytest.raises(S.SessionRefused):
        registry.open(objective_id="o2", worker="w2", windows=("b:1",))
    clock.now += 31
    registry.active()
    assert gov.queue_depth() == 0


def test_the_process_registry_is_one_shared_instance_that_refuses_across_callers():
    gov = _gov()
    S.configure(S.SessionRegistry(governor=gov, clock=Clock()))
    try:
        first, second = S.registry(), S.registry()
        assert first is second
        first.open(objective_id="o1", worker="w1", windows=("same:1",))
        with pytest.raises(S.SessionRefused) as e:
            second.open(objective_id="o2", worker="w2", windows=("same:1",))
        assert e.value.record["reason_code"] == S.WINDOW_TAKEN
    finally:
        S.configure(None)


def test_the_app_boundary_applies_only_between_sessions_that_declare_the_app(reg):
    _open(reg, windows=("chrome:1",), apps=("chrome",))
    # documented: a session that declares no apps is bounded by its windows only
    b = _open(reg, objective="obj-2", worker="w-2", windows=("chrome:2",))
    assert reg.describe(b.session_id)["apps"] == []
    with pytest.raises(S.SessionRefused) as e:
        _open(reg, objective="obj-3", worker="w-3", windows=("chrome:3",), apps=("Chrome",))
    assert e.value.record["reason_code"] == S.APP_TAKEN
