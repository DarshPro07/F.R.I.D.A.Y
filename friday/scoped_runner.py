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

Inside a granted root the runner keeps the file jail's line (EXEC-SBX-001,
slice 2): every file or directory whose path matches `fsjail.DENY_PATTERNS`
(.env, .git, .ssh, keys, credentials) is cut off from the container SID: it
stops inheriting the root's grant and loses the SID's copy (an explicit deny
entry for an AppContainer SID is not honoured by the kernel, measured). The
path's DACL is saved as SDDL first and put back exactly at exit (explicit
entries and the protected flag only; Windows recomputes the inherited ones from
the parent). Runners share one registry of cut-off paths (`protected.json` in
the lease directory, under a lock file): the first runner to need a path saves
and cuts it off, the last one to leave restores it. A root that holds or lies
inside FRIDAY's install or state tree (outside the work areas) is refused. Each runner has its own profile.

A run that is killed outright cannot take its grants off. Every open runner
therefore keeps a lease file (pid, SID, profile, granted and denied paths);
`sweep` removes the entries and the profile of every lease whose process is
gone, and a new runner calls it first.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import logging
import os
import re
import subprocess
import tempfile
import time
import uuid
from contextlib import contextmanager
from ctypes import wintypes
from pathlib import Path

from . import appcontainer, config, execution, fsjail, quarantine
from .contracts import Envelope, Limits

logger = logging.getLogger(__name__)

READ_RIGHTS = "RX"
WRITE_RIGHTS = "M"
#: Files and directories one walk over the roots may visit. Above it the run is
#: refused: a root this large cannot be protected by name before the command runs.
MAX_WALK = 200_000
LEASE_DIR_NAME = "scope-leases"
REGISTRY_NAME = "protected.json"
LOCK_NAME = "protected.lock"
#: How long a runner waits for the registry lock, and how old a lock must be
#: (with a dead writer) before it is taken over.
LOCK_WAIT = 30.0
LOCK_STALE = 120.0
_SID_RE = re.compile(r"S-1-15-2(-\d+)+")


class ScopedRunnerError(RuntimeError):
    """The envelope was refused, or the sandbox could not be built or removed."""


def _protected_directories() -> list[Path]:
    """Directories no root may be or lie under."""
    names = ("WINDIR", "ProgramFiles", "ProgramFiles(x86)")
    return [Path(os.environ[name]).resolve() for name in names if os.environ.get(name)]


def _refuse_own_state(root: Path, raw: str, kind: str) -> None:
    """A grant covers everything beneath a root, so a root may neither hold nor
    lie inside either of FRIDAY's two trees (the code, `config.PROJECT_ROOT`, and
    the state, `config.STATE_ROOT`: logs, vault, config, data) - the one way in is
    a work area, where the file tools already work. In development both trees
    are the repository, so its own checkouts are refused too."""
    for tree in (Path(config.PROJECT_ROOT).resolve(), Path(config.STATE_ROOT).resolve()):
        if tree.is_relative_to(root):
            raise ScopedRunnerError(
                f"{kind} root {raw!r} holds FRIDAY's install or state directory")
        if root.is_relative_to(tree) and not any(
                root.is_relative_to(Path(area).resolve()) for area in fsjail.WORK_AREAS):
            raise ScopedRunnerError(
                f"{kind} root {raw!r} is inside FRIDAY's install or state directory, "
                "outside its work areas")


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
    _refuse_own_state(root, raw, kind)
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


def _icacls(path: Path, *args: str) -> str:
    """Run icacls on one path; "" on success, else why it failed."""
    done = subprocess.run(["icacls", str(path), *args], capture_output=True,
                          text=True, timeout=120)
    return "" if done.returncode == 0 else (done.stdout + done.stderr).strip()[:300]


def _cut_off(path: Path, sid: str) -> None:
    """Make one protected path unreachable for the container. An explicit
    deny entry for an AppContainer SID is NOT honoured (measured on this host:
    the process still reads the file), so the path stops inheriting the root's
    grant instead and the SID's copy of it is removed."""
    for args in (("/inheritance:d",), ("/remove:g", f"*{sid}")):
        why = _icacls(path, *args)
        if why:
            raise ScopedRunnerError(f"could not cut off {path.name}: {why}")


_DACL_INFORMATION = 0x4
_PROTECTED_DACL = 0x80000000
_UNPROTECTED_DACL = 0x20000000
_SE_FILE_OBJECT = 1
_SDDL_REVISION_1 = 1


