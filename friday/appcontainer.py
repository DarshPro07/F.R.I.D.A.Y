"""
A Windows AppContainer, through ctypes: the OS-level boundary under quarantine
(CAP-FORGE-003).

An AppContainer is a token the kernel checks twice. A file is reachable only if
the user's own SIDs *and* the container's SID (or ALL APPLICATION PACKAGES) are
granted on it, and a container with no capability has no network at all, not
even loopback. So a process started here cannot read a user file or open a
socket by any route - `open`, `_winapi.CreateFile`, a raw `_socket`, an
`_overlapped` ConnectEx, a child made with `_winapi.CreateProcess` - because the
refusal is the kernel's, not an in-process hook's. Children inherit the token,
and the Job Object has no breakaway, so there is no way out through them.

What it does not claim: it is not a VM. Kernel and broker-service bugs, and
whatever ALL APPLICATION PACKAGES already exposes (the Windows and Program Files
trees), remain reachable.

Pieces:

  * `AppContainer(name)`: a profile with a fresh SID, deleted on `close`.
    `grant` adds an ACE for that SID through `icacls`, so a directory is
    reachable only because the quarantine said so.
  * `provision_runtime(parent)`: a copy of the interpreter and standard library
    the container may read. The real Python install sits under the user's
    profile, which the container (rightly) cannot read, and the user's ACLs are
    not edited to make it so.
  * `AppContainer.run`: CreateProcess with a SECURITY_CAPABILITIES attribute,
    stdio on inherited temp files, assigned to the caller's Job Object before
    the first instruction runs.
"""

from __future__ import annotations

import ctypes
import hashlib
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes
from pathlib import Path
from typing import Callable

logger = logging.getLogger("friday-agent")

WINDOWS = os.name == "nt"
ALL_APPLICATION_PACKAGES = "S-1-15-2-1"
#: A child that floods its stdout/stderr is killed rather than left to fill the disk.
MAX_OUTPUT_BYTES = 8 * 1024 * 1024

_SECURITY_CAPABILITIES_ATTRIBUTE = 0x00020009
_HANDLE_LIST_ATTRIBUTE = 0x00020002
_EXTENDED_STARTUPINFO_PRESENT = 0x00080000
_CREATE_SUSPENDED = 0x00000004
_CREATE_UNICODE_ENVIRONMENT = 0x00000400
_CREATE_NO_WINDOW = 0x08000000
_STARTF_USESTDHANDLES = 0x00000100
_WAIT_OBJECT_0 = 0
_HRESULT_ALREADY_EXISTS = 0x800700B7
_RUNTIME_IGNORE = shutil.ignore_patterns(
    "site-packages", "test", "idlelib", "tkinter", "turtledemo", "__pycache__",
    "ensurepip", "lib2to3")


class AppContainerError(RuntimeError):
    """The container could not be made, granted, or started."""


class _SECURITY_CAPABILITIES(ctypes.Structure):
    _fields_ = [("AppContainerSid", ctypes.c_void_p),
                ("Capabilities", ctypes.c_void_p),
                ("CapabilityCount", wintypes.DWORD),
                ("Reserved", wintypes.DWORD)]


class _STARTUPINFOW(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR),
                ("lpDesktop", wintypes.LPWSTR), ("lpTitle", wintypes.LPWSTR),
                ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD),
                ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD),
                ("dwXCountChars", wintypes.DWORD), ("dwYCountChars", wintypes.DWORD),
                ("dwFillAttribute", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD),
                ("lpReserved2", ctypes.c_void_p), ("hStdInput", wintypes.HANDLE),
                ("hStdOutput", wintypes.HANDLE), ("hStdError", wintypes.HANDLE)]


class _STARTUPINFOEXW(ctypes.Structure):
    _fields_ = [("StartupInfo", _STARTUPINFOW), ("lpAttributeList", ctypes.c_void_p)]


class _PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE),
                ("dwProcessId", wintypes.DWORD), ("dwThreadId", wintypes.DWORD)]


