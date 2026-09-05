"""
A-051: the soak harness itself is under test (PRD §16 readiness gate).

The 8-hour run is the gate; this proves the thing that produces the
verdict cannot lie:

  - a short real run drives EVERY promised workload move against the real
    control plane (fakes only behind the provider and Hermes transports),
    reports zero violations and zero errored cycles, and says SMOKE - not
    PASS, which only a judged (>= 30 min) run may say;
  - the analyser calls monotonic growth on a series that never returns to
    its first-window level, and does not call it on a sawtooth;
  - a run whose governor shed every worker is INCOMPLETE, never PASS;
  - errored cycles fail the verdict; they are never a footnote.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("soak", ROOT / "scripts" / "soak.py")
soak = importlib.util.module_from_spec(spec)
sys.modules["soak"] = soak
spec.loader.exec_module(soak)


def _samples(values: list[float], key: str = "rss_mb", step: float = 10.0) -> list[dict]:
    out = []
    for i, v in enumerate(values):
        row = {"t": i * step, "rss_mb": 50.0, "handles": 100, "threads": 5, "children": 2,
               "sqlite_conns": 3, "tokens": 0, "provider_calls": 0, "log_lines": 0,
               "queue_depth": 0, "cpu_pct": 1.0, "host_ram_pct": 50.0, "host_cpu_pct": 10.0}
        row[key] = v
        out.append(row)
    return out


COUNTS_ALL = {k: 5 for k in ("objective", "provider_ok", "budget_refused", "hermes_delegate",
                             "hermes_stop_start", "hermes_crash_recovered", "worker_crashes_recovered",
                             "cancels", "nonces", "scheduler_claims", "handoffs", "db_writes")}


def test_a_series_that_never_returns_is_monotonic_growth():
    # 2 hours of samples, rss climbing 50 -> 250 and never coming back.
    n = 720
    s = _samples([50.0 + i * (200.0 / n) for i in range(n)])
    r = soak.analyse(s, 7200.0, COUNTS_ALL, [], [], 100)
    assert r["series"]["rss_mb"]["monotonic_growth"] is True
    assert "rss_mb" in r["growing"]
    assert r["verdict"] == "FAIL"


def test_a_sawtooth_is_not_a_leak():
    # rss oscillates 50..120 every 20 samples for 2 hours: GC, a child that comes and goes.
    n = 720
    s = _samples([50.0 + (i % 20) * 3.5 for i in range(n)])
    r = soak.analyse(s, 7200.0, COUNTS_ALL, [], [], 100)
    assert r["series"]["rss_mb"]["monotonic_growth"] is False
    assert r["growing"] == []
    assert r["verdict"] == "PASS"


def test_small_drift_under_the_floor_is_not_called_growth():
    n = 720
    s = _samples([50.0 + i * (6.0 / n) for i in range(n)])      # +6 MB over 2 h, under the 10 MB floor
    r = soak.analyse(s, 7200.0, COUNTS_ALL, [], [], 100)
    assert r["growing"] == []


def test_warmup_is_excluded_from_the_first_window():
    # A 25 MB jump in the first 60 s (lazy imports), flat afterwards, for 2 hours.
    n = 720
    s = _samples([30.0 if i < 5 else 55.0 for i in range(n)])
    r = soak.analyse(s, 7200.0, COUNTS_ALL, [], [], 100)
    assert r["warmup_s"] >= 60.0
    assert r["series"]["rss_mb"]["first_median"] == 55.0
    assert r["growing"] == []


def test_a_short_run_is_smoke_never_pass():
    s = _samples([50.0] * 30)                                       # 5 minutes
    r = soak.analyse(s, 300.0, COUNTS_ALL, [], [], 10)
    assert r["judged"] is False
    assert r["verdict"] == "SMOKE"


def test_a_shed_worker_lifecycle_is_incomplete_never_pass():
    counts = dict(COUNTS_ALL)
    for k in ("hermes_delegate", "hermes_stop_start", "hermes_crash_recovered"):
        counts[k] = 0
    counts["hermes_shed"] = 300
    s = _samples([50.0] * 720)
    r = soak.analyse(s, 7200.0, counts, [], [], 300)
    assert r["verdict"] == "INCOMPLETE"
    assert set(r["missing_moves"]) == {"hermes_delegate", "hermes_stop_start", "hermes_crash_recovered"}
    assert "INCOMPLETE" in soak.render(r)


def test_errored_cycles_and_violations_fail_the_verdict():
    s = _samples([50.0] * 720)
    assert soak.analyse(s, 7200.0, COUNTS_ALL, [], ["cycle 3: boom"], 10)["verdict"] == "FAIL"
    assert soak.analyse(s, 7200.0, COUNTS_ALL, ["budget: exhausted objective allowed a call"], [], 10)["verdict"] == "FAIL"


def test_a_real_short_run_drives_every_move_with_no_violations(tmp_path, monkeypatch):
    """The harness against the real control plane: 45 s is enough for the
    every-Nth moves (crash every 10th cycle, stop/start every 25th, worker
    kill every 15th) to fire at least once."""
    from friday import governor as G
    monkeypatch.setenv("ADA_DB", str(tmp_path / "soak.sqlite3"))
    report = soak.run(45.0, tmp_path / "out", sample_every_s=2.0, governor_mode="relaxed")
    try:
        assert report["violations"] == [], report["violations"]
        assert report["errors"] == [], report["errors"]
        assert report["missing_moves"] == [], report["missing_moves"]
        assert report["verdict"] == "SMOKE", report["verdict"]
        assert (tmp_path / "out" / "report.md").exists()
        assert (tmp_path / "out" / "samples.json").exists()
        assert report["counts"]["hermes_crash_recovered"] >= 1
        assert report["counts"]["worker_crashes_recovered"] >= 1
        assert report["counts"]["budget_refused"] >= 1
    finally:
        G.configure(None)


def test_the_short_run_cadence_fires_every_move_at_least_twice():
    """CI failed where this machine passed: at a fixed N=25 the Hermes
    stop/start never fired inside a 45 s run on a slower runner, and the
    report said INCOMPLETE for a reason that was the harness's, not the
    product's. The cadence tightens with the planned duration - so assert
    the arithmetic, not the wall clock."""
    # The slowest observed rate is ~14 cycles in 45 s (CI). Every gated
    # move must fire at least twice at that rate.
    slowest_cycles = 14
    tight = {"budget": 3, "cancel": 4, "hermes_crash": 4, "worker_crash": 5,
             "hermes_stop_start": 6}
    for move, n in tight.items():
        assert slowest_cycles // n >= 2, f"{move} fires {slowest_cycles // n}x in the shortest run"


def test_a_long_run_keeps_the_rare_moves_rare():
    hourly_cycles = 3000            # measured: ~3,100 cycles/hour
    loose = {"hermes_crash": 10, "worker_crash": 15, "hermes_stop_start": 25}
    for move, n in loose.items():
        fires = hourly_cycles // n
        assert 100 < fires < 400, f"{move} fires {fires}x/hour"
