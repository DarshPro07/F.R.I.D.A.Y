"""
Durable objective runs: statuses, the task graph, and the compiler.

The P0 gate: one multi-step objective must reach a terminal state without
the user saying "Continue". Everything here is pure data and state-machine
logic - no I/O, no asyncio - so it is testable without the engine. The
compiler takes an LLM-produced plan (a list of capability tasks with
dependencies), validates it against the capability manifest, topologically
orders it, refuses cycles, and persists it to the store. Progression after
compilation belongs to `friday/continuous.py`.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone

from friday import contracts, dag
from friday.contracts import new_run_id, now_iso
from friday.store import Store

logger = logging.getLogger("friday-objectives")


# ---------------------------------------------------------------------------
# State vocabulary (shared by store, engine and tests)
# ---------------------------------------------------------------------------

TASK_QUEUED = "QUEUED"
TASK_READY = "READY"
TASK_RUNNING = "RUNNING"
TASK_WAITING = "WAITING"
TASK_SUCCEEDED = "SUCCEEDED"
TASK_FAILED = "FAILED"
TASK_SKIPPED = "SKIPPED"
TASK_INTERRUPTED = "INTERRUPTED"
#: Same failure fingerprint kept recurring past MAX_STRATEGY_CHANGES with no
#: new evidence - retrying further is a loop, not progress.
TASK_BLOCKED = "BLOCKED"

TASK_STATUSES = (TASK_QUEUED, TASK_READY, TASK_RUNNING, TASK_WAITING,
                 TASK_SUCCEEDED, TASK_FAILED, TASK_SKIPPED, TASK_INTERRUPTED,
                 TASK_BLOCKED)
TASK_TERMINAL = (TASK_SUCCEEDED, TASK_FAILED, TASK_SKIPPED, TASK_BLOCKED)


class TaskStatus:
    QUEUED = TASK_QUEUED
    READY = TASK_READY
    RUNNING = TASK_RUNNING
    WAITING = TASK_WAITING
    SUCCEEDED = TASK_SUCCEEDED
    FAILED = TASK_FAILED
    SKIPPED = TASK_SKIPPED
    INTERRUPTED = TASK_INTERRUPTED
    BLOCKED = TASK_BLOCKED


RUN_RUNNING = "RUNNING"
RUN_COMPLETED = "COMPLETED"
RUN_PARTIAL = "PARTIAL"
RUN_FAILED = "FAILED"
RUN_CANCELLED = "CANCELLED"
RUN_WAITING_QUESTION = "WAITING_QUESTION"
RUN_WAITING_PERMISSION = "WAITING_PERMISSION"
RUN_PAUSED = "PAUSED"

RUN_STATUSES = (RUN_RUNNING, RUN_COMPLETED, RUN_PARTIAL, RUN_FAILED,
                RUN_CANCELLED, RUN_WAITING_QUESTION, RUN_WAITING_PERMISSION,
                RUN_PAUSED)
RUN_TERMINAL = (RUN_COMPLETED, RUN_PARTIAL, RUN_FAILED, RUN_CANCELLED)

#: Statuses where the run is deliberately not executing. The driver loop, the
#: orphan watchdog and the invariant all treat these as legitimate waits
#: rather than stranded runs - nothing to heal, nothing to resume on its own.
#: `RUN_PAUSED` is the control-plane pause: nobody woke it, it stays put until
#: an explicit resume.
RUN_WAITING_STATUSES = (RUN_WAITING_QUESTION, RUN_WAITING_PERMISSION,
                        RUN_PAUSED)


class RunStatus:
    RUNNING = RUN_RUNNING
    COMPLETED = RUN_COMPLETED
    PARTIAL = RUN_PARTIAL
    FAILED = RUN_FAILED
    CANCELLED = RUN_CANCELLED
    WAITING_QUESTION = RUN_WAITING_QUESTION
    WAITING_PERMISSION = RUN_WAITING_PERMISSION
    PAUSED = RUN_PAUSED


FAILURE_TRANSIENT = "TRANSIENT"
FAILURE_PROVIDER_DOWN = 'PROVIDER_DOWN'
FAILURE_STRUCTURAL = "STRUCTURAL"
FAILURE_CAPABILITY_MISSING = "CAPABILITY_MISSING"
FAILURE_POLICY_BLOCK = "POLICY_BLOCK"
FAILURE_INVALID_ARGUMENT = "INVALID_ARGUMENT"
FAILURE_NOT_CONFIGURED = "NOT_CONFIGURED"
FAILURE_USER_REQUIRED = "USER_REQUIRED"
FAILURE_CONNECTIVITY = "CONNECTIVITY"
#: A usage cap, not an outage - the provider named WHEN it clears, so a
#: retry belongs on the next candidate immediately, not behind
#: PROVIDER_BACKOFF_SECONDS on the same provider.
FAILURE_CAPPED = "CAPPED"

FAILURE_KINDS = (FAILURE_TRANSIENT, FAILURE_PROVIDER_DOWN, FAILURE_STRUCTURAL,
                 FAILURE_CAPABILITY_MISSING, FAILURE_POLICY_BLOCK,
                 FAILURE_INVALID_ARGUMENT, FAILURE_NOT_CONFIGURED,
                 FAILURE_USER_REQUIRED, FAILURE_CONNECTIVITY, FAILURE_CAPPED)

#: The only kinds that may be retried, and only a bounded number of times.
RETRYABLE_KINDS = (FAILURE_TRANSIENT, FAILURE_PROVIDER_DOWN,
                   FAILURE_CONNECTIVITY, FAILURE_CAPPED)


class FailureKind:
    TRANSIENT = FAILURE_TRANSIENT
    PROVIDER_DOWN = FAILURE_PROVIDER_DOWN
    STRUCTURAL = FAILURE_STRUCTURAL
    CAPABILITY_MISSING = FAILURE_CAPABILITY_MISSING
    POLICY_BLOCK = FAILURE_POLICY_BLOCK
    INVALID_ARGUMENT = FAILURE_INVALID_ARGUMENT
    NOT_CONFIGURED = FAILURE_NOT_CONFIGURED
    USER_REQUIRED = FAILURE_USER_REQUIRED
    CONNECTIVITY = FAILURE_CONNECTIVITY
    CAPPED = FAILURE_CAPPED


#: Events the engine appends to the continuation trace.
EVENT_RUN_CREATED = "run.created"
EVENT_RUN_STARTED = "run.started"
EVENT_TASK_STARTED = "task.started"
EVENT_TASK_SUCCEEDED = "task.succeeded"
EVENT_TASK_FAILED = "task.failed"
EVENT_TASK_RETRY = "task.retry"          # FR-054: a retried attempt is recorded
EVENT_TASK_BLOCKED = "task.blocked"
EVENT_TASK_SKIPPED = "task.skipped"
EVENT_TASK_INTERRUPTED = "task.interrupted"
EVENT_WORKER_WAITING = "worker.waiting"
EVENT_WORKER_RECONCILED = "worker.reconciled"
EVENT_CONTINUATION_SCHEDULED = "continuation.scheduled"
EVENT_LEASE_ACQUIRED = "lease.acquired"
EVENT_LEASE_RECONCILED = "lease.reconciled"
EVENT_WATCHDOG_ORPHANED = "watchdog.orphaned"
EVENT_RUN_COMPLETED = "run.completed"
EVENT_RUN_PARTIAL = "run.partial"
EVENT_RUN_FAILED = "run.failed"
EVENT_RUN_CANCELLED = "run.cancelled"
EVENT_RUN_PAUSED = "run.paused"
EVENT_RUN_RESUMED = "run.resumed"
EVENT_MANUAL_CONTINUE_REQUIRED = "manual_continue.required"
#: PRD v7 DUR-001: a side-effecting call returned successfully - recorded
#: before anything else, so recovery never runs it a second time.
EVENT_SIDE_EFFECT_COMMITTED = "side_effect.committed"
#: A task completed from its commit record after its executor died.
EVENT_TASK_RECOVERED = "task.recovered"

#: What each task event does to a task's status when an objective is replayed.
_EVENT_TASK_STATUS = {
    EVENT_TASK_STARTED: TaskStatus.RUNNING,
    EVENT_TASK_SUCCEEDED: TaskStatus.SUCCEEDED,
    EVENT_TASK_RECOVERED: TaskStatus.SUCCEEDED,
    EVENT_TASK_FAILED: TaskStatus.FAILED,
    EVENT_TASK_BLOCKED: TaskStatus.BLOCKED,
    EVENT_TASK_SKIPPED: TaskStatus.SKIPPED,
    EVENT_TASK_INTERRUPTED: TaskStatus.INTERRUPTED,
    EVENT_WORKER_WAITING: TaskStatus.WAITING,
    EVENT_TASK_RETRY: TaskStatus.QUEUED,
}


def replay_task_statuses(events: list[dict]) -> dict[str, str]:
    """Each task's status as the event log alone says it is (DUR-001).

    Folds the events, oldest first, into {task_id: status}. A task whose
    side effect committed but whose success was never recorded is
    SUCCEEDED: the commit is the truth, not the missing row update. Tasks
    that never appear in an event are absent."""
    statuses: dict[str, str] = {}
    for event in events:
        task_id = event.get("task_id")
        if not task_id:
            continue
        name = event.get("event")
        if name == EVENT_SIDE_EFFECT_COMMITTED:
            statuses[task_id] = TaskStatus.SUCCEEDED
        elif name in _EVENT_TASK_STATUS:
            if statuses.get(task_id) == TaskStatus.SUCCEEDED and name == EVENT_TASK_STARTED:
                continue  # a committed task is never started again
            statuses[task_id] = _EVENT_TASK_STATUS[name]
    return statuses


def idempotency_key(run_id: str, task_id: str) -> str:
    """The key a side-effecting capability can use to do a call once.

    Stable across the attempts of one task on purpose: the attempt that
    died between the call returning and its commit record is re-run under
    the same key, so a capability that honours it makes the change once."""
    return f"{run_id}:{task_id}"


#: A side-effecting call returned successfully; written by the port the moment
#: it has the result, keyed by idempotency key, so a re-run under that key
#: gets the recorded result instead of making the change again.
EVENT_CALL_RETURNED = "call.returned"


def recorded_call(events: list[dict], key: str) -> dict | None:
    """The result a call under `key` already returned, if one was recorded."""
    for event in events:
        detail = event.get("detail")
        if (event.get("event") == EVENT_CALL_RETURNED and isinstance(detail, dict)
                and detail.get("key") == key and isinstance(detail.get("result"), dict)):
            return detail["result"]
    return None

UNMAPPED_CAPABILITY = 'objective.unmapped'

#: A task id may only be referenced by dependencies after it is defined.
_TASK_REF = re.compile(r"\{\{tasks\.([A-Za-z0-9_-]+)\.([A-Za-z0-9_.-]+)\}\}")


class CompileError(ValueError):
    """The plan cannot become a graph. The message says exactly why."""


class LiteralDropped(CompileError):
    """
    The plan does not use something the person named - a file, a path, the
    words they gave (PRD v7 INTENT-001). Refused before anything is written:
    a run that silently used another name would do what they did not ask.
    """


class ForbiddenByContract(CompileError):
    """
    The plan does what the person forbade (PRD v7 BCON-001): an action class
    they ruled out, or a named thing they protected from one. Refused before
    anything is written, whoever wrote the plan.
    """


def forbidden_steps(contract, tasks: list[dict]) -> list[str]:
    """Each planned step the contract's governance rules out, and why."""
    governance = dict(contract.governance or {})
    forbidden = set(governance.get("forbidden") or ())
    protected = dict(governance.get("protected") or {})
    found = []
    for spec in tasks:
        capability = str(spec.get("capability") or "")
        kind = contracts.action_class(capability)
        if not kind:
            continue
        if kind in forbidden:
            found.append(f"{capability} ({kind} was forbidden)")
            continue
        values = _strings(spec.get("arguments") or {})
        for name in protected.get(kind, ()):
            if any(name in value for value in values):
                found.append(f"{capability} on {name} ({kind} of it was forbidden)")
    return found


