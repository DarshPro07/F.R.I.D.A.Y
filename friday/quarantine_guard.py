"""
The tripwire that runs INSIDE a quarantined Python process (CAP-FORGE-003).

`friday.quarantine` copies this file into the quarantine root and starts the
planted repository through it:

    python -I quarantine_guard.py --root R --record F ... -- script.py args

Standalone on purpose: it imports nothing from `friday`, because the child must
not be able to reach FRIDAY's package, and everything it needs arrives on the
command line (the layout and the secret-name pattern are owned by
`friday.quarantine`, not repeated here).

What it does, with `sys.addaudithook`:

  * `open` and the path-taking os/shutil events: allowed inside the quarantine
    root (the guard and record directories are read-only), reads also allowed
    under the interpreter's own prefixes so imports work; anything else is
    denied.
  * socket connect/bind/send and name resolution: denied.
  * subprocess, os.system/exec/spawn, ctypes loading: denied, because a child
    process or a native call would carry on without this hook.
  * `os.environ` is replaced by a view that denies secret-shaped names.

Every denial is appended to the record file, through a descriptor opened
before the hook was installed, and then raised as PermissionError.

HONESTY: an audit hook is a tripwire, not a wall, and its event list is open
ended: `_overlapped` and raw `_winapi` calls raise events only in some builds,
and some raise none. So this file is the RECORD of what a planted repository
tried and the early refusal, not the boundary. The boundary is the
AppContainer (`friday.appcontainer`) the child runs inside: an attempt that
slips past this hook is still refused by the kernel, just unrecorded here.
"""

from __future__ import annotations

import collections.abc
import json
import os
import re
import runpy
import sys
import threading
import time

_PATH_READ = {"os.listdir": (0,), "os.scandir": (0,), "os.chdir": (0,),
              "shutil.copyfile": (0,), "shutil.copytree": (0,)}
_PATH_WRITE = {"os.mkdir": (0,), "os.remove": (0,), "os.rmdir": (0,),
               "os.chmod": (0,), "os.utime": (0,), "os.rename": (0, 1),
               "shutil.rmtree": (0,), "shutil.move": (0, 1)}
_WRITE_BEHIND_READ = {"shutil.copyfile": (1,), "shutil.copytree": (1,)}
_NETWORK = frozenset({
    "socket.connect", "socket.bind", "socket.sendto", "socket.sendmsg",
    "socket.getaddrinfo", "socket.gethostbyname", "socket.gethostbyaddr",
    "socket.getnameinfo"})
_ADDRESSED = frozenset({"socket.connect", "socket.bind", "socket.sendto",
                        "socket.sendmsg"})
_PROCESS = frozenset({
    "subprocess.Popen", "os.system", "os.exec", "os.spawn", "os.posix_spawn",
    "os.fork", "os.forkpty", "os.startfile", "_winapi.CreateProcess",
    "_winapi.OpenProcess"})
_NATIVE = frozenset({"ctypes.dlopen", "ctypes.dlsym", "ctypes.call_function",
                     "os.symlink", "os.link", "_winapi.CreateJunction",
                     "_winapi.CreateNamedPipe"})
#: `_winapi.CreateFile(name, desired_access, ...)`: a write is any access that
#: is more than reading (GENERIC_WRITE, FILE_WRITE_DATA/APPEND, DELETE, ALL).
_WINAPI_CREATEFILE = "_winapi.CreateFile"
_WINAPI_WRITE_ACCESS = 0x40000000 | 0x10000000 | 0x00000002 | 0x00000004 | 0x00010000
_WRITE_FLAGS = (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)
_DEVNULL = frozenset({"nul", "/dev/null"})

_state = threading.local()
_config: dict = {}


def _fold(path: str) -> str:
    return os.path.normcase(os.path.realpath(path))


def _inside(path: str, root: str) -> bool:
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:
        return False


def _text(value) -> str | None:
    """A path argument as text, or None for a descriptor / no path."""
    if value is None or isinstance(value, int):
        return None
    try:
        return os.fsdecode(os.fspath(value))
    except TypeError:
        return None


def _allowed(path: str, write: bool) -> bool:
    if os.path.normcase(path) in _DEVNULL:
        return True
    # Both readings must be allowed. `realpath` of a path the process cannot
    # open (inside a container, anything outside its grants) falls back to a
    # string that can keep its `..` parts and so still look inside the root;
    # the lexical form closes that, the resolved form catches a link.
    lexical = os.path.abspath(path)
    return all(_permitted(form, write)
               for form in {os.path.normcase(lexical), _fold(lexical)})


def _permitted(real: str, write: bool) -> bool:
    if _inside(real, _config["root"]):
        # The guard and the record are readable (a traceback prints the
        # guard's own frames) but not writable: tampering is the risk.
        return not (write and any(_inside(real, fixed) for fixed in _config["readonly_dirs"]))
    return not write and any(_inside(real, prefix) for prefix in _config["read_roots"])


