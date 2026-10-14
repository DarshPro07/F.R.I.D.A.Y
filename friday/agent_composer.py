"""
The Agent Composer (AGENT-003): start a specialist on an executor with a
compact context packet and explicit limits, and write the real worker
lifecycle to the agent ledger.

    request -> availability -> packet (capped) -> claim -> launcher -> end

* The context packet is built from *references*, not transcripts. Every
  item names where its full text lives; a packet over its cap is cut and
  says where the rest is (`build_packet`). The cap bounds what the caller
  supplies; the role's own instructions come from the registry.
* Limits are wall time, tool calls and spend. All three are required and
  positive: a run with "no limit" is not an authorised run. The composer
  stops the worker at the hard limit and records the stop.
* A launcher is how a worker is actually started: `async (job, channel) ->
  Outcome`. The channel is the worker's only way to tell the ledger it
  started or progressed, and it writes through `RunManager`, so the ledger
  row and the `executor_runs` row agree and a restart reads INTERRUPTED, never
  a phantom RUNNING. Executors with a builder get `ExecutorLauncher`; the
  others (opencode, codex) take an injected launcher.
* An executor that cannot run (unknown, no builder and no launcher, not
  installed, not signed in) is reported UNAVAILABLE with the reason. Nothing
  is claimed and no other runtime is substituted.

Which executor and model route to ask for is the caller's decision
(`executor_router.choose`, the routing policy); this module honours it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import uuid
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from friday import contracts as c
from friday import executor_router, roles
from friday.agent_ledger import CANCELLED, FAILED, SUCCEEDED, TERMINAL, AgentLedger
from friday.agent_registry import AgentRegistry
from friday.executors.runs import RunManager
from friday.specialist import Specialist, WorkspacePolicy
from friday import specialist as specialist_module
from friday.store import now_iso

logger = logging.getLogger("friday-agent")

UNAVAILABLE = "UNAVAILABLE"

#: Characters of caller-supplied context a worker is handed unless asked for less.
DEFAULT_PACKET_CAP = 6000
#: Below this the cut marker alone would not fit.
MIN_PACKET_CAP = 200

_REF_LIMIT = 120          # one reference, as printed in the cut marker
_LIMIT_NAMES = {"wall_s": "wall time", "tool_calls": "tool calls",
                "spend_usd": "spend"}


class ComposerError(ValueError):
    """The request is malformed, as opposed to a runtime being unavailable."""


# -- the context packet ----------------------------------------------------


@dataclass(frozen=True)
class ContextItem:
    label: str
    text: str
    #: Where the full text lives (a path, a ledger row, a run id). Required:
    #: a packet carries references to the rest, not the rest.
    ref: str

    def __post_init__(self) -> None:
        if not self.ref.strip():
            raise ComposerError(
                f"context item {self.label!r} needs a reference to its full text")


@dataclass(frozen=True)
class Packet:
    text: str
    cap: int
    truncated: bool
    omitted_chars: int
    #: References of every item that was cut or left out.
    refs: tuple[str, ...]


def build_packet(items: tuple[ContextItem, ...], *, cap: int) -> Packet:
    """Render `items` into at most `cap` characters, pointing at what was cut."""
    if cap < MIN_PACKET_CAP:
        raise ComposerError(f"packet cap {cap} is below the minimum {MIN_PACKET_CAP}")
    rendered = [(item, f"## {item.label} ({item.ref})\n{item.text}") for item in items]
    whole = "\n\n".join(block for _, block in rendered)
    if len(whole) <= cap:
        return Packet(whole, cap, False, 0, ())

    # The tail is sized first so the body can be given exactly what is left.
    # It names the items that did not make it in whole, which is decided by
    # filling the body greedily against the cap minus the worst-case tail.
    names = [item.ref[:_REF_LIMIT] for item in items]
    budget = cap - min(cap // 2, len(_tail(names)))
    body: list[str] = []
    used, cut = 0, []
    omitted = 0
    for item, block in rendered:
        sep = 2 if body else 0
        if not cut and used + sep + len(block) <= budget:
            body.append(block)
            used += sep + len(block)
            continue
        room = budget - used - sep
        header = f"## {item.label} ({item.ref})\n"
        if not cut and room > len(header):
            kept = block[:room]
            body.append(kept)
            used += sep + len(kept)
            omitted += len(block) - len(kept)
        else:
            omitted += len(block)
        cut.append(item.ref)
    tail = _tail([ref[:_REF_LIMIT] for ref in cut])
    text = "\n\n".join(body)
    text = (text[: max(0, cap - len(tail) - 2)] + "\n\n" + tail)[:cap]
    return Packet(text, cap, True, omitted, tuple(cut))


def _tail(refs: list[str]) -> str:
    return "[context cut at the cap; the rest is by reference: " + ", ".join(refs) + "]"


# -- limits, request, outcome ----------------------------------------------


@dataclass(frozen=True)
class Limits:
    wall_s: float
    tool_calls: int
    spend_usd: float

    def __post_init__(self) -> None:
        for name in _LIMIT_NAMES:
            if not getattr(self, name) > 0:
                raise ComposerError(
                    f"limit {name} must be set and positive, got {getattr(self, name)!r}")


@dataclass(frozen=True)
class Request:
    role_id: str
    executor_id: str
    model_route: str
    goal: str
    workspace: str
    limits: Limits
    context: tuple[ContextItem, ...] = ()
    packet_cap: int = DEFAULT_PACKET_CAP
    allowed_tools: tuple[str, ...] = ()
    allowed_paths: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()
    acceptance: tuple[str, ...] = ()
    parent_run_id: str = ""
    run_id: str = field(default_factory=lambda: f"AGT-{uuid.uuid4().hex[:12]}")


@dataclass(frozen=True)
class Outcome:
    """How a launcher says its worker ended."""

    status: str                 # SUCCEEDED, FAILED or CANCELLED
    summary: str = ""
    #: What was observed. A success without it is a claim, not a result.
    evidence: str = ""

    def __post_init__(self) -> None:
        if self.status not in (SUCCEEDED, FAILED, CANCELLED):
            raise ComposerError(f"{self.status!r} is not a worker outcome")
        if self.status == SUCCEEDED and not self.evidence.strip():
            raise ComposerError("a success needs evidence")


@dataclass(frozen=True)
class Result:
    run_id: str
    executor_id: str
    status: str                 # SUCCEEDED, FAILED, CANCELLED or UNAVAILABLE
    reason: str = ""
    #: wall_s, tool_calls or spend_usd when a hard limit ended the run.
    stopped_by: str = ""
    summary: str = ""
    usage: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Job:
    request: Request
    bundle: object              # the executor TaskBundle for this specialist
    packet: Packet
    run_id: str
    limits: Limits


class Channel:
    """The worker's line to the ledger. Usage is checked against the limits."""

    def __init__(self, composer: "Composer", job: Job) -> None:
        self._composer, self._job = composer, job
        self.usage = {"tool_calls": 0, "spend_usd": 0.0}
        self.breach: tuple[str, str] | None = None
        self.task: asyncio.Future | None = None
        self._started = False

    def started(self, pid: int | None = None) -> None:
        """The worker is running. `pid` defaults to this process, which is
        what an in-process launcher truthfully is."""
        manager = self._composer.manager
        manager.open(self._job.bundle, executor_type=self._job.request.executor_id)
        manager.running(self._job.run_id, pid=pid or os.getpid())
        self._started = True

    def progress(self, message: str, *, tool_calls: int = 0,
                 spend_usd: float = 0.0) -> None:
        """Record a step and its cost; at a hard limit the worker is stopped."""
        if not self._started:
            raise ComposerError("progress before started()")
        self.usage["tool_calls"] += tool_calls
        self.usage["spend_usd"] += spend_usd
        self._composer.manager.saw(self._job.run_id, message)
        limits = self._job.limits
        for name in ("tool_calls", "spend_usd"):
            if self.breach is None and self.usage[name] > getattr(limits, name):
                self.breach = (name, f"{name} {self.usage[name]} exceeded the "
                                     f"limit {getattr(limits, name)}")
                if self.task is not None:
                    self.task.cancel()

    def stop(self, name: str, why: str) -> None:
        """The worker has been stopped by a limit it enforces itself. Record
        it as a limit stop (the first breach wins); nothing is cancelled."""
        if self.breach is None:
            self.breach = (name, why)