def _advapi32():
    """advapi32 with the signatures the DACL helpers use (pointers as c_void_p)."""
    lib = ctypes.WinDLL("advapi32", use_last_error=True)
    ptr = ctypes.c_void_p
    lib.GetNamedSecurityInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                          ptr, ptr, ptr, ptr, ptr]
    lib.GetNamedSecurityInfoW.restype = wintypes.DWORD
    lib.SetNamedSecurityInfoW.argtypes = [wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD,
                                          ptr, ptr, ptr, ptr]
    lib.SetNamedSecurityInfoW.restype = wintypes.DWORD
    lib.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [
        ptr, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(wintypes.ULONG)]
    lib.ConvertSecurityDescriptorToStringSecurityDescriptorW.restype = wintypes.BOOL
    lib.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ptr), ctypes.POINTER(wintypes.ULONG)]
    lib.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    lib.GetSecurityDescriptorDacl.argtypes = [ptr, ctypes.POINTER(wintypes.BOOL),
                                              ctypes.POINTER(ptr), ctypes.POINTER(wintypes.BOOL)]
    lib.GetSecurityDescriptorDacl.restype = wintypes.BOOL
    return lib


def _local_free(pointer) -> None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    kernel32.LocalFree(pointer)


def _read_sddl(path: Path) -> str:
    """The DACL of one path as an SDDL string."""
    lib = _advapi32()
    descriptor = ctypes.c_void_p()
    code = lib.GetNamedSecurityInfoW(str(path), _SE_FILE_OBJECT, _DACL_INFORMATION, None, None,
                                     None, None, ctypes.byref(descriptor))
    if code:
        raise ScopedRunnerError(f"could not read the ACL of {path.name} (error {code})")
    try:
        text = wintypes.LPWSTR()
        if not lib.ConvertSecurityDescriptorToStringSecurityDescriptorW(
                descriptor, _SDDL_REVISION_1, _DACL_INFORMATION, ctypes.byref(text), None):
            raise ScopedRunnerError(
                f"could not read the ACL of {path.name} as SDDL (error {ctypes.get_last_error()})")
        try:
            return text.value or ""
        finally:
            _local_free(ctypes.cast(text, ctypes.c_void_p))
    finally:
        _local_free(descriptor)


def _parse_dacl(sddl: str) -> tuple[bool, list[tuple[str, list[str], str]]]:
    """(protected, [(ace, flags, sid), ...]) of a DACL in SDDL form, in order.
    Anything that does not parse raises: a DACL read wrongly must not read as
    "no entry"."""
    if not sddl.startswith("D:"):
        raise ScopedRunnerError(f"unexpected SDDL {sddl[:40]!r}")
    body = sddl[2:]
    first = body.find("(")
    header, rest = (body, "") if first < 0 else (body[:first], body[first:])
    aces, depth, start = [], 0, 0
    for index, char in enumerate(rest):
        if char == "(":
            if depth == 0:
                start = index
            depth += 1
        elif char == ")":
            depth -= 1
            if depth < 0:
                raise ScopedRunnerError(f"unbalanced SDDL {sddl[:40]!r}")
            if depth == 0:
                ace = rest[start:index + 1]
                fields = ace[1:-1].split(";")
                if len(fields) < 6 or not fields[5]:
                    raise ScopedRunnerError(f"unreadable ACE {ace[:40]!r}")
                flags = [fields[1][i:i + 2] for i in range(0, len(fields[1]), 2)]
                aces.append((ace, flags, fields[5]))
    if depth:
        raise ScopedRunnerError(f"unbalanced SDDL {sddl[:40]!r}")
    return "P" in header, aces


def _aces(sddl: str) -> tuple[bool, list[str]]:
    """(protected, explicit ACE strings) of a DACL in SDDL form: every ACE whose
    flags carry ID (inherited) is dropped, the parent supplies those again."""
    protected, aces = _parse_dacl(sddl)
    return protected, [ace for ace, flags, _ in aces if "ID" not in flags]


def _inherited_grant(sddl: str, sid: str) -> bool:
    """Whether the DACL holds an inherited (ID) entry of `sid`: the grant the
    root's `AppContainer.grant` handed down to this path."""
    return any("ID" in flags and ace_sid == sid
               for _, flags, ace_sid in _parse_dacl(sddl)[1])


