#!/usr/bin/env python3
"""
Closing and ending programs, against real windows this script created.

The unit tests use fixtures because the suite runs constantly and a test run
that kills the boss's editor every time is a test run that gets disabled. This
is the other half: real windows, real WM_CLOSE, real TerminateProcess, real
read-back - and it touches nothing it did not create.

That rule is not decoration. An earlier live gate launched `notepad.exe`,
matched a window by title, and moved somebody's open file around, because
modern Notepad can hand a launch off to a process that is already running. The
durable lesson was never "Notepad is single-instance" - it is that launching an
application does not prove the returned pid, or any window matching its name,
belongs to this run. So every fixture here has a title nothing else on earth
shares, and the script refuses to start if one already exists.

    python scripts/golden_processes.py
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from friday import confirmation as CF  # noqa: E402
from friday import contracts as c  # noqa: E402
from friday.toolsets import processes as P  # noqa: E402

#: Titles nothing else can share.
COOPERATIVE = "friday-gate-cooperative-8b21f4"
STUBBORN = "friday-gate-stubborn-8b21f4"
CHILD = "friday-gate-child-8b21f4"
BYSTANDER = "friday-gate-bystander-8b21f4"

WINDOW = """
import tkinter
root = tkinter.Tk()
root.title({title!r})
root.geometry("360x140+{x}+120")
tkinter.Label(root, text={title!r}).pack(expand=True)
{refuse}
root.mainloop()
"""

REFUSE = 'root.protocol("WM_DELETE_WINDOW", lambda: None)'

#: A launcher that re-execs a *different* interpreter, which then owns the
#: window, and then exits - the venv trampoline case that produced a real bug
#: (pid 5496 launched, pid 11808 owned the window). It also starts an
#: unrelated child that must survive.
LAUNCHER = """
import subprocess, sys, time
child = subprocess.Popen([sys.executable, "-c", {window!r}])
bystander = subprocess.Popen([sys.executable, "-c",
                              "import time; time.sleep(120)"])
