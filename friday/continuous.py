"""
The continuous execution engine: lease-protected task execution, a driver
loop, and the orphan watchdog.

Progression is deterministic and never depends on a user message. The
executor acquires a generation-bumped lease on the run row, executes a
bounded portion of ready tasks by calling capabilities directly (no LLM in
the loop), persists every transition plus a trace event, computes the next
wake, and releases.

Every executor also runs a background driver loop that picks up runs whose
scheduled `next_wake` is due and whose lease is free, so a run continues
across tool boundaries without any user input. If the process dies mid-wake
the lease goes stale and the watchdog reconciles the run exactly once:
interrupt the dead portion, drive one round, and schedule one continuation
which completes the rest.

The invariant enforced after every mutation:

    NON_TERMINAL_RUN_HAS_FUTURE  =  non-terminal run has a live lease OR a
                                   scheduled next_wake OR a legitimate
                                   WAITING_QUESTION / WAITING_PERMISSION.

The forbidden state is RUNNING with no owner and no scheduled continuation.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import socket
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Sequence

from friday import contracts as c
from friday import objective_budget as budget_module

from friday.contracts import now_iso
from friday import objectives as O
from friday import signals
from friday import trajectory
from friday import waves
from friday.store import Store

logger = logging.getLogger("friday-continuous")

#: How long a lease may go without being refreshed before it is stale.
DEFAULT_LEASE_TIMEOUT = float(os.getenv("ADA_LEASE_TIMEOUT", "90"))
#: How often the watchdog sweeps for orphaned runs.
DEFAULT_WATCHDOG_INTERVAL = float(os.getenv("ADA_WATCHDOG_INTERVAL", "15"))
#: Max tasks executed per wake; bounds how long the session appears blocked.
DEFAULT_PORTION_BUDGET = int(os.getenv("ADA_PORTION_BUDGET", "8"))
#: TRANSIENT retry cap.
DEFAULT_MAX_ATTEMPTS = int(os.getenv("ADA_MAX_ATTEMPTS", "3"))
#: How often the background driver loop checks for due wakes.
DRIVER_INTERVAL = 0.05
#: Wake delay after a budget boundary; short so the driver resumes promptly.
WAKE_SECONDS = 0.5
#: Watchdog continuation delay; lets the test observe the reconciled state.
CONTINUATION_DELAY = 0.02


def _as_naive(iso: str) -> datetime:
    """Parse an ISO timestamp; aware timestamps are converted to naive local.

    The engine and its tests write naive-local ``datetime.now()`` ISO
    timestamps, while ``now_iso()`` (created_at, retry wakes) is UTC-aware.
    Comparisons must always be done in one wall-clock.
    """
    parsed = datetime.fromisoformat(iso)
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed


def _due(iso: str | None) -> bool:
    """True when the wake time has arrived (missing/unparseable = due)."""
    if not iso:
        return True
    try:
        return _as_naive(iso) <= datetime.now()
    except (ValueError, TypeError):
        return True


def _fresh(iso: str) -> bool:
    try:
        return _as_naive(iso) > datetime.now()
    except (ValueError, TypeError):
        return False


class InvariantViolation(RuntimeError):
    """A non-terminal run has no owner, no wake, and no legitimate wait."""


class FailedToAcquire(RuntimeError):
    """Another executor owns the run with a fresh lease."""


CapabilityPort = Callable[[str, dict], Awaitable[dict]]

#: Why a run from before execution envelopes does not run (PRD v7 SEC-005).
NO_ENVELOPE_BLOCKER = (
    "no execution envelope: this objective was written before envelopes, or "
    "its envelope cannot be read, so it waits for the person to re-issue it "
    "rather than running under an assumed scope")


def _takes_run(port) -> bool:
    """Whether a capability port accepts the task's Run as `run=`."""
    return _takes(port, "run")


def _takes(port, name: str) -> bool:
    import inspect

    try:
        return name in inspect.signature(port).parameters
    except (TypeError, ValueError):
        return False


#: How long to leave a provider alone after it says it cannot answer.
#:
#: Longer than an ordinary retry on purpose. A rate-limited or overloaded
#: model asked again immediately is asked again for nothing, and the attempt
#: budget is spent inside a few seconds of an outage that lasts minutes.
PROVIDER_BACKOFF_SECONDS = float(os.getenv("ADA_PROVIDER_BACKOFF", "20"))

#: How long a task the governor would not admit a worker for is left alone.
#: Each refusal queues an entry in the governor, so asking again at once only
#: fills its queue; the machine frees a worker in seconds, not milliseconds.
ADMISSION_BACKOFF_SECONDS = 5.0

#: How many times a task may change strategy (re-plan, different role,
#: reduce scope) before a recurring failure gives up and goes BLOCKED
#: instead of retrying the same thing forever. The number lives with the
#: objective budget so the run-level replans cap is defined in terms of it.
MAX_STRATEGY_CHANGES = budget_module.MAX_STRATEGY_CHANGES
STRATEGY_HINTS = ("replan", "different_role", "reduce")

_FP_PATH_RE = re.compile(r"(?:[A-Za-z]:)?[\\/][\w.\-\\/]+")
_FP_ID_RE = re.compile(r"\b[0-9a-fA-F]{8,}\b")
_FP_NUM_RE = re.compile(r"\d+")


def _normalize_failure_text(text: str) -> str:
    """Strip the parts of an error message that change every run (paths,
    generated ids, line numbers) so the same failure fingerprints the same."""
    text = _FP_PATH_RE.sub("<path>", text)
    text = _FP_ID_RE.sub("<id>", text)
    text = _FP_NUM_RE.sub("<n>", text)
    return text.strip()


def failure_fingerprint(kind: str, error: str, verifier: str = "",
                        task_id: str = "") -> str:
    """
    A stable identity for "the same failure happened again". Two attempts
    that differ only by a temp path, a generated id or a line number are the
    same failure - the strategy-change guard below treats them that way
    instead of burning the attempt budget on a blind retry.
    """
    error_class = str(error).split(":", 1)[0].strip()
    normalized = _normalize_failure_text(str(error))
    payload = "|".join([kind or "", error_class, normalized,
                        verifier or "", task_id or ""])
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _in_seconds(seconds: float) -> str:
    """A wake time, `seconds` from now. Zero means due immediately."""
    if seconds <= 0:
        return now_iso()
    return (datetime.now() + timedelta(seconds=seconds)).isoformat()


def _looks_like_a_provider(exc: Exception) -> bool:
    """Whether this came from a model provider rather than from a capability."""
    name = type(exc).__name__
    if name in ("APIStatusError", "APIConnectionError", "APITimeoutError",
                "ClientError", "ServerError"):
        return True
    text = f"{name}: {exc}".lower()
    return ("llm" in text or "gemini" in text or "provider" in text
            or "all llms" in text)


#: Refusal statuses an ActionResult can carry, mapped to failure kinds.
#: Recovered verbatim from the CPython 3.11.15 bytecode oracle.
_REFUSED_AS = {
    c.FAILED: O.FailureKind.STRUCTURAL,
    c.CANCELLED: O.FailureKind.USER_REQUIRED,
    c.NOT_PERMITTED: O.FailureKind.POLICY_BLOCK,
    c.NOT_CONFIGURED: O.FailureKind.NOT_CONFIGURED,
    c.UNSUPPORTED: O.FailureKind.NOT_CONFIGURED,
    c.NOT_CARRIED_OUT: O.FailureKind.STRUCTURAL,
}

#: A terminal Hermes WorkRun status that means the delegated work failed.
_WORKRUN_REFUSED_AS = {"FAILED": O.FailureKind.STRUCTURAL}


def _refused(result) -> tuple[str, str] | None:
    """(kind, reason) if this result says it did not work, else None.

    PARTIAL and OBSERVED are deliberately not failures: partial work happened
    and an observation is the whole point of a read. They are not claimable
    completions either, which is a distinction the ActionResult carries into
    the row and the summary reads back.
    """
    if not isinstance(result, dict):
        return None
    status = result.get("status")
    if not status:
        for envelope_key in ("result", "run"):
            inner = result.get(envelope_key)
            if isinstance(inner, dict) and inner.get("status"):
                result = inner
                status = inner.get("status")
                break
    if not status:
        return None
    kind = _REFUSED_AS.get(status) or _WORKRUN_REFUSED_AS.get(status)
    if kind is None:
        return None
    reason = (result.get("error") or result.get("result")
              or f"{result.get('tool_id') or 'the capability'} returned "
                 f"{status}")
    reason = str(reason)
    if _is_auth(reason):
        return (O.FailureKind.USER_REQUIRED, reason)
    if kind == O.FailureKind.NOT_CONFIGURED and _is_connectivity(reason):
        return (O.FailureKind.CONNECTIVITY, reason)
    return (kind, reason)


#: RC1.1: a LINK being down reads as NOT_CONFIGURED unless these appear.
_CONNECTIVITY_SIGNS = ("bridge", "gateway", "disconnected",
                       "not connected", "connection", "unreachable",
                       "sse", "socket", "refused")


def _is_connectivity(reason: str) -> bool:
    lowered = reason.lower()
    return any(sign in lowered for sign in _CONNECTIVITY_SIGNS)


#: RC1.1: auth is checked BEFORE connectivity - a login wall is not an
#: outage, and retrying it forever is the wrong answer.
_AUTH_SIGNS = ("credentials", "api key", "api_key", "authenticate",
               "login", "/login", "setup-token", "unauthorized",
               "not logged in", "auth")


def _is_auth(reason: str) -> bool:
    lowered = reason.lower()
    return any(sign in lowered for sign in _AUTH_SIGNS)


#: The policy engine's refusal prefix (toolsets/system.APPROVAL_PREFIX and
#: capability_runtime): the capability was NOT run because a person has to
#: say yes to this exact action first (PRD FR-060). It is a boundary, not a
#: failure - the objective parks at WAITING_PERMISSION with the action it
#: needs approved recorded on the run, and resumes when that approval is
#: granted (`resume_after_approval`).
_APPROVAL_SIGNS = ("approval_required", "approval required", "needs approval",
                   "requires confirmation", "confirm_required")


def _is_approval(reason: str) -> bool:
    lowered = reason.lower()
    return any(sign in lowered for sign in _APPROVAL_SIGNS)


def _working_worker(result) -> bool:
    """Whether a capability submitted durable work that is not done yet."""
    return (isinstance(result, dict)
            and str(result.get("status") or "").lower() == "working"
            and bool(result.get("work_run_id")))


def _pending_worker_id(task: dict) -> str:
    """The WorkRun identity persisted on a WAITING objective task."""
    if task.get("status") != O.TaskStatus.WAITING:
        return ""
    result = task.get("result") or {}
    return (str(result.get("work_run_id") or "")
            if isinstance(result, dict) else "")


def _task_surface(task: dict) -> frozenset[str]:
    """The files a task declared it touches (compile keeps them in its detail,
    ORCH-001). Empty means unknown, and an unknown task runs alone."""
    detail = task.get("detail")
    return waves.surface_of(
        detail.get("surface") if isinstance(detail, dict) else None)


