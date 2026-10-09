"""
DEVICE-007: an update verifies its hash or signature, goes to one test device,
passes a health probe, then rolls out; a failed probe rolls back to the prior
release. The provider and the probe are stubs; the SDK is not involved.
"""

from __future__ import annotations

import hashlib

from friday import fabric
from friday.toolsets import device_update as du
from friday.toolsets import devices


def _release(version: str, payload: bytes = b"fw", **kw) -> du.Release:
    return du.Release(version, payload, sha256=kw.pop(
        "sha256", hashlib.sha256(payload).hexdigest()), **kw)


class Fleet(devices.DeviceProvider):
    """Records installs per device; `bad` is the set of versions that brick."""

    id = "fleet"

    def __init__(self, state=fabric.READY, fail_install_on=()):
        self.state = state
        self.installed: dict[str, str] = {}
        self.log: list[tuple[str, str]] = []
        self.fail_install_on = set(fail_install_on)

    def health(self):
        return {"state": self.state}

    def list_devices(self):
        return []

    def read_sensor(self, device_id, sensor):
        return {}

    def poll_events(self, device_id):
        return []

    def act(self, device_id, action, params=None):
        assert action == "firmware.install"
        if device_id in self.fail_install_on and params["version"] == "2.0":
            raise RuntimeError("flash failed")
        self.installed[device_id] = params["version"]
        self.log.append((device_id, params["version"]))
        return {"ok": True}


PRIOR = _release("1.0", b"old")
NEW = _release("2.0", b"new")
DEVICES = ["d1", "d2", "d3"]


def _fleet():
    f = Fleet()
    f.installed = {d: "1.0" for d in DEVICES}
    return f


def _probe_ok(provider, device_id):
    return True


def test_good_update_goes_to_the_canary_first_then_the_rest():
    fleet = _fleet()
    out = du.rollout(fleet, NEW, PRIOR, DEVICES, probe=_probe_ok)
    assert out["status"] == du.ROLLED_OUT
    assert [d for d, _ in fleet.log] == DEVICES        # canary d1 first
    assert set(fleet.installed.values()) == {"2.0"}


def test_failed_canary_probe_rolls_back_and_leaves_the_rest_untouched():
    fleet = _fleet()
    out = du.rollout(fleet, NEW, PRIOR, DEVICES, probe=lambda p, d: False)
    assert out["status"] == du.ROLLED_BACK
    assert out["failed_device"] == "d1"
    assert fleet.installed == {"d1": "1.0", "d2": "1.0", "d3": "1.0"}
    assert [d for d, _ in fleet.log] == ["d1", "d1"]    # install, then rollback


def test_failed_probe_mid_rollout_rolls_back_every_updated_device():
    fleet = _fleet()
    out = du.rollout(fleet, NEW, PRIOR, DEVICES, probe=lambda p, d: d != "d2")
    assert out["status"] == du.ROLLED_BACK
    assert out["failed_device"] == "d2"
    assert set(fleet.installed.values()) == {"1.0"}
    assert "d3" not in [d for d, _ in fleet.log]


def test_failed_install_counts_as_a_failed_probe():
    fleet = _fleet()
    fleet.fail_install_on = {"d1"}
    out = du.rollout(fleet, NEW, PRIOR, DEVICES, probe=_probe_ok)
    assert out["status"] == du.ROLLED_BACK
    assert set(fleet.installed.values()) == {"1.0"}


def test_a_probe_that_raises_is_a_failed_probe():
    def boom(provider, device_id):
        raise TimeoutError("no answer")

    fleet = _fleet()
    out = du.rollout(fleet, NEW, PRIOR, DEVICES, probe=boom)
    assert out["status"] == du.ROLLED_BACK
    assert set(fleet.installed.values()) == {"1.0"}


def test_hash_mismatch_is_rejected_before_anything_is_sent():
    fleet = _fleet()
    bad = _release("2.0", b"new", sha256="0" * 64)
    out = du.rollout(fleet, bad, PRIOR, DEVICES, probe=_probe_ok)
    assert out["status"] == du.REJECTED
    assert fleet.log == []


def test_a_release_with_no_hash_or_signature_is_rejected():
    out = du.rollout(_fleet(), du.Release("2.0", b"new"), PRIOR, DEVICES,
                     probe=_probe_ok)
    assert out["status"] == du.REJECTED


def test_a_valid_signature_alone_verifies():
    signed = du.Release("2.0", b"new", signature=b"sig")
    fleet = _fleet()
    out = du.rollout(fleet, signed, PRIOR, DEVICES, probe=_probe_ok,
                     verify_signature=lambda payload, sig: sig == b"sig")
    assert out["status"] == du.ROLLED_OUT


def test_a_bad_or_unverifiable_signature_is_rejected():
    signed = du.Release("2.0", b"new", signature=b"sig")
    for verifier in (lambda payload, sig: False, None):
        fleet = _fleet()
        out = du.rollout(fleet, signed, PRIOR, DEVICES, probe=_probe_ok,
                         verify_signature=verifier)
        assert out["status"] == du.REJECTED
        assert fleet.log == []


def test_an_unverifiable_rollback_target_is_rejected_up_front():
    bad_prior = _release("1.0", b"old", sha256="f" * 64)
    fleet = _fleet()
    out = du.rollout(fleet, NEW, bad_prior, DEVICES, probe=_probe_ok)
    assert out["status"] == du.REJECTED
    assert fleet.log == []


def test_an_absent_provider_reports_unavailable_and_sends_nothing():
    fleet = Fleet(state=fabric.UNAVAILABLE)
    out = du.rollout(fleet, NEW, PRIOR, DEVICES, probe=_probe_ok)
    assert out["status"] == du.UNAVAILABLE
    assert fleet.log == []


def test_a_rollback_that_fails_is_reported_not_hidden():
    class Stuck(Fleet):
        def act(self, device_id, action, params=None):
            if params["version"] == "1.0":
                raise RuntimeError("cannot flash back")
            return super().act(device_id, action, params)

    fleet = Stuck()
    out = du.rollout(fleet, NEW, PRIOR, DEVICES, probe=lambda p, d: False)
    assert out["status"] == du.ROLLBACK_FAILED
    assert out["stuck"] == ["d1"]


def test_no_devices_is_rejected():
    out = du.rollout(_fleet(), NEW, PRIOR, [], probe=_probe_ok)
    assert out["status"] == du.REJECTED
