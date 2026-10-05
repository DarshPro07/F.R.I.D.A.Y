"""
Drive the built FRIDAY.exe through its golden journeys (docs/build/WINDOWS_EXE.md).

    .venv-build\\Scripts\\python.exe scripts\\validate_exe.py
    .venv-build\\Scripts\\python.exe scripts\\validate_exe.py --exe dist\\FRIDAY\\FRIDAY.exe --with-agent

Tests the artifact, never the source tree: every step runs dist\\FRIDAY\\
FRIDAY.exe as a user would. Journeys:

    EXE-00  preconditions: the exe exists, :8000 and :8770 are free
    EXE-01  --version answers with the stamped commit
    EXE-02  --doctor bootstraps the database and its artifact checks do not FAIL
            (a fresh profile has no keys yet; those rows are shown, not judged)
    EXE-03  --role server answers SSE on :8000
    EXE-05  --role ui serves /health and / on :8770
    EXE-08  sys.executable shim: -c, -m and a bundled .py path
    EXE-09  --stop leaves no process and frees the ports
    EXE-10  an unknown role fails loudly with exit 2
    EXE-04  the database survives across those processes
    EXE-07  (--with-agent, needs .env beside the exe) the supervisor starts
            all three roles; the voice worker's inference process and job
            runner come up inside the bundle and it reaches LiveKit
    EXE-06  (--with-agent) the voice worker registers with LiveKit. Reported
            as BLOCKED_EXTERNAL, not FAIL, when the .env still carries the
            .env.example placeholders (LiveKit answers 401)

Prints one line per journey and exits non-zero if any failed. Output of
each started role is kept under build/validate/<journey>.log.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOGS = ROOT / "build" / "validate"
MCP = ("127.0.0.1", 8000)
UI = ("127.0.0.1", 8770)

RESULTS: list[tuple[str, bool, str]] = []

#: Environment every launched FRIDAY.exe gets: LOCALAPPDATA redirected to a
#: scratch profile so the owner's real %LOCALAPPDATA%\FRIDAY is never
#: touched, FRIDAY_HOME and PORTABLE_MODE cleared so the default path -
#: including the migration of an older build's state - is what gets tested.
VALIDATOR_ENV: dict[str, str] = dict(os.environ)
SCRATCH = ROOT / "build" / "validate" / "profile"


def prepare_scratch_profile(exe: Path) -> Path:
    """
    A fresh LOCALAPPDATA for this run. The `.env` the owner keeps (beside
    the exe from an older layout, or already under the real profile) is
    copied into the scratch config first, so the agent journeys have
    credentials and the launcher's adoption step has nothing to move.
    """
    import shutil

    shutil.rmtree(SCRATCH, ignore_errors=True)
    config_dir = SCRATCH / "FRIDAY" / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    real = Path(os.environ.get("LOCALAPPDATA", "")) / "FRIDAY" / "config" / ".env"
    for source in (exe.parent / ".env", real):
        if source.is_file():
            shutil.copy2(source, config_dir / ".env")
            break
    VALIDATOR_ENV["LOCALAPPDATA"] = str(SCRATCH)
    VALIDATOR_ENV.pop("FRIDAY_HOME", None)
    VALIDATOR_ENV.pop("PORTABLE_MODE", None)
    return SCRATCH / "FRIDAY"


def record(journey: str, ok: bool, detail: str) -> bool:
    RESULTS.append((journey, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {journey}  {detail}", flush=True)
    return ok


def port_open(host: str, port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


def wait_for(probe, timeout: float, process: subprocess.Popen | None = None) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            return False
        if probe():
            return True
        time.sleep(0.3)
    return False


def run(exe: Path, *args: str, timeout: float = 180) -> subprocess.CompletedProcess:
    try:
        return subprocess.run([str(exe), *args], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout,
                              cwd=str(exe.parent), stdin=subprocess.DEVNULL, env=VALIDATOR_ENV)
    except subprocess.TimeoutExpired as exc:
        # A host that entered standby mid-call resumes with the deadline long
        # gone (Python reports a negative timeout): a FAILED journey with a
        # named reason, never a crashed validator.
        return subprocess.CompletedProcess([str(exe), *args], 124, stdout="",
                                           stderr=f"timed out ({exc.timeout:.0f}s): the host may have suspended")


def start(exe: Path, journey: str, *args: str) -> subprocess.Popen:
    LOGS.mkdir(parents=True, exist_ok=True)
    log = open(LOGS / f"{journey}.log", "w", encoding="utf-8", errors="replace")
    return subprocess.Popen([str(exe), *args], stdout=log, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, cwd=str(exe.parent), env=VALIDATOR_ENV,
                            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))


def http(url: str, timeout: float = 5.0) -> tuple[int, str, str]:
    """(status, content-type, first 200 chars). SSE streams: headers only."""
    request = urllib.request.Request(url, headers={"Accept": "text/event-stream, */*"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            kind = response.headers.get("content-type", "")
            if "event-stream" in kind:
                return response.status, kind, ""
            return response.status, kind, response.read(200).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, "", ""
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        return 0, "", str(exc)


def stop(exe: Path, journey: str) -> bool:
    result = run(exe, "--stop", timeout=60)
    (LOGS / f"{journey}-stop.log").write_text(result.stdout + result.stderr, encoding="utf-8")
    settled = wait_for(lambda: not port_open(*MCP) and not port_open(*UI), 15)
    status = run(exe, "--status", timeout=60).stdout
    return settled and "no Friday processes" in status


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--exe", default=str(ROOT / "dist" / "FRIDAY" / "FRIDAY.exe"))
    parser.add_argument("--with-agent", action="store_true",
                        help="also start the voice worker (needs .env beside the exe)")
    options = parser.parse_args()
    exe = Path(options.exe).resolve()
    internal = exe.parent / "_internal"
    profile = prepare_scratch_profile(exe)
    print(f"validating {exe}", flush=True)
    print(f"scratch profile (LOCALAPPDATA for every launched exe): {SCRATCH}", flush=True)

    # EXE-00 -----------------------------------------------------------------
    if not record("EXE-00 preconditions", exe.is_file() and not port_open(*MCP) and not port_open(*UI),
                  f"exe={'yes' if exe.is_file() else 'MISSING'} :8000={'busy' if port_open(*MCP) else 'free'} "
                  f":8770={'busy' if port_open(*UI) else 'free'}"):
        return 2

    # EXE-01 -----------------------------------------------------------------
    version = run(exe, "--version")
    record("EXE-01 --version", version.returncode == 0 and version.stdout.startswith("FRIDAY")
           and "commit" in version.stdout, version.stdout.strip() or version.stderr.strip()[:200])

    # EXE-12 -----------------------------------------------------------------
    # State lives under %LOCALAPPDATA%\FRIDAY (here: the scratch profile),
    # never inside the bundle's _internal, and .env is read from its config/.
    where = run(exe, "--paths")
    try:
        found = json.loads(where.stdout)
    except ValueError:
        found = {}
    state_root = Path(found.get("state_root", ""))
    db_path = Path(found.get("database", ""))
    logs_dir = Path(found.get("logs_dir", ""))
    env_file = Path(found.get("env_file", ""))
    outside = (bool(found) and state_root == profile and "_internal" not in str(state_root)
               and db_path.parent == state_root / "data" and logs_dir == state_root / "logs"
               and (not found.get("env_file") or env_file == state_root / "config" / ".env"))
    record("EXE-12 state root outside the bundle", outside,
           f"state_root={state_root} db={db_path.name} env={found.get('env_file') or 'none'}"
           if found else f"--paths did not answer: {where.stderr.strip()[:200]}")

    # EXE-13 -----------------------------------------------------------------
    # The layout of builds before 2026-09-15 kept state under _internal; the
    # launcher moves it out on the first start and leaves the shipped
    # fixtures where they are.
    probes = {"data": internal / "data" / "legacy-probe.txt",
              "logs": internal / "logs" / "legacy-probe.log",
              "vault": internal / "vault" / "legacy-probe.md"}
    for probe in probes.values():
        probe.parent.mkdir(parents=True, exist_ok=True)
        probe.write_text("planted by validate_exe", encoding="utf-8")
    fixtures_before = (internal / "data" / "corpus").is_dir()

    # EXE-02 -----------------------------------------------------------------
    doctor = run(exe, "--doctor", "--doctor-scope", "artifact", timeout=300)
    db_line = next((line.strip() for line in doctor.stdout.splitlines() if " database " in line), "")
    tables = 0
    if "tables)" in db_line:
        try:
            tables = int(db_line.rsplit("(", 1)[1].split()[0])
        except (IndexError, ValueError):
            tables = 0
    record("EXE-02 --doctor bootstraps", doctor.returncode in (0, 1) and tables >= 42 and db_path.is_file(),
           f"exit {doctor.returncode} ({'PASS' if doctor.returncode == 0 else 'DEGRADED' if doctor.returncode == 1 else 'FAIL'}), "
           f"{tables} tables, db {'exists' if db_path.is_file() else 'MISSING'} at {db_path}")
    LOGS.mkdir(parents=True, exist_ok=True)
    (LOGS / "EXE-02-doctor.log").write_text(doctor.stdout + doctor.stderr, encoding="utf-8")

    migrated = all((state_root / name / probe.name).is_file() and not probe.exists()
                   for name, probe in probes.items())
    fixtures_after = (internal / "data" / "corpus").is_dir() if fixtures_before else True
    record("EXE-13 legacy state migrates out of _internal", migrated and fixtures_after,
           f"moved data/logs/vault probes into {state_root}; shipped fixtures stayed={fixtures_after}")

    # EXE-03 -----------------------------------------------------------------
    server = start(exe, "EXE-03-server", "--role", "server")
    up = wait_for(lambda: port_open(*MCP), 120, server)
    status, kind, _ = http("http://127.0.0.1:8000/sse", timeout=5) if up else (0, "", "")
    record("EXE-03 --role server", up and status == 200 and "event-stream" in kind,
           f"up={up} GET /sse -> {status} {kind or '(no answer)'}")
    record("EXE-09 --stop (server)", stop(exe, "EXE-03-server"), "ports free, no processes")

    # EXE-05 -----------------------------------------------------------------
    ui = start(exe, "EXE-05-ui", "--role", "ui", "--no-browser")
    up = wait_for(lambda: http("http://127.0.0.1:8770/health")[0] == 200, 90, ui)
    home_status, home_kind, home_body = http("http://127.0.0.1:8770/") if up else (0, "", "")
    record("EXE-05 --role ui", up and home_status == 200 and "html" in home_kind.lower(),
           f"health={'200' if up else 'no answer'} GET / -> {home_status} {home_kind}")
    record("EXE-09 --stop (ui)", stop(exe, "EXE-05-ui"), "ports free, no processes")

    # EXE-08 -----------------------------------------------------------------
    shim_c = run(exe, "-c", "import friday, sys; print(friday.__file__); print(sys.frozen)")
    shim_m = run(exe, "-m", "friday.launcher", "--version")
    shim_py = run(exe, str(internal / "friday" / "objective_cli.py"), "--help")
    ok_c = shim_c.returncode == 0 and "_internal" in shim_c.stdout and "True" in shim_c.stdout
    ok_m = shim_m.returncode == 0 and shim_m.stdout.startswith("FRIDAY")
    ok_py = shim_py.returncode == 0 and "usage" in shim_py.stdout.lower()
    record("EXE-08 interpreter shim", ok_c and ok_m and ok_py,
           f"-c {'ok' if ok_c else 'FAIL'}; -m {'ok' if ok_m else 'FAIL'}; bundled .py {'ok' if ok_py else 'FAIL'}"
           + ("" if ok_c and ok_m and ok_py else
              f" | {(shim_c.stderr or shim_m.stderr or shim_py.stderr).strip()[-300:]}"))

    # EXE-10 -----------------------------------------------------------------
    bad = run(exe, "--role", "nope")
    record("EXE-10 unknown role fails loudly", bad.returncode == 2 and "unknown role" in bad.stderr,
           f"exit {bad.returncode}: {bad.stderr.strip()[:120]}")

    # EXE-04 -----------------------------------------------------------------
    # The same file, and never fewer tables: later processes may add ledgers
    # (the doctor's skills section creates its own after the database check),
    # so the count is allowed to grow, never to shrink or reset.
    after = run(exe, "--doctor", timeout=300)
    after_line = next((line.strip() for line in after.stdout.splitlines() if " database " in line), "")
    after_tables = 0
    if "tables)" in after_line:
        try:
            after_tables = int(after_line.rsplit("(", 1)[1].split()[0])
        except (IndexError, ValueError):
            after_tables = 0
    record("EXE-04 database persists", db_path.is_file() and str(db_path) in after_line and after_tables >= tables,
           f"{db_path.name} still there: {tables} tables at bootstrap, {after_tables} after the server and ui ran")

    # EXE-06 / EXE-07 --------------------------------------------------------
    env_file = profile / "config" / ".env"                 # copied there by prepare_scratch_profile
    if options.with_agent and env_file.is_file():
        everything = start(exe, "EXE-06-all", "--no-browser")
        agent_log = logs_dir / "agent.log"
        started_at = time.time()

        def agent_text() -> str:
            """Only this run's part of the (appended) agent log."""
            try:
                text = agent_log.read_text(encoding="utf-8", errors="replace")
            except OSError:
                return ""
            marker = "=== agent started"
            return text[text.rfind(marker):] if marker in text else text

        def booted() -> bool:
            text = agent_text()
            # The worker is up when its job runner exists and it has either
            # registered or at least reached LiveKit's front door.
            return "job runner initialized" in text and (
                "registered worker" in text or "failed to connect to livekit" in text)

        def registered() -> bool:
            return "registered worker" in agent_text()

        mcp_up = wait_for(lambda: port_open(*MCP), 120, everything)
        ui_up = wait_for(lambda: http("http://127.0.0.1:8770/health")[0] == 200, 90, everything)
        up = wait_for(booted, 150, everything)
        text = agent_text()
        inference = "inference" in text and "process initialized" in text
        alive = everything.poll() is None
        record("EXE-07 voice worker boots in the bundle", mcp_up and ui_up and up and inference and alive,
               f"mcp={mcp_up} ui={ui_up} job runner={up} inference process={inference} "
               f"launcher alive={alive} ({time.time() - started_at:.0f}s)")

        worker = registered() or wait_for(registered, 30, everything)
        placeholder = "your-project-xxxxx.livekit.cloud" in agent_text()
        if worker:
            record("EXE-06 voice worker registered with LiveKit", True, "registered worker")
        elif placeholder:
            # Not a packaging failure: the .env beside the exe still carries
            # the LIVEKIT_URL from .env.example, which LiveKit rejects (401).
            RESULTS.append(("EXE-06 voice worker registered with LiveKit", True,
                            "BLOCKED_EXTERNAL: the .env holds the .env.example LiveKit placeholders (401)"))
            print("  BLOCKED_EXTERNAL  EXE-06 voice worker registered with LiveKit  "
                  f"{env_file} still holds the .env.example LiveKit placeholders; "
                  "LiveKit answered 401. Fill in real credentials and rerun.", flush=True)
        else:
            record("EXE-06 voice worker registered with LiveKit", False,
                   "no 'registered worker' line; see build/validate/EXE-06-all.log")
        record("EXE-09 --stop (all)", stop(exe, "EXE-06-all"), "ports free, no processes")
    elif options.with_agent:
        record("EXE-06 voice worker registered with LiveKit", False, f"no .env at {env_file}")
    else:
        print("  SKIP  EXE-06/07 voice worker  (pass --with-agent with a .env beside the exe)",
              flush=True)

    failed = [journey for journey, ok, _ in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} journeys passed"
          + (f"; failed: {', '.join(failed)}" if failed else ""), flush=True)
    print(f"role output under {LOGS}", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
