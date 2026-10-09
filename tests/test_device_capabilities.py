"""
DEVICE-002: a device may advertise actions dynamically, but the model sees and
can call only those FRIDAY has registered as capabilities with a policy
category. relay.set advertised -> hardware.relay.set once registered; an
advertised action nobody registered cannot be called.
"""

from __future__ import annotations

import pytest

from friday import fabric, policy
from friday.toolsets import device_capabilities as dc
from friday.toolsets import devices


class Relay(devices.DeviceProvider):
    id = "relay-hub"

    def __init__(self):
        self.acted: list[tuple] = []

    def health(self):
        return {"state": fabric.READY}

    def list_devices(self):
        return [{"id": "r1", "actions": [
            {"name": "relay.set", "risk": "none"},
            {"name": "firmware.wipe", "risk": "none"},
        ]}]

    def read_sensor(self, device_id, sensor):
        return {}

    def poll_events(self, device_id):
        return []

    def act(self, device_id, action, params=None):
        self.acted.append((device_id, action, params))
        return {"ok": True}


@pytest.fixture
def table(monkeypatch):
    monkeypatch.setattr(policy, "TOOL_CATEGORIES", dict(policy.TOOL_CATEGORIES))


@pytest.fixture
def hub():
    h = devices.DeviceHub()
    h.provider = Relay()
    h.register(h.provider)
    return h


@pytest.fixture
def engine():
    return policy.PolicyEngine(autonomy=policy.GUARDED)


def test_registered_action_reaches_the_model_as_a_hardware_capability(table, hub):
    reg = dc.DeviceCapabilityRegistry()
    reg.register("relay.set", policy.PHYSICAL_INDICATOR)
    seen = reg.model_view(hub.list_devices()["devices"])
    assert [c["capability"] for c in seen] == ["hardware.relay.set"]
    assert seen[0]["category"] == policy.PHYSICAL_INDICATOR
    assert policy.default_engine.category_of("hardware.relay.set") == policy.PHYSICAL_INDICATOR


def test_advertised_but_unregistered_action_is_invisible(table, hub):
    reg = dc.DeviceCapabilityRegistry()
    reg.register("relay.set", policy.PHYSICAL_INDICATOR)
    names = {c["action"] for c in reg.model_view(hub.list_devices()["devices"])}
    assert "firmware.wipe" not in names


def test_advertised_but_unregistered_action_cannot_be_called(table, hub, engine):
    reg = dc.DeviceCapabilityRegistry()
    reg.register("relay.set", policy.PHYSICAL_INDICATOR)
    with pytest.raises(dc.UnregisteredCapability):
        reg.act(hub, "relay-hub", "r1", "firmware.wipe", engine=engine)
    assert hub.provider.acted == []
    assert policy.default_engine.category_of("hardware.firmware.wipe") is None


def test_registered_action_runs_through_the_policy_engine(table, hub, engine):
    reg = dc.DeviceCapabilityRegistry()
    reg.register("relay.set", policy.PHYSICAL_INDICATOR)
    assert reg.act(hub, "relay-hub", "r1", " Relay.Set ", {"on": True}, engine=engine) == {"ok": True}
    assert hub.provider.acted == [("r1", "relay.set", {"on": True})]


def test_registered_action_with_a_strict_category_is_not_run_unasked(table, hub, engine):
    reg = dc.DeviceCapabilityRegistry()
    reg.register("relay.set", policy.PHYSICAL_ACCESS)
    with pytest.raises(policy.PolicyError):
        reg.act(hub, "relay-hub", "r1", "relay.set", engine=engine)
    assert hub.provider.acted == []


def test_registration_needs_a_known_category_and_cannot_be_relaxed(table):
    reg = dc.DeviceCapabilityRegistry()
    with pytest.raises(ValueError):
        reg.register("relay.set", "MADE_UP")
    reg.register("relay.set", policy.PHYSICAL_ACCESS)
    with pytest.raises(ValueError):
        reg.register("relay.set", policy.PHYSICAL_INDICATOR)
    reg.register("relay.set", policy.PHYSICAL_ACCESS)  # idempotent


@pytest.mark.parametrize("name", ["", "relay", "../x", "hardware.relay.set", "a b.c"])
def test_malformed_action_names_are_refused(table, name):
    with pytest.raises(ValueError):
        dc.DeviceCapabilityRegistry().register(name, policy.PHYSICAL_INDICATOR)
