"""
The machine FRIDAY runs on, measured (PRD v7 RES-002).

Amendment A4 makes two machines first-class reference targets - REFERENCE-A
(15.4 GB RAM, an integrated GPU with about 0.5 GB of dedicated video memory,
its operating system idling near 76% of RAM) and REFERENCE-B (16 GB RAM, 4 GB
of dedicated video memory) - and tunes FRIDAY to neither. Nothing here knows a
machine by name, hostname or tier. RAM, the idle baseline, CPUs, GPUs and
their dedicated memory, disk and power are measured; a value the platform does
not report is None, never guessed; and the policy FRIDAY runs with is derived
from the measurements, so a third machine is profiled the same way.

`qualify()` measures, derives and stores the profile in the state root
(`python -m friday.hardware_profile qualify` from a shell). The resource
governor judges pressure against its idle baseline (RES-003) and takes its
caps from the policy. A stored profile whose hardware no longer matches this
machine is not used: the governor then profiles the machine from its own
readings until it is measured again (RES-004).
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import logging
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from friday import governor as G
from friday.config import DATA_DIR

logger = logging.getLogger("friday.hardware_profile")

PROFILE_PATH = Path(DATA_DIR) / "hardware_profile.json"

#: Amendment A3: an individual default local model artifact stays below 2 GB.
MODEL_ARTIFACT_CAP_GB = 2.0


@dataclass(frozen=True)
class GPU:
    name: str
    vendor: str                  # PCI vendor id, e.g. "0x1002"
    dedicated_gb: float          # dedicated video memory, as the driver reports it
    shared_gb: float             # system memory it may borrow


def gpus() -> list[GPU] | None:
    """The hardware GPUs Direct3D would use, with their memory, on Windows
    (DXGI); the software renderer is left out. None where the platform does
    not say - never a guess."""
    if sys.platform != "win32":
        return None
    try:
        return _dxgi_adapters()
    except OSError as exc:
        logger.warning("GPU enumeration failed: %s", exc)
        return None


def _dxgi_adapters() -> list[GPU]:
    import ctypes
    from ctypes import wintypes

    class Guid(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                    ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

    class Luid(ctypes.Structure):
        _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]

    class AdapterDesc1(ctypes.Structure):
        _fields_ = [("Description", wintypes.WCHAR * 128), ("VendorId", wintypes.UINT),
                    ("DeviceId", wintypes.UINT), ("SubSysId", wintypes.UINT),
                    ("Revision", wintypes.UINT), ("DedicatedVideoMemory", ctypes.c_size_t),
                    ("DedicatedSystemMemory", ctypes.c_size_t),
                    ("SharedSystemMemory", ctypes.c_size_t), ("AdapterLuid", Luid),
                    ("Flags", wintypes.UINT)]

    iid_factory1 = Guid(0x770AAE78, 0xF26F, 0x4DBA,
                        (ctypes.c_ubyte * 8)(0xA8, 0x29, 0x25, 0x3C, 0x83, 0xD1, 0xB3, 0x87))
    not_found = ctypes.c_long(0x887A0002).value      # DXGI_ERROR_NOT_FOUND: no more adapters
    software = 0x2                                    # DXGI_ADAPTER_FLAG_SOFTWARE

    def method(obj: ctypes.c_void_p, index: int, *argtypes):
        table = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        return ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)(table[index])

    def check(hr: int, what: str) -> None:
        if hr != 0:
            raise OSError(f"DXGI {what} failed: 0x{hr & 0xFFFFFFFF:08X}")

    dxgi = ctypes.WinDLL("dxgi")
    dxgi.CreateDXGIFactory1.restype = ctypes.c_long
    dxgi.CreateDXGIFactory1.argtypes = [ctypes.POINTER(Guid), ctypes.POINTER(ctypes.c_void_p)]
    factory = ctypes.c_void_p()
    check(dxgi.CreateDXGIFactory1(ctypes.byref(iid_factory1), ctypes.byref(factory)),
          "CreateDXGIFactory1")
    found: list[GPU] = []
    try:
        index = 0
        while True:
            adapter = ctypes.c_void_p()
            # IDXGIFactory1::EnumAdapters1 is vtable slot 12; Release is 2.
            hr = method(factory, 12, wintypes.UINT, ctypes.POINTER(ctypes.c_void_p))(
                factory, index, ctypes.byref(adapter))
            if hr == not_found:
                break
            check(hr, "EnumAdapters1")
            try:
                desc = AdapterDesc1()
                # IDXGIAdapter1::GetDesc1 is vtable slot 10.
                check(method(adapter, 10, ctypes.POINTER(AdapterDesc1))(adapter, ctypes.byref(desc)),
                      "GetDesc1")
                if not desc.Flags & software:
                    found.append(GPU(name=desc.Description, vendor=f"0x{desc.VendorId:04x}",
                                     dedicated_gb=desc.DedicatedVideoMemory / 1024**3,
                                     shared_gb=desc.SharedSystemMemory / 1024**3))
            finally:
                method(adapter, 2)(adapter)
            index += 1
    finally:
        method(factory, 2)(factory)
    return found


def measure_machine() -> dict:
    """What this machine has, as the platform reports it: RAM, CPUs, GPUs,
    the state root's disk, and the battery (None when there is none)."""
    import psutil

    try:
        disk = psutil.disk_usage(str(Path(DATA_DIR).resolve().anchor))
        disk_free, disk_total = disk.free / 1024**3, disk.total / 1024**3
    except OSError:
        disk_free = disk_total = None
    battery = psutil.sensors_battery() if hasattr(psutil, "sensors_battery") else None
    return {"platform": sys.platform,
            "ram_total_gb": psutil.virtual_memory().total / 1024**3,
            "cpu_logical": psutil.cpu_count(logical=True),
            "cpu_physical": psutil.cpu_count(logical=False),
            "gpus": gpus(), "disk_free_gb": disk_free, "disk_total_gb": disk_total,
            "battery": None if battery is None else {"percent": battery.percent,
                                                     "plugged": battery.power_plugged}}


