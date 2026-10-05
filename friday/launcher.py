"""
One entry point for the packaged FRIDAY.exe and for a source checkout.

Friday is three processes (see `start.py` and `scripts/restart_friday.py`,
which this module replaces for the packaged build):

    server   the MCP server, SSE on :8000          (server.py)
    ui       the control room, HTTP on :8770       (scripts/run_ui.py)
    agent    the LiveKit voice worker              (agent_friday.py start)

The packaged executable cannot shell out to `python.exe`, so the supervisor
starts each role as `FRIDAY.exe --role <name>` - the same executable, a
different in-process entry - and Friday's own code, which re-invokes
`sys.executable` as an interpreter (`-c`, `-m friday.objective_cli`, a
`.py` path), is served by `maybe_run_as_python()`.

Nothing here decides where state lives. Every module anchors its data on
`Path(__file__)` (friday/config.py explains why), which in a onedir build
is `dist/FRIDAY/_internal/`. This module only makes sure those directories
exist and that `.env` is loaded from beside the executable before any of
them import.

    FRIDAY.exe                    start server -> ui -> agent, stay attached
    FRIDAY.exe --stop             stop everything running from this executable
    FRIDAY.exe --status           what is listening and what is running
    FRIDAY.exe --doctor           diagnostics; exit 0 PASS, 1 DEGRADED, 2 FAIL
    FRIDAY.exe --version
    FRIDAY.exe --role server|ui|agent [args]     one role, in this process

From source the same thing is `python -m friday.launcher ...`.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import runpy
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from friday import credentials

ROLES = ("server", "ui", "agent")

MCP_HOST, MCP_PORT = "127.0.0.1", 8000
UI_HOST, UI_PORT = "127.0.0.1", 8770

#: How long each role gets to answer before the launch is called failed.
#: The MCP server imports every toolset at start (35 s was seen on a cold
#: disk); the UI is a Starlette app and is quick.
MCP_WAIT_S = 90.0
UI_WAIT_S = 45.0
POLL_S = 0.3

#: Interpreter flags Friday passes when it re-invokes `sys.executable`.
#: They mean nothing to a frozen runtime and are skipped.
_PYTHON_FLAGS = frozenset({"-I", "-u", "-B", "-E", "-s", "-S", "-X"})

PASS, DEGRADED, FAIL = "PASS", "DEGRADED", "FAIL"


# ---------------------------------------------------------------------------
# Where things are
# ---------------------------------------------------------------------------

def frozen() -> bool:
    """True inside a PyInstaller build."""
    return bool(getattr(sys, "frozen", False))


def app_root() -> Path:
    """
    The application core: `_internal` in a onedir build, the repository
    root from source. Every `Path(__file__).resolve().parent.parent` in
    the `friday` package resolves to this directory.
    """
    if frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def exe_dir() -> Path:
    """The directory holding FRIDAY.exe (or the repo root from source)."""
    if frozen():
        return Path(sys.executable).resolve().parent
    return app_root()


def manifest() -> dict:
    """The build manifest written by scripts/build_windows.py, or {}."""
    path = app_root() / "build_manifest.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def version() -> str:
    found = manifest()
    if found.get("version"):
        commit = found.get("commit_short") or "unknown"
        dirty = "+dirty" if found.get("dirty") else ""
        return f"FRIDAY {found['version']} (commit {commit}{dirty}, {found.get('channel', 'dev')})"
    try:
        from importlib.metadata import version as _version

        return f"FRIDAY {_version('friday-tony-stark-demo')} (source)"
    except Exception:                                       # noqa: BLE001
        return "FRIDAY (source, unversioned)"


def state_root() -> Path:
    """
    Where the user's state lives: %LOCALAPPDATA%\\FRIDAY for a packaged
    build, beside the executable in portable mode, FRIDAY_HOME when set,
    the repository from source. Decided once, in friday.config, which is
    also where `.env` is loaded from (OS environment first, then
    STATE_ROOT/config/.env, then beside the executable only in portable
    mode, then the repository only from source).
    """
    from friday.config import STATE_ROOT

    return STATE_ROOT


def env_file_candidates() -> list[Path]:
    from friday.config import env_file_candidates as candidates

    return candidates()


def load_env() -> Path | None:
    """The `.env` this process loaded (friday.config does it at import)."""
    from friday.config import ENV_FILE

    return ENV_FILE


def ensure_state_dirs(root: Path | None = None) -> list[Path]:
    """Create the writable directories the roles expect. Returns them."""
    if root is None:
        from friday.config import STATE_DIRS

        wanted = list(STATE_DIRS)
    else:
        wanted = [root / "data", root / "data" / "logs", root / "data" / "artifacts",
                  root / "logs", root / "vault", root / "config", root / "skills",
                  root / "cache", root / "runtime"]
    for path in wanted:
        path.mkdir(parents=True, exist_ok=True)
    return wanted


#: What an earlier packaged build kept under `_internal`, and the two
#: shipped fixture directories under data/ that are application files.
LEGACY_STATE_DIRS = ("data", "logs", "vault")
SHIPPED_DATA = ("corpus", "gate")


def migrate_legacy_state(app_root_: Path, state_root_: Path, *,
                         explicit_home: bool) -> list[str]:
    """
    Move state an earlier build kept under `_internal` into the state root.

    Only when the state root is the default one: an explicit FRIDAY_HOME is
    an explicit intent and never receives another install's data. Entry by
    entry, never overwriting - a newer file in the new location wins and
    the old one stays where it was for the owner to inspect. The shipped
    fixtures (`data/corpus`, `data/gate`) are application files and stay.
    Returns the relative paths that moved.
    """
    if explicit_home:
        return []
    try:
        if app_root_.resolve() == state_root_.resolve():
            return []
    except OSError:
        return []
    moved: list[str] = []
    for name in LEGACY_STATE_DIRS:
        source = app_root_ / name
        if not source.is_dir():
            continue
        for item in sorted(source.iterdir()):
            if name == "data" and item.name in SHIPPED_DATA:
                continue
            target = state_root_ / name / item.name
            if target.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(item), str(target))
            moved.append(f"{name}/{item.name}")
    return moved


def adopt_legacy_state() -> list[str]:
    """Run the migration for this process's roots; a no-op from source."""
    if not frozen():
        return []
    return migrate_legacy_state(app_root(), state_root(),
                                explicit_home=bool(os.getenv("FRIDAY_HOME", "").strip()))