def available() -> bool:
    """Whether this host can make an AppContainer at all (Windows 8 or later)."""
    return WINDOWS and sys.getwindowsversion().major >= 6 and hasattr(ctypes, "WinDLL")


def _apis():
    userenv = ctypes.WinDLL("userenv", use_last_error=True)
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    userenv.CreateAppContainerProfile.argtypes = [
        wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_void_p,
        wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)]
    userenv.CreateAppContainerProfile.restype = ctypes.c_long
    userenv.DeriveAppContainerSidFromAppContainerName.argtypes = [
        wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p)]
    userenv.DeriveAppContainerSidFromAppContainerName.restype = ctypes.c_long
    userenv.DeleteAppContainerProfile.argtypes = [wintypes.LPCWSTR]
    userenv.DeleteAppContainerProfile.restype = ctypes.c_long
    advapi32.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    advapi32.FreeSid.argtypes = [ctypes.c_void_p]
    advapi32.FreeSid.restype = ctypes.c_void_p
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    kernel32.InitializeProcThreadAttributeList.argtypes = [
        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.c_size_t)]
    kernel32.InitializeProcThreadAttributeList.restype = wintypes.BOOL
    kernel32.UpdateProcThreadAttribute.argtypes = [
        ctypes.c_void_p, wintypes.DWORD, ctypes.c_size_t, ctypes.c_void_p,
        ctypes.c_size_t, ctypes.c_void_p, ctypes.c_void_p]
    kernel32.UpdateProcThreadAttribute.restype = wintypes.BOOL
    kernel32.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
    kernel32.DeleteProcThreadAttributeList.restype = None
    kernel32.CreateProcessW.argtypes = [
        wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p, ctypes.c_void_p,
        wintypes.BOOL, wintypes.DWORD, ctypes.c_void_p, wintypes.LPCWSTR,
        ctypes.POINTER(_STARTUPINFOEXW), ctypes.POINTER(_PROCESS_INFORMATION)]
    kernel32.CreateProcessW.restype = wintypes.BOOL
    kernel32.ResumeThread.argtypes = [wintypes.HANDLE]
    kernel32.ResumeThread.restype = wintypes.DWORD
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    return userenv, advapi32, kernel32


def _sid_string(advapi32, kernel32, sid: ctypes.c_void_p) -> str:
    text = wintypes.LPWSTR()
    if not advapi32.ConvertSidToStringSidW(sid, ctypes.byref(text)):
        raise AppContainerError(f"could not read the container SID: {ctypes.WinError(ctypes.get_last_error())}")
    try:
        return text.value
    finally:
        kernel32.LocalFree(ctypes.cast(text, ctypes.c_void_p))


def grant(path: str | Path, sid: str, rights: str, *, inherit: bool = True) -> None:
    """Add an ACE for `sid` on `path`. `rights` is an icacls mask: F, M, RX..."""
    spec = f"*{sid}:" + ("(OI)(CI)" if inherit else "") + f"({rights})"
    done = subprocess.run(["icacls", str(path), "/grant", spec], capture_output=True,
                          text=True, timeout=120)
    if done.returncode != 0:
        raise AppContainerError(f"icacls could not grant {rights} on {Path(path).name}: "
                                f"{(done.stdout + done.stderr).strip()[:300]}")


