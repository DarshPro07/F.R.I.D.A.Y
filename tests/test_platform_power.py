"""
What this machine can do about power, and why the obvious API is not asked.

Two findings are pinned here, both measured rather than reasoned:

    SeShutdownPrivilege is held and DISABLED, so every power call fails until
    it is enabled - and AdjustTokenPrivileges returns non-zero having enabled
    nothing, so its return value is not the answer.

    IsPwrSuspendAllowed answers about S1-S3. On a modern-standby machine it
    returns False while the machine sleeps every night.

The second is the one worth a test of its own. Believing it would have Friday
tell somebody their own hardware cannot do something it does nightly, which is
the csrss.exe mistake in another costume: reporting as absent something that
is plainly present.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

import pytest

from friday.platform import windows as native

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Win32")


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------


def test_capabilities_can_be_read_at_all():
    caps = native.power_capabilities()
    assert isinstance(caps.sleep, bool)
    assert isinstance(caps.hibernate, bool)


def test_a_modern_standby_machine_is_reported_as_able_to_sleep():
    """
    The finding. `IsPwrSuspendAllowed` reports on S1-S3, which a modern
    machine does not have; `AoAc` is where its sleep lives.

    Skipped rather than asserted on a machine that is not modern-standby,
    because the disagreement is a property of that hardware and a test that
    fails on a desktop is a test that gets deleted.
    """
    caps = native.power_capabilities()
    if not caps.modern_standby:
        pytest.skip("not a modern-standby machine; the two APIs agree here")

    legacy = bool(ctypes.WinDLL("powrprof").IsPwrSuspendAllowed())
    assert caps.sleep is True, "modern standby is present and sleep says no"
    if legacy is False:
        # The documented disagreement, observed. If this ever stops being
        # true the finding has changed and the comment above should go.
        assert caps.sleep != legacy


def test_the_legacy_suspend_api_is_never_consulted():
    """
    Behaviour, not prose - parsed, because grepping the module for a name has
    matched that module's own docstring five separate times in this project.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(native))
    called = {
        node.func.attr for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    } | {
        node.func.id for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "IsPwrSuspendAllowed" not in called
    assert "IsPwrHibernateAllowed" not in called


def test_a_failed_capability_call_raises_rather_than_reporting_nothing(
        monkeypatch):
    """
    The mistake this guard exists for: a wrong-sized struct returns
    STATUS_BUFFER_TOO_SMALL and leaves the buffer zeroed, so every flag reads
    False and the machine looks like it supports nothing at all. An
    all-false answer and a failed call must not be the same thing.
    """
    STATUS_BUFFER_TOO_SMALL = -1073741789

    monkeypatch.setattr(native, "CallNtPowerInformation",
                        lambda *args: STATUS_BUFFER_TOO_SMALL)
    with pytest.raises(OSError) as caught:
        native.power_capabilities()
    assert "C0000023" in str(caught.value).upper()


# ---------------------------------------------------------------------------
# Privilege
# ---------------------------------------------------------------------------


def _shutdown_privilege_enabled() -> bool:
    """Read the token directly, so the test does not trust the thing it tests."""
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

    class PRIVILEGE_SET(ctypes.Structure):
        _fields_ = [("PrivilegeCount", wintypes.DWORD),
                    ("Control", wintypes.DWORD),
                    ("Privilege", native.LUID_AND_ATTRIBUTES * 1)]

    token = wintypes.HANDLE()
    native.OpenProcessToken(native.GetCurrentProcess(), native.TOKEN_QUERY,
                            ctypes.byref(token))
    luid = native.LUID()
    native.LookupPrivilegeValueW(None, native.SE_SHUTDOWN_NAME,
                                 ctypes.byref(luid))
    privileges = PRIVILEGE_SET()
    privileges.PrivilegeCount = 1
    privileges.Control = 0
    privileges.Privilege[0].Luid = luid
    privileges.Privilege[0].Attributes = 0

    advapi32.PrivilegeCheck.argtypes = (
        wintypes.HANDLE, ctypes.POINTER(PRIVILEGE_SET),
        ctypes.POINTER(wintypes.BOOL))
    advapi32.PrivilegeCheck.restype = wintypes.BOOL
    result = wintypes.BOOL()
    advapi32.PrivilegeCheck(token, ctypes.byref(privileges),
                            ctypes.byref(result))
    native.CloseHandle(token)
    return bool(result.value)


def test_enabling_the_privilege_actually_flips_the_token_bit():
    """
    Not "the function returned True" - the token itself, read back. This is
    the same read-back rule the window toolset follows, applied to a bit that
    every power action depends on.

    And it is put back. Leaving it enabled would run the rest of the suite in
    a process that is allowed to shut the machine down, which is a strange
    thing for a test about being careful with shutdowns to do.
    """
    was = _shutdown_privilege_enabled()
    try:
        assert native.enable_shutdown_privilege() is True
        assert _shutdown_privilege_enabled() is True, \
            "the call reported success and the privilege is still disabled"
    finally:
        native.enable_shutdown_privilege(was)
    assert _shutdown_privilege_enabled() is was, "the token was left changed"


def test_the_privilege_check_does_not_trust_the_return_value(monkeypatch):
    """
    `AdjustTokenPrivileges` returns non-zero **even when it enabled nothing**,
    setting ERROR_NOT_ALL_ASSIGNED. A caller that believes the return value
    gets a silent no-op and then blames Windows for refusing the shutdown.
    """
    def adjusted_nothing(*args):
        ctypes.set_last_error(native.ERROR_NOT_ALL_ASSIGNED)
        return 1                                     # non-zero: "success"

    monkeypatch.setattr(native, "AdjustTokenPrivileges", adjusted_nothing)
    assert native.enable_shutdown_privilege() is False, \
        "a privilege that was not assigned was reported as enabled"
