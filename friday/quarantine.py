"""
Quarantine: where a new repository is analysed before it gets any authority
(CAP-FORGE-003).

A repository under analysis is untrusted code from outside. Until it has been
looked at, it must hold no secret, no FRIDAY memory, no user file and no
standing network. Pinned upstream clones (`third_party/upstream`) are audit
evidence only; this is the place an arbitrary one is taken in.

    with Quarantine(store=store, run_id=run_id) as q:
        q.acquire(local_checkout)
        result = q.run_python("probe.py")
        result.denials          # what the repository tried and was refused

What it builds, all inside one fresh root under `DATA_DIR/quarantine` (never
beside `ada.sqlite3`):

  * `acquire` copies a local checkout in, refusing a symlink or junction
    (`fsjail.is_reparse_point`), anything that is not a plain file or
    directory, and a tree over the file or byte cap. Nothing is copied unless
    the whole tree passed.
  * an environment built from nothing, not from the host allowlist: PATH and
    SYSTEMROOT, the profile variables rebound to empty directories inside the
    root, and a FRIDAY_QUARANTINE marker. No FRIDAY secret is in the process.
  * the run goes through `NativeExecutionEnvironment` with `DENY_ALL` egress,
    so the Job Object still bounds memory, process count and lifetime.
  * isolation="os" (the default): the child runs in a fresh AppContainer
    (`friday.appcontainer`) from a copy of the interpreter. The kernel then
    refuses every route to a user file or a socket, `_winapi.CreateFile`,
    raw `_socket`, `_overlapped` and a `_winapi.CreateProcess` grandchild
    included, because none of them is checked by anything in the process.
    The root alone is granted to that container's SID, so another quarantine's
    root is as unreachable as a user file.
  * a Python probe starts through `quarantine_guard`, an audit hook that
    RECORDS what the repository tried (file, socket, process, native, secret
    environment read) and refuses it early with a PermissionError. The hook is
    the evidence trail; it is not what holds the line.
  * each denial is persisted to the store as a `quarantine.denied` objective
    event, so the attempt outlives the quarantine, which is deleted on exit.

HONEST TIER: `APPCONTAINER+JOB_OBJECT+TRIPWIRE`. An attempt that goes around
the hook (an API that raises no audit event) is still refused by the kernel,
but the hook cannot record it; it shows only in the run's exit code and
stderr. An AppContainer is a Windows security boundary, not a VM: a kernel or
broker-service bug, and the system trees every packaged app may read, are out
of its scope. isolation="tripwire" is the explicit opt-in to the weaker
`JOB_OBJECT+TRIPWIRE` tier, for hosts with no AppContainer; there a hostile
native module can bypass the hook, and `friday/forge.py` keeps refusing
arbitrary code unless the tier is the OS one.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import stat
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from friday import appcontainer, execution, fsjail
from friday.config import DATA_DIR

logger = logging.getLogger("friday-agent")

TIER = "TRIPWIRE"
ISOLATIONS = ("os", "tripwire")
MAX_FILES = 5000
MAX_BYTES = 50 * 1024 * 1024
#: A hostile child can write into its own record file, so how much of it the
#: parent will read is bounded.
MAX_RECORD_BYTES = 256 * 1024
MAX_DENIALS = 200
KINDS = ("file", "network", "env", "process", "native")

#: Variable -> directory inside the root. Each is empty and owned by the run.
PROFILE_DIRS = {
    "HOME": "home", "USERPROFILE": "home",
    "APPDATA": "appdata", "LOCALAPPDATA": "localappdata",
    "TEMP": "tmp", "TMP": "tmp", "TMPDIR": "tmp",
}
_HOST_VARIABLES = ("PATH", "SYSTEMROOT")
_MODULE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")
_GUARD_SOURCE = Path(__file__).with_name("quarantine_guard.py")


class QuarantineError(RuntimeError):
    """The repository was refused, or the quarantine could not be kept."""


@dataclass(frozen=True)
class Denial:
    """One thing the repository tried and was refused. A name or address,
    never a secret's value."""

    kind: str
    event: str
    target: str
    at: float

    def as_dict(self) -> dict:
        return {"kind": self.kind, "event": self.event,
                "target": self.target, "at": self.at}


