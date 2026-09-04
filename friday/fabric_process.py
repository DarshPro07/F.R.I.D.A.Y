"""
friday/fabric_process.py -- the fabric owns its children, or it owns nothing.

`fabric.activate()` called an adapter's `start()` and then polled `health()`
immediately. Nothing waited for readiness, nothing captured output, nothing
noticed a crash, nothing cleaned up an orphan. `SIDECAR` and `MCP` were words
in `INTEGRATION_MODES` with no runtime behind them, which is why exactly one of
sixteen providers owned a process and why every copyleft upstream stayed
unintegrated: the isolation the licence invariant demands had nowhere to run.

This is that runtime. It is deliberately small and Windows-first: one machine,
single-digit children, no containers. What it does provide is the five things
whose absence made a sidecar unusable -- a port, a readiness gate, its logs, a
bounded restart, and a guarantee that stopping actually stops it.

The environment scrub is the part to read twice. A child gets PATH, SYSTEMROOT,
TEMP and exactly what its Spec declares. It does NOT inherit Friday's
environment, because that is where GOOGLE_API_KEY and every other credential
lives, and handing all of them to an unaudited clone is the leak the
permission gate in `fabric.call()` exists to prevent.
"""
from __future__ import annotations

import logging
import os
import pathlib
import socket
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

ROOT = pathlib.Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / "logs" / "fabric"

STARTING = "STARTING"
READY = "READY"
CRASHED = "CRASHED"
STOPPED = "STOPPED"

#: Lines kept in memory per child. A health probe has to be able to quote a
#: reason without touching the disk in the middle of a voice turn.
LOG_LINES = 2000

#: Environment a child is allowed to inherit. Everything else must be declared.
PASSTHROUGH = ("PATH", "SYSTEMROOT", "SystemRoot", "TEMP", "TMP", "COMSPEC",
               "PATHEXT", "WINDIR", "USERPROFILE", "HOME", "LANG")


class ProcessError(RuntimeError):
    """A child could not be started, or would not become ready."""


# --- readiness -------------------------------------------------------------


class Ready:
    """How to tell a child is actually up. Subclasses implement `check`."""

    def check(self, child: "Child") -> bool:  # pragma: no cover - interface
        raise NotImplementedError


@dataclass(frozen=True)
class Immediate(Ready):
    """Up means running. For one-shot commands and trivial children."""

    def check(self, child: "Child") -> bool:
        return child.alive()


@dataclass(frozen=True)
class LogLine(Ready):
    """Up when a substring appears on stdout or stderr."""

    marker: str

    def check(self, child: "Child") -> bool:
        return any(self.marker in line for line in child.log_tail(LOG_LINES))


@dataclass(frozen=True)
class TcpPort(Ready):
    """Up when the child's allocated port accepts a connection."""

    def check(self, child: "Child") -> bool:
        if not child.port:
            return False
        with socket.socket() as sock:
            sock.settimeout(0.4)
            return sock.connect_ex(("127.0.0.1", child.port)) == 0


@dataclass(frozen=True)
class HttpOk(Ready):
    """Up when a path on the child's port answers below 500."""

    path: str = "/"

    def check(self, child: "Child") -> bool:
        if not child.port:
            return False
        try:
            import httpx
            resp = httpx.get(f"http://127.0.0.1:{child.port}{self.path}",
                             timeout=1.0)
            return resp.status_code < 500
        except Exception:  # noqa: BLE001
            return False


# --- the declaration -------------------------------------------------------


@dataclass
class Spec:
    """How to start one child. Declared by an adapter, executed here."""

    argv: tuple[str, ...]
    cwd: pathlib.Path | None = None
    #: ADDED to the scrubbed base. `{port}` is substituted where it appears.
    env: dict[str, str] = field(default_factory=dict)
    ready: Ready = field(default_factory=Immediate)
    #: True when the child needs a port; it is allocated and exported.
    needs_port: bool = False
    port_env: str = "PORT"
    stop_timeout: float = 10.0
    max_restarts: int = 3
    restart_window: float = 300.0
    marker: str = ""


