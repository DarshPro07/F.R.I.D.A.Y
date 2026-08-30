"""
Processes, identified by more than a number.

Batch 2D. Windows reuses process ids as soon as a process ends, so a pid
resolved before a question and used after the answer may name something else
entirely. This project has been caught by that shape three times already - a
recycled pid read as a live executor run, a window title that was not
ownership, an audio session whose pid had been dead for minutes - so here the
identity is the pid **together with** the creation time and the image path,
captured at resolution and re-checked immediately before anything is done.

Two ways to end a process, and they are not interchangeable:

    close     WM_CLOSE, the message an application is built to receive. It can
              prompt to save, and it can decline. This is what "close Notepad"
              means.
    terminate TerminateProcess, which Microsoft's own documentation says is
              unconditional and for extreme circumstances - handlers do not
              run, buffers are not flushed, DLLs are not notified. This is
              what "it is hung, kill it" means, and it needs its own yes.

Both are asynchronous. Neither returning is evidence that the process ended,
so both wait and then look.
"""

from __future__ import annotations

import os
import time

import psutil

from friday import confirmation as CF
from friday import contracts as c
from friday.platform import windows as native
from friday import policy as policy_module
from friday.policy import PolicyEngine, default_engine
from friday.toolsets.system import APPROVAL_PREFIX

EXECUTION_SCOPE = "local_machine"

#: How long to let an application respond to a close request before saying it
#: did not. Long enough for a save prompt to appear, short enough that a voice
#: turn does not stall.
CLOSE_SECONDS = 6.0

#: After TerminateProcess. This one is unconditional, so it is quick.
TERMINATE_SECONDS = 5.0

#: Never, whatever anybody says. Killing these is not a user decision that
#: went wrong, it is a machine that stops working.
#:
#: Names alone would be a fragile list - a user process can be called anything
#: - so this is one of three checks, alongside the pid floor and Friday's own
#: process tree.
CRITICAL_NAMES = frozenset({
    "system", "system idle process", "registry", "memory compression",
    "smss.exe", "csrss.exe", "wininit.exe", "winlogon.exe", "services.exe",
    "lsass.exe", "lsaiso.exe", "svchost.exe", "dwm.exe", "fontdrvhost.exe",
    "sihost.exe", "ctfmon.exe", "audiodg.exe", "explorer.exe",
})

#: pid 0 is the idle process and pid 4 is the kernel. Neither is a program.
CRITICAL_PIDS = frozenset({0, 4})


class ProcessError(RuntimeError):
    """No process matched, or more than one did, or it must not be touched."""


class Protected(ProcessError):
    """This process is not a legitimate target at any authorisation level."""


def _gate(run: c.Run, tool_id: str, engine: PolicyEngine) -> c.ActionResult | None:
    """
    Policy, and where the objective came from, before anything happens.

    Provenance is checked first and separately. A run whose objective was
    lifted out of a web page cannot reach a destructive capability at all, so
    there is no question to ask and no confirmation to offer - the refusal is
    the whole answer, and it is BLOCKED rather than CANCELLED because nobody
    is being waited on.
    """
    refusal = policy_module.provenance_verdict(tool_id, run.provenance)
    if refusal is not None:
        return run.record(c.started(run.run_id, tool_id).finish(
            status=c.FAILED,
            error=f"BLOCKED: {refusal.reason}",
        ))

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
# Identity
# ---------------------------------------------------------------------------


def own_chain() -> set[int]:
    """
    Friday, and whatever Friday is running inside.

    Self and ancestors, deliberately not descendants. Killing this process or
    the shell that started it takes Friday down mid-answer; killing a child is
    often exactly the job - the music player Friday spawned is a child, and
    "stop the music" has to be able to reach it.

    The first version walked `children(recursive=True)` and would have made
    Friday unable to close anything it had started, which is a strange kind of
    safety: it protects the wrong direction of the tree.
    """
    mine = {os.getpid()}
    try:
        process = psutil.Process()
        for _ in range(8):          # a shell in a terminal in a runner is deep
            parent = process.parent()
            if parent is None:
                break
            mine.add(parent.pid)
            process = parent
    except psutil.Error:
        pass
    return mine


