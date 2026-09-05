"""
Where Friday runs code it did not write, and what that actually guarantees.

    A git worktree is version control, not containment.

It stops a coding agent corrupting the branch. It does nothing about the
agent reading `.env` two directories up, forking until the machine swaps,
opening a socket to somewhere it should not, or leaving a process alive after
the run is "finished". Four failures, and the worktree addresses none.

## What this is, and what it is not

The design is taken from OpenSandbox's separation of concerns - environment
lifecycle, command execution, file movement, resource limits, egress policy,
credential injection - and implemented against what this machine actually
has, which is Windows with no container runtime. Nothing here downloads
anything or needs an administrator.

    OpenSandbox concept        how it is done here
    ------------------------   ---------------------------------------------
    environment lifecycle      `NativeExecutionEnvironment`, over a worktree
    resource limits            a Windows Job Object
    guaranteed cleanup         JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    filesystem boundary        the workspace, resolved before use
    egress policy              `Egress`, DENY_ALL by default
    credential vault           a scoped environment, never on disk
    artifact export            an explicit copy-out of named paths

The Job Object is what makes this real rather than advisory. It is a kernel
object: every process the agent spawns is assigned to it, the limits are
enforced by the kernel rather than by asking politely, and closing the handle
kills the whole tree. `taskkill /T` walks a parent-child list that a process
escapes by orphaning itself; a job cannot be escaped that way, because
membership is inherited and cannot be renounced.

**It is not a security sandbox, and the name no longer says it is.** Microsoft
documents job objects as a process-management and resource-control mechanism -
not a filesystem, credential or network boundary. Calling this class
`Sandbox` implied an isolation guarantee it has never had. What it honestly
provides is:

    an agent that misbehaves cannot quietly take the machine or the
    repository with it

`strength()` reports `JOB_OBJECT` or `PROCESS_ONLY` in those terms. A real
adversary with code execution on this host has better options than any of
this, and stronger tiers exist - Windows AppContainer, then a container, then
a remote machine - behind the same `ExecutionBackend` contract, for when one
of them is worth its cost.
"""

from __future__ import annotations

import ctypes
import logging
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Protocol
from pathlib import Path, PurePosixPath, PureWindowsPath

logger = logging.getLogger("friday-agent")

WINDOWS = sys.platform == "win32"

# Never crosses the boundary, even when a caller asks for the whole
# environment. A denylist of the secrets somebody remembered - which is why
# `environment()` works from `KEPT` and this only backs it up.
SCRUBBED = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    "DEEPGRAM_API_KEY",
    "CARTESIA_API_KEY",
    "ELEVENLABS_API_KEY",
    "LIVEKIT_API_KEY",
    "LIVEKIT_API_SECRET",
    "SARVAM_API_KEY",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_ACCESS_KEY_ID",
    "AWS_SESSION_TOKEN",
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "HF_TOKEN",
    "NGROK_AUTHTOKEN",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "FRIDAY_COMPANION_TOKEN",
    "FRIDAY_PAIRING_TOKEN",
)

# What a process needs to run at all on this machine. Everything else stays
# on the host side of the boundary.
KEPT = (
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "WINDIR",
    "COMSPEC",
    "TEMP",
    "TMP",
    "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE",
    "USERPROFILE",
    "HOME",
    "LANG",
    "LC_ALL",
    "TZ",
    "APPDATA",
    "LOCALAPPDATA",
    "PROGRAMFILES",
    "PROGRAMFILES(X86)",
    "PROGRAMDATA",
)

DENY_ALL = "DENY_ALL"
ALLOWLIST = "ALLOWLIST"
ALLOW_ALL = "ALLOW_ALL"


@dataclass(frozen=True)
class Limits:
    """
    What one sandboxed run may consume.

    Defaults are sized for a coding agent on a developer machine, not for a
    build farm. They are deliberately low enough that a runaway is stopped
    while the machine is still usable - the whole point is that Friday's
    voice loop keeps answering while a development run misbehaves.
    """

    #: Kernel-enforced ceiling for the whole tree, not per process.
    memory_mb: int = 4096
    #: Active processes in the job at once; a fork bomb hits this first.
    processes: int = 64
    #: Wall clock for one command. The executor has its own for the run; this
    #: one is what stops `npm test` hanging on a prompt for an hour.
    seconds: float = 1800.0
    #: The largest single artifact `export` will copy out.
    artifact_bytes: int = 64 * 1024 * 1024