Launcher = Callable[[Job, Channel], Awaitable[Outcome]]


# -- availability ----------------------------------------------------------

def default_probe(executor_id: str) -> str | None:
    """Why `executor_id` cannot run right now, or None when it can."""
    known = executor_router.BY_ID.get(executor_id)
    if known is None:
        return f"unknown executor {executor_id!r}"
    found = known.path()
    if found is None:
        return "not installed"
    from friday import cli_model_provider

    # BACKENDS is keyed by the executor id ("claude", "codex"); an executor
    # with no CLI sign-in (hermes) has no entry and is checked by its binary.
    backend = cli_model_provider.BACKENDS.get(executor_id)
    if backend is not None:
        ok, detail = backend().signed_in()
        if not ok:
            return f"not signed in: {detail}"
    return known.validate_binary(found)


#: How long a stopped executor gets to wind itself down (release its sandbox
#: and lease) before its task is cancelled outright.
STOP_GRACE_S = 15.0
_TOOL_LIMIT_MARK = "ENVELOPE: tool-call limit"
_EXECUTOR_TIMEOUT = re.compile(r"\bexceeded \d+s\b")


class ExecutorLauncher:
    """Runs a request on the executor Friday has a builder for. The executor
    writes its own start/progress/finish through `RunManager`."""

    def __init__(self, store) -> None:
        self.store = store

    async def __call__(self, job: Job, channel: Channel) -> Outcome:
        executor = executor_router.build(job.request.executor_id, self.store)
        envelope = c.Envelope(run_id=job.run_id,
                              tool_call_limit=job.limits.tool_calls)
        work = asyncio.ensure_future(executor.execute(
            job.bundle, timeout=job.limits.wall_s, envelope=envelope))
        try:
            result = await asyncio.shield(work)
        except asyncio.CancelledError:
            await self._stop(executor, work, job.run_id)
            raise
        error = result.error or ""
        if result.status == c.SUCCEEDED and result.verification is not None:
            v = result.verification
            return Outcome(SUCCEEDED, "", evidence=f"{v.method}: {v.evidence}")
        if result.status == c.CANCELLED:
            return Outcome(CANCELLED, error)
        # The executor enforces the tool-call limit and its own timeout; say
        # so, so the stop is recorded as a limit stop and not a plain failure.
        if result.status == c.NOT_PERMITTED and error.startswith(_TOOL_LIMIT_MARK):
            channel.stop("tool_calls", error[:400])
        elif result.status == c.FAILED and _EXECUTOR_TIMEOUT.search(error):
            channel.stop("wall_s", error[:400])
        return Outcome(FAILED, (error or result.status)[:400])

    @staticmethod
    async def _stop(executor, work: asyncio.Future, run_id: str) -> None:
        """The composer cancelled the worker. Ask the executor to stop, which
        kills the process tree, and let `execute` return on its own so its
        sandbox is closed and its lease released; cancel it only if it will
        not (or cannot be asked to) stop."""
        if work.done():
            return
        stop = getattr(executor, "cancel", None)
        if stop is not None:
            try:
                await asyncio.shield(stop())
            except Exception:
                logger.exception("executor.cancel failed for %s", run_id)
        else:
            logger.warning("executor for %s has no cancel(); cancelling its task",
                           run_id)
        if stop is not None:
            await asyncio.wait({work}, timeout=STOP_GRACE_S)
        if not work.done():
            work.cancel()


