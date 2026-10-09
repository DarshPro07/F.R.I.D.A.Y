"""
Scoped command runner: a command and its interpreters run inside an OS-enforced
sandbox that is bounded by one `Envelope` (EXEC-SBX-001, slice 1).

    with ScopedRunner(envelope) as runner:
        done = runner.run(["python", "-c", "..."], timeout=30)

The boundary is a fresh Windows AppContainer (`friday.appcontainer`), the same
one `friday.quarantine` uses, so the kernel refuses every path the SID was not
granted, for every program and every child it starts, whatever the program
is. The envelope's read roots get read-and-execute, its write roots modify,
and nothing else gets anything. There is no network (`DENY_ALL`) and the Job
Object from `friday.execution` still bounds memory, process count and time.

What this module does not do: decide which programs may run (the caller does;
the runner passes the vector as given), and fall back. With no AppContainer on
the host, or a root that would hand over more than a project directory, it
raises `ScopedRunnerError` before it changes anything.

Known gap: a run that is killed outright leaves its grants behind. The ACE
names a profile nobody re-creates, so it is inert but not removed; a sweep of
leftover grants is a later slice.
"""

from __future__ import annotations

import hashlib
import logging
import os
import subprocess
import tempfile
from pathlib import Path

from . import appcontainer, execution, fsjail, quarantine
from .contracts import Envelope, Limits

logger = logging.getLogger(__name__)

READ_RIGHTS = "RX"
WRITE_RIGHTS = "M"


class ScopedRunnerError(RuntimeError):
    """The envelope was refused, or the sandbox could not be built or removed."""


def _protected_directories() -> list[Path]:
    """Directories no root may be or lie under."""
    names = ("WINDIR", "ProgramFiles", "ProgramFiles(x86)")
    return [Path(os.environ[name]).resolve() for name in names if os.environ.get(name)]


def _checked_root(raw: str, kind: str) -> Path:
    """The real directory behind one envelope root, or a refusal saying why.
    Granting is recursive and inheriting, so a root is a promise about
    everything beneath it."""
    given = Path(raw)
    if not raw or not given.is_absolute():
        raise ScopedRunnerError(f"{kind} root {raw!r} is not an absolute path")
    if not given.is_dir():
        raise ScopedRunnerError(f"{kind} root {raw!r} is not an existing directory")
    if fsjail.is_reparse_point(given):
        raise ScopedRunnerError(f"{kind} root {raw!r} is a link or junction")
    root = given.resolve()
    if root == Path(root.anchor):
        raise ScopedRunnerError(f"{kind} root {raw!r} is a drive root")
    # The home directory itself, or anything that contains it, would hand over
    # the user's files, not a project.
    if Path.home().resolve().is_relative_to(root):
        raise ScopedRunnerError(f"{kind} root {raw!r} is the home directory or contains it")
    for protected in _protected_directories():
        if root.is_relative_to(protected):
            raise ScopedRunnerError(f"{kind} root {raw!r} lies under {protected}")
    return root


