"""
Phase 1G: durable, OS-scheduled reminders.

Time parsing is tested exhaustively and offline. Scheduler tests create real
Windows tasks and clean up after themselves. The test that proves a reminder
actually fires is marked `slow` because it has to wait for the clock.
"""

from __future__ import annotations

import sys
import time
from datetime import datetime

import pytest

from friday import contracts as c
from friday import policy as p
from friday.store import Store
from friday.toolsets import reminders as R
from friday.toolsets.reminders import WhenError, parse_when

WINDOWS = sys.platform == "win32"
windows_only = pytest.mark.skipif(not WINDOWS, reason="Windows scheduler")
slow = pytest.mark.slow

NOW = datetime(2026, 8, 16, 14, 30, 0)


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "reminders.sqlite3")
    R.reset_store(s)
    yield s
    R.reset_store(None)


@pytest.fixture
def run():
    return c.Run.create("test", capability="reminders")


@pytest.fixture
def cleanup():
    """Delete any scheduler tasks a test leaves behind."""
    created: list[str] = []
    yield created
    for task in created:
        R._delete_task(task)


# ---------------------------------------------------------------------------
# Understanding "when"
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text, expected", [
    ("in 30 seconds", "2026-08-16T14:30:30"),
    ("in 20 minutes", "2026-08-16T14:50:00"),
    ("in 1 minute", "2026-08-16T14:31:00"),
    ("20 minutes", "2026-08-16T14:50:00"),
    ("in 2 hours", "2026-08-16T16:30:00"),
    ("in 2 days", "2026-08-18T14:30:00"),
    ("tomorrow morning", "2026-08-17T09:00:00"),
    ("tomorrow afternoon", "2026-08-17T14:00:00"),
    ("tomorrow evening", "2026-08-17T19:00:00"),
    ("tomorrow", "2026-08-17T09:00:00"),
    ("tonight", "2026-08-16T21:00:00"),
    ("at 15:45", "2026-08-16T15:45:00"),
    ("at 3pm", "2026-08-16T15:00:00"),
    ("at 9am", "2026-08-17T09:00:00"),      # already past today
    ("2026-12-25T08:00:00", "2026-12-25T08:00:00"),
])
def test_when_parsing(text, expected):
    assert parse_when(text, now=NOW).isoformat(timespec="seconds") == expected


@pytest.mark.parametrize("text", [
    "", "   ", "sometime", "next fortnight-ish", "at 99:99",
    "in 5 parsecs", "when the mood strikes",
])
def test_unparseable_times_are_refused_not_guessed(text):
    """A reminder at the wrong moment is worse than one the user is told failed."""
    with pytest.raises(WhenError):
        parse_when(text, now=NOW)


def test_a_clock_time_already_past_rolls_to_tomorrow():
    assert parse_when("at 9am", now=NOW).day == 17
    assert parse_when("at 11pm", now=NOW).day == 16


def test_iso_with_timezone_is_normalised():
    parsed = parse_when("2026-12-25T08:00:00+05:30", now=NOW)
    assert parsed.tzinfo is None


# ---------------------------------------------------------------------------
# How it speaks
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("hour, expect", [
    (2, "Still up"), (23, "Still up"),
    (7, "Morning"), (11, "Morning"),
    (14, "you asked me to remind you"), (16, "Flagging this"),
    (19, "Evening"), (21, "this is the one"),
])
def test_openers_match_the_hour(hour, expect):
    """A 2am nudge should not read like a 9am one."""
    openers = [R.opener_for(hour, seed) for seed in range(3)]
    assert any(expect.lower() in o.lower() for o in openers), openers


def test_openers_vary_within_a_band():
    variants = {R.opener_for(14, seed) for seed in range(3)}
    assert len(variants) == 3, "the same sentence every time reads like a machine"


def test_every_opener_is_in_character():
    """
    FRIDAY addresses the boss. Every line, no exceptions - an opener that
    forgets reads like the system alert this is meant not to be.
    """
    for band, lines in R._OPENERS_BY_HOUR.items():
        for line in lines:
            assert "boss" in line.lower(), f"{band}: {line!r} does not address the boss"
            assert not line.lower().startswith("reminder")
            assert line.endswith((".", "!")), f"{band}: {line!r} is unfinished"


def test_generated_fire_script_is_valid_python():
    """
    This broke twice while being written: the body was an f-string, so every
    dict literal and nested f-string inside the generated script needed its
    braces doubled. Header and body are separate now; this pins it.
    """
    import ast

    script = R._fire_script(7, "Run the thermal sim", "C:/tmp/x.sqlite3")
    ast.parse(script)  # raises SyntaxError if the template is broken again
    assert "F.R.I.D.A.Y." in script
    assert "Run the thermal sim" in script


