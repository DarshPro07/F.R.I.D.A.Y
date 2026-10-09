"""
Device actions as registered capabilities (DEVICE-002).

A device may advertise any action it likes at run time. None of that reaches
the model, or a provider's `act`, until FRIDAY has registered the action here
under a policy category it chose: `relay.set` becomes the capability
`hardware.relay.set`, entered in `policy.TOOL_CATEGORIES` - the one registry
the `PolicyEngine` reads. The advertisement carries no category parameter, so
a device cannot pick its own.
"""

from __future__ import annotations

import re

from friday import policy
from friday.toolsets.devices import DeviceHub

PREFIX = "hardware."
_ACTION = re.compile(r"[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+")


class UnregisteredCapability(policy.PolicyError):
    """The action was advertised by a device but FRIDAY never registered it."""


def _normalise(action: str) -> str:
    return (action or "").strip().lower()


class DeviceCapabilityRegistry:
    def __init__(self) -> None:
        self._actions: dict[str, str] = {}

    def register(self, action: str, category: str) -> str:
        """Register `action` under `category`; return its capability id."""
        name = _normalise(action)
        if not _ACTION.fullmatch(name) or name.startswith(PREFIX):
            raise ValueError(f"not a device action name: {action!r}")
        if category not in policy.DEFAULT_POLICY:
            raise ValueError(f"unknown policy category: {category!r}")
        existing = self._actions.get(name)
        if existing is not None and existing != category:
            raise ValueError(
                f"{name} is already registered as {existing}; a category is not changed")
        self._actions[name] = category
        policy.TOOL_CATEGORIES[PREFIX + name] = category
        return PREFIX + name

    def model_view(self, advertised: list[dict]) -> list[dict]:
        """What the model may see of `DeviceHub.list_devices()["devices"]`."""
        seen: list[dict] = []
        for device in advertised:
            for entry in device.get("actions") or []:
                name = _normalise(entry.get("name", "") if isinstance(entry, dict) else "")
                if name in self._actions:
                    seen.append({
                        "capability": PREFIX + name, "action": name,
                        "category": self._actions[name],
                        "provider": device.get("provider"), "device_id": device.get("id"),
                    })
        return seen

    def act(self, hub: DeviceHub, provider_id: str, device_id: str, action: str,
            params: dict | None = None, *,
            engine: policy.PolicyEngine | None = None) -> dict:
        """Run a registered action; anything else is refused before the provider."""
        name = _normalise(action)
        if name not in self._actions:
            raise UnregisteredCapability(
                f"{action!r} is not a registered capability; a device advertising "
                "it does not make it callable")
        verdict = (engine or policy.default_engine).decide(PREFIX + name)
        if not verdict.allowed:
            raise policy.PolicyError(f"{verdict.tool_id}: {verdict.reason}")
        return hub.act(provider_id, device_id, name, params)