def identity(process: psutil.Process) -> dict:
    """
    Enough to know it is still the same process later.

    `created_at` is the part that does the work: pids are reused, creation
    times are not. A pid that comes back with a different creation time is a
    different program wearing the same number.
    """
    with process.oneshot():
        try:
            path = process.exe()
        except (psutil.AccessDenied, psutil.ZombieProcess, OSError):
            path = ""
        return {"pid": process.pid, "name": process.name(),
                "created_at": process.create_time(), "image_path": path}


def token(described: dict) -> str:
    """A stable handle for one process, for confirmations to be bound to."""
    return f"{described['name']}#{described['pid']}@{described['created_at']:.0f}"


def still_the_same(described: dict) -> psutil.Process | None:
    """
    The process this identity named, or None if it is gone or replaced.

    Checked immediately before acting, never trusted from when it was
    resolved. Between a question and its answer a pid can be released and
    handed to something else, and that gap is exactly where the confirmation
    is sitting.
    """
    try:
        process = psutil.Process(described["pid"])
        if abs(process.create_time() - described["created_at"]) > 0.01:
            return None
        return process
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return None


def guard(described: dict) -> None:
    """Refuse the things that are never legitimate targets."""
    if described["pid"] in CRITICAL_PIDS:
        raise Protected(f"pid {described['pid']} is the kernel, not a program")
    if described["name"].lower() in CRITICAL_NAMES:
        raise Protected(
            f"{described['name']} is a Windows system process - ending it "
            f"does not close an application, it breaks the machine")
    if described["pid"] in own_chain():
        raise Protected(
            f"{described['name']} (pid {described['pid']}) is Friday "
            f"itself, or what Friday is running inside - ending it would end "
            f"this conversation")


def find(pattern: str, *, include_protected: bool = False) -> list[dict]:
    """
    Processes whose name or pid matches, case-insensitively.

    Protected ones are excluded by default so they are never offered as
    candidates - but `include_protected` exists because hiding them made
    Friday lie. Asked to terminate `csrss.exe`, it answered "nothing running
    matches", which is false: csrss.exe is running, and the true answer is
    that it is a system process and will not be touched. "It does not exist"
    and "I will not do that" are different sentences and only one of them is
    honest.
    """
    needle = (pattern or "").strip().lower()
    if not needle:
        return []
    found = []
    for process in psutil.process_iter(["pid", "name", "create_time"]):
        try:
            name = (process.info["name"] or "").lower()
            if needle not in name and needle != str(process.info["pid"]):
                continue
            described = identity(process)
            try:
                guard(described)
            except Protected as exc:
                if not include_protected:
                    continue
                found.append({**described, "protected": True,
                              "protected_reason": str(exc)})
                continue
            found.append({**described, "protected": False})
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return found


def _one(pattern: str) -> dict:
    matches = find(pattern)
    if not matches:
        # Before saying it does not exist, check whether it exists and is
        # simply not a legitimate target. The two answers are not the same.
        protected = [m for m in find(pattern, include_protected=True)
                     if m.get("protected")]
        if protected:
            raise Protected(protected[0]["protected_reason"])
        raise ProcessError(f"nothing running matches {pattern!r}")
    names = {m["name"] for m in matches}
    if len(matches) > 1 and len(names) > 1:
        raise ProcessError(
            f"{len(matches)} different programs match {pattern!r} - say "
            f"which: {sorted(names)}")
    if len(matches) > 1:
        # Several copies of one program - a browser with its renderers. Say so
        # rather than picking, because "close Chrome" meaning all of them is a
        # different instruction from meaning one.
        raise ProcessError(
            f"{len(matches)} copies of {matches[0]['name']} are running "
            f"(pids {sorted(m['pid'] for m in matches)}) - say which pid, or "
            f"close its window instead")
    return matches[0]