def test_fire_script_never_interpolates_the_message_into_powershell():
    """
    The message reaches PowerShell through the environment, so a reminder
    whose text contains quotes cannot become PowerShell.
    """
    nasty = "'; Remove-Item C:\\ -Recurse; #"
    script = R._fire_script(1, nasty, "C:/tmp/x.sqlite3")
    powershell_lines = [ln for ln in script.splitlines()
                        if "ShowBalloonTip" in ln or "LoadXml" in ln]
    assert powershell_lines
    for line in powershell_lines:
        assert nasty not in line, "message was baked into the PowerShell source"
    assert "$env:ADA_MSG" in script or "$env:ADA_TOAST_XML" in script


# ---------------------------------------------------------------------------
# Refusals that need no scheduler
# ---------------------------------------------------------------------------


def test_past_time_is_refused(store, run):
    result = R.reminders_create(run, "too late", "2020-01-01T00:00:00")
    assert result.status == c.FAILED
    assert "in the past" in result.error
    assert not result.may_claim_completion


def test_empty_message_is_refused(store, run):
    assert R.reminders_create(run, "   ", "in 10 minutes").status == c.FAILED


def test_unparseable_when_fails_the_tool_too(store, run):
    result = R.reminders_create(run, "something", "whenever")
    assert result.status == c.FAILED
    assert "could not understand" in result.error


def test_cancel_unknown_id_fails(store, run):
    assert R.reminders_cancel(run, 9999).status == c.FAILED


def test_reminder_policy_defaults():
    engine = p.PolicyEngine()
    assert engine.decide("reminders.create").decision == p.AUTO
    assert engine.decide("reminders.list").decision == p.AUTO
    assert engine.decide("reminders.cancel").decision == p.AUTO


# ---------------------------------------------------------------------------
# The real scheduler
# ---------------------------------------------------------------------------


@windows_only
def test_create_verifies_the_task_is_actually_registered(store, run, cleanup):
    """
    Mark-L checks the scheduler's returncode. This queries the task back, so
    'succeeded' means the task was observed to exist.
    """
    result = R.reminders_create(run, "unit test reminder", "in 45 minutes")
    assert result.status == c.SUCCEEDED, result.error
    task = result.output["task_name"]
    cleanup.append(task)

    assert R.task_exists(task), "task reported created but is not registered"
    assert result.verification.method == "scheduler_task_queried"
    assert "confirmed registered" in result.verification.evidence


@windows_only
def test_pending_list_reports_whether_the_os_still_holds_it(store, run, cleanup):
    created = R.reminders_create(run, "listed reminder", "in 40 minutes")
    cleanup.append(created.output["task_name"])

    listed = R.reminders_list(run)
    assert listed.status == c.SUCCEEDED
    assert listed.output["count"] == 1
    row = listed.output["reminders"][0]
    assert row["still_scheduled"] is True
    assert row["fired"] == 0


@windows_only
def test_cancel_removes_the_task_from_the_scheduler(store, run):
    created = R.reminders_create(run, "doomed", "in 35 minutes")
    task = created.output["task_name"]
    assert R.task_exists(task)

    cancelled = R.reminders_cancel(run, created.output["id"])
    assert cancelled.status == c.SUCCEEDED
    assert not R.task_exists(task), "cancel claimed success but task remains"
    assert R.reminders_list(run).output["count"] == 0


@windows_only
def test_reminder_row_persists_independently_of_the_scheduler(store, run, cleanup):
    """The database is the record; the scheduler is the mechanism."""
    created = R.reminders_create(run, "persisted", "in 50 minutes")
    cleanup.append(created.output["task_name"])

    row = store.get_reminder(created.output["id"])
    assert row["message"] == "persisted"
    assert row["scheduler"] == "schtasks"
    assert row["job_id"] == created.output["task_name"]
    assert row["fired"] == 0


@windows_only
@slow
def test_a_reminder_actually_fires(store, run):
    """
    §27: "Remind me in 2 minutes. Verify reminder actually fires."

    The fired flag is set by the script the Windows scheduler executes, so
    seeing it flip is evidence the OS ran our task - not that we scheduled it.
    """
    created = R.reminders_create(run, "pytest fire check", "in 65 seconds")
    assert created.status == c.SUCCEEDED, created.error
    reminder_id = created.output["id"]

    deadline = time.monotonic() + 180
    fired = False
    while time.monotonic() < deadline:
        row = store.get_reminder(reminder_id)
        if row and row["fired"]:
            fired = True
            break
        time.sleep(2)

    if not fired:
        R.reminders_cancel(run, reminder_id)
    assert fired, "the reminder never fired within the window"