def _strings(value) -> list[str]:
    """Every string inside a task's arguments, however nested."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for item in value.values() for s in _strings(item)]
    if isinstance(value, (list, tuple)):
        return [s for item in value for s in _strings(item)]
    return []


def _dropped_literals(contract, tasks: list[dict], known: set) -> list[str]:
    """The contract's literals no runnable task carries verbatim."""
    carried = [s for spec in tasks if spec.get("capability") in known
               for s in _strings(spec.get("arguments") or {})]
    return [literal for literal in contract.exact_literals
            if not any(literal in value for value in carried)]


class CompositionNeedsConfirmation(CompileError):
    """
    The plan's capabilities together could send what they read (PRD v7
    CAP-002), and the person has not agreed yet. A CompileError on purpose:
    a caller that does not know how to ask treats it as the refusal it is.
    """

    def __init__(self, verdict, capabilities) -> None:
        super().__init__(verdict.reason)
        self.verdict = verdict
        self.capabilities = tuple(capabilities)


def composition_of(capabilities, *, provenance: str, attended: bool):
    """policy.composition_verdict over these capabilities' policy tool ids.
    A capability none of whose ids has a category cannot be judged, and is
    handed over as unknown rather than left out (Slice 2 review)."""
    from friday import capabilities as registry
    from friday import policy

    tools, unknown = [], []
    for cap in capabilities:
        found = registry.by_id(cap)
        if found is None:
            continue
        ids = found.policy_tool_ids()
        if any(tool in policy.TOOL_CATEGORIES for tool in ids):
            tools.extend(ids)
        else:
            unknown.append(found.id)
    return policy.composition_verdict(tools, provenance=provenance,
                                      attended=attended, unknown=unknown)


