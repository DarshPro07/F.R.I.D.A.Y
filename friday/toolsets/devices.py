"""
Paired physical devices behind one provider-neutral interface (DEVICE-001).

A device exposes sensors, events, displays, audio and actions. Callers talk to
`DeviceHub`; a backend is a `DeviceProvider`. Displays and audio are actions
(`display.show`, `audio.play`), so the interface stays four verbs wide and a
second backend (MQTT, Home Assistant) is written against it by registering
with the hub - no caller changes.

An absent backend is a state, not an error: `HermesGadgetAdapter` reports
UNAVAILABLE when its SDK is not installed, and the hub keeps working with
whatever else is registered (NON_NEGOTIABLE 15).
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Callable

from friday import fabric

logger = logging.getLogger(__name__)


class DeviceUnavailable(RuntimeError):
    """The named provider is unknown, absent, or not answering."""


class DeviceProvider(ABC):
    """What a device backend must answer. Nothing here is Hermes-specific."""

    id: str

    @abstractmethod
    def health(self) -> dict:
        """`{"state": fabric.READY | fabric.UNAVAILABLE | ...}` plus detail."""

    @abstractmethod
    def list_devices(self) -> list[dict]:
        """Paired devices, each a dict with at least an `id`."""

    @abstractmethod
    def read_sensor(self, device_id: str, sensor: str) -> dict:
        ...

    @abstractmethod
    def poll_events(self, device_id: str) -> list[dict]:
        ...

    @abstractmethod
    def act(self, device_id: str, action: str, params: dict | None = None) -> dict:
        ...


#: Assumed import name of Adolanium/hermes-gadget-sdk. The upstream is not
#: cloned or pinned yet, so this and the client surface below are unverified.
SDK_MODULE = "hermes_gadget_sdk"


def _load_sdk():
    """The SDK client, or None when it is not installed."""
    try:
        import importlib
        return importlib.import_module(SDK_MODULE)
    except ImportError:
        return None


class HermesGadgetAdapter(DeviceProvider):
    """First backend: hermes-gadget-sdk. `loader` is the injectable seam."""

    id = "hermes-gadget-sdk"

    def __init__(self, loader: Callable[[], object | None] = _load_sdk):
        self._loader = loader

    def _sdk(self):
        sdk = self._loader()
        if sdk is None:
            raise DeviceUnavailable(f"{self.id}: {SDK_MODULE} is not installed")
        return sdk

    def health(self) -> dict:
        if self._loader() is None:
            return {"state": fabric.UNAVAILABLE,
                    "detail": f"{SDK_MODULE} is not installed"}
        return {"state": fabric.READY}

    def list_devices(self) -> list[dict]:
        return list(self._sdk().devices())

    def read_sensor(self, device_id: str, sensor: str) -> dict:
        return self._sdk().read(device_id, sensor)

    def poll_events(self, device_id: str) -> list[dict]:
        return list(self._sdk().events(device_id))

    def act(self, device_id: str, action: str, params: dict | None = None) -> dict:
        return self._sdk().act(device_id, action, params or {})


class DeviceHub:
    """The one thing callers use. Holds providers; never raises on boot."""

    def __init__(self) -> None:
        self._providers: dict[str, DeviceProvider] = {}

    def register(self, provider: DeviceProvider) -> None:
        if provider.id in self._providers:
            raise ValueError(f"device provider {provider.id!r} already registered")
        self._providers[provider.id] = provider

    def _provider(self, provider_id: str) -> DeviceProvider:
        try:
            return self._providers[provider_id]
        except KeyError:
            raise DeviceUnavailable(
                f"unknown device provider {provider_id!r}") from None

    def list_devices(self) -> dict:
        """Every device from every answering provider, plus each one's state."""
        found: list[dict] = []
        states: dict[str, dict] = {}
        for provider in self._providers.values():
            health = provider.health()
            states[provider.id] = health
            if health.get("state") not in (fabric.READY, fabric.DEGRADED):
                continue
            try:
                for device in provider.list_devices():
                    found.append({**device, "provider": provider.id})
            except Exception as exc:                         # noqa: BLE001
                logger.warning("device provider %s failed: %s", provider.id, exc)
                states[provider.id] = {"state": fabric.FAILED, "error": str(exc)}
        return {"devices": found, "providers": states}

    def _ready(self, provider_id: str) -> DeviceProvider:
        provider = self._provider(provider_id)
        state = provider.health().get("state")
        if state not in (fabric.READY, fabric.DEGRADED):
            raise DeviceUnavailable(f"{provider_id} is {state}")
        return provider

    def read_sensor(self, provider_id: str, device_id: str, sensor: str) -> dict:
        return self._ready(provider_id).read_sensor(device_id, sensor)

    def poll_events(self, provider_id: str, device_id: str) -> list[dict]:
        return self._ready(provider_id).poll_events(device_id)

    def act(self, provider_id: str, device_id: str, action: str,
            params: dict | None = None) -> dict:
        return self._ready(provider_id).act(device_id, action, params)