class AppContainer:
    """One profile, one SID. A process started through `run` has no capability:
    no network, and files only where `grant` put the SID."""

    def __init__(self, name: str) -> None:
        if not available():
            raise AppContainerError("AppContainer needs Windows 8 or later")
        self.name = name
        self.sid = ""
        self._sid_ptr = ctypes.c_void_p()
        self._created = False
        self._apis = _apis()

    def __enter__(self) -> "AppContainer":
        self.create()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def create(self) -> str:
        userenv, advapi32, kernel32 = self._apis
        result = userenv.CreateAppContainerProfile(
            self.name, self.name, "FRIDAY repository quarantine", None, 0,
            ctypes.byref(self._sid_ptr)) & 0xFFFFFFFF
        if result == _HRESULT_ALREADY_EXISTS:
            result = userenv.DeriveAppContainerSidFromAppContainerName(
                self.name, ctypes.byref(self._sid_ptr)) & 0xFFFFFFFF
        elif result == 0:
            self._created = True
        if result != 0 or not self._sid_ptr:
            raise AppContainerError(f"could not create the AppContainer profile (HRESULT {result:#x})")
        self.sid = _sid_string(advapi32, kernel32, self._sid_ptr)
        logger.info("appcontainer.created name=%s", self.name)
        return self.sid

    def close(self) -> None:
        """Idempotent: the profile is removed whichever way the run ended."""
        userenv, advapi32, _ = self._apis
        if self._sid_ptr:
            advapi32.FreeSid(self._sid_ptr)
            self._sid_ptr = ctypes.c_void_p()
        if self.sid:
            userenv.DeleteAppContainerProfile(self.name)
            self.sid = ""

    def grant(self, path: str | Path, rights: str, *, inherit: bool = True) -> None:
        grant(path, self.sid, rights, inherit=inherit)

    # -- running -----------------------------------------------------------

    def run(self, command, *, cwd: str | Path, env: dict[str, str], timeout: float,
            stdin: str | None = None,
            adopt: Callable[[int], bool] | None = None) -> tuple[int, str, str, bool]:
        """Run one command inside the container and wait for it. Returns
        (exit code, stdout, stderr, timed out). `adopt(pid)` puts the suspended
        process into the caller's Job Object before it runs; a False from it is
        a failure to contain, so the process is killed and this raises."""
        if not self.sid:
            raise AppContainerError("the container is not created")
        _, _, kernel32 = self._apis
        command = [str(part) for part in command]
        files = [tempfile.TemporaryFile(), tempfile.TemporaryFile(), tempfile.TemporaryFile()]
        attributes = None
        info = _PROCESS_INFORMATION()
        try:
            if stdin:
                files[0].write(stdin.encode("utf-8"))
                files[0].flush()
                files[0].seek(0)
            handles = [_inheritable(f) for f in files]
            capabilities = _SECURITY_CAPABILITIES(self._sid_ptr, None, 0, 0)
            inherited = (wintypes.HANDLE * 3)(*handles)
            attributes, keep = self._attribute_list(kernel32, capabilities, inherited)
            startup = _STARTUPINFOEXW()
            startup.StartupInfo.cb = ctypes.sizeof(startup)
            startup.StartupInfo.dwFlags = _STARTF_USESTDHANDLES
            startup.StartupInfo.hStdInput, startup.StartupInfo.hStdOutput, \
                startup.StartupInfo.hStdError = handles
            startup.lpAttributeList = ctypes.addressof(attributes)
            block = "".join(f"{k}={v}\0" for k, v in sorted(env.items())) + "\0"
            block_bytes = block.encode("utf-16-le") + b"\0\0"
            environment = ctypes.create_string_buffer(block_bytes, len(block_bytes))
            line = ctypes.create_unicode_buffer(subprocess.list2cmdline(command))
            ok = kernel32.CreateProcessW(
                None, line, None, None, True,
                _EXTENDED_STARTUPINFO_PRESENT | _CREATE_SUSPENDED
                | _CREATE_UNICODE_ENVIRONMENT | _CREATE_NO_WINDOW,
                ctypes.cast(environment, ctypes.c_void_p), str(cwd),
                ctypes.byref(startup), ctypes.byref(info))
            if not ok:
                raise AppContainerError(f"could not start inside the container: "
                                        f"{ctypes.WinError(ctypes.get_last_error())}")
            del keep
            if adopt is not None and not adopt(info.dwProcessId):
                kernel32.TerminateProcess(info.hProcess, 1)
                raise AppContainerError("could not put the process into the job object")
            kernel32.ResumeThread(info.hThread)
            code, timed_out = self._wait(kernel32, info.hProcess, files, timeout)
            out = _slurp(files[1])
            err = _slurp(files[2])
            if timed_out:
                err = f"{err}\nkilled after {timeout:.0f}s"
            return code, out, err, timed_out
        finally:
            if attributes is not None:
                kernel32.DeleteProcThreadAttributeList(ctypes.addressof(attributes))
            for handle in (info.hThread, info.hProcess):
                if handle:
                    kernel32.CloseHandle(handle)
            for stream in files:
                stream.close()

    @staticmethod
    def _attribute_list(kernel32, capabilities, inherited):
        size = ctypes.c_size_t(0)
        kernel32.InitializeProcThreadAttributeList(None, 2, 0, ctypes.byref(size))
        buffer = ctypes.create_string_buffer(size.value)
        if not kernel32.InitializeProcThreadAttributeList(buffer, 2, 0, ctypes.byref(size)):
            raise AppContainerError(f"attribute list: {ctypes.WinError(ctypes.get_last_error())}")
        for attribute, value, length in (
                (_SECURITY_CAPABILITIES_ATTRIBUTE, ctypes.addressof(capabilities),
                 ctypes.sizeof(capabilities)),
                (_HANDLE_LIST_ATTRIBUTE, ctypes.addressof(inherited), ctypes.sizeof(inherited))):
            if not kernel32.UpdateProcThreadAttribute(buffer, 0, attribute, value, length, None, None):
                raise AppContainerError(f"attribute {attribute:#x}: "
                                        f"{ctypes.WinError(ctypes.get_last_error())}")
        # The two values must outlive CreateProcess; the caller holds them.
        return buffer, (capabilities, inherited)

    @staticmethod
    def _wait(kernel32, process, files, timeout: float) -> tuple[int, bool]:
        deadline = time.monotonic() + timeout
        timed_out = False
        while kernel32.WaitForSingleObject(process, 100) != _WAIT_OBJECT_0:
            flooding = any(os.fstat(f.fileno()).st_size > MAX_OUTPUT_BYTES for f in files[1:])
            if flooding or time.monotonic() >= deadline:
                timed_out = True
                kernel32.TerminateProcess(process, 1)
                kernel32.WaitForSingleObject(process, 5000)
                break
        status = wintypes.DWORD(0)
        kernel32.GetExitCodeProcess(process, ctypes.byref(status))
        return (124 if timed_out else status.value), timed_out