# -- the composer ----------------------------------------------------------


class Composer:
    def __init__(self, ledger: AgentLedger, *,
                 launchers: dict[str, Launcher] | None = None,
                 probe: Callable[[str], str | None] = default_probe) -> None:
        self.ledger = ledger
        self.manager = RunManager(ledger.store)
        self.launchers = dict(launchers or {})
        self.probe = probe
        self._tasks: dict[str, asyncio.Future] = {}
        self._cancelled: set[str] = set()

    def cancel(self, run_id: str) -> bool:
        """Ask a running worker to stop. False when no such run is in flight."""
        task = self._tasks.get(run_id)
        if task is None or task.done():
            return False
        self._cancelled.add(run_id)
        task.cancel()
        return True

    def _launcher_for(self, executor_id: str) -> tuple[Launcher | None, str]:
        injected = self.launchers.get(executor_id)
        if injected is not None:
            return injected, ""
        known = executor_router.BY_ID.get(executor_id)
        if known is None:
            return None, f"unknown executor {executor_id!r}"
        if not known.buildable:
            return None, (f"{executor_id} has no builder in FRIDAY and no "
                          "launcher was supplied")
        return ExecutorLauncher(self.ledger.store), ""

    async def run(self, req: Request) -> Result:
        # The catalogue's role, else a role the owner approved for promotion.
        role = AgentRegistry(self.ledger.store).role(req.role_id)
        if role is None:
            raise ComposerError(
                f"unknown role {req.role_id!r}; roles: {sorted(roles.BY_ID)}")
        launcher, reason = self._launcher_for(req.executor_id)
        if not reason:
            # The probe spawns subprocesses (`<bin> --version`, sign-in check);
            # keep them off the event loop of the live voice / MCP process.
            reason = await asyncio.to_thread(self.probe, req.executor_id) or ""
        if launcher is None or reason:
            logger.info("composer.unavailable %s: %s", req.executor_id, reason)
            return Result(req.run_id, req.executor_id, UNAVAILABLE, reason=reason)

        packet = build_packet(req.context, cap=req.packet_cap)
        spec = Specialist(
            role_id=req.role_id, executor_id=req.executor_id,
            model_route=req.model_route, skills=req.skills,
            allowed_tools=req.allowed_tools,
            workspace=WorkspacePolicy(allowed_paths=req.allowed_paths),
            role=None if req.role_id in roles.BY_ID else role)
        bundle = spec.bundle(
            req.goal, req.workspace, run_id=req.run_id,
            context=(packet.text,) if packet.text else (),
            acceptance=req.acceptance)
        run_id = req.run_id
        specialist_module.record(self.ledger, spec, run_id=run_id, task=req.goal,
                                 workspace=req.workspace,
                                 parent_run_id=req.parent_run_id)
        own = [f"agent_runs:{run_id}"]
        self.ledger.event(run_id, "packet", json.dumps({
            "cap": packet.cap, "length": len(packet.text),
            "truncated": packet.truncated, "omitted_chars": packet.omitted_chars,
            "refs": list(packet.refs)}), list(packet.refs) or own)
        self.ledger.event(run_id, "limits", json.dumps({
            "wall_s": req.limits.wall_s, "tool_calls": req.limits.tool_calls,
            "spend_usd": req.limits.spend_usd,
            "allowed_tools": list(req.allowed_tools),
            "allowed_paths": list(req.allowed_paths)}), own)

        job = Job(req, bundle, packet, run_id, req.limits)
        channel = Channel(self, job)
        task = asyncio.ensure_future(launcher(job, channel))
        channel.task = task
        self._tasks[run_id] = task
        try:
            done, _ = await asyncio.wait({task}, timeout=req.limits.wall_s)
            if not done:
                channel.breach = ("wall_s", f"wall_s exceeded the limit "
                                            f"{req.limits.wall_s}")
                task.cancel()
            return await self._settle(task, channel, req)
        except asyncio.CancelledError:
            task.cancel()
            self._end(run_id, CANCELLED, "cancelled", "composer was cancelled")
            raise
        finally:
            self._tasks.pop(run_id, None)
            self._cancelled.discard(run_id)

    async def _settle(self, task, channel: Channel, req: Request) -> Result:
        run_id, usage = req.run_id, dict(channel.usage)
        outcome, error = None, ""
        try:
            outcome = await task
        except asyncio.CancelledError:
            if not (channel.breach or run_id in self._cancelled):
                raise
        except Exception as exc:                   # recorded below, never dropped
            logger.exception("worker %s raised", run_id)
            error = f"{type(exc).__name__}: {exc}"

        if channel.breach is not None:
            name, why = channel.breach
            self.ledger.event(run_id, "limit_stop", why, [f"agent_runs:{run_id}"])
            self._end(run_id, FAILED, None, why)
            return Result(run_id, req.executor_id, FAILED, reason=why,
                          stopped_by=name, usage=usage)
        if run_id in self._cancelled:
            self._end(run_id, CANCELLED, "cancelled", "cancelled on request")
            return Result(run_id, req.executor_id, CANCELLED,
                          reason="cancelled on request", usage=usage)
        if error:
            self._end(run_id, FAILED, "failed", error)
            return Result(run_id, req.executor_id, FAILED, reason=error, usage=usage)

        message = outcome.summary or outcome.status
        if outcome.evidence:
            message = f"{message} [{outcome.evidence[:300]}]"
        kind = {SUCCEEDED: "completed", FAILED: "failed",
                CANCELLED: "cancelled"}[outcome.status]
        self._end(run_id, outcome.status, kind, message)
        return Result(run_id, req.executor_id, outcome.status,
                      reason="" if outcome.status == SUCCEEDED else message,
                      summary=outcome.summary, usage=usage)

    def _end(self, run_id: str, status: str, kind: str | None, message: str) -> None:
        """Close the run in both tables, once, with evidence."""
        refs = [f"agent_runs:{run_id}"]
        executor_row = self.ledger.store.executor_run(run_id)
        if executor_row is not None:
            refs.append(f"executor_runs:{run_id}")
            if executor_row["status"] not in TERMINAL:
                self.ledger.store.touch_executor_run(
                    run_id, status=status, pid=None, ended_at=now_iso(),
                    completion_evidence=message[:1000])
        if kind:
            self.ledger.event(run_id, kind, message[:1000], refs)
        if self.ledger.run(run_id)["status"] not in TERMINAL:
            self.ledger.finish(run_id, status, refs, message[:1000])
