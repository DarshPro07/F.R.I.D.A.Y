"""
Whether FRIDAY can really drive the desktop on this machine (PRD v7 CUA-005).

The same earned readiness the browser got (`friday.browser_readiness`): a
desktop backend is DESKTOP_READY only after a real, harmless action whose
post-state was observed by a different route than the one that acted. Having
the input layer importable is not readiness.

The steps, each named when it fails:

    installed   the native input layer exists and a display is attached
    signature   the binary the probe launches is where it should be and its
                Authenticode signature is valid (integrity)
    launch      the process starts; a launch the OS refuses for code integrity
                or policy is BINARY_BLOCKED, any other refusal LAUNCH_FAILED
    windows     enumeration finds a visible top-level window of that process
    action      the window is asked to close (WM_CLOSE) and a second
                enumeration shows it gone: the post-state

The target is fixed (Notepad from System32) and never caller-chosen, so the
probe is not a way to start an arbitrary program. Whatever the probe started
is terminated before it returns. The native layer is injected: tests pass a
fake, the machine run uses `NativeLayer`.

    python -m friday.desktop_readiness        # probe, print the state

Not covered yet: the accessibility-tree read and a background (no-focus)
action; they are the next steps of CUA-005.
"""
from __future__ import annotations

import os
import subprocess
import time
from dataclasses import asdict, dataclass

DESKTOP_READY = "DESKTOP_READY"
NOT_INSTALLED = "DESKTOP_NOT_INSTALLED"
NO_DISPLAY = "DESKTOP_NO_DISPLAY"
BINARY_MISSING = "DESKTOP_BINARY_MISSING"
SIGNATURE_INVALID = "DESKTOP_SIGNATURE_INVALID"
BINARY_BLOCKED = "DESKTOP_BINARY_BLOCKED"
LAUNCH_FAILED = "DESKTOP_LAUNCH_FAILED"
NO_WINDOW = "DESKTOP_NO_WINDOW"
ENUMERATION_FAILED = "DESKTOP_ENUMERATION_FAILED"
ACTION_UNOBSERVED = "DESKTOP_ACTION_UNOBSERVED"

#: The harmless program the probe starts, and finds under System32.
TARGET_IMAGE = "notepad.exe"

#: Windows error codes for a launch the OS refused on trust grounds:
#: ERROR_VIRUS_INFECTED, ERROR_INVALID_IMAGE_HASH, ERROR_ACCESS_DISABLED_BY_POLICY
#: and the Code Integrity / AppLocker "blocked" code. A plain access-denied (5)
#: is deliberately not here: it says nothing about integrity.
BLOCKED_WINERRORS = frozenset({225, 577, 1260, 4551})

_POLL_SECONDS = 0.1
_SIGNATURE_TIMEOUT_S = 30


@dataclass(frozen=True)
class Readiness:
    state: str
    detail: str
    #: The steps that passed, in order.
    steps: tuple[str, ...] = ()
    seconds: float = 0.0


class NativeLayer:
    """The real Windows layer. Everything the probe needs from the machine.

    Ownership is strict, because the probe runs on a machine a person is using:
    a process is the probe's own only if it is the launched pid or one of its
    descendants, and a window is the probe's own only if it belongs to such a
    process, or it is a new Notepad window (absent from the snapshot taken
    before the launch; Windows 11 hands Notepad off to a packaged process that
    is not a descendant). Only the first group is ever killed; a handed-off
    window is at most asked to close. A Notepad window or process that was
    there before, or that is not Notepad, is never touched.
    """

    def __init__(self, *, windows_api=None, psutil_api=None, popen=None) -> None:
        self._windows_api = windows_api
        self._psutil_api = psutil_api
        self._popen = popen or subprocess.Popen
        self._before: set[int] = set()
        self._ours: set[int] = set()

    def _win(self):
        if self._windows_api is None:
            from friday.platform import windows
            self._windows_api = windows
        return self._windows_api

    def _ps(self):
        if self._psutil_api is None:
            import psutil
            self._psutil_api = psutil
        return self._psutil_api

    def native_available(self) -> bool:
        return self._win().AVAILABLE

    def screen_size(self) -> tuple[int, int]:
        return self._win().screen_size()

    def find(self, image: str) -> str | None:
        root = os.environ.get("SystemRoot") or r"C:\Windows"
        path = os.path.join(root, "System32", image)
        return path if os.path.isfile(path) else None

    def signature(self, path: str) -> tuple[bool, str]:
        # The path travels in the environment, never in the script text.
        script = "(Get-AuthenticodeSignature -LiteralPath $env:FRIDAY_PROBE_PATH).Status"
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=_SIGNATURE_TIMEOUT_S, check=False,
            env={**os.environ, "FRIDAY_PROBE_PATH": path})
        status = completed.stdout.strip()
        if completed.returncode != 0 or not status:
            return False, (completed.stderr.strip() or "no signature status")[:200]
        return status == "Valid", status

    def launch(self, path: str) -> int:
        # Snapshot first: whatever is already on screen is not ours.
        self._before = {hwnd for hwnd, _ in self._win().top_level_windows()}
        process = self._popen(
            [path], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL)
        self._ours = {process.pid}
        return process.pid

    def _our_pids(self, pid: int) -> set[int]:
        """The launched process and its descendants, nothing else."""
        ps = self._ps()
        ours = set(self._ours) | {pid}
        try:
            ours |= {child.pid for child in ps.Process(pid).children(recursive=True)}
        except ps.Error:                 # gone, or not ours to read: keep what we know
            pass
        self._ours = ours
        return ours

    def _is_notepad(self, pid: int) -> bool:
        ps = self._ps()
        try:
            return str(ps.Process(pid).name()).lower() == TARGET_IMAGE
        except ps.Error:
            return False

    def windows(self, pid: int) -> list[int]:
        """The probe's windows, those of its own processes first."""
        ours = self._our_pids(pid)
        own: list[int] = []
        handed_off: list[int] = []
        for hwnd, owner in self._win().top_level_windows():
            if owner in ours:
                own.append(hwnd)
            elif hwnd not in self._before and self._is_notepad(owner):
                handed_off.append(hwnd)
        return own + handed_off

    def close(self, hwnd: int) -> None:
        native = self._win()
        native.PostMessageW(hwnd, native.WM_CLOSE, 0, 0)

    def terminate(self, pid: int) -> None:
        ps = self._ps()
        for target in self._our_pids(pid):
            try:
                ps.Process(target).kill()
            except ps.Error:
                continue                      # already gone is the goal