# Restored from the .pyc oracle: proven by a LOAD_CONST/STORE_NAME
# pair in the running system's bytecode, present in no source candidate.
COMPOSITE = 'composite'
OBJECTIVE_CONTROL_CAPABILITIES = frozenset({
    "objective_start", "objective_status", "objective_pause", "objective_resume",
    "objective_cancel", "objective_list", "objective_history",
})


@dataclass
class CompiledTask:
    capability: str
    arguments: dict = field(default_factory=dict)
    dependencies: tuple[str, ...] = ()
    max_attempts: int = 3
    children: tuple['CompiledChild', ...] = ()

    def to_dict(self) -> dict:
        return {
            "capability": self.capability,
            "arguments": self.arguments,
            "dependencies": list(self.dependencies),
            "max_attempts": self.max_attempts,
            "children": [child.to_dict() for child in self.children],
        }


@dataclass
class CompiledChild:
    """One leaf of a composite, with an optional reason it will not run.

    `skipped_because` is the only pre-settlement a plan may declare, and the
    restriction is deliberate: an audit needs to say "this one needs a person
    to say yes" without pretending it ran, and nothing anywhere may declare a
    task already SUCCEEDED. That would be evidence for work nobody did.
    """

    capability: str
    arguments: dict = field(default_factory=dict)
    skipped_because: str = ""
    max_attempts: int = 3

    def to_dict(self) -> dict:
        return {
            "capability": self.capability,
            "arguments": self.arguments,
            "skipped_because": self.skipped_because,
            "max_attempts": self.max_attempts,
        }


@dataclass
class TaskResultSummary:
    """Compact continuation context: what one task produced, not its history."""

    run_id: str
    task_id: str
    capability: str
    status: str
    attempts: int
    result_keys: tuple[str, ...] = ()
    evidence: str = ""
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "task_id": self.task_id,
            "capability": self.capability,
            "status": self.status,
            "attempts": self.attempts,
            "result_keys": list(self.result_keys),
            "evidence": self.evidence,
            "error": self.error,
        }


@dataclass
class TaskContext:
    """What the LLM may see when it must plan or explain a continuation."""

    run_id: str
    objective_summary: str
    ready: list[TaskResultSummary] = field(default_factory=list)
    waiting: list[TaskResultSummary] = field(default_factory=list)
    done: list[TaskResultSummary] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "objective_summary": self.objective_summary,
            "ready": [t.to_dict() for t in self.ready],
            "waiting": [t.to_dict() for t in self.waiting],
            "done": [t.to_dict() for t in self.done],
        }