@dataclass(frozen=True)
class Analysis:
    """One run: what it did and what it was refused."""

    result: execution.Execution
    denials: tuple[Denial, ...]
    strength: str

    @property
    def denied(self) -> bool:
        return bool(self.denials)


def default_base() -> Path:
    return DATA_DIR / "quarantine"


def default_runtime_dir() -> Path:
    """Where the container's copy of the interpreter lives: shared by every
    quarantine, built once, and not inside any quarantine root."""
    return default_base() / "_runtime"


def _unlink_link(path: Path) -> None:
    try:
        os.unlink(path)
    except OSError:
        os.rmdir(path)      # a junction or directory symlink on Windows


def _unlink_file(path: Path) -> None:
    try:
        os.unlink(path)
    except PermissionError:
        os.chmod(path, stat.S_IWRITE)       # read-only objects under .git
        os.unlink(path)


def _remove_tree(path: Path) -> None:
    """Delete a tree without ever following a link out of it. A link is
    removed as a link, so a junction to the user's home deletes nothing."""
    if fsjail.is_reparse_point(path):
        _unlink_link(path)
        return
    with os.scandir(path) as entries:
        children = list(entries)
    for entry in children:
        child = Path(entry.path)
        if fsjail.is_reparse_point(child):
            _unlink_link(child)
        elif entry.is_dir(follow_symlinks=False):
            _remove_tree(child)
        else:
            _unlink_file(child)
    os.rmdir(path)