# ---------------------------------------------------------------------------
# Looking
# ---------------------------------------------------------------------------


def processes_find(
    run: c.Run, pattern: str, *, engine: PolicyEngine = default_engine
) -> c.ActionResult:
    """Which processes match, with the identity needed to act on one."""
    tool_id = "process.find"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    matches = find(pattern)
    return run.record(c.succeeded(
        started,
        output=_scoped({"matches": [{**m, "token": token(m)} for m in matches],
                        "count": len(matches)}),
        verification=c.Verification(
            method="process_enumeration",
            evidence=f"{len(matches)} process(es) match {pattern!r}: "
                     + ", ".join(f"{m['name']}#{m['pid']}"
                                 for m in matches[:5]) or "none"),
    ))


# ---------------------------------------------------------------------------
# Ending one
# ---------------------------------------------------------------------------


def with_descendants(pid: int) -> set[int]:
    """
    This process and everything under it - as CANDIDATES, not as "the app".

    Necessary because the window is very often not owned by the process that
    was launched. Measured here: `Popen([python, "-c", ...])` returned pid
    5496 while the tkinter window belonged to 11808, because the venv's
    python.exe is a trampoline that re-execs the real interpreter. Chromium
    and Electron applications split themselves on purpose.

    Deliberately not treated as ownership. A program can launch something
    entirely unrelated - a text editor opening a browser - and its windows are
    not the editor's to close. So this narrows *where to look*, and
    `windows_of` decides what actually belongs by asking Windows who created
    each window.
    """
    family = {pid}
    try:
        for child in psutil.Process(pid).children(recursive=True):
            family.add(child.pid)
    except psutil.Error:
        pass
    return family


def windows_of(pid: int, *, image_path: str = "") -> list[dict]:
    """
    The visible top-level windows this application owns.

    Ownership comes from `GetWindowThreadProcessId`, which answers which
    process actually created a window - not from the title, which is what the
    Notepad incident mistook for ownership.

    `image_path` is accepted and deliberately NOT used to narrow. The obvious
    filter - "only descendants running the same executable" - was tried and
    breaks the exact case that made descendants necessary: a trampoline
    re-execs a *different* binary, so the launcher was `.venv/Scripts/
    python.exe` while the window belonged to `uv/python/.../python.exe`, and
    matching on image excluded the only real answer.

    The residual risk is a program that launched something unrelated, whose
    windows would also be asked to close. WM_CLOSE is the reason that is
    tolerable rather than dangerous: an unrelated application receives a
    polite request and is free to prompt or refuse, exactly as if somebody had
    clicked its close button. Every window asked is reported, so what happened
    is visible rather than inferred.
    """
    found = []
    family = with_descendants(pid)
    for hwnd, owner in native.top_level_windows():
        if owner in family:
            found.append({"hwnd": hwnd, "owner_pid": owner,
                          "title": native.window_title(hwnd)})
    return found


def ask_windows_to_close(windows: list[dict]) -> int:
    """
    Post WM_CLOSE to each window. Returns how many accepted the message.

    Posting rather than sending: a hung application would block a synchronous
    send indefinitely, and the entire point of asking politely is being able
    to notice that it did not answer.
    """
    asked = 0
    for window in windows:
        if native.PostMessageW(window["hwnd"], native.WM_CLOSE, 0, 0):
            asked += 1
    return asked


