"""
Run the built FRIDAY.exe as a machine that has never seen this repository.

    .venv-build\\Scripts\\python.exe scripts\\validate_exe_clean.py

Windows Sandbox is not installed on this host (and enabling it needs
elevation and a reboot), so this is the next best thing, and it catches the
same class of defect - a hidden dependency on the developer's machine:

    - the bundle is COPIED to a directory on another drive (the system
      temp on C:), so no path inside it can reach the checkout on F:;
    - PATH is reduced to the Windows system directories: no Python, no uv,
      no git, no Hermes, no Node;
    - LOCALAPPDATA, APPDATA, USERPROFILE and TEMP point at fresh empty
      directories: no profile, no cache, no adopted .env;
    - the working directory is C:\\ ;
    - every developer variable is dropped (FRIDAY_*, HERMES_*, PYTHON*,
      VIRTUAL_ENV, ADA_*, LIVEKIT_*, *_API_KEY).

Journeys: CLEAN-01 --version, CLEAN-02 --paths resolves under the fresh
profile, CLEAN-03 --doctor bootstraps and does not FAIL for a missing
dependency (DEGRADED for credentials/hermes/browser is the expected
answer), CLEAN-04 the MCP server and control room start and answer
(`--no-agent --no-browser`), CLEAN-05 --stop leaves nothing behind.
Exit is non-zero when any journey fails; the copy is deleted afterwards.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS: list[tuple[str, bool, str]] = []

DROP_PREFIXES = ("FRIDAY_", "HERMES_", "PYTHON", "ADA_", "LIVEKIT_", "VIRTUAL_ENV", "UV_", "CONDA", "PIP_",
                 "GROQ_", "SARVAM_", "OPENAI_", "GOOGLE_", "ANTHROPIC_", "DEEPGRAM_", "HF_")


def record(journey: str, ok: bool, detail: str) -> bool:
    RESULTS.append((journey, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {journey}  {detail}", flush=True)
    return ok


def port_open(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def http_status(url: str) -> int:
    try:
        with urllib.request.urlopen(url, timeout=3) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except (urllib.error.URLError, OSError):
        return 0


def wait_for(probe, timeout: float, process=None) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            return False
        if probe():
            return True
        time.sleep(0.5)
    return False


def clean_environment(profile: Path) -> dict[str, str]:
    system_root = os.environ.get("SYSTEMROOT", r"C:\Windows")
    keep = {}
    for key, value in os.environ.items():
        upper = key.upper()
        if upper.startswith(DROP_PREFIXES) or upper.endswith("_API_KEY") or upper in ("PATH", "PYTHONPATH"):
            continue
        keep[key] = value
    local = profile / "LocalAppData"
    roaming = profile / "Roaming"
    temp = profile / "Temp"
    for path in (local, roaming, temp, profile / "Home"):
        path.mkdir(parents=True, exist_ok=True)
    keep.update({
        "PATH": f"{system_root}\\System32;{system_root};{system_root}\\System32\\WindowsPowerShell\\v1.0",
        "LOCALAPPDATA": str(local), "APPDATA": str(roaming), "TEMP": str(temp), "TMP": str(temp),
        "USERPROFILE": str(profile / "Home"), "HOMEPATH": str(profile / "Home"),
        "PYTHONUTF8": "1",
    })
    return keep


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dist", default=str(ROOT / "dist" / "FRIDAY"))
    parser.add_argument("--keep", action="store_true", help="keep the copy and profile afterwards")
    options = parser.parse_args()
    source = Path(options.dist).resolve()
    if not (source / "FRIDAY.exe").is_file():
        print(f"no FRIDAY.exe under {source}", file=sys.stderr)
        return 2
    if port_open(8000) or port_open(8770):
        print("ports 8000/8770 are busy; stop the running Friday first", file=sys.stderr)
        return 2

    base = Path(tempfile.mkdtemp(prefix="friday-clean-", dir=os.environ.get("SYSTEMDRIVE", "C:") + "\\Windows\\Temp"
                                 if Path((os.environ.get("SYSTEMDRIVE", "C:") + "\\Windows\\Temp")).is_dir() else None))
    copy = base / "FRIDAY"
    profile = base / "profile"
    env = clean_environment(profile)
    exe = copy / "FRIDAY.exe"
    print(f"copying {source} -> {copy} ({sum(p.stat().st_size for p in source.rglob('*') if p.is_file()) // (1 << 20)} MB)", flush=True)
    started = time.monotonic()
    shutil.copytree(source, copy, ignore=shutil.ignore_patterns(".env", "PORTABLE_MODE", "data", "logs", "vault", "config", "skills"))
    print(f"copied in {time.monotonic() - started:.0f}s; PATH={env['PATH']}", flush=True)

    def run(*args: str, timeout: float = 300) -> subprocess.CompletedProcess:
        try:
            return subprocess.run([str(exe), *args], capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=timeout, cwd="C:\\", env=env, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired as exc:
            # A host that entered standby mid-call resumes with the deadline
            # long gone (Python reports a negative timeout). That is a
            # FAILED journey with a named reason, not a crashed validator.
            return subprocess.CompletedProcess([str(exe), *args], 124, stdout="",
                                               stderr=f"timed out ({exc.timeout:.0f}s): the host may have suspended")

    try:
        version = run("--version")
        record("CLEAN-01 --version", version.returncode == 0 and version.stdout.startswith("FRIDAY"),
               version.stdout.strip() or version.stderr.strip()[-200:])

        where = run("--paths")
        try:
            found = json.loads(where.stdout)
        except ValueError:
            found = {}
        state_root = Path(found.get("state_root", ""))
        record("CLEAN-02 --paths under the fresh profile",
               bool(found) and state_root == profile / "LocalAppData" / "FRIDAY" and str(copy) not in str(state_root),
               f"state_root={state_root} env_file={found.get('env_file') or 'none'}"
               if found else f"no answer: {where.stderr.strip()[-200:]}")

        doctor = run("--doctor")
        lines = [line for line in doctor.stdout.splitlines() if line.strip().startswith(("PASS", "DEGRADED", "FAIL"))]
        failed = [line.strip() for line in lines if line.strip().startswith("FAIL")]
        allowed_fail = [line for line in failed if "livekit credentials" in line]
        record("CLEAN-03 --doctor without any developer environment",
               doctor.returncode in (0, 1, 2) and not [line for line in failed if line not in allowed_fail]
               and any("core imports" in line and line.strip().startswith("PASS") for line in lines),
               f"exit {doctor.returncode}; FAIL lines: {failed or 'none'}")
        (ROOT / "build" / "validate").mkdir(parents=True, exist_ok=True)
        (ROOT / "build" / "validate" / "CLEAN-03-doctor.log").write_text(doctor.stdout + doctor.stderr, encoding="utf-8")

        log = open(ROOT / "build" / "validate" / "CLEAN-04-stack.log", "w", encoding="utf-8", errors="replace")
        stack = subprocess.Popen([str(exe), "--no-agent", "--no-browser"], stdout=log, stderr=subprocess.STDOUT,
                                 stdin=subprocess.DEVNULL, cwd="C:\\", env=env,
                                 creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        mcp_up = wait_for(lambda: port_open(8000), 150, stack)
        ui_up = wait_for(lambda: http_status("http://127.0.0.1:8770/health") == 200, 90, stack)
        record("CLEAN-04 MCP server and control room start", mcp_up and ui_up and stack.poll() is None,
               f"mcp={mcp_up} ui={ui_up} launcher alive={stack.poll() is None}")

        stopped = run("--stop", timeout=90)
        settled = wait_for(lambda: not port_open(8000) and not port_open(8770), 20)
        status = run("--status", timeout=60)
        record("CLEAN-05 --stop leaves nothing behind", settled and "no Friday processes" in status.stdout,
               "ports free, no processes" if settled else stopped.stdout.strip()[-200:])
        try:
            stack.wait(timeout=10)
        except subprocess.TimeoutExpired:
            stack.kill()
        log.close()

        # Removing the application core must not touch the profile: the
        # database the doctor bootstrapped is still there afterwards.
        database = profile / "LocalAppData" / "FRIDAY" / "data" / "ada.sqlite3"
        shutil.rmtree(copy, ignore_errors=True)
        record("CLEAN-06 removing the application keeps the user's data",
               not copy.exists() and database.is_file(),
               f"bundle removed={not copy.exists()} database still present={database.is_file()}")
    finally:
        if not options.keep:
            shutil.rmtree(base, ignore_errors=True)
        else:
            print(f"kept {base}", flush=True)

    failed = [j for j, ok, _ in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} clean-machine journeys passed"
          + (f"; failed: {', '.join(failed)}" if failed else ""), flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