print(child.pid, bystander.pid, flush=True)
time.sleep(1.0)
"""


def check(passed: bool, message: str, detail: str = "") -> bool:
    print(f"  [{'PASS' if passed else 'FAIL'}] {message}")
    if detail:
        print(f"         {detail}")
    return bool(passed)


def run_for(label: str) -> c.Run:
    return c.Run.create(label, capability="system")


def spawn(title: str, *, refuse: bool = False, x: int = 120):
    source = WINDOW.format(title=title, refuse=REFUSE if refuse else "", x=x)
    process = subprocess.Popen([sys.executable, "-c", source])
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        if P.windows_of(process.pid):
            return process
        time.sleep(0.2)
    return process


def cleanup(processes) -> None:
    for process in processes:
        try:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
        except Exception:                               # noqa: BLE001
            pass


def cooperative_and_stubborn(results: list[bool], started: list) -> None:
    print("\n-- an application that closes, and one that will not --")

    friendly = spawn(COOPERATIVE)
    started.append(friendly)
    result = P.processes_close(run_for("close it"), str(friendly.pid))
    results.append(check(
        result.status == c.SUCCEEDED and friendly.poll() is not None,
        "a cooperative application closes, and is observed gone",
        result.verification.evidence if result.verification else result.error))

    difficult = spawn(STUBBORN, refuse=True, x=520)
    started.append(difficult)
    result = P.processes_close(run_for("close it"), str(difficult.pid))
    results.append(check(
        result.status == c.PARTIAL
        and not result.may_claim_completion
        and difficult.poll() is None,
        "one that refuses stays running, and is not claimed as closed",
        f"status={result.status} may_claim={result.may_claim_completion}"))
    results.append(check(
        bool((result.output or {}).get("force_would_need_confirmation")),
        "and the result says forcing it would need a yes"))


def force_path(results: list[bool], started: list) -> None:
    print("\n-- ending one, which needs a yes about this exact one --")

    book = CF.Book()
    target = spawn(STUBBORN + "-force", refuse=True, x=520)
    started.append(target)
    run = run_for("force close it")

    asked = P.processes_terminate(run, str(target.pid), book=book)
    results.append(check(
        asked.status == c.CANCELLED and target.poll() is None,
        "without a yes, nothing is ended",
        asked.error[:90]))

    elsewhere = book.ask(run.run_id, "RESTART_MACHINE", "LOCAL_MACHINE", "?")
    book.approve(elsewhere.nonce)
    wrong = P.processes_terminate(run, str(target.pid), elsewhere.nonce,
                                  book=book)
    results.append(check(
        wrong.status == c.FAILED and target.poll() is None,
        "a yes given for a restart cannot end a process",
        wrong.error[:90]))

    nonce = asked.output["confirm"]["nonce"]
    book.approve(nonce)
    right = P.processes_terminate(run, str(target.pid), nonce, book=book)
    results.append(check(
        right.status == c.SUCCEEDED and target.poll() is not None,
        "the right yes ends exactly that process",
        right.verification.evidence if right.verification else right.error))

    protected = P.processes_terminate(run_for("kill it"), "csrss.exe",
                                      book=book)
    results.append(check(
        protected.status == c.FAILED
        and "system process" in (protected.error or ""),
        "a protected process is refused, and told the truth about why",
        protected.error[:90]))
    results.append(check(
        "nothing running matches" not in (protected.error or ""),
        "and is not reported as though it does not exist"))


def ownership(results: list[bool], started: list) -> None:
    """
    Launcher A re-execs child B, B owns the window, A exits, C is unrelated.

    The case that produced a real bug: the pid a launch returns is not the pid
    that owns the window, because the venv python.exe is a trampoline that
    re-execs a different interpreter. Chromium and Electron do the same thing
    deliberately.
    """
    print("\n-- the window's owner is not the pid that was launched --")

    source = WINDOW.format(title=CHILD, refuse="", x=120)
    launcher = subprocess.Popen(
        [sys.executable, "-c", LAUNCHER.format(window=source)],
        stdout=subprocess.PIPE, text=True)
    started.append(launcher)

    line = launcher.stdout.readline().strip()
    child_pid, bystander_pid = (int(value) for value in line.split())
    launcher.wait(timeout=15)

    deadline = time.monotonic() + 12
    found: list[dict] = []
    while time.monotonic() < deadline and not found:
        found = [w for w in P.windows_of(child_pid)
                 if w["title"] == CHILD]
        time.sleep(0.2)

    results.append(check(
        bool(found),
        "the window is found on the process that owns it, not the launcher",
        f"launcher {launcher.pid} exited; window owned by {child_pid}"))
    results.append(check(
        launcher.poll() is not None,
        "and the launcher has already exited, as a trampoline does"))

    import psutil

    book = CF.Book()
    run = run_for("force close it")
    asked = P.processes_terminate(run, str(child_pid), book=book)
    results.append(check(
        str(child_pid) in (asked.error or "") or
        str(child_pid) in str((asked.output or {}).get("target", {})),
        "the confirmation names the process that owns the window",
        asked.error[:90]))

    nonce = asked.output["confirm"]["nonce"]
    book.approve(nonce)
    ended = P.processes_terminate(run, str(child_pid), nonce, book=book)

    results.append(check(
        ended.status == c.SUCCEEDED and not psutil.pid_exists(child_pid),
        "the window's owner is ended"))
    results.append(check(
        psutil.pid_exists(bystander_pid),
        "and the unrelated sibling is untouched",
        f"bystander {bystander_pid} still running"))

    try:
        psutil.Process(bystander_pid).kill()
    except psutil.Error:
        pass


def main() -> int:
    print("=" * 70)
    print("Closing and ending, on windows this script created")
    print("=" * 70)

    for title in (COOPERATIVE, STUBBORN, CHILD, BYSTANDER):
        if P.find(title):
            print(f"  something called {title!r} already exists, which should "
                  f"be impossible - refusing rather than acting on it")
            return 2

    results: list[bool] = []
    started: list = []
    try:
        cooperative_and_stubborn(results, started)
        force_path(results, started)
        ownership(results, started)
    finally:
        cleanup(started)
        print("\n  (everything this script opened has been closed again)")

    passed = sum(1 for r in results if r)
    print("\n" + "=" * 70)
    print(f"RESULT: {passed}/{len(results)} behaved correctly")
    print("=" * 70)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
