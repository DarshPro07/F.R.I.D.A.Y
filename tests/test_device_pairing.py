"""
DEVICE-003: every device has a persistent identity and an explicit pairing and
trust lifecycle.

UNPAIRED -> pairing code -> owner approval -> PAIRED_UNVERIFIED -> capability
inventory -> policy assignment -> ACTIVE, persisted in the store. A device that
merely announces itself on the LAN is never trusted, and ws:// is accepted only
on the trusted local network.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from friday.store import Store
from friday.toolsets import device_pairing as dp

LAN = "ws://192.168.1.20:8765"


@pytest.fixture
def store(tmp_path):
    db = Store(tmp_path / "devices.sqlite3")
    yield db
    db.close()


@pytest.fixture
def registry(store):
    return dp.Registry(store)


def test_full_lifecycle_reaches_active_and_survives_a_new_process(tmp_path):
    path = tmp_path / "persist.sqlite3"
    first = Store(path)
    reg = dp.Registry(first)
    asked = reg.request_pairing("desk lamp", LAN)
    device_id = asked["device_id"]
    assert asked["state"] == dp.UNPAIRED and len(asked["code"]) == 6
    assert reg.approve(device_id, asked["code"], approved_by="owner") == dp.PAIRED_UNVERIFIED
    reg.record_inventory(device_id, ["sensor.temperature", "display.show"])
    assert reg.assign_policy(device_id, "read_only") == dp.ACTIVE
    first.close()

    second = Store(path)
    try:
        again = dp.Registry(second).get(device_id)
        assert again["state"] == dp.ACTIVE
        assert again["device_id"] == device_id
        assert again["inventory"] == ["sensor.temperature", "display.show"]
        assert again["policy"] == "read_only"
        assert again["approved_by"] == "owner"
    finally:
        second.close()


def test_a_device_on_the_lan_is_not_trusted_without_owner_approval(registry):
    device_id = registry.request_pairing("camera", LAN)["device_id"]
    assert not registry.is_trusted(device_id)
    assert not registry.is_trusted("dev_never_seen")
    # Skipping the owner's approval is refused at every later step.
    with pytest.raises(dp.PairingRefused):
        registry.record_inventory(device_id, ["sensor.motion"])
    with pytest.raises(dp.PairingRefused):
        registry.assign_policy(device_id, "read_only")
    assert registry.get(device_id)["state"] == dp.UNPAIRED


def test_only_an_active_device_is_trusted(registry):
    asked = registry.request_pairing("plug", LAN)
    device_id = asked["device_id"]
    registry.approve(device_id, asked["code"], approved_by="owner")
    assert not registry.is_trusted(device_id)  # paired, not yet verified
    registry.record_inventory(device_id, ["action.power"])
    assert not registry.is_trusted(device_id)  # no policy yet
    registry.assign_policy(device_id, "confirm_each_action")
    assert registry.is_trusted(device_id)


def test_wrong_or_expired_code_does_not_pair(store):
    now = datetime(2026, 10, 9, tzinfo=timezone.utc)
    clock = {"t": now}
    reg = dp.Registry(store, now=lambda: clock["t"])
    asked = reg.request_pairing("speaker", LAN)
    wrong = "000000" if asked["code"] != "000000" else "111111"
    with pytest.raises(dp.PairingRefused):
        reg.approve(asked["device_id"], wrong, approved_by="owner")
    assert reg.get(asked["device_id"])["state"] == dp.UNPAIRED
    clock["t"] = now + timedelta(hours=1)
    with pytest.raises(dp.PairingRefused):
        reg.approve(asked["device_id"], asked["code"], approved_by="owner")
    assert reg.get(asked["device_id"])["state"] == dp.UNPAIRED


def test_an_approval_is_spent_once_and_the_code_is_not_stored(registry, store):
    asked = registry.request_pairing("hub", LAN)
    device_id = asked["device_id"]
    registry.approve(device_id, asked["code"], approved_by="owner")
    with pytest.raises(dp.PairingRefused):
        registry.approve(device_id, asked["code"], approved_by="owner")
    row = store.device(device_id)
    assert asked["code"] not in {str(v) for v in row.values()}


def test_policy_needs_an_inventory_first(registry):
    asked = registry.request_pairing("sensor", LAN)
    device_id = asked["device_id"]
    registry.approve(device_id, asked["code"], approved_by="owner")
    with pytest.raises(dp.PairingRefused):
        registry.assign_policy(device_id, "read_only")
    assert registry.get(device_id)["state"] == dp.PAIRED_UNVERIFIED


@pytest.mark.parametrize("endpoint", [
    "wss://gadget.example.com/ws",
    "ws://192.168.1.20:8765",
    "ws://10.0.0.5/ws",
    "ws://127.0.0.1:9000",
    "ws://localhost:9000",
    "wss://203.0.113.7/ws",
])
def test_secure_or_trusted_local_endpoints_are_accepted(registry, endpoint):
    assert registry.request_pairing("gadget", endpoint)["state"] == dp.UNPAIRED


@pytest.mark.parametrize("endpoint", [
    "ws://gadget.example.com/ws",
    "ws://8.8.8.8/ws",
    "ws://169.254.169.254/ws",
    "http://192.168.1.20/ws",
    "https://gadget.example.com",
    "gadget.local",
    "",
])
def test_cleartext_off_the_local_network_is_refused(registry, endpoint):
    with pytest.raises(dp.PairingRefused):
        registry.request_pairing("gadget", endpoint)
    assert registry.devices() == []