@dataclass
class Child:
    provider_id: str
    spec: Spec
    popen: subprocess.Popen | None = None
    state: str = STARTING
    port: int = 0
    started_at: float = 0.0
    restarts: int = 0
    last_error: str = ""
    exit_code: int | None = None
    _lines: deque = field(default_factory=lambda: deque(maxlen=LOG_LINES))
    _restart_times: list = field(default_factory=list)

    def alive(self) -> bool:
        return self.popen is not None and self.popen.poll() is None

    def log_tail(self, count: int = 200) -> list[str]:
        return list(self._lines)[-count:]


_CHILDREN: dict[str, Child] = {}
_LOCK = threading.Lock()


# --- lifecycle -------------------------------------------------------------


def _free_port() -> int:
    """Bind 0, read the assignment, release.

    Racy in principle - something else may take it in the gap. The alternative
    is a hand-maintained port table, which is racy in practice AND drifts, so
    this is the better of two imperfect options. Revisit only if a collision is
    actually observed.
    """
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _environment(spec: Spec, port: int) -> dict[str, str]:
    env = {key: os.environ[key] for key in PASSTHROUGH if key in os.environ}
    for key, value in spec.env.items():
        env[key] = str(value).replace("{port}", str(port))
    if spec.needs_port:
        env[spec.port_env] = str(port)
    return env