# ---------------------------------------------------------------------------
# `sys.executable` as a Python interpreter
# ---------------------------------------------------------------------------

def bundled_module_name(path: Path | str, root: Path | None = None) -> str | None:
    """
    `<root>/friday/objective_cli.py` -> `friday.objective_cli`.

    A frozen build keeps pure-Python modules inside the archive, so a path
    that Friday computed from `__file__` may not exist on disk even though
    the module is importable. None when the path is outside the root or is
    not a `.py` file.
    """
    root = (root or app_root()).resolve()
    try:
        relative = Path(path).resolve().relative_to(root)
    except ValueError:
        return None
    if relative.suffix != ".py":
        return None
    parts = list(relative.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts) if parts else None


def _importable(module: str) -> bool:
    """Whether `module` can be found without importing it (parents are imported)."""
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def maybe_run_as_python(argv: list[str], root: Path | None = None) -> int | None:
    """
    Serve `sys.executable -c code`, `-m module`, and `script.py` invocations.

    Returns None when `argv` is not one of those shapes, so the caller can
    parse it as launcher arguments instead. A module or script runs as
    `__main__` in this process; its SystemExit propagates to the caller.
    """
    args = list(argv)
    while args and (args[0] in _PYTHON_FLAGS or args[0].startswith("-X")):
        args.pop(0)
    if not args:
        return None
    head = args[0]
    if head == "-c" and len(args) >= 2:
        sys.argv = ["-c", *args[2:]]
        exec(compile(args[1], "<string>", "exec"), {"__name__": "__main__"})
        return 0
    if head == "-m" and len(args) >= 2:
        sys.argv = [args[1], *args[2:]]
        runpy.run_module(args[1], run_name="__main__", alter_sys=True)
        return 0
    if head.endswith(".py"):
        script = Path(head)
        sys.argv = [str(script), *args[1:]]
        if script.is_file():
            runpy.run_path(str(script), run_name="__main__")
            return 0
        module = bundled_module_name(script, root)
        if module and _importable(module):
            runpy.run_module(module, run_name="__main__", alter_sys=True)
            return 0
        print(f"FRIDAY: can't open file '{script}'", file=sys.stderr)
        return 2
    return None


# ---------------------------------------------------------------------------
# Roles, each in-process
# ---------------------------------------------------------------------------

def run_role(role: str, extra: list[str]) -> int:
    if role == "server":
        import server                                        # registers at import

        server.main()
        return 0
    if role == "ui":
        from scripts import run_ui

        sys.argv = ["run_ui.py", *extra]
        run_ui.main()
        return 0
    if role == "agent":
        import agent_friday

        # `start` is production mode. `dev` re-imports on every file change
        # and overflows the Windows environment block on a large tree
        # (AGENTS.md, operational notes); it is never the default here.
        sys.argv = ["agent_friday.py", *(extra or ["start"])]
        agent_friday.main()
        return 0
    print(f"FRIDAY: unknown role '{role}' (expected one of {', '.join(ROLES)})",
          file=sys.stderr)
    return 2


def role_command(role: str, extra: list[str] | None = None) -> list[str]:
    """The command that runs `role` in a fresh process of this same runtime."""
    extra = list(extra or [])
    if frozen():
        return [sys.executable, "--role", role, *extra]
    return [sys.executable, "-m", "friday.launcher", "--role", role, *extra]


# ---------------------------------------------------------------------------
# Readiness
# ---------------------------------------------------------------------------

def port_open(host: str, port: int, timeout: float = 0.5) -> bool:
    with socket.socket() as sock:
        sock.settimeout(timeout)
        return sock.connect_ex((host, port)) == 0


def http_ok(url: str, timeout: float = 1.5) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return 200 <= response.status < 300
    except (urllib.error.URLError, OSError, ValueError):
        return False


def wait_for(probe, timeout: float, process: subprocess.Popen | None = None) -> bool:
    """Poll `probe()` until true, the deadline, or the process dying."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            return False
        if probe():
            return True
        time.sleep(POLL_S)
    return False


# ---------------------------------------------------------------------------
# Supervisor
# ---------------------------------------------------------------------------

def _say(message: str) -> None:
    print(f"[friday] {message}", flush=True)


#: A Hermes profile name is a directory name and a CLI argument; the one that
#: comes from the environment is held to the shape Hermes itself accepts.
_PROFILE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")

#: `hermes profile create` imports Hermes's whole runtime, tens of seconds on a
#: loaded host (hermes_version's measurement); this is the one place it is run.
HERMES_PROFILE_TIMEOUT_S = 120.0


def hermes_profile_name() -> str:
    """The profile FRIDAY's gateway runs under; empty when the override is off."""
    from friday import hermes_bridge

    return os.getenv(hermes_bridge.ENV_PROFILE, hermes_bridge.DEFAULT_PROFILE).strip()