def _summarise(row: dict) -> TaskResultSummary:
    result = row.get("result") or {}
    keys = tuple(sorted(str(k) for k in result.keys())) if isinstance(
        result, dict) else ()
    return TaskResultSummary(
        run_id=row["run_id"],
        task_id=row["task_id"],
        capability=row["capability"],
        status=row["status"],
        attempts=row["attempts"],
        result_keys=keys,
        evidence=row.get("evidence") or "",
        error=row.get("failure_kind") or "",
    )


def task_context(store: Store, run_id: str) -> TaskContext:
    """Build the continuation context from persisted rows only."""
    run = store.objective_run(run_id)
    if run is None:
        raise KeyError(f"no objective run {run_id!r}")
    ctx = TaskContext(run_id=run_id,
                      objective_summary=run["objective_summary"])
    for row in store.objective_tasks(run_id):
        summary = _summarise(row)
        if row["status"] in TASK_TERMINAL:
            ctx.done.append(summary)
        elif row["status"] == TASK_READY:
            ctx.ready.append(summary)
        else:
            ctx.waiting.append(summary)
    return ctx


def continuity_state(store: Store, run_id: str) -> dict:
    """Durable parent-objective cursor projected from authoritative rows.

    No conversational prose is state. Current/next/verified steps and linked
    WorkRuns are derived from the persisted task graph, so this survives
    compaction and restart without duplicating mutable cursor columns.
    """
    run = store.objective_run(run_id)
    if run is None:
        raise KeyError(f"no objective run {run_id!r}")

    tasks = [task for task in store.objective_tasks(run_id)
             if not task.get("parent_id")]
    open_tasks = [task for task in tasks
                  if task["status"] not in TASK_TERMINAL]
    current = next(
        (task for task in open_tasks
         if task["status"] in (TASK_RUNNING, TASK_WAITING, TASK_READY)),
        open_tasks[0] if open_tasks else None)
    verified = [task for task in tasks if task["status"] == TASK_SUCCEEDED]

    active_workers, complete_workers, stopped_workers = [], [], []
    for task in tasks:
        result = task.get("result") or {}
        worker_id = (str(result.get("work_run_id") or "")
                     if isinstance(result, dict) else "")
        if not worker_id:
            continue
        if task["status"] in (TASK_WAITING, TASK_RUNNING):
            active_workers.append(worker_id)
        elif task["status"] == TASK_SUCCEEDED:
            complete_workers.append(worker_id)
        else:
            stopped_workers.append(worker_id)

    if run["status"] in RUN_TERMINAL:
        state = run["status"]
    elif run["status"] == RUN_WAITING_QUESTION:
        state = "WAITING_USER_INPUT"
    elif run["status"] == RUN_WAITING_PERMISSION:
        state = "WAITING_USER_INPUT"
    elif current and active_workers:
        state = "WAITING_WORKER"
    elif current and current.get("failure_kind") in RETRYABLE_KINDS:
        state = "RECOVERING"
    else:
        state = "RUNNING"

    blocker_task = next(
        (task for task in open_tasks if task.get("failure_kind")), None)

    return {
        "objective_id": run_id,
        "objective": run["objective_summary"],
        "state": state,
        "current_phase": (current["capability"].split("_", 1)[0]
                          if current else "complete"),
        "current_step": current["task_id"] if current else "",
        "last_verified_step": (verified[-1]["task_id"] if verified else ""),
        "next_action": current["capability"] if current else "",
        "active_workrun_ids": active_workers,
        "completed_workrun_ids": complete_workers,
        "stopped_workrun_ids": stopped_workers,
        "blocker": (blocker_task.get("evidence") or "") if blocker_task
                   else "",
        "blocker_class": (blocker_task.get("failure_kind") or "")
                         if blocker_task else "",
        "resume_policy": "RUN_UNTIL_DONE",
        "retry_count": sum(int(task.get("attempts") or 0)
                           for task in tasks),
        "updated_at": run["updated_at"],
    }


#: How long a compiled objective's envelope lasts. Objectives are durable -
#: they survive restarts and wait on people - so this is days, not minutes.
#: An objective still going when its envelope expires stops acting until the
#: person re-issues it.
ENVELOPE_LIFETIME = timedelta(days=7)


#: How long a person's yes to one pending action stands (PRD v7
#: APPROVAL-001). Past it the run asks again.
APPROVAL_TTL = timedelta(hours=24)


def plan_hash(store: Store, run_id: str) -> str:
    """
    What the objective will do, as one comparable value: each task's
    capability, arguments and dependencies, in plan order. An approval is
    bound to it (APPROVAL-001), so a material change to the plan - a task
    added, an argument amended - leaves the yes behind. A status change is
    not a plan change, and the approval marker itself is not part of it.
    """
    import hashlib

    rows = []
    for task in store.objective_tasks(run_id):
        arguments = {k: v for k, v in dict(task.get("arguments") or {}).items()
                     if k != "approved_by"}
        rows.append([task.get("task_id"), task.get("capability"), arguments,
                     sorted(task.get("dependencies") or [])])
    return hashlib.sha256(json.dumps(rows, sort_keys=True, default=str)
                          .encode("utf-8")).hexdigest()