def _restore_dacl(path: Path, sddl: str) -> None:
    """Put one path back to the DACL saved as `sddl`: its explicit entries and
    its protected flag, with the inherited entries recomputed by Windows from
    the parent. (`icacls /inheritance:e` cannot do this: it leaves the copies
    that `/inheritance:d` made, and `icacls /restore` needs a privilege an
    unelevated run lacks.) Raises with the error code, never passes a NULL DACL."""
    protected, explicit = _aces(sddl)
    lib = _advapi32()
    descriptor = ctypes.c_void_p()
    if not lib.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            "D:" + ("P" if protected else "") + "AI" + "".join(explicit), _SDDL_REVISION_1,
            ctypes.byref(descriptor), None):
        raise ScopedRunnerError(
            f"could not build the saved ACL of {path.name} (error {ctypes.get_last_error()})")
    try:
        present, defaulted, dacl = wintypes.BOOL(), wintypes.BOOL(), ctypes.c_void_p()
        if not lib.GetSecurityDescriptorDacl(descriptor, ctypes.byref(present), ctypes.byref(dacl),
                                             ctypes.byref(defaulted)):
            raise ScopedRunnerError(f"could not read the saved ACL of {path.name}")
        if not present.value or not dacl.value:
            raise ScopedRunnerError(f"the saved ACL of {path.name} has no DACL")
        flag = _PROTECTED_DACL if protected else _UNPROTECTED_DACL
        code = lib.SetNamedSecurityInfoW(str(path), _SE_FILE_OBJECT, _DACL_INFORMATION | flag,
                                         None, None, dacl, None)
        if code:
            raise ScopedRunnerError(f"could not restore the ACL of {path.name} (error {code})")
    finally:
        _local_free(descriptor)


# -- the registry of cut-off paths, shared by all runners ---------------------

class _Registry:
    """{path: {"sddl": the original DACL, "holders": [profile, ...]}}, kept in
    `protected.json` and only touched under `_registry`'s lock."""

    def __init__(self, file: Path) -> None:
        self.file = file
        try:
            self.data = json.loads(file.read_text(encoding="utf-8")) if file.exists() else {}
        except (OSError, ValueError) as error:
            raise ScopedRunnerError(f"unreadable {file.name}: {error}") from error

    def save(self) -> None:
        partial = self.file.with_suffix(".tmp")
        partial.write_text(json.dumps(self.data), encoding="utf-8")
        os.replace(partial, self.file)


def _lock_writer_alive(lock: Path) -> bool:
    try:
        return _pid_alive(int(lock.read_text(encoding="utf-8").strip()))
    except (OSError, ValueError):
        return True  # unreadable: its writer may be mid-write


@contextmanager
def _registry(lease_dir: Path):
    """The registry under an exclusive lock file (created with O_EXCL, retried
    for `LOCK_WAIT`, a lock older than `LOCK_STALE` whose writer is dead is taken
    over). Changes are saved when the block ends without an error, or by `save`."""
    lease_dir.mkdir(parents=True, exist_ok=True)
    lock = lease_dir / LOCK_NAME
    deadline = time.monotonic() + LOCK_WAIT
    while True:
        try:
            handle = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            try:
                stale = time.time() - lock.stat().st_mtime > LOCK_STALE
            except OSError:
                continue  # it just went away
            if stale and not _lock_writer_alive(lock):
                lock.unlink(missing_ok=True)
                continue
            if time.monotonic() > deadline:
                raise ScopedRunnerError(f"timed out waiting for the lock {lock.name}") from None
            time.sleep(0.05)
    try:
        os.write(handle, str(os.getpid()).encode())
        os.close(handle)
        registry = _Registry(lease_dir / REGISTRY_NAME)
        yield registry
        registry.save()
    finally:
        lock.unlink(missing_ok=True)


def _hold(lease_dir: Path, path: Path, profile: str, sid: str) -> bool:
    """Make `profile` a holder of the cut-off of `path`. The first holder saves
    the DACL and cuts the path off; later ones only add themselves. False when
    the path never inherited the grant (judged from the DACL that is saved, so
    an unreadable DACL raises instead), so nothing was cut off and nobody holds it."""
    key = os.path.normcase(str(path))
    with _registry(lease_dir) as registry:
        entry = registry.data.get(key)
        if entry is not None:
            if profile not in entry["holders"]:
                entry["holders"].append(profile)
            return True
        sddl = _read_sddl(path)
        if not _inherited_grant(sddl, sid):
            return False
        # Saved before the cut-off, so a crash leaves an entry, never a cut-off
        # nobody can undo.
        registry.data[key] = {"sddl": sddl, "holders": [profile]}
        registry.save()
        try:
            _cut_off(path, sid)
        except ScopedRunnerError:
            _restore_dacl(path, sddl)
            del registry.data[key]
            registry.save()
            raise
        return True


