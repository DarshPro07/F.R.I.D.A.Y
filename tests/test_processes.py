"""
Ending a process, and knowing which one.

Two operations that are deliberately not interchangeable: asking an
application to close, which it may refuse, and terminating it, which it
cannot. The second needs a yes that names *this* process, and the target is
resolved again immediately before the act - because between the question and
the answer a pid can be released and handed to something else, and that gap is
exactly where the confirmation sits.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

from friday import confirmation as CF
from friday import contracts as c
from friday.toolsets import processes as P

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Win32")

COOPERATIVE = 'import tkinter; r=tkinter.Tk(); r.title({title!r}); r.mainloop()'
STUBBORN = (
    'import tkinter\n'
    'r = tkinter.Tk(); r.title({title!r})\n'
    'r.protocol("WM_DELETE_WINDOW", lambda: None)\n'
    'r.mainloop()\n'
)


@pytest.fixture
def run():
    return c.Run.create("close that", capability="system")


@pytest.fixture
def book():
    return CF.Book()


@pytest.fixture
def spawned():
    """
    Windows this test owns and cleans up, whatever happens.

    Every one gets a title nothing else can share, because the harness may
    only mutate what it can prove it created.
    """
    started: list[subprocess.Popen] = []

    def spawn(source: str, title: str) -> subprocess.Popen:
        process = subprocess.Popen(
            [sys.executable, "-c", source.format(title=title)])
        started.append(process)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if P.windows_of(process.pid):
                return process
            time.sleep(0.2)
        return process

    yield spawn
    for process in started:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


def test_a_token_survives_a_pid_being_reused(run):
    """
    A pid is not an identity. The creation time is what makes the difference
    between the same process and a different one wearing its number.
    """
    described = {"pid": 4216, "name": "x.exe", "created_at": 1000.0,
                 "image_path": ""}
    recycled = {**described, "created_at": 2000.0}
    assert P.token(described) != P.token(recycled)


def test_the_window_owner_is_not_the_launched_pid(spawned):
    """
    Measured, and the reason `with_descendants` exists: the venv's python.exe
    is a trampoline that re-execs the real interpreter, so the pid that comes
    back from Popen is not the pid that owns the window. Chromium and Electron
    do the same thing deliberately.
    """
    process = spawned(COOPERATIVE, "friday-owner-test")
    windows = P.windows_of(process.pid)
    assert windows, "no window found for a process that has one"
    assert any(w["title"] == "friday-owner-test" for w in windows)


def test_ownership_comes_from_windows_not_from_the_title(spawned):
    process = spawned(COOPERATIVE, "friday-ownership-test")
    owners = {w["owner_pid"] for w in P.windows_of(process.pid)}
    assert owners <= P.with_descendants(process.pid), \
        "a window was claimed that this process tree does not own"


# ---------------------------------------------------------------------------
# Closing: the application decides
# ---------------------------------------------------------------------------


def test_a_cooperative_application_closes(run, spawned):
    process = spawned(COOPERATIVE, "friday-cooperative")
    result = P.processes_close(run, str(process.pid))
    assert result.status == c.SUCCEEDED
    assert result.output["closed"] is True
    assert result.output["windows_asked"] >= 1
    assert process.poll() is not None


def test_an_application_that_refuses_is_not_reported_as_closed(run, spawned):
    """
    Refusing is what WM_CLOSE is FOR. A save prompt is an application doing
    its job, and it is neither a failure nor a success.
    """
    process = spawned(STUBBORN, "friday-refuses")
    result = P.processes_close(run, str(process.pid))

    assert result.status == c.PARTIAL
    assert not result.may_claim_completion
    assert result.output["closed"] is False
    assert result.output["force_would_need_confirmation"] is True
    assert process.poll() is None, "it was killed after refusing to close"


def _calls_in(function) -> set[str]:
    """Method names called by a function, parsed rather than grepped."""
    import ast
    import inspect

    tree = ast.parse(inspect.cleandoc(inspect.getsource(function)))
    return {node.func.attr for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)}


def test_closing_never_terminates(run, spawned):
    """
    The substitution this whole design refuses. Three implementations were
    tried that would have force-killed here: psutil.terminate (an alias for
    kill on Windows), taskkill without /F (undocumented as WM_CLOSE), and the
    first WM_CLOSE attempt with an untyped HWND that found no windows at all.
    """
    called = _calls_in(P.processes_close)
    assert "kill" not in called and "terminate" not in called


def test_the_app_level_close_never_terminates_either():
    """
    `apps_close` is the capability the phrase "close Chrome" actually reaches,
    and it shipped calling `psutil.Process.terminate()` in a loop - which on
    Windows is an alias for kill(). Under the default autonomy setting nobody
    was asked, because the category was ASK and FULL turns ASK into a yes.

    So "close Chrome" force-killed every Chrome process and reported
    SUCCEEDED, with evidence reading `process_absent_after_terminate`. The
    evidence was true. The word the person used was not.

    Guarding one of the two close paths and not the other would have left the
    common one open, which is how this was missed the first time.
    """
    from friday.toolsets import system as S

    called = _calls_in(S.apps_close)
    assert "terminate" not in called, "apps_close force-kills again"
    assert "kill" not in called, "apps_close force-kills again"


def test_closing_is_auto_under_full_autonomy_and_still_graceful():
    """
    The combination that hid the defect for a whole phase.

    Each half looks reasonable alone. `APP_CLOSE: ASK` reads as caution;
    `FULL` converting ASK to AUTO is the documented and deliberate default,
    added because a gate with no key is a hang rather than a safety feature.
    Together they meant a destructive action ran unattended - and nothing
    failed, because no test asked what the two did in combination.

    Both halves are asserted here, in one test, so they cannot drift apart
    again: closing stays automatic (people should not be interrogated about
    closing an app) *and* stays graceful (which is what makes that safe).
    """
    from friday import policy as p

    assert p.TOOL_CATEGORIES["apps.close"] == p.GRACEFUL_PROCESS_CLOSE
    assert p.TOOL_CATEGORIES["process.close"] == p.GRACEFUL_PROCESS_CLOSE
    assert p.DEFAULT_POLICY[p.GRACEFUL_PROCESS_CLOSE] == p.ASK

    full = p.resolve_policy(p.FULL)
    assert full[p.GRACEFUL_PROCESS_CLOSE] == p.AUTO, \
        "closing an app should not interrogate the boss"
    assert full[p.FORCE_PROCESS_TERMINATION] == p.CONFIRM, \
        "ending one must never be automatic - this is the pairing that failed"


def test_a_process_with_no_window_is_not_quietly_killed(run):
    """A background process has nothing to receive WM_CLOSE."""
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        result = P.processes_close(run, str(process.pid))
        assert result.status == c.PARTIAL
        assert result.output["windows"] == 0
        assert process.poll() is None
    finally:
        process.kill()
        process.wait(timeout=5)


# ---------------------------------------------------------------------------
# Terminating: somebody has to say so, about this one
# ---------------------------------------------------------------------------


def test_without_a_yes_nothing_is_terminated(run, book, spawned):
    process = spawned(STUBBORN, "friday-needs-a-yes")
    result = P.processes_terminate(run, str(process.pid), book=book)

    assert result.status == c.CANCELLED
    assert process.poll() is None
    assert result.output["confirm"]["nonce"]
    assert result.output["unsaved_work_at_risk"] is True


def test_a_yes_for_something_else_does_not_terminate_it(run, book, spawned):
    """The failure the fingerprint exists to prevent."""
    process = spawned(STUBBORN, "friday-wrong-yes")
    P.processes_terminate(run, str(process.pid), book=book)

    elsewhere = book.ask(run.run_id, "RESTART_MACHINE", "LOCAL_MACHINE", "?")
    book.approve(elsewhere.nonce)

    result = P.processes_terminate(run, str(process.pid), elsewhere.nonce,
                                   book=book)
    assert result.status == c.FAILED
    assert "RESTART_MACHINE" in result.error
    assert process.poll() is None


def test_the_right_yes_terminates_exactly_that_process(run, book, spawned):
    process = spawned(STUBBORN, "friday-force-me")
    asked = P.processes_terminate(run, str(process.pid), book=book)
    nonce = asked.output["confirm"]["nonce"]
    book.approve(nonce)

    result = P.processes_terminate(run, str(process.pid), nonce, book=book)
    assert result.status == c.SUCCEEDED
    assert process.poll() is not None
    assert "creation time" in result.verification.evidence


def test_a_confirmation_cannot_be_spent_twice(run, book, spawned):
    process = spawned(STUBBORN, "friday-once-only")
    asked = P.processes_terminate(run, str(process.pid), book=book)
    nonce = asked.output["confirm"]["nonce"]
    book.approve(nonce)
    P.processes_terminate(run, str(process.pid), nonce, book=book)

    again = P.processes_terminate(run, str(sys.argv and os.getpid()), nonce,
                                  book=book)
    assert again.status == c.FAILED


def test_a_target_that_exits_while_waiting_is_not_replaced(run, book, spawned):
    """
    TARGET_GONE, and no looking around for another process with the same
    name. The one that was confirmed is the only one authorised.
    """
    process = spawned(STUBBORN, "friday-vanishes")
    asked = P.processes_terminate(run, str(process.pid), book=book)
    nonce = asked.output["confirm"]["nonce"]
    book.approve(nonce)

    process.kill()
    process.wait(timeout=5)

    result = P.processes_terminate(run, str(process.pid), nonce, book=book)
    assert result.status == c.FAILED
    assert "TARGET_GONE" in result.error or "nothing running" in result.error


# ---------------------------------------------------------------------------
# What is never a target
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["csrss.exe", "explorer.exe", "lsass.exe"])
def test_system_processes_are_refused_by_name(run, name):
    result = P.processes_terminate(run, name)
    assert result.status == c.FAILED
    assert "BLOCKED" in result.error
    assert "system process" in result.error


def test_friday_will_not_end_itself(run):
    result = P.processes_terminate(run, str(os.getpid()))
    assert result.status == c.FAILED
    assert "Friday itself" in result.error


def test_a_protected_process_is_refused_before_it_is_offered(run, book):
    """
    No confirmation is created for something that could never be authorised.
    Offering to confirm it would be a lie about what saying yes could do.
    """
    result = P.processes_terminate(run, "csrss.exe", book=book)
    assert result.status == c.FAILED
    assert not book.pending, "it offered a yes for something it will not do"


def test_a_protected_process_is_not_reported_as_missing(run):
    """
    Found by the live gate: `find` filtered protected processes out, so
    terminating csrss.exe answered "nothing running matches" - which is
    false. It is running. "It does not exist" and "I will not do that" are
    different sentences, and only one of them is honest.
    """
    result = P.processes_terminate(run, "csrss.exe")
    assert "nothing running matches" not in result.error
    assert "system process" in result.error


def test_something_that_genuinely_is_not_running_says_so(run):
    result = P.processes_terminate(run, "definitely-not-a-real-program")
    assert "nothing running matches" in result.error
