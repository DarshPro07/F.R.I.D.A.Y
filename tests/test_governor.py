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


TOTAL_GB = 32.0

#: The machine the admission tests run on, at rest: 30% of its RAM and 10% of
#: its CPU in use. Pressure is judged against this (RES-003).
IDLE = G.Baseline(ram_available_gb=TOTAL_GB * 0.7, cpu_percent=10.0,
                  readings=12, provisional=False)


def sample(cpu=10.0, ram=30.0, disk=100.0, browsers=0, *, at=None,
           total=TOTAL_GB) -> G.Sample:
    return G.Sample(at=time.time() if at is None else at, cpu_percent=cpu,
                    ram_percent=ram, ram_available_gb=total * (1 - ram / 100),
                    disk_free_gb=disk, browser_processes=browsers,
                    friday_rss_mb=200.0, ram_total_gb=total)


class Dial:
    """A sampler whose reading the test turns."""

    def __init__(self, s: G.Sample) -> None:
        self.s = s

    def __call__(self) -> G.Sample:
        return self.s


def make(s: G.Sample | None = None, *, baseline: G.Baseline | None = IDLE,
         **thresholds) -> tuple[G.Governor, Dial]:
    dial = Dial(s or sample())
    gov = G.Governor(thresholds=G.Thresholds(**thresholds), sampler=dial,
                     sample_ttl_s=0.0, baseline=baseline)
    return gov, dial


# -- assessment ------------------------------------------------------------


def test_levels_rise_with_the_share_of_idle_headroom_in_use():
    def level(**reading) -> str:
        return G.assess(sample(**reading), baseline=IDLE).level

    assert level() == G.NORMAL
    assert level(cpu=72) == G.ELEVATED       # 69% of the idle CPU capacity in use
    assert level(cpu=90) == G.HIGH           # 89%
    assert level(cpu=97) == G.CRITICAL
    assert level(ram=50) == G.NORMAL         # 29% of the idle RAM headroom
    assert level(ram=60) == G.ELEVATED       # 43%
    assert level(ram=75) == G.HIGH           # 64%
    assert level(ram=90) == G.CRITICAL       # 86%
    assert level(disk=3) == G.HIGH
    assert level(disk=0.5) == G.CRITICAL


def test_assess_reports_every_reason_and_the_worst_level_wins():
    p = G.assess(sample(cpu=90, ram=96), baseline=IDLE)
    assert p.level == G.CRITICAL
    assert any("CPU" in r for r in p.reasons) and any("RAM" in r for r in p.reasons)


# -- RES-003: judged against this machine, not a fixed percentage ----------

#: REFERENCE-A, measured: 15.35 GB of RAM, its operating system idling near
#: 76% (RES-001, 2026-09-27).
REF_A_TOTAL = 15.35
REF_A_IDLE = G.Baseline(ram_available_gb=REF_A_TOTAL * 0.24, cpu_percent=8.0,
                        readings=12, provisional=False)


def ref_a(ram: float, *, at: float) -> G.Sample:
    return sample(cpu=8.0, ram=ram, at=at, total=REF_A_TOTAL)


def test_a_machine_idling_at_76_percent_reads_green():
    # The old classifier called this ELEVATED and shed optional work at rest.
    assert G.assess(ref_a(76.0, at=0.0)).level == G.NORMAL
    gov, _ = make(ref_a(76.0, at=0.0), baseline=REF_A_IDLE)
    p = gov.pressure()
    assert p.level == G.NORMAL and p.to_dict()["state"] == "GREEN", p.reasons
    assert gov.admit(G.OPTIONAL).admitted


def test_losing_headroom_fast_reads_pressured_before_any_fixed_line():
    # REFERENCE-A at rest, then something takes memory at about 45 MB/s.
    gov, dial = make(ref_a(76.0, at=1000.0), baseline=REF_A_IDLE)
    assert gov.pressure().level == G.NORMAL
    for i, ram in enumerate((77.5, 79.0, 80.5, 82.0), start=1):
        dial.s = ref_a(ram, at=1000.0 + 5 * i)
        p = gov.pressure()
    assert p.level in (G.ELEVATED, G.HIGH), p.reasons
    assert any("falling" in r for r in p.reasons), p.reasons
    assert p.sample.ram_percent < gov.thresholds.ram_critical
    # The same reading without the fall behind it is not pressure: the
    # governor answers to the fall, not to the number.
    still, _ = make(ref_a(82.0, at=2000.0), baseline=REF_A_IDLE)
    assert still.pressure().level == G.NORMAL


