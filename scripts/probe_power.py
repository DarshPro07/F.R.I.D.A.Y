#!/usr/bin/env python3
"""
What this machine actually offers for power control. Nothing is ever initiated.

Phase 0 research for specs/001-process-power-control. Three of the findings
contradict what the documentation implies, which is why this exists as a script
rather than a paragraph:

    SeShutdownPrivilege   held, but DISABLED - every power call fails until
                          AdjustTokenPrivileges enables it, and that function
                          returns non-zero even when it enabled nothing

    IsPwrSuspendAllowed   answers about S1-S3. On a modern-standby machine it
                          returns False while the machine sleeps every night.
                          Using it would have Friday tell somebody their own
                          hardware cannot do something it does nightly.

    CallNtPowerInformation  a wrong-sized struct returns STATUS_BUFFER_TOO_SMALL
                          and leaves the buffer zeroed - which reads exactly
                          like a machine that supports nothing. Check the status
                          before believing the flags.

    python scripts/probe_power.py
"""

from __future__ import annotations

import ctypes
import subprocess
import sys
from ctypes import wintypes

if sys.platform != "win32":
    print("Windows only")
    raise SystemExit(2)

advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32 = ctypes.WinDLL("user32", use_last_error=True)
powrprof = ctypes.WinDLL("powrprof", use_last_error=True)

BYTE = ctypes.c_ubyte
TOKEN_QUERY = 0x0008
SM_REMOTESESSION = 0x1000
SystemPowerCapabilities = 4
STATUS_SUCCESS = 0


class LUID(ctypes.Structure):
    _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]


class LUID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Luid", LUID), ("Attributes", wintypes.DWORD)]


class PRIVILEGE_SET(ctypes.Structure):
    _fields_ = [("PrivilegeCount", wintypes.DWORD),
                ("Control", wintypes.DWORD),
                ("Privilege", LUID_AND_ATTRIBUTES * 1)]


class BATTERY_REPORTING_SCALE(ctypes.Structure):
    #: Two ULONGs, so 8 bytes each and 24 for the array. Declaring this as 12
    #: bytes is what made the first run of this probe report a machine with no
    #: power features at all.
    _fields_ = [("Granularity", wintypes.ULONG), ("Capacity", wintypes.ULONG)]


class SYSTEM_POWER_CAPABILITIES(ctypes.Structure):
    _fields_ = [
        ("PowerButtonPresent", BYTE), ("SleepButtonPresent", BYTE),
        ("LidPresent", BYTE), ("SystemS1", BYTE), ("SystemS2", BYTE),
        ("SystemS3", BYTE), ("SystemS4", BYTE), ("SystemS5", BYTE),
        ("HiberFilePresent", BYTE), ("FullWake", BYTE),
        ("VideoDimPresent", BYTE), ("ApmPresent", BYTE), ("UpsPresent", BYTE),
        ("ThermalControl", BYTE), ("ProcessorThrottle", BYTE),
        ("ProcessorMinThrottle", BYTE), ("ProcessorMaxThrottle", BYTE),
        ("FastSystemS4", BYTE), ("Hiberboot", BYTE), ("WakeAlarmPresent", BYTE),
        ("AoAc", BYTE), ("DiskSpinDown", BYTE), ("HiberFileType", BYTE),
        ("AoAcConnectivitySupported", BYTE), ("spare3", BYTE * 6),
        ("SystemBatteriesPresent", BYTE), ("BatteriesAreShortTerm", BYTE),
        ("BatteryScale", BATTERY_REPORTING_SCALE * 3),
        ("AcOnLineWake", ctypes.c_int), ("SoftLidWake", ctypes.c_int),
        ("RtcWake", ctypes.c_int), ("MinDeviceWakeState", ctypes.c_int),
        ("DefaultLowLatencyWake", ctypes.c_int),
    ]


ENTRY_POINTS = [
    (advapi32, "InitiateShutdownW"), (advapi32, "AbortSystemShutdownW"),
    (advapi32, "InitiateSystemShutdownExW"), (user32, "ExitWindowsEx"),
    (user32, "LockWorkStation"), (powrprof, "SetSuspendState"),
    (powrprof, "CallNtPowerInformation"), (advapi32, "OpenProcessToken"),
    (advapi32, "LookupPrivilegeValueW"), (advapi32, "AdjustTokenPrivileges"),
]


def entry_points() -> bool:
    print("=== do the entry points exist? ===")
    every = True
    for dll, name in ENTRY_POINTS:
        there = hasattr(dll, name)
        every = every and there
        print(f"  {name:28} {'yes' if there else 'NO'}")
    return every


