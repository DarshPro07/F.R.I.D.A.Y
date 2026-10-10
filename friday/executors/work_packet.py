"""
friday/executors/work_packet.py -- what a CLI executor owes the manager.

PRD v7 section 46 ("CLI work packets and output contract") fixes one default
output shape for a coding worker, whichever CLI ran it. Each adapter returns a
`contracts.ActionResult` whose `output` is its own: the Claude Code executor
keeps `claude_says` and `changed_files`, the Hermes executor keeps `result`
and `work_run_id`. This module is the one place that reads those and produces
the section-46 dict, so the manager sees the same eight fields from either.

What is deliberately NOT here: a claim the adapter did not make. A field the
adapter cannot supply is empty, and `remaining_risk` says so; it is never
filled with a plausible value.
"""
from __future__ import annotations

import math
import os
import signal
import subprocess
import sys
from dataclasses import asdict, dataclass, field, replace
from pathlib import PurePosixPath, PureWindowsPath

from friday import contracts as c
from friday.executors.claude_code import TaskBundle

#: PRD v7 section 46, "Output default", in the order the PRD lists them.
OUTPUT_FIELDS = ("status", "result", "files_changed", "tests", "evidence",
                 "artifacts", "blockers", "remaining_risk")

#: "Full narratives go to artifacts, not the manager context."
RESULT_CHARS = 600

#: Where each adapter keeps what the worker said, most specific first.
_SUMMARY_KEYS = ("claude_says", "result")


def output_of(result: c.ActionResult) -> dict:
    """The section-46 output for one executor's terminal `ActionResult`."""
    out = result.output if isinstance(result.output, dict) else {}
    risk: list[str] = []

    summary = next((str(out[key]) for key in _SUMMARY_KEYS if out.get(key)), "")
    if len(summary) > RESULT_CHARS:
        summary = summary[:RESULT_CHARS]
        risk.append(f"the result was cut to {RESULT_CHARS} characters")

    blockers = [text for text in (result.error, out.get("pending_question")) if text]

    if result.verification is None:
        risk.append("no verification was recorded for this run")
    if "changed_files" not in out:
        risk.append("this adapter did not report which files changed")
    # No executor runs the tests itself; `DevelopmentRun.verify` owns that.
    risk.append("no tests were run by the executor")

    return {
        "status": result.status,
        "result": summary,
        "files_changed": [str(path) for path in out.get("changed_files") or ()],
        "tests": [],
        "evidence": [result.verification.evidence] if result.verification else [],
        "artifacts": [artifact.path_or_uri for artifact in result.artifacts],
        "blockers": blockers,
        "remaining_risk": risk,
    }


# ---------------------------------------------------------------------------
# FAB-002: one bounded packet, several runtimes, one receipt shape
# ---------------------------------------------------------------------------

#: Receipt statuses. UNAVAILABLE is a runtime that is not installed or not
#: signed in: it is reported as itself and never stood in for by another.
DONE = "DONE"
FAILED = "FAILED"
UNAVAILABLE = "UNAVAILABLE"
OVER_BUDGET = "OVER_BUDGET"


@dataclass(frozen=True)
class Budget:
    """What one runtime may spend on one packet. A signed-in CLI has no
    per-call price, so `usd=0.0` means "no paid call", not "no work"."""

    usd: float
    seconds: float

    def __post_init__(self) -> None:
        if not (math.isfinite(self.usd) and self.usd >= 0):
            raise ValueError(f"budget usd must be finite and >= 0, got {self.usd!r}")
        if not (math.isfinite(self.seconds) and self.seconds > 0):
            raise ValueError(f"budget seconds must be finite and > 0, got {self.seconds!r}")


def _relative(path: str) -> str:
    """`path` as a forward-slash relative path, or "" when it is not one."""
    text = str(path).strip().replace("\\", "/")
    if not text or PurePosixPath(text).is_absolute() or PureWindowsPath(text).drive:
        return ""
    parts = PurePosixPath(text).parts
    return "/".join(parts) if ".." not in parts else ""


@dataclass(frozen=True)
class WorkPacket:
    """The `TaskBundle` every adapter already takes (goal, allowed paths,
    constraints, acceptance), plus the two things it lacked: a budget, and the
    acceptance tests the manager runs itself, as argv lists (never a shell)."""

    bundle: TaskBundle
    budget: Budget
    checks: tuple[tuple[str, ...], ...]

    def __post_init__(self) -> None:
        if not self.bundle.goal.strip():
            raise ValueError("a work packet needs a goal")
        if not self.bundle.allowed_paths or not all(
                _relative(p) for p in self.bundle.allowed_paths):
            raise ValueError("a work packet needs allowed_paths: relative "
                             "files or directories inside the workspace")
        if not self.checks or not all(argv for argv in self.checks):
            raise ValueError("a work packet needs at least one check "
                             "(an acceptance test as an argv list)")

    def in_scope(self, path: str) -> bool:
        """Whether `path` is a listed file or under a listed directory."""
        norm = _relative(path)
        return bool(norm) and any(
            norm == scope or norm.startswith(scope + "/")
            for scope in (_relative(p).rstrip("/") for p in self.bundle.allowed_paths))

    def prompt(self) -> str:
        """The bundle's own prompt, with the scope and the checks stated as
        the constraint and the "done means" they are."""
        b = self.bundle
        return replace(
            b,
            constraints=(*b.constraints,
                         "Touch only these paths: " + ", ".join(b.allowed_paths)),
            acceptance=(*b.acceptance,
                        *(f"this command passes: {' '.join(argv)}"
                          for argv in self.checks)),
        ).prompt()


@dataclass
class Receipt:
    """What came back from one runtime for one packet. `status` is DONE only
    when the work was done AND the manager's own checks agreed."""

    runtime: str
    status: str
    model: str = ""
    result: str = ""
    cost_usd: float = 0.0
    usage: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    files_changed: list[str] = field(default_factory=list)
    #: One {"command", "passed", "output"} per check the manager ran.
    acceptance: list[dict] = field(default_factory=list)
    #: The isolated worktree the runtime worked in ("" once it was removed).
    workspace: str = ""
    #: The commit that carried the work onto the target; "" if it did not land.
    commit: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def run_cli(argv: list[str], *, prompt: str, cwd: str, timeout_s: float,
            worker: str) -> tuple[int, str, str]:
    """One coding-CLI process in `cwd`, the prompt on stdin, its environment
    the allowlisted worker environment. Returns (returncode, stdout, stderr).

    A run past `timeout_s` has its whole process tree killed and raises
    `TimeoutError`; a binary that cannot be launched raises `OSError`."""
    from friday import execution, stop

    posix = sys.platform != "win32"
    proc = subprocess.Popen(
        argv, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
        env=execution.worker_environment(worker), start_new_session=posix,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    stop.own_process(proc.pid)
    try:
        out, err = proc.communicate(prompt, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        if posix:
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True, timeout=30)
        try:
            proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            # A grandchild that escaped the kill can still hold the pipes; the
            # run is already being reported as timed out, so do not wait on it.
            pass
        raise TimeoutError(f"{worker} did not finish within {timeout_s:.0f}s") from None
    finally:
        stop.disown_process(proc.pid)
    return proc.returncode, out or "", err or ""