def _deny(kind: str, event: str, target) -> None:
    """Record the attempt, then refuse it. The target is a name or address,
    never a value: an environment read records the variable's NAME only."""
    entry = {"kind": kind, "event": event, "target": str(target)[:300],
             "at": time.time()}
    line = (json.dumps(entry) + "\n").encode("utf-8", "replace")
    try:
        os.write(_config["fd"], line)
    except OSError as error:
        sys.__stderr__.write(f"quarantine: denial not recorded ({error}): {kind} {event}\n")
    raise PermissionError(f"quarantine denied: {kind} ({event})")


def _is_write(mode, flags) -> bool:
    if isinstance(mode, str) and set(mode) & set("wax+"):
        return True
    return isinstance(flags, int) and bool(flags & _WRITE_FLAGS)


def _inspect(event: str, args: tuple) -> None:
    if event == "open":
        padded = tuple(args) + (None, None, None)
        target = _text(padded[0])
        if target is not None and not _allowed(target, _is_write(padded[1], padded[2])):
            _deny("file", event, target)
    elif event == _WINAPI_CREATEFILE:
        target = _text(args[0]) if args else None
        access = args[1] if len(args) > 1 and isinstance(args[1], int) else 0
        if target is not None and not _allowed(target, bool(access & _WINAPI_WRITE_ACCESS)):
            _deny("file", event, target)
    elif event in _PATH_READ or event in _PATH_WRITE:
        for index in _PATH_WRITE.get(event, ()):
            target = _text(args[index]) if index < len(args) else None
            if target is not None and not _allowed(target, True):
                _deny("file", event, target)
        for index in _PATH_READ.get(event, ()):
            target = _text(args[index]) if index < len(args) else None
            if target is not None and not _allowed(target, False):
                _deny("file", event, target)
        for index in _WRITE_BEHIND_READ.get(event, ()):
            target = _text(args[index]) if index < len(args) else None
            if target is not None and not _allowed(target, True):
                _deny("file", event, target)
    elif event in _NETWORK:
        position = 1 if event in _ADDRESSED else 0
        _deny("network", event, args[position] if len(args) > position else "")
    elif event in _PROCESS:
        _deny("process", event, args[0] if args else "")
    elif event in _NATIVE:
        _deny("native", event, args[0] if args else "")


def _hook(event: str, args: tuple) -> None:
    # The checks themselves resolve paths; whatever that triggers is not a
    # second attempt by the planted code.
    if getattr(_state, "busy", False):
        return
    _state.busy = True
    try:
        _inspect(event, args)
    finally:
        _state.busy = False


class _GuardedEnviron(collections.abc.MutableMapping):
    """`os.environ` minus anything secret-shaped; asking for one is a denial."""

    def __init__(self, inner, secret) -> None:
        self._inner = inner
        self._secret = secret

    def _check(self, key) -> None:
        if isinstance(key, str) and self._secret.search(key):
            _deny("env", "os.environ", key)

    def __getitem__(self, key):
        self._check(key)
        return self._inner[key]

    def __setitem__(self, key, value) -> None:
        self._inner[key] = value

    def __delitem__(self, key) -> None:
        del self._inner[key]

    def __contains__(self, key) -> bool:
        self._check(key)
        return key in self._inner

    def __iter__(self):
        return (key for key in list(self._inner) if not self._secret.search(key))

    def __len__(self) -> int:
        return sum(1 for _ in self)

    def copy(self) -> dict:
        return {key: self._inner[key] for key in self}


def _parse(argv: list[str]) -> tuple[dict, list[str]]:
    split = argv.index("--")
    options, rest = argv[:split], argv[split + 1:]
    parsed: dict = {"readonly_dirs": [], "module": False}
    flags = {"--root": "root", "--record": "record", "--repo": "repo",
             "--secret-pattern": "secret"}
    index = 0
    while index < len(options):
        name = options[index]
        if name == "--module":
            parsed["module"] = True
            index += 1
        elif name == "--readonly-dir":
            parsed["readonly_dirs"].append(options[index + 1])
            index += 2
        elif name in flags:
            parsed[flags[name]] = options[index + 1]
            index += 2
        else:
            raise SystemExit(f"quarantine_guard: unknown option {name}")
    return parsed, rest


def main(argv: list[str]) -> None:
    options, rest = _parse(argv)
    prefixes = {sys.prefix, sys.base_prefix, sys.exec_prefix, sys.base_exec_prefix}
    _config.update(
        root=_fold(options["root"]),
        repo=options["repo"],
        readonly_dirs=[_fold(path) for path in options["readonly_dirs"]],
        read_roots=[_fold(path) for path in prefixes],
        # Opened before the hook exists, so recording never needs an open().
        fd=os.open(options["record"], os.O_WRONLY | os.O_APPEND | os.O_CREAT))
    os.environ = _GuardedEnviron(os.environ, re.compile(options["secret"], re.IGNORECASE))
    sys.addaudithook(_hook)

    sys.path.insert(0, options["repo"])
    sys.argv = [rest[0], *rest[1:]]
    if options["module"]:
        runpy.run_module(rest[0], run_name="__main__", alter_sys=True)
    else:
        runpy.run_path(os.path.join(options["repo"], rest[0]), run_name="__main__")


if __name__ == "__main__":
    main(sys.argv[1:])