def _committed_result(store, run_id: str, task_id: str):
    """The result a side-effecting call on this task's latest attempt
    committed (DUR-001), or None if that attempt committed nothing."""
    events = [e for e in store.recent_objective_events(run_id, limit=2000)
              if e.get("task_id") == task_id]
    started = max((i for i, e in enumerate(events)
                   if e.get("event") == O.EVENT_TASK_STARTED), default=-1)
    for event in reversed(events[started + 1:]):
        if event.get("event") == O.EVENT_SIDE_EFFECT_COMMITTED:
            detail = event.get("detail") if isinstance(event.get("detail"), dict) else {}
            return detail.get("result")
    return None


def _worker_status(observed: dict) -> tuple[str, dict]:
    """Normalise hermes_status and direct test shapes."""
    if not isinstance(observed, dict):
        return ("unknown", {"observed": observed})
    run = observed.get("run")
    payload = run if isinstance(run, dict) else observed
    status = str(payload.get("status") or "").lower()
    return (status, payload)


class FailureClassifier:
    """Exception -> failure kind. A capability-missing tool is never re-called."""

    @staticmethod
    def classify(exc: Exception) -> str:
        from friday.policy import PolicyError

        if isinstance(exc, LookupError):
            return O.FailureKind.CAPABILITY_MISSING
        if isinstance(exc, PolicyError):
            return O.FailureKind.POLICY_BLOCK
        if isinstance(exc, (ValueError, TypeError)):
            return O.FailureKind.INVALID_ARGUMENT
        if isinstance(exc, NotImplementedError):
            return O.FailureKind.NOT_CONFIGURED
        if isinstance(exc, TimeoutError):
            return O.FailureKind.TRANSIENT
        # A dropped link (refused, reset, aborted, broken pipe, DNS gone) is
        # not a broken task: it used to land on STRUCTURAL, which is not
        # retryable, so one network blip failed the task for good and skipped
        # everything downstream of it. CONNECTIVITY is retried, within budget.
        if isinstance(exc, (ConnectionError, socket.gaierror, socket.herror)):
            return O.FailureKind.CONNECTIVITY

        # A model provider that could not answer is not a broken task.
        #
        # Everything below used to land on STRUCTURAL, which is not
        # retryable - so a Google 503, a 429 under load, and "all LLMs are
        # unavailable" each marked their task permanently failed and skipped
        # every dependent. A capacity blip lasting seconds would take out a
        # branch of the run for good. Measured: all four provider conditions
        # classified STRUCTURAL, retryable=False.
        #
        # The distinction that matters is the one the provider already tells
        # us: 429 and 5xx are worth waiting for, and a malformed request -
        # a missing thought signature, a tool call where no tool was declared
        # - is not, because the second attempt sends the same impossible
        # request. See friday/provider_diagnostics.py.
        if _looks_like_a_provider(exc):
            from friday import provider_diagnostics as PD

            found = PD.diagnose(exc)
            if found.kind == PD.CAPPED:
                return O.FailureKind.CAPPED
            if found.worth_retrying:
                return O.FailureKind.PROVIDER_DOWN
            return O.FailureKind.STRUCTURAL

        return O.FailureKind.STRUCTURAL