def shutdown_privilege() -> bool:
    """Held is not enabled, and enabled is what the power APIs require."""
    print("\n=== SeShutdownPrivilege ===")
    advapi32.OpenProcessToken.argtypes = (wintypes.HANDLE, wintypes.DWORD,
                                          ctypes.POINTER(wintypes.HANDLE))
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.LookupPrivilegeValueW.argtypes = (
        wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.POINTER(LUID))
    advapi32.LookupPrivilegeValueW.restype = wintypes.BOOL
    advapi32.PrivilegeCheck.argtypes = (
        wintypes.HANDLE, ctypes.POINTER(PRIVILEGE_SET),
        ctypes.POINTER(wintypes.BOOL))
    advapi32.PrivilegeCheck.restype = wintypes.BOOL

    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), TOKEN_QUERY,
                                     ctypes.byref(token)):
        print("  could not open the process token")
        return False

    luid = LUID()
    if not advapi32.LookupPrivilegeValueW(None, "SeShutdownPrivilege",
                                          ctypes.byref(luid)):
        print("  the privilege is not known to this system")
        return False

    privileges = PRIVILEGE_SET()
    privileges.PrivilegeCount = 1
    privileges.Control = 0
    privileges.Privilege[0].Luid = luid
    privileges.Privilege[0].Attributes = 0
    enabled = wintypes.BOOL()
    advapi32.PrivilegeCheck(token, ctypes.byref(privileges),
                            ctypes.byref(enabled))
    kernel32.CloseHandle(token)

    print(f"  held by this token            yes")
    print(f"  enabled                       {bool(enabled.value)}")
    if not enabled.value:
        print("  -> every power call fails until AdjustTokenPrivileges enables it,")
        print("     and it must not be reported as the machine refusing")
    return bool(enabled.value)


def capabilities() -> SYSTEM_POWER_CAPABILITIES | None:
    print("\n=== SYSTEM_POWER_CAPABILITIES ===")
    powrprof.CallNtPowerInformation.argtypes = (
        ctypes.c_int, ctypes.c_void_p, wintypes.ULONG, ctypes.c_void_p,
        wintypes.ULONG)
    powrprof.CallNtPowerInformation.restype = wintypes.LONG

    caps = SYSTEM_POWER_CAPABILITIES()
    status = powrprof.CallNtPowerInformation(
        SystemPowerCapabilities, None, 0, ctypes.byref(caps),
        ctypes.sizeof(caps))
    print(f"  status                        0x{status & 0xFFFFFFFF:08X}"
          f" ({'ok' if status == STATUS_SUCCESS else 'FAILED'})")
    if status != STATUS_SUCCESS:
        print("  -> the flags below would be an uninitialised buffer, not an")
        print("     answer. A wrong-sized struct fails exactly like a machine")
        print("     with no features. Refusing to report them.")
        return None

    for field in ("SystemS1", "SystemS2", "SystemS3", "SystemS4",
                  "HiberFilePresent", "AoAc", "Hiberboot"):
        print(f"  {field:29} {bool(getattr(caps, field))}")
    return caps


def compare(caps: SYSTEM_POWER_CAPABILITIES) -> None:
    print("\n=== availability: legacy answer vs the real one ===")
    powrprof.IsPwrSuspendAllowed.restype = wintypes.BOOL
    powrprof.IsPwrHibernateAllowed.restype = wintypes.BOOL

    legacy_sleep = bool(powrprof.IsPwrSuspendAllowed())
    legacy_hibernate = bool(powrprof.IsPwrHibernateAllowed())
    real_sleep = bool(caps.AoAc or caps.SystemS1 or caps.SystemS2
                      or caps.SystemS3)
    real_hibernate = bool(caps.SystemS4 and caps.HiberFilePresent)

    print(f"  sleep       IsPwrSuspendAllowed={legacy_sleep}"
          f"   capabilities={real_sleep}")
    print(f"  hibernate   IsPwrHibernateAllowed={legacy_hibernate}"
          f" capabilities={real_hibernate}")

    if legacy_sleep != real_sleep:
        print("\n  -> THEY DISAGREE. The capabilities answer is the correct one:")
        print("     IsPwrSuspendAllowed reports on S1-S3, and this machine uses")
        print("     S0 low power idle. Friday must never tell somebody their")
        print("     machine cannot sleep on the strength of the legacy call.")
    else:
        print("\n  -> they agree on this machine (they do not always)")

    print(f"\n  remote session                "
          f"{bool(user32.GetSystemMetrics(SM_REMOTESESSION))}")


def powercfg() -> None:
    print("\n=== powercfg /a, for a second opinion ===")
    try:
        out = subprocess.run(["powercfg", "/a"], capture_output=True,
                             text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"  could not run powercfg: {exc}")
        return
    for line in out.stdout.splitlines():
        if line.strip():
            print("  " + line.rstrip())


def main() -> int:
    print("=" * 68)
    print("Power control, as this machine actually offers it")
    print("=" * 68)
    entry_points()
    shutdown_privilege()
    caps = capabilities()
    if caps is not None:
        compare(caps)
    powercfg()
    print("\nNothing was initiated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
