"""
What this machine physically is: battery, disks, displays, network adapters.

Batch 2A of the donor migration, and it is a build rather than a port. The
audit called `Mark-L/actions/computer_settings.py` "64 entry points into
Windows settings - the deepest OS surface in any donor". Measured: it is
**91 pyautogui calls**. `volume_up()` presses a key. `close_window()` sends
Alt+F4. `snap_left()` sends Win+Left. The parts that are not keystrokes are
subprocess calls to `osascript` and `pactl`, and a ctypes path to Core Audio
that Friday already has through pycaw.

Migrating it would have replaced verifiable capabilities with unverifiable
ones, which is worse than adding nothing:

    pyautogui.press("volumeup")   cannot report what the volume became
    pycaw endpoint volume         reads it back

    Alt+F4                        cannot report whether the app closed
    psutil by pid and create_time can

A keystroke cannot satisfy the proof-of-work contract, because it produces no
observation - only an assumption that something received it. So the third
donor summary in this project turns out to have been wrong about what the
donor is, and the useful part of "system inspection" was never in it.

What was genuinely missing is here: `system_get_info` knew the CPU and total
memory, `system_resource_usage` knew one disk's percentage. Neither knew
whether the machine was on battery, what else was mounted, or what it was
plugged into. All read-only, all through psutil, which is already a
dependency.
"""

from __future__ import annotations

import shutil
import socket
import subprocess

import psutil

from friday import contracts as c
from friday.policy import PolicyEngine, default_engine
from friday.toolsets.system import APPROVAL_PREFIX

EXECUTION_SCOPE = "local_machine"

#: Filesystems that are not the user's storage: loopback mounts, virtual
#: filesystems, and on Windows the empty optical drive that raises rather than
#: reporting zero.
SKIP_FILESYSTEMS = frozenset({"squashfs", "tmpfs", "devtmpfs", "overlay",
                              "proc", "sysfs", "cdrom", "udf", "iso9660"})


def _gate(run: c.Run, tool_id: str, engine: PolicyEngine) -> c.ActionResult | None:
    verdict = engine.decide(tool_id)
    if verdict.allowed:
        return None
    return run.record(c.started(run.run_id, tool_id).finish(
        status=c.CANCELLED,
        error=f"{APPROVAL_PREFIX}: {verdict.reason} [{verdict.decision}]",
    ))


def _scoped(payload: dict) -> dict:
    return {"execution_scope": EXECUTION_SCOPE, **payload}


def _hours(seconds) -> float | None:
    """psutil reports two sentinels; neither is a number of seconds."""
    if seconds in (None, psutil.POWER_TIME_UNLIMITED,
                   psutil.POWER_TIME_UNKNOWN):
        return None
    return round(seconds / 3600, 2)


# ---------------------------------------------------------------------------
# Battery
# ---------------------------------------------------------------------------


def system_battery(
    run: c.Run, *, engine: PolicyEngine = default_engine
) -> c.ActionResult:
    """
    Charge, and whether it is going up or down.

    A desktop has no battery, and that is an answer rather than a failure -
    "this machine does not have one" is what the boss needs to hear, not an
    error about a missing sensor.
    """
    tool_id = "system.battery"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    try:
        battery = psutil.sensors_battery()
    except Exception as exc:                            # noqa: BLE001
        return run.record(c.failed(started, f"could not read the battery: {exc}"))

    if battery is None:
        return run.record(c.succeeded(
            started,
            output=_scoped({"has_battery": False, "percent": None,
                            "plugged_in": None, "hours_left": None}),
            verification=c.Verification(
                method="psutil_sensors_battery",
                evidence="no battery on this machine (desktop or VM)"),
        ))

    hours_left = _hours(battery.secsleft)
    return run.record(c.succeeded(
        started,
        output=_scoped({"has_battery": True,
                        "percent": round(battery.percent, 1),
                        "plugged_in": bool(battery.power_plugged),
                        "hours_left": hours_left}),
        verification=c.Verification(
            method="psutil_sensors_battery",
            evidence=f"{battery.percent:.0f}%, "
                     f"{'charging' if battery.power_plugged else 'on battery'}"
                     + (f", about {hours_left}h left" if hours_left else "")),
    ))


# ---------------------------------------------------------------------------
# Disks
# ---------------------------------------------------------------------------