def test_a_machine_at_68_percent_losing_memory_fast_is_pressured():
    idle = G.Baseline(ram_available_gb=8.0, cpu_percent=5.0, readings=12, provisional=False)
    gov, dial = make(sample(ram=50.0, at=0.0, total=16.0), baseline=idle)
    gov.pressure()
    for i, ram in enumerate((56.0, 62.0, 68.0), start=1):
        dial.s = sample(ram=ram, at=4.0 * i, total=16.0)
        p = gov.pressure()
    assert p.level != G.NORMAL and any("falling" in r for r in p.reasons), p.reasons


def _at_share(share: float, at: float) -> G.Sample:
    """A reading with `share` of IDLE's RAM headroom in use."""
    available = IDLE.ram_available_gb * (1 - share)
    return sample(ram=100 * (1 - available / TOTAL_GB), at=at)


def test_a_reading_wavering_at_a_boundary_does_not_flap():
    # 40% of the idle headroom in use is the YELLOW line; for two minutes the
    # reading sits a hair either side of it.
    gov, dial = make(baseline=IDLE)
    seen = []
    for i in range(60):
        dial.s = _at_share(0.402 if i % 2 == 0 else 0.398, at=100.0 + 2 * i)
        seen.append(gov.pressure())
    raw_changes = sum(1 for a, b in zip(seen, seen[1:]) if a.raw != b.raw)
    changes = sum(1 for a, b in zip(seen, seen[1:]) if a.level != b.level)
    assert raw_changes == 59                    # the line really is being crossed
    assert seen[0].level == G.ELEVATED and changes == 0


def test_a_better_reading_must_hold_before_the_level_falls():
    gov, dial = make(baseline=IDLE)
    dial.s = _at_share(0.64, at=0.0)            # ORANGE
    assert gov.pressure().level == G.HIGH
    held = []
    for t in (2.0, 12.0, 22.0, 31.0):           # back at rest from t=2
        dial.s = _at_share(0.0, at=t)
        held.append(gov.pressure())
    assert all(p.level == G.HIGH and p.raw == G.NORMAL for p in held), held
    assert "holding ORANGE" in held[-1].reasons[-1]
    dial.s = _at_share(0.0, at=32.0)            # 30 s of calm
    assert gov.pressure().level == G.NORMAL


def test_the_level_falls_to_the_worst_reading_of_the_wait_and_needs_a_margin():
    gov, dial = make(baseline=IDLE)
    dial.s = _at_share(0.64, at=0.0)
    assert gov.pressure().level == G.HIGH
    dial.s = _at_share(0.45, at=1.0)            # a YELLOW reading while calming
    gov.pressure()
    dial.s = _at_share(0.0, at=20.0)
    gov.pressure()
    dial.s = _at_share(0.0, at=31.0)
    assert gov.pressure().level == G.ELEVATED   # not straight to GREEN
    # Just under the YELLOW line is not far enough under it to leave.
    for t in range(40, 200, 10):
        dial.s = _at_share(0.37, at=float(t))
        assert gov.pressure().level == G.ELEVATED, t


def test_a_cpu_spike_is_not_pressure_but_a_sustained_load_is():
    gov, dial = make(sample(cpu=10.0, at=0.0))
    gov.pressure()
    for t in (2.0, 4.0, 6.0, 8.0):
        dial.s = sample(cpu=10.0, at=t)
        gov.pressure()
    dial.s = sample(cpu=100.0, at=9.0)          # one second at full load
    p = gov.pressure()
    assert p.level == G.NORMAL, p.reasons
    for t in (11.0, 13.0, 15.0, 17.0, 19.0):    # ten seconds of it
        dial.s = sample(cpu=100.0, at=t)
        p = gov.pressure()
    assert p.level == G.CRITICAL and "CPU 100%" in p.reasons