def _wait_for_exit(described: dict, seconds: float) -> bool:
    """True when the process this identity named is gone."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if still_the_same(described) is None:
            return True
        time.sleep(0.15)
    return still_the_same(described) is None


def processes_close(
    run: c.Run, pattern: str, *, engine: PolicyEngine = default_engine
) -> c.ActionResult:
    """
    Ask an application to close, the way clicking its X does.

    This is what "close Notepad" means. The application receives the request
    and may put up a save prompt, or decline - and a refusal is a PARTIAL
    result rather than a failure, because nothing went wrong: it is waiting
    for somebody.
    """
    tool_id = "process.close"
    blocked = _gate(run, tool_id, engine)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    try:
        described = _one(pattern)
    except Protected as exc:
        return run.record(c.failed(started, str(exc)))
    except ProcessError as exc:
        return run.record(c.failed(started, str(exc)))

    process = still_the_same(described)
    if process is None:
        return run.record(c.failed(
            started, f"{described['name']} exited before it could be closed"))

    # WM_CLOSE, posted to the windows this process owns.
    #
    # Two wrong answers were tried before this one. `psutil.terminate()` says
    # in its own docstring "On Windows this is an alias for kill()" - it calls
    # TerminateProcess, so the graceful path would have been the force path
    # wearing a gentler name. And `taskkill` without /F is not documented as
    # equivalent to pressing the close button; only /F is documented, as the
    # forceful one. Neither is the thing an application is built to receive.
    #
    # WM_CLOSE is. Microsoft documents it as the message that gives an
    # application the chance to prompt before its window is destroyed, which
    # is exactly the difference between "close this" and "kill this".
    windows = windows_of(described["pid"],
                         image_path=described.get("image_path", ""))
    asked = ask_windows_to_close(windows)
    if not windows:
        # No visible window to ask. A background service or a console process
        # has nothing to receive WM_CLOSE, and inventing a force kill here
        # would be the substitution this whole function exists to refuse.
        return run.record(c.partial(
            started,
            f"{described['name']} has no window to close - it is a background "
            f"process. Ending it means terminating it, which needs saying so "
            f"explicitly.",
            output=_scoped({"target": described, "closed": False,
                            "windows": 0, "token": token(described),
                            "force_would_need_confirmation": True})))

    if _wait_for_exit(described, CLOSE_SECONDS):
        return run.record(c.succeeded(
            started,
            output=_scoped({"target": described, "closed": True,
                            "windows_asked": asked, "method": "WM_CLOSE",
                            "windows": [{"pid": w["owner_pid"],
                                         "title": w["title"]}
                                        for w in windows]}),
            side_effects=(f"closed {described['name']}#{described['pid']}",),
            verification=c.Verification(
                method="process_absent_after_wm_close",
                evidence=f"{described['name']}#{described['pid']} received "
                         f"WM_CLOSE on {asked} window(s) and is gone"),
        ))

    # Still there. Very often that is the application doing its job: a save
    # prompt is a window that appeared *because* the close was received.
    now_open = windows_of(described["pid"],
                          image_path=described.get("image_path", ""))
    prompting = len(now_open) > len(windows)
    return run.record(c.partial(
        started,
        (f"{described['name']} received the close request and put something "
         f"up - it is probably asking whether to save."
         if prompting else
         f"{described['name']} was asked to close and is still running after "
         f"{CLOSE_SECONDS:.0f}s.")
        + " Forcing it would discard whatever is unsaved, and needs saying so "
          "explicitly.",
        output=_scoped({"target": described, "closed": False,
                        "windows_asked": asked,
                        "waiting_on_user": prompting,
                        "token": token(described),
                        "force_would_need_confirmation": True})))


# ---------------------------------------------------------------------------
# Ending one that will not go
# ---------------------------------------------------------------------------


FORCE_TERMINATE = "FORCE_TERMINATE"


def processes_terminate(
    run: c.Run, pattern: str, nonce: str = "", *,
    engine: PolicyEngine = default_engine,
    book: CF.Book | None = None,
) -> c.ActionResult:
    """
    End a process unconditionally, once somebody has said so about this one.

    TerminateProcess does not ask. Handlers do not run, buffers are not
    flushed, DLLs are not notified - Microsoft's own documentation reserves it
    for extreme circumstances. So it is never the implementation of "close
    this", and it needs a yes that names this exact process.

    Called without a nonce it does not terminate anything. It resolves the
    target, refuses it if it is protected, and returns the question to ask
    along with what the answer would authorise. Called with a nonce it
    revalidates the target *again* - because between the question and the
    answer a pid can be released and handed to something else, and that gap is
    exactly where the confirmation was sitting.
    """
    tool_id = "process.terminate"
    ledger = book if book is not None else CF.book
    started = c.started(run.run_id, tool_id)

    # Before resolving anything. A run whose objective came out of a page
    # cannot end a process at any authorisation level, so the refusal must not
    # depend on the target resolving first - otherwise an ambiguous pattern
    # answers "which one did you mean?" to an instruction Friday should not be
    # following at all.
    refusal = policy_module.provenance_verdict(tool_id, run.provenance)
    if refusal is not None:
        return run.record(c.failed(started, f"BLOCKED: {refusal.reason}"))

    try:
        described = _one(pattern)
    except Protected as exc:
        # Refused before anything is asked. There is no authorisation level at
        # which this becomes available, so offering to confirm it would be a
        # lie about what saying yes could do.
        return run.record(c.failed(started, f"BLOCKED: {exc}"))
    except ProcessError as exc:
        return run.record(c.failed(started, str(exc)))

    target = token(described)

    if not nonce:
        verdict = engine.decide(tool_id)
        if verdict.denied:
            return run.record(c.failed(
                started, f"BLOCKED: {verdict.reason}"))
        windows = windows_of(described["pid"])
        question = (f"Force {described['name']} (pid {described['pid']}) to "
                    f"close? Anything unsaved in it is lost.")
        pending = ledger.ask(run.run_id, FORCE_TERMINATE, target, question,
                             {"image_path": described["image_path"]})
        return run.record(c.started(run.run_id, tool_id).finish(
            status=c.CANCELLED,
            error=f"{APPROVAL_PREFIX}: {question}",
            output=_scoped({"confirm": pending.to_dict(), "target": described,
                            "open_windows": len(windows),
                            "unsaved_work_at_risk": bool(windows)}),
        ))

    spent = ledger.consume(nonce, run_id=run.run_id, action=FORCE_TERMINATE,
                           target=target,
                           arguments={"image_path": described["image_path"]})
    if not spent.ok:
        return run.record(c.failed(started, f"not confirmed: {spent.reason}"))

    # The target, again, now. Not the one resolved before the question.
    process = still_the_same(described)
    if process is None:
        return run.record(c.failed(
            started,
            f"TARGET_GONE: {described['name']}#{described['pid']} is no longer "
            f"the process that was confirmed - it exited, or that pid now "
            f"belongs to something else. Nothing was terminated."))

    try:
        process.kill()
    except psutil.NoSuchProcess:
        return run.record(c.succeeded(
            started,
            output=_scoped({"target": described, "terminated": True,
                            "method": "it exited on its own"}),
            verification=c.Verification(
                method="process_absent",
                evidence=f"{target} was already gone")))
    except psutil.AccessDenied as exc:
        return run.record(c.failed(
            started, f"Windows refused to terminate {described['name']}: "
                     f"{exc}. It is running with more privilege than Friday."))

    if not _wait_for_exit(described, TERMINATE_SECONDS):
        return run.record(c.partial(
            started,
            f"{described['name']} was terminated and is still listed after "
            f"{TERMINATE_SECONDS:.0f}s",
            output=_scoped({"target": described, "terminated": False})))

    return run.record(c.succeeded(
        started,
        output=_scoped({"target": described, "terminated": True,
                        "method": "TerminateProcess"}),
        side_effects=(f"force terminated {target}",),
        verification=c.Verification(
            method="process_absent_after_terminate",
            evidence=f"{target} is gone; the pid and creation time that were "
                     f"confirmed no longer resolve to a process"),
    ))
