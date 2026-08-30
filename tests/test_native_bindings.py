"""
Every Win32 call declared, and declared correctly.

`ctypes` assumes a C `int` for anything undeclared, and on 64-bit Windows a
pointer-sized handle does not fit in one. The failure is silent: an HWND is
truncated, `GetWindowThreadProcessId` reports the wrong owner, and a function
that enumerates windows returns nothing for a window plainly on the screen.
That happened, and it happened because the prototype was written inline at the
call site where nobody was thinking about widths.

This walks the bindings instead of trusting that whoever adds the next one
remembers.
"""

from __future__ import annotations

import ctypes
import sys

import pytest

from friday.platform import windows as native

pytestmark = pytest.mark.skipif(sys.platform != "win32",
                                reason="Win32 bindings")


def test_something_is_actually_bound():
    assert native.BOUND, "no Win32 functions were registered"


@pytest.mark.parametrize("name", sorted(native.BOUND))
def test_every_binding_declares_its_prototype(name):
    """
    The whole point. A registered function without argtypes marshals its
    arguments as C ints, which is correct for exactly none of the handle types
    Friday passes.
    """
    function = native.BOUND[name]
    assert function.argtypes is not None, f"{name} has no argtypes"
    assert function.restype is not None, f"{name} has no restype"


#: function -> which argument positions are handles. Written out rather than
#: inferred, because the first version of this test asserted "no argument is
#: c_int" and failed on `SetSuspendState(BOOL, BOOL, BOOL)` - wintypes.BOOL
#: *is* c_int on Windows, and correctly so. A guard that fires on correct code
#: gets deleted, so it has to be precise about what it is guarding.
HANDLE_POSITIONS = {
    "EnumWindows": (),
    "GetWindowThreadProcessId": (0,),
    "IsWindow": (0,), "IsWindowVisible": (0,), "GetWindowTextW": (0,),
    "SetForegroundWindow": (0,),
    "PostMessageW": (0,), "SendMessageTimeoutW": (0,),
    "TerminateProcess": (0,), "GetProcessTimes": (0,),
    "WaitForSingleObject": (0,), "GetExitCodeProcess": (0,),
    "CloseHandle": (0,),
    # A token is a handle like any other, and adjusting privileges through a
    # truncated one fails in the direction that looks like "the machine said
    # no" rather than "we passed half a handle".
    "OpenProcessToken": (0,), "AdjustTokenPrivileges": (0,),
}


@pytest.mark.parametrize("name,positions", sorted(HANDLE_POSITIONS.items()))
def test_every_handle_argument_is_pointer_sized(name, positions):
    """
    The truncation bug, written down. c_int is 32 bits on Windows whatever the
    pointer size, so an HWND or HANDLE declared as one loses the top half of
    every handle on a 64-bit machine - which is every machine.
    """
    function = native.BOUND[name]
    for position in positions:
        argtype = function.argtypes[position]
        assert ctypes.sizeof(argtype) == ctypes.sizeof(ctypes.c_void_p), (
            f"{name} argument {position} is {argtype.__name__}, "
            f"{ctypes.sizeof(argtype)} bytes - a handle needs "
            f"{ctypes.sizeof(ctypes.c_void_p)}")


def test_every_handle_returning_function_returns_pointer_sized():
    for name in ("OpenProcess", "GetForegroundWindow", "GetCurrentProcess"):
        restype = native.BOUND[name].restype
        assert ctypes.sizeof(restype) == ctypes.sizeof(ctypes.c_void_p),             f"{name} returns {restype.__name__}, which cannot hold a handle"


def test_the_functions_that_matter_are_all_here():
    """
    Named individually, because a missing binding is not a test failure
    anywhere else - it is an inline ctypes call somewhere with no prototype.
    """
    for name in ("EnumWindows", "GetWindowThreadProcessId", "PostMessageW",
                 "OpenProcess", "TerminateProcess", "WaitForSingleObject",
                 "CloseHandle", "GetProcessTimes", "ExitWindowsEx",
                 "LockWorkStation", "SetSuspendState", "GetForegroundWindow",
                 "InitiateShutdownW", "AbortSystemShutdownW",
                 "OpenProcessToken", "LookupPrivilegeValueW",
                 "AdjustTokenPrivileges", "CallNtPowerInformation",
                 "GetCurrentProcess"):
        assert name in native.BOUND, f"{name} is not declared"


def test_initiate_shutdown_returns_an_error_code_not_a_boolean():
    """
    The trap in this particular API. `InitiateShutdownW` returns a Win32 error
    code where 0 means success, so `if not result:` reads exactly backwards
    from every neighbouring call - and the backwards reading is the one that
    fires the shutdown and reports failure.
    """
    assert native.BOUND["InitiateShutdownW"].restype is not ctypes.c_bool
    assert native.ERROR_SUCCESS == 0


def test_the_power_structs_are_the_size_windows_expects():
    """
    A struct declared too small makes CallNtPowerInformation return
    STATUS_BUFFER_TOO_SMALL and leave the buffer zeroed, which is
    indistinguishable from a machine that supports nothing. This caught it:
    BATTERY_REPORTING_SCALE is two ULONGs, so the array of three is 24 bytes,
    and declaring it as 12 silently reported a laptop with no power features.
    """
    assert ctypes.sizeof(native.BATTERY_REPORTING_SCALE) == 8
    caps = native.SYSTEM_POWER_CAPABILITIES()
    status = native.CallNtPowerInformation(
        native.SYSTEM_POWER_CAPABILITIES_INFO, None, 0,
        ctypes.byref(caps), ctypes.sizeof(caps))
    assert status == native.STATUS_SUCCESS, (
        f"0x{status & 0xFFFFFFFF:08X} - the struct is the wrong size, and "
        f"every capability flag would read False")


def test_enumeration_finds_real_windows_with_real_owners():
    """
    The regression, live. The broken version returned an empty list on a
    machine with a hundred windows open.
    """
    windows = native.top_level_windows()
    assert windows, "no top-level windows found on a running desktop"
    for hwnd, pid in windows:
        assert hwnd > 0
        assert pid > 0, "a window came back with no owning process"


def test_owning_pids_are_plausible_process_ids():
    """
    A truncated handle produces garbage owners rather than no owners, so
    "we got some numbers" is not enough - they have to be processes.
    """
    import psutil

    owners = {pid for _, pid in native.top_level_windows()}
    alive = {pid for pid in owners if psutil.pid_exists(pid)}
    assert alive, "not one reported owner is a running process"
    # Not all: a window can close between the two calls. Most, though.
    assert len(alive) >= len(owners) * 0.5, \
        f"only {len(alive)} of {len(owners)} owners are real processes"


def test_a_window_title_can_be_read():
    for hwnd, _pid in native.top_level_windows():
        if native.window_title(hwnd).strip():
            return
    pytest.fail("not one window has a readable title")


def test_the_force_flags_exist_but_nothing_defaults_to_them():
    """
    EWX_FORCE is defined because the constant is real, not because Friday
    uses it. Microsoft warns it causes data loss, and the graceful path is
    what a request to shut down means.
    """
    import inspect

    source = inspect.getsource(native)
    assert "EWX_FORCE" in source
    assert "deliberately no default force flag" in source.lower() or \
        "deliberately never set" in source.lower()