def _failure(state: str, detail: str, steps: list[str], started: float,
             clock) -> Readiness:
    return Readiness(state, detail, tuple(steps), round(clock() - started, 2))


def probe(layer=None, *, timeout_ms: int = 10_000, clock=time.monotonic,
          sleep=time.sleep) -> Readiness:
    """Run a real harmless action on the desktop and say what stood in the way."""
    layer = layer or NativeLayer()
    started = clock()
    steps: list[str] = []

    def fail(state: str, detail: str) -> Readiness:
        return _failure(state, detail, steps, started, clock)

    if not layer.native_available():
        return fail(NOT_INSTALLED, "the native input layer is Windows-only; "
                                   "this host has none")
    try:
        width, height = layer.screen_size()
    except Exception as exc:  # noqa: BLE001 - reported, typed
        return fail(NO_DISPLAY, f"the screen cannot be read: {type(exc).__name__}: {exc}")
    if width <= 0 or height <= 0:
        return fail(NO_DISPLAY, f"no display is attached ({width}x{height})")
    steps.append("installed")

    path = layer.find(TARGET_IMAGE)
    if not path:
        return fail(BINARY_MISSING, f"{TARGET_IMAGE} is not under System32")
    try:
        valid, status = layer.signature(path)
    except Exception as exc:  # noqa: BLE001 - reported, typed
        return fail(SIGNATURE_INVALID,
                    f"the signature of {path} could not be checked: "
                    f"{type(exc).__name__}: {exc}")
    if not valid:
        return fail(SIGNATURE_INVALID, f"{path} signature is {status}, not Valid")
    steps.append("signature")

    try:
        pid = layer.launch(path)
    except OSError as exc:
        code = getattr(exc, "winerror", None)
        if code in BLOCKED_WINERRORS:
            return fail(BINARY_BLOCKED, f"the OS refused to start {path} "
                                        f"(code integrity or policy, error {code}): {exc}")
        return fail(LAUNCH_FAILED, f"{type(exc).__name__}: {exc}")
    steps.append("launch")

    deadline = clock() + timeout_ms / 1000
    try:
        shown = _wait(lambda: layer.windows(pid), deadline, clock, sleep)
        if not shown:
            return fail(NO_WINDOW, f"{TARGET_IMAGE} started but no visible window "
                                   f"appeared in {timeout_ms} ms")
        steps.append("windows")

        layer.close(shown[0])
        remaining = _wait(lambda: not any(h == shown[0] for h in layer.windows(pid)),
                          deadline + timeout_ms / 1000, clock, sleep)
        if not remaining:
            return fail(ACTION_UNOBSERVED, f"window {shown[0]} was asked to close "
                                           f"and was still there after {timeout_ms} ms")
        steps.append("action")
    except OSError as exc:
        return fail(ENUMERATION_FAILED,
                    f"windows could not be enumerated: {type(exc).__name__}: {exc}")
    finally:
        layer.terminate(pid)

    return Readiness(DESKTOP_READY,
                     f"started {TARGET_IMAGE}, saw its window, closed it and saw it go",
                     tuple(steps), round(clock() - started, 2))


def _wait(check, deadline: float, clock, sleep):
    """Poll `check` until it returns something truthy or `deadline` passes."""
    while True:
        value = check()
        if value:
            return value
        if clock() >= deadline:
            return value
        sleep(_POLL_SECONDS)


def main() -> int:
    import json

    readiness = probe()
    print(json.dumps(asdict(readiness), indent=1))
    return 0 if readiness.state == DESKTOP_READY else 1


if __name__ == "__main__":
    raise SystemExit(main())