class ContinuousTaskExecutor:
    """One executor per process identity; the lease is the only shared state."""

    def __init__(self, store: Store, call_capability: CapabilityPort,
                 executor_id: str | None = None,
                 health_probe=None, health_recover=None,
                 governor=None) -> None:
        self.store = store
        self.call_capability = call_capability
        #: Whether the port can carry each task's Run (lineage and envelope,
        #: TRUST-002). Both production ports can; a port that cannot - a test
        #: fake - still sees the envelope enforced here before dispatch.
        self._port_takes_run = _takes_run(call_capability)
        self._port_takes_key = _takes(call_capability, "idempotency_key")
        self.executor_id = executor_id or f"exec-{os.getpid()}"
        self.lease_timeout = DEFAULT_LEASE_TIMEOUT
        self.portion_budget = DEFAULT_PORTION_BUDGET
        self.max_attempts = DEFAULT_MAX_ATTEMPTS
        #: Optional layered-health seam (1A). `health_probe` returns a
        #: `hermes_health.Report`; probes that accept `active_workruns=` are
        #: given the live WorkRun ids so a dead-looking gateway can demand
        #: RECONCILE_THEN_RESTART instead of a blind restart. `health_recover`
        #: is an async callable Report -> bool that EXECUTES the derived
        #: repair (reconnect bridge, restart gateway via the existing hermes
        #: lifecycle, ...). Both optional: without them the CONNECTIVITY
        #: retry keeps its plain bounded-timer contract, so tests and the
        #: CLI need no health stack.
        self.health_probe = health_probe
        self.health_recover = health_recover
        #: ORCH-001: returns the governor that bounds how many of a run's
        #: tasks start together; None is the process-wide governor.
        self._governor_source = governor
        #: run_ids this process is currently driving; guards against the
        #: driver loop and an explicit start() double-driving one run.
        self._driving: set[str] = set()
        self._driver_task: asyncio.Task | None = None
        try:
            self._driver_task = asyncio.get_running_loop().create_task(
                self._driver_loop())
        except RuntimeError:
            # No *running* loop (sync context, e.g. a test fixture or a
            # constructor on the main thread): a loop may still be installed
            # and about to run, so schedule the driver on it.
            try:
                loop = asyncio.get_event_loop()
                if not loop.is_closed():
                    self._driver_task = loop.create_task(self._driver_loop())
            except RuntimeError:
                # No loop at all: start()/watchdog still work.
                self._driver_task = None

    def stop(self) -> None:
        """Cancel the background driver loop of THIS process.

        Durable runs are untouched: statuses, wakes and leases stay in the
        store, so a later executor - or the watchdog - resumes them. Used at
        process teardown so the loop cannot outlive its owner.
        """
        if self._driver_task is not None and not self._driver_task.done():
            self._driver_task.cancel()

    # -- lease --------------------------------------------------------------

    def acquire(self, run_id: str) -> bool:
        """
        Try to take ownership of the run. Generation-bumped single UPDATE:
        two writers cannot both win, which is what prevents the watchdog and
        a live executor from double-driving the same run.
        """
        now = now_iso()
        won = self.store.acquire_objective_lease(
            run_id, executor_id=self.executor_id,
            expiry=(datetime.now()
                    + timedelta(seconds=self.lease_timeout)).isoformat(),
        )
        if won:
            self.store.append_objective_event(
                run_id, O.EVENT_LEASE_ACQUIRED,
                detail={"executor_id": self.executor_id, "at": now})
        return won

    def release(self, run_id: str) -> None:
        self.store.release_objective_lease(run_id,
                                           executor_id=self.executor_id)

    def _heartbeat(self, run_id: str) -> None:
        self.store.touch_objective_run(
            run_id,
            lease_expiry=(datetime.now()
                          + timedelta(seconds=self.lease_timeout)).isoformat(),
        )

    # -- background driver --------------------------------------------------

    async def _driver_loop(self) -> None:
        while True:
            try:
                await self._driver_tick()
            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception("driver tick failed")
            await asyncio.sleep(DRIVER_INTERVAL)

    async def _driver_tick(self) -> None:
        """Drive runs whose next_wake is due and whose lease is free."""
        for run in self.store.objective_runs(limit=100):
            run_id = run["run_id"]
            if run["status"] in O.RUN_TERMINAL:
                continue
            if run["status"] in O.RUN_WAITING_STATUSES:
                continue
            if run_id in self._driving:
                continue
            if not run.get("next_wake"):
                # A run with no wake at all is the watchdog's job.
                continue
            if not _due(run["next_wake"]):
                continue
            if run.get("lease_executor_id") and run.get("lease_expiry") \
                    and _fresh(run["lease_expiry"]):
                continue
            self._driving.add(run_id)
            try:
                if self.acquire(run_id):
                    await self._drive_until_done(run_id)
            except Exception:
                logger.exception("driver failed while driving %s", run_id)
            finally:
                self._driving.discard(run_id)

    # -- main loop ----------------------------------------------------------

    async def start(self, run_id: str) -> bool:
        """
        Drive one bounded portion of the run: acquire the lease, execute
        ready tasks, cascade skips, schedule the next wake or finish the
        run. Returns False if ownership was lost (another writer is driving).
        """
        if run_id in self._driving:
            return True
        run = self.store.objective_run(run_id)
        if run is not None and run["status"] in O.RUN_WAITING_STATUSES:
            # A paused or question-blocked run is a legitimate wait: nobody
            # asked to drive it, and waking it would defeat the pause.
            return True
        if not self.acquire(run_id):
            return False
        self._driving.add(run_id)
        try:
            self.store.append_objective_event(
                run_id, O.EVENT_RUN_STARTED,
                detail={"executor_id": self.executor_id})
            await self._drive_until_done(run_id)
        except Exception:
            logger.exception("executor failed while driving %s", run_id)
            # Never leave a run stranded: give it a wake the driver can use.
            try:
                self._schedule_wake(run_id, seconds=1.0)
            except Exception:
                logger.exception("could not schedule recovery wake for %s",
                                 run_id)
            self.release(run_id)
            raise
        finally:
            self._driving.discard(run_id)
        return True

    async def _drive_until_done(self, run_id: str) -> None:
        """Drive rounds until the run is terminal or must wait for a wake."""
        executed = 0
        try:
            while True:
                run = self.store.objective_run(run_id)
                if run is None or run["status"] in O.RUN_TERMINAL:
                    return
                if run["status"] in O.RUN_WAITING_STATUSES:
                    # Paused or waiting on the user while driving: leave the
                    # run exactly as it is - no wake, no settlement - and let
                    # the control plane decide when it resumes.
                    return
                self._heartbeat(run_id)
                self._check_invariant(run_id)

                ready = self._ready_snapshot(run_id)
                if not ready:
                    self._settle_without_work(run_id)
                    return
                batch = ready[:max(self.portion_budget - executed, 0)]
                if self._trajectory_pauses(run_id, [t["task_id"] for t in batch]):
                    return

                done = await self._execute_tasks(
                    run_id, ready, self.portion_budget - executed)
                executed += done
                if done == 0:
                    self._settle_without_work(run_id)
                    return

                run = self.store.objective_run(run_id)
                if run is None or run["status"] in O.RUN_TERMINAL:
                    continue
                tasks = self.store.objective_tasks(run_id)
                if all(t["status"] in O.TASK_TERMINAL for t in tasks):
                    self._finish(run_id)
                    continue
                if executed >= self.portion_budget:
                    # Budget exhausted: persist the plan and wake later. The
                    # run stays RUNNING with a scheduled wake - never
                    # abandoned.
                    self._schedule_wake(run_id, seconds=WAKE_SECONDS)
                    return
                # More work remains and the budget allows it: keep driving.
                # Each round boundary is a continuation point - recorded so
                # the trace shows the run advanced without the user.
                self.store.append_objective_event(
                    run_id, O.EVENT_CONTINUATION_SCHEDULED,
                    detail={"by": "engine",
                            "executed_this_wake": executed})
                self._check_invariant(run_id)
        finally:
            self.release(run_id)

    async def _drive_one_round(self, run_id: str) -> int:
        """One snapshot round, no scheduling. Used by the watchdog.

        The same trajectory check as the driver loop (TRAJ-001): a run the
        watchdog took over from a dead owner is not driven past a pause its
        own history calls for."""
        if self._trajectory_pauses(run_id):
            return 0
        ready = self._ready_snapshot(run_id)
        if not ready:
            return 0
        return await self._execute_tasks(run_id, ready, self.portion_budget)

    def _ready_snapshot(self, run_id: str) -> list[dict]:
        """
        One snapshot per round: READY and interrupted tasks plus QUEUED
        tasks whose dependencies all succeeded (promoted), and WAITING tasks
        whose retry wake is due. Tasks become executable only between
        rounds, which is what lets the watchdog drive part of a graph and
        leave the rest for the continuation.
        """
        tasks = self.store.objective_tasks(run_id)
        by_id = {t["task_id"]: t for t in tasks}
        ready: list[dict] = []
        for task in tasks:
            if task["capability"] == O.COMPOSITE:
                # A group is never dispatched: it settles when its
                # children do, in _settle_composites.
                continue
            deps_ok = all(
                by_id.get(dep, {}).get("status") == O.TaskStatus.SUCCEEDED
                for dep in (task.get("dependencies") or [])
            )
            if not deps_ok:
                continue
            status = task["status"]
            if status == O.TaskStatus.READY:
                ready.append(task)
            elif status == O.TaskStatus.INTERRUPTED:
                # Re-drive a task the watchdog interrupted.
                ready.append(task)
            elif status == O.TaskStatus.QUEUED:
                self.store.update_objective_task(
                    task["task_id"], status=O.TaskStatus.READY)
                task["status"] = O.TaskStatus.READY
                ready.append(task)
            elif status == O.TaskStatus.WAITING:
                wake = task.get("next_wake")
                due = _due(wake) if wake is not None else True
                if due:
                    ready.append(task)
        return ready

    async def _execute_tasks(self, run_id: str, ready: list[dict],
                             limit: int) -> int:
        """
        Execute up to `limit` of the snapshot as dependency waves (ORCH-001).

        A task starts when its dependencies succeeded and its declared file
        surface is disjoint from every running task's, as many together as
        the governor admits for this run. A task that shares a file with a
        running one waits for it, and one with no declared surface runs alone,
        so a graph that declares nothing runs one task at a time in snapshot
        order, as it always has. Each task is re-checked as it starts; the
        per-task gates (stop latch, envelope, budget) stay in `_execute_task`.
        """
        pending = list(ready)
        flying: dict[asyncio.Task, tuple[str, frozenset[str]]] = {}
        executed = 0
        failure: BaseException | None = None
        try:
            while True:
                if failure is None and executed < limit:
                    for task in self._wave_starts(run_id, pending, flying,
                                                  limit - executed):
                        running = asyncio.ensure_future(
                            self._execute_task(run_id, task))
                        flying[running] = (task["task_id"], _task_surface(task))
                        executed += 1
                if not flying:
                    break
                finished, _ = await asyncio.wait(
                    flying, return_when=asyncio.FIRST_COMPLETED)
                for one in finished:
                    del flying[one]
                    # Every finished task's outcome is read, so none goes
                    # unretrieved; the first failure ends the wave, and is
                    # raised once what is already running has settled.
                    problem = (asyncio.CancelledError() if one.cancelled()
                               else one.exception())
                    if problem is not None and failure is None:
                        failure = problem
                if flying:
                    self._heartbeat(run_id)
        finally:
            for one in flying:
                one.cancel()
            if flying:
                await asyncio.gather(*flying, return_exceptions=True)
        if failure is not None:
            raise failure
        return executed

    def _wave_starts(self, run_id: str, pending: list[dict],
                     flying: dict, limit: int) -> list[dict]:
        """The pending tasks to start now; `pending` keeps what must wait.

        `flying` maps each running task to (task id, surface). Paused, stopped
        or finished runs start nothing more - what is running still settles."""
        run_now = self.store.objective_run(run_id)
        if run_now is None or run_now["status"] in O.RUN_TERMINAL \
                or run_now["status"] in O.RUN_WAITING_STATUSES:
            # Paused mid-snapshot: stop executing; the run stays exactly
            # as it is until the control plane resumes it.
            pending.clear()
            return []
        rows = {t["task_id"]: t for t in self.store.objective_tasks(run_id)}
        # A state precondition reads the results of FINISHED steps, so a step
        # that may establish one must finish before the next starts: a run
        # with preconditions declares no usable surface and runs one at a time.
        surface_of = (lambda _task: frozenset()) \
            if self._governance(run_id).get("requires") else _task_surface
        eligible: list[dict] = []
        for task in pending:
            current = rows.get(task["task_id"])
            if current is None or current["status"] in O.TASK_TERMINAL:
                continue
            if any(rows.get(dep, {}).get("status") != O.TaskStatus.SUCCEEDED
                   for dep in current.get("dependencies") or []):
                continue
            eligible.append(current)
        pending[:] = eligible
        if not eligible:
            return []

        own = {task_id for task_id, _ in flying.values()}
        waiting_on = {t["task_id"] for t in eligible}
        # What earlier rounds left running or waiting on a worker still
        # holds its files; one that declared none holds nothing we can name.
        running = [surface for _, surface in flying.values()]
        running += [s for t in rows.values()
                    if t["status"] in (O.TaskStatus.RUNNING,
                                       O.TaskStatus.WAITING)
                    and t["task_id"] not in own
                    and t["task_id"] not in waiting_on
                    for s in (surface_of(t),) if s]
        together = waves.pick_starts(eligible, surface_of, running=running,
                                     room=len(eligible))
        gov = self._governor()
        if len(flying) + len(together) >= 2:
            gov.justify_parallel(run_id)
        from friday import governor as G
        capacity = max(1, gov.worker_capacity(run_id))
        # Workers held by anyone, other objectives included, count against
        # the same ceiling; a task of ours not yet holding one counts too.
        # Idle, one task always starts - `admit` still gates its worker.
        room = capacity - max(len(flying), gov.active(G.WORKER))
        starts = waves.pick_starts(
            eligible, surface_of, running=running,
            room=min(max(room, 0 if flying else 1), limit))
        started = {id(t) for t in starts}
        pending[:] = [t for t in eligible if id(t) not in started]
        return starts

    def _governor(self):
        if self._governor_source is not None:
            return self._governor_source()
        from friday import governor as G
        return G.governor()

    async def _execute_task(self, run_id: str, task: dict) -> None:
        task_id = task["task_id"]
        # STOP-001: a latched emergency stop parks the run before anything is
        # dispatched, whichever process the stop was pressed in - a worker
        # poll included, since a poll that finds the gateway STOP killed would
        # ask recovery to restart it. The WorkRun id stays on the task, so the
        # person's resume reconciles that same run.
        stopped = self.store.stop_latched()
        if stopped:
            self.store.touch_objective_run(
                run_id, status=O.RunStatus.PAUSED, next_wake=None,
                blocker=f"stopped: {stopped['reason']}")
            self.store.append_objective_event(
                run_id, O.EVENT_RUN_PAUSED, task_id=task_id,
                detail={"reason": f"stopped: {stopped['reason']}"})
            return

        # Submit-first Hermes work has already been dispatched. Re-entering a
        # due WAITING task reconciles that SAME WorkRun; it never calls the
        # mutating delegate a second time.
        worker_id = _pending_worker_id(task)
        if worker_id:
            await self._reconcile_worker(run_id, task, worker_id)
            return

        # SIGNAL-001: an amendment from any surface raises a barrier. What is
        # already running is left to finish, but nothing new starts under the
        # old version of the plan; the run waits until the amendment is
        # applied or the person says go on.
        barrier = signals.barrier_up(self.store, run_id)
        if barrier:
            detail = barrier.get("detail") or {}
            self.store.touch_objective_run(
                run_id, status=O.RunStatus.PAUSED, next_wake=None,
                blocker=f"amendment from {detail.get('surface', 'another surface')}"
                        " pending: the old plan's next step is held")
            self.store.append_objective_event(
                run_id, O.EVENT_RUN_PAUSED, task_id=task_id,
                detail={"reason": "amendment barrier"})
            return

        # PRD v7 SEC-005 / TRUST-002: each task runs as the objective's own
        # lineage, inside the envelope it was compiled with. A run with no
        # readable envelope is parked for the person - fail closed - before
        # any budget is charged or anything is dispatched.
        task_run = self._task_run(run_id, task)
        if task_run is None:
            self.store.touch_objective_run(
                run_id, status=O.RunStatus.PAUSED, next_wake=None,
                blocker=NO_ENVELOPE_BLOCKER)
            self.store.append_objective_event(
                run_id, O.EVENT_RUN_PAUSED, task_id=task_id,
                detail={"reason": "no execution envelope"})
            return

        # Audit A-022: the budget is checked against RECORDED spend before
        # every call - gateway usage rows, worker usage, attempts, strategy
        # changes, wall time - never against an estimate. It runs before
        # this attempt is written, so a parked task is charged nothing. An
        # exhausted budget parks the run (PAUSED with a blocker naming the
        # dimension and numbers) rather than trimming work silently; the
        # person resumes it, which is a decision, or raises the budget.
        verdict = self._budget_verdict(run_id, task["capability"], task_id)
        if not verdict.allowed:
            self.store.touch_objective_run(
                run_id, status=O.RunStatus.PAUSED, next_wake=None,
                blocker=f"budget exhausted ({verdict.dimension}): {verdict.reason}")
            self.store.append_objective_event(
                run_id, budget_module.EVENT_BUDGET_EXHAUSTED, task_id=task_id,
                detail={"dimension": verdict.dimension, "reason": verdict.reason,
                        "limit": verdict.limit, "spend": vars(verdict.spend)})
            return
        # ORCH-003: past the soft limit the run goes on, but reports once.
        budget_module.record_soft_limits(self.store, run_id, verdict, task_id=task_id)

        # EXP-001: how much evidence this attempt buys. The budget check above
        # bounds recorded spend against the run's declared limits, and the
        # attempt budget bounds how many tries there are; neither sizes the
        # verification. The plan prices the cheapest evidence that could settle
        # this attempt and escalates a rung per attempt, to the depth the
        # task's risk justifies and no further. It never refuses the attempt.
        #
        # Imported here, not at module scope: experiment_engine reuses this
        # module's failure_fingerprint, so the dependency only points one way
        # at import time.
        from friday import experiment_engine

        plan = experiment_engine.evidence_plan(task)

        attempt = int(task.get("attempts") or 0) + 1
        self.store.update_objective_task(
            task_id, status=O.TaskStatus.RUNNING,
            started_at=task.get("started_at") or now_iso(),
            attempts=attempt,
        )
        self.store.append_objective_event(
            run_id, O.EVENT_TASK_STARTED,
            task_id=task_id,
            # On the append-only event, not the dispatch arguments: worker
            # capabilities have strict signatures, so an extra keyword is a
            # TypeError, not a hint.
            detail={"attempt": attempt, "capability": task["capability"],
                    "evidence_depth": plan.depth,
                    "evidence_cap": plan.cap,
                    "evidence_reason": plan.reason},
        )

        try:
            arguments = self._substitute(task["arguments"], run_id)
        except KeyError as exc:
            self._fail_task(run_id, task_id, O.FailureKind.STRUCTURAL,
                            f"unresolvable reference {exc}")
            return

        hint = (task.get("detail") or {}).get("strategy_hint")
        if hint:
            # The strategy change must reach the worker, not just the log: a
            # hint nobody reads is a blind retry with better bookkeeping.
            arguments = {**arguments, "strategy_hint": hint}
        governance = self._governance(run_id)
        # BCON-001 recovery: a retried batch carries only the items the last
        # attempt failed (OBJ-004 per-item state, kept on the task).
        arguments = c.resume_arguments(
            governance, arguments,
            (task.get("detail") or {}).get("item_outcomes"))
        # BCON-001 preconditions: an action whose own arguments break a data
        # invariant, or whose required state no finished step established, is
        # not made.
        finished = [t.get("result") for t in self.store.objective_tasks(run_id)
                    if t["status"] == O.TaskStatus.SUCCEEDED]
        broken = (c.broken_invariants(governance, arguments)
                  + c.unmet_requirements(governance, finished,
                                         str(task.get("capability") or "")))
        if broken:
            self._fail_task(run_id, task_id, O.FailureKind.STRUCTURAL,
                            "INVARIANT: " + "; ".join(broken))
            return
        try:
            result = await self._dispatch(task["capability"], arguments, task_run,
                                          task_id)
        except Exception as exc:
            from friday import governor as G

            if isinstance(exc, G.Refused):
                # ORCH-001: the governor would not admit a worker now. That is
                # an answer about the machine, not a failure of the task: no
                # work was done, so no attempt is spent, and the task waits.
                self._defer_refused(run_id, task_id, attempt, exc)
                return
            kind = FailureClassifier.classify(exc)
            max_attempts = self._max_attempts_for(task)

            found = None
            if kind in (O.FailureKind.PROVIDER_DOWN, O.FailureKind.CAPPED):
                # Said out loud, and in the terms that decide what to do next.
                # "all LLMs are unavailable" in a log is a symptom; which
                # provider, which code, and whether it is worth asking again
                # is the thing anybody reading it actually needs.
                from friday import provider_diagnostics as PD

                found = PD.diagnose(exc)
                logger.info(
                    "provider.failure run_id=%s task=%s class=%s "
                    "status_code=%s finish_reason=%s retryable=%s attempt=%d/%d",
                    run_id, task_id, found.kind, found.status_code or "-",
                    found.finish_reason or "-", found.worth_retrying,
                    attempt, max_attempts)

            if kind in O.RETRYABLE_KINDS:
                # A provider that cannot answer is left alone for a while; an
                # ordinary transient failure is picked up on the next round.
                # Asking an overloaded model again immediately spends the
                # attempt budget inside the first few seconds of an outage.
                if kind == O.FailureKind.CAPPED:
                    # A cap is a known reset time, not an outage - wait until
                    # it, not 0s (which would hammer the capped provider on
                    # every poll for the rest of its window) and not the
                    # generic PROVIDER_DOWN backoff (which is usually too
                    # short to clear a quota reset).
                    from datetime import datetime
                    delay = 0.0
                    if found is not None and found.reset_at:
                        try:
                            delay = max(0.0, (datetime.fromisoformat(
                                found.reset_at) - datetime.now()
                                ).total_seconds())
                        except ValueError:
                            pass
                else:
                    delay = (PROVIDER_BACKOFF_SECONDS
                             if kind == O.FailureKind.PROVIDER_DOWN else 0.0)
                # The run keeps a future, which is the invariant that matters:
                # a provider outage may end this attempt and must never end
                # the objective.
                self._requeue_or_block(
                    run_id, task_id, task, kind=kind,
                    reason=f"{type(exc).__name__}: {exc}",
                    attempt=attempt, max_attempts=max_attempts, delay=delay)
                return
            self._fail_task(run_id, task_id, kind,
                            f"{type(exc).__name__}: {exc}")
            return

        # The port's contract is a result envelope (CapabilityPort). None, a
        # proxy's error page, a bare list or raw bytes prove nothing: stored
        # as a "result" they read as a success or break the task row. Name
        # the type and never echo the content - it is untrusted and may hold
        # a secret.
        if not isinstance(result, dict):
            self._fail_task(
                run_id, task_id, O.FailureKind.STRUCTURAL,
                "malformed tool output: expected a result envelope, got "
                f"{type(result).__name__}; the outcome of the call is unknown")
            return

        # A delegation that is still running is not a result. Park the task
        # WAITING on the worker id and wake to poll; the run keeps a future.
        if _working_worker(result):
            worker_id = str(result["work_run_id"])
            self.store.update_objective_task(
                task_id, status=O.TaskStatus.WAITING,
                result={"work_run_id": worker_id},
                evidence=f"worker {worker_id} is still running",
                next_wake=_in_seconds(WAKE_SECONDS))
            self.store.append_objective_event(
                run_id, O.EVENT_WORKER_WAITING, task_id=task_id,
                detail={"work_run_id": worker_id, "attempt": attempt})
            self._schedule_wake(run_id, seconds=WAKE_SECONDS)
            return

        # A capability that returned rather than raised can still be a
        # refusal - a structured failure in the envelope. Classify it the same
        # way an exception is classified, so connectivity is diagnosed and
        # repaired, an auth boundary parks for the boss, and everything else
        # retries within budget or fails honestly.
        refusal = _refused(result)
        if refusal is not None:
            kind, reason = refusal
            max_attempts = self._max_attempts_for(task)
            if kind in O.RETRYABLE_KINDS:
                if kind == O.FailureKind.CONNECTIVITY:
                    reason = await self._diagnose_and_recover(
                        run_id, task_id, reason)
                self._requeue_or_block(
                    run_id, task_id, task, kind=kind, reason=reason,
                    attempt=attempt, max_attempts=max_attempts, delay=0.0)
                return
            if kind == O.FailureKind.CONNECTIVITY:
                reason = await self._diagnose_and_recover(
                    run_id, task_id, reason)
            if kind == O.FailureKind.USER_REQUIRED and _is_auth(reason):
                self._park_for_auth(run_id, task_id, kind, reason)
                return
            if kind == O.FailureKind.USER_REQUIRED and _is_approval(reason):
                self._park_for_approval(run_id, task_id, task, reason)
                return
            self._fail_task(run_id, task_id, kind, reason)
            return

        # BCON-001 postcondition: a result that breaks a data invariant fails
        # the task, so everything that depends on it is skipped, not run on it.
        broken = c.broken_invariants(governance, result)
        if broken:
            self._fail_task(run_id, task_id, O.FailureKind.STRUCTURAL,
                            "INVARIANT: " + "; ".join(broken))
            return
        # BCON-001 recovery: per-item outcomes are kept, and a batch with
        # failed items is retried for those items alone.
        outcomes = {**((task.get("detail") or {}).get("item_outcomes") or {}),
                    **c.item_outcomes(result)}
        pending = sorted(i for i, ok in outcomes.items() if not ok)
        if pending and c.partial_failure_recovery(governance) == c.RESUME_FAILED_ITEMS_ONLY:
            task = {**task, "detail": {**(task.get("detail") or {}),
                                       "item_outcomes": outcomes}}
            self._requeue_or_block(
                run_id, task_id, task, kind=O.FailureKind.TRANSIENT,
                reason="items failed: " + ", ".join(pending),
                attempt=attempt, max_attempts=self._max_attempts_for(task))
            return
        if trajectory._side_effecting(str(task.get("capability") or "")):
            # DUR-001: the first thing written once a side effect has
            # happened. If this process dies before the task is marked done,
            # recovery reads this and completes the task instead of doing the
            # side effect a second time (V7-D42).
            self.store.append_objective_event(
                run_id, O.EVENT_SIDE_EFFECT_COMMITTED, task_id=task_id,
                detail={"attempt": attempt, "capability": task.get("capability"),
                        "result": result})
        self.store.update_objective_task(
            task_id, status=O.TaskStatus.SUCCEEDED,
            result=result, evidence=json.dumps(result, default=str),
            finished_at=now_iso(),
            # Every item's outcome across retries, not just the last attempt's.
            **({"detail": {"item_outcomes": outcomes}} if outcomes else {}),
        )
        self.store.append_objective_event(
            run_id, O.EVENT_TASK_SUCCEEDED, task_id=task_id,
            detail={"attempts": attempt})
        self._ledger_evidence(run_id, task_id, task, result, passed=True)
        self._cascade_skips(run_id)
        self._check_invariant(run_id)

    def _defer_refused(self, run_id: str, task_id: str, attempt: int,
                       refused: BaseException) -> None:
        """Park a task whose worker the governor refused (QUEUE or SHED). It
        keeps its attempts, and the run keeps a future."""
        reason = str(refused)
        self.store.update_objective_task(
            task_id, status=O.TaskStatus.WAITING,
            failure_kind=O.FailureKind.TRANSIENT, evidence=reason,
            attempts=attempt - 1,
            next_wake=_in_seconds(ADMISSION_BACKOFF_SECONDS))
        self.store.append_objective_event(
            run_id, O.EVENT_TASK_RETRY, task_id=task_id,
            detail={"kind": "admission", "reason": reason, "attempt": attempt,
                    "delay_s": ADMISSION_BACKOFF_SECONDS})
        self._schedule_wake(
            run_id, seconds=max(ADMISSION_BACKOFF_SECONDS, WAKE_SECONDS))

    def _governance(self, run_id: str) -> dict:
        """The governance of the objective's stored contract; empty when it has
        none or the row cannot be read."""
        raw = (self.store.objective_run(run_id) or {}).get("contract")
        try:
            return dict((json.loads(raw) if raw else {}).get("governance") or {})
        except (ValueError, TypeError, AttributeError):
            logger.exception("objective %s: its stored contract cannot be read", run_id)
            return {}

    def _ledger_evidence(self, run_id: str, task_id: str, task: dict,
                         result, *, passed: bool, reason: str = "") -> None:
        """FR-052: one evidence-ledger entry per consequential step -
        expected (the capability + arguments the plan asked for), actual
        (what came back), the verification method the capability
        reported, and pass/fail. Never raises: the ledger is evidence
        about the run, not a step of it."""
        try:
            verification = ""
            if isinstance(result, dict):
                v = result.get("verification") or {}
                verification = str(v.get("method") or "") if isinstance(v, dict) else str(v)
            expected = f"{task.get('capability', '?')}({json.dumps(task.get('arguments') or {}, default=str)[:300]})"
            actual = (reason if reason else json.dumps(result, default=str))[:800]
            self.store.append_objective_evidence(
                run_id, task_id=task_id, expected=expected, actual=actual,
                method=verification or ("capability_result" if passed else "failure"),
                passed=passed)
        except Exception:  # noqa: BLE001
            logger.debug("evidence ledger write failed", exc_info=True)

    def _park_for_approval(self, run_id: str, task_id: str, task: dict,
                           reason: str) -> None:
        """Permission boundary (FR-060): the exact action - operation,
        target, parameters - is recorded as a PENDING approval on the run,
        the task WAITS, the run parks at WAITING_PERMISSION and the boss is
        told once. Nothing is retried: the same call without a yes is the
        same refusal."""
        arguments = dict(task.get("arguments") or {})
        target = str(arguments.get("path") or arguments.get("url")
                     or arguments.get("target") or arguments.get("name") or "")
        self.store.update_objective_task(
            task_id, status=O.TaskStatus.WAITING,
            failure_kind=O.FailureKind.USER_REQUIRED,
            evidence=reason, next_wake=None)
        self.store.append_objective_approval(
            run_id, operation=str(task.get("capability") or ""), target=target,
            parameters=arguments, decision="PENDING", decided_by="")
        self.store.touch_objective_run(
            run_id, status=O.RunStatus.WAITING_PERMISSION, next_wake=None,
            blocker=f"approval needed: {task.get('capability')} {target}".strip())
        self.store.append_objective_event(
            run_id, "approval.boundary", task_id=task_id,
            detail={"capability": task.get("capability"), "target": target,
                    "reason": reason[:300]})
        self.store.create_objective_delivery(
            run_id,
            f"Boss, I need your go-ahead before I {_describe(task.get('capability'), target)}. "
            "Say yes to that exact step and I'll continue from where I stopped.")
        logger.info("objective.approval_boundary run_id=%s task=%s",
                    run_id, task_id)
        self.release(run_id)

    def _trajectory_pauses(self, run_id: str, upcoming: Sequence[str] = ()) -> bool:
        """
        PRD v7 TRAJ-001: before more work, is the run still going somewhere?
        A run failing across tasks, failing everywhere for one cause, piling
        up policy refusals, spending far ahead of its progress, re-running
        a step with side effects, or about to act with side effects while the
        contract holds open questions (`upcoming`: the batch about to be
        dispatched) is paused and the person asked: carry on, change the
        plan, or cancel. Resuming starts a new window.
        """
        try:
            assessment = trajectory.for_run(self.store, run_id, tuple(upcoming))
        except Exception:                                    # noqa: BLE001
            # The budget still holds every hard ceiling; a check that could
            # not read the run is reported, not a reason to stop it.
            logger.exception("objective %s: the trajectory check failed", run_id)
            return False
        if assessment.verdict == trajectory.ON_TRACK:
            return False
        reasons = "; ".join(assessment.reasons)
        self.store.touch_objective_run(
            run_id, status=O.RunStatus.PAUSED, next_wake=None,
            blocker=f"trajectory: {reasons}"[:500])
        self.store.append_objective_event(
            run_id, trajectory.EVENT_FLAGGED,
            detail={"verdict": assessment.verdict, "reasons": list(assessment.reasons),
                    "signals": assessment.signals})
        self.store.create_objective_delivery(
            run_id,
            f"Boss, I've paused this objective: {reasons}. "
            "Say carry on, change the plan, or cancel it.")
        logger.warning("objective.trajectory_paused run_id=%s verdict=%s reasons=%s",
                       run_id, assessment.verdict, reasons)
        self.release(run_id)
        return True

    def _park_for_auth(self, run_id: str, task_id: str, kind: str,
                       reason: str) -> None:
        """Auth boundary: park the run, tell the boss ONCE, await verify."""
        self.store.update_objective_task(
            task_id, status=O.TaskStatus.WAITING, failure_kind=kind,
            evidence=reason, next_wake=None)
        self.store.touch_objective_run(
            run_id, status=O.RunStatus.WAITING_PERMISSION, next_wake=None)
        self.store.append_objective_event(
            run_id, "auth.boundary", task_id=task_id,
            detail={"reason": reason[:300]})
        self.store.create_objective_delivery(
            run_id,
            "Boss, one of your providers needs you to sign in before I can "
            "finish this objective. Complete the sign-in and I'll pick the "
            "task up automatically - you don't need to repeat anything.")
        logger.info("objective.auth_boundary run_id=%s task=%s",
                    run_id, task_id)
        self.release(run_id)

    def _live_worker_ids(self, run_id: str) -> tuple[str, ...]:
        """WorkRun ids still in flight for this run - the restart guard."""
        found = []
        for task in self.store.objective_tasks(run_id):
            if task["status"] in (O.TaskStatus.WAITING,
                                  O.TaskStatus.RUNNING):
                result = task.get("result") or {}
                worker = (str(result.get("work_run_id") or "")
                          if isinstance(result, dict) else "")
                if worker:
                    found.append(worker)
        return tuple(found)

    async def _diagnose_and_recover(self, run_id: str, task_id: str,
                                    reason: str) -> str:
        """Consult hermes_health, run the layer's repair, record both.

        Returns enriched evidence naming the failed layer. Every failure
        inside this method degrades to the caller's plain retry - the
        diagnosis must never make recovery WORSE than the blind timer it
        replaced, and it must never surface as a user question.
        """
        if self.health_probe is None:
            return reason
        stopped = self.store.stop_latched()
        if stopped:
            # STOP-001: the stop may be what took the layer down, and its
            # repair is a restart; recovering would undo the stop.
            self.store.append_objective_event(
                run_id, "connectivity.not_repaired", task_id=task_id,
                detail={"reason": f"stopped: {stopped['reason']}"})
            return f"{reason} [not repaired: stopped]"
        workers = self._live_worker_ids(run_id)
        try:
            try:
                report = self.health_probe(active_workruns=workers)
            except TypeError:
                # An older probe takes no arguments; the diagnosis is the
                # same, it just cannot say which WorkRuns are in flight.
                report = self.health_probe()
        except Exception as exc:                                 # noqa: BLE001
            logger.exception("health probe failed; plain retry")
            return f"{reason} (health probe unavailable: {exc})"
        detail = {
            "failed_layer": report.failed_layer,
            "recovery": report.recovery.value,
            "gateway_is_dead": report.gateway_is_dead,
            "active_workruns": list(report.active_workruns),
        }
        self.store.append_objective_event(
            run_id, "connectivity.diagnosed", task_id=task_id, detail=detail)
        logger.info(
            "objective.connectivity run_id=%s task=%s layer=%s repair=%s",
            run_id, task_id, report.failed_layer or "none",
            report.recovery.value)
        if report.healthy or self.health_recover is None:
            return f"{reason} [health: {report.summary}]"
        try:
            repaired = bool(await self.health_recover(report))
        except Exception as exc:                                 # noqa: BLE001
            logger.exception("health recovery failed; plain retry")
            return (f"{reason} [layer {report.failed_layer}; recovery "
                    f"{report.recovery.value} raised {type(exc).__name__}]")
        self.store.append_objective_event(
            run_id, "connectivity.recovered" if repaired
            else "connectivity.recovery_failed",
            task_id=task_id, detail=detail)
        state = "repaired" if repaired else "repair did not take"
        return (f"{reason} [layer {report.failed_layer}; recovery "
                f"{report.recovery.value}; {state}]")

    def _task_run(self, run_id: str, task: dict) -> c.Run | None:
        """The Run a task executes as, rebuilt from what its objective was
        compiled with. None when there is no readable envelope."""
        row = self.store.objective_run(run_id) or {}
        raw = row.get("envelope")
        if not raw:
            return None
        try:
            envelope = c.Envelope.from_dict(json.loads(raw))
        except (ValueError, KeyError, TypeError):
            logger.exception("objective %s: its stored envelope cannot be read",
                             run_id)
            return None
        return c.Run(run_id=c.new_run_id(),
                     request=str(row.get("request") or "")[:240],
                     capability=task["capability"],
                     provenance=envelope.provenance,
                     attended=envelope.attended, envelope=envelope)

    async def _dispatch(self, capability_id: str, arguments: dict,
                        run: c.Run, task_id: str = "") -> dict:
        """Call the port as this task's Run, after holding it to the envelope
        and to its lineage here. The capability runtime checks again; a port
        that carries the Run is not trusted to, and one that cannot carry it
        never sees it - so neither check can depend on the port."""
        envelope = run.envelope
        refusal = envelope.permits(capability_id) or (
            "this run's envelope has expired" if envelope.expired() else "")
        if refusal:
            return run.record(c.started(run.run_id, capability_id).finish(
                status=c.NOT_PERMITTED, error=f"ENVELOPE: {refusal}")).to_dict()
        from friday.capability_runtime import lineage_refusal

        blocked = lineage_refusal(capability_id, run.provenance)
        if blocked is not None:
            return run.record(c.started(run.run_id, capability_id).finish(
                status=c.FAILED, error=f"BLOCKED: {blocked.reason}")).to_dict()
        # BCON-001: "at most N changes" holds for the whole objective. The
        # change after the Nth is refused here, before any port sees it. A
        # change still running on its worker counts: it has not finished, but
        # it is being made.
        ceiling = envelope.max_side_effects
        if ceiling and trajectory._side_effecting(capability_id):
            # A task running beside this one (ORCH-001) is a change being made
            # too: waves start tasks together, so it has not finished here.
            made = sum(1 for task in self.store.objective_tasks(envelope.objective_id)
                       if (task["status"] == O.TaskStatus.SUCCEEDED
                           or _pending_worker_id(task)
                           or (task["status"] == O.TaskStatus.RUNNING
                               and task["task_id"] != task_id))
                       and trajectory._side_effecting(str(task.get("capability") or "")))
            if made >= ceiling:
                return run.record(c.started(run.run_id, capability_id).finish(
                    status=c.FAILED,
                    error=f"BLOCKED: the person allowed at most {ceiling} changes for "
                          f"this objective and {made} have been made or are in progress")).to_dict()
        extra: dict = {}
        if self._port_takes_run:
            extra["run"] = run
        if self._port_takes_key and task_id and trajectory._side_effecting(capability_id):
            extra["idempotency_key"] = O.idempotency_key(
                envelope.objective_id, task_id, arguments.get("items"))
        return await self.call_capability(capability_id, arguments, **extra)

    async def _reconcile_worker(self, run_id: str, task: dict,
                                worker_id: str) -> None:
        """Poll one existing WorkRun. Never re-dispatch its mutation."""
        task_id = task["task_id"]
        try:
            observed = await self.call_capability(
                "hermes_status", {"work_run_id": worker_id})
        except Exception as exc:
            self.store.update_objective_task(
                task_id, status=O.TaskStatus.WAITING,
                evidence=f"worker status unavailable: "
                         f"{type(exc).__name__}: {exc}",
                next_wake=_in_seconds(WAKE_SECONDS))
            self._schedule_wake(run_id, seconds=WAKE_SECONDS)
            return

        status, result = _worker_status(observed)
        poll_refusal = _refused(observed)
        if (poll_refusal is not None
                and poll_refusal[0] == O.FailureKind.CONNECTIVITY):
            evidence = await self._diagnose_and_recover(
                run_id, task_id, poll_refusal[1])
            self.store.update_objective_task(
                task_id, status=O.TaskStatus.WAITING,
                result={"work_run_id": worker_id},
                evidence=f"worker {worker_id} unreachable: {evidence}",
                next_wake=_in_seconds(WAKE_SECONDS))
            self._schedule_wake(run_id, seconds=WAKE_SECONDS)
            return

        if status in ("working", "starting", "steered", "cancelling", ""):
            self.store.update_objective_task(
                task_id, status=O.TaskStatus.WAITING,
                result={"work_run_id": worker_id},
                evidence=f"worker {worker_id} is {status or 'not terminal'}",
                next_wake=_in_seconds(WAKE_SECONDS))
            self._schedule_wake(run_id, seconds=WAKE_SECONDS)
            return

        if status == "complete":
            self.store.update_objective_task(
                task_id, status=O.TaskStatus.SUCCEEDED, result=result,
                evidence=json.dumps(result, default=str),
                finished_at=now_iso(), next_wake=None)
            self.store.append_objective_event(
                run_id, O.EVENT_WORKER_RECONCILED, task_id=task_id,
                detail={"work_run_id": worker_id, "status": status})
            self.store.append_objective_event(
                run_id, O.EVENT_TASK_SUCCEEDED, task_id=task_id,
                detail={"attempts": int(task.get("attempts") or 0)})
            self._ledger_evidence(run_id, task_id, task, result, passed=True)
            self._cascade_skips(run_id)
            return

        self._fail_task(
            run_id, task_id, O.FailureKind.STRUCTURAL,
            f"worker {worker_id} ended {status}: "
            f"{json.dumps(result, default=str)[:500]}")

    def _fail_task(self, run_id: str, task_id: str, kind: str,
                   reason: str, *, detail: dict | None = None) -> None:
        fields = {"status": O.TaskStatus.FAILED, "failure_kind": kind,
                  "evidence": reason, "finished_at": now_iso()}
        if detail is not None:
            fields["detail"] = detail
        self.store.update_objective_task(task_id, **fields)
        self.store.append_objective_event(
            run_id, O.EVENT_TASK_FAILED, task_id=task_id,
            detail={"kind": kind, "reason": reason})
        self._ledger_evidence(run_id, task_id,
                              self.store.objective_task(task_id) or {},
                              None, passed=False, reason=f"{kind}: {reason}")
        self._cascade_skips(run_id)
        self._check_invariant(run_id)

    def _budget_verdict(self, run_id: str, capability: str,
                        task_id: str | None = None) -> "budget_module.Verdict":
        """Recorded spend vs the run's budget (audit A-022). The gateway
        ledger and the Hermes work log are opened lazily and are optional:
        a missing ledger contributes zero tokens and says so in
        `spend.sources`, it never blocks a run by itself."""
        telemetry = work_log = None
        try:
            from friday.model_gateway import GatewayTelemetry
            telemetry = GatewayTelemetry()
        except Exception:  # noqa: BLE001 - recorded in spend.sources by measure()
            logger.debug("objective budget: gateway telemetry unavailable", exc_info=True)
        try:
            from friday.hermes_bridge import WorkRunLog
            work_log = WorkRunLog()
        except Exception:  # noqa: BLE001
            logger.debug("objective budget: hermes work log unavailable", exc_info=True)
        return budget_module.check(self.store, run_id, next_capability=capability,
                                   next_task_id=task_id,
                                   telemetry=telemetry, work_log=work_log)

    def _max_attempts_for(self, task: dict) -> int:
        """`iteration_budget` (from a Hermes TaskBundle, carried through the
        task's arguments) caps the task's attempt budget when set."""
        max_attempts = int(task.get("max_attempts") or self.max_attempts)
        budget = int((task.get("arguments") or {}).get("iteration_budget") or 0)
        if budget > 0:
            max_attempts = min(max_attempts, budget)
        return max_attempts

    def _requeue_or_block(self, run_id: str, task_id: str, task: dict, *,
                          kind: str, reason: str, attempt: int,
                          max_attempts: int, delay: float = 0.0,
                          verifier: str = "") -> None:
        """
        A retryable failure only earns another attempt if it looks like a
        new situation. The same fingerprint with no new hypothesis means the
        next attempt would repeat the last one verbatim - instead of
        spending the budget on that, the task changes strategy (re-plan,
        different role, reduce scope), and after MAX_STRATEGY_CHANGES it
        stops asking and goes BLOCKED with the evidence attached.
        """
        fingerprint = failure_fingerprint(kind, reason, verifier=verifier,
                                          task_id=task_id)
        detail = dict(task.get("detail") or {})
        history = list(detail.get("fingerprint_history") or [])
        prev_fingerprint = detail.get("last_fingerprint")
        prev_hypothesis = detail.get("hypothesis")
        hypothesis = ((task.get("arguments") or {}).get("hypothesis")
                     or reason.split(":", 1)[0].strip())
        strategy_changes = int(detail.get("strategy_changes") or 0)
        same_failure = (fingerprint == prev_fingerprint
                        and hypothesis == prev_hypothesis)

        history.append(fingerprint)
        detail["fingerprint_history"] = history
        detail["last_fingerprint"] = fingerprint
        detail["hypothesis"] = hypothesis

        if same_failure:
            strategy_changes += 1
            detail["strategy_changes"] = strategy_changes
            if strategy_changes > MAX_STRATEGY_CHANGES:
                detail["strategy_hint"] = None
                self.store.update_objective_task(
                    task_id, status=O.TaskStatus.BLOCKED, failure_kind=kind,
                    evidence=reason, finished_at=now_iso(), detail=detail)
                self.store.append_objective_event(
                    run_id, O.EVENT_TASK_BLOCKED, task_id=task_id,
                    detail={"fingerprint": fingerprint, "reason": reason,
                            "strategy_changes": strategy_changes})
                self._cascade_skips(run_id)
                self._check_invariant(run_id)
                return
            detail["strategy_hint"] = STRATEGY_HINTS[
                min(strategy_changes - 1, len(STRATEGY_HINTS) - 1)]
        else:
            detail["strategy_changes"] = strategy_changes
            detail["strategy_hint"] = None

        if attempt >= max_attempts:
            self._fail_task(run_id, task_id, kind, reason, detail=detail)
            return

        self.store.update_objective_task(
            task_id, status=O.TaskStatus.WAITING, failure_kind=kind,
            evidence=reason, next_wake=_in_seconds(delay), detail=detail)
        # FR-054: a retried attempt is a recorded event, not something to
        # infer from the attempt counter. The trace must show the failure,
        # its kind, and that the engine chose to retry (or change strategy).
        self.store.append_objective_event(
            run_id, O.EVENT_TASK_RETRY, task_id=task_id,
            detail={"kind": kind, "reason": reason, "attempt": attempt,
                    "max_attempts": max_attempts, "delay_s": delay,
                    "strategy_hint": detail.get("strategy_hint")})
        self._schedule_wake(run_id, seconds=max(delay, WAKE_SECONDS))

    def _cascade_skips(self, run_id: str) -> None:
        """
        A task whose dependency will never succeed is skipped, not re-run.
        A dependency that is RUNNING, WAITING or INTERRUPTED may still
        succeed, so it does not skip dependents.
        """
        while True:
            settled_any = self._settle_composites(run_id)
            tasks = self.store.objective_tasks(run_id)
            by_id = {t["task_id"]: t for t in tasks}
            skipped_any = settled_any
            for task in tasks:
                if task["status"] in O.TASK_TERMINAL:
                    continue
                dead = [
                    dep for dep in (task.get("dependencies") or [])
                    if by_id.get(dep, {}).get("status")
                    in (O.TaskStatus.FAILED, O.TaskStatus.SKIPPED,
                        O.TaskStatus.BLOCKED)
                ]
                if not dead:
                    continue
                self.store.update_objective_task(
                    task["task_id"], status=O.TaskStatus.SKIPPED,
                    blocked_by=dead[0], finished_at=now_iso(),
                )
                self.store.append_objective_event(
                    run_id, O.EVENT_TASK_SKIPPED,
                    task_id=task["task_id"],
                    detail={"blocked_by": dead[0]})
                skipped_any = True
            if not skipped_any:
                return

    def _settle_composites(self, run_id: str) -> bool:
        """
        A group finishes when its children do, and its status says what
        happened rather than what was attempted.

        The rule is the run's own PARTIAL rule, one level down: anything
        succeeding means the group did something, so it succeeded and the
        failures are its result. Only a group where nothing succeeded is a
        failure, and a group where nothing even could - every child skipped
        with a reason - is skipped, which for an audit is the honest answer
        and not a gap.
        """
        children: dict[str, list[dict]] = {}
        tasks = self.store.objective_tasks(run_id)
        for task in tasks:
            parent = task.get("parent_id")
            if parent:
                children.setdefault(parent, []).append(task)
        if not children:
            return False

        settled = False
        for group in tasks:
            if group["capability"] != O.COMPOSITE:
                continue
            if group["status"] in O.TASK_TERMINAL:
                continue
            kids = children.get(group["task_id"]) or []
            if not kids or any(kid["status"] not in O.TASK_TERMINAL
                               for kid in kids):
                continue

            tally: dict[str, int] = {}
            for kid in kids:
                tally[kid["status"]] = tally.get(kid["status"], 0) + 1

            if tally.get(O.TaskStatus.SUCCEEDED):
                status, event = (O.TaskStatus.SUCCEEDED,
                                 O.EVENT_TASK_SUCCEEDED)
            elif tally.get(O.TaskStatus.FAILED):
                status, event = O.TaskStatus.FAILED, O.EVENT_TASK_FAILED
            else:
                status, event = O.TaskStatus.SKIPPED, O.EVENT_TASK_SKIPPED

            result = {
                "children": len(kids),
                "tally": tally,
                "failures": [
                    {"task_id": kid["task_id"],
                     "capability": kid["capability"],
                     "kind": kid.get("failure_kind"),
                     "reason": kid.get("evidence") or ""}
                    for kid in kids
                    if kid["status"] == O.TaskStatus.FAILED],
            }
            self.store.update_objective_task(
                group["task_id"], status=status, result=result,
                evidence=json.dumps(tally, sort_keys=True),
                finished_at=now_iso())
            self.store.append_objective_event(
                run_id, event, task_id=group["task_id"],
                detail={"composite": True, "tally": tally})
            self._ledger_evidence(
                run_id, group["task_id"], group, result,
                passed=(status == O.TaskStatus.SUCCEEDED),
                reason="" if status == O.TaskStatus.SUCCEEDED
                else f"composite {status}: {json.dumps(tally, sort_keys=True)}")
            settled = True
        return settled

    def _substitute(self, arguments: dict, run_id: str) -> dict:
        """Resolve {{tasks.<id>.<key>}} from this run's persisted results."""
        tasks = {t["task_id"]: t for t in self.store.objective_tasks(run_id)}

        def resolve(value):
            if isinstance(value, dict):
                return {k: resolve(v) for k, v in value.items()}
            if isinstance(value, list):
                return [resolve(v) for v in value]
            if isinstance(value, str):
                def repl(match):
                    task_id, key = match.group(1), match.group(2)
                    row = tasks.get(task_id)
                    if row is None or row["status"] != O.TaskStatus.SUCCEEDED:
                        raise KeyError(
                            f"{task_id}.{key} referenced before it succeeded")
                    result = row.get("result") or {}
                    if not isinstance(result, dict) or key not in result:
                        raise KeyError(f"{task_id} has no result key {key!r}")
                    return str(result[key])
                return O._TASK_REF.sub(repl, value)
            return value

        return resolve(arguments)

    # -- scheduling ---------------------------------------------------------

    def _schedule_wake(self, run_id: str, *, seconds: float = WAKE_SECONDS,
                       at: str | None = None) -> None:
        wake = at if at is not None else \
            (datetime.now() + timedelta(seconds=seconds)).isoformat()
        self.store.touch_objective_run(run_id, next_wake=wake)
        self.store.append_objective_event(
            run_id, O.EVENT_CONTINUATION_SCHEDULED,
            detail={"next_wake": wake})

    def _settle_without_work(self, run_id: str) -> None:
        """No task can run right now: finish if the graph is over, else
        leave a wake for the continuation loop."""
        self._cascade_skips(run_id)
        run = self.store.objective_run(run_id)
        if run is None or run["status"] in O.RUN_TERMINAL:
            return
        tasks = self.store.objective_tasks(run_id)
        if all(t["status"] in O.TASK_TERMINAL for t in tasks):
            self._finish(run_id)
            return
        wakes = [t["next_wake"] for t in tasks
                 if t["status"] == O.TaskStatus.WAITING and t.get("next_wake")]
        if wakes:
            self._schedule_wake(run_id, at=min(wakes, key=_as_naive))
        else:
            self._schedule_wake(run_id, seconds=1.0)

    def _finish(self, run_id: str) -> None:
        every = self.store.objective_tasks(run_id)
        # Groups are the tasks the plan named; their leaves are the checks
        # inside them. The verdict is over the named tasks, and the
        # leaves are reported beside it so an audit's failed checks are
        # visible without being counted twice.
        tasks = [t for t in every if not t.get("parent_id")]
        leaves = [t for t in every if t.get("parent_id")]
        succeeded = [t for t in tasks if t["status"] == O.TaskStatus.SUCCEEDED]
        failed = [t for t in tasks if t["status"] == O.TaskStatus.FAILED]
        skipped = [t for t in tasks if t["status"] == O.TaskStatus.SKIPPED]
        interrupted = [t for t in tasks
                       if t["status"] == O.TaskStatus.INTERRUPTED]
        blocked = [t for t in tasks if t["status"] == O.TaskStatus.BLOCKED]
        outcome = None
        if not tasks:
            status, event = O.RunStatus.COMPLETED, O.EVENT_RUN_COMPLETED
        elif failed or skipped or interrupted or blocked:
            if succeeded:
                status, event = O.RunStatus.PARTIAL, O.EVENT_RUN_PARTIAL
            else:
                status, event = O.RunStatus.FAILED, O.EVENT_RUN_FAILED
            if blocked and not failed and not skipped and not interrupted:
                # Every remaining task hit its strategy-change budget on the
                # same fingerprint - the outcome names it instead of just
                # reporting a generic PARTIAL/FAILED.
                fp = (blocked[0].get("detail") or {}).get("last_fingerprint")
                outcome = f"blocked:{fp}"
        else:
            status, event = O.RunStatus.COMPLETED, O.EVENT_RUN_COMPLETED

        # FR-053 completion gate: COMPLETED is a verdict of the control
        # plane over the evidence ledger, never a worker's word. Every
        # succeeded top-level task must have a passing evidence entry;
        # one without is a task somebody SAID finished, and the run is
        # PARTIAL with the gap named. (Composite leaves are checked by
        # their group.)
        if status == O.RunStatus.COMPLETED and succeeded:
            gate = self._completion_gate(run_id, succeeded)
            if not gate["passed"]:
                status, event = O.RunStatus.PARTIAL, O.EVENT_RUN_PARTIAL
                outcome = f"evidence_gap:{','.join(gate['missing'][:3])}"
                self.store.append_objective_event(
                    run_id, "completion.gate_refused",
                    detail={"missing_evidence": gate["missing"]})

        started = self.store.objective_run(run_id)
        duration = 0.0
        if started and started.get("created_at"):
            try:
                duration = (datetime.now() - _as_naive(
                    started["created_at"])).total_seconds()
            except (ValueError, TypeError):
                duration = 0.0
        summary = {
            "succeeded": len(succeeded),
            "failed": len(failed),
            "skipped": len(skipped),
            "interrupted": len(interrupted),
            "blocked": len(blocked),
            "outcome": outcome,
            "attempts": sum(int(t.get("attempts") or 0) for t in tasks),
            "manual_continue_count": int(
                (started or {}).get("manual_continue_count") or 0),
            "duration_seconds": round(duration, 1),
            "failures": [
                {
                    "task_id": t["task_id"],
                    "capability": t["capability"],
                    "kind": t.get("failure_kind"),
                    "reason": t.get("evidence") or "",
                }
                for t in (leaves or tasks)
                if t["status"] == O.TaskStatus.FAILED
            ],
        }
        if leaves:
            summary["checks"] = len(leaves)
            summary["checks_failed"] = sum(
                1 for t in leaves if t["status"] == O.TaskStatus.FAILED)
            summary["checks_skipped"] = sum(
                1 for t in leaves if t["status"] == O.TaskStatus.SKIPPED)
        self.store.finish_objective_run(run_id, status=status, summary=summary)
        # A finished objective keeps no network authority (NET-001).
        O.release_network_grant(self.store, run_id, reason=f"objective {status}")
        self.store.append_objective_event(
            run_id, event,
            detail={"summary": summary, "executor_id": self.executor_id})
        # Terminal state is not user delivery. Persist an exactly-once message
        # at the moment of truth; the live AgentSession drain owns rendering
        # it to the user without another model call.
        #
        # PRD FR-042: a run fired by a SCHEDULE does not announce itself -
        # the schedule evaluates its condition over this run's tasks and
        # decides whether anything is said (the no-noise rule). The ledger
        # still has everything; only the unconditional announcement is
        # withheld.
        run_row = self.store.objective_run(run_id) or {}
        if not str(run_row.get("source_channel") or "").startswith("schedule:"):
            self.store.create_objective_delivery(run_id, speak(self.store, run_id))
        logger.info("objective %s %s (%d/%d tasks)",
                    run_id, status, len(succeeded), len(tasks))
        self.release(run_id)

    # -- invariant & status -------------------------------------------------

    def _completion_gate(self, run_id: str, succeeded: list[dict]) -> dict:
        """FR-053: which succeeded tasks have passing evidence in the
        objective's ledger. `missing` names the ones that do not."""
        ledger = self.store.objective_ledger(run_id) or {}
        backed = {e.get("task_id") for e in ledger.get("evidence", [])
                  if e.get("passed")}
        missing = [t["task_id"] for t in succeeded if t["task_id"] not in backed]
        return {"passed": not missing, "missing": missing,
                "evidence_entries": len(ledger.get("evidence", []))}

    def _check_invariant(self, run_id: str) -> None:
        run = self.store.objective_run(run_id)
        if run is None:
            return
        if run["status"] in O.RUN_TERMINAL:
            return
        if run["status"] in O.RUN_WAITING_STATUSES:
            return
        if run["next_wake"]:
            return
        if run["lease_executor_id"] == self.executor_id and run["lease_expiry"] \
                and _fresh(run["lease_expiry"]):
            return
        raise InvariantViolation(
            f"run {run_id} is {run['status']} with no executor, no wake and "
            f"no legitimate wait - the forbidden state")

    def cancel(self, run_id: str, *, reason: str = "user request") -> bool:
        """Explicit stop: in-flight tasks INTERRUPTED, run CANCELLED."""
        if not self.acquire(run_id):
            run = self.store.objective_run(run_id)
            return bool(run and run["status"] in O.RUN_TERMINAL)
        try:
            for task in self.store.objective_tasks(run_id):
                if task["status"] not in O.TASK_TERMINAL:
                    self.store.update_objective_task(
                        task["task_id"], status=O.TaskStatus.INTERRUPTED,
                        finished_at=now_iso(),
                        evidence=f"cancelled: {reason}",
                    )
                    self.store.append_objective_event(
                        run_id, O.EVENT_TASK_INTERRUPTED,
                        task_id=task["task_id"],
                        detail={"reason": reason})
            run = self.store.objective_run(run_id) or {}
            tasks = self.store.objective_tasks(run_id)
            self.store.finish_objective_run(
                run_id, status=O.RunStatus.CANCELLED,
                summary={
                    "succeeded": 0, "failed": 0, "skipped": 0,
                    "interrupted": sum(1 for t in tasks
                                       if t["status"] ==
                                       O.TaskStatus.INTERRUPTED),
                    "attempts": sum(int(t.get("attempts") or 0)
                                    for t in tasks),
                    "manual_continue_count": int(
                        run.get("manual_continue_count") or 0),
                    "duration_seconds": 0.0,
                    "failures": [],
                },
            )
            self.store.append_objective_event(
                run_id, O.EVENT_RUN_CANCELLED, detail={"reason": reason})
            O.release_network_grant(self.store, run_id,
                                    reason=f"objective cancelled: {reason}")
            return True
        finally:
            self.release(run_id)

    def status(self, run_id: str = "") -> dict:
        if run_id:
            run = self.store.objective_run(run_id)
            if run is None:
                return {"error": f"no objective run {run_id!r}"}
            run = dict(run)
            run["tasks"] = self.store.objective_tasks(run_id)
            run["events"] = self.store.objective_events(run_id, limit=20)
            return run
        runs = self.store.objective_runs(limit=5)
        return {"runs": runs}

    def list_runs(self, limit: int = 5) -> list[dict]:
        return self.store.objective_runs(limit=limit)

    def speak_summary(self, run_id: str) -> str:
        """Prose summary for speech: counts, failures, never raw JSON."""
        return speak(self.store, run_id)


