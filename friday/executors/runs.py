"""
Development runs that outlive the process running them.

The Claude transcript is an accelerator, not the source of truth. Sessions are
kept locally and local files get cleaned up; if ADA's ability to continue
depends on one surviving, then the whole architecture is a nicer chat client
with extra steps.

So everything needed to start again lives in `executor_runs`: the task bundle
whole, the workspace, the session id, the last thing seen, and how it ended.
The recovery ladder falls back one rung at a time and only reaches the boss
when something is genuinely missing:

    1  the process is still alive           -> attach, do not start a second
    2  the saved Claude session resumes     -> --resume, continue the work
    3  the session is gone                  -> a fresh session from the bundle,
                                               the repository as it stands now,
                                               the accepted decisions, and the
                                               previous run's summary
    4  the bundle itself is missing         -> ask, because now something
                                               really is unknown

A process exiting 0 never means SUCCEEDED. That verdict comes from the
ActionResult, as it does everywhere else.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass

from friday.store import Store

logger = logging.getLogger("friday-agent.executors.runs")

STARTING = "STARTING"
RUNNING = "RUNNING"
WAITING_QUESTION = "WAITING_QUESTION"
WAITING_PERMISSION = "WAITING_PERMISSION"
INTERRUPTED = "INTERRUPTED"
RESUMING = "RESUMING"
SUCCEEDED = "SUCCEEDED"
FAILED = "FAILED"
CANCELLED = "CANCELLED"

STATUSES = (STARTING, RUNNING, WAITING_QUESTION, WAITING_PERMISSION,
            INTERRUPTED, RESUMING, SUCCEEDED, FAILED, CANCELLED)

#: A run in one of these is finished and will not change again.
TERMINAL = frozenset({SUCCEEDED, FAILED, CANCELLED})

#: A run in one of these believes it is live. If the process behind it is not,
#: it did not fail - it was interrupted, and that is a different thing with a
#: different answer.
LIVE = frozenset({STARTING, RUNNING, WAITING_QUESTION, WAITING_PERMISSION,
                  RESUMING})

#: Neither. This is the state the whole design exists to make useful: the
#: process is gone and the work is not finished, so it is waiting to be picked
#: back up. Folding it into TERMINAL would make a recoverable run look like a
#: dead one; folding it into LIVE would make it look like something is still
#: working on it.
RESUMABLE = frozenset({INTERRUPTED})


class DuplicateRun(RuntimeError):
    """A second executor was asked for while the first is still alive."""

    def __init__(self, run_id: str, pid: int):
        super().__init__(
            f"{run_id} already has a live executor (pid {pid}); "
            "one run means at most one process")
        self.run_id = run_id
        self.pid = pid


def process_started_at(pid: int | None) -> float | None:
    """When that pid started, or None if there is nothing there."""
    if not pid:
        return None
    try:
        import psutil

        return float(psutil.Process(int(pid)).create_time())
    except Exception:
        return None


#: Two clocks, one process. A second of slack costs nothing and avoids a
#: false negative from float rounding across a database round trip.
_START_TIME_TOLERANCE = 1.0


def process_alive(pid: int | None, started_at: float | None = None) -> bool:
    """
    Is that pid still *ours*?

    Checking the name was not enough, and the golden run proved it: after a
    hard kill the run still read as RUNNING, because this machine has plenty
    of node processes and a recycled pid matched `"node" in name`. A run that
    was killed then looked alive, which is precisely the state the whole
    recovery ladder exists to detect.

    So identity is the pid *and* when it started. Without a recorded start
    time the name check is all there is, and it is treated as the weak
    evidence it is.
    """
    if not pid:
        return False
    try:
        import psutil

        process = psutil.Process(int(pid))
        if not process.is_running() or process.status() == psutil.STATUS_ZOMBIE:
            return False
        if started_at is not None:
            return abs(float(process.create_time()) - float(started_at)) \
                <= _START_TIME_TOLERANCE
        name = (process.name() or "").lower()
        return "claude" in name or "node" in name
    except Exception:
        return False


@dataclass(frozen=True)
class Recovery:
    """What to do with a run that was found rather than started."""

    action: str          # attach | resume | restart | ask
    run: dict
    reason: str
    session_id: str = ""

    @property
    def can_continue(self) -> bool:
        return self.action in ("attach", "resume", "restart")


class RunManager:
    """Owns the executor_runs table, and the invariant that guards it."""

    def __init__(self, store: Store) -> None:
        self.store = store

    # -- lifecycle ---------------------------------------------------------

    def open(self, bundle, *, executor_type: str = "claude_code",
             worktree_path: str | None = None) -> None:
        existing = self.store.executor_run(bundle.run_id)
        if existing and existing["status"] in LIVE and self.is_live(existing):
            raise DuplicateRun(bundle.run_id, int(existing["pid"]))
        self.store.open_executor_run(
            bundle.run_id, executor_type=executor_type,
            working_directory=bundle.workspace, project=bundle.project,
            task_bundle=json.dumps(asdict(bundle)),
            worktree_path=worktree_path, status=STARTING)

    def running(self, run_id: str, *, pid: int | None, session_id: str = "") -> None:
        # The start time goes down with the pid. A pid on its own is not an
        # identity, and a run that reads as alive after being killed is worse
        # than one that reads as dead while running.
        fields: dict = {"status": RUNNING, "pid": pid,
                        "pid_started_at": process_started_at(pid)}
        if session_id:
            fields["session_id"] = session_id
        self.store.touch_executor_run(run_id, **fields)

    @staticmethod
    def is_live(run: dict) -> bool:
        """Is the process this row names still the one it meant?"""
        return process_alive(run.get("pid"), run.get("pid_started_at"))

    def saw(self, run_id: str, event: str, *, status: str | None = None) -> None:
        """Heartbeat. What it was doing, when it was last known to be doing it."""
        fields: dict = {"last_event": event[:400]}
        if status:
            fields["status"] = status
        try:
            self.store.touch_executor_run(run_id, **fields)
        except Exception:
            logger.exception("could not record executor progress")

    def close(self, run_id: str, result) -> None:
        """
        Record how it ended, from the ActionResult.

        Never from the exit code. A process that exits 0 having changed nothing
        has not succeeded; that judgement belongs to the contract.
        """
        from friday import contracts as c
        from friday.store import now_iso

        status = {
            c.SUCCEEDED: SUCCEEDED,
            c.FAILED: FAILED,
            c.CANCELLED: CANCELLED,
        }.get(result.status, INTERRUPTED if result.status == c.PARTIAL else FAILED)

        evidence = ""
        if result.verification is not None:
            evidence = f"{result.verification.method}: {result.verification.evidence}"

        self.store.touch_executor_run(
            run_id, status=status, pid=None, ended_at=now_iso(),
            summary=str((result.output or {}).get("claude_says", ""))[:2000],
            completion_evidence=evidence[:1000] or (result.error or "")[:1000])

    def cancelled(self, run_id: str) -> None:
        from friday.store import now_iso

        self.store.touch_executor_run(run_id, status=CANCELLED, pid=None,
                                      ended_at=now_iso())

    # -- recovery ----------------------------------------------------------

    def reconcile(self, run_id: str) -> dict | None:
        """
        Believe the world, not the row.

        A run that says RUNNING with a dead pid did not fail - nobody was there
        to write FAILED. It was interrupted, and INTERRUPTED is what invites a
        resume where FAILED invites a shrug.
        """
        run = self.store.executor_run(run_id)
        if run is None:
            return None
        if run["status"] in LIVE and not self.is_live(run):
            self.store.touch_executor_run(run_id, status=INTERRUPTED, pid=None)
            logger.info("%s was %s with a dead process; now INTERRUPTED",
                        run_id, run["status"])
            return self.store.executor_run(run_id)
        return run

    def reconcile_all(self) -> list[dict]:
        """Run at startup: every run that thinks it is live, checked."""
        out = []
        for row in self.store.executor_runs(limit=100):
            if row["status"] in LIVE:
                fixed = self.reconcile(row["run_id"])
                if fixed and fixed["status"] == INTERRUPTED:
                    out.append(fixed)
        return out

    def recover(self, run_id: str, *, session_exists=None) -> Recovery:
        """
        Which rung of the ladder this run lands on.

        `session_exists(session_id)` says whether Claude still has it; the
        default assumes it does, and a resume that fails falls through to a
        restart anyway.
        """
        run = self.reconcile(run_id)
        if run is None:
            return Recovery("ask", {}, f"no run recorded as {run_id}")

        if run["status"] in LIVE and self.is_live(run):
            return Recovery("attach", run,
                            f"pid {run['pid']} is still working on it")

        if run["status"] in TERMINAL:
            return Recovery("ask", run,
                            f"this run already ended as {run['status']}")

        if not run.get("task_bundle"):
            # The one case where the ladder genuinely runs out.
            return Recovery("ask", run,
                            "the task bundle was never recorded, so there is "
                            "nothing to continue from")

        session = str(run.get("session_id") or "")
        if session and (session_exists is None or session_exists(session)):
            return Recovery("resume", run,
                            f"session {session} can be picked back up",
                            session_id=session)

        return Recovery(
            "restart", run,
            "the Claude session is gone; starting a fresh one from the task "
            "bundle, the repository as it stands and what was already decided")

    # -- reading -----------------------------------------------------------

    def bundle_of(self, run: dict):
        """The task bundle back out of the row, whole."""
        from friday.executors.claude_code import TaskBundle

        data = json.loads(run["task_bundle"])
        for key in ("context", "acceptance", "constraints"):
            if key in data:
                data[key] = tuple(data[key])
        return TaskBundle(**data)

    def active(self, project: str = "") -> list[dict]:
        rows = self.store.executor_runs(project=project or None, limit=50)
        return [r for r in rows if r["status"] not in TERMINAL]

    def brief(self, project: str = "", *, limit: int = 3) -> str:
        """
        What a brand-new conversation needs to answer "how is that going?".

        Deliberately plain text: this is read into a voice turn, and the model
        should be able to say it out loud without translating a data structure.
        """
        rows = self.store.executor_runs(project=project or None, limit=limit)
        if not rows:
            return ""
        lines = ["[DEVELOPMENT WORK ADA IS RUNNING OR HAS RUN]"]
        for row in rows:
            lines.append(
                f"  {row['run_id']} [{row['status']}] "
                f"{'on ' + row['project'] if row['project'] else ''}".rstrip())
            try:
                goal = json.loads(row["task_bundle"]).get("goal", "")
            except Exception:
                goal = ""
            if goal:
                lines.append(f"    goal: {goal[:160]}")
            if row.get("last_event"):
                lines.append(f"    last: {row['last_event'][:120]}")
            if row.get("completion_evidence"):
                lines.append(f"    evidence: {row['completion_evidence'][:160]}")
        return "\n".join(lines)


def session_file_exists(session_id: str) -> bool:
    """
    Does Claude still hold this session locally?

    Best effort by design. Session storage is Claude's business and its layout
    is not ADA's to depend on, so "cannot tell" answers True and a failed
    --resume falls through to a restart, which was always the safe direction.
    """
    if not session_id:
        return False
    root = os.path.expanduser("~/.claude/projects")
    if not os.path.isdir(root):
        return True
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if session_id in name:
                return True
    return False
