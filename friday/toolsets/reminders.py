"""
Reminders toolset (Phase 1G): durable, OS-scheduled, verified.

Independent reimplementation. Mark-L is CC BY-NC so none of its source is
used, but its *architecture* is the one the parity matrix marked genuinely
good and it is worth stating why: the reminder is registered with the
operating system's own scheduler rather than an in-process timer. The OS then
owns the schedule, so the reminder survives the agent exiting, the machine
sleeping, and a reboot. An asyncio task would not.

Two things are done differently:

1. **Verification.** Mark-L checks the scheduler's returncode and reports
   honestly on failure - better than most of its file. Here the task is
   queried back after creation (`schtasks /Query`), so success means the task
   was observed to exist, not that a command exited zero.

2. **State.** The reminder is also a row in our SQLite database, and the fired
   script marks that row. So "did it fire?" is answerable after the fact
   rather than only observable as a toast nobody was watching for.
"""

from __future__ import annotations

import json
import os
import platform
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

from friday import contracts as c
from friday.policy import PolicyEngine, default_engine
from friday.store import DEFAULT_DB, Store
from friday.toolsets.system import APPROVAL_PREFIX

EXECUTION_SCOPE = "local_machine"
TASK_PREFIX = "ADAReminder"

_NO_WINDOW = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}

_store: Store | None = None


def store() -> Store:
    global _store
    if _store is None:
        _store = Store(os.getenv("ADA_DB") or DEFAULT_DB)
    return _store


def reset_store(new: Store | None = None) -> None:
    global _store
    if _store is not None and new is not _store:
        try:
            _store.close()
        except Exception:
            pass
    _store = new