def ensure_hermes_profile(binary: str | None, *, timeout: float = HERMES_PROFILE_TIMEOUT_S,
                          runner=subprocess.run, announce=lambda message: None) -> str:
    """Create FRIDAY's Hermes profile when Hermes is installed and the profile
    is missing. Returns one console line about what happened, or "" when there
    was nothing to do. Never raises: a failed creation is reported and the
    launch goes on, because the gateway is only started when work needs it.

    A profile pinned by FRIDAY_HERMES_PROFILE_HOME is the operator's own and is
    left alone, as is an install without Hermes (the doctor reports that)."""
    from friday import hermes_bridge

    if not binary or os.getenv(hermes_bridge.ENV_PROFILE_HOME, "").strip():
        return ""
    name = hermes_profile_name()
    if not name:
        return ""
    if not _PROFILE_NAME.fullmatch(name):
        return f"hermes profile: '{name}' is not a valid profile name; not created"
    if hermes_bridge.profile_home(name):
        return ""
    command = f"hermes profile create {name} --clone"
    announce(f"creating the Hermes profile '{name}' (first run only; Hermes starts slowly)")
    try:
        done = runner([binary, "profile", "create", name, "--clone"], capture_output=True,
                      text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return f"hermes profile: '{name}' was not created within {timeout:.0f}s; run: {command}"
    except (OSError, subprocess.SubprocessError) as exc:
        return f"hermes profile: could not run hermes ({type(exc).__name__}); run: {command}"
    if done.returncode != 0:
        lines = ((done.stderr or done.stdout or "").strip().splitlines() or ["no output"])
        return (f"hermes profile: '{name}' was not created (exit {done.returncode}: "
                f"{lines[-1].strip()[:160]}); run: {command}")
    if not hermes_bridge.profile_home(name):
        return f"hermes profile: hermes reported success but '{name}' is still missing; run: {command}"
    return f"hermes profile: created '{name}' (cloned from the default profile)"


def register_skills_at_startup() -> str:
    """Register the skills found on disk that the registry does not know, so a
    fresh install has them without a manual `sync`. One console line, or ""
    when there was nothing to register; never raises."""
    try:
        from friday.skills import cli as skills_cli

        added = skills_cli.register_unregistered(skills_cli.Services())
    except Exception as exc:                                # noqa: BLE001
        return (f"skills: could not register the skills on disk ({type(exc).__name__}: "
                f"{str(exc)[:120]}); run: FRIDAY.exe -m friday.skills.cli sync")
    if not added:
        return ""
    return f"skills: registered {len(added)} from disk ({', '.join(name for name, _, _ in added[:6])})"


def console_will_close() -> bool:
    """
    Whether this process is the only one attached to its console - the
    case of a double-clicked executable, whose window disappears the
    moment it exits. A command typed in a terminal has the shell attached
    too, so the window stays and a "press Enter" hold would only be in the
    way. Never true off Windows.
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32                   # type: ignore[attr-defined]
        pids = (ctypes.c_uint * 2)()
        count = kernel32.GetConsoleProcessList(pids, 2)
        return count == 1
    except Exception:                                       # noqa: BLE001
        return False


def hold_if_the_window_would_close(code: int) -> None:
    """Keep a failure readable when someone double-clicked the executable."""
    if code and console_will_close():
        try:
            input("\nPress Enter to close...")
        except EOFError:
            pass


def child_env() -> dict[str, str]:
    return dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8",
                FRIDAY_LAUNCHER_PID=str(os.getpid()))


def _tee(label: str, process: subprocess.Popen, log_path: Path) -> None:
    """Echo a child's output to the console and append it to its log file."""
    with open(log_path, "a", encoding="utf-8", errors="replace", buffering=1) as log:
        log.write(f"\n=== {label} started {time.strftime('%Y-%m-%d %H:%M:%S')} pid {process.pid} ===\n")
        assert process.stdout is not None
        for line in process.stdout:
            text = line.rstrip()
            log.write(text + "\n")
            print(f"[{label}] {text}", flush=True)


def start_role(role: str, extra: list[str], logs_dir: Path,
               registry: list[tuple[str, subprocess.Popen]]) -> subprocess.Popen:
    logs_dir.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen(
        role_command(role, extra),
        cwd=str(exe_dir()),
        env=child_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    registry.append((role, process))
    threading.Thread(target=_tee, args=(role, process, logs_dir / f"{role}.log"),
                     daemon=True).start()
    return process


def terminate_tree(process: subprocess.Popen, grace: float = 8.0) -> None:
    """Stop a role and everything it spawned (LiveKit job processes)."""
    if process.poll() is not None:
        return
    try:
        import psutil

        parent = psutil.Process(process.pid)
        family = parent.children(recursive=True) + [parent]
        for member in family:
            try:
                member.terminate()
            except psutil.Error:
                pass
        _, alive = psutil.wait_procs(family, timeout=grace)
        for member in alive:
            try:
                member.kill()
            except psutil.Error:
                pass
    except Exception:                                       # noqa: BLE001
        process.kill()


def voice_worker_refusal(probe=None) -> str | None:
    """Why the voice worker must not be started, or None when it may be.

    A worker started on a template or refused LiveKit credential retries its
    401 until ADA_MAX_RETRY is spent, so MISSING, PLACEHOLDER and REJECTED
    stop it. UNVERIFIED (LiveKit did not answer within the probe's bound) does
    not: the worker has its own retry for an outage, and refusing here would
    keep voice off until the next launch for a transient network blip."""
    verdicts = credentials.check_livekit(*(os.getenv(name) for name in REQUIRED_FOR_AGENT),
                                         live=True, probe=probe)
    worst = credentials.worst_livekit(verdicts)
    if worst.state in (credentials.MISSING, credentials.PLACEHOLDER, credentials.REJECTED):
        return f"LiveKit credentials are not usable ({worst.detail})"
    return None


def run_all(*, with_ui: bool = True, with_agent: bool = True,
            ui_args: list[str] | None = None, agent_args: list[str] | None = None) -> int:
    root = app_root()
    home = state_root()
    moved = adopt_legacy_state()
    ensure_state_dirs()
    logs_dir = home / "logs"
    _say(version())
    _say(f"app    : {root}")
    _say(f"state  : {home}")
    _say(f"logs   : {logs_dir}")
    if moved:
        _say(f"moved {len(moved)} item(s) of an earlier build's state out of _internal into {home}")
    env_path = load_env()
    _say(f"env    : {env_path}" if env_path else
         "env    : none found - the voice worker cannot register without LiveKit credentials")

    if not (root / "ui" / "index.html").is_file() and with_ui:
        _say("ui/index.html is missing from this build - the control room will not serve")

    for note in (register_skills_at_startup(),
                 ensure_hermes_profile(shutil.which("hermes"), announce=_say)):
        if note:
            _say(note)

    jobs: list[tuple[str, subprocess.Popen]] = []
    try:
        if port_open(MCP_HOST, MCP_PORT):
            _say(f"an MCP server already answers on :{MCP_PORT} - attaching to it")
        else:
            _say("starting the MCP server")
            server = start_role("server", [], logs_dir, jobs)
            if not wait_for(lambda: port_open(MCP_HOST, MCP_PORT), MCP_WAIT_S, server):
                _say(f"the MCP server did not answer on :{MCP_PORT} within {MCP_WAIT_S:.0f}s; "
                     f"see {logs_dir / 'server.log'}")
                return 1
            _say(f"MCP server up: http://{MCP_HOST}:{MCP_PORT}/sse")

        if with_ui:
            if port_open(UI_HOST, UI_PORT):
                _say(f"a control room already answers on :{UI_PORT} - leaving it")
            else:
                _say("starting the control room")
                ui = start_role("ui", list(ui_args or []), logs_dir, jobs)
                ui_url = f"http://{UI_HOST}:{UI_PORT}/"
                if not wait_for(lambda: http_ok(ui_url + "health"), UI_WAIT_S, ui):
                    _say(f"the control room did not answer on :{UI_PORT} within {UI_WAIT_S:.0f}s; "
                         f"see {logs_dir / 'ui.log'}")
                    return 1
                _say(f"control room up: {ui_url}")

        if with_agent:
            refusal = voice_worker_refusal()
            if refusal:
                _say(f"not starting the voice worker: {refusal}; "
                     "the control room still has its own browser voice")
            else:
                _say("starting the voice worker (LiveKit, production mode)")
                start_role("agent", list(agent_args or ["start"]), logs_dir, jobs)

        _say("running. Ctrl+C stops everything this launcher started.")
        while True:
            for name, process in jobs:
                code = process.poll()
                if code is not None:
                    _say(f"'{name}' exited with code {code} - stopping the rest; "
                         f"see {logs_dir / (name + '.log')}")
                    return code or 1
            time.sleep(POLL_S)
    except KeyboardInterrupt:
        _say("stopping")
        return 0
    finally:
        for _, process in reversed(jobs):
            terminate_tree(process)
        if jobs:
            _say("all roles stopped")


# ---------------------------------------------------------------------------
# Stop / status
# ---------------------------------------------------------------------------

def is_friday_process(name: str, exe: str, cmdline: list[str], *,
                      self_exe: str, frozen_build: bool) -> bool:
    """
    Whether one process record belongs to this Friday runtime.

    Frozen: anything running from this exact executable (the launcher, the
    roles, LiveKit's `--multiprocessing-fork` children). Source: a Python
    that was started as a launcher role. Never a stray python.exe that
    merely mentions Friday somewhere in its arguments.
    """
    if frozen_build:
        return bool(exe) and Path(exe).resolve() == Path(self_exe).resolve()
    joined = " ".join(cmdline)
    return "python" in name.lower() and "friday.launcher" in joined and "--role" in joined


def friday_processes() -> list:
    import psutil

    me = os.getpid()
    found = []
    for process in psutil.process_iter(["pid", "name", "exe", "cmdline"]):
        info = process.info
        if info["pid"] == me:
            continue
        try:
            if is_friday_process(info.get("name") or "", info.get("exe") or "",
                                 info.get("cmdline") or [], self_exe=sys.executable,
                                 frozen_build=frozen()):
                found.append(process)
        except (psutil.Error, OSError):
            continue
    return found


def emergency_stop_everything(grace: float = 8.0) -> int:
    """
    STOP-001 from the terminal: latch the stop in the store every FRIDAY
    process shares - parking objectives and revoking network grants - and
    then end every FRIDAY process. The latch outlives them, so nothing new
    starts when they come back until the person resumes (`--resume`).
    """
    from friday import stop

    try:
        report = stop.emergency_stop("from the terminal", actor="person")
        _say(f"STOP latched: {len(report['paused'])} objective(s) parked, "
             f"{report['grants_revoked']} network grant(s) revoked")
    except Exception as exc:                                # noqa: BLE001 - said; the processes still end
        # The stop must never depend on the store being writable.
        _say(f"could not latch the stop ({type(exc).__name__}: {exc}); "
             f"ending the processes anyway")
    return stop_all(grace)


def resume_everything() -> int:
    """The person clears the emergency stop. Parked objectives stay parked."""
    from friday import stop

    resumed = stop.resume(actor="person")
    _say("STOP cleared; parked objectives stay parked until each is resumed"
         if resumed else "nothing was stopped")
    return 0


def stop_all(grace: float = 8.0) -> int:
    import psutil

    targets = friday_processes()
    if not targets:
        _say("nothing to stop")
    else:
        for process in targets:
            try:
                process.terminate()
            except psutil.Error:
                pass
        _, alive = psutil.wait_procs(targets, timeout=grace)
        for process in alive:
            try:
                process.kill()
            except psutil.Error:
                pass
        _say(f"stopped {len(targets)} process(es): {', '.join(str(p.pid) for p in targets)}")
    time.sleep(0.5)
    for label, host, port in (("mcp", MCP_HOST, MCP_PORT), ("ui", UI_HOST, UI_PORT)):
        state = "still listening" if port_open(host, port) else "free"
        _say(f":{port} ({label}) {state}")
    return 0


def paths() -> int:
    """Every resolved location, as JSON, for scripts and for humans."""
    from friday.config import runtime_paths

    found = runtime_paths()
    found.update({"app_root": str(app_root()), "exe_dir": str(exe_dir()),
                  "version": version(), "manifest": manifest()})
    print(json.dumps(found, indent=2), flush=True)
    return 0


def status() -> int:
    _say(version())
    _say(f"state  : {state_root()}")
    for label, host, port in (("mcp", MCP_HOST, MCP_PORT), ("ui", UI_HOST, UI_PORT)):
        _say(f":{port} ({label}) {'listening' if port_open(host, port) else 'not listening'}")
    processes = friday_processes()
    if not processes:
        _say("no Friday processes from this runtime")
    for process in processes:
        try:
            args = " ".join(process.cmdline()[1:])[:100]
        except Exception:                                   # noqa: BLE001
            args = "?"
        _say(f"pid {process.pid}: {args}")
    return 0


# ---------------------------------------------------------------------------
# Doctor
# ---------------------------------------------------------------------------

#: Without these the voice worker cannot register, so their absence is a
#: FAIL for the agent role. Everything else in friday.config.VARIABLES is
#: provider-specific and merely narrows what Friday can do.
REQUIRED_FOR_AGENT = ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET")

#: A credential is a USED variable whose name says so. Settings with code
#: defaults (STT_PROVIDER, LLM_ROLE, ...) are not reported: unset is normal.
_CREDENTIAL_SUFFIXES = ("_API_KEY", "_SECRET", "_TOKEN")


def provider_credential_names() -> list[str]:
    """USED credential variables from friday.config, LiveKit's excluded."""
    from friday.config import USED, VARIABLES

    return [name for name, (kind, _) in VARIABLES.items()
            if kind == USED and name.endswith(_CREDENTIAL_SUFFIXES)
            and name not in REQUIRED_FOR_AGENT]


#: Modules the roles cannot start without. A frozen build that lost one to
#: a packaging omission must say so here rather than in a stack trace.
CORE_IMPORTS = ("mcp.server.fastmcp", "starlette", "uvicorn", "livekit.agents",
                "livekit.plugins.silero", "onnxruntime", "av", "psutil", "dotenv",
                "trafilatura", "friday.store", "friday.tools", "friday.ui_server")


def _grouped(verdicts: dict) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    for name, verdict in verdicts.items():
        groups.setdefault(verdict.state, []).append(name)
    return groups


def _describe(groups: dict[str, list[str]], order: tuple[str, ...]) -> str:
    return "; ".join(f"{state.lower()}: {', '.join(groups[state])}"
                     for state in order if groups.get(state))


def livekit_check(verdicts: dict) -> tuple[str, str]:
    """(status, detail) for the LiveKit variables. Unset is a FAIL (the voice
    worker cannot register); a template or refused value is DEGRADED, because
    the control room keeps its own browser voice; UNVERIFIED is not a defect
    anyone observed, so it is reported as such and does not lower the status."""
    groups = _grouped(verdicts)
    order = (credentials.MISSING, credentials.PLACEHOLDER, credentials.REJECTED,
             credentials.UNVERIFIED, credentials.VALID)
    detail = _describe(groups, order)
    if credentials.MISSING in groups:
        return FAIL, f"{detail} - the voice worker cannot register"
    if credentials.PLACEHOLDER in groups or credentials.REJECTED in groups:
        return DEGRADED, f"{detail} - the voice worker is not started; set real LiveKit credentials"
    if credentials.UNVERIFIED in groups:
        return PASS, f"{detail} (set; LiveKit did not confirm them)"
    return PASS, "LIVEKIT_URL / API_KEY / API_SECRET were accepted by LiveKit"


def provider_check(verdicts: dict) -> tuple[str, str]:
    """(status, detail) for the STT/LLM/TTS keys. DEGRADED when any key is a
    template or refused, or when nothing usable is set; an unset key alone is
    normal (a provider may be deliberately unused)."""
    groups = _grouped(verdicts)
    order = (credentials.VALID, credentials.UNVERIFIED, credentials.PLACEHOLDER,
             credentials.REJECTED, credentials.MISSING)
    bad = credentials.PLACEHOLDER in groups or credentials.REJECTED in groups
    usable = credentials.VALID in groups or credentials.UNVERIFIED in groups
    if not usable and not bad:
        return DEGRADED, ("none set - no STT, LLM or TTS provider can be used"
                          if verdicts else "no provider credentials are defined")
    return (DEGRADED if bad else PASS), _describe(groups, order)


def doctor_checks(root: Path | None = None, env_path: Path | None = None,
                  state: Path | None = None, *, live: bool = False,
                  provider_probe=None, livekit_probe=None) -> list[tuple[str, str, str]]:
    """
    (name, PASS|DEGRADED|FAIL, detail) per check. Values of secrets are
    never printed; only names are. `root` is the application (ui assets);
    `state` the user's state root, defaulting to `root` when a caller passes
    only that (tests) and to friday.config otherwise. `live` asks each
    provider whether it accepts its credential (bounded probes); without it
    a credential is only checked for being set and not a template. The
    probe arguments replace the live ones (tests).
    """
    explicit_root = root is not None
    root = root or app_root()
    home = state or (root if explicit_root else state_root())
    checks: list[tuple[str, str, str]] = []

    checks.append(("application", PASS,
                   f"{version()}; frozen={frozen()}; python {sys.version.split()[0]}; root {root}"))

    try:
        from friday.config import portable_mode

        portable = portable_mode()
    except Exception:                                       # noqa: BLE001
        portable = False
    checks.append(("state root", PASS,
                   f"{home}" + (" (portable: beside the executable)" if portable else "")
                   + (f" (FRIDAY_HOME)" if os.getenv("FRIDAY_HOME", "").strip() else "")))

    leftovers = []
    if frozen() and not explicit_root:
        for name in LEGACY_STATE_DIRS:
            legacy = root / name
            if legacy.is_dir() and any(item.name not in SHIPPED_DATA or name != "data"
                                       for item in legacy.iterdir()):
                leftovers.append(str(legacy))
    if leftovers:
        checks.append(("legacy state", DEGRADED,
                       "an earlier build's state is still under _internal: "
                       + ", ".join(leftovers) + " - it is moved to the state root on the next start"))

    if env_path is None:
        checks.append(("env file", DEGRADED,
                       "no .env found; copy .env.example to " + str(home / "config" / ".env")
                       + " and fill it in "
                       f"(looked in: {', '.join(str(p) for p in env_file_candidates())})"))
    else:
        checks.append(("env file", PASS, str(env_path)))

    provider_probe = provider_probe or (credentials.live_probe if live else None)
    livekit = credentials.check_livekit(
        *(os.getenv(name) for name in REQUIRED_FOR_AGENT),
        live=live or livekit_probe is not None, probe=livekit_probe)
    checks.append(("livekit credentials", *livekit_check(livekit)))

    verdicts: dict = {}
    try:
        keys = provider_credential_names()
        verdicts = credentials.check_credentials({name: os.getenv(name) for name in keys},
                                                 probe=provider_probe)
        checks.append(("provider credentials", *provider_check(verdicts)))
    except Exception as exc:                                # noqa: BLE001
        checks.append(("provider credentials", FAIL, f"friday.config failed to import: {exc}"))

    for name in ("data", "logs", "vault", "config", "skills"):
        target = home / name
        try:
            target.mkdir(parents=True, exist_ok=True)
            probe = target / ".write-probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            checks.append((f"writable {name}/", PASS, str(target)))
        except OSError as exc:
            checks.append((f"writable {name}/", FAIL, f"{target}: {exc}"))

    try:
        from friday import store

        db = Path(os.getenv("ADA_DB") or store.DEFAULT_DB)
        handle = store.Store(db)
        try:
            tables = handle._conn.execute(
                "SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
        finally:
            handle._conn.close()
        checks.append(("database", PASS, f"{db} ({tables} tables)"))
    except Exception as exc:                                # noqa: BLE001
        checks.append(("database", FAIL, f"could not open or initialise: {exc}"))

    broken = []
    for module in CORE_IMPORTS:
        try:
            __import__(module)
        except Exception as exc:                            # noqa: BLE001
            broken.append(f"{module} ({type(exc).__name__}: {str(exc)[:80]})")
    checks.append(("core imports", FAIL if broken else PASS,
                   "; ".join(broken) if broken else f"{len(CORE_IMPORTS)} modules import"))

    index = root / "ui" / "index.html"
    models = sorted((root / "ui" / "models").glob("*.bin")) if (root / "ui" / "models").is_dir() else []
    checks.append(("ui assets", PASS if index.is_file() else FAIL,
                   f"{index} ({len(models)} recognition model files)"
                   if index.is_file() else f"{index} missing"))

    try:
        from scripts import run_ui

        browser = run_ui._browser()
    except Exception:                                       # noqa: BLE001
        browser = None
    checks.append(("browser", PASS if browser else DEGRADED,
                   browser or "no Chrome/Edge found; the control room opens in the default browser"))

    hermes = shutil.which("hermes")
    checks.append(("hermes", PASS if hermes else DEGRADED,
                   f"{hermes} ({hermes_version(hermes)})" if hermes
                   else "hermes not on PATH; execution delegation is unavailable"))
    checks.append(hermes_profile_check(hermes))

    for label, host, port in (("mcp", MCP_HOST, MCP_PORT), ("ui", UI_HOST, UI_PORT)):
        checks.append((f"port {port} ({label})", PASS,
                       "in use (a Friday may already be running)" if port_open(host, port) else "free"))

    checks.extend(skills_check(home))
    checks.append(onboarding_check(home))
    checks.append(voice_check({**livekit, **verdicts}))
    checks.append(security_check(home, env_path))

    signed = manifest().get("signing_status") or "NOT_CONFIGURED"
    checks.append(("code signing", PASS if signed == "SIGNED" else DEGRADED, signed))
    return checks


def hermes_profile_check(binary: str | None) -> tuple[str, str, str]:
    """The Hermes profile FRIDAY's gateway runs under. Read-only: the first
    start creates it (ensure_hermes_profile); the doctor says whether it is
    there. Without Hermes there is no profile to want, and the `hermes` row
    already carries that."""
    name = "hermes profile"
    if not binary:
        return (name, PASS, "not applicable: hermes is not installed")
    try:
        from friday import hermes_bridge

        profile = hermes_profile_name()
        if not profile:
            return (name, PASS, "none selected (FRIDAY_HERMES_PROFILE is empty); "
                    "the gateway uses the profile the environment resolves")
        home = hermes_bridge.profile_home(profile)
    except Exception as exc:                                # noqa: BLE001
        return (name, DEGRADED, f"could not be read: {type(exc).__name__}: {str(exc)[:120]}")
    if home:
        return (name, PASS, f"'{profile}' at {home}")
    return (name, DEGRADED, f"'{profile}' does not exist; the first start creates it, or run: "
            f"hermes profile create {profile} --clone")


def hermes_version(binary: str, timeout: float = 45.0) -> str:
    """The first line of `hermes --version`, or why it could not be read.
    Hermes's CLI imports its whole runtime to answer, which takes tens of
    seconds on a loaded host; a timeout is reported as such, not as absence."""
    try:
        completed = subprocess.run([binary, "--version"], capture_output=True, text=True,
                                   encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return f"present; version not read within {timeout:.0f}s (Hermes starts slowly on a loaded host)"
    except (OSError, subprocess.SubprocessError) as exc:
        return f"version unknown ({type(exc).__name__})"
    line = (completed.stdout or completed.stderr or "").strip().splitlines()
    return line[0].strip() if line else "version unknown (no output)"


def skills_check(home: Path) -> list[tuple[str, str, str]]:
    """The skill layer: tiers on disk, the registry, what is held back."""
    try:
        from friday.skills import contracts as SK
        from friday.skills import files as SF
        from friday.skills.registry import SkillRegistry

        roots = SF.tier_roots()
        on_disk = [(tier, skill) for tier, root in roots.items() for skill in SF.discover(root)]
        registry = SkillRegistry()
        records = registry.list()
        by_state: dict[str, int] = {}
        for record in records:
            by_state[record.state] = by_state.get(record.state, 0) + 1
        known = {record.name for record in records}
        unregistered = sorted(skill.name for _, skill in on_disk if skill.name not in known)
        held = by_state.get(SK.QUARANTINED, 0) + by_state.get(SK.BROKEN, 0)
        stale = by_state.get(SK.NEEDS_REVALIDATION, 0) + by_state.get(SK.STALE, 0)
        detail = (f"{len(on_disk)} on disk in {len(roots)} tier(s), {len(records)} registered"
                  + (", " + ", ".join(f"{k}={v}" for k, v in sorted(by_state.items())) if by_state else ""))
        state = PASS
        if unregistered:
            state = DEGRADED
            detail += f"; unregistered: {', '.join(unregistered[:5])} (run: FRIDAY.exe -m friday.skills.cli sync)"
        if held or stale:
            state = DEGRADED
            detail += f"; {held} quarantined/broken, {stale} awaiting revalidation"
        return [("skills", state, detail)]
    except Exception as exc:                                # noqa: BLE001
        return [("skills", FAIL, f"the skill layer failed to load: {type(exc).__name__}: {str(exc)[:120]}")]


def onboarding_check(home: Path) -> tuple[str, str, str]:
    """How far the interview has got, and whether FRIDAY is useful yet.

    PASS even when it has not started: the answers are the person's to give,
    so an unanswered interview is a setup step still to take, not a defect in
    the install. The detail says how many essential questions are left and
    how to answer them; only an interview that cannot load is a FAIL.
    """
    try:
        from friday.onboarding import Onboarding, state_root_marker

        progress = Onboarding().progress()
        marker = state_root_marker(home)
        answered, total = progress["answered"], progress["total"]
        detail = f"{answered}/{total} answered"
        if progress["useful_now"]:
            return ("onboarding", PASS,
                    detail + "; the essentials are done" +
                    ("; marker present" if marker.exists() else ""))
        return ("onboarding", PASS,
                detail + f"; setup step: {progress['essential_remaining']} essential question(s) "
                "left to answer (run: FRIDAY.exe -m friday.onboarding next)")
    except Exception as exc:                                # noqa: BLE001
        return ("onboarding", FAIL,
                f"the interview failed to load: {type(exc).__name__}: {str(exc)[:120]}")


def cli_brain() -> str | None:
    """The signed-in coding-agent CLI the voice agent answers through when its
    API model cannot (providers.build_resilient_llm), or None. Reads the
    sign-in state locally; makes no model call."""
    from friday import cli_model_provider

    for backend in cli_model_provider.discover_backends():
        if backend.entry().get("authenticated"):
            return backend.id
    return None


def voice_check(verdicts: dict | None = None) -> tuple[str, str, str]:
    """Speech in and out: provider selection and its credentials (names
    only), and whether a microphone is visible to the audio stack.

    `verdicts` are the credential verdicts the doctor already has, by
    variable name. A key is set is not a key that works: a stage whose own key
    is a template, the wrong format, or refused by its provider cannot run, so
    that is a FAIL naming the variable and the provider's reason. A stage whose
    key is simply unset is DEGRADED, as before. The llm stage is the exception:
    with a signed-in CLI brain the agent still answers, without tool calls, so
    an unusable llm key is DEGRADED and the row names the brain."""
    parts, degraded, failed = [], False, False
    verdicts = verdicts or {}
    try:
        from friday import health, providers

        report = health.report()
        tables = {"stt": providers.STT_CREDENTIALS, "llm": providers.LLM_CREDENTIALS,
                  "tts": providers.TTS_CREDENTIALS}
        for label in ("stt", "tts", "llm"):
            block = report.get(label) or {}
            provider = block.get("provider", "?")
            refused = [verdicts[name].detail for name in tables[label].get(provider, ())
                       if name in verdicts and verdicts[name].state in
                       (credentials.PLACEHOLDER, credentials.REJECTED)]
            shown = f"{label} {provider}" if label != "llm" else \
                f"llm {block.get('backend', '?')}/{block.get('model', '?')}"
            brain = ""
            if label == "llm" and (refused or not (block.get("error") or block.get("configured"))):
                brain = cli_brain() or ""
            via = f"; answering through {brain} (no tool calls)" if brain else ""
            if refused:
                parts.append(f"{shown}: {'; '.join(refused)}{via}")
                degraded = degraded or bool(brain)
                failed = failed or not brain
            elif block.get("error"):
                parts.append(f"{shown}: {block['error'][:60]}")
                degraded = True
            elif block.get("configured"):
                parts.append(f"{shown} configured" if label != "llm" else shown)
            else:
                parts.append(f"{shown} missing {', '.join(block.get('missing', []))}{via}")
                degraded = True
    except Exception as exc:                                # noqa: BLE001
        parts.append(f"provider report failed: {type(exc).__name__}")
        degraded = True
    try:
        import sounddevice

        inputs = [d for d in sounddevice.query_devices() if int(d.get("max_input_channels", 0)) > 0]
        parts.append(f"{len(inputs)} input device(s)")
        if not inputs:
            degraded = True
    except Exception as exc:                                # noqa: BLE001
        parts.append(f"microphone unknown ({type(exc).__name__})")
        degraded = True
    return ("voice", FAIL if failed else DEGRADED if degraded else PASS, "; ".join(parts))


def security_check(home: Path, env_path: Path | None) -> tuple[str, str, str]:
    """The kernel modules are present, and credentials live outside the
    application directory. Values are never read."""
    problems = []
    try:
        from friday.self_upgrade import KERNEL_PATHS

        modules = [p[:-3].replace("/", ".") for p in KERNEL_PATHS
                   if p.startswith("friday/") and p.endswith(".py")]
        missing = [m for m in modules if importlib.util.find_spec(m) is None]
        if missing:
            problems.append(f"kernel modules missing: {', '.join(missing[:4])}")
        kernel = f"{len(modules) - len(missing)}/{len(modules)} kernel modules present"
    except Exception as exc:                                # noqa: BLE001
        kernel = f"kernel list unreadable ({type(exc).__name__})"
        problems.append(kernel)
    if env_path is not None and frozen():
        # Only a packaged build separates the application from its state; a
        # source checkout is both by design, and portable mode puts them
        # side by side on purpose.
        from friday.config import portable_mode

        try:
            inside = env_path.resolve().is_relative_to(app_root().resolve())
        except (OSError, ValueError):
            inside = False
        if inside and not portable_mode():
            problems.append(f"credentials inside the application directory: {env_path}")
    detail = kernel + "; secrets reported by name only"
    if problems:
        return ("security", FAIL if "missing" in problems[0] else DEGRADED, "; ".join(problems) + "; " + detail)
    return ("security", PASS, detail)


#: Checks about this machine's setup rather than the build: credentials, voice, LiveKit, Hermes,
#: skills, onboarding, signing. A fresh install has none of them yet, and FRIDAY.exe --no-agent
#: runs without LiveKit, so they never make the artifact itself FAIL (release candidate 4c0815d's
#: build failed only because this host's API keys were placeholders).
CONFIGURATION_CHECKS = frozenset({
    "env file", "livekit credentials", "provider credentials", "voice", "hermes", "hermes profile",
    "skills", "onboarding", "code signing",
})


def in_scope(checks: list[tuple[str, str, str]], scope: str) -> list[tuple[str, str, str]]:
    """`artifact` keeps the checks about the build itself; `all` keeps everything."""
    if scope == "artifact":
        return [c for c in checks if c[0] not in CONFIGURATION_CHECKS]
    return list(checks)


def verdict(checks: list[tuple[str, str, str]]) -> str:
    states = {state for _, state, _ in checks}
    if FAIL in states:
        return FAIL
    if DEGRADED in states:
        return DEGRADED
    return PASS


def doctor(scope: str = "all") -> int:
    # The migration runs here too: a doctor that bootstrapped an empty
    # database in the new state root would make the old one unreachable.
    moved = adopt_legacy_state()
    if moved:
        print(f"  moved {len(moved)} item(s) of an earlier build's state into {state_root()}", flush=True)
    env_path = load_env()
    checks = doctor_checks(env_path=env_path, live=True)
    width = max(len(name) for name, _, _ in checks)
    for name, state, detail in checks:
        print(f"  {state:<8} {name:<{width}}  {detail}", flush=True)
    result = verdict(in_scope(checks, scope))
    print(f"\nFRIDAY doctor: {result}" + (" (artifact checks; setup checks listed above)"
                                          if scope == "artifact" else ""), flush=True)
    return {PASS: 0, DEGRADED: 1, FAIL: 2}[result]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="FRIDAY", description="Start, stop and diagnose Friday.",
        epilog="With no options: start the MCP server, the control room and the voice worker.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--stop", action="store_true", help="stop every Friday process from this runtime")
    group.add_argument("--resume", action="store_true",
                       help="clear an emergency stop so new actions may start again")
    group.add_argument("--status", action="store_true", help="ports and processes")
    group.add_argument("--doctor", action="store_true", help="diagnostics (exit 0 PASS, 1 DEGRADED, 2 FAIL)")
    group.add_argument("--paths", action="store_true", help="every resolved location, as JSON")
    group.add_argument("--version", action="store_true")
    parser.add_argument("--doctor-scope", choices=("all", "artifact"), default="all",
                        help="with --doctor: 'artifact' judges the build only, not this machine's setup")
    parser.add_argument("--no-ui", action="store_true", help="do not start the control room")
    parser.add_argument("--no-agent", action="store_true", help="do not start the voice worker")
    parser.add_argument("--no-browser", action="store_true", help="serve the control room without opening a window")
    parser.add_argument("--password", action="store_true", help="control room gate by PIN instead of face")
    parser.add_argument("--bypass-face", action="store_true", help="face recognition off for this run")
    return parser


def debug_mode() -> bool:
    """FRIDAY-Debug.exe, or FRIDAY_DEBUG=1: the same program with its
    diagnostics on - DEBUG logging to stderr and fault handlers, so a start
    that dies before its first log line still says where."""
    flag = os.getenv("FRIDAY_DEBUG", "").strip().lower() in ("1", "true", "yes")
    return flag or Path(sys.executable).stem.lower().endswith("-debug")


def enable_diagnostics() -> None:
    import faulthandler
    import logging

    faulthandler.enable()
    logging.basicConfig(level=logging.DEBUG, stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    os.environ.setdefault("FRIDAY_DEBUG", "1")             # the roles inherit it
    _say(f"diagnostics on: {sys.executable} frozen={frozen()} argv={sys.argv[1:]}")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    if debug_mode():
        enable_diagnostics()

    if frozen():
        served = maybe_run_as_python(argv)
        if served is not None:
            return served

    if argv[:1] == ["--role"]:
        if len(argv) < 2:
            print("FRIDAY: --role needs a name", file=sys.stderr)
            return 2
        adopt_legacy_state()
        load_env()
        ensure_state_dirs()
        return run_role(argv[1], argv[2:])

    options = build_parser().parse_args(argv)
    if options.version:
        print(version())
        return 0
    if options.paths:
        return paths()
    if options.doctor:
        return doctor(options.doctor_scope)
    if options.stop:
        return emergency_stop_everything()
    if options.resume:
        return resume_everything()
    if options.status:
        return status()

    ui_args = [flag for flag, on in (("--no-browser", options.no_browser),
                                     ("--password", options.password),
                                     ("--bypass-face", options.bypass_face)) if on]
    return run_all(with_ui=not options.no_ui, with_agent=not options.no_agent, ui_args=ui_args)


if __name__ == "__main__":
    code = main()
    hold_if_the_window_would_close(code)
    sys.exit(code)