def test_a_cpu_reading_right_after_another_is_not_a_new_measurement(monkeypatch):
    """V7-D57: psutil measures CPU since its previous call, so a reading 20 ms
    after another is noise - it said 100% on a quiet machine, and the
    governor shed everything on it."""
    import psutil

    calls = []

    def cpu_percent(interval=None):
        calls.append(interval)
        return 100.0

    monkeypatch.setattr(psutil, "cpu_percent", cpu_percent)
    monkeypatch.setattr(G, "_cpu_last", (time.monotonic(), 12.0))
    assert G.sample_machine().cpu_percent == 12.0 and calls == []
    monkeypatch.setattr(G, "_cpu_last", (time.monotonic() - 5.0, 12.0))
    assert G.sample_machine().cpu_percent == 100.0 and calls == [None]


def test_fixed_percentages_are_emergency_guardrails_only():
    # 88% was the old HIGH line; on a machine that idles at 88% it is rest.
    idle_88 = G.Baseline(ram_available_gb=REF_A_TOTAL * 0.12, cpu_percent=8.0,
                         readings=12, provisional=False)
    assert G.assess(ref_a(88.0, at=0.0), baseline=idle_88).level == G.NORMAL
    # 95% is an emergency whatever the baseline.
    idle_94 = G.Baseline(ram_available_gb=REF_A_TOTAL * 0.06, cpu_percent=8.0,
                         readings=12, provisional=False)
    p = G.assess(ref_a(95.2, at=0.0), baseline=idle_94)
    assert p.level == G.CRITICAL and "RAM 95% used" in p.reasons
    # So is available memory below the floor, and the commit charge at its limit.
    low = ref_a(80.0, at=0.0)
    low.ram_available_gb = 0.3
    assert G.assess(low, baseline=REF_A_IDLE).level == G.CRITICAL
    near_limit = ref_a(76.0, at=0.0)
    near_limit.commit_available_gb, near_limit.commit_limit_gb = 0.4, 30.7
    p = G.assess(near_limit, baseline=REF_A_IDLE)
    assert p.level == G.CRITICAL and any("commit limit" in r for r in p.reasons)


def test_the_commit_charge_is_judged_against_its_own_idle_headroom():
    idle = G.Baseline(ram_available_gb=REF_A_TOTAL * 0.24, cpu_percent=8.0,
                      commit_available_gb=10.0, readings=12, provisional=False)
    reading = ref_a(76.0, at=0.0)
    reading.commit_available_gb, reading.commit_limit_gb = 3.5, 30.7
    p = G.assess(reading, baseline=idle)
    assert p.level == G.HIGH and any("commit charge" in r for r in p.reasons), p.reasons


def test_without_a_qualified_baseline_the_governor_profiles_the_machine_itself():
    gov, dial = make(sample(ram=50.0, at=0.0), baseline=None)
    p = gov.pressure()
    assert p.level == G.NORMAL and p.baseline.provisional and p.baseline.readings == 1
    # Readings at rest refine it - their median - spaced so a burst cannot dominate.
    for i in range(1, 5):
        dial.s = sample(ram=30.0, at=10.0 * i)
        gov.pressure()
    assert gov.baseline.readings == 5
    assert gov.baseline.ram_available_gb == pytest.approx(TOTAL_GB * 0.7)
    # Readings taken while FRIDAY holds work do not.
    lease = gov.admit(G.WORKER).lease
    dial.s = sample(ram=70.0, at=60.0)
    gov.pressure()
    assert gov.baseline.readings == 5
    gov.release(lease)
    # A measured baseline replaces it and is not moved by readings.
    gov.rebaseline(IDLE)
    dial.s = sample(ram=50.0, at=80.0)
    gov.pressure()
    assert gov.baseline == IDLE


def test_live_sampler_has_the_shape_and_never_guesses():
    s = G.sample_machine()
    assert 0.0 <= s.cpu_percent <= 100.0
    assert 0.0 <= s.ram_percent <= 100.0
    assert s.disk_free_gb > 0
    assert s.browser_processes >= 0
    assert s.friday_rss_mb > 0
    assert s.ram_total_gb and s.ram_available_gb < s.ram_total_gb
    if sys.platform == "win32":                 # the commit charge, where it is reported
        assert 0.0 < s.commit_available_gb < s.commit_limit_gb
    else:
        assert s.commit_available_gb is None and s.commit_limit_gb is None


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