def system_disks(
    run: c.Run, *, engine: PolicyEngine = default_engine
) -> c.ActionResult:
    """
    Every mounted volume, not just the one the process happens to be on.

    `system_resource_usage` reports a single `disk_percent`, which is the
    volume of the current working directory - so on a machine with the project
    on E: and Windows on C: it answers about whichever one the process started
    in. That is the same class of bug as deriving a database path from the
    working directory, and this is the honest version.
    """
    tool_id = "system.disks"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    volumes = []
    for partition in psutil.disk_partitions(all=False):
        if partition.fstype.lower() in SKIP_FILESYSTEMS:
            continue
        try:
            usage = psutil.disk_usage(partition.mountpoint)
        except (PermissionError, OSError):
            # An empty card reader or unmounted optical drive. It exists and
            # cannot be measured, which is worth saying rather than hiding.
            volumes.append({"mount": partition.mountpoint,
                            "filesystem": partition.fstype,
                            "readable": False})
            continue
        volumes.append({
            "mount": partition.mountpoint, "filesystem": partition.fstype,
            "readable": True,
            "total_gb": round(usage.total / 1024 ** 3, 1),
            "used_gb": round(usage.used / 1024 ** 3, 1),
            "free_gb": round(usage.free / 1024 ** 3, 1),
            "percent_used": usage.percent,
        })

    if not volumes:
        return run.record(c.failed(started, "no readable volumes found"))

    tight = [v for v in volumes if v.get("readable")
             and v["percent_used"] >= 90]
    return run.record(c.succeeded(
        started,
        output=_scoped({"volumes": volumes, "count": len(volumes),
                        "nearly_full": [v["mount"] for v in tight]}),
        verification=c.Verification(
            method="psutil_disk_partitions",
            evidence="; ".join(
                f"{v['mount']} {v.get('free_gb', '?')}GB free"
                for v in volumes[:6])),
    ))


# ---------------------------------------------------------------------------
# Displays
# ---------------------------------------------------------------------------


def _windows_displays() -> list[dict]:
    """
    Monitors, through the API Windows actually answers.

    Deliberately not a new dependency: `EnumDisplayMonitors` is three ctypes
    lines, and a screen count is not worth a package.
    """
    import ctypes
    from ctypes import wintypes

    monitors: list[dict] = []

    class RECT(ctypes.Structure):
        _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                    ("right", wintypes.LONG), ("bottom", wintypes.LONG)]

    callback_type = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC,
        ctypes.POINTER(RECT), wintypes.LPARAM)

    def collect(monitor, _dc, rect_pointer, _lparam):
        rect = rect_pointer.contents
        monitors.append({
            "width": rect.right - rect.left,
            "height": rect.bottom - rect.top,
            "left": rect.left, "top": rect.top,
            "primary": rect.left == 0 and rect.top == 0,
        })
        return True

    ctypes.windll.user32.EnumDisplayMonitors(
        None, None, callback_type(collect), 0)
    return monitors


def system_displays(
    run: c.Run, *, engine: PolicyEngine = default_engine
) -> c.ActionResult:
    """How many screens there are, how big, and which is the primary."""
    tool_id = "system.displays"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    try:
        import sys

        if sys.platform == "win32":
            screens = _windows_displays()
        else:
            return run.record(c.partial(
                started,
                f"display enumeration is only implemented for Windows, and "
                f"this is {sys.platform}",
                output=_scoped({"displays": [], "count": 0})))
    except Exception as exc:                            # noqa: BLE001
        return run.record(c.failed(
            started, f"could not enumerate displays: "
                     f"{type(exc).__name__}: {exc}"))

    if not screens:
        return run.record(c.failed(started, "no displays reported"))
    return run.record(c.succeeded(
        started,
        output=_scoped({"displays": screens, "count": len(screens)}),
        verification=c.Verification(
            method="EnumDisplayMonitors",
            evidence="; ".join(f"{s['width']}x{s['height']}"
                               + (" (primary)" if s["primary"] else "")
                               for s in screens)),
    ))


# ---------------------------------------------------------------------------
# Network adapters
# ---------------------------------------------------------------------------


def system_network(
    run: c.Run, *, engine: PolicyEngine = default_engine
) -> c.ActionResult:
    """
    Which adapters exist, which are up, and what addresses they hold.

    `system_wifi_status` answers about the wireless connection. This answers
    about the machine's connectivity as a whole, which is the question behind
    "why can't you reach anything?" - a VPN adapter up and a physical one down
    look identical from inside a failed request.

    MAC addresses are deliberately excluded. They identify the hardware
    permanently and nothing Friday does needs them.
    """
    tool_id = "system.network"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    try:
        stats = psutil.net_if_stats()
        addresses = psutil.net_if_addrs()
    except Exception as exc:                            # noqa: BLE001
        return run.record(c.failed(started, f"could not read adapters: {exc}"))

    adapters = []
    for name, stat in stats.items():
        ips = [a.address for a in addresses.get(name, [])
               if a.family in (socket.AF_INET, socket.AF_INET6)]
        adapters.append({"name": name, "up": bool(stat.isup),
                         "speed_mbps": stat.speed or None,
                         "addresses": ips})
    adapters.sort(key=lambda a: (not a["up"], a["name"]))
    live = [a for a in adapters if a["up"] and a["addresses"]]

    return run.record(c.succeeded(
        started,
        output=_scoped({"adapters": adapters, "count": len(adapters),
                        "connected": len(live)}),
        verification=c.Verification(
            method="psutil_net_if_stats",
            evidence=f"{len(live)} of {len(adapters)} adapter(s) up with an "
                     f"address: "
                     + ", ".join(a["name"] for a in live[:4])),
    ))