@dataclass(frozen=True)
class Egress:
    """The outbound policy for one sandbox."""

    mode: str = DENY_ALL
    hosts: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.mode not in (DENY_ALL, ALLOWLIST, ALLOW_ALL):
            raise ValueError(f"unknown egress mode {self.mode!r}")
        if self.mode == ALLOWLIST and not self.hosts:
            raise ValueError("ALLOWLIST with no hosts denies everything; say DENY_ALL if that is what you mean")

    def allows(self, host: str) -> bool:
        """Whether this policy permits a connection to `host`."""
        if self.mode == ALLOW_ALL:
            return True
        if self.mode == DENY_ALL:
            return False
        host = (host or "").strip().lower().rstrip(".")
        for allowed in self.hosts:
            allowed = allowed.strip().lower().rstrip(".")
            # ".example.com" means the domain and everything under it.
            if allowed.startswith("."):
                if host == allowed[1:] or host.endswith(allowed):
                    return True
            elif host == allowed:
                return True
        return False

    def describe(self) -> str:
        if self.mode == ALLOWLIST:
            return f"{self.mode}({', '.join(self.hosts)})"
        return self.mode


# ---------------------------------------------------------------------------
# The Windows Job Object, through ctypes. Constants from winnt.h.
# ---------------------------------------------------------------------------

JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200
JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_LIMIT_BREAKAWAY_OK = 0x00000800
_EXTENDED_LIMIT_INFORMATION = 9        # JobObjectExtendedLimitInformation


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong)]


class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", ctypes.c_uint32),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", ctypes.c_uint32),
                ("Affinity", ctypes.POINTER(ctypes.c_ulong)),
                ("PriorityClass", ctypes.c_uint32),
                ("SchedulingClass", ctypes.c_uint32)]


class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", _IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t)]


class JobObject:
    """
    A kernel-enforced container for a process tree.

    Membership is inherited and cannot be renounced, which is the property
    that matters: a child that orphans itself to escape `taskkill /T` is
    still in the job, still counted against the limits, and still killed when
    the handle closes.

    Unavailable off Windows, and `available` says so rather than pretending.
    Every method is a no-op in that case so callers do not need to branch -
    but `Sandbox.strength()` reports the weaker guarantee honestly.
    """

    def __init__(self, name: str, limits: Limits) -> None:
        self.name = name
        self.limits = limits
        self.handle = None
        self.available = False
        if not WINDOWS:
            return
        try:
            self._create()
            self.available = True
        except OSError:
            # A machine that refuses job objects still runs the command; it
            # just says PROCESS_ONLY about it.
            logger.exception("could not create a job object for %s", name)

    def _create(self) -> None:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = ctypes.c_void_p
        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())

        info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = (
            JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            | JOB_OBJECT_LIMIT_ACTIVE_PROCESS
            | JOB_OBJECT_LIMIT_JOB_MEMORY)
        info.BasicLimitInformation.ActiveProcessLimit = self.limits.processes
        info.JobMemoryLimit = self.limits.memory_mb * 1024 * 1024

        ok = kernel32.SetInformationJobObject(
            ctypes.c_void_p(handle), _EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(info), ctypes.sizeof(info))
        if not ok:
            error = ctypes.get_last_error()
            kernel32.CloseHandle(ctypes.c_void_p(handle))
            raise ctypes.WinError(error)
        self.handle = handle

    def adopt(self, pid: int) -> bool:
        """Put a process, and everything it goes on to spawn, inside."""
        if not self.available or self.handle is None:
            return False
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        # PROCESS_SET_QUOTA | PROCESS_TERMINATE is what assignment needs.
        kernel32.OpenProcess.restype = ctypes.c_void_p
        process = kernel32.OpenProcess(0x0100 | 0x0001, False, pid)
        if not process:
            logger.warning("could not open pid %s to sandbox it", pid)
            return False
        try:
            ok = kernel32.AssignProcessToJobObject(
                ctypes.c_void_p(self.handle), ctypes.c_void_p(process))
            if not ok:
                logger.warning("could not assign pid %s to the job: %s",
                               pid, ctypes.WinError(ctypes.get_last_error()))
            return bool(ok)
        finally:
            kernel32.CloseHandle(ctypes.c_void_p(process))

    def close(self) -> None:
        """Kill everything in the job. Closing the handle is what does it."""
        if self.handle is None:
            return
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CloseHandle(ctypes.c_void_p(self.handle))
        self.handle = None


@dataclass
class Execution:
    """What one command did. Never carries the environment it ran with."""

    command: tuple[str, ...]
    exit_code: int
    stdout: str
    stderr: str
    seconds: float
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


class ExecutionError(RuntimeError):
    """A containment guarantee could not be met, so nothing was run."""