def test_worker_capacity_is_what_admit_would_let_run_at_once():
    gov, dial = make()
    assert gov.worker_capacity("fanout") == 2
    gov.justify_parallel("fanout")
    assert gov.worker_capacity("fanout") == 3
    assert gov.worker_capacity("serial") == 2, "the lift is for that objective only"
    admitted = 0
    while gov.admit(G.WORKER, objective_id="fanout").admitted:
        admitted += 1
        assert admitted <= 3
    assert admitted == gov.worker_capacity("fanout")
    dial.s = sample(cpu=90)
    assert gov.worker_capacity("fanout") == 1, "HIGH pressure reduces it to one"
    dial.s = sample(ram=99)
    assert gov.worker_capacity("fanout") == 0, "CRITICAL admits no new worker"


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
    dial.s = sample(ram=60)                      # merely ELEVATED
    assert gov.admit(G.OPTIONAL).decision == G.SHED
    assert gov.admit(G.WORKER).admitted           # workers still fine


def test_browser_cap_and_high_pressure_serialise_browsers():
    gov, dial = make(max_browsers=2)
    a = gov.admit(G.BROWSER)
    b = gov.admit(G.BROWSER)
    assert a.admitted and b.admitted
    assert gov.admit(G.BROWSER).decision == G.QUEUE
    gov.release(b.lease)
    dial.s = sample(ram=75)                           # HIGH
    assert gov.admit(G.BROWSER).decision == G.QUEUE   # one already running


def test_status_and_banner_explain_reduced_concurrency():
    gov, dial = make()
    assert gov.status()["banner"] == ""
    dial.s = sample(ram=75)
    st = gov.status()
    assert st["pressure"]["level"] == G.HIGH and st["pressure"]["state"] == "ORANGE"
    assert st["pressure"]["baseline"]["provisional"] is False
    assert "one worker at a time" in st["banner"]
    assert st["caps"]["workers"] == 2
    dial.s = sample(ram=97)
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


@pytest.mark.slow
def test_real_memory_pressure_bounds_new_work():
    """RES-001/RES-003's low-RAM stress test: memory really allocated, the
    machine really sampled, judged against its own idle baseline with no
    threshold moved. As the RAM climbs the governor must shed optional work
    and hold concurrency to one worker before any fixed line is crossed, keep
    that level for the dwell once the memory is released, and then admit
    again. Slow: it allocates up to 1.2 GB, so the standard gate leaves it
    out and it is run on its own."""
    import psutil

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import stress_low_ram

    room = psutil.virtual_memory().available / 1024**3 - 1.0
    if room < 0.4:
        pytest.skip(f"only {room:.2f} GB above the run's 1 GB safety floor: too little "
                    "to show a fall without endangering the machine")
    result = stress_low_ram.run(cap_mb=1200, floor_gb=1.0)
    wrong = [step for step in result["steps"] + result["after_release"]
             if not step["as_policy_says"]]
    assert not wrong, wrong
    assert result["demonstrated"], (result["peak"], result["released"]["level"], result["stopped"])
    first = result["first_pressure"]
    assert first["ram_percent"] < result["thresholds"]["ram_critical"], first
    assert result["held_s"] >= result["thresholds"]["dwell_s"], result["held_s"]
    # Real running optional work was stopped by the governor itself, by
    # memory pressure, with section 55's record filled in.
    running = result["running_optional"]
    record = running["owned_process"]
    assert record and record["owner"] == "stress_low_ram" and record["objective_id"] == "RES-001"
    assert record["pid"] and record["deadline"] and record["cleanup"] and record["kind"] == G.OPTIONAL
    assert running["memory_driven"] and running["stopped_at_level"] in (G.ELEVATED, G.HIGH, G.CRITICAL)
    assert running["exit_code"] is not None
    assert running["reduction"]["released"] and running["reduction"]["lease"] == record["lease"]


# -- RES-001: GPU, network, model residency, running work, section 55 -------