def _describe(capability, target: str) -> str:
    verb = str(capability or "do that").replace("_", " ")
    return f"{verb} {target}".strip()


def _pending_approval(store: Store, run_id: str, operation: str,
                      target: str) -> tuple[dict, dict, str] | None:
    """The WAITING task a run is parked on for an approval, with its arguments
    and target, when it is the action `operation`/`target` name (empty means
    any); None when the run is not waiting for that."""
    run = store.objective_run(run_id)
    if run is None or run["status"] != O.RunStatus.WAITING_PERMISSION:
        return None
    parked = [t for t in store.objective_tasks(run_id)
              if t["status"] == O.TaskStatus.WAITING
              and t.get("failure_kind") == O.FailureKind.USER_REQUIRED
              and _is_approval(str(t.get("evidence") or ""))]
    if not parked:
        return None
    task = parked[0]
    arguments = dict(task.get("arguments") or {})
    actual_target = str(arguments.get("path") or arguments.get("url")
                        or arguments.get("target") or arguments.get("name") or "")
    if operation and operation != task.get("capability"):
        return None
    if target and target != actual_target:
        return None
    return task, arguments, actual_target


def deny_pending(store: Store, run_id: str, *, decided_by: str,
                 operation: str = "", target: str = "") -> bool:
    """A person refused the pending action on this run. The WAITING task is
    stopped (FAILED, not retried) and the refusal is recorded against the exact
    action; the run goes back to RUNNING so the driver skips what depended on
    the step and settles the run. Refuses when nothing is pending or the named
    action does not match what was asked."""
    pending = _pending_approval(store, run_id, operation, target)
    if pending is None:
        return False
    task, arguments, actual_target = pending
    reason = f"approval denied by the person via {decided_by}"
    if not store.update_objective_task_if(
            task["task_id"], expect=(O.TaskStatus.WAITING,),
            status=O.TaskStatus.FAILED, evidence=reason,
            finished_at=now_iso(), next_wake=None):
        return False
    store.append_objective_approval(
        run_id, operation=str(task.get("capability") or ""), target=actual_target,
        parameters=arguments, decision="DENIED", decided_by=decided_by)
    store.touch_objective_run(run_id, status=O.RunStatus.RUNNING,
                              next_wake=_in_seconds(0.0), blocker="")
    store.append_objective_event(
        run_id, O.EVENT_TASK_FAILED, task_id=task["task_id"],
        detail={"kind": O.FailureKind.USER_REQUIRED, "reason": reason})
    logger.info("objective.approval_denied run_id=%s task=%s by=%s",
                run_id, task["task_id"], decided_by)
    return True