def _release(lease_dir: Path, path: Path, profile: str) -> None:
    """Take `profile` off the holders of `path`; the last one out restores the
    saved DACL. A path unknown to the registry was never cut off."""
    key = os.path.normcase(str(path))
    with _registry(lease_dir) as registry:
        entry = registry.data.get(key)
        if entry is None:
            return
        entry["holders"] = [holder for holder in entry["holders"] if holder != profile]
        if entry["holders"]:
            return
        if path.exists():
            _restore_dacl(path, entry["sddl"])
        del registry.data[key]


def _protected(path: Path) -> bool:
    return any(pattern.search(str(path)) for pattern in fsjail._DENY_RE)


def _pid_alive(pid: int) -> bool:
    """Whether a process with this id is running. A reused id reads as alive,
    which only delays a sweep; it never removes a live run's grants."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ctypes.get_last_error() == 5  # access denied: it exists
    try:
        code = wintypes.DWORD()
        return not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value == 259
    finally:
        kernel32.CloseHandle(handle)


def _default_lease_dir() -> Path:
    return quarantine.default_runtime_dir().parent / LEASE_DIR_NAME


def _sweep_lease(lease: Path) -> bool:
    """Clean one lease whose process is gone. True when cleaned and removed,
    False when its process is still running; raises with the reason when it
    cannot be cleaned, so the lease stays for the next sweep."""
    try:
        record = json.loads(lease.read_text(encoding="utf-8"))
        pid, sid, profile = record["pid"], record["sid"], record["profile"]
        granted, denied = list(record["granted"]), list(record["denied"])
        scratch = record.get("scratch") or ""
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ScopedRunnerError(f"unreadable lease: {error}") from error
    if (not isinstance(pid, int) or not isinstance(sid, str) or not _SID_RE.fullmatch(sid)
            or not isinstance(profile, str) or not profile.startswith("friday-scope-")):
        raise ScopedRunnerError("malformed lease")
    if _pid_alive(pid):
        return False
    problems = []
    for path in denied:
        try:
            _release(lease.parent, Path(path), profile)
        except ScopedRunnerError as error:
            problems.append(f"restore {Path(path).name}: {error}")
    for path in granted:
        if Path(path).exists():
            why = _icacls(Path(path), "/remove:g", f"*{sid}")
            if why:
                problems.append(f"/remove:g {Path(path).name}: {why}")
    if scratch:
        scratch = Path(scratch)
        if scratch.name.startswith("scope-") and scratch.parent == lease.parent.parent                 and scratch.is_dir():
            try:
                quarantine._remove_tree(scratch)
            except OSError as error:
                problems.append(f"scratch {scratch.name}: {error}")
    if problems:
        raise ScopedRunnerError("; ".join(problems))
    container = appcontainer.AppContainer(profile)
    container.sid = sid
    container.close()
    lease.unlink()
    return True


def sweep(lease_dir: str | Path | None = None) -> list[str]:
    """Take the grants, denies and profile of every dead run off again and
    return the profiles cleaned. A lease whose process is still running is left
    alone; one that cannot be cleaned is logged with the reason and kept."""
    lease_dir = Path(lease_dir) if lease_dir is not None else _default_lease_dir()
    cleaned = []
    if not lease_dir.is_dir():
        return cleaned
    for lease in sorted(lease_dir.glob("*.json")):
        if lease.name == REGISTRY_NAME:
            continue
        try:
            if _sweep_lease(lease):
                cleaned.append(lease.stem)
                logger.info("scoped_runner.swept profile=%s", lease.stem)
        except (ScopedRunnerError, appcontainer.AppContainerError, OSError,
                subprocess.SubprocessError) as error:
            logger.warning("scoped_runner.sweep_failed lease=%s: %s", lease.name, error)
    return cleaned


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
        #: The container's SID and profile name while open, so a caller can
        #: audit the ACLs. The name is unique per runner.
        self.sid = ""
        self.profile = ""
        self._python = ""
        self._container: appcontainer.AppContainer | None = None
        self._env: execution.NativeExecutionEnvironment | None = None
        self._granted: list[Path] = []
        self._denied: list[Path] = []
        self._scratch: Path | None = None
        self._lease: Path | None = None

    # -- lifecycle ---------------------------------------------------------

    def __enter__(self) -> "ScopedRunner":
        write_roots, read_roots, cwd = self._validate()
        sweep(self.runtime_dir.parent / LEASE_DIR_NAME)
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
            self.profile = ("friday-scope-" + hashlib.sha1(self.envelope.run_id.encode())
                            .hexdigest()[:8] + "-" + uuid.uuid4().hex[:8])
            self._container = appcontainer.AppContainer(self.profile)
            self.sid = self._container.create()
            self._lease = self.runtime_dir.parent / LEASE_DIR_NAME / f"{self.profile}.json"
            self._write_lease()
            for root in read_roots:
                self._grant(root, READ_RIGHTS)
            for root in write_roots:
                self._grant(root, WRITE_RIGHTS)
            self._deny_protected(read_roots + write_roots)
            parent = self.runtime_dir.parent
            parent.mkdir(parents=True, exist_ok=True)
            self._scratch = Path(tempfile.mkdtemp(prefix="scope-", dir=parent)).resolve()
            self._write_lease()
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
        # Recorded in the lease before the ACE exists, so a crash between the
        # two leaves a lease that over-names, never a grant that nobody knows.
        self._granted.append(root)
        self._write_lease()
        self._container.grant(root, rights)

    def _write_lease(self) -> None:
        record = {"pid": os.getpid(), "sid": self.sid, "profile": self.profile,
                  "scratch": str(self._scratch) if self._scratch else "",
                  "granted": [str(path) for path in self._granted],
                  "denied": [str(path) for path in self._denied]}
        self._lease.parent.mkdir(parents=True, exist_ok=True)
        partial = self._lease.with_suffix(".tmp")
        partial.write_text(json.dumps(record), encoding="utf-8")
        os.replace(partial, self._lease)

    def _deny_protected(self, roots: list[Path]) -> None:
        """Walk every root and cut the container off from every file and
        directory the file jail would refuse (see `_cut_off`). A pruned
        directory is cut off as a whole. Links are neither followed nor
        touched: they give no reach of their own, and an ACL change through one
        would land on its target."""
        found: dict[str, Path] = {}
        seen = 0

        def failed(error: OSError) -> None:
            raise ScopedRunnerError(f"could not walk {error.filename!r}: {error.strerror}")

        for root in roots:
            for directory, subdirs, files in os.walk(root, followlinks=False, onerror=failed):
                walked = [(name, True) for name in subdirs] + [(name, False) for name in files]
                keep = []
                for name, is_dir in walked:
                    seen += 1
                    if seen > MAX_WALK:
                        raise ScopedRunnerError(
                            f"the walk over the roots passed {MAX_WALK} entries; "
                            "a root that large cannot be protected before the command runs")
                    path = Path(directory) / name
                    if fsjail.is_reparse_point(path):
                        continue
                    if _protected(path):
                        found[str(path).lower()] = path
                    elif is_dir:
                        keep.append(name)
                subdirs[:] = keep
        # Named in the lease before it is touched; narrowed to what was cut.
        self._denied.extend(found.values())
        self._write_lease()
        self._denied = [path for path in found.values()
                        if _hold(self._lease.parent, path, self.profile, self.sid)]
        self._write_lease()

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
        denied, self._denied = self._denied, []
        lease, self._lease = self._lease, None
        profile, self.profile = self.profile, ""
        scratch, self._scratch = self._scratch, None
        sid, self.sid = self.sid, ""
        try:
            for path in denied:
                try:
                    _release(lease.parent, path, profile)
                except ScopedRunnerError as error:
                    problems.append(f"could not restore {path.name}: {error}")
            for path in granted:
                why = _icacls(path, "/remove:g", f"*{sid}")
                if why:
                    problems.append(f"icacls could not remove the grant on {path.name}: {why}")
        finally:
            if container is not None:
                container.close()
            if scratch is not None:
                try:
                    quarantine._remove_tree(scratch)
                except OSError as error:
                    problems.append(f"could not remove the scratch profile {scratch.name}: {error}")
        if lease is not None and not problems:
            try:
                lease.unlink(missing_ok=True)
            except OSError as error:
                problems.append(f"could not remove the lease {lease.name}: {error}")
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
        return self._env.run(vector, timeout=self._bounded(timeout))

    def _bounded(self, timeout: float | None) -> float:
        """The command's wall time, never past what the envelope allows: the
        caller's timeout, the envelope's per-command limit and the time left
        before it expires - whichever ends first. An envelope already over is
        refused rather than given a moment of access."""
        bounds = [self.limits.seconds] + ([timeout] if timeout is not None else [])
        left = self.envelope.seconds_left()
        if left is not None:
            if left <= 0:
                raise ScopedRunnerError("the envelope has expired; nothing runs under it")
            bounds.append(left)
        return min(bounds)