def envelope_for(run_id: str, capabilities, *, provenance: str,
                 attended: bool) -> contracts.Envelope:
    """
    The envelope a compiled objective runs inside (PRD v7 section 43).

    Its capabilities are exactly the plan's: an objective may do what it was
    compiled to do and nothing else. Its roots are the file roots FRIDAY
    already works within (`fsjail.configured_roots`), and its network is the
    public web for reading - both stated here so they can be narrowed, not
    newly granted. Secret aliases are for worker processes; native
    capabilities reach secrets through the broker under policy.
    """
    from friday import capability_runtime, fsjail

    roots = tuple(str(root) for root in fsjail.configured_roots())
    expires = (datetime.now(timezone.utc) + ENVELOPE_LIFETIME).isoformat(
        timespec="seconds")
    return contracts.Envelope(
        run_id=run_id, objective_id=run_id,
        provenance=provenance, attended=attended,
        read_roots=roots, write_roots=roots,
        network=contracts.NetworkScope(mode=contracts.NET_PUBLIC_WEB_READ,
                                       objective_id=run_id, expires_at=expires),
        allowed_capabilities=tuple(sorted(set(capabilities))),
        expires_at=expires,
        # JIT-001: what each capability was when this plan was made.
        capability_hashes={cap: capability_runtime.fingerprint(cap)
                           for cap in sorted(set(capabilities))})


def _admit(envelope: contracts.Envelope, minted: contracts.Envelope) -> None:
    """
    A caller may hand compile a narrower envelope than the one compile mints,
    never a wider one: no capability the plan does not use, no root, host or
    secret compile would not grant, no later expiry and none left open. Its
    network grant is compile's to issue, because a grant id handed in could be
    another objective's live grant.
    """
    run_id = minted.run_id
    if envelope.run_id != run_id:
        raise contracts.ContractError(
            f"the envelope belongs to {envelope.run_id}, not {run_id}")
    if (envelope.provenance, envelope.attended) != (minted.provenance, minted.attended):
        raise contracts.ContractError(
            f"{run_id}: the envelope says {envelope.provenance}/attended="
            f"{envelope.attended}, the caller says {minted.provenance}/"
            f"attended={minted.attended}")
    problems = envelope.wider_than(minted)
    if envelope.network.grant_id:
        problems.append(f"network grant {envelope.network.grant_id} was not issued here")
    if problems:
        raise contracts.ContractError(
            f"{run_id}: the envelope is wider than its plan allows: " + "; ".join(problems))


def _audit_grant(grant_id: str, objective_id: str, decision: str, *,
                 actor: str = "friday", detail: dict | None = None) -> None:
    """NET-001: a network grant's issue and revocation are audited (PRD
    section 44: tokens are objective-bound, auditable and revocable)."""
    from friday import trust

    trust.audit().record(actor=actor, action="network.grant", tier=trust.R1,
                         decision=decision, target=grant_id,
                         objective_id=objective_id, detail=detail or {})


def _issue_grant(store: Store, envelope: contracts.Envelope) -> contracts.Envelope:
    """Back the envelope's network scope with a grant in the store, so any
    process can revoke it and netguard in every process sees that."""
    scope = envelope.network
    if scope.mode == contracts.NET_NONE or scope.grant_id:
        return envelope
    grant_id = f"GRANT-{secrets.token_hex(8)}"
    store.grant_issue(grant_id=grant_id, objective_id=envelope.objective_id,
                      mode=scope.mode, hosts=scope.hosts,
                      expires_at=scope.expires_at, now=now_iso())
    _audit_grant(grant_id, envelope.objective_id, "ISSUED",
                 detail={"mode": scope.mode, "hosts": list(scope.hosts),
                         "expires_at": scope.expires_at})
    return replace(envelope, network=replace(scope, grant_id=grant_id))


def revoke_network_grant(store: Store, grant_id: str, *, reason: str,
                         actor: str = "person") -> bool:
    """Revoke one objective's network grant. Audited; False if it was not live."""
    row = store.grant(grant_id)
    done = store.grant_revoke(grant_id, reason=reason, now=now_iso())
    if done:
        _audit_grant(grant_id, (row or {}).get("objective_id", ""), "REVOKED",
                     actor=actor, detail={"reason": reason})
    return done


def release_network_grant(store: Store, run_id: str, *, reason: str) -> bool:
    """A finished or cancelled objective keeps no network authority: revoke
    the grant behind its envelope's scope. False if it had none live."""
    row = store.objective_run(run_id) or {}
    try:
        grant_id = json.loads(row.get("envelope") or "{}").get(
            "network", {}).get("grant_id", "")
    except ValueError:
        logger.exception("objective %s: its stored envelope cannot be read", run_id)
        return False
    if not grant_id:
        return False
    return revoke_network_grant(store, grant_id, reason=reason, actor="friday")


def pause_all_running(store: Store, *, reason: str) -> list[str]:
    """STOP-001: park every running objective with the reason. PAUSED is the
    explicit-resume wait, so nothing picks them up again on its own."""
    paused = []
    for run in store.objective_runs_in(RUN_RUNNING):
        store.touch_objective_run(run["run_id"], status=RUN_PAUSED,
                                  next_wake=None, blocker=f"stopped: {reason}")
        store.append_objective_event(run["run_id"], EVENT_RUN_PAUSED,
                                     detail={"reason": f"stopped: {reason}"})
        paused.append(run["run_id"])
    return paused


def revoke_all_network_grants(store: Store, *, reason: str,
                              actor: str = "friday") -> int:
    """Revoke every live network grant at once (STOP-001). Audited per grant."""
    revoked = store.grants_revoke_all(reason=reason, now=now_iso())
    for grant_id in revoked:
        _audit_grant(grant_id, "", "REVOKED", actor=actor, detail={"reason": reason})
    return len(revoked)