def _drain(child: Child) -> None:
    """Pump the child's output into memory and onto disk until it ends."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = LOG_DIR / f"{child.provider_id}.log"
    stream = child.popen.stdout if child.popen else None
    if stream is None:
        return
    try:
        with open(path, "a", encoding="utf-8", errors="replace") as sink:
            for raw in stream:
                line = raw.rstrip("\r\n")
                child._lines.append(line)
                sink.write(line + "\n")
                sink.flush()
    except Exception:  # noqa: BLE001
        pass
    finally:
        # EOF means the process ended. Recording it here is what turns a silent
        # death into a reportable CRASHED, which was the whole G3 complaint.
        code = child.popen.poll() if child.popen else None
        child.exit_code = code
        if child.state not in (STOPPED,):
            child.state = CRASHED
            child.last_error = f"exited with code {code}"


def _launch(child: Child) -> None:
    spec = child.spec
    child.port = _free_port() if spec.needs_port else 0
    argv = [part.replace("{port}", str(child.port)) for part in spec.argv]
    try:
        child.popen = subprocess.Popen(  # noqa: S603 - argv, never a shell
            argv,
            cwd=str(spec.cwd) if spec.cwd else None,
            env=_environment(spec, child.port),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            shell=False,
        )
    except Exception as exc:  # noqa: BLE001
        child.state = CRASHED
        child.last_error = f"spawn failed: {exc}"
        raise ProcessError(child.last_error) from exc
    child.started_at = time.time()
    child.state = STARTING
    threading.Thread(target=_drain, args=(child,), daemon=True,
                     name=f"fabric-log-{child.provider_id}").start()


def spawn(provider_id: str, spec: Spec, *, timeout: float = 60.0) -> Child:
    """
    Start a child and block until it is ready.

    On timeout the child is stopped and `ProcessError` is raised quoting the
    log tail. That specific failure -- started, never became usable, said
    nothing -- is what this module exists to make loud.
    """
    with _LOCK:
        existing = _CHILDREN.get(provider_id)
        if existing is not None and existing.state == READY and existing.alive():
            return existing
        if existing is not None:
            stop(provider_id)
        child = Child(provider_id=provider_id, spec=spec)
        _CHILDREN[provider_id] = child

    _launch(child)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not child.alive():
            tail = " | ".join(child.log_tail(20))
            stop(provider_id)
            raise ProcessError(
                f"{provider_id} exited during startup "
                f"(code {child.exit_code}): {tail}")
        if spec.ready.check(child):
            child.state = READY
            return child
        time.sleep(0.2)

    tail = " | ".join(child.log_tail(20))
    stop(provider_id)
    raise ProcessError(f"{provider_id} did not become ready in {timeout:.0f}s: {tail}")


def stop(provider_id: str) -> None:
    """Graceful, then terminate, then kill. Verified, not hoped for."""
    with _LOCK:
        child = _CHILDREN.pop(provider_id, None)
    if child is None:
        return
    child.state = STOPPED
    proc = child.popen
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=child.spec.stop_timeout)
        return
    except Exception:  # noqa: BLE001 - includes TimeoutExpired
        pass
    # A child that ignores terminate is not a reason to give up and leave a
    # port held; the old `deactivate()` swallowed exactly this and called it a
    # process-table problem.
    proc.kill()
    try:
        proc.wait(timeout=5)
    except Exception:  # noqa: BLE001
        logger.error("fabric child %s survived kill (pid %s)",
                     provider_id, proc.pid)


def restart(provider_id: str, *, timeout: float = 60.0) -> Child:
    """Relaunch a crashed child, up to `max_restarts` inside the window.

    Bounded on purpose: a crash loop that retries forever is how one bad clone
    eats a laptop.
    """
    child = _CHILDREN.get(provider_id)
    if child is None:
        raise ProcessError(f"{provider_id} was never started")
    now = time.monotonic()
    window = child.spec.restart_window
    child._restart_times = [t for t in child._restart_times if now - t < window]
    if len(child._restart_times) >= child.spec.max_restarts:
        child.state = CRASHED
        child.last_error = (
            f"{child.spec.max_restarts} restarts in {window:.0f}s; giving up")
        raise ProcessError(child.last_error)
    child._restart_times.append(now)
    child.restarts += 1
    delay = min(30.0, 2.0 ** len(child._restart_times))
    time.sleep(delay)
    spec = child.spec
    stop(provider_id)
    fresh = spawn(provider_id, spec, timeout=timeout)
    fresh.restarts = child.restarts
    fresh._restart_times = child._restart_times
    return fresh


def child(provider_id: str) -> Child | None:
    return _CHILDREN.get(provider_id)


def logs(provider_id: str, tail: int = 200) -> list[str]:
    known = _CHILDREN.get(provider_id)
    return known.log_tail(tail) if known else []


def running() -> dict[str, dict]:
    """What this module currently owns, for `fabric.processes()` and the UI."""
    return {
        pid: {"pid": ch.popen.pid if ch.popen else None, "state": ch.state,
              "port": ch.port, "restarts": ch.restarts,
              "uptime_s": round(time.time() - ch.started_at, 1) if ch.started_at else 0,
              "last_error": ch.last_error}
        for pid, ch in _CHILDREN.items()
    }


def stop_all() -> None:
    for provider_id in list(_CHILDREN):
        stop(provider_id)


def reap_orphans(markers: dict[str, str]) -> list[int]:
    """
    Kill marker-matching processes this module did not spawn.

    A hard kill of Friday leaves a sidecar holding its port, and the next start
    then fails for a reason that looks nothing like the cause. Called at fabric
    startup. `markers` is provider_id -> command-line marker.
    """
    try:
        import psutil
    except ModuleNotFoundError:
        return []
    ours = {ch.popen.pid for ch in _CHILDREN.values() if ch.popen}
    killed = []
    for proc in psutil.process_iter(["pid", "cmdline"]):
        try:
            pid = proc.info["pid"]
            if pid in ours or pid == os.getpid():
                continue
            line = " ".join(proc.info.get("cmdline") or [])
        except Exception:  # noqa: BLE001
            continue
        if not line:
            continue
        for marker in markers.values():
            if marker and marker in line:
                try:
                    proc.kill()
                    killed.append(pid)
                except Exception:  # noqa: BLE001
                    pass
                break
    return killed