def scripts_dir() -> Path:
    path = Path(os.getenv("ADA_REMINDER_DIR", Path.home() / ".ada" / "reminders"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def _gate(run: c.Run, tool_id: str, engine: PolicyEngine) -> c.ActionResult | None:
    verdict = engine.decide(tool_id)
    if verdict.allowed:
        return None
    return run.record(c.started(run.run_id, tool_id).finish(
        status=c.CANCELLED,
        error=f"{APPROVAL_PREFIX}: {verdict.reason} [{verdict.decision}]",
    ))


def _scoped(payload: dict) -> dict:
    return {"execution_scope": EXECUTION_SCOPE, **payload}


# ---------------------------------------------------------------------------
# When
# ---------------------------------------------------------------------------

_UNITS = {
    "second": 1, "seconds": 1, "sec": 1, "secs": 1, "s": 1,
    "minute": 60, "minutes": 60, "min": 60, "mins": 60, "m": 60,
    "hour": 3600, "hours": 3600, "hr": 3600, "hrs": 3600, "h": 3600,
    "day": 86400, "days": 86400,
}

_NAMED_TIMES = {
    "morning": (9, 0), "afternoon": (14, 0), "evening": (19, 0),
    "night": (21, 0), "noon": (12, 0), "midnight": (0, 0),
}


class WhenError(ValueError):
    """The requested time could not be understood, or is in the past."""


def parse_when(text: str, *, now: datetime | None = None) -> datetime:
    """
    Turn "in 2 minutes" / "tomorrow morning" / "at 15:30" / ISO into a time.

    Raises rather than guessing. A reminder set for the wrong moment is worse
    than one the user is told could not be understood.
    """
    now = now or datetime.now()
    raw = (text or "").strip().lower()
    if not raw:
        raise WhenError("no time given")

    # ISO first - unambiguous, so it wins.
    try:
        parsed = datetime.fromisoformat(text.strip())
        return parsed if parsed.tzinfo is None else parsed.astimezone().replace(tzinfo=None)
    except ValueError:
        pass

    relative = re.match(r"^(?:in\s+)?(\d+(?:\.\d+)?)\s*([a-z]+)$", raw)
    if relative:
        amount, unit = float(relative.group(1)), relative.group(2)
        if unit not in _UNITS:
            raise WhenError(f"unknown time unit {unit!r}")
        return now + timedelta(seconds=amount * _UNITS[unit])

    day_offset = 0
    if raw.startswith("tomorrow"):
        day_offset, raw = 1, raw[len("tomorrow"):].strip()
    elif raw.startswith("today") or raw.startswith("tonight"):
        if raw.startswith("tonight"):
            raw = "night " + raw[len("tonight"):].strip()
        else:
            raw = raw[len("today"):].strip()

    raw = raw.lstrip("at").strip() if raw.startswith("at ") else raw

    clock = re.match(r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$", raw)
    if clock:
        hour = int(clock.group(1))
        minute = int(clock.group(2) or 0)
        meridiem = clock.group(3)
        if meridiem == "pm" and hour < 12:
            hour += 12
        elif meridiem == "am" and hour == 12:
            hour = 0
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise WhenError(f"{text!r} is not a valid clock time")
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        target += timedelta(days=day_offset)
        if target <= now and day_offset == 0:
            target += timedelta(days=1)  # "at 9" tonight already passed -> tomorrow
        return target

    for name, (hour, minute) in _NAMED_TIMES.items():
        if raw.startswith(name):
            target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            target += timedelta(days=day_offset)
            if target <= now:
                target += timedelta(days=1)
            return target

    if day_offset:  # bare "tomorrow"
        target = now.replace(hour=9, minute=0, second=0, microsecond=0)
        return target + timedelta(days=day_offset)

    raise WhenError(
        f"could not understand {text!r}. Try 'in 20 minutes', 'tomorrow "
        f"morning', 'at 15:30', or an ISO timestamp"
    )


# ---------------------------------------------------------------------------
# The script the scheduler runs
# ---------------------------------------------------------------------------


#: How F.R.I.D.A.Y. opens a reminder. Chosen by hour so a late-night nudge
#: does not read like a 9am one, and varied within each band so the same
#: sentence is not repeated every single time.
_OPENERS_BY_HOUR = {
    "night": (
        "Still up, boss. You wanted this flagged.",
        "Late one, boss — you asked me to bring this up.",
        "Burning the midnight oil, boss. You wanted a nudge on this.",
    ),
    "morning": (
        "Morning, boss. You asked me to flag this.",
        "Early start, boss — here's the one you wanted.",
        "Boss. Flagging this, as requested.",
    ),
    "afternoon": (
        "Boss — you asked me to remind you.",
        "Flagging this for you, boss.",
        "Here's the one you wanted brought up, boss.",
    ),
    "evening": (
        "Evening, boss. You wanted this raised.",
        "Winding down, boss — but you asked me to flag this.",
        "Boss, this is the one you wanted remembered.",
    ),
}


def opener_for(hour: int, seed: int) -> str:
    """Pick an in-character opener for the hour of day."""
    if hour >= 22 or hour < 5:
        band = "night"
    elif hour < 12:
        band = "morning"
    elif hour < 17:
        band = "afternoon"
    else:
        band = "evening"
    choices = _OPENERS_BY_HOUR[band]
    return choices[seed % len(choices)]


def _fire_script(reminder_id: int, message: str, db_path: str) -> str:
    """
    Generated on demand. Marks the database row first - that is the durable
    evidence the reminder fired - then speaks, in character.

    The notification is a native Windows toast so it lands in Action Center
    and looks like the system's own, with a NotifyIcon balloon as fallback.
    The record is the proof; the toast is the part the user actually meets.
    """
    # Values are emitted as a header via json.dumps; the body below is a plain
    # string, NOT an f-string. Templating the body with f-strings meant every
    # dict literal and nested f-string inside the generated script had to have
    # its braces doubled, which broke twice in a row. Splitting header from
    # body removes the escaping problem entirely.
    header = (
        "# Auto-generated by ADA. Safe to delete.\n"
        f"MESSAGE = {json.dumps(message)}\n"
        f"DB = {json.dumps(db_path)}\n"
        f"REMINDER_ID = {reminder_id}\n"
        f"OPENERS = {json.dumps(_OPENERS_BY_HOUR)}\n"
    )
    return header + _FIRE_BODY


_FIRE_BODY = '''
import sqlite3, sys, pathlib
from datetime import datetime
from xml.sax.saxutils import escape

try:
    conn = sqlite3.connect(DB)
    conn.execute("UPDATE reminders SET fired=1 WHERE id=?", (REMINDER_ID,))
    conn.commit()
    conn.close()
except Exception:
    pass

_h = datetime.now().hour
_band = ("night" if (_h >= 22 or _h < 5) else
         "morning" if _h < 12 else
         "afternoon" if _h < 17 else "evening")
_choices = OPENERS[_band]
OPENER = _choices[REMINDER_ID % len(_choices)]
STAMP = datetime.now().strftime("%H:%M")

# Everything variable is passed through the environment, never interpolated
# into PowerShell source. That removes every quoting layer and, with it, any
# way for a reminder's text to become PowerShell.
_TOAST_XML = (
    '<toast duration="long" scenario="reminder">'
    '<visual><binding template="ToastGeneric">'
    '<text>F.R.I.D.A.Y.</text>'
    f'<text>{escape(OPENER)}</text>'
    f'<text placement="attribution">{escape(MESSAGE)}  ·  {STAMP}</text>'
    '</binding></visual>'
    '<audio src="ms-winsoundevent:Notification.Reminder"/>'
    '</toast>'
)

_NATIVE_TOAST = (
    "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications,"
    " ContentType=WindowsRuntime] > $null;"
    "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument,"
    " ContentType=WindowsRuntime] > $null;"
    "$x = New-Object Windows.Data.Xml.Dom.XmlDocument;"
    "$x.LoadXml($env:ADA_TOAST_XML);"
    "$t = New-Object Windows.UI.Notifications.ToastNotification $x;"
    "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("
    "'{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\\\WindowsPowerShell\\\\v1.0\\\\powershell.exe'"
    ").Show($t)"
)

_BALLOON = (
    "Add-Type -AssemblyName System.Windows.Forms;"
    "Add-Type -AssemblyName System.Drawing;"
    "$n = New-Object System.Windows.Forms.NotifyIcon;"
    "$n.Icon = [System.Drawing.SystemIcons]::Information;"
    "$n.Visible = $true;"
    "$n.ShowBalloonTip(20000, 'F.R.I.D.A.Y.', $env:ADA_OPENER + \\\"`n\\\" + $env:ADA_MSG, 'Info');"
    "Start-Sleep -Seconds 15; $n.Dispose()"
)


def _powershell(script, env_extra):
    import os as _os
    import subprocess
    return subprocess.run(
        ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", script],
        env=dict(_os.environ, **env_extra), timeout=45,
        capture_output=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    ).returncode


_shown = False
try:
    _shown = _powershell(_NATIVE_TOAST, {"ADA_TOAST_XML": _TOAST_XML}) == 0
except Exception:
    _shown = False

if not _shown:  # older shells, or WinRT unavailable
    try:
        _powershell(_BALLOON, {"ADA_OPENER": OPENER, "ADA_MSG": MESSAGE})
    except Exception:
        pass

try:
    import winsound, time
    for freq in (784, 1047, 1319):  # a rising third, not an error buzz
        winsound.Beep(freq, 140)
        time.sleep(0.04)
except Exception:
    pass

try:
    pathlib.Path(__file__).unlink(missing_ok=True)
except Exception:
    pass
'''


def _task_xml(when: datetime, command: str, arguments: str) -> str:
    # DeleteExpiredTaskAfter requires the trigger to declare an EndBoundary -
    # without one schtasks refuses the whole document. The boundary is set
    # well after the fire time so the task is still valid when it runs, then
    # expires and removes itself rather than accumulating in Task Scheduler.
    end = when + timedelta(minutes=30)
    return (
        '<?xml version="1.0" encoding="UTF-16"?>\n'
        '<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">\n'
        "  <RegistrationInfo><Description>ADA Reminder</Description></RegistrationInfo>\n"
        "  <Triggers><TimeTrigger>\n"
        f'    <StartBoundary>{when.strftime("%Y-%m-%dT%H:%M:%S")}</StartBoundary>\n'
        f'    <EndBoundary>{end.strftime("%Y-%m-%dT%H:%M:%S")}</EndBoundary>\n'
        "    <Enabled>true</Enabled>\n"
        "  </TimeTrigger></Triggers>\n"
        "  <Actions><Exec>\n"
        f"    <Command>{command}</Command>\n"
        f"    <Arguments>{arguments}</Arguments>\n"
        "  </Exec></Actions>\n"
        "  <Settings>\n"
        "    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>\n"
        "    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>\n"
        "    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>\n"
        "    <StartWhenAvailable>true</StartWhenAvailable>\n"
        "    <ExecutionTimeLimit>PT5M</ExecutionTimeLimit>\n"
        "    <DeleteExpiredTaskAfter>PT10M</DeleteExpiredTaskAfter>\n"
        "    <Enabled>true</Enabled>\n"
        "  </Settings>\n"
        "  <Principals><Principal>\n"
        "    <LogonType>InteractiveToken</LogonType>\n"
        "    <RunLevel>LeastPrivilege</RunLevel>\n"
        "  </Principal></Principals>\n"
        "</Task>"
    )


def _pythonw() -> str:
    exe = Path(sys.executable)
    windowless = exe.parent / "pythonw.exe"
    return str(windowless if windowless.exists() else exe)


def task_exists(task_name: str) -> bool:
    """Ask the scheduler whether the task is registered."""
    if os.name != "nt":
        return False
    result = subprocess.run(
        ["schtasks", "/Query", "/TN", task_name],
        capture_output=True, text=True, **_NO_WINDOW,
    )
    return result.returncode == 0


def _delete_task(task_name: str) -> bool:
    if os.name != "nt":
        return False
    result = subprocess.run(
        ["schtasks", "/Delete", "/TN", task_name, "/F"],
        capture_output=True, text=True, **_NO_WINDOW,
    )
    return result.returncode == 0


def scheduled_task_names() -> list[str]:
    """Every ADA reminder task currently registered with the OS."""
    if os.name != "nt":
        return []
    result = subprocess.run(
        ["schtasks", "/Query", "/FO", "LIST"],
        capture_output=True, text=True, **_NO_WINDOW,
    )
    names = []
    for line in (result.stdout or "").splitlines():
        if line.strip().lower().startswith("taskname:"):
            name = line.split(":", 1)[1].strip().lstrip("\\")
            if name.startswith(TASK_PREFIX):
                names.append(name)
    return names


def prune_stale() -> dict:
    """
    Remove scheduler tasks and fire scripts for reminders that already fired
    or were cancelled.

    Windows expires the task itself via DeleteExpiredTaskAfter, but only
    thirty minutes later and only if it ran. Anything interrupted would linger
    indefinitely, so this sweeps explicitly rather than trusting that.
    """
    if os.name != "nt":
        return {"tasks_removed": 0, "scripts_removed": 0}

    pending = {r["job_id"] for r in store().pending_reminders() if r.get("job_id")}
    removed_tasks = [name for name in scheduled_task_names()
                     if name not in pending and _delete_task(name)]

    removed_scripts = 0
    for script in scripts_dir().glob(f"{TASK_PREFIX}_*.py"):
        if script.stem not in pending:
            script.unlink(missing_ok=True)
            removed_scripts += 1
    for leftover in scripts_dir().glob(f"{TASK_PREFIX}_*.xml"):
        leftover.unlink(missing_ok=True)

    return {"tasks_removed": len(removed_tasks), "scripts_removed": removed_scripts,
            "removed": removed_tasks}


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------


def reminders_create(
    run: c.Run, message: str, when: str, *, engine: PolicyEngine = default_engine
) -> c.ActionResult:
    """Schedule a reminder with the OS, then verify the task is registered."""
    tool_id = "reminders.create"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    if not (message or "").strip():
        return run.record(c.failed(started, "reminder message is required"))
    try:
        target = parse_when(when)
    except WhenError as exc:
        return run.record(c.failed(started, str(exc)))

    now = datetime.now()
    if target <= now:
        return run.record(c.failed(
            started, f"{target.isoformat(timespec='seconds')} is in the past"
        ))
    if platform.system() != "Windows":
        return run.record(c.failed(
            started, f"reminders not implemented for {platform.system()}"
        ))

    message = message.strip()
    reminder_id = store().save_reminder(
        message, target.isoformat(timespec="seconds"), scheduler="schtasks"
    )
    task_name = f"{TASK_PREFIX}_{reminder_id}_{target.strftime('%Y%m%d_%H%M%S')}"

    script_path = scripts_dir() / f"{task_name}.py"
    xml_path = scripts_dir() / f"{task_name}.xml"
    try:
        script_path.write_text(
            _fire_script(reminder_id, message, str(store().path.resolve())),
            encoding="utf-8",
        )
        xml_path.write_text(
            _task_xml(target, _pythonw(), f'"{script_path}"'), encoding="utf-16"
        )
        created = subprocess.run(
            ["schtasks", "/Create", "/TN", task_name, "/XML", str(xml_path), "/F"],
            capture_output=True, text=True, **_NO_WINDOW,
        )
    except OSError as exc:
        script_path.unlink(missing_ok=True)
        return run.record(c.failed(started, f"could not prepare reminder: {exc}"))
    finally:
        xml_path.unlink(missing_ok=True)

    if created.returncode != 0:
        script_path.unlink(missing_ok=True)
        detail = (created.stderr or created.stdout or "").strip()[:200]
        return run.record(c.failed(started, f"scheduler refused the task: {detail}"))

    # returncode 0 says the command succeeded. Query it back to say the task exists.
    if not task_exists(task_name):
        script_path.unlink(missing_ok=True)
        return run.record(c.partial(
            started,
            f"schtasks reported success but {task_name!r} is not registered",
            output=_scoped({"task_name": task_name}),
        ))

    store().set_reminder_job(reminder_id, task_name)

    delta = target - now
    return run.record(c.succeeded(
        started,
        output=_scoped({"id": reminder_id, "message": message,
                        "due_at": target.isoformat(timespec="seconds"),
                        "in_seconds": int(delta.total_seconds()),
                        "task_name": task_name, "scheduler": "schtasks"}),
        side_effects=(f"registered Windows scheduled task {task_name}",),
        verification=c.Verification(
            method="scheduler_task_queried",
            evidence=f"task {task_name!r} confirmed registered via schtasks /Query; "
                     f"fires {target.isoformat(timespec='seconds')} "
                     f"(in {int(delta.total_seconds())}s)",
        ),
    ))


def reminders_list(
    run: c.Run, *, engine: PolicyEngine = default_engine
) -> c.ActionResult:
    tool_id = "reminders.list"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    pending = store().pending_reminders()
    for row in pending:
        row["still_scheduled"] = task_exists(row["job_id"]) if row.get("job_id") else False

    return run.record(c.succeeded(
        started,
        output=_scoped({"count": len(pending), "reminders": pending}),
        verification=c.Verification(
            method="reminder_query",
            evidence=f"{len(pending)} pending reminder(s) in {store().path}; "
                     f"{sum(1 for r in pending if r['still_scheduled'])} still "
                     f"registered with the OS scheduler",
        ),
    ))


def reminders_cancel(
    run: c.Run, reminder_id: int, *, engine: PolicyEngine = default_engine
) -> c.ActionResult:
    """Cancel a reminder and verify the scheduler no longer holds it."""
    tool_id = "reminders.cancel"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    row = next((r for r in store().pending_reminders() if r["id"] == reminder_id), None)
    if row is None:
        return run.record(c.failed(started, f"no pending reminder with id {reminder_id}"))

    task_name = row.get("job_id")
    if task_name:
        _delete_task(task_name)
        if task_exists(task_name):
            return run.record(c.partial(
                started, f"database updated but {task_name!r} is still scheduled",
                output=_scoped({"id": reminder_id, "task_name": task_name}),
            ))
        (scripts_dir() / f"{task_name}.py").unlink(missing_ok=True)

    store().close_reminder(reminder_id)

    return run.record(c.succeeded(
        started,
        output=_scoped({"id": reminder_id, "message": row["message"],
                        "task_name": task_name}),
        side_effects=(f"cancelled reminder {reminder_id}",),
        verification=c.Verification(
            method="scheduler_task_absent",
            evidence=f"task {task_name!r} no longer registered; reminder "
                     f"{reminder_id} closed in {store().path}",
        ),
    ))
