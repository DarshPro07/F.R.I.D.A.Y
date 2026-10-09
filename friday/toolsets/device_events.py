"""
Device events as an automation trigger source (DEVICE-006).

There is no second automation engine: an automation whose trigger is
`{"kind": "device", "device_id": ..., "event": ..., "sensor": optional}` is an
ordinary row in `friday.toolsets.automations`, and `dispatch` fires it through
`automations.execute`, so its steps keep the same allow-list, policy gate,
retries and run history as a scheduled automation.

The gate is the device registry (DEVICE-003): only an ACTIVE device's events
are routed. Anything else - unpaired, awaiting a policy, unknown - is read off
the hub so it does not queue up, then dropped and logged. Event and sensor
payloads are untrusted data; they reach a step only as `{{vars.*}}` text.
"""

from __future__ import annotations

import logging

from friday import policy
from friday.toolsets import automations
from friday.toolsets.device_pairing import Registry
from friday.toolsets.devices import DeviceHub, DeviceUnavailable

logger = logging.getLogger(__name__)


async def dispatch(hub: DeviceHub, registry: Registry, provider_id: str,
                   device_id: str, *,
                   engine: policy.PolicyEngine = policy.default_engine) -> dict:
    """Poll one device and run the automations its events trigger."""
    out: dict = {"device_id": device_id, "trusted": False, "events": 0,
                 "dropped": 0, "routed": []}
    try:
        events = hub.poll_events(provider_id, device_id)
    except DeviceUnavailable as exc:
        logger.warning("device events: %s", exc)
        return {**out, "error": str(exc)}
    out["events"] = len(events)

    out["trusted"] = registry.is_trusted(device_id)
    if not out["trusted"]:
        out["dropped"] = len(events)
        if events:
            logger.warning("device events: dropped %d event(s) from %s, which is "
                           "not an ACTIVE device", len(events), device_id)
        return out

    armed = [a for a in automations.store().automations()
             if a["enabled"] and a["trigger"].get("kind") == "device"
             and a["trigger"].get("device_id") == device_id]
    for event in events:
        if not isinstance(event, dict):
            out["dropped"] += 1
            logger.warning("device events: dropped a malformed event from %s", device_id)
            continue
        for automation in armed:
            trigger = automation["trigger"]
            if event.get("type") != trigger["event"]:
                continue
            variables: dict = {"event": event, "device_id": device_id}
            if trigger.get("sensor"):
                try:
                    variables["sensor"] = hub.read_sensor(
                        provider_id, device_id, trigger["sensor"])
                except Exception as exc:                      # noqa: BLE001
                    logger.warning("device events: sensor %s on %s unreadable: %s",
                                   trigger["sensor"], device_id, exc)
                    out["routed"].append({"automation": automation["name"],
                                          "status": "not_run",
                                          "error": f"sensor unreadable: {exc}"})
                    continue
            key = (f"{automation['name']}@device:{device_id}:{event['id']}"
                   if event.get("id") else "")
            record = await automations.execute(
                automation["name"], variables=variables,
                fired_by=f"device:{device_id}", engine=engine, execution_key=key)
            routed = {"automation": automation["name"], "run_id": record["run_id"],
                      "status": record["status"]}
            if record.get("duplicate_of"):
                routed["duplicate_of"] = record["duplicate_of"]
            out["routed"].append(routed)
    return out
