"""
Run the shipped FRIDAY zip on a disposable Windows machine (PKG-001).

    .venv-build\\Scripts\\python.exe scripts\\validate_exe_sandbox.py --jobs-parked

validate_exe_clean.py simulates a clean machine on this host; this runs the
real thing in Windows Sandbox. The host stages a temp dir holding only the
zip, its .sha256 and scripts/sandbox_guest.ps1, writes friday.wsb with
networking, clipboard, vGPU, audio/video input and printers disabled
explicitly (Sandbox enables some of them by default), launches it and waits
for the guest's result.json. Nothing of the repository, no .env and no
environment variable is mapped in.

Journeys (scripts/sandbox_guest.ps1): SBX-00 machine config and the
guest-computed zip digest, SBX-01 install from the zip under
%LOCALAPPDATA%\\Programs, SBX-02 --version/--paths, SBX-03 --doctor (judged
here with validate_exe_clean.doctor_verdict), SBX-04 `--no-agent
--no-browser` serves :8000 and :8770/health, SBX-05 a useful task, SBX-06
--stop leaves nothing running, SBX-07 restart keeps the PIN and the run,
SBX-08 uninstall keeps then removes the user's state.

SBX-05 sets a synthetic PIN, unlocks with it and posts an objective whose
explicit `tasks` name `memory_remember` (offline, no edge, no approval) with
a per-sandbox canary, so no model is needed. Under --no-agent nothing ticks
the objective driver (ContinuousTaskExecutor runs only in the voice worker,
agent_friday.py:39), so the guest has the installed FRIDAY.exe drive the run
itself through friday.objective_cli's dispatch and executor (the EXE runs a
.py argument as bundled Python), then reads it back through the control
room: it must be COMPLETED with its task SUCCEEDED. SBX-07, after the
restart, needs the same run still COMPLETED and a second run whose
`memory_recall` of the canary SUCCEEDS (recall fails on "nothing recorded"),
so the task's effect persisted across the restart.

Refusals, in order, all exit 2: Windows Sandbox absent (BLOCKED_EXTERNAL with
the enable command), --jobs-parked not passed (enabling or running Sandbox
may need a reboot; only when development jobs are parked), a Sandbox already
running, the zip or its .sha256 missing. A timeout waiting for result.json,
including one caused by host standby, is a named failure. Exit 0 only when
verdict() passes every journey. PKG-001 stays PARTIAL until a real
result.json from a real run passes here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts import validate_exe_clean as clean  # noqa: E402

ROOT = clean.ROOT
GUEST_SCRIPT = Path(__file__).resolve().with_name("sandbox_guest.ps1")
JOURNEYS = tuple(f"SBX-0{n}" for n in range(9))
MAX_RESULT_BYTES = 1 << 20
ENABLE_COMMAND = 'Enable-WindowsOptionalFeature -FeatureName "Containers-DisposableClientVM" -All -Online'
GUEST_IN = "C:\\In"
GUEST_OUT = "C:\\Out"


def build_wsb(zip_dir: Path, guest_dir: Path, results_dir: Path, *, memory_mb: int = 4096) -> str:
    """The Windows Sandbox configuration. Every device and channel a guest
    could use to reach the host or the network is disabled by name, since
    Sandbox enables networking and clipboard by default. Inputs map
    read-only; the writable C:\\Out (an empty host-created `results_dir`) is
    the only way results can leave a networkless sandbox."""
    root = ET.Element("Configuration")
    for switch in ("Networking", "ClipboardRedirection", "vGPU", "AudioInput", "VideoInput",
                   "PrinterRedirection"):
        ET.SubElement(root, switch).text = "Disable"
    ET.SubElement(root, "ProtectedClient").text = "Enable"
    ET.SubElement(root, "MemoryInMB").text = str(memory_mb)
    folders = ET.SubElement(root, "MappedFolders")
    for host, guest, read_only in ((zip_dir, f"{GUEST_IN}\\dist", "true"),
                                   (guest_dir, f"{GUEST_IN}\\guest", "true"),
                                   (results_dir, GUEST_OUT, "false")):
        folder = ET.SubElement(folders, "MappedFolder")
        ET.SubElement(folder, "HostFolder").text = str(host)
        ET.SubElement(folder, "SandboxFolder").text = guest
        ET.SubElement(folder, "ReadOnly").text = read_only
    logon = ET.SubElement(root, "LogonCommand")
    ET.SubElement(logon, "Command").text = (
        f"powershell.exe -NoProfile -ExecutionPolicy Bypass -File {GUEST_IN}\\guest\\{GUEST_SCRIPT.name}")
    return ET.tostring(root, encoding="unicode")


def verdict(result_json_text: str, expected_ids, zip_sha: str) -> tuple[bool, list[str]]:
    """(pass, reasons) for the guest's result.json. The text came out of a
    machine running the artifact under test, so it is untrusted: a half-run,
    garbled or padded report never reads as PASS, and SBX-03 is re-judged
    here with the clean-machine doctor rule whatever the guest said."""
    if len(result_json_text.encode("utf-8", "replace")) > MAX_RESULT_BYTES:
        return False, ["result.json is over 1 MB; refused unread"]
    if not result_json_text.strip():
        return False, ["result.json is empty"]
    try:
        records = json.loads(result_json_text)
    except (ValueError, RecursionError) as exc:  # deep nesting under 1 MB
        return False, [f"result.json is not JSON: {exc}"]
    if not isinstance(records, list) or not records:
        return False, ["result.json is not a list of {id, ok, detail} records"]

    expected = tuple(expected_ids)
    reasons: list[str] = []
    seen: dict[str, dict] = {}
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("id"), str):
            reasons.append(f"a record is not {{id, ok, detail}}: {str(record)[:80]}")
            continue
        journey = record["id"]
        if journey not in expected:
            reasons.append(f"unknown journey {journey[:40]!r}")
            continue
        if journey in seen:
            reasons.append(f"duplicate journey {journey}")
            continue
        seen[journey] = record
        if record.get("ok") is not True:
            reasons.append(f"{journey} failed: {str(record.get('detail'))[:300]}")
    missing = [journey for journey in expected if journey not in seen]
    if missing:
        reasons.append(f"missing journeys: {', '.join(missing)}")

    machine = (seen.get("SBX-00") or {}).get("detail")
    guest_sha = machine.get("zip_sha256") if isinstance(machine, dict) else None
    if not isinstance(guest_sha, str) or not guest_sha:
        reasons.append("SBX-00 carries no zip sha256")
    elif guest_sha.lower() != zip_sha.lower():
        reasons.append(f"SBX-00 zip sha256 {guest_sha[:64]} is not the artifact's {zip_sha}")

    doctor = (seen.get("SBX-03") or {}).get("detail")
    if "SBX-03" in seen:
        code = doctor.get("returncode") if isinstance(doctor, dict) else None
        stdout = doctor.get("stdout") if isinstance(doctor, dict) else None
        if type(code) is not int or not isinstance(stdout, str):
            reasons.append("SBX-03 carries no {returncode, stdout}")
        else:
            doctor_ok, failed = clean.doctor_verdict(code, stdout)
            if not doctor_ok:
                reasons.append(f"SBX-03 doctor fails the clean-machine rule: exit {code}; "
                               f"FAIL lines: {failed or 'none'}")
    return not reasons, reasons


def sandbox_running() -> bool:
    """True when a Windows Sandbox process exists; only one may run at a time.
    An unreadable process list counts as running, so it refuses rather than
    launching blind."""
    try:
        listing = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True,
                                 timeout=30, errors="replace")
    except (OSError, subprocess.SubprocessError):
        return True
    if listing.returncode != 0:
        return True
    return any(line.strip('"').lower().startswith("windowssandbox") for line in listing.stdout.splitlines())


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _detail(text: str, journey: str) -> dict:
    """One record's detail from a report verdict() has already judged, for printing."""
    try:
        records = json.loads(text)
    except (ValueError, RecursionError):
        return {}
    for record in records if isinstance(records, list) else ():
        if isinstance(record, dict) and record.get("id") == journey and isinstance(record.get("detail"), dict):
            return record["detail"]
    return {}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--zip", help="the artifact (default: the newest dist/FRIDAY-*-win64.zip)")
    parser.add_argument("--jobs-parked", action="store_true",
                        help="confirm no development job is running (Sandbox may need a reboot)")
    parser.add_argument("--timeout", type=float, default=1800, help="seconds to wait for result.json")
    parser.add_argument("--memory-mb", type=int, default=4096)
    options = parser.parse_args(argv)

    if not clean.sandbox_available():
        print("  BLOCKED_EXTERNAL  Windows Sandbox is not installed. From an elevated PowerShell, "
              f"once development jobs are parked (it may reboot the host): {ENABLE_COMMAND}", flush=True)
        return 2
    if not options.jobs_parked:
        print("refused: pass --jobs-parked once no development job is running", flush=True)
        return 2
    if sandbox_running():
        print("refused: a Windows Sandbox is already running; close it first", flush=True)
        return 2
    if options.zip:
        archive = Path(options.zip).resolve()
    else:
        found = sorted((ROOT / "dist").glob("FRIDAY-*-win64.zip"), key=lambda p: p.stat().st_mtime)
        archive = found[-1] if found else ROOT / "dist" / "FRIDAY-win64.zip"
    sidecar = Path(f"{archive}.sha256")
    if not archive.is_file() or not sidecar.is_file():
        print(f"refused: {archive} or its .sha256 is missing; build with scripts/build_windows.py", flush=True)
        return 2

    base = Path(tempfile.mkdtemp(prefix="friday-sandbox-"))
    zip_dir, guest_dir, results_dir = base / "dist", base / "guest", base / "out"
    for folder in (zip_dir, guest_dir, results_dir):
        folder.mkdir()
    shutil.copy2(archive, zip_dir / archive.name)
    shutil.copy2(sidecar, zip_dir / sidecar.name)
    shutil.copy2(GUEST_SCRIPT, guest_dir / GUEST_SCRIPT.name)
    zip_sha = sha256(zip_dir / archive.name)
    wsb = base / "friday.wsb"
    wsb.write_text(build_wsb(zip_dir, guest_dir, results_dir, memory_mb=options.memory_mb), encoding="utf-8")
    print(f"staged {base}; zip sha256 {zip_sha}; launching {wsb}", flush=True)
    os.startfile(wsb)  # noqa: S606 - Windows-only by design; opens the .wsb with WindowsSandbox.exe

    result = results_dir / "result.json"
    deadline = time.monotonic() + options.timeout
    while not result.is_file() and time.monotonic() < deadline:
        time.sleep(5)
    if not result.is_file():
        print(f"\nFAIL  TIMEOUT  no result.json after {options.timeout:.0f}s (the host may have entered "
              f"standby, or the guest stopped; partial records, if any: {results_dir}). The sandbox may "
              "still be open; close it before the next run.", flush=True)
        return 1
    with open(result, "rb") as handle:
        raw = handle.read(MAX_RESULT_BYTES + 1)
    text = raw.decode("utf-8-sig", errors="replace")

    ok, reasons = verdict(text, JOURNEYS, zip_sha)
    for reason in reasons:
        print(f"  FAIL  {reason}", flush=True)
    stale = clean.stale_dist_note(str(_detail(text, "SBX-02").get("version", "")), clean.repo_head())
    if stale:
        print(f"  STALE_DIST  {stale}; rebuild with scripts/build_windows.py before reading "
              "this run as a verdict on this checkout", flush=True)
    machine = {key: value for key, value in _detail(text, "SBX-00").items() if key != "zip_sha256"}
    print(f"\n{'PASS' if ok else 'FAIL'}  sandbox journeys {len(JOURNEYS)} expected, {len(reasons)} problem(s); "
          f"machine={json.dumps(machine, sort_keys=True)[:600]} artifact={archive.name} sha256={zip_sha}; "
          f"evidence in {results_dir}", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