class Quarantine:
    """One quarantine root for one repository. Use as a context manager;
    leaving the block kills what is running and deletes the root."""

    def __init__(self, *, store, run_id: str = "", base: str | Path | None = None,
                 name: str = "quarantine", max_files: int = MAX_FILES,
                 max_bytes: int = MAX_BYTES, limits: execution.Limits | None = None,
                 isolation: str = "os", runtime_dir: str | Path | None = None) -> None:
        if isolation not in ISOLATIONS:
            raise QuarantineError(f"isolation must be one of {ISOLATIONS}, not {isolation!r}")
        self.isolation = isolation
        self.runtime_dir = Path(runtime_dir) if runtime_dir is not None else default_runtime_dir()
        self._container: appcontainer.AppContainer | None = None
        self._python = sys.executable
        # Required: a quarantine whose denials die with its root would make the
        # one thing worth keeping, the attempt, disappear on exit.
        self._store = store
        self.run_id = run_id
        self.base = Path(base) if base is not None else default_base()
        self.name = name
        self.max_files = max_files
        self.max_bytes = max_bytes
        self.limits = limits or execution.Limits(memory_mb=1024, processes=8, seconds=120.0)
        self.root: Path | None = None
        self._env: execution.NativeExecutionEnvironment | None = None
        self._acquired = False
        self._record_offset = 0
        self.denials: list[Denial] = []

    # -- layout ------------------------------------------------------------

    @property
    def repo(self) -> Path:
        return self._need_root() / "repo"

    @property
    def _guard_dir(self) -> Path:
        return self._need_root() / "_guard"

    @property
    def _record_file(self) -> Path:
        return self._need_root() / "_record" / "denials.jsonl"

    def _need_root(self) -> Path:
        if self.root is None:
            raise QuarantineError("the quarantine is not open")
        return self.root

    def profile(self) -> dict[str, str]:
        """The environment the repository gets: built from nothing."""
        root = self._need_root()
        env = {name: os.environ[name] for name in _HOST_VARIABLES if name in os.environ}
        for variable, directory in PROFILE_DIRS.items():
            env[variable] = str(root / directory)
        env["FRIDAY_QUARANTINE"] = "1"
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        return env

    # -- lifecycle ---------------------------------------------------------

    def __enter__(self) -> "Quarantine":
        if self.isolation == "os":
            # Fail closed: no container, no run. The weaker tier is a choice
            # the caller makes by name, never what a missing boundary falls to.
            if not appcontainer.available():
                raise QuarantineError(
                    "no OS boundary on this host (AppContainer needs Windows 8 or "
                    "later); isolation='tripwire' accepts the weaker tier explicitly")
            try:
                self._python = str(appcontainer.provision_runtime(self.runtime_dir))
            except (appcontainer.AppContainerError, OSError) as error:
                raise QuarantineError(f"could not prepare the container runtime: {error}") from error
        self.base.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix="q-", dir=self.base)).resolve()
        try:
            self._open_root()
        except BaseException:
            self.__exit__()
            raise
        logger.info("quarantine.open run=%s strength=%s", self.run_id, self.strength())
        return self

    def _open_root(self) -> None:
        if self.isolation == "os":
            try:
                self._container = appcontainer.AppContainer(f"friday-{self.root.name}")
                self._container.create()
                # Granted while the root is empty, so everything made inside it
                # inherits the ACE; nothing outside it ever receives one.
                self._container.grant(self.root, "F")
            except appcontainer.AppContainerError as error:
                raise QuarantineError(f"could not make the container: {error}") from error
        for sub in ("repo", "_guard", "_record", *set(PROFILE_DIRS.values())):
            (self.root / sub).mkdir()
        shutil.copyfile(_GUARD_SOURCE, self._guard_dir / _GUARD_SOURCE.name)
        self._record_file.touch()
        if not self.run_id:
            self.run_id = f"quarantine:{self.root.name}"
        self._env = execution.NativeExecutionEnvironment(
            self.repo, name=f"{self.name}-{self.root.name}", limits=self.limits,
            egress=execution.Egress(execution.DENY_ALL),
            profile=self.profile(), guard=TIER, container=self._container)
        self._env.__enter__()

    def __exit__(self, *exc) -> None:
        if self._env is not None:
            self._env.terminate()
        root, self.root = self.root, None
        container, self._container = self._container, None
        try:
            if root is None:
                return
            for attempt in range(3):
                try:
                    _remove_tree(root)
                    break
                except OSError as error:
                    if attempt == 2:
                        raise QuarantineError(
                            f"could not remove the quarantine {root.name}: {error}") from error
                    time.sleep(0.2)     # the killed process may still hold a handle
            logger.info("quarantine.closed run=%s denials=%d", self.run_id, len(self.denials))
        finally:
            if container is not None:
                container.close()

    def strength(self) -> str:
        return self._env.strength() if self._env is not None else ""

    # -- taking a repository in -------------------------------------------

    def acquire(self, source: str | Path) -> Path:
        """Copy a local checkout into the quarantine. The whole tree is checked
        first and nothing is written if any of it is refused."""
        root = self._need_root()
        if self._acquired:
            raise QuarantineError("a repository is already acquired")
        source = Path(source)
        if not source.is_dir():
            raise QuarantineError(f"no such repository directory: {source.name}")
        if fsjail.is_reparse_point(source):
            raise QuarantineError("refused: the repository root is a link")
        source = source.resolve()
        if root == source or root.is_relative_to(source):
            raise QuarantineError("refused: the repository contains the quarantine")
        directories, files = self._scan(source)
        for relative in directories:
            (self.repo / relative).mkdir(parents=True, exist_ok=True)
        for relative in files:
            shutil.copyfile(source / relative, self.repo / relative, follow_symlinks=False)
        self._acquired = True
        self._store.append_objective_event(self.run_id, "quarantine.acquired", detail={
            "files": len(files), "directories": len(directories),
            "strength": self.strength()})
        return self.repo

    def _scan(self, source: Path) -> tuple[list[str], list[str]]:
        directories: list[str] = []
        files: list[str] = []
        total = 0
        pending = [source]
        while pending:
            with os.scandir(pending.pop()) as entries:
                listing = list(entries)
            for entry in listing:
                path = Path(entry.path)
                relative = path.relative_to(source).as_posix()
                if fsjail.is_reparse_point(path):
                    raise QuarantineError(f"refused: {relative} is a symlink or junction")
                if entry.is_dir(follow_symlinks=False):
                    directories.append(relative)
                    pending.append(path)
                elif entry.is_file(follow_symlinks=False):
                    total += entry.stat(follow_symlinks=False).st_size
                    files.append(relative)
                else:
                    raise QuarantineError(f"refused: {relative} is not a plain file or directory")
                if len(files) + len(directories) > self.max_files:
                    raise QuarantineError(f"refused: more than {self.max_files} entries")
                if total > self.max_bytes:
                    raise QuarantineError(f"refused: more than {self.max_bytes} bytes")
        return directories, files

    # -- running it --------------------------------------------------------

    def run_python(self, target: str, *args: str, module: bool = False,
                   timeout: float | None = None) -> Analysis:
        """Run a script (relative to the repository) or `-m` module under the
        guard, with the quarantine environment and no network."""
        if not self._acquired or self._env is None:
            raise QuarantineError("acquire a repository first")
        if module:
            if not _MODULE_NAME.match(target):
                raise QuarantineError(f"not a module name: {target!r}")
        elif not self._env.resolve(target).is_file():
            raise QuarantineError(f"no such script in the repository: {target!r}")
        # Fresh each run: an earlier probe could have rewritten it.
        shutil.copyfile(_GUARD_SOURCE, self._guard_dir / _GUARD_SOURCE.name)
        command = [
            self._python, "-I", "-B", str(self._guard_dir / _GUARD_SOURCE.name),
            "--root", str(self.root), "--record", str(self._record_file),
            "--repo", str(self.repo), "--secret-pattern", execution._SECRET_SHAPED.pattern,
            "--readonly-dir", str(self._guard_dir),
            "--readonly-dir", str(self._record_file.parent),
            *(["--module"] if module else []), "--", target, *args]
        ran = self._env.run(command, timeout=timeout)
        found = self._read_denials()
        self.denials.extend(found)
        for denial in found:
            self._store.append_objective_event(
                self.run_id, "quarantine.denied",
                detail={**denial.as_dict(), "target_script": target,
                        "strength": self.strength()})
        self._store.append_objective_event(self.run_id, "quarantine.ran", detail={
            "target": target, "exit_code": ran.exit_code, "denials": len(found),
            "strength": self.strength()})
        return Analysis(ran, tuple(found), self.strength())

    def _read_denials(self) -> list[Denial]:
        """New record lines since the last run. The child can write this file,
        so the read is bounded and every line is validated, not trusted."""
        with open(self._record_file, "rb") as handle:
            handle.seek(self._record_offset)
            raw = handle.read(MAX_RECORD_BYTES)
        self._record_offset += len(raw)
        found: list[Denial] = []
        for line in raw.decode("utf-8", "replace").splitlines():
            try:
                entry = json.loads(line)
                kind, event = str(entry["kind"]), str(entry["event"])
                target, at = str(entry["target"])[:300], float(entry["at"])
            except (ValueError, KeyError, TypeError):
                logger.warning("quarantine.record_line_rejected run=%s", self.run_id)
                continue
            if kind not in KINDS:
                logger.warning("quarantine.record_kind_rejected run=%s", self.run_id)
                continue
            found.append(Denial(kind, event, target, at))
            if len(found) >= MAX_DENIALS:
                break
        return found

    # -- evidence ----------------------------------------------------------

    def report(self) -> dict:
        """What was run and refused, in a shape that can be stored."""
        report = self._env.report() if self._env is not None else {}
        report["tier"] = self.strength()
        report["isolation"] = self.isolation
        report["enforcement"] = (
            "os boundary: an AppContainer with no capability (no network, files only "
            "where granted) inside a Job Object; the audit hook records, it does not hold the line"
            if self.isolation == "os"
            else "tripwire: an in-process audit hook, not an OS boundary")
        report["denials"] = [d.as_dict() for d in self.denials]
        return report