def resume_after_approval(store: Store, run_id: str, *, decided_by: str,
                          operation: str = "", target: str = "") -> bool:
    """A person approved the pending action on this run (FR-060: the
    approval names the exact operation/target it is for). The WAITING
    task goes READY with the approval marked on the run, the run goes
    RUNNING, and the driver picks it up. Refuses when nothing is pending or
    the named action does not match what was asked."""
    pending = _pending_approval(store, run_id, operation, target)
    if pending is None:
        return False
    task, arguments, actual_target = pending
    # A lease on this plan (APPROVAL-001): bound to what the objective would
    # do at this moment, and to a time after which the person is asked again.
    from friday import modification

    store.append_objective_approval(
        run_id, operation=str(task.get("capability") or ""), target=actual_target,
        parameters=arguments, decision="APPROVED", decided_by=decided_by,
        expires_at=(datetime.now(timezone.utc) + O.APPROVAL_TTL).isoformat(),
        plan_hash=O.plan_hash(store, run_id),
        graph_version=modification.graph_version(store, run_id),
        blast_radius=O.blast_radius(str(task.get("capability") or ""), arguments))
    # The approval travels with the call as `approved_by`, and the capability
    # runtime honours it only when the APPROVED record written above matches
    # this capability and these exact arguments (`_approval_on_record`). It
    # answers an ASK; a CONFIRM, such as a browser external write, still needs
    # its own exact-action confirmation.
    if not store.update_objective_task_if(
            task["task_id"], expect=(O.TaskStatus.WAITING,),
            status=O.TaskStatus.READY, failure_kind="",
            arguments={**arguments, "approved_by": decided_by},
            next_wake=_in_seconds(0.0)):
        return False
    store.touch_objective_run(run_id, status=O.RunStatus.RUNNING,
                              next_wake=_in_seconds(0.0), blocker="")
    store.append_objective_event(
        run_id, "approval.granted", task_id=task["task_id"],
        detail={"capability": task.get("capability"), "target": actual_target,
                "decided_by": decided_by})
    logger.info("objective.approval_granted run_id=%s task=%s by=%s",
                run_id, task["task_id"], decided_by)
    return True


