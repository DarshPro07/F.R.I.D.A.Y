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

import re
from dataclasses import dataclass, field

from friday import dag
from friday.contracts import new_run_id, now_iso
from friday.store import Store


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

TASK_STATUSES = (TASK_QUEUED, TASK_READY, TASK_RUNNING, TASK_WAITING,
                 TASK_SUCCEEDED, TASK_FAILED, TASK_SKIPPED, TASK_INTERRUPTED)
TASK_TERMINAL = (TASK_SUCCEEDED, TASK_FAILED, TASK_SKIPPED)


class TaskStatus:
    QUEUED = TASK_QUEUED
    READY = TASK_READY
    RUNNING = TASK_RUNNING
    WAITING = TASK_WAITING
    SUCCEEDED = TASK_SUCCEEDED
    FAILED = TASK_FAILED
    SKIPPED = TASK_SKIPPED
    INTERRUPTED = TASK_INTERRUPTED


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

FAILURE_KINDS = (FAILURE_TRANSIENT, FAILURE_PROVIDER_DOWN, FAILURE_STRUCTURAL,
                 FAILURE_CAPABILITY_MISSING, FAILURE_POLICY_BLOCK,
                 FAILURE_INVALID_ARGUMENT, FAILURE_NOT_CONFIGURED,
                 FAILURE_USER_REQUIRED, FAILURE_CONNECTIVITY)

#: The only kinds that may be retried, and only a bounded number of times.
RETRYABLE_KINDS = (FAILURE_TRANSIENT, FAILURE_PROVIDER_DOWN, FAILURE_CONNECTIVITY)


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


#: Events the engine appends to the continuation trace.
EVENT_RUN_CREATED = "run.created"
EVENT_RUN_STARTED = "run.started"
EVENT_TASK_STARTED = "task.started"
EVENT_TASK_SUCCEEDED = "task.succeeded"
EVENT_TASK_FAILED = "task.failed"
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

UNMAPPED_CAPABILITY = 'objective.unmapped'

#: A task id may only be referenced by dependencies after it is defined.
_TASK_REF = re.compile(r"\{\{tasks\.([A-Za-z0-9_-]+)\.([A-Za-z0-9_.-]+)\}\}")


class CompileError(ValueError):
    """The plan cannot become a graph. The message says exactly why."""


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


def compile_objective(store: Store, *, request: str, tasks: list[dict],
                      manifest: list[dict], objective_summary: str,
                      run_id: str | None = None) -> dict:
    """
    Turn an LLM-produced plan into a durable, validated task graph.

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
    # Stored task ids carry the run id so two runs never share a row, and
    # the plan's own references ({{tasks.t1.output}}, dependency names) are
    # rewritten to the stored ids before anything is persisted.
    stored_ids = [f"{run_id}-{task_id}" for task_id in task_ids]
    translate = dict(zip(task_ids, stored_ids))
    stamp = now_iso()
    store.open_objective_run(
        run_id, request=request, objective_summary=objective_summary,
        status=RUN_RUNNING, next_wake=stamp,
    )
    store.append_objective_event(run_id, EVENT_RUN_CREATED,
                                 detail={"task_count": len(compiled)})
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
    store.append_objective_event(
        run_id, EVENT_RUN_CANCELLED,
        detail={"reason": reason, "by": executor_id})
    return True