class ScopedRunner:
    """One envelope, one AppContainer. Use as a context manager: leaving the
    block kills what is running, takes every grant off again and deletes the
    container and its scratch profile."""

    def __init__(self, envelope: Envelope, *, runtime_dir: str | Path | None = None,
                 limits: Limits | None = None) -> None:
        self.envelope = envelope
        self.runtime_dir = Path(runtime_dir) if runtime_dir is not None \
            else quarantine.default_runtime_dir()
        self.limits = limits or envelope.limits
        #: The container's SID while open, so a caller can audit the ACLs.
        self.sid = ""
        self._python = ""
        self._container: appcontainer.AppContainer | None = None
        self._env: execution.NativeExecutionEnvironment | None = None
        self._granted: list[Path] = []
        self._scratch: Path | None = None

    # -- lifecycle ---------------------------------------------------------

    def __enter__(self) -> "ScopedRunner":
        write_roots, read_roots, cwd = self._validate()
        try:
            self._open(write_roots, read_roots, cwd)
        except BaseException:
            self.__exit__()
            raise
        logger.info("scoped_runner.open run=%s read=%d write=%d",
                    self.envelope.run_id, len(read_roots), len(write_roots))
        return self

    def _validate(self) -> tuple[list[Path], list[Path], Path]:
        """Everything that can be refused is refused here, before any grant."""
        if not appcontainer.available():
            raise ScopedRunnerError(
                "no OS boundary on this host (AppContainer needs Windows 8 or later); "
                "a scoped command is not run without one")
        if not self.envelope.write_roots:
            raise ScopedRunnerError("the envelope has no write root")
        write_roots = [_checked_root(raw, "write") for raw in self.envelope.write_roots]
        read_roots = [_checked_root(raw, "read") for raw in self.envelope.read_roots]
        if self.envelope.cwd:
            wanted = Path(self.envelope.cwd).resolve()
            inside = any(wanted.is_relative_to(root) for root in write_roots + read_roots)
        else:
            inside = False
        cwd = wanted if inside else write_roots[0]
        if inside and not cwd.is_dir():
            cwd = write_roots[0]
        return write_roots, read_roots, cwd

    def _open(self, write_roots: list[Path], read_roots: list[Path], cwd: Path) -> None:
        try:
            self._python = str(appcontainer.provision_runtime(self.runtime_dir))
            name = "friday-scope-" + hashlib.sha1(self.envelope.run_id.encode()).hexdigest()[:12]
            self._container = appcontainer.AppContainer(name)
            self.sid = self._container.create()
            for root in read_roots:
                self._grant(root, READ_RIGHTS)
            for root in write_roots:
                self._grant(root, WRITE_RIGHTS)
            parent = self.runtime_dir.parent
            parent.mkdir(parents=True, exist_ok=True)
            self._scratch = Path(tempfile.mkdtemp(prefix="scope-", dir=parent)).resolve()
            # Granted while empty, so the profile directories inherit the ACE.
            self._container.grant(self._scratch, "F")
            for sub in set(quarantine.PROFILE_DIRS.values()):
                (self._scratch / sub).mkdir()
        except (appcontainer.AppContainerError, OSError) as error:
            raise ScopedRunnerError(f"could not build the sandbox: {error}") from error
        self._env = execution.NativeExecutionEnvironment(
            cwd, name=f"scope-{self._scratch.name}", limits=self.limits,
            egress=execution.Egress(execution.DENY_ALL),
            profile=self._profile(), container=self._container)
        self._env.__enter__()

    def _grant(self, root: Path, rights: str) -> None:
        self._container.grant(root, rights)
        self._granted.append(root)

    def _profile(self) -> dict[str, str]:
        """The environment the command gets: built from nothing."""
        env = {name: os.environ[name] for name in ("PATH", "SYSTEMROOT") if name in os.environ}
        for variable, directory in quarantine.PROFILE_DIRS.items():
            env[variable] = str(self._scratch / directory)
        return env

    def __exit__(self, *exc) -> None:
        problems = []
        if self._env is not None:
            self._env.terminate()
        env, self._env = self._env, None
        container, self._container = self._container, None
        granted, self._granted = self._granted, []
        scratch, self._scratch = self._scratch, None
        sid, self.sid = self.sid, ""
        try:
            for root in granted:
                done = subprocess.run(["icacls", str(root), "/remove:g", f"*{sid}"],
                                      capture_output=True, text=True, timeout=120)
                if done.returncode != 0:
                    problems.append(f"icacls could not remove the grant on {root.name}: "
                                    f"{(done.stdout + done.stderr).strip()[:300]}")
        finally:
            if container is not None:
                container.close()
            if scratch is not None:
                try:
                    quarantine._remove_tree(scratch)
                except OSError as error:
                    problems.append(f"could not remove the scratch profile {scratch.name}: {error}")
        if env is not None:
            logger.info("scoped_runner.closed run=%s ran=%d", self.envelope.run_id, len(env.executions))
        if problems:
            raise ScopedRunnerError("; ".join(problems))

    # -- running -----------------------------------------------------------

    def run(self, vector, *, timeout: float | None = None) -> execution.Execution:
        """Run one command inside the sandbox and wait for it. `python` as the
        program means the provisioned runtime; any other program is passed as
        given. Which programs may run is the caller's decision."""
        if self._env is None:
            raise ScopedRunnerError("the scoped runner is not open")
        vector = [str(part) for part in vector]
        if not vector:
            raise ScopedRunnerError("no command to run")
        if vector[0] == "python":
            vector[0] = self._python
        return self._env.run(vector, timeout=timeout)