@dataclass
class Profile:
    measured_at: str
    cause: str                       # why it was taken: a first run, a change, the owner
    platform: str
    ram_total_gb: float
    baseline: G.Baseline             # the machine at rest (RES-003)
    cpu_logical: int | None
    cpu_physical: int | None
    gpus: list[GPU] | None
    disk_free_gb: float | None
    disk_total_gb: float | None
    battery: dict | None
    browser_mb: float | None = None  # one headless Chromium (BROW-004's probe), when measured
    policy: dict = field(default_factory=dict)

    def identity(self) -> dict:
        """What makes this machine this machine, for the profile: RAM, CPUs
        and GPUs. Power is not identity - it changes by the hour."""
        return identity_of(self.ram_total_gb, self.cpu_logical, self.gpus)

    def fingerprint(self) -> str:
        text = json.dumps(self.identity(), sort_keys=True)
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]

    def to_dict(self) -> dict:
        return {**asdict(self), "fingerprint": self.fingerprint()}

    @classmethod
    def from_dict(cls, raw: dict) -> "Profile":
        fields = {f.name for f in dataclasses.fields(cls)}
        data = {k: v for k, v in raw.items() if k in fields}
        data["baseline"] = G.Baseline(**data["baseline"])
        if data.get("gpus") is not None:
            data["gpus"] = [GPU(**g) for g in data["gpus"]]
        return cls(**data)


def identity_of(ram_total_gb: float, cpu_logical: int | None, found: list[GPU] | None) -> dict:
    return {"ram_total_gb": round(ram_total_gb, 1), "cpu_logical": cpu_logical,
            "gpus": None if found is None else sorted([g.name, round(g.dedicated_gb, 1)] for g in found)}


