"""
The resource governor (PRD v3.1 FR-056, FR-013, NFR-P09).

Pressure is assessed from injected samples so every level is exercised
deterministically; the live-machine sampler is exercised once for shape.
The dispatch seams are proven with the REAL supervisor against the scripted
gateway: a worker under a saturated governor is refused before any session
is created, and a finished run releases its lease.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

from friday import governor as G

FAKE_GATEWAY = str(Path(__file__).parent / "fake_hermes_gateway.py")


def sample(cpu=10.0, ram=30.0, disk=100.0, browsers=0) -> G.Sample:
    return G.Sample(at=time.time(), cpu_percent=cpu, ram_percent=ram,
                    ram_available_gb=16.0, disk_free_gb=disk,
                    browser_processes=browsers, friday_rss_mb=200.0)


class Dial:
    """A sampler whose reading the test turns."""

    def __init__(self, s: G.Sample) -> None:
        self.s = s

    def __call__(self) -> G.Sample:
        return self.s


def make(s: G.Sample | None = None, **thresholds) -> tuple[G.Governor, Dial]:
    dial = Dial(s or sample())
    gov = G.Governor(thresholds=G.Thresholds(**thresholds), sampler=dial,
                     sample_ttl_s=0.0)
    return gov, dial


# -- assessment ------------------------------------------------------------


def test_assess_levels_are_monotonic_in_cpu_and_ram():
    assert G.assess(sample()).level == G.NORMAL
    assert G.assess(sample(cpu=72)).level == G.ELEVATED
    assert G.assess(sample(cpu=90)).level == G.HIGH
    assert G.assess(sample(cpu=97)).level == G.CRITICAL
    assert G.assess(sample(ram=80)).level == G.ELEVATED
    assert G.assess(sample(ram=90)).level == G.HIGH
    assert G.assess(sample(ram=96)).level == G.CRITICAL
    assert G.assess(sample(disk=3)).level == G.HIGH
    assert G.assess(sample(disk=0.5)).level == G.CRITICAL


def test_assess_reports_every_reason_and_the_worst_level_wins():
    p = G.assess(sample(cpu=90, ram=96))
    assert p.level == G.CRITICAL
    assert any("CPU" in r for r in p.reasons) and any("RAM" in r for r in p.reasons)


def test_live_sampler_has_the_shape_and_never_guesses():
    s = G.sample_machine()
    assert 0.0 <= s.cpu_percent <= 100.0
    assert 0.0 <= s.ram_percent <= 100.0
    assert s.disk_free_gb > 0
    assert s.browser_processes >= 0
    assert s.friday_rss_mb > 0


# -- admission -------------------------------------------------------------


def test_worker_cap_is_two_by_default_then_queue_then_shed():
    gov, _ = make(max_queue=1)
    a = gov.admit(G.WORKER, label="one")
    b = gov.admit(G.WORKER, label="two")
    assert a.admitted and b.admitted and a.lease != b.lease
    c = gov.admit(G.WORKER, label="three")
    assert c.decision == G.QUEUE and "cap 2" in c.reason
    d = gov.admit(G.WORKER, label="four")
    assert d.decision == G.SHED and "queue full" in d.reason
    gov.release(a.lease)
    assert gov.active(G.WORKER) == 1
    e = gov.admit(G.WORKER, label="five")
    assert e.admitted


def test_parallel_justification_lifts_the_cap_by_one_for_that_objective():
    gov, _ = make()
    gov.admit(G.WORKER)
    gov.admit(G.WORKER)
    assert gov.admit(G.WORKER, objective_id="serial").decision == G.QUEUE
    gov.justify_parallel("fanout")
    assert gov.admit(G.WORKER, objective_id="fanout").admitted
    assert gov.admit(G.WORKER, objective_id="fanout").decision == G.QUEUE


def test_high_pressure_reduces_concurrency_to_one():
    gov, dial = make()
    dial.s = sample(cpu=90)
    first = gov.admit(G.WORKER)
    assert first.admitted and "HIGH" in first.reason
    second = gov.admit(G.WORKER)
    assert second.decision == G.QUEUE and "reduced to 1" in second.reason


def test_critical_pressure_sheds_all_new_work():
    gov, dial = make()
    dial.s = sample(ram=97)
    for kind in (G.WORKER, G.BROWSER, G.OPTIONAL):
        d = gov.admit(kind)
        assert d.decision == G.SHED, kind
        assert "RAM 97%" in d.reason


def test_optional_work_is_shed_first():
    gov, dial = make()
    assert gov.admit(G.OPTIONAL).admitted
    dial.s = sample(cpu=72)                      # merely ELEVATED
    assert gov.admit(G.OPTIONAL).decision == G.SHED
    assert gov.admit(G.WORKER).admitted           # workers still fine


def test_browser_cap_and_high_pressure_serialise_browsers():
    gov, dial = make(max_browsers=2)
    a = gov.admit(G.BROWSER)
    b = gov.admit(G.BROWSER)
    assert a.admitted and b.admitted
    assert gov.admit(G.BROWSER).decision == G.QUEUE
    gov.release(b.lease)
    dial.s = sample(cpu=90)
    assert gov.admit(G.BROWSER).decision == G.QUEUE   # one already running


def test_status_and_banner_explain_reduced_concurrency():
    gov, dial = make()
    assert gov.status()["banner"] == ""
    dial.s = sample(cpu=90)
    st = gov.status()
    assert st["pressure"]["level"] == G.HIGH
    assert "one worker at a time" in st["banner"]
    assert st["caps"]["workers"] == 2
    dial.s = sample(cpu=97)
    assert "critical" in gov.status()["banner"].lower()


def test_sample_failure_degrades_to_elevated_not_a_crash():
    def broken():
        raise OSError("psutil exploded")
    gov = G.Governor(sampler=broken, sample_ttl_s=0.0)
    p = gov.pressure()
    assert p.level == G.ELEVATED and "sample failed" in p.reasons[0]
    assert gov.admit(G.WORKER).admitted          # workers still run
    assert gov.admit(G.OPTIONAL).decision == G.SHED


def test_unknown_kind_is_a_programming_error():
    gov, _ = make()
    with pytest.raises(ValueError):
        gov.admit("voice")


def test_control_plane_reads_stay_responsive_under_pressure():
    """FR-056 acceptance: a stress of admissions under CRITICAL pressure
    answers immediately (no probe per call beyond the TTL) and never blocks."""
    gov, dial = make()
    gov.sample_ttl_s = 60.0
    dial.s = sample(cpu=99)
    t0 = time.perf_counter()
    decisions = [gov.admit(G.WORKER) for _ in range(500)]
    elapsed = time.perf_counter() - t0
    assert all(d.decision == G.SHED for d in decisions)
    assert elapsed < 1.0
    assert len(gov.decisions) <= 200                 # bounded history


# -- the real dispatch seams --------------------------------------------


def test_hermes_delegate_is_refused_under_a_saturated_governor(tmp_path, monkeypatch):
    from friday import hermes_bridge as hb
    gov, _ = make()
    G.configure(gov)
    try:
        gov.admit(G.WORKER, label="busy-1")
        gov.admit(G.WORKER, label="busy-2")
        log = hb.WorkRunLog(tmp_path / "bridge.sqlite3")
        sup = hb.HermesSupervisor(log=log, command=[sys.executable, FAKE_GATEWAY], profile="")
        sup.READY_TIMEOUT = 20
        with pytest.raises(G.Refused) as info:
            sup.delegate(hb.TaskBundle(goal="do a thing"))
        assert info.value.decision.decision == G.QUEUE
        assert log.recent(10) == []                  # nothing was created
        sup.stop()
    finally:
        G.configure(None)


def test_hermes_run_holds_a_lease_and_releases_it_on_completion(tmp_path, monkeypatch):
    from friday import hermes_bridge as hb
    for key in ("FAKE_HERMES_CLARIFY", "FAKE_HERMES_HANG", "FAKE_HERMES_DIE"):
        monkeypatch.delenv(key, raising=False)
    gov, _ = make()
    G.configure(gov)
    try:
        log = hb.WorkRunLog(tmp_path / "bridge.sqlite3")
        sup = hb.HermesSupervisor(log=log, command=[sys.executable, FAKE_GATEWAY], profile="")
        sup.READY_TIMEOUT = 20
        out = sup.delegate(hb.TaskBundle(goal="do a thing"), share_memory=False)
        assert gov.active(G.WORKER) == 1
        record = sup.wait_for(out["work_run_id"], timeout=30)
        assert record["status"] in hb.TERMINAL
        assert gov.active(G.WORKER) == 0
        sup.stop()
    finally:
        G.configure(None)


def test_hermes_delegate_tool_reports_refusal_honestly(tmp_path):
    """The MCP tool turns Refused into a structured 'queued'/'shed' answer
    the model can read back to the boss - never a stack trace."""
    from friday.tools import hermes_control
    from friday import hermes_bridge as hb

    class Stub:
        log = None

        def delegate(self, *a, **k):
            raise G.Refused(G.Decision(G.QUEUE, G.WORKER, "worker cap 2 reached (2 active)",
                                       G.NORMAL, active_workers=2, queued=1))

    class FakeMCP:
        def __init__(self):
            self.tools = {}

        def tool(self):
            def deco(fn):
                self.tools[fn.__name__] = fn
                return fn
            return deco

    mcp = FakeMCP()
    hermes_control.register(mcp)
    hermes_control.configure(Stub())
    try:
        out = mcp.tools["hermes_delegate"](goal="build the thing")
        assert out["status"] == "queued"
        assert "cap 2" in out["reason"] and out["active_workers"] == 2
    finally:
        hermes_control.configure(None)