def compile_objective(store: Store, *, request: str, tasks: list[dict],
                      manifest: list[dict], objective_summary: str,
                      provenance: str, attended: bool,
                      run_id: str | None = None,
                      envelope: contracts.Envelope | None = None,
                      composition_confirmed: bool = False,
                      contract: contracts.ObjectiveContract | None = None) -> dict:
    """
    Turn an LLM-produced plan into a durable, validated task graph.

    `provenance` and `attended` have no default: where a plan came from and
    whether anyone is there to answer are the caller's to say, never assumed
    as the person, attended (Slice 2 review).

    Validation is refusal for plans that cannot become a graph: a missing
    dependency, or a cycle fails the compile and names the offender. A task
    without an explicit capability name defaults to `cap{index}` (plan
    order), which is how a manifest that describes capabilities by position
    stays bindable. An *unknown* capability is not a refusal - the LLM
    may have hallucinated a tool - so that task is persisted as immediately
    FAILED/CAPABILITY_MISSING rather than executed or re-asked, and its
    dependents are skipped at runtime. Task ids are assigned in plan order
    (t1, t2, ...) and the graph's ordering is verified with the shared
    topological sort.
    """
    known = {m["id"] for m in manifest}

    recursive = [str(spec.get("capability") or "") for spec in tasks
                 if str(spec.get("capability") or "")
                 in OBJECTIVE_CONTROL_CAPABILITIES]
    if recursive:
        raise CompileError(
            f"control-plane capability {recursive[0]!r} cannot be a task inside an objective; it governs the parent run")

    compiled: list[CompiledTask] = []
    for index, spec in enumerate(tasks, start=1):
        capability = spec.get("capability") or f"cap{index}"
        deps = tuple(str(d) for d in spec.get("dependencies", ()))
        compiled.append(CompiledTask(
            capability=capability,
            arguments=dict(spec.get("arguments") or {}),
            dependencies=deps,
            max_attempts=int(spec.get("max_attempts", 3)),
            children=tuple(
                CompiledChild(
                    capability=str(child.get("capability") or ""),
                    arguments=dict(child.get("arguments") or {}),
                    skipped_because=str(child.get("skipped_because") or ""),
                    max_attempts=int(child.get("max_attempts", 3)),
                )
                for child in spec.get("children") or ()),
        ))

    task_ids = [f"t{index}" for index in range(1, len(compiled) + 1)]
    by_index = {task_ids[index - 1]: index - 1 for index in range(1, len(compiled) + 1)}

    for index, task in enumerate(compiled, start=1):
        task_id = task_ids[index - 1]
        for dep in task.dependencies:
            if dep not in by_index:
                raise CompileError(
                    f"task {task_id}: depends on {dep!r}, which does not exist")
            if dep == task_id:
                raise CompileError(f"task {task_id}: depends on itself")
            # Only earlier tasks may be dependencies: a plan that references
            # the future is a plan that cannot be ordered.
            if by_index[dep] >= index - 1:
                raise CompileError(
                    f"task {task_id}: dependency {dep!r} is not earlier in "
                    f"the plan")

    nodes = {task_id: list(task.dependencies)
             for task_id, task in zip(task_ids, compiled)}
    try:
        dag.topological(nodes)
    except dag.CycleError as exc:
        raise CompileError(f"cycle in task graph: {exc}") from exc

    _validate_references(compiled, task_ids)

    run_id = run_id or new_run_id()
    known = {m["id"] for m in manifest}
    # PRD v7 INTENT-001: what the person named is used as named, or the plan
    # is not compiled - before anything, a grant included, is written.
    if contract is not None:
        dropped = _dropped_literals(contract, tasks, known)
        if dropped:
            raise LiteralDropped(
                "the plan does not use what the person named: "
                + ", ".join(repr(d) for d in dropped))
        # BCON-001: what the person forbade is not planned, whoever planned.
        forbidden = forbidden_steps(contract, tasks)
        if forbidden:
            raise ForbiddenByContract(
                "the plan does what the person forbade: " + "; ".join(forbidden))
    planned = [cap for task in compiled
               for cap in (task.capability, *(ch.capability for ch in task.children))]
    # PRD v7 CAP-002: the capability set is judged as a whole, here, before
    # anything is persisted - the one door every objective comes through.
    composed = composition_of(planned, provenance=provenance, attended=attended)
    if composed is not None:
        from friday import policy

        if composed.decision == policy.DENY:
            raise CompileError(composed.reason)
        if not composition_confirmed:
            raise CompositionNeedsConfirmation(composed, sorted(set(planned)))
    # PRD v7 SEC-005 / TRUST-002: where this came from, whether anyone is
    # there to answer, and what it may do travel with the objective, so the
    # executor never has to assume the person, attended.
    minted = envelope_for(run_id, [cap for cap in planned if cap in known],
                          provenance=provenance, attended=attended)
    ceiling = int((contract.governance or {}).get("max_side_effects") or 0) \
        if contract is not None else 0
    if ceiling:
        # BCON-001: "at most N changes" is the executor's ceiling to hold.
        minted = replace(minted, max_side_effects=ceiling)
    if envelope is None:
        envelope = minted
    else:
        # The versions are compile's record of what each capability is now,
        # not something a caller grants: they are written onto a supplied
        # envelope, and one that pins a different version is still refused.
        envelope = replace(envelope, capability_hashes={
            **minted.capability_hashes, **envelope.capability_hashes})
        _admit(envelope, minted)
    envelope = _issue_grant(store, envelope)
    # Stored task ids carry the run id so two runs never share a row, and
    # the plan's own references ({{tasks.t1.output}}, dependency names) are
    # rewritten to the stored ids before anything is persisted.
    stored_ids = [f"{run_id}-{task_id}" for task_id in task_ids]
    translate = dict(zip(task_ids, stored_ids))
    stamp = now_iso()
    store.open_objective_run(
        run_id, request=request, objective_summary=objective_summary,
        status=RUN_RUNNING, next_wake=stamp,
        provenance=envelope.provenance, attended=envelope.attended,
        envelope=envelope.to_dict(),
        contract=None if contract is None else contract.to_dict(),
    )
    # PRD FR-001/FR-002: the objective is classified once, at admission,
    # and the class, risk tier, budgets and required capabilities are
    # durable columns - inspectable, resumable, traceable (FR-001
    # acceptance). Deterministic: no model call decides this.
    try:
        from friday import task_class as TC
        from friday.model_gateway import budget_for
        klass = TC.classify(request, acceptance=len(tasks))
        budget = budget_for(klass.task_class)
        store.touch_objective_run(
            run_id, task_class=klass.task_class,
            risk_tier=TC.risk_tier_for(klass.task_class),
            required_capabilities=[t.capability for t in compiled],
            retry_budget=max(int(t.max_attempts) for t in compiled) if compiled else 3,
            cost_budget_tokens=budget.objective_ceiling,
            time_budget_s={"TRIVIAL": 60, "SIMPLE": 300, "STANDARD": 1800,
                           "COMPLEX": 7200, "LONG_RUNNING": 0,
                           "CRITICAL": 3600}[klass.task_class])
        classification = klass.to_dict()
    except Exception as exc:  # noqa: BLE001 - classification must never block admission
        classification = {"error": str(exc)}
    store.append_objective_event(run_id, EVENT_RUN_CREATED,
                                 detail={"task_count": len(compiled),
                                         "classification": classification})
    for task_id, task in zip(stored_ids, compiled):
        dependencies = [translate[dep] for dep in task.dependencies]
        arguments = _retarget(task.arguments, translate)
        if task.children:
            _save_composite(store, run_id=run_id, task_id=task_id, task=task,
                            dependencies=dependencies, arguments=arguments,
                            known=known)
            continue
        if task.capability not in known:
            # A hallucinated tool is a fact about the plan, not a runtime
            # question: record it failed and never dispatch it.
            reason = _why_it_cannot_run(task.capability, arguments)
            store.save_objective_task(
                task_id=task_id, run_id=run_id, capability=task.capability,
                arguments=json_dumps(arguments),
                dependencies=json_dumps(dependencies),
                status=TASK_FAILED, attempts=0,
                failure_kind=FAILURE_CAPABILITY_MISSING,
                evidence=reason,
            )
            store.append_objective_event(
                run_id, EVENT_TASK_FAILED, task_id=task_id,
                detail={"kind": FAILURE_CAPABILITY_MISSING,
                        "reason": reason, "by": "compile"})
            continue
        store.save_objective_task(
            task_id=task_id, run_id=run_id, capability=task.capability,
            arguments=json_dumps(arguments),
            dependencies=json_dumps(dependencies),
            status=TASK_READY if not task.dependencies else TASK_QUEUED,
            attempts=0,
        )
    store.touch_objective_run(run_id)
    return store.objective_run(run_id) or {"run_id": run_id}


