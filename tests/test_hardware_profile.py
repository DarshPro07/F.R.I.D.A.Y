"""
The machine FRIDAY runs on is measured, and its policy derived from the
measurements (PRD v7 RES-002).

Amendment A4: two reference machines, both first-class, and FRIDAY tuned to
neither. The same code must give different profiles, and different policies,
where the machines differ - and nothing may be keyed to a machine's name.
"""
from __future__ import annotations

import json
import platform
import sys
import time

import psutil
import pytest

from friday import governor as G
from friday import hardware_profile as hp


def readings(*, total: float, used_percent: float, n: int = 10) -> list[G.Sample]:
    return [G.Sample(at=float(i), cpu_percent=8.0, ram_percent=used_percent,
                     ram_available_gb=total * (1 - used_percent / 100), disk_free_gb=200.0,
                     browser_processes=0, friday_rss_mb=150.0, ram_total_gb=total)
            for i in range(n)]


def machine(*, total: float, gpus: list[hp.GPU] | None, cpus: int = 16) -> dict:
    return {"platform": "win32", "ram_total_gb": total, "cpu_logical": cpus,
            "cpu_physical": cpus // 2, "gpus": gpus, "disk_free_gb": 200.0,
            "disk_total_gb": 475.0, "battery": {"percent": 80.0, "plugged": True}}


#: The two reference machines as Amendment A4 describes them.
REFERENCE_A = machine(total=15.35, gpus=[hp.GPU("AMD Radeon(TM) Graphics", "0x1002", 0.48, 7.68)])
REFERENCE_B = machine(total=16.0, gpus=[hp.GPU("NVIDIA GeForce RTX", "0x10de", 4.0, 7.9)])


def test_this_machine_is_measured_and_nothing_is_guessed():
    profile = hp.qualify(cause="test", readings=2, every_s=0.0, save=False)
    assert profile.ram_total_gb == pytest.approx(psutil.virtual_memory().total / 1024**3)
    assert profile.cpu_logical == psutil.cpu_count(logical=True)
    assert profile.baseline.readings == 2 and not profile.baseline.provisional
    if sys.platform == "win32":
        assert isinstance(profile.gpus, list)
        assert all(g.dedicated_gb >= 0 and g.name for g in profile.gpus)
        assert "Microsoft Basic Render Driver" not in [g.name for g in profile.gpus]
    else:
        assert profile.gpus is None                     # not reported here, so not claimed
    assert set(profile.policy) >= {"room_gb", "workers", "browsers", "model"}


def test_two_reference_machines_get_different_profiles_and_policies_from_the_same_code():
    a = hp.build(REFERENCE_A, readings(total=15.35, used_percent=76.0), cause="test")
    b = hp.build(REFERENCE_B, readings(total=16.0, used_percent=55.0), cause="test")
    assert a.fingerprint() != b.fingerprint()
    # A local model may take the RAM the machine can spend and still read GREEN,
    # within Amendment A3's 2 GB per artifact...
    assert a.policy["model"]["ram_budget_gb"] == pytest.approx(0.4 * 15.35 * 0.24, abs=0.01)
    assert b.policy["model"]["ram_budget_gb"] == hp.MODEL_ARTIFACT_CAP_GB
    # ...and be offloaded only as far as the GPU's own memory goes.
    assert a.policy["model"]["gpu_offload_gb"] == pytest.approx(0.48)
    assert b.policy["model"]["gpu_offload_gb"] == hp.MODEL_ARTIFACT_CAP_GB
    # Nothing is keyed to a machine's name.
    text = json.dumps([a.to_dict(), b.to_dict()])
    assert "hostname" not in text and platform.node() not in text


def test_browsers_are_as_many_as_the_room_holds_at_the_measured_cost():
    roomy = hp.build(REFERENCE_B, readings(total=16.0, used_percent=55.0), cause="test",
                     browser_mb=184.0)
    assert roomy.policy["browsers"]["max"] == G.Thresholds().max_browsers
    tight = hp.build(machine(total=8.0, gpus=[]), readings(total=8.0, used_percent=97.0),
                     cause="test", browser_mb=184.0)
    assert tight.policy["browsers"]["max"] == 1      # FRIDAY stays useful on a small machine
    assert tight.policy["model"]["gpu_offload_gb"] == 0.0
    unmeasured = hp.build(REFERENCE_A, readings(total=15.35, used_percent=76.0), cause="test")
    assert "not measured" in unmeasured.policy["browsers"]["basis"]


def test_the_governor_starts_from_the_stored_profile_of_this_machine(tmp_path, monkeypatch):
    monkeypatch.setattr(hp, "PROFILE_PATH", tmp_path / "hardware_profile.json")
    here = hp.measure_machine()
    profile = hp.build(here, readings(total=here["ram_total_gb"], used_percent=60.0),
                       cause="test", browser_mb=184.0)
    hp.store(profile)
    assert hp.stored() == profile
    G.configure(None)
    try:
        gov = G.governor()
        assert gov.baseline == profile.baseline and not gov.baseline.provisional
        assert gov.thresholds.max_browsers == profile.policy["browsers"]["max"]
    finally:
        G.configure(None)


def test_a_profile_of_another_machine_is_not_used(tmp_path, monkeypatch):
    monkeypatch.setattr(hp, "PROFILE_PATH", tmp_path / "hardware_profile.json")
    here = hp.measure_machine()
    elsewhere = dict(here, ram_total_gb=here["ram_total_gb"] + 48.0)
    hp.store(hp.build(elsewhere, readings(total=64.0, used_percent=30.0), cause="test"))
    assert any("RAM total" in c for c in hp.changes(hp.stored(), here))
    baseline, thresholds = hp.for_governor()
    assert baseline is None and thresholds == G.Thresholds()


def test_the_command_line_qualifies_and_shows(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(hp, "PROFILE_PATH", tmp_path / "hardware_profile.json")
    assert hp.main(["show"]) == 1                        # nothing measured yet
    out = tmp_path / "evidence.json"
    started = time.monotonic()
    assert hp.main(["qualify", "--readings", "2", "--every-s", "0", "--out", str(out)]) == 0
    assert time.monotonic() - started < 60
    assert json.loads(out.read_text(encoding="utf-8"))["cause"] == "qualification requested"
    capsys.readouterr()
    assert hp.main(["show"]) == 0
    assert json.loads(capsys.readouterr().out)["changes"] == []