def _looks_absolute(path: str | Path) -> bool:
    """Absolute under Windows OR POSIX rules, whatever the host.

    `Path("C:/x").is_absolute()` is False on Linux and `Path("/etc").is_absolute()`
    is False on Windows for the `\\` flavour of the same question; the sandbox
    must refuse both everywhere, so both flavours are asked.
    """
    text = str(path)
    return (PureWindowsPath(text).is_absolute() or PurePosixPath(text).is_absolute()
            or text.startswith(("\\\\", "//")) or bool(PureWindowsPath(text).drive))


class NativeExecutionEnvironment:
    """
    One controlled place for one development run.

    Use it as a context manager. Leaving the block is the cleanup, and it is
    not best-effort: the job handle closes and the kernel kills whatever is
    still running, including anything that tried to outlive the run.

        with NativeExecutionEnvironment(workspace, name="DEV-7") as box:
            result = box.run(["npm", "test"])
            box.export("dist/app.js", into=artifacts)

    `workspace` must already exist - normally the git worktree the executor
    was given. The sandbox does not create or destroy it; the worktree
    manager owns that, and two owners for one directory is how directories
    get deleted twice.
    """

    def __init__(self, workspace: str | Path, *, name: str = "",
                 limits: Limits | None = None, egress: Egress | None = None,
                 credentials: dict[str, str] | None = None) -> None:
        self.workspace = Path(workspace).resolve()
        if not self.workspace.is_dir():
            raise ExecutionError(f"no such workspace: {self.workspace}")
        self.name = name or self.workspace.name
        self.limits = limits or Limits()
        self.egress = egress or Egress()
        # Copied, so the caller's dict is not what a later `terminate`
        # clears; and cleared on terminate, so a secret lives as long as the run.
        self._credentials = dict(credentials or {})
        self.job = JobObject(self.name, self.limits)
        self.started_at = 0.0
        self.executions = []
        self._closed = False

    # -- lifecycle ---------------------------------------------------------

    def __enter__(self) -> "NativeExecutionEnvironment":
        self.started_at = time.monotonic()
        logger.info("execution.open name=%s workspace=%s strength=%s egress=%s",
                    self.name, self.workspace, self.strength(),
                    self.egress.describe())
        return self

    def __exit__(self, *exc) -> None:
        self.terminate()

    def terminate(self) -> None:
        """Close the job. Idempotent, because cleanup runs on every path."""
        if self._closed:
            return
        self._closed = True
        self._credentials.clear()
        self.job.close()
        logger.info("execution.closed name=%s ran=%d for=%.1fs",
                    self.name, len(self.executions),
                    time.monotonic() - self.started_at)

    def strength(self) -> str:
        """
        What containment this sandbox actually provides, in plain terms.

        Said out loud rather than assumed, because the difference between
        "the kernel enforces this" and "we asked nicely" is the difference
        between a boundary and a comment.
        """
        if self.job.available:
            return "JOB_OBJECT"
        return "PROCESS_ONLY"

    # -- environment -------------------------------------------------------

    def environment(self) -> dict[str, str]:
        """
        The environment a sandboxed process gets.

        An allowlist, not a denylist. A denylist is a list of the secrets
        somebody remembered, and this repository adds providers faster than
        anyone updates such a list - so the default is that a variable does
        not cross the boundary, and `KEPT` names the few that must.
        """
        env = {name: os.environ[name] for name in KEPT if name in os.environ}
        # Belt and braces: nothing in SCRUBBED gets through even if it were
        # ever added to KEPT by mistake.
        for name in SCRUBBED:
            env.pop(name, None)
        env["FRIDAY_EXECUTION_ENV"] = self.name
        env["FRIDAY_EXECUTION_EGRESS"] = self.egress.mode
        if self.egress.mode == DENY_ALL:
            # No firewall rule is written here (that needs an administrator);
            # the proxy variables point every well-behaved HTTP client at a
            # port nothing listens on, and the executor's own policy refuses
            # network tools. Not a wall, but a locked door with a sign on it.
            env["NO_PROXY"] = "*"
            env["HTTP_PROXY"] = env["HTTPS_PROXY"] = "http://127.0.0.1:9"
        env.update(self._credentials)
        return env

    def redacted(self, text: str) -> str:
        """Any injected credential removed from text before it is stored."""
        for value in self._credentials.values():
            if value and len(value) >= 8:
                text = text.replace(value, "[redacted]")
        return text

    # -- processes ---------------------------------------------------------

    def run(self, command: list[str] | tuple[str, ...], *,
            timeout: float | None = None, stdin: str | None = None) -> Execution:
        """
        Run one command inside the boundary and wait for it.

        The process starts suspended-in-effect: it is created, adopted into
        the job, and only then allowed to matter. On Windows a process cannot
        be created directly into a job without CREATE_SUSPENDED and a resume,
        so there is a genuine window here of a few milliseconds. It is
        documented rather than hidden: a process that spawns children inside
        that window could leave them outside the job. Nothing Friday runs
        does that, and closing it properly needs CreateProcess with
        PROC_THREAD_ATTRIBUTE_JOB_LIST, which is the upgrade path if it ever
        matters.
        """
        if self._closed:
            raise ExecutionError("this environment is closed")
        command = tuple(str(part) for part in command)
        limit = timeout if timeout is not None else self.limits.seconds
        started = time.monotonic()

        try:
            process = subprocess.Popen(
                command, cwd=str(self.workspace), env=self.environment(),
                stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW if WINDOWS else 0)
        except (OSError, ValueError) as exc:
            return self._record(Execution(command, 127, "",
                                          f"could not start: {exc}",
                                          time.monotonic() - started))

        self.job.adopt(process.pid)
        timed_out = False
        try:
            out, err = process.communicate(input=stdin, timeout=limit)
        except subprocess.TimeoutExpired:
            timed_out = True
            process.kill()
            out, err = process.communicate()
            err = f"{err}\nkilled after {limit:.0f}s"

        return self._record(Execution(
            command, process.returncode if not timed_out else 124,
            self.redacted(out or ""), self.redacted(err or ""),
            time.monotonic() - started, timed_out))

    def _record(self, execution: Execution) -> Execution:
        self.executions.append(execution)
        logger.info("execution.ran name=%s cmd=%s exit=%s in=%.1fs",
                    self.name, execution.command[0], execution.exit_code,
                    execution.seconds)
        return execution

    # -- files -------------------------------------------------------------

    def resolve(self, relative: str | Path) -> Path:
        """
        A path inside the workspace, or an error.

        Resolved before checking, so a symlink inside the workspace pointing
        outside it is caught. `FileJail` makes the same argument at greater
        length; this is the same rule applied to one narrower root.

        Absolute under either path flavour is refused outright: a sandbox
        path is relative by definition, and `C:/Windows/...` handed to a
        Linux host is not a relative path that happens to contain a colon
        - `workspace / "C:/Windows/..."` quietly nests it inside the
        workspace there, which is how the ubuntu job passed the very input
        the Windows job refused (2026-09-05).
        """
        if _looks_absolute(relative):
            raise ExecutionError(f"{relative!r} is an absolute path; the sandbox takes paths relative to its workspace")
        target = (self.workspace / Path(relative)).resolve()
        try:
            target.relative_to(self.workspace)
        except ValueError:
            raise ExecutionError(
                f"{relative!r} resolves to {target}, outside the sandbox")
        return target

    def read(self, relative: str | Path, *, limit: int = 1_000_000) -> str:
        return self.resolve(relative).read_text(encoding="utf-8",
                                                errors="replace")[:limit]

    def write(self, relative: str | Path, content: str) -> Path:
        target = self.resolve(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return target

    def listing(self, relative: str | Path = ".") -> list[str]:
        root = self.resolve(relative)
        return sorted(str(p.relative_to(self.workspace))
                      for p in root.rglob("*") if p.is_file())

    def export(self, relative: str | Path, *, into: str | Path) -> Path:
        """
        Copy one artifact out of the sandbox.

        Named paths only, one at a time. The alternative - handing the host a
        directory the agent controlled - is how a build step gets to write
        anywhere the host can write.
        """
        source = self.resolve(relative)
        if not source.is_file():
            raise ExecutionError(f"no artifact at {relative!r}")
        size = source.stat().st_size
        if size > self.limits.artifact_bytes:
            raise ExecutionError(
                f"{relative!r} is {size} bytes, over the "
                f"{self.limits.artifact_bytes} artifact limit")
        destination = Path(into).resolve()
        destination.mkdir(parents=True, exist_ok=True)
        out = destination / source.name
        shutil.copy2(source, out)
        logger.info("execution.exported name=%s file=%s bytes=%d",
                    self.name, source.name, size)
        return out

    # -- evidence ----------------------------------------------------------

    def report(self) -> dict:
        """What happened, in a shape that can be stored beside the run."""
        return {
            "environment": self.name,
            "workspace": str(self.workspace),
            "strength": self.strength(),
            "egress": self.egress.describe(),
            "limits": {"memory_mb": self.limits.memory_mb,
                       "processes": self.limits.processes,
                       "seconds": self.limits.seconds},
            "commands": [{"command": " ".join(e.command), "exit_code": e.exit_code,
                          "seconds": round(e.seconds, 2), "timed_out": e.timed_out}
                         for e in self.executions],
            "credentials_injected": sorted(self._credentials),
        }


def for_development(workspace: str | Path, *, name: str = "", network: bool = False,
                    hosts: tuple[str, ...] = ()) -> NativeExecutionEnvironment:
    """
    The sandbox a coding run gets, with the defaults that suit one.

    Network off unless asked for. A development task that needs to reach the
    registry says so and names the hosts; a task that did not say so and
    tried anyway fails loudly, which is the outcome that leaves evidence.
    """
    if network and hosts:
        egress = Egress(mode=ALLOWLIST, hosts=hosts)
    elif network:
        egress = Egress(mode=ALLOW_ALL)
    else:
        egress = Egress(mode=DENY_ALL)
    return NativeExecutionEnvironment(workspace, name=name,
                                      egress=egress)


class ExecutionBackend(Protocol):
    """
    Where a development run's commands actually happen.

    Deliberately narrow. A backend owns processes, files inside the
    workspace, resource lifetime and the environment - and nothing else.
    Intent, planning, projects, memory, policy, verification and promotion
    stay in Friday, because a backend that starts making decisions is a
    second brain that will disagree with the first one.

    The contract exists so that today's answer and tomorrow's are the same
    shape:

        NativeWindowsBackend    a Job Object on this machine, now
        AppContainerBackend     stronger Windows isolation, if it earns it
        ContainerBackend        when a container runtime is installed
        RemoteBackend           a bigger machine, or a GPU box

    `DevelopmentRun` must never learn which one it has. If a planner has to
    branch on the backend, the contract is wrong rather than the planner.
    """

    name: str

    def create(self, workspace: str | Path, *, run_id: str = "",
               limits: "Limits | None" = None, egress: "Egress | None" = None,
               credentials: dict[str, str] | None = None):
        ...

    def exec(self, environment, command, *, timeout: float | None = None,
             stdin: str | None = None) -> "Execution":
        ...

    def status(self, environment) -> dict: ...

    def put_file(self, environment, relative: str | Path, content: str) -> Path:
        ...

    def get_file(self, environment, relative: str | Path) -> str: ...

    def list_files(self, environment, relative: str | Path = ".") -> list[str]: ...

    def collect_artifacts(self, environment, names, *, into: str | Path) -> list[Path]:
        ...

    def terminate(self, environment) -> None: ...


class NativeWindowsBackend:
    """
    Controlled native execution on this machine. No container, no download.

    The honest first tier. Processes are real Windows processes owned by a
    Job Object, files are the workspace, and the environment is an allowlist.
    It is trusted-executor containment, not adversarial isolation, and
    `strength()` says which.
    """

    name = "native_windows"

    def create(self, workspace, *, run_id="", limits=None,
               egress=None, credentials=None) -> NativeExecutionEnvironment:
        environment = NativeExecutionEnvironment(
            workspace, name=run_id or Path(workspace).name, limits=limits,
            egress=egress, credentials=credentials)
        environment.__enter__()
        return environment

    def exec(self, environment, command, *, timeout=None, stdin=None):
        return environment.run(command, timeout=timeout, stdin=stdin)

    def status(self, environment) -> dict:
        return environment.report()

    def put_file(self, environment, relative, content):
        return environment.write(relative, content)

    def get_file(self, environment, relative) -> str:
        return environment.read(relative)

    def list_files(self, environment, relative=".") -> list[str]:
        return environment.listing(relative)

    def collect_artifacts(self, environment, names, *, into) -> list[Path]:
        """Named paths only. A directory the agent controlled is not a list."""
        collected = []
        for name in names:
            try:
                collected.append(environment.export(name, into=into))
            except ExecutionError:
                logger.info("execution.artifact_missing name=%s", name)
        return collected

    def terminate(self, environment) -> None:
        environment.terminate()


DEFAULT_BACKEND = NativeWindowsBackend()


def backend_named(name: str = "") -> ExecutionBackend:
    """
    Look a backend up, or get the default.

    A name that is not available raises rather than quietly falling back. A
    caller that asked for container isolation and silently got a Job Object
    would report a guarantee it does not have, which is the failure this
    whole module is careful about.
    """
    if not name or name == DEFAULT_BACKEND.name:
        return DEFAULT_BACKEND
    raise LookupError(
        f"no execution backend called {name!r} is available here; only "
        f"{DEFAULT_BACKEND.name!r} is installed")
