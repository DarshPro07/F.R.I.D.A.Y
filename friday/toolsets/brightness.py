"""
Screen brightness, where the screen has one.

Batch 2C-C. Windows exposes brightness through the WMI classes
`WmiMonitorBrightness` (what it is) and `WmiMonitorBrightnessMethods` (how to
change it), and those instances exist only when the monitor and its driver
provide them. A desktop with an external monitor on HDMI usually has neither.

That absence is not a failure. `NOT_CONFIGURED` is the truthful answer, and
reporting it as broken would send somebody looking for a bug in Friday instead
of at the fact that their monitor has physical buttons.

Two things this deliberately does not do:

  guess       If the WMI instance is missing there is no fallback to sending
              brightness keystrokes, which cannot be read back and so cannot
              be verified. The whole batch exists because keystrokes cannot
              satisfy the proof-of-work contract.
  go dark     A bounded floor, because "set brightness to 0" from a voice
              assistant leaves somebody in the dark holding a machine whose
              screen they now cannot see to fix.
"""

from __future__ import annotations

import subprocess

from friday import contracts as c
from friday.policy import PolicyEngine, default_engine
from friday.toolsets.system import APPROVAL_PREFIX

EXECUTION_SCOPE = "local_machine"

#: Never below this from a spoken instruction. Zero is a black screen, and
#: recovering from one means finding physical buttons.
MINIMUM_PERCENT = 10

#: WMI calls go through PowerShell rather than a new dependency. Slower than
#: a COM binding and about twenty lines cheaper, for something asked a few
#: times a day.
TIMEOUT_SECONDS = 20

_READ = (
    "try { (Get-CimInstance -Namespace root/WMI "
    "-ClassName WmiMonitorBrightness -ErrorAction Stop"
    ").CurrentBrightness } catch { 'NONE' }"
)

_SET = (
    "try {{ (Get-CimInstance -Namespace root/WMI "
    "-ClassName WmiMonitorBrightnessMethods -ErrorAction Stop"
    ") | Invoke-CimMethod -MethodName WmiSetBrightness "
    "-Arguments @{{Timeout=1;Brightness={percent}}} | Out-Null; 'OK' }} "
    "catch {{ 'NONE' }}"
)

#: Windows can also be told to go back to whatever the power policy says,
#: rather than to a number Friday remembers. That is a different and often
#: better restoration - the policy may have changed the brightness itself
#: while Friday's number was getting stale.
_REVERT = (
    "try { (Get-CimInstance -Namespace root/WMI "
    "-ClassName WmiMonitorBrightnessMethods -ErrorAction Stop"
    ") | Invoke-CimMethod -MethodName WmiRevertToPolicyBrightness "
    "| Out-Null; 'OK' } catch { 'NONE' }"
)


class BrightnessUnavailable(RuntimeError):
    """This machine exposes no brightness control."""


def _gate(run: c.Run, tool_id: str, engine: PolicyEngine) -> c.ActionResult | None:
    verdict = engine.decide(tool_id)
    if verdict.allowed:
        return None
    return run.record(c.started(run.run_id, tool_id).finish(
        status=c.CANCELLED,
        error=f"{APPROVAL_PREFIX}: {verdict.reason} [{verdict.decision}]",
    ))


def _powershell(script: str) -> str:
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=TIMEOUT_SECONDS,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return (completed.stdout or "").strip()


def supported() -> bool:
    """Does this machine expose a brightness control at all?"""
    try:
        return _powershell(_READ).isdigit()
    except (OSError, subprocess.SubprocessError):
        return False


def read_percent() -> int:
    answer = _powershell(_READ)
    if not answer.isdigit():
        raise BrightnessUnavailable(
            "this machine exposes no WmiMonitorBrightness instance - the "
            "monitor or its driver does not provide one")
    return int(answer)


def write_percent(percent: int) -> None:
    if _powershell(_SET.format(percent=int(percent))) != "OK":
        raise BrightnessUnavailable(
            "WmiSetBrightness is not available on this machine")


def revert_to_policy() -> bool:
    """Hand brightness back to the power policy. True if Windows accepted."""
    try:
        return _powershell(_REVERT) == "OK"
    except (OSError, subprocess.SubprocessError):
        return False


def brightness_get(
    run: c.Run, *, engine: PolicyEngine = default_engine
) -> c.ActionResult:
    """How bright the screen is, or that this machine cannot say."""
    tool_id = "brightness.get"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    try:
        percent = read_percent()
    except BrightnessUnavailable as exc:
        # NOT a failure. The machine has no such control, and saying so is the
        # answer rather than an error to be investigated.
        return run.record(c.partial(
            started, f"NOT_CONFIGURED: {exc}",
            output={"execution_scope": EXECUTION_SCOPE, "supported": False,
                    "percent": None}))
    except (OSError, subprocess.SubprocessError) as exc:
        return run.record(c.failed(started, f"could not read brightness: {exc}"))

    return run.record(c.succeeded(
        started,
        output={"execution_scope": EXECUTION_SCOPE, "supported": True,
                "percent": percent},
        verification=c.Verification(
            method="wmi_monitor_brightness",
            evidence=f"the screen reports {percent}%"),
    ))


def brightness_set(
    run: c.Run, percent: int, *, engine: PolicyEngine = default_engine
) -> c.ActionResult:
    """Set the screen brightness, and read it back."""
    tool_id = "brightness.set"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    if not 0 <= percent <= 100:
        return run.record(c.failed(
            started, f"brightness must be 0-100, not {percent}"))
    wanted = max(MINIMUM_PERCENT, int(percent))

    try:
        before = read_percent()
        write_percent(wanted)
        after = read_percent()
    except BrightnessUnavailable as exc:
        return run.record(c.partial(
            started, f"NOT_CONFIGURED: {exc}",
            output={"execution_scope": EXECUTION_SCOPE, "supported": False}))
    except (OSError, subprocess.SubprocessError) as exc:
        return run.record(c.failed(started, f"could not set brightness: {exc}"))

    payload = {"execution_scope": EXECUTION_SCOPE, "supported": True,
               "previous_percent": before, "requested_percent": percent,
               "floored_to": wanted if wanted != percent else None,
               "observed_percent": after, "reversible": True}
    if abs(after - wanted) > 2:
        return run.record(c.partial(
            started, f"asked for {wanted}% and the screen is {after}%",
            output=payload))
    return run.record(c.succeeded(
        started, output=payload,
        side_effects=(f"screen brightness {before}% -> {after}%",),
        verification=c.Verification(
            method="wmi_brightness_read_back",
            evidence=f"brightness was {before}% and reads {after}%"
                     + (f" (floored from {percent}% - a dark screen is hard "
                        f"to recover from)" if wanted != percent else "")),
    ))