def _auth_parked(store: Store, run: dict) -> list[dict]:
    """The WAITING tasks a WAITING_PERMISSION run holds for a sign-in."""
    if run["status"] != O.RunStatus.WAITING_PERMISSION:
        return []
    return [t for t in store.objective_tasks(run["run_id"])
            if t["status"] == O.TaskStatus.WAITING
            and t.get("failure_kind") == O.FailureKind.USER_REQUIRED
            and _is_auth(str(t.get("evidence") or ""))]


def runs_parked_at_auth(store: Store) -> list[str]:
    """Ids of the runs waiting for a connector sign-in."""
    return [run["run_id"] for run in store.objective_runs(limit=100)
            if _auth_parked(store, run)]


def resume_after_auth(store: Store, *, reason: str = "",
                      run_id: str = "") -> list[str]:
    """Wake every run parked at an auth boundary - the connector_verify
    success hook.

    Deterministic and executor-free: flips WAITING_PERMISSION runs whose
    open WAITING task recorded a USER_REQUIRED auth failure back to
    RUNNING with an immediate next_wake. The running executor's driver
    tick (or the watchdog) picks them up; the boss repeats nothing.
    """
    resumed: list[str] = []
    for run in store.objective_runs(limit=100):
        if run_id and run["run_id"] != run_id:
            continue
        parked = _auth_parked(store, run)
        if not parked:
            continue
        for task in parked:
            store.update_objective_task(
                task["task_id"], status=O.TaskStatus.READY,
                failure_kind="", next_wake=_in_seconds(0.0))
        store.touch_objective_run(
            run["run_id"], status=O.RunStatus.RUNNING,
            next_wake=_in_seconds(0.0))
        store.append_objective_event(
            run["run_id"], "auth.resumed",
            detail={"reason": (reason or "credential verified")[:200]})
        resumed.append(run["run_id"])
        logger.info("objective.auth_resumed run_id=%s", run["run_id"])
    return resumed