def derive_policy(profile: Profile, thresholds: G.Thresholds | None = None) -> dict:
    """What FRIDAY runs with on this machine, from the measurements.

    The room FRIDAY's own work may take is the share of the idle headroom it
    can use while the machine still reads GREEN (RES-003's YELLOW line): about
    1.5 GB on a machine with 3.7 GB free at rest, 3.2 GB on one with 8 GB.
    Workers keep FR-013's bound until a worker's memory is measured; browsers
    are as many as the room holds at the measured cost of one, within the
    governor's cap and never fewer than one; a local model may take the room,
    within Amendment A3's 2 GB per artifact, and may be offloaded to a GPU only
    as far as the GPU's own dedicated memory goes."""
    t = thresholds or G.Thresholds()
    room_gb = t.ram_yellow * profile.baseline.ram_available_gb
    if profile.browser_mb:
        fit = int(room_gb * 1024 // profile.browser_mb)
        browsers = {"max": max(1, min(t.max_browsers, fit)),
                    "basis": f"{fit} of {profile.browser_mb:.0f} MB fit in {room_gb:.2f} GB; "
                             f"governor cap {t.max_browsers}"}
    else:
        browsers = {"max": t.max_browsers,
                    "basis": "the governor's cap; one browser's memory not measured"}
    if profile.gpus is None:
        vram = None
    else:
        vram = max((g.dedicated_gb for g in profile.gpus), default=0.0)
    return {
        "room_gb": round(room_gb, 2),
        "workers": {"max": t.max_workers,
                    "basis": "FR-013's bound; a worker's memory is not measured yet"},
        "browsers": browsers,
        "model": {"ram_budget_gb": round(min(MODEL_ARTIFACT_CAP_GB, room_gb), 2),
                  "gpu_offload_gb": None if vram is None else round(min(MODEL_ARTIFACT_CAP_GB, vram), 2),
                  "basis": "RAM: the room, within A3's 2 GB per artifact; GPU offload: up to the "
                           "largest GPU's dedicated memory, None where the platform reports no GPUs"},
    }


def build(machine: dict, readings: list[G.Sample], *, cause: str,
          browser_mb: float | None = None, thresholds: G.Thresholds | None = None) -> Profile:
    """A profile from measurements already taken. Pure."""
    profile = Profile(measured_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                      cause=cause, baseline=G.Baseline.of(readings, provisional=False),
                      browser_mb=browser_mb, **machine)
    profile.policy = derive_policy(profile, thresholds)
    return profile


def _browser_mb() -> float | None:
    """One headless Chromium's memory, from BROW-004's readiness probe; None
    when the browser is not ready (the probe says why)."""
    from friday import browser_readiness

    ready = browser_readiness.probe()
    if ready.state != browser_readiness.BROWSER_READY:
        logger.warning("browser cost not measured: %s (%s)", ready.state, ready.detail)
        return None
    return ready.memory_mb


def qualify(*, cause: str, readings: int = 10, every_s: float = 1.0, sampler=None,
            browser: bool = False, save: bool = True) -> Profile:
    """Measure this machine and derive its policy. The idle baseline is the
    median of `readings` taken `every_s` apart, so take it while FRIDAY is
    doing nothing; `browser` also launches Chromium once to measure its cost."""
    sample = sampler or G.sample_machine
    sample()                                        # opens the CPU window
    taken = []
    for _ in range(readings):
        time.sleep(every_s)
        taken.append(sample())
    profile = build(measure_machine(), taken, cause=cause,
                    browser_mb=_browser_mb() if browser else None)
    if save:
        store(profile)
    return profile


def store(profile: Profile, path: Path | None = None) -> Path:
    path = Path(path or PROFILE_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(".tmp")
    partial.write_text(json.dumps(profile.to_dict(), indent=1), encoding="utf-8")
    partial.replace(path)
    return path


def stored(path: Path | None = None) -> Profile | None:
    path = Path(path or PROFILE_PATH)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return Profile.from_dict(raw)
    except FileNotFoundError:
        return None
    except (OSError, ValueError, KeyError, TypeError) as exc:
        logger.warning("hardware profile at %s is unreadable: %s", path, exc)
        return None


def changes(profile: Profile, machine: dict | None = None) -> list[str]:
    """How this machine differs from the one the profile measured (RES-004)."""
    now = machine or measure_machine()
    was = profile.identity()
    is_ = identity_of(now["ram_total_gb"], now["cpu_logical"], now["gpus"])
    out = []
    if was["ram_total_gb"] != is_["ram_total_gb"]:
        out.append(f"RAM total {was['ram_total_gb']} -> {is_['ram_total_gb']} GB")
    if was["cpu_logical"] != is_["cpu_logical"]:
        out.append(f"logical CPUs {was['cpu_logical']} -> {is_['cpu_logical']}")
    if was["gpus"] != is_["gpus"]:
        out.append(f"GPUs {was['gpus']} -> {is_['gpus']}")
    return out


def for_governor() -> tuple[G.Baseline | None, G.Thresholds]:
    """The baseline and caps the process-wide governor starts with: the stored
    profile's when it describes this machine; otherwise no baseline - the
    governor then profiles the machine from its own readings - and the
    default caps."""
    profile = stored()
    if profile is None:
        return None, G.Thresholds()
    moved = changes(profile)
    if moved:
        logger.warning("hardware profile describes another machine (%s); judging pressure "
                       "against this one's own readings until it is measured again",
                       "; ".join(moved))
        return None, G.Thresholds()
    caps = dataclasses.replace(G.Thresholds(), max_workers=profile.policy["workers"]["max"],
                               max_browsers=profile.policy["browsers"]["max"])
    return profile.baseline, caps


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m friday.hardware_profile",
                                 description="Measure the machine FRIDAY runs on (PRD v7 RES-002).")
    sub = ap.add_subparsers(dest="command", required=True)
    q = sub.add_parser("qualify", help="measure this machine at rest and store its profile; "
                                       "run it while FRIDAY is idle")
    q.add_argument("--readings", type=int, default=10)
    q.add_argument("--every-s", type=float, default=1.0)
    q.add_argument("--browser", action="store_true",
                   help="also launch Chromium once to measure one browser's memory")
    q.add_argument("--cause", default="qualification requested")
    q.add_argument("--no-save", action="store_true", help="measure without storing the profile")
    q.add_argument("--out", help="also write the profile to this file")
    sub.add_parser("show", help="print the stored profile and how this machine differs from it")
    args = ap.parse_args(argv)
    if args.command == "qualify":
        profile = qualify(cause=args.cause, readings=args.readings, every_s=args.every_s,
                          browser=args.browser, save=not args.no_save)
        text = json.dumps(profile.to_dict(), indent=1)
        if args.out:
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out).write_text(text, encoding="utf-8")
        print(text)
        return 0
    profile = stored()
    if profile is None:
        print(json.dumps({"stored": None, "path": str(PROFILE_PATH)}))
        return 1
    print(json.dumps({**profile.to_dict(), "changes": changes(profile)}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