def test_gpu_memory_pressure_is_judged_and_absent_gpu_is_not_guessed():
    assert sample().gpu_memory_percent is None
    assert G.assess(sample(), G.Thresholds(), baseline=IDLE).level == G.NORMAL
    full = sample()
    full.gpu_memory_percent = 93.0
    assert G.assess(full, G.Thresholds(), baseline=IDLE).level == G.HIGH
    full.gpu_memory_percent = 99.0
    p = G.assess(full, G.Thresholds(), baseline=IDLE)
    assert p.level == G.CRITICAL and any("GPU memory" in r for r in p.reasons)
    assert p.to_dict()["sample"]["gpu_memory_percent"] == 99.0


def test_network_throughput_is_sampled_from_counter_deltas(monkeypatch):
    import types

    import psutil
    counters = iter([types.SimpleNamespace(bytes_sent=0, bytes_recv=0),
                     types.SimpleNamespace(bytes_sent=500_000, bytes_recv=500_000)])
    monkeypatch.setattr(psutil, "net_io_counters", lambda *a, **k: next(counters))
    clock = iter([100.0, 101.0])
    monkeypatch.setattr(G.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(G, "_net_last", None)
    assert G._network_mbps() is None            # nothing to measure against yet
    assert G._network_mbps() == pytest.approx(8.0)


def test_resident_model_is_capped_and_refused_under_high_pressure():
    gov, dial = make()
    assert gov.admit(G.MODEL, label="llama").admitted
    assert gov.admit(G.MODEL, label="qwen").decision == G.QUEUE
    gov.release(gov.active_leases()[0]["lease"])
    dial.s = sample(cpu=90)
    d = gov.admit(G.MODEL, label="qwen")
    assert d.decision == G.SHED and "no new local model" in d.reason


def test_an_observer_hears_each_fresh_reading_and_cannot_break_it():
    gov, dial = make()
    heard = []
    gov.add_observer(heard.append)
    gov.add_observer(heard.append)                      # registered once
    gov.add_observer(lambda p: 1 / 0)                   # a failing observer costs nothing
    p = gov.pressure(fresh=True)
    assert heard == [p]
    cached = G.Governor(thresholds=G.Thresholds(), sampler=dial, sample_ttl_s=60.0, baseline=IDLE)
    cached.add_observer(heard.append)
    cached.pressure()
    cached.pressure()                                   # the second read is the cached one
    assert len(heard) == 2
    gov.remove_observer(heard.append)
    gov.pressure(fresh=True)
    assert len(heard) == 2


def test_every_admitted_lease_records_the_owned_process_fields():
    gov, _ = make()
    d = gov.admit(G.WORKER, label="job", objective_id="obj-1", owner="hermes",
                  pid=4242, deadline_s=60.0)
    (rec,) = gov.owned_processes()
    assert (rec["lease"], rec["owner"], rec["objective_id"], rec["pid"]) == (d.lease, "hermes", "obj-1", 4242)
    assert rec["heartbeat"] and rec["deadline"] > rec["started"] and rec["kind"] == G.WORKER
    assert gov.status()["owned"][0]["lease"] == d.lease
    gov.release(d.lease)
    assert gov.owned_processes() == []


def test_pressure_stops_running_optional_work_but_not_workers():
    gov, dial = make()
    stopped = []
    opt = gov.admit(G.OPTIONAL, label="digest", cleanup=lambda: stopped.append("digest"))
    worker = gov.admit(G.WORKER, label="job", cleanup=lambda: stopped.append("job"))
    assert opt.admitted and worker.admitted
    dial.s = sample(cpu=90)
    assert gov.pressure(fresh=True).level != G.NORMAL
    assert stopped == ["digest"]
    assert gov.active(G.OPTIONAL) == 0 and gov.active(G.WORKER) == 1
    assert gov.reductions[-1]["lease"] == opt.lease and gov.reductions[-1]["released"]


def test_high_pressure_evicts_a_resident_model_and_a_failed_cleanup_keeps_the_lease():
    gov, dial = make()
    gov.admit(G.MODEL, label="llama", cleanup=lambda: None)
    dial.s = sample(cpu=90)
    assert gov.pressure(fresh=True).level in (G.HIGH, G.CRITICAL)
    assert gov.active(G.MODEL) == 0 and gov.owned_processes() == []

    gov2, dial2 = make()

    def stuck():
        raise RuntimeError("stuck")

    gov2.admit(G.MODEL, label="llama", cleanup=stuck)
    dial2.s = sample(cpu=90)
    gov2.pressure(fresh=True)
    assert gov2.active(G.MODEL) == 1 and "cleanup failed" in gov2.reductions[-1]["reason"]
    n = len(gov2.reductions)
    gov2.pressure(fresh=True)
    assert len(gov2.reductions) == n                # reported once, not every reading


# -- BG-001: background work waits for the person, the power and the priority --


class Ctx:
    """A context reading the test turns: the person's idle seconds and the power."""

    def __init__(self, idle_s: float | None = 600.0, on_battery: bool | None = False) -> None:
        self.idle_s, self.on_battery = idle_s, on_battery

    def __call__(self) -> G.Context:
        return G.Context(user_idle_s=self.idle_s, on_battery=self.on_battery)


def make_ctx(ctx: Ctx | None, s: G.Sample | None = None) -> tuple[G.Governor, Dial]:
    dial = Dial(s or sample())
    gov = G.Governor(thresholds=G.Thresholds(), sampler=dial, sample_ttl_s=0.0,
                     baseline=IDLE, context=ctx)
    return gov, dial


def test_background_work_runs_when_the_person_is_away_on_mains_with_headroom():
    gov, _ = make_ctx(Ctx(idle_s=600.0, on_battery=False))
    assert gov.pressure().level == G.NORMAL
    assert gov.admit(G.OPTIONAL, label="digest").admitted


def test_background_work_is_deferred_while_the_person_is_active():
    ctx = Ctx(idle_s=5.0)
    gov, _ = make_ctx(ctx)
    assert gov.pressure().level == G.NORMAL           # headroom alone would admit it
    d = gov.admit(G.OPTIONAL, label="digest")
    assert d.decision == G.SHED and "person is active" in d.reason, d.reason
    away = gov.thresholds.user_idle_s
    ctx.idle_s = away - 0.1
    assert gov.admit(G.OPTIONAL).decision == G.SHED
    ctx.idle_s = away                                 # away once idle for the whole window
    assert gov.admit(G.OPTIONAL).admitted


def test_background_work_is_deferred_on_battery_and_runs_once_plugged_in():
    ctx = Ctx(on_battery=True)
    gov, _ = make_ctx(ctx)
    d = gov.admit(G.OPTIONAL, label="digest")
    assert d.decision == G.SHED and "battery" in d.reason, d.reason
    ctx.on_battery = False
    assert gov.admit(G.OPTIONAL, label="digest").admitted


def test_pressure_still_sheds_background_work_when_idle_and_plugged_in():
    gov, _ = make_ctx(Ctx(idle_s=600.0, on_battery=False), sample(cpu=90))
    d = gov.admit(G.OPTIONAL)
    assert d.decision == G.SHED and "pressure" in d.reason, d.reason


def test_urgent_work_skips_the_activity_and_power_gates_but_not_pressure():
    gov, dial = make_ctx(Ctx(idle_s=1.0, on_battery=True))
    assert gov.admit(G.OPTIONAL).decision == G.SHED
    assert gov.admit(G.OPTIONAL, priority=G.URGENT).admitted
    dial.s = sample(cpu=90)
    d = gov.admit(G.OPTIONAL, priority=G.URGENT)
    assert d.decision == G.SHED and "pressure" in d.reason, d.reason


def test_running_background_work_stops_when_the_person_returns():
    ctx = Ctx(idle_s=600.0)
    gov, _ = make_ctx(ctx)
    stopped = []
    opt = gov.admit(G.OPTIONAL, label="digest", cleanup=lambda: stopped.append("digest"))
    worker = gov.admit(G.WORKER, label="job", cleanup=lambda: stopped.append("job"))
    assert opt.admitted and worker.admitted
    assert gov.pressure(fresh=True).level == G.NORMAL and stopped == []   # still away
    ctx.idle_s = 3.0                                  # the person is back at the keyboard
    assert gov.pressure(fresh=True).level == G.NORMAL  # headroom alone would not stop it
    assert stopped == ["digest"]
    assert gov.active(G.OPTIONAL) == 0 and gov.active(G.WORKER) == 1
    record = gov.reductions[-1]
    assert record["lease"] == opt.lease and record["released"]
    assert "person is active" in record["reason"], record["reason"]


def test_running_background_work_stops_when_the_machine_is_unplugged():
    ctx = Ctx(on_battery=False)
    gov, _ = make_ctx(ctx)
    stopped = []
    gov.admit(G.OPTIONAL, label="index", cleanup=lambda: stopped.append("index"))
    gov.pressure(fresh=True)
    assert stopped == []
    ctx.on_battery = True
    gov.pressure(fresh=True)
    assert stopped == ["index"] and gov.active(G.OPTIONAL) == 0
    assert "battery" in gov.reductions[-1]["reason"]


def test_running_urgent_work_keeps_going_when_the_person_returns_or_unplugs():
    ctx = Ctx()
    gov, dial = make_ctx(ctx)
    stopped = []
    gov.admit(G.OPTIONAL, label="urgent", priority=G.URGENT,
              cleanup=lambda: stopped.append("urgent"))
    ctx.idle_s, ctx.on_battery = 1.0, True
    gov.pressure(fresh=True)
    assert stopped == [] and gov.active(G.OPTIONAL) == 1
    assert gov.owned_processes()[0]["priority"] == G.URGENT
    dial.s = sample(cpu=90)                           # pressure still stops it
    gov.pressure(fresh=True)
    assert stopped == ["urgent"]


def test_running_background_work_is_not_stopped_by_an_unknown_or_failing_context():
    ctx = Ctx(idle_s=None, on_battery=None)
    gov, _ = make_ctx(ctx)
    gov.admit(G.OPTIONAL, label="digest", cleanup=lambda: None)
    gov.pressure(fresh=True)
    assert gov.active(G.OPTIONAL) == 1

    def broken() -> G.Context:
        raise OSError("probe gone")

    failing, _ = make_ctx(broken)
    assert failing.context() == G.Context()
    assert failing.admit(G.OPTIONAL, cleanup=lambda: None).admitted
    failing.pressure(fresh=True)                      # must not raise
    assert failing.active(G.OPTIONAL) == 1


def test_the_context_is_not_probed_when_no_background_work_runs():
    probes = []

    def counting() -> G.Context:
        probes.append(1)
        return G.Context(user_idle_s=600.0, on_battery=False)

    gov, _ = make_ctx(counting)
    gov.admit(G.WORKER, label="job")
    gov.pressure(fresh=True)
    assert probes == []
    gov.admit(G.OPTIONAL, label="digest", cleanup=lambda: None)
    probes.clear()
    gov.pressure(fresh=True)
    assert probes == [1]


def test_an_unreadable_context_never_defers_work():
    assert G.Context(user_idle_s=None, on_battery=None).deferral(G.Thresholds()) == ""
    unknown, _ = make_ctx(Ctx(idle_s=None, on_battery=None))
    assert unknown.admit(G.OPTIONAL).admitted
    unwired, _ = make_ctx(None)
    assert unwired.context() == G.Context() and unwired.admit(G.OPTIONAL).admitted


def test_only_optional_work_waits_for_the_person():
    gov, _ = make_ctx(Ctx(idle_s=1.0, on_battery=True))
    for kind in (G.WORKER, G.BROWSER, G.MODEL):
        assert gov.admit(kind).admitted, kind


def test_an_unknown_priority_is_refused():
    gov, _ = make_ctx(Ctx())
    with pytest.raises(ValueError, match="priority"):
        gov.admit(G.OPTIONAL, priority="whenever")


def test_status_reports_the_context():
    gov, _ = make_ctx(Ctx(idle_s=42.0, on_battery=True))
    assert gov.status()["context"] == {"user_idle_s": 42.0, "on_battery": True}


def test_background_wait_names_each_reason_and_is_empty_only_when_all_clear():
    gov, _ = make_ctx(Ctx(idle_s=600.0, on_battery=False))
    assert gov.background_wait() == ""
    active, _ = make_ctx(Ctx(idle_s=5.0, on_battery=False))
    assert "person is active" in active.background_wait()
    unplugged, _ = make_ctx(Ctx(idle_s=600.0, on_battery=True))
    assert "battery" in unplugged.background_wait()
    pressured, _ = make_ctx(Ctx(idle_s=600.0, on_battery=False), sample(cpu=90))
    wait = pressured.background_wait()
    assert "no headroom" in wait and "pressure" in wait, wait


def test_a_pressured_machine_does_not_spend_a_context_probe_on_background_work():
    probes = []

    def counting() -> G.Context:
        probes.append(1)
        return G.Context(user_idle_s=600.0, on_battery=False)

    gov, _ = make_ctx(counting, sample(cpu=90))
    assert "no headroom" in gov.background_wait()
    assert probes == []


def test_status_says_whether_background_work_runs_now_and_why_not():
    ctx = Ctx(idle_s=600.0, on_battery=False)
    gov, dial = make_ctx(ctx)
    assert gov.status()["background"] == {"runs": True, "wait": ""}
    ctx.idle_s = 3.0
    background = gov.status()["background"]
    assert background["runs"] is False and "person is active" in background["wait"]
    ctx.idle_s, ctx.on_battery = 600.0, True
    assert "battery" in gov.status()["background"]["wait"]
    ctx.on_battery = False
    dial.s = sample(cpu=90)
    background = gov.status()["background"]
    assert background["runs"] is False and "no headroom" in background["wait"]


def test_the_background_verdict_agrees_with_what_admit_does():
    for ctx, s in ((Ctx(), None), (Ctx(idle_s=1.0), None), (Ctx(on_battery=True), None),
                   (Ctx(), sample(cpu=90))):
        gov, _ = make_ctx(ctx, s)
        verdict = gov.status()["background"]
        assert verdict["runs"] == gov.admit(G.OPTIONAL, label="digest").admitted, verdict


def test_real_probes_defer_background_work_when_the_machine_has_no_headroom():
    """The live sampler against a floor no machine clears: headroom is the only
    gate (the person is treated as always away), and it holds."""
    gov = G.Governor(thresholds=G.Thresholds(ram_floor_gb=1e6, user_idle_s=0.0),
                     context=G.sample_context)
    background = gov.status()["background"]
    assert background["runs"] is False and "no headroom" in background["wait"], background
    d = gov.admit(G.OPTIONAL, label="digest")
    assert d.decision == G.SHED and "pressure" in d.reason, d.reason
    assert gov.admit(G.OPTIONAL, label="digest", priority=G.URGENT).decision == G.SHED


def test_on_battery_reads_the_power_state(monkeypatch):
    import psutil

    class Battery:
        def __init__(self, plugged):
            self.percent, self.power_plugged = 80.0, plugged

    for plugged, expected in ((True, False), (False, True), (None, None)):
        monkeypatch.setattr(psutil, "sensors_battery", lambda p=plugged: Battery(p))
        assert G._on_battery() is expected
    monkeypatch.setattr(psutil, "sensors_battery", lambda: None)   # a desktop
    assert G._on_battery() is None


def test_idle_seconds_survive_the_32_bit_tick_wrap():
    assert G._idle_seconds_since(now=5, last=0xFFFFFFFF - 994) == pytest.approx(1.0)
    assert G._idle_seconds_since(now=90_000, last=30_000) == pytest.approx(60.0)


@pytest.mark.skipif(sys.platform != "win32", reason="only Windows reports last input")
def test_the_real_idle_probe_reads_a_plausible_value():
    idle = G._user_idle_s()
    assert idle is None or 0.0 <= idle < 366 * 86400


def test_work_past_its_deadline_or_silent_is_reaped_at_any_pressure():
    gov, _ = make(heartbeat_stale_s=30.0, max_workers=5)
    gone = []
    late = gov.admit(G.WORKER, label="late", deadline_s=-1.0, cleanup=lambda: gone.append("late"))
    quiet = gov.admit(G.WORKER, label="quiet", cleanup=lambda: gone.append("quiet"))
    assert gov.heartbeat(quiet.lease)               # it reported in once, then went silent
    gov._owned[quiet.lease].heartbeat -= 60.0
    live = gov.admit(G.WORKER, label="live", cleanup=lambda: gone.append("live"))
    gov.reduce_running()
    assert sorted(gone) == ["late", "quiet"]
    assert gov.heartbeat(late.lease) is False and gov.heartbeat(live.lease) is True