def _why_it_cannot_run(capability: str, arguments: dict) -> str:
    """The evidence line for a task that will never be dispatched.

    "unknown capability 'objective.unmapped'" is a true sentence about the
    marker and tells the boss nothing: what they need to hear is which of the
    things they said Friday could not place, and that clause is sitting in
    the arguments. It was being thrown away in favour of the marker's name.
    """
    if capability == UNMAPPED_CAPABILITY:
        clause = str(arguments.get("clause") or "").strip()
        return (f"nothing Friday can do matches {clause!r}" if clause
                else "part of that request matched nothing Friday can do")
    return f"unknown capability {capability!r}"


def _save_composite(store: Store, *, run_id: str, task_id: str,
                    task: CompiledTask, dependencies: list[str],
                    arguments: dict, known: set) -> None:
    """Write a group row plus its leaves.

    The group is written non-terminal and is never dispatched: it settles
    when its children do, in `ContinuousTaskExecutor._settle_composites`.
    A group with dependencies of its own is not modelled, because nothing
    needs it - the children carry the group's dependencies instead, so a
    group is always a view over leaves rather than a step in the graph.
    """
    store.save_objective_task(
        task_id=task_id, run_id=run_id, capability=COMPOSITE,
        arguments=json_dumps(arguments),
        dependencies=json_dumps(dependencies),
        status=TASK_RUNNING, attempts=0)

    for index, child in enumerate(task.children, start=1):
        child_id = f"{task_id}.{index}"
        if child.skipped_because:
            store.save_objective_task(
                task_id=child_id, run_id=run_id,
                capability=child.capability,
                arguments=json_dumps(child.arguments),
                dependencies="[]", parent_id=task_id,
                status=TASK_SKIPPED, attempts=0,
                evidence=child.skipped_because)
            store.append_objective_event(
                run_id, EVENT_TASK_SKIPPED, task_id=child_id,
                detail={"reason": child.skipped_because, "by": "compile"})
            continue

        if child.capability not in known:
            store.save_objective_task(
                task_id=child_id, run_id=run_id,
                capability=child.capability,
                arguments=json_dumps(child.arguments),
                dependencies="[]", parent_id=task_id,
                status=TASK_FAILED, attempts=0,
                failure_kind=FAILURE_CAPABILITY_MISSING,
                evidence=f"unknown capability {child.capability!r}")
            store.append_objective_event(
                run_id, EVENT_TASK_FAILED, task_id=child_id,
                detail={"kind": FAILURE_CAPABILITY_MISSING,
                        "reason": f"unknown capability "
                                  f"{child.capability!r}",
                        "by": "compile"})
            continue

        store.save_objective_task(
            task_id=child_id, run_id=run_id,
            capability=child.capability,
            arguments=json_dumps(child.arguments),
            dependencies=json_dumps(dependencies),
            parent_id=task_id,
            status=TASK_READY if not dependencies else TASK_QUEUED,
            attempts=0)


