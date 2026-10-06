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


def test_the_default_local_context_comes_from_the_machines_own_room():
    """MODEL-007: 4K-8K, with the context the room holds beside the model budget.
    A's 1.47 GB holds none beside its budget, so it stays at the 4K floor; B's
    2.88 GB holds 8K beside a 2 GB model."""
    a = hp.build(REFERENCE_A, readings(total=15.35, used_percent=76.0), cause="test")
    b = hp.build(REFERENCE_B, readings(total=16.0, used_percent=55.0), cause="test")
    assert (a.policy["model"]["default_ctx"], b.policy["model"]["default_ctx"]) == (4096, 8192)
    tight = hp.build(machine(total=8.0, gpus=None), readings(total=8.0, used_percent=70.0), cause="test")
    assert tight.policy["model"]["default_ctx"] == 4096


def test_resident_models_are_as_many_artifact_slots_as_the_room_holds():
    """MODEL-005: the governor's resident-model cap comes from the machine, not
    a constant. The reference machines' rooms (1.47 GB and 2.88 GB) hold one
    2 GB artifact, as does a tight 8 GB machine's; a roomy 32 GB one holds three."""
    a = hp.build(REFERENCE_A, readings(total=15.35, used_percent=76.0), cause="test")
    b = hp.build(REFERENCE_B, readings(total=16.0, used_percent=55.0), cause="test")
    tight = hp.build(machine(total=8.0, gpus=None), readings(total=8.0, used_percent=70.0), cause="test")
    roomy = hp.build(machine(total=32.0, gpus=None), readings(total=32.0, used_percent=40.0), cause="test")
    assert [p.policy["model"]["max_resident"] for p in (a, b, tight, roomy)] == [1, 1, 1, 3]
    assert hp._caps(roomy).max_resident_models == 3
    assert hp._caps(a).max_resident_models == 1


def test_a_stored_profile_from_before_the_cap_was_derived_keeps_the_default():
    old = hp.build(REFERENCE_B, readings(total=16.0, used_percent=55.0), cause="test")
    del old.policy["model"]["max_resident"]
    assert hp._caps(old).max_resident_models == G.Thresholds().max_resident_models


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
        assert gov.thresholds.max_resident_models == profile.policy["model"]["max_resident"]
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


# -- RES-004: re-profiled when the machine changes, with the cause --------


class Readings:
    """A sampler whose readings the test sets: `used` percent of `total`."""

    def __init__(self, *, total: float, used: float) -> None:
        self.total, self.used, self.at = total, used, 0.0

    def __call__(self) -> G.Sample:
        self.at += 1.0
        return G.Sample(
            at=self.at, cpu_percent=8.0, ram_percent=self.used,
            ram_available_gb=self.total * (1 - self.used / 100), disk_free_gb=200.0,
            browser_processes=0, friday_rss_mb=150.0, ram_total_gb=self.total)


def test_the_first_run_qualifies_the_machine_and_records_why(tmp_path, monkeypatch):
    monkeypatch.setattr(hp, "PROFILE_PATH", tmp_path / "hardware_profile.json")
    sampler = Readings(total=15.35, used=76.0)
    profile, cause = hp.refresh(machine=REFERENCE_A, sampler=sampler, readings=2, every_s=0.0)
    assert cause == "first run on this machine"
    assert hp.stored() == profile and not profile.baseline.provisional
    assert [h["cause"] for h in profile.history] == [cause]
    again, cause = hp.refresh(machine=REFERENCE_A, sampler=sampler, readings=2, every_s=0.0)
    assert cause is None and again == profile             # nothing changed, nothing redone


def test_a_hardware_change_is_measured_again_with_its_cause(tmp_path, monkeypatch):
    monkeypatch.setattr(hp, "PROFILE_PATH", tmp_path / "hardware_profile.json")
    first, _ = hp.refresh(machine=REFERENCE_A, sampler=Readings(total=15.35, used=76.0),
                          readings=2, every_s=0.0)
    upgraded = dict(REFERENCE_A, ram_total_gb=31.35,
                    gpus=[hp.GPU("NVIDIA GeForce RTX", "0x10de", 4.0, 15.7)])
    profile, cause = hp.refresh(machine=upgraded, sampler=Readings(total=31.35, used=40.0),
                                readings=2, every_s=0.0)
    assert f"RAM total {round(15.35, 1)} -> {round(31.35, 1)} GB" in cause and "GPUs" in cause
    assert profile.baseline.ram_available_gb == pytest.approx(31.35 * 0.6)
    assert profile.policy["model"]["gpu_offload_gb"] == hp.MODEL_ARTIFACT_CAP_GB
    assert [h["remeasured"] for h in profile.history] == [["machine", "baseline", "policy"]] * 2
    assert hp.changes(hp.stored(), upgraded) == []


def test_a_power_change_rederives_without_measuring_again(tmp_path, monkeypatch):
    monkeypatch.setattr(hp, "PROFILE_PATH", tmp_path / "hardware_profile.json")
    first, _ = hp.refresh(machine=REFERENCE_A, sampler=Readings(total=15.35, used=76.0),
                          readings=2, every_s=0.0)
    unplugged = dict(REFERENCE_A, battery={"percent": 64.0, "plugged": False})
    profile, cause = hp.refresh(machine=unplugged, sampler=Readings(total=15.35, used=90.0),
                                readings=2, every_s=0.0)
    assert cause == "power: mains -> battery"
    assert profile.baseline == first.baseline               # not measured again
    assert profile.battery == unplugged["battery"]
    assert profile.history[-1]["remeasured"] == ["power", "policy"]


def test_the_running_governor_adopts_a_new_profile(tmp_path, monkeypatch):
    monkeypatch.setattr(hp, "PROFILE_PATH", tmp_path / "hardware_profile.json")
    G.configure(G.Governor(sampler=Readings(total=15.35, used=76.0)))
    try:
        profile, cause = hp.refresh_and_adopt(machine=REFERENCE_A,
                                              sampler=Readings(total=15.35, used=76.0),
                                              readings=2, every_s=0.0)
        assert cause and G.governor().baseline == profile.baseline
        assert G.governor().thresholds.max_browsers == profile.policy["browsers"]["max"]
    finally:
        G.configure(None)


def test_a_busy_governor_postpones_the_measurement(tmp_path, monkeypatch):
    monkeypatch.setattr(hp, "PROFILE_PATH", tmp_path / "hardware_profile.json")
    gov = G.Governor(sampler=Readings(total=15.35, used=76.0))
    G.configure(gov)
    try:
        lease = gov.admit(G.WORKER).lease
        profile, cause = hp.refresh_and_adopt(machine=REFERENCE_A,
                                              sampler=Readings(total=15.35, used=76.0),
                                              readings=2, every_s=0.0)
        assert profile is None and cause == "postponed: FRIDAY is busy"
        assert hp.stored() is None
        gov.release(lease)
    finally:
        G.configure(None)
