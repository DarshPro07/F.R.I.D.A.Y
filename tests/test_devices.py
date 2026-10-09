"""
DEVICE-001: one provider-neutral interface for paired physical devices.

Callers talk to the hub; a backend is a DeviceProvider. These tests pin the
three acceptance clauses: the first backend (hermes-gadget-sdk) is a plain
DeviceProvider that reports UNAVAILABLE when the SDK is absent, an absent or
broken backend never breaks the hub, and a second provider is added by
registering it, with no change to the caller.
"""

from __future__ import annotations

import pytest

from friday import fabric
from friday.toolsets import devices


class FakeMqtt(devices.DeviceProvider):
    """A second backend, written only against the interface."""

    id = "mqtt"

    def health(self):
        return {"state": fabric.READY}

    def list_devices(self):
        return [{"id": "lamp-1", "kind": "light"}]

    def read_sensor(self, device_id, sensor):
        return {"device_id": device_id, "sensor": sensor, "value": 21.5}

    def poll_events(self, device_id):
        return [{"device_id": device_id, "event": "motion"}]

    def act(self, device_id, action, params=None):
        return {"device_id": device_id, "action": action, "params": params or {}}


class Broken(FakeMqtt):
    id = "broken"

    def list_devices(self):
        raise RuntimeError("broker down")


def test_hermes_gadget_adapter_is_a_provider_and_unavailable_without_the_sdk():
    adapter = devices.HermesGadgetAdapter(loader=lambda: None)
    assert isinstance(adapter, devices.DeviceProvider)
    assert adapter.id == "hermes-gadget-sdk"
    assert adapter.health()["state"] == fabric.UNAVAILABLE
    with pytest.raises(devices.DeviceUnavailable):
        adapter.list_devices()


def test_hermes_gadget_adapter_delegates_to_an_injected_sdk():
    class Sdk:
        def devices(self):
            return [{"id": "g1"}]

        def read(self, device_id, sensor):
            return {"value": 1}

        def events(self, device_id):
            return []

        def act(self, device_id, action, params):
            return {"ok": True}

    adapter = devices.HermesGadgetAdapter(loader=lambda: Sdk())
    assert adapter.health()["state"] == fabric.READY
    assert adapter.list_devices() == [{"id": "g1"}]
    assert adapter.act("g1", "display.show", {"text": "hi"}) == {"ok": True}


def test_hub_with_only_the_absent_backend_runs_and_reports_it():
    hub = devices.DeviceHub()
    hub.register(devices.HermesGadgetAdapter(loader=lambda: None))
    result = hub.list_devices()
    assert result["devices"] == []
    assert result["providers"]["hermes-gadget-sdk"]["state"] == fabric.UNAVAILABLE


def test_a_second_provider_is_added_without_changing_the_caller():
    hub = devices.DeviceHub()
    hub.register(devices.HermesGadgetAdapter(loader=lambda: None))
    hub.register(FakeMqtt())
    result = hub.list_devices()
    assert result["devices"] == [{"id": "lamp-1", "kind": "light", "provider": "mqtt"}]
    assert hub.read_sensor("mqtt", "lamp-1", "temp")["value"] == 21.5
    assert hub.act("mqtt", "lamp-1", "on")["action"] == "on"
    assert hub.poll_events("mqtt", "lamp-1") == [
        {"device_id": "lamp-1", "event": "motion"}]


def test_a_failing_provider_is_reported_not_raised():
    hub = devices.DeviceHub()
    hub.register(Broken())
    hub.register(FakeMqtt())
    result = hub.list_devices()
    assert result["providers"]["broken"]["state"] == fabric.FAILED
    assert "broker down" in result["providers"]["broken"]["error"]
    assert [d["provider"] for d in result["devices"]] == ["mqtt"]


def test_calling_an_unavailable_or_unknown_provider_is_a_clear_error():
    hub = devices.DeviceHub()
    hub.register(devices.HermesGadgetAdapter(loader=lambda: None))
    with pytest.raises(devices.DeviceUnavailable):
        hub.act("hermes-gadget-sdk", "g1", "on")
    with pytest.raises(devices.DeviceUnavailable):
        hub.act("nope", "g1", "on")


def test_duplicate_provider_id_is_refused():
    hub = devices.DeviceHub()
    hub.register(FakeMqtt())
    with pytest.raises(ValueError):
        hub.register(FakeMqtt())


def test_default_loader_reports_unavailable_when_sdk_not_installed():
    # the real loader, no injection: the SDK is not installed here
    assert devices.HermesGadgetAdapter().health()["state"] == fabric.UNAVAILABLE