def _inheritable(stream) -> int:
    import msvcrt
    handle = msvcrt.get_osfhandle(stream.fileno())
    os.set_handle_inheritable(handle, True)
    return handle


def _slurp(stream) -> str:
    stream.seek(0)
    return stream.read(MAX_OUTPUT_BYTES).decode("utf-8", "replace")


def provision_runtime(parent: str | Path) -> Path:
    """The interpreter and stdlib, copied once under `parent`, readable by every
    AppContainer. Returns the python.exe to run. The real install is left
    alone: the user's own directories keep their own ACLs."""
    base = Path(sys.base_prefix)
    key = f"py{sys.version_info.major}{sys.version_info.minor}-" + hashlib.sha1(
        str(base).lower().encode()).hexdigest()[:8]
    parent = Path(parent)
    target = parent / key
    exe = target / "python.exe"
    if (target / "READY").is_file() and exe.is_file():
        return exe
    parent.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix=f"{key}.part-", dir=parent))
    try:
        grant(scratch, ALL_APPLICATION_PACKAGES, "RX")      # before the copy: files inherit it
        for item in base.iterdir():
            if item.is_file() and (item.suffix.lower() == ".dll"
                                   or item.name.lower() == "python.exe"):
                shutil.copyfile(item, scratch / item.name)
        for directory in ("DLLs", "Lib"):
            if (base / directory).is_dir():
                shutil.copytree(base / directory, scratch / directory, ignore=_RUNTIME_IGNORE)
        if not (scratch / "python.exe").is_file():
            raise AppContainerError(f"no python.exe under {base.name}")
        (scratch / "READY").write_text(key, encoding="utf-8")
        try:
            os.replace(scratch, target)
        except OSError:
            if not (target / "READY").is_file():       # another run won the race
                raise
    finally:
        if scratch.exists():
            shutil.rmtree(scratch, ignore_errors=True)
    return exe
