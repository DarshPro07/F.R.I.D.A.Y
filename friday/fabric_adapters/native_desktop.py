"""
Friday's own desktop takeover as the coordinates-path computer-use backend
(CUA-001).

This is the backend `friday/toolsets/desktop.py` already acts through:
vision proposes a target and the native input layer
(`friday/platform/windows.py`) clicks at absolute screen coordinates. It is
registered here so the action-path router can choose it, and so a machine
with no display (a service session, a non-Windows host) reports UNAVAILABLE
with the reason.

`coordinates.probe` reads the screen size and nothing else; no input is sent.
Moving the pointer or typing stays behind `desktop_plan` / `desktop_step`,
which carry the policy gate, the confirmation and the emergency stop.

Readiness is earned (CUA-005). A display is only the first step: until
`coordinates.readiness` has run (`desktop_readiness.probe`: signature check,
launch, window enumeration, a close and the observed post-state) the backend
is DEGRADED, and a failed step makes it UNAVAILABLE with its named state, so
the router skips it. That operation starts a program on the user's desktop, so
it needs the `desktop.readiness_probe` grant; the screen probe stays open.

A second desktop backend (Cua Driver) is an adapter beside this one answering
the same `coordinates.probe`; it needs a pinned upstream first.
"""

from __future__ import annotations

import dataclasses

from friday import action_path, computer_use, desktop_readiness, fabric

PATHS = (action_path.COORDINATES,)
PROBE = computer_use.probe_operation(action_path.COORDINATES)
READINESS = f"{action_path.COORDINATES}.readiness"
PERMISSION = "desktop.readiness_probe"

#: Bounds on the one caller-supplied argument. The target program is not
#: caller-supplied: a readiness probe that starts a chosen program is a launcher.
_MIN_TIMEOUT_MS, _MAX_TIMEOUT_MS = 1_000, 60_000


def _native():
    # Imported on use: the registry imports every adapter to read its
    # DESCRIPTOR, and a host that cannot load the input layer must still boot.
    from friday.platform import windows
    return windows


def _layer():
    return desktop_readiness.NativeLayer()


def start():
    native = _native()
    if not native.AVAILABLE:
        raise fabric.FabricError(
            "the native input layer is Windows-only; this host has none")
    return {"native": native, "readiness": None}


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
    readiness = handle["readiness"]
    if readiness is None:
        return {"state": fabric.DEGRADED,
                "detail": f"{width}x{height} desktop; no real action has been "
                          f"observed yet - not probed yet (call {READINESS})"}
    if readiness.state == desktop_readiness.DESKTOP_READY:
        return {"state": fabric.READY,
                "detail": f"{width}x{height} desktop; {readiness.detail}"}
    return {"state": fabric.UNAVAILABLE,
            "detail": f"{readiness.state}: {readiness.detail}"}


def call(operation: str, handle, **arguments):
    if operation == READINESS:
        unknown = set(arguments) - {"timeout_ms"}
        if unknown:
            raise fabric.FabricError(
                f"{READINESS} takes only timeout_ms, not {sorted(unknown)}")
        try:
            timeout_ms = int(arguments.get("timeout_ms", 10_000))
        except (TypeError, ValueError):
            raise fabric.FabricError(
                f"timeout_ms must be an integer, got {arguments['timeout_ms']!r}") from None
        if not _MIN_TIMEOUT_MS <= timeout_ms <= _MAX_TIMEOUT_MS:
            raise fabric.FabricError(
                f"timeout_ms must be {_MIN_TIMEOUT_MS}..{_MAX_TIMEOUT_MS}, "
                f"got {timeout_ms}")
        readiness = desktop_readiness.probe(_layer(), timeout_ms=timeout_ms)
        handle["readiness"] = readiness
        return dataclasses.asdict(readiness)
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
    operations=(PROBE, READINESS),
    risk="high",
    permissions=(PERMISSION,),
    open_operations=(PROBE,),
    license_mode=fabric.BUILTIN_LICENSE,
    integration_mode=fabric.BUILTIN,
    cost_class="free",
    notes="Friday's own vision-and-coordinates takeover. Serves the "
          "coordinates path. Exposes a read-only screen probe; input stays in "
          "the policy-gated desktop_* tools. The readiness operation starts a fixed "
          "harmless program and needs the desktop.readiness_probe grant.",
)