def speak(store: Store, run_id: str) -> str:
    """Module-level prose summary, so the objective tools can narrate a run
    without constructing an executor (which would spawn a driver loop)."""
    run = store.objective_run(run_id)
    if run is None:
        return "I don't have a record of that run."
    status = run["status"]
    if status in O.RUN_TERMINAL:
        summary = run.get("summary") or {}
        words = {
            "succeeded": _plural(summary.get("succeeded", 0), "task"),
            "failed": _plural(summary.get("failed", 0), "task"),
            "skipped": _plural(summary.get("skipped", 0), "task"),
        }
        line = (f"The objective finished as {status.lower()}. "
                f"{words['succeeded']} succeeded")
        if summary.get("failed"):
            line += f", {words['failed']} failed"
        if summary.get("skipped"):
            line += f", {words['skipped']} were skipped"
        line += "."
        if summary.get("checks_failed"):
            line += (f" {summary['checks_failed']} of "
                     f"{summary.get('checks', 0)} checks inside them didn't work.")
        for failure in summary.get("failures", [])[:3]:
            if failure.get("capability") == O.UNMAPPED_CAPABILITY:
                line += (f" One thing I couldn't place: "
                         f"{_plain(failure.get('reason'))}.")
                continue
            line += (f" {failure.get('capability')} didn't work - "
                     f"{_plain(failure.get('reason'))}.")
        return line
    if status == O.RunStatus.PAUSED:
        return "That's paused - say the word and I'll pick it back up."
    if status in (O.RunStatus.WAITING_QUESTION,
                  O.RunStatus.WAITING_PERMISSION):
        return ("That's waiting on your answer before it can continue.")
    tasks = [t for t in store.objective_tasks(run_id)
             if not t.get("parent_id")]
    done = sum(1 for t in tasks if t["status"] in O.TASK_TERMINAL)
    return (f"That's still going - {done} of {len(tasks)} steps done "
            f"so far, no action needed from you.")


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}{'s' if count != 1 else ''}"


