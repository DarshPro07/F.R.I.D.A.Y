"""
DEVICE-006: device events and sensor values reach the existing automations
engine as a trigger source, and only an ACTIVE device's events do.

No real backend exists (hermes-gadget-sdk is not pinned), so the hub is the
in-memory fake below.
"""
from __future__ import annotations

import asyncio
import json
import logging

import pytest

from friday import contracts as c
from friday import fabric
from friday.store import Store
from friday.toolsets import automations as A
from friday.toolsets import device_events as de
from friday.toolsets import device_pairing as dp
from friday.toolsets import devices

LAN = "ws://192.168.1.20:8765"


class FakeSensorHub(devices.DeviceProvider):
    id = "fake"

    def __init__(self):
        self.queue: dict[str, list[dict]] = {}
        self.polled: list[str] = []

    def health(self):
        return {"state": fabric.READY}

    def list_devices(self):
        return []

    def read_sensor(self, device_id, sensor):
        return {"device_id": device_id, "sensor": sensor, "value": 21.5}

    def poll_events(self, device_id):
        self.polled.append(device_id)
        return self.queue.pop(device_id, [])

    def act(self, device_id, action, params=None):
        return {}


@pytest.fixture(autouse=True)
def store(tmp_path):
    fresh = Store(tmp_path / "test.db")
    A.reset_store(fresh)
    yield fresh
    A.reset_store(None)
    fresh.close()


@pytest.fixture
def seen(monkeypatch):
    calls: list[dict] = []

    def fake(run, *, engine=None, **args):
        calls.append(args)
        return c.succeeded(c.started(run.run_id, "web.news"), output={"ok": True},
                           verification=c.Verification(method="fake", evidence="ran"))

    monkeypatch.setattr(A, "TOOLS", {"web.news": fake})
    return calls


@pytest.fixture
def world(store):
    provider = FakeSensorHub()
    hub = devices.DeviceHub()
    hub.register(provider)
    registry = dp.Registry(store)
    return hub, registry, provider


def activate(registry, name="hall sensor"):
    asked = registry.request_pairing(name, LAN)
    device_id = asked["device_id"]
    registry.approve(device_id, asked["code"], approved_by="owner")
    registry.record_inventory(device_id, ["sensor.temperature", "event.motion"])
    registry.assign_policy(device_id, "read_only")
    return device_id


def define(device_id, **trigger):
    return A.automations_create(
        c.Run.create("t", capability="automation"), "someone-entered",
        json.dumps({"kind": "device", "device_id": device_id, "event": "motion",
                    **trigger}),
        json.dumps([{"id": "a", "tool": "web.news",
                     "args": {"query": "{{vars.event.who}} t={{vars.sensor.value}}"}}]))


def dispatch(world, device_id):
    hub, registry, _ = world
    return asyncio.run(de.dispatch(hub, registry, "fake", device_id))


def test_device_trigger_is_stored_without_a_scheduled_task(world, seen):
    device_id = activate(world[1])
    made = define(device_id)
    assert made.status == c.SUCCEEDED, made.error
    assert made.output["task_name"] is None
    assert A.store().get_automation("someone-entered")["trigger"]["kind"] == "device"


def test_a_device_trigger_needs_a_device_and_an_event():
    run = c.Run.create("t", capability="automation")
    bad = A.automations_create(run, "x", json.dumps({"kind": "device", "event": "motion"}),
                               json.dumps([{"id": "a", "tool": "web.news", "args": {}}]))
    assert bad.status == c.FAILED and "device_id" in bad.error


def test_an_event_and_a_sensor_value_from_an_active_device_reach_the_routine(world, seen):
    device_id = activate(world[1])
    define(device_id, sensor="temperature")
    world[2].queue[device_id] = [{"id": "e1", "type": "motion", "who": "someone"}]

    out = dispatch(world, device_id)

    assert out["trusted"] and out["dropped"] == 0
    assert [r["automation"] for r in out["routed"]] == ["someone-entered"]
    assert out["routed"][0]["status"] == c.SUCCEEDED
    assert seen == [{"query": "someone t=21.5"}]
    runs = A.store().automation_history("someone-entered")
    assert runs and runs[0]["fired_by"] == f"device:{device_id}"


def test_an_event_of_another_type_or_device_does_not_fire(world, seen):
    device_id = activate(world[1])
    other = activate(world[1], "other")
    define(device_id)
    world[2].queue[device_id] = [{"id": "e1", "type": "door"}]
    world[2].queue[other] = [{"id": "e2", "type": "motion"}]

    assert dispatch(world, device_id)["routed"] == []
    assert dispatch(world, other)["routed"] == []
    assert seen == []


def test_the_same_event_id_runs_the_routine_once(world, seen):
    device_id = activate(world[1])
    define(device_id)
    world[2].queue[device_id] = [{"id": "e1", "type": "motion", "who": "a"}]
    dispatch(world, device_id)
    world[2].queue[device_id] = [{"id": "e1", "type": "motion", "who": "a"}]
    again = dispatch(world, device_id)
    assert len(seen) == 1
    assert again["routed"][0].get("duplicate_of")


@pytest.mark.parametrize("stage", ["unpaired", "paired_unverified"])
def test_events_from_an_untrusted_device_are_dropped_and_logged(world, seen, caplog, stage):
    hub, registry, provider = world
    asked = registry.request_pairing("intruder", LAN)
    device_id = asked["device_id"]
    if stage == "paired_unverified":
        registry.approve(device_id, asked["code"], approved_by="owner")
    define(device_id)
    provider.queue[device_id] = [{"id": "e1", "type": "motion"}]

    with caplog.at_level(logging.WARNING):
        out = dispatch(world, device_id)

    assert out["trusted"] is False and out["dropped"] == 1 and out["routed"] == []
    assert seen == []
    assert "dropped" in caplog.text and device_id in caplog.text


def test_an_unknown_device_is_dropped_not_raised(world, seen):
    hub, registry, provider = world
    provider.queue["ghost"] = [{"id": "e1", "type": "motion"}]
    out = dispatch(world, "ghost")
    assert out["trusted"] is False and out["dropped"] == 1 and seen == []


def test_an_unavailable_provider_is_reported_not_raised(world):
    hub, registry, _ = world
    out = asyncio.run(de.dispatch(hub, registry, "nope", "dev_x"))
    assert out["routed"] == [] and "unknown device provider" in out["error"]