def _retarget(value, translate: dict[str, str]):
    """Rewrite {{tasks.t1.output}} to name the stored task id."""
    if isinstance(value, str):
        return _TASK_REF.sub(
            lambda m: f"{{{{tasks.{translate.get(m.group(1), m.group(1))!s}."
                      f"{m.group(2)!s}}}}}",
            value)
    if isinstance(value, dict):
        return {key: _retarget(item, translate) for key, item in value.items()}
    if isinstance(value, list):
        return [_retarget(item, translate) for item in value]
    return value


def _validate_references(compiled: list[CompiledTask],
                         task_ids: list[str]) -> None:
    """{{tasks.<id>.<key>}} may only name earlier, existing tasks."""
    for index, task in enumerate(compiled, start=1):
        task_id = task_ids[index - 1]
        for value in _walk(task.arguments):
            for referenced, _key in _TASK_REF.findall(str(value)):
                if referenced not in task_ids:
                    raise CompileError(
                        f"task {task_id}: references unknown task "
                        f"{referenced!r}")
                if task_ids.index(referenced) >= index - 1:
                    raise CompileError(
                        f"task {task_id}: references {referenced!r}, which "
                        f"has not run yet")


def _walk(value):
    """Yield every string leaf of a nested dict/list so refs are findable."""
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)
    elif isinstance(value, str):
        yield value


def json_dumps(value) -> str:
    import json

    return json.dumps(value, default=str)


# ---------------------------------------------------------------------------
# Control plane (Phase 3)
# ---------------------------------------------------------------------------
#
# Pause, resume and cancel are deliberately lease-free. The lease guards who
# may DRIVE a run; stopping one must work even while another process is
# driving it - the MCP server and the agent job are different processes, so
# a module-level registry in either one cannot reach the other. These
# helpers mutate durable rows only; the driver loop, the per-task re-check
# and the watchdog observe the new status at the next task boundary.

def active_run(store: Store, run_id: str = "") -> dict | None:
    """The named run, or the most recent non-terminal one. None if absent."""
    if run_id:
        return store.objective_run(run_id)
    for run in store.objective_runs(limit=100):
        if run["status"] not in RUN_TERMINAL:
            return run
    return None


def pause_run(store: Store, *, run_id: str, reason: str,
              executor_id: str) -> bool:
    """Put a run into a legitimate wait: no wake scheduled, so neither the
    driver loop nor the watchdog touches it until an explicit resume.

    Only meaningful for a run that is not already terminal or paused;
    returns False otherwise.
    """
    run = store.objective_run(run_id)
    if run is None or run["status"] in RUN_TERMINAL:
        return False
    if run["status"] == RUN_PAUSED:
        return False
    store.touch_objective_run(run_id, status=RUN_PAUSED, next_wake=None)
    store.append_objective_event(
        run_id, EVENT_RUN_PAUSED,
        detail={"reason": reason, "by": executor_id})
    return True


def resume_run(store: Store, *, run_id: str, reason: str,
               executor_id: str) -> bool:
    """Wake a paused run: RUNNING again, with an immediate next_wake so the
    driver loop picks it up on its next tick. No-op unless the run is
    actually paused.
    """
    run = store.objective_run(run_id)
    if run is None or run["status"] != RUN_PAUSED:
        return False
    store.touch_objective_run(run_id, status=RUN_RUNNING,
                              next_wake=now_iso())
    store.append_objective_event(
        run_id, EVENT_RUN_RESUMED,
        detail={"reason": reason, "by": executor_id})
    return True


def cancel_run(store: Store, *, run_id: str, reason: str,
               executor_id: str) -> bool:
    """Interrupt every unfinished task and mark the run CANCELLED.

    Mirrors the engine's own cancel: task rows become INTERRUPTED with an
    evidence trail, the run reaches RUN_CANCELLED, and the caller receives
    the finished run dict. No lease is taken - stopping wins over driving.
    """
    run = store.objective_run(run_id)
    if run is None or run["status"] in RUN_TERMINAL:
        return False
    for task in store.objective_tasks(run_id):
        if task["status"] in TASK_TERMINAL:
            continue
        store.update_objective_task(
            task["task_id"], status=TASK_INTERRUPTED,
            finished_at=now_iso(),
            evidence=f"cancelled: {reason}")
        store.append_objective_event(
            run_id, EVENT_TASK_INTERRUPTED, task_id=task["task_id"],
            detail={"reason": reason, "by": executor_id})
    tasks = store.objective_tasks(run_id)
    succeeded = sum(1 for t in tasks if t["status"] == TASK_SUCCEEDED)
    failed = sum(1 for t in tasks if t["status"] == TASK_FAILED)
    interrupted = sum(1 for t in tasks
                      if t["status"] == TASK_INTERRUPTED)
    store.finish_objective_run(
        run_id, status=RUN_CANCELLED,
        summary={
            "tasks": len(tasks),
            "succeeded": succeeded,
            "failed": failed,
            "interrupted": interrupted,
            "reason": reason,
            "by": executor_id,
        })
    release_network_grant(store, run_id, reason=f"objective cancelled: {reason}")
    store.append_objective_event(
        run_id, EVENT_RUN_CANCELLED,
        detail={"reason": reason, "by": executor_id})
    return True
