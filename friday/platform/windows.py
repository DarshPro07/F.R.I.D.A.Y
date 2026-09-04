"""
Every Win32 call Friday makes, declared once and typed properly.

`ctypes` assumes a C `int` for anything not declared, and on 64-bit Windows a
pointer-sized handle does not fit in one. The failure is silent and total: an
`HWND` gets truncated, `GetWindowThreadProcessId` reports the wrong owner or
none, and `windows_of()` returns an empty list for a window plainly on the
screen. That is exactly what happened, and it happened because the prototype
was written inline at the call site where nobody was thinking about widths.

So the prototypes live here, once, and `tests/test_native_bindings.py` fails
if any registered function is missing `argtypes` or `restype`. A privileged
Win32 API called through an untyped ctypes function object is a bug waiting
for a machine with a different pointer size, which on Windows is every
machine.

Nothing here decides anything. There is no policy, no verification and no
Friday concept in this file - it is the thinnest possible correct binding, so
that the modules above it can be about what they mean rather than about
marshalling.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from typing import NamedTuple

#: Every function bound here, for the test that walks them. A binding that is
#: not in this list is not covered by the check, so `_bind` adds to it rather
#: than leaving it to whoever wrote the call.
BOUND: dict[str, object] = {}

AVAILABLE = sys.platform == "win32"

if AVAILABLE:
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _powrprof = ctypes.WinDLL("powrprof", use_last_error=True)
    _advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
else:                                                   # pragma: no cover
    _user32 = _kernel32 = _powrprof = _advapi32 = None


def _bind(library, name: str, argtypes, restype):
    """Declare one function, record it, and hand it back."""
    if library is None:                                 # pragma: no cover
        return None
    function = getattr(library, name)
    function.argtypes = list(argtypes)
    function.restype = restype
    BOUND[name] = function
    return function


# --- types the Win32 headers use that wintypes does not spell out ----------

LRESULT = ctypes.c_ssize_t
ULONG_PTR = ctypes.c_size_t

#: The callback EnumWindows takes. Declared once because the signature is easy
#: to get subtly wrong and the consequence is a silent empty result.
ENUM_WINDOWS_PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND,
                                       wintypes.LPARAM)

# --- messages ---------------------------------------------------------------

#: What a window receives when its close button is pressed. The application
#: decides what to do with it, including putting up "save changes?" and
#: declining to go. This is the difference between closing and killing.
WM_CLOSE = 0x0010

#: SendMessageTimeout flags, for when a bounded wait is wanted instead of a
#: post-and-observe.
SMTO_ABORTIFHUNG = 0x0002
SMTO_ERRORONEXIT = 0x0020

# --- process access rights --------------------------------------------------

PROCESS_TERMINATE = 0x0001
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SYNCHRONIZE = 0x00100000

WAIT_OBJECT_0 = 0x00000000
WAIT_TIMEOUT = 0x00000102

# --- ExitWindowsEx -----------------------------------------------------------
#
# Deliberately no default force flag. Microsoft's own documentation warns that
# EWX_FORCE can cause data loss, because applications do not get the chance to
# object. Friday asks the normal way first and treats "an application is
# blocking it" as information rather than an obstacle to bulldoze.

EWX_LOGOFF = 0x00000000
EWX_SHUTDOWN = 0x00000001
EWX_REBOOT = 0x00000002
EWX_FORCE = 0x00000004
EWX_FORCEIFHUNG = 0x00000010

#: Why the machine is being shut down, for the event log. "Other planned" is
#: honest: a person asked.
SHTDN_REASON_MAJOR_OTHER = 0x00000000
SHTDN_REASON_MINOR_OTHER = 0x00000000
SHTDN_REASON_FLAG_PLANNED = 0x80000000


# --- windows ----------------------------------------------------------------

EnumWindows = _bind(
    _user32, "EnumWindows", (ENUM_WINDOWS_PROC, wintypes.LPARAM), wintypes.BOOL)

#: Returns the creating thread, and writes the creating PROCESS id through the
#: pointer. The out-parameter is why the pointer width matters here.
GetWindowThreadProcessId = _bind(
    _user32, "GetWindowThreadProcessId",
    (wintypes.HWND, ctypes.POINTER(wintypes.DWORD)), wintypes.DWORD)

IsWindow = _bind(_user32, "IsWindow", (wintypes.HWND,), wintypes.BOOL)
IsWindowVisible = _bind(_user32, "IsWindowVisible", (wintypes.HWND,),
                        wintypes.BOOL)
GetWindowTextW = _bind(
    _user32, "GetWindowTextW",
    (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int), ctypes.c_int)

GetForegroundWindow = _bind(_user32, "GetForegroundWindow", (), wintypes.HWND)
SetForegroundWindow = _bind(_user32, "SetForegroundWindow", (wintypes.HWND,),
                            wintypes.BOOL)

PostMessageW = _bind(
    _user32, "PostMessageW",
    (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM),
    wintypes.BOOL)

SendMessageTimeoutW = _bind(
    _user32, "SendMessageTimeoutW",
    (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
     wintypes.UINT, wintypes.UINT, ctypes.POINTER(ULONG_PTR)), LRESULT)

EnumDisplayMonitors = _bind(
    _user32, "EnumDisplayMonitors",
    (wintypes.HDC, ctypes.c_void_p, ctypes.c_void_p, wintypes.LPARAM),
    wintypes.BOOL)

# --- session and power ------------------------------------------------------

LockWorkStation = _bind(_user32, "LockWorkStation", (), wintypes.BOOL)

ExitWindowsEx = _bind(_user32, "ExitWindowsEx",
                      (wintypes.UINT, wintypes.DWORD), wintypes.BOOL)

#: Sleep or hibernate. bHibernate chooses; bForce is deliberately never set
#: by Friday, for the same reason EWX_FORCE is not.
SetSuspendState = _bind(
    _powrprof, "SetSuspendState",
    (wintypes.BOOL, wintypes.BOOL, wintypes.BOOL), wintypes.BOOL)

#: Shutdown and restart, the version that can be called back.
#:
#: `ExitWindowsEx` above cannot be delayed and cannot be aborted, so it cannot
#: give anybody the thirty seconds in which to say "no, wait". This one takes
#: a timeout and pairs with `AbortSystemShutdownW`, which is the whole reason
#: it is here: a confirmation catches a misrouted request, and only a callback
#: window catches a misheard yes.
#:
#: Returns a Win32 error code (0 == ERROR_SUCCESS), not a BOOL - so the usual
#: `if not result` reads exactly backwards, and `restype` says DWORD to make
#: that obvious at the call site.
InitiateShutdownW = _bind(
    _advapi32, "InitiateShutdownW",
    (wintypes.LPWSTR,     # lpMachineName - NULL for this machine
     wintypes.LPWSTR,     # lpMessage
     wintypes.DWORD,      # dwGracePeriod, seconds
     wintypes.DWORD,      # dwShutdownFlags
     wintypes.DWORD),     # dwReason
    wintypes.DWORD)

AbortSystemShutdownW = _bind(
    _advapi32, "AbortSystemShutdownW", (wintypes.LPWSTR,), wintypes.BOOL)

#: `InitiateShutdownW` flags. The force pair is defined so the code can say
#: out loud that it is not setting them.
SHUTDOWN_GRACE_OVERRIDE = 0x00000020
SHUTDOWN_INSTALL_UPDATES = 0x00000040
SHUTDOWN_RESTART = 0x00000004
SHUTDOWN_POWEROFF = 0x00000008
SHUTDOWN_NOREBOOT = 0x00000010
SHUTDOWN_FORCE_OTHERS = 0x00000001
SHUTDOWN_FORCE_SELF = 0x00000002

ERROR_SUCCESS = 0
ERROR_ACCESS_DENIED = 5
ERROR_NOT_ALL_ASSIGNED = 1300
#: Returned by AbortSystemShutdownW when nothing is counting down.
ERROR_NO_SHUTDOWN_IN_PROGRESS = 1116

# --- privilege --------------------------------------------------------------
#
# Measured on this machine: SeShutdownPrivilege is present in Friday's token
# and DISABLED, which is the Windows default. Every shutdown, restart and
# hibernate call fails until it is enabled - and would look like the machine
# refusing rather than like Friday never having asked.


class LUID(ctypes.Structure):
    _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]


class LUID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Luid", LUID), ("Attributes", wintypes.DWORD)]


class TOKEN_PRIVILEGES(ctypes.Structure):
    _fields_ = [("PrivilegeCount", wintypes.DWORD),
                ("Privileges", LUID_AND_ATTRIBUTES * 1)]


TOKEN_QUERY = 0x0008
TOKEN_ADJUST_PRIVILEGES = 0x0020
SE_PRIVILEGE_ENABLED = 0x00000002
SE_SHUTDOWN_NAME = "SeShutdownPrivilege"

OpenProcessToken = _bind(
    _advapi32, "OpenProcessToken",
    (wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)),
    wintypes.BOOL)

LookupPrivilegeValueW = _bind(
    _advapi32, "LookupPrivilegeValueW",
    (wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.POINTER(LUID)),
    wintypes.BOOL)

#: Returns non-zero **even when it enabled nothing**, setting
#: ERROR_NOT_ALL_ASSIGNED. Callers that trust the return value get a silent
#: no-op, which is why `enable_shutdown_privilege` reads GetLastError.
AdjustTokenPrivileges = _bind(
    _advapi32, "AdjustTokenPrivileges",
    (wintypes.HANDLE, wintypes.BOOL, ctypes.POINTER(TOKEN_PRIVILEGES),
     wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p),
    wintypes.BOOL)

GetCurrentProcess = _bind(_kernel32, "GetCurrentProcess", (), wintypes.HANDLE)

# --- what this machine can actually do --------------------------------------


class BATTERY_REPORTING_SCALE(ctypes.Structure):
    #: Two ULONGs each, so 24 bytes for the array of three. Declaring it as 12
    #: made `CallNtPowerInformation` return STATUS_BUFFER_TOO_SMALL and leave
    #: the buffer zeroed - which reads exactly like a machine that supports no
    #: power features at all. The status is the only thing that tells them
    #: apart.
    _fields_ = [("Granularity", wintypes.ULONG), ("Capacity", wintypes.ULONG)]


_BYTE = ctypes.c_ubyte


class SYSTEM_POWER_CAPABILITIES(ctypes.Structure):
    _fields_ = [
        ("PowerButtonPresent", _BYTE), ("SleepButtonPresent", _BYTE),
        ("LidPresent", _BYTE), ("SystemS1", _BYTE), ("SystemS2", _BYTE),
        ("SystemS3", _BYTE), ("SystemS4", _BYTE), ("SystemS5", _BYTE),
        ("HiberFilePresent", _BYTE), ("FullWake", _BYTE),
        ("VideoDimPresent", _BYTE), ("ApmPresent", _BYTE),
        ("UpsPresent", _BYTE), ("ThermalControl", _BYTE),
        ("ProcessorThrottle", _BYTE), ("ProcessorMinThrottle", _BYTE),
        ("ProcessorMaxThrottle", _BYTE), ("FastSystemS4", _BYTE),
        ("Hiberboot", _BYTE), ("WakeAlarmPresent", _BYTE), ("AoAc", _BYTE),
        ("DiskSpinDown", _BYTE), ("HiberFileType", _BYTE),
        ("AoAcConnectivitySupported", _BYTE), ("spare3", _BYTE * 6),
        ("SystemBatteriesPresent", _BYTE), ("BatteriesAreShortTerm", _BYTE),
        ("BatteryScale", BATTERY_REPORTING_SCALE * 3),
        ("AcOnLineWake", ctypes.c_int), ("SoftLidWake", ctypes.c_int),
        ("RtcWake", ctypes.c_int), ("MinDeviceWakeState", ctypes.c_int),
        ("DefaultLowLatencyWake", ctypes.c_int),
    ]


SYSTEM_POWER_CAPABILITIES_INFO = 4
STATUS_SUCCESS = 0

CallNtPowerInformation = _bind(
    _powrprof, "CallNtPowerInformation",
    (ctypes.c_int, ctypes.c_void_p, wintypes.ULONG, ctypes.c_void_p,
     wintypes.ULONG),
    wintypes.LONG)

# --- processes --------------------------------------------------------------

OpenProcess = _bind(
    _kernel32, "OpenProcess",
    (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD), wintypes.HANDLE)

TerminateProcess = _bind(
    _kernel32, "TerminateProcess", (wintypes.HANDLE, wintypes.UINT),
    wintypes.BOOL)

GetProcessTimes = _bind(
    _kernel32, "GetProcessTimes",
    (wintypes.HANDLE, ctypes.POINTER(wintypes.FILETIME),
     ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME),
     ctypes.POINTER(wintypes.FILETIME)), wintypes.BOOL)

WaitForSingleObject = _bind(
    _kernel32, "WaitForSingleObject", (wintypes.HANDLE, wintypes.DWORD),
    wintypes.DWORD)

GetExitCodeProcess = _bind(
    _kernel32, "GetExitCodeProcess",
    (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)), wintypes.BOOL)

CloseHandle = _bind(_kernel32, "CloseHandle", (wintypes.HANDLE,),
                    wintypes.BOOL)


# ---------------------------------------------------------------------------
# The two helpers worth having here rather than at every call site
# ---------------------------------------------------------------------------


def top_level_windows() -> list[tuple[int, int]]:
    """
    Every visible top-level window, as (hwnd, owning pid).

    `GetWindowThreadProcessId` is the authority on which process created a
    window - not its title, which is what the Notepad incident mistook for
    ownership.
    """
    if not AVAILABLE:                                   # pragma: no cover
        return []
    found: list[tuple[int, int]] = []

    def visit(hwnd, _lparam):
        owner = wintypes.DWORD()
        GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value and IsWindowVisible(hwnd):
            found.append((hwnd, owner.value))
        return True

    EnumWindows(ENUM_WINDOWS_PROC(visit), 0)
    return found


def window_title(hwnd: int, limit: int = 256) -> str:
    if not AVAILABLE:                                   # pragma: no cover
        return ""
    buffer = ctypes.create_unicode_buffer(limit)
    GetWindowTextW(hwnd, buffer, limit)
    return buffer.value


# ---------------------------------------------------------------------------
# Power: asking properly, and knowing what this machine can do
# ---------------------------------------------------------------------------


class PowerCapabilities(NamedTuple):
    """What the machine says about itself, read once and not cached."""

    sleep: bool
    hibernate: bool
    modern_standby: bool
    hibernate_file: bool


def enable_shutdown_privilege(enabled: bool = True) -> bool:
    """
    Turn SeShutdownPrivilege on (or back off) for this process, and say
    whether it worked.

    The privilege is in Friday's token already - it is an ordinary interactive
    user token - but disabled, which is the Windows default for all but a
    handful. Until it is enabled, every shutdown, restart and hibernate call
    fails, and the failure arrives as a plain zero that reads like the machine
    refusing.

    `AdjustTokenPrivileges` returns non-zero **even when it enabled nothing**,
    setting ERROR_NOT_ALL_ASSIGNED. So the return value is not the answer;
    GetLastError is. This is the same shape as TerminateProcess returning true
    without the process being gone, and it is checked for the same reason.

    `enabled=False` puts it back. Callers turn it on immediately before the
    power call and off immediately after, so the window in which this process
    can shut the machine down is the width of one call rather than the life of
    the process - which also keeps a test suite from running a thousand
    unrelated tests with the privilege live.
    """
    if not AVAILABLE:                                   # pragma: no cover
        return False

    token = wintypes.HANDLE()
    if not OpenProcessToken(GetCurrentProcess(),
                            TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY,
                            ctypes.byref(token)):
        return False
    try:
        luid = LUID()
        if not LookupPrivilegeValueW(None, SE_SHUTDOWN_NAME,
                                     ctypes.byref(luid)):
            return False

        privileges = TOKEN_PRIVILEGES()
        privileges.PrivilegeCount = 1
        privileges.Privileges[0].Luid = luid
        privileges.Privileges[0].Attributes = (
            SE_PRIVILEGE_ENABLED if enabled else 0)

        ctypes.set_last_error(0)
        AdjustTokenPrivileges(token, False, ctypes.byref(privileges), 0,
                              None, None)
        return ctypes.get_last_error() == ERROR_SUCCESS
    finally:
        CloseHandle(token)


def power_capabilities() -> PowerCapabilities:
    """
    Whether this machine can sleep and hibernate, from the authoritative API.

    Deliberately not `IsPwrSuspendAllowed`. That answers about S1-S3, and on a
    modern-standby machine - S0 low power idle, which is most current laptops -
    it returns False while the machine sleeps every night. Measured here: it
    said False with AoAc True. Believing it would have Friday tell somebody
    their own hardware cannot do something it does nightly, which is the
    csrss.exe mistake in a different costume: reporting as absent something
    that is present.

    Raises rather than returning a tidy all-false answer when the call fails.
    A wrong-sized struct returns STATUS_BUFFER_TOO_SMALL and leaves the buffer
    zeroed, and "this machine supports nothing" is exactly what that looks
    like.
    """
    if not AVAILABLE:                                   # pragma: no cover
        raise OSError("power capabilities are a Windows notion")

    caps = SYSTEM_POWER_CAPABILITIES()
    status = CallNtPowerInformation(SYSTEM_POWER_CAPABILITIES_INFO, None, 0,
                                    ctypes.byref(caps), ctypes.sizeof(caps))
    if status != STATUS_SUCCESS:
        raise OSError(
            f"CallNtPowerInformation returned 0x{status & 0xFFFFFFFF:08X}; "
            f"the capability flags would be an uninitialised buffer rather "
            f"than an answer")

    return PowerCapabilities(
        sleep=bool(caps.AoAc or caps.SystemS1 or caps.SystemS2
                   or caps.SystemS3),
        hibernate=bool(caps.SystemS4 and caps.HiberFilePresent),
        modern_standby=bool(caps.AoAc),
        hibernate_file=bool(caps.HiberFilePresent),
    )


# --- synthetic input -------------------------------------------------------
#
# Moving the pointer and pressing keys, for the takeover capability. `SendInput`
# is the only supported way to do this: it goes through the same queue as real
# hardware, so applications cannot tell the difference and none of the
# focus-stealing tricks of `PostMessage` are needed.
#
# Nothing in this section decides *whether* to act. That judgement lives in
# `friday.toolsets.desktop`, behind a CONFIRM gate. These are the hands, not
# the will.

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000

KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004

SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79

#: Virtual-key codes for the keys a task actually needs to press by name.
VK = {
    "enter": 0x0D, "return": 0x0D, "tab": 0x09, "esc": 0x1B, "escape": 0x1B,
    "space": 0x20, "backspace": 0x08, "delete": 0x2E, "home": 0x24, "end": 0x23,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "pageup": 0x21, "pagedown": 0x22,
    "ctrl": 0x11, "control": 0x11, "alt": 0x12, "shift": 0x10, "win": 0x5B,
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73, "f5": 0x74, "f6": 0x75,
    "f7": 0x76, "f8": 0x77, "f9": 0x78, "f10": 0x79, "f11": 0x7A, "f12": 0x7B,
}


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD),
                ("wParamH", wintypes.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


SendInput = _bind(_user32, "SendInput",
                  (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int),
                  wintypes.UINT)
GetSystemMetrics = _bind(_user32, "GetSystemMetrics", (ctypes.c_int,), ctypes.c_int)
GetCursorPos = _bind(_user32, "GetCursorPos", (ctypes.POINTER(wintypes.POINT),),
                     wintypes.BOOL)


def make_dpi_aware() -> None:
    """Make one pixel here mean one pixel on the screen.

    Without this, `GetSystemMetrics` answers in logical pixels on a scaled
    display while the screenshot the coordinates came from is in physical ones,
    and every click lands off by the scale factor. Measured on this machine at
    125%: a point asked for at x=1400 was acted on at x=1750.
    """
    if not AVAILABLE:                                   # pragma: no cover
        return
    for attempt in (
        lambda: _user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)),
        lambda: ctypes.WinDLL("shcore").SetProcessDpiAwareness(2),
        lambda: _user32.SetProcessDPIAware(),
    ):
        try:
            if attempt():
                return
        except Exception:                               # noqa: BLE001
            continue


def _require() -> None:
    if not AVAILABLE or SendInput is None:              # pragma: no cover
        raise OSError("synthetic input is a Windows notion")


def virtual_screen() -> tuple[int, int, int, int]:
    """(left, top, width, height) of the whole virtual desktop, in pixels."""
    _require()
    make_dpi_aware()
    return (GetSystemMetrics(SM_XVIRTUALSCREEN),
            GetSystemMetrics(SM_YVIRTUALSCREEN),
            GetSystemMetrics(SM_CXVIRTUALSCREEN),
            GetSystemMetrics(SM_CYVIRTUALSCREEN))


def screen_size() -> tuple[int, int]:
    left, top, width, height = virtual_screen()
    return width, height


def cursor_pos() -> tuple[int, int]:
    _require()
    point = wintypes.POINT()
    if not GetCursorPos(ctypes.byref(point)):
        raise OSError("could not read the cursor position")
    return int(point.x), int(point.y)


def _send(*events: INPUT) -> None:
    array = (INPUT * len(events))(*events)
    sent = SendInput(len(events), array, ctypes.sizeof(INPUT))
    if sent != len(events):
        raise OSError(f"SendInput accepted {sent} of {len(events)} events "
                      f"(error {ctypes.get_last_error()})")


def send_mouse_move_abs(x: int, y: int) -> None:
    """Move the pointer to an absolute pixel on the virtual desktop."""
    _require()
    left, top, width, height = virtual_screen()
    if width <= 0 or height <= 0:                       # pragma: no cover
        raise OSError("the virtual desktop has no size")
    # SendInput's absolute space is 0..65535 across the virtual desktop, not
    # pixels - so the pixel is converted here, once, against the real metrics.
    nx = int(round((x - left) * 65535 / max(1, width - 1)))
    ny = int(round((y - top) * 65535 / max(1, height - 1)))
    nx = max(0, min(65535, nx))
    ny = max(0, min(65535, ny))
    event = INPUT(type=INPUT_MOUSE)
    event.mi = MOUSEINPUT(
        dx=nx, dy=ny, mouseData=0,
        dwFlags=MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK,
        time=0, dwExtraInfo=0)
    _send(event)


def send_mouse_click(button: str = "left", double: bool = False) -> None:
    """Press and release where the pointer already is."""
    _require()
    down, up = ((MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP)
                if button == "right"
                else (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP))
    events = []
    for _ in range(2 if double else 1):
        for flag in (down, up):
            event = INPUT(type=INPUT_MOUSE)
            event.mi = MOUSEINPUT(dx=0, dy=0, mouseData=0, dwFlags=flag,
                                  time=0, dwExtraInfo=0)
            events.append(event)
    _send(*events)


def send_text(text: str) -> None:
    """Type a string. Unicode, so it does not depend on the keyboard layout."""
    _require()
    events = []
    for character in text:
        for flags in (KEYEVENTF_UNICODE,
                      KEYEVENTF_UNICODE | KEYEVENTF_KEYUP):
            event = INPUT(type=INPUT_KEYBOARD)
            event.ki = KEYBDINPUT(wVk=0, wScan=ord(character), dwFlags=flags,
                                  time=0, dwExtraInfo=0)
            events.append(event)
    if events:
        _send(*events)


def send_key(name: str) -> None:
    """Press a named key, optionally with modifiers: "enter", "ctrl+s"."""
    _require()
    parts = [p.strip().lower() for p in str(name).split("+") if p.strip()]
    if not parts:
        raise ValueError("no key named")
    unknown = [p for p in parts if p not in VK]
    if unknown:
        raise ValueError(f"unknown key {unknown[0]!r}")
    codes = [VK[p] for p in parts]
    events = []
    for code in codes:                                   # modifiers down first
        event = INPUT(type=INPUT_KEYBOARD)
        event.ki = KEYBDINPUT(wVk=code, wScan=0, dwFlags=0, time=0, dwExtraInfo=0)
        events.append(event)
    for code in reversed(codes):                         # and up in reverse
        event = INPUT(type=INPUT_KEYBOARD)
        event.ki = KEYBDINPUT(wVk=code, wScan=0, dwFlags=KEYEVENTF_KEYUP,
                              time=0, dwExtraInfo=0)
        events.append(event)
    _send(*events)
