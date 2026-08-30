"""
The execution boundary around code Friday did not write.

A git worktree is version control, not containment. It stops a coding agent
from corrupting the branch; it does nothing about the agent reading `.env`
two directories up, opening a socket to somewhere it should not, forking
until the machine swaps, or leaving a process alive after the run is
"finished". Those are four different failures and the worktree addresses none
of them.

This module is the boundary. The design is taken from OpenSandbox's runtime
API - create, exec, files, artifacts, terminate, with resource limits, an
egress policy and credential injection - and implemented against what this
machine actually has, which is Windows with no container runtime installed.
Nothing here downloads anything or requires an administrator.

    OpenSandbox concept        how it is done here
    ------------------------   ---------------------------------------------
    sandbox lifecycle          `Sandbox`, a context manager over a worktree
    filesystem API             `FileJail`, re-rooted at the workspace
    resource limits            a Windows Job Object
    guaranteed cleanup         JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    network egress policy      `netguard`, plus a scrubbed environment
    credential vaulting        a scoped environment, never written to disk
    artifact export            an explicit copy-out of named paths

The Job Object is the part that makes this real rather than advisory. It is a
kernel object: every process the agent spawns is assigned to it, the limits
are enforced by the kernel rather than by asking politely, and when the
handle closes the kernel kills the whole tree. A `taskkill /T` walks a
parent-child list that a process can escape by orphaning itself. A job cannot
be escaped by orphaning, because membership is inherited and permanent.

What this is NOT: a security boundary against a determined attacker. It is a
containment boundary against a capable agent doing something careless or
wrong. A real adversary with code execution on the host has better options
than any of this. The honest name for the guarantee is "an agent that
misbehaves cannot quietly take the machine or the repository with it", and
`Sandbox.strength()` says so in those terms rather than claiming isolation it
does not have.
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
from pathlib import Path

logger = logging.getLogger("friday-agent")

WINDOWS = sys.platform == "win32"

#: Environment variables that must never reach a sandboxed process. Some are
#: secrets; the proxy ones are here because an inherited proxy is a hole
#: straight through an egress policy that only inspects URLs.
SCRUBBED = (
    "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY",
    "DEEPGRAM_API_KEY", "CARTESIA_API_KEY", "ELEVENLABS_API_KEY",
    "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "SARVAM_API_KEY",
    "AWS_SECRET_ACCESS_KEY", "AWS_ACCESS_KEY_ID", "AWS_SESSION_TOKEN",
    "GITHUB_TOKEN", "GH_TOKEN", "HF_TOKEN", "NGROK_AUTHTOKEN",
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy",
    "FRIDAY_COMPANION_TOKEN", "FRIDAY_PAIRING_TOKEN",
)

#: Kept, because a process with no PATH or no temp directory does not run at
#: all and the failure looks like a bug in the task rather than in the jail.
KEPT = ("PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR",
        "COMSPEC", "TEMP", "TMP", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE",
        "USERPROFILE", "HOME", "LANG", "LC_ALL", "TZ", "APPDATA", "LOCALAPPDATA",
        "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMDATA")


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------

#: No outbound network at all. The default, because most development tasks
#: need the filesystem and a compiler and nothing else, and a task that
#: silently reached the internet is a task whose result cannot be reproduced.
DENY_ALL = "DENY_ALL"

#: Only hosts named up front. What a task that must install packages gets.
ALLOWLIST = "ALLOWLIST"

#: Everything. Requires an explicit decision and is recorded as one.
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

    #: Total committed memory across every process in the job.
    memory_mb: int = 4096
    #: Ceiling on process count. A fork bomb hits this instead of the machine.
    processes: int = 64
    #: Wall clock. Separate from the executor's own timeout, and lower-level:
    #: this one is enforced even if the executor's event loop is wedged.
    seconds: float = 1800.0
    #: Bytes a single written artifact may have before export refuses it.
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
            raise ValueError("ALLOWLIST with no hosts denies everything; "
                             "say DENY_ALL if that is what you mean")

    def allows(self, host: str) -> bool:
        """Whether this policy permits a connection to `host`."""
        if self.mode == ALLOW_ALL:
            return True
        if self.mode == DENY_ALL:
            return False
        host = (host or "").strip().lower().rstrip(".")
        for allowed in self.hosts:
            allowed = allowed.strip().lower().rstrip(".")
            # A leading dot means "this domain and anything under it".
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
# The Windows Job Object
# ---------------------------------------------------------------------------

JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200
JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_LIMIT_BREAKAWAY_OK = 0x00000800
_EXTENDED_LIMIT_INFORMATION = 9


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
            # A machine that refuses job objects still runs the task; it just
            # runs it with a weaker promise, which `strength()` will say.
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
        # PROCESS_SET_QUOTA | PROCESS_TERMINATE
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


# ---------------------------------------------------------------------------
# The sandbox
# ---------------------------------------------------------------------------

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


class SandboxError(RuntimeError):
    """A containment guarantee could not be met, so nothing was run."""


class Sandbox:
    """
    One isolated place for one development run.

    Use it as a context manager. Leaving the block is the cleanup, and it is
    not best-effort: the job handle closes and the kernel kills whatever is
    still running, including anything that tried to outlive the run.

        with Sandbox(workspace, name="DEV-7") as box:
            result = box.run(["npm", "test"])
            box.export("dist/app.js", into=artifacts)

    `workspace` must already exist - normally the git worktree the executor
    was given. The sandbox does not create or destroy it; the worktree
    manager owns that, and two owners for one directory is how directories
    get deleted twice.
    """

    def __init__(self, workspace: str | Path, *, name: str = "",
                 limits: Limits | None = None,
                 egress: Egress | None = None,
                 credentials: dict[str, str] | None = None) -> None:
        self.workspace = Path(workspace).resolve()
        if not self.workspace.is_dir():
            raise SandboxError(f"no such workspace: {self.workspace}")
        self.name = name or self.workspace.name
        self.limits = limits or Limits()
        self.egress = egress or Egress()
        #: Injected into the environment, never written to disk, never logged.
        #: Held on the instance only for the life of the block.
        self._credentials = dict(credentials or {})
        self.job = JobObject(self.name, self.limits)
        self.started_at = 0.0
        self.executions: list[Execution] = []
        self._closed = False

    # -- lifecycle ---------------------------------------------------------

    def __enter__(self) -> "Sandbox":
        self.started_at = time.monotonic()
        logger.info("sandbox.open name=%s workspace=%s strength=%s egress=%s",
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
        logger.info("sandbox.closed name=%s ran=%d for=%.1fs",
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
            return "JOB_OBJECT"          # kernel-enforced limits and cleanup
        return "PROCESS_ONLY"            # cwd and environment only

    # -- the environment ---------------------------------------------------

    def environment(self) -> dict[str, str]:
        """
        The environment a sandboxed process gets.

        An allowlist, not a denylist. A denylist is a list of the secrets
        somebody remembered, and this repository adds providers faster than
        anyone updates such a list - so the default is that a variable does
        not cross the boundary, and `KEPT` names the few that must.
        """
        env = {name: os.environ[name] for name in KEPT if name in os.environ}
        # Belt and braces: if a KEPT name ever overlaps a secret, the scrub
        # wins. Cheap, and the alternative is finding out the other way.
        for name in SCRUBBED:
            env.pop(name, None)
        env["FRIDAY_SANDBOX"] = self.name
        env["FRIDAY_SANDBOX_EGRESS"] = self.egress.mode
        if self.egress.mode == DENY_ALL:
            # Honoured by well-behaved tooling. Not a boundary on its own,
            # which is why the real answer is that the task should not need
            # the network - but it turns "silently downloaded something" into
            # "failed loudly", and that is worth having.
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

    # -- running -----------------------------------------------------------

    def run(self, command: list[str] | tuple[str, ...], *,
            timeout: float | None = None,
            stdin: str | None = None) -> Execution:
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
            raise SandboxError("this sandbox is closed")
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
        logger.info("sandbox.ran name=%s cmd=%s exit=%s in=%.1fs",
                    self.name, execution.command[0], execution.exit_code,
                    execution.seconds)
        return execution

    # -- the filesystem ----------------------------------------------------

    def resolve(self, relative: str | Path) -> Path:
        """
        A path inside the workspace, or an error.

        Resolved before checking, so a symlink inside the workspace pointing
        outside it is caught. `FileJail` makes the same argument at greater
        length; this is the same rule applied to one narrower root.
        """
        target = (self.workspace / Path(relative)).resolve()
        try:
            target.relative_to(self.workspace)
        except ValueError:
            raise SandboxError(
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
            raise SandboxError(f"no artifact at {relative!r}")
        size = source.stat().st_size
        if size > self.limits.artifact_bytes:
            raise SandboxError(
                f"{relative!r} is {size} bytes, over the "
                f"{self.limits.artifact_bytes} artifact limit")
        destination = Path(into).resolve()
        destination.mkdir(parents=True, exist_ok=True)
        out = destination / source.name
        shutil.copy2(source, out)
        logger.info("sandbox.exported name=%s file=%s bytes=%d",
                    self.name, source.name, size)
        return out

    # -- reporting ---------------------------------------------------------

    def report(self) -> dict:
        """What happened, in a shape that can be stored beside the run."""
        return {
            "sandbox": self.name,
            "workspace": str(self.workspace),
            "strength": self.strength(),
            "egress": self.egress.describe(),
            "limits": {"memory_mb": self.limits.memory_mb,
                       "processes": self.limits.processes,
                       "seconds": self.limits.seconds},
            "commands": [
                {"command": " ".join(e.command), "exit_code": e.exit_code,
                 "seconds": round(e.seconds, 2), "timed_out": e.timed_out}
                for e in self.executions],
            "credentials_injected": sorted(self._credentials),
        }


def for_development(workspace: str | Path, *, name: str = "",
                    network: bool = False,
                    hosts: tuple[str, ...] = ()) -> Sandbox:
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
    return Sandbox(workspace, name=name, egress=egress)