def _plain(text: str) -> str:
    """First sentence of an error, JSON noise removed, lowercase 'Error:'."""
    text = re.sub(r"\bError:\s*", "", text or "")
    return text.split(".")[0].strip(" :")[:160] or "it failed"


class RunWatchdog:
    """Finds runs whose owner vanished and reconciles them exactly once."""

    def __init__(self, executor: ContinuousTaskExecutor, *,
                 interval: float | None = None,
                 lease_timeout: float | None = None) -> None:
        self.executor = executor
        self.interval = interval if interval is not None \
            else DEFAULT_WATCHDOG_INTERVAL
        self.lease_timeout = lease_timeout if lease_timeout is not None \
            else DEFAULT_LEASE_TIMEOUT
        self._task: asyncio.Task | None = None
        #: Continuations this watchdog started and that have not finished:
        #: held so they cannot be collected mid-run and can be awaited.
        self._continuations: set[asyncio.Task] = set()

    def start(self) -> asyncio.Task | None:
        if self._task is None or self._task.done():
            self._task = asyncio.get_event_loop().create_task(self._loop())
        return self._task

    async def _loop(self) -> None:
        while True:
            try:
                await self.sweep_once()
            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception("watchdog sweep failed")
            await asyncio.sleep(self.interval)

    async def sweep_once(self) -> list[str]:
        """
        One scan. Orphan = non-terminal run with a stale lease (or none) and
        no scheduled future. Each orphan is reconciled exactly once: the dead
        portion is interrupted, the lease reacquired (generation bump), one
        round driven, and one continuation scheduled. When nothing is
        orphaned the sweep sleeps briefly so any pending continuation can
        finish before the caller inspects the run.
        """
        orphaned: list[str] = []
        for run in self.store_runs():
            run_id = run["run_id"]
            if not self._is_orphan(run):
                continue
            orphaned.append(run_id)
            await self._reconcile(run_id)
        if not orphaned:
            await asyncio.sleep(0.05)
        return orphaned

    def store_runs(self) -> list[dict]:
        return self.executor.store.objective_runs(limit=100)

    def _is_orphan(self, run: dict) -> bool:
        if run["status"] in O.RUN_TERMINAL:
            return False
        if run["status"] in O.RUN_WAITING_STATUSES:
            return False
        lease_live = bool(run.get("lease_executor_id") and run.get("lease_expiry")
                          and _fresh(run["lease_expiry"]))
        if lease_live:
            return False
        if run.get("next_wake") and not _due(run["next_wake"]):
            # A future wake belongs to the driver loop; not yet anyone's
            # problem.
            return False
        # No live lease and no future wake. A DUE wake with a dead lease is
        # the crash case exactly (PRD FR-005/FR-014, measured by
        # tests/test_chaos_restart.py): the process that scheduled the wake
        # died before taking it, and in this process the driver loop only
        # fires for runs it can lease - which it can, so either path
        # recovers; but the watchdog must not defer to a wake nobody will
        # take when the driver loop is not running.
        return True

    async def _reconcile(self, run_id: str) -> None:
        """Synchronous mutations plus one inline round, then a single
        scheduled continuation that completes the rest of the run."""
        store = self.executor.store
        store.append_objective_event(
            run_id, O.EVENT_WATCHDOG_ORPHANED,
            detail={"watchdog": self.executor.executor_id})
        for task in store.objective_tasks(run_id):
            committed = (_committed_result(store, run_id, task["task_id"])
                         if task["status"] == O.TaskStatus.RUNNING else None)
            if committed is not None:
                # DUR-001: its side effect happened before the executor died.
                # Completed from the commit record; never run again (V7-D42).
                store.update_objective_task(
                    task["task_id"], status=O.TaskStatus.SUCCEEDED,
                    result=committed, evidence=json.dumps(committed, default=str),
                    finished_at=now_iso())
                store.append_objective_event(
                    run_id, O.EVENT_TASK_RECOVERED, task_id=task["task_id"],
                    detail={"from": O.EVENT_SIDE_EFFECT_COMMITTED,
                            "watchdog": self.executor.executor_id})
                store.append_objective_event(
                    run_id, O.EVENT_TASK_SUCCEEDED, task_id=task["task_id"],
                    detail={"recovered": True})
                self.executor._ledger_evidence(run_id, task["task_id"], task,
                                               committed, passed=True)
                continue
            if task["status"] in (O.TaskStatus.RUNNING, O.TaskStatus.WAITING):
                store.update_objective_task(
                    task["task_id"], status=O.TaskStatus.INTERRUPTED,
                    finished_at=now_iso(),
                    evidence="interrupted by watchdog: executor vanished",
                )
                store.append_objective_event(
                    run_id, O.EVENT_TASK_INTERRUPTED,
                    task_id=task["task_id"],
                    detail={"by": "watchdog"})
        # Reacquire (generation bump) and drive one round inline so the
        # reconciled state is observable: the interrupted task is re-driven,
        # independent tasks run, and the remaining graph is left for the
        # continuation.
        self.executor.acquire(run_id)
        await self.executor._drive_one_round(run_id)
        run = store.objective_run(run_id)
        if run is not None and run["status"] == O.RUN_PAUSED:
            # The trajectory check paused it and asked the person (TRAJ-001):
            # no wake and no continuation, so nothing drives it before they
            # answer.
            return
        self.executor._schedule_wake(run_id)
        task = asyncio.get_event_loop().create_task(
            self._continue_after(run_id, delay=CONTINUATION_DELAY))
        self._continuations.add(task)
        task.add_done_callback(self._continuations.discard)

    async def drain(self) -> None:
        """Wait until every continuation this watchdog started has finished.
        A sweep returns while its continuation still runs, so whoever needs
        the run's settled state waits here rather than on a fixed sleep."""
        while self._continuations:
            await asyncio.gather(*list(self._continuations), return_exceptions=True)

    async def _continue_after(self, run_id: str, *,
                              delay: float = CONTINUATION_DELAY) -> None:
        await asyncio.sleep(delay)
        try:
            await self.executor._drive_until_done(run_id)
        except Exception:
            logger.exception("watchdog continuation failed for %s", run_id)

    def stop(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
