"""
Friday's own desktop takeover as the coordinates-path computer-use backend
(CUA-001).

This is the backend `friday/toolsets/desktop.py` already acts through:
vision proposes a target and the native input layer
(`friday/platform/windows.py`) clicks at absolute screen coordinates. It is
registered here so the action-path router can choose it, and so a machine
with no display (a service session, a non-Windows host) reports UNAVAILABLE
with the reason.

The probe reads the screen size and nothing else; no input is sent. Moving
the pointer or typing stays behind `desktop_plan` / `desktop_step`, which carry
the policy gate, the confirmation and the emergency stop.

A second desktop backend (Cua Driver) is an adapter beside this one answering
the same `coordinates.probe`; it needs a pinned upstream first.
"""

from __future__ import annotations

from friday import action_path, computer_use, fabric

PATHS = (action_path.COORDINATES,)
PROBE = computer_use.probe_operation(action_path.COORDINATES)


def _native():
    # Imported on use: the registry imports every adapter to read its
    # DESCRIPTOR, and a host that cannot load the input layer must still boot.
    from friday.platform import windows
    return windows


def start():
    native = _native()
    if not native.AVAILABLE:
        raise fabric.FabricError(
            "the native input layer is Windows-only; this host has none")
    return {"native": native}


def stop(handle) -> None:
    return None


def health(handle) -> dict:
    try:
        width, height = handle["native"].screen_size()
    except Exception as exc:                       # noqa: BLE001 - reported
        return {"state": fabric.UNAVAILABLE,
                "detail": f"the screen cannot be read: {type(exc).__name__}: {exc}"}
    if width <= 0 or height <= 0:
        return {"state": fabric.UNAVAILABLE,
                "detail": f"no display is attached ({width}x{height})"}
    return {"state": fabric.READY, "detail": f"{width}x{height} desktop"}


def call(operation: str, handle, **arguments):
    if operation != PROBE:
        raise fabric.FabricError(f"native_desktop has no operation {operation!r}")
    if arguments:
        raise fabric.FabricError(f"{PROBE} takes no arguments, not {sorted(arguments)}")
    width, height = handle["native"].screen_size()
    return {"width": width, "height": height}


DESCRIPTOR = fabric.Provider(
    id="native_desktop",
    family=computer_use.FAMILY,
    upstream="",
    operations=(PROBE,),
    risk="high",
    license_mode=fabric.BUILTIN_LICENSE,
    integration_mode=fabric.BUILTIN,
    cost_class="free",
    notes="Friday's own vision-and-coordinates takeover. Serves the "
          "coordinates path. Exposes a read-only screen probe; input stays in "
          "the policy-gated desktop_* tools.",
)
