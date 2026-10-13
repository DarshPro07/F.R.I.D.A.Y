"""
Starting a development run, and promoting what it produced, as
capability-runtime implementations (PRD v7 CTX-003; owner ruling 2026-10-03d).
MCP faces in `friday/tools/development_control.py`.

`development_run` is a selfdev/FILE_WRITE-class action. It asks by default; it
proceeds without another prompt only when the objective holds a valid
APPROVAL-001 lease for exactly this capability, repository and arguments
(`capability_runtime._approval_on_record`: same plan, unexpired, same blast
radius). The only way it reaches a worker is `development.for_request`, so the
context compiler reads the objective's critical facts first and the package
fails loudly if they cannot be kept.

`development_promote` is a separate, higher-authority action (COMMAND_EXECUTION):
it merges a run's sandbox worktree into a live branch. A yes to start a run
never covers it; only a lease that names this exact branch does.

A run that needs commands is refused (`NO_SANDBOX`) until EXEC-SBX-001 exists -
never a fallback to host commands.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from friday import capabilities
from friday import compaction as K
from friday import contracts as c
from friday import development
from friday import model_gateway as mg
from friday import objectives as O
from friday.executors import worktrees as W

#: Why a run that needs command execution does not start. It names the
#: requirement so the refusal can be traced, and says what it will not do.
NO_SANDBOX = (
    "EXEC-SBX-001: a development run that needs command execution is refused - "
    "commands run only behind an OS-enforced sandbox, which does not exist yet, "
    "and a run never falls back to unrestricted host commands")

_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")

#: The objective event that carries a conflict's full integration task (both
#: sides' requirements and diffs); the task's own arguments hold the summary.
EVENT_INTEGRATION_TASK = "development.integration_task"


def _objective_store():
    from friday.toolsets.objectives import store
    return store()


def _scope(run: c.Run, root: str) -> tuple[Path | None, str]:
    """The repository as a real directory, and why not when it is not one this
    run may write to. Under an envelope the repository must lie in a write root:
    a run's sandbox worktree is made inside it."""
    folder = Path(root or "").expanduser()
    if not str(root or "").strip() or not folder.is_dir():
        return None, f"{root or '(none)'} is not a directory"
    folder = folder.resolve()
    envelope = run.envelope
    if envelope is not None and not envelope.writes(str(folder)):
        return None, f"ENVELOPE: {folder} is outside this run's write roots"
    return folder, ""


def _integration_work(task: dict) -> str:
    """The work a conflict leaves, as the request a development run takes."""
    asked = task["requirements"]
    lines = [f"Resolve the merge conflict between {task['branch']} and {task['target']} "
             f"in: {', '.join(task['conflicts'])}.",
             "Keep both requirements.",
             f"Requirements on {task['target']}: {'; '.join(asked['target']) or '(none)'}.",
             f"Requirements on {task['branch']}: {'; '.join(asked['worker']) or '(none)'}.",
             f"Acceptance: {task['acceptance']}."]
    return " ".join(lines)


def _same_integration_task(left: dict, right: dict) -> bool:
    return json.dumps(left, sort_keys=True, separators=(",", ":"), default=str) == \
        json.dumps(right, sort_keys=True, separators=(",", ":"), default=str)


def _existing_integration_objective(store, task: dict) -> str:
    """The LIVE run that already holds this exact integration task, searched
    across the whole objective store (not just the newest runs), else empty.
    A run that has finished (cancelled, failed, partial, completed) no longer
    owns the conflict, so it is skipped and the caller staffs it afresh."""
    for event in store.objective_events_across_runs(EVENT_INTEGRATION_TASK):
        detail = event.get("detail")
        if not (isinstance(detail, dict) and _same_integration_task(detail, task)):
            continue
        owner = store.objective_run(event["run_id"])
        if owner is not None and owner["status"] not in O.RUN_TERMINAL:
            return event["run_id"]
    return ""


def _persist_integration_task(run: c.Run, folder: Path, task: dict) -> str:
    """A conflict's integration task as its own durable objective, so the
    engine owns it and it survives this call. Returns the objective's run id;
    raises `O.CompileError` when it cannot be admitted."""
    store = _objective_store()
    existing = _existing_integration_objective(store, task)
    if existing:
        return existing
    work = _integration_work(task)
    created = O.compile_objective(
        store, request=work, objective_summary=work, manifest=capabilities.as_dicts(),
        tasks=[{"capability": "development_run",
                "arguments": {"work": work, "root": str(folder)}}],
        provenance=run.provenance, attended=run.attended)
    store.append_objective_event(created["run_id"], EVENT_INTEGRATION_TASK, detail=task)
    return created["run_id"]


def development_run(run: c.Run, work: str, root: str, objective_id: str = "",
                    project: str = "", strategy_hint: str = "",
                    budget_tokens: int = 0,
                    needs_commands: bool = False) -> c.ActionResult:
    """Stage one development run for `work` in the repository at `root`, for the
    objective it serves: the context compiler keeps the objective's facts, then
    the run understands the repository and chooses its team. Inside an objective
    the objective is the run's own; `objective_id` names it for a call made
    outside one."""
    started = c.started(run.run_id, "development.run")
    if needs_commands:
        return run.record(started.finish(status=c.NOT_PERMITTED, error=NO_SANDBOX))

    envelope = run.envelope
    if envelope is not None:
        if objective_id and objective_id != envelope.objective_id:
            return run.record(c.failed(
                started, "an objective's run starts work for that objective only"))
        objective_id = envelope.objective_id
    folder, why = _scope(run, root)
    if folder is None:
        return run.record(started.finish(
            status=c.NOT_PERMITTED if why.startswith("ENVELOPE") else c.FAILED,
            error=why))

    store = _objective_store()
    if not objective_id or store.objective_run(objective_id) is None:
        return run.record(c.failed(
            started, f"no objective {objective_id or '(none)'} for this development "
                     "run to serve; its critical facts come from an objective"))
    try:
        staged = development.for_request(store, K.ContextRequest(
            objective_id=objective_id, work=work, root=str(folder), project=project,
            strategy_hint=strategy_hint, budget_tokens=int(budget_tokens)))
    except (c.ContractError, mg.ContextOverBudget) as exc:
        return run.record(c.failed(started, f"{type(exc).__name__}: {exc}"))

    report = staged.report()
    roles = (report["team"] or {}).get("roles", ())
    return run.record(c.succeeded(
        started, output={"objective_id": objective_id, "report": report},
        verification=c.Verification(
            method="context_compiled",
            evidence=(f"{objective_id}: critical facts kept by the context compiler; "
                      f"run staffed for {folder} ({', '.join(roles) or 'no team'}); "
                      "work stays in its own sandbox worktree"))))


def development_promote(run: c.Run, root: str, run_id: str, branch: str,
                        message: str = "") -> c.ActionResult:
    """Merge a development run's sandbox worktree into `branch`, which must be
    named: promotion never picks the live branch for itself. The merge commit
    and the rollback target come back so it can be undone from the record."""
    started = c.started(run.run_id, "development.promote")
    if not (branch or "").strip():
        return run.record(c.failed(
            started, "promotion names the exact branch it lands on"))
    if not _RUN_ID.fullmatch(run_id or ""):
        return run.record(c.failed(started, f"{run_id!r} is not a development run id"))
    folder, why = _scope(run, root)
    if folder is None:
        return run.record(started.finish(
            status=c.NOT_PERMITTED if why.startswith("ENVELOPE") else c.FAILED,
            error=why))

    manager = W.WorktreeManager(folder)
    name = f"ada-{run_id.lower()}"
    try:
        promotion = manager.promote(
            name, target=branch.strip(),
            message=message or f"ADA {run_id}: development run promoted to {branch}")
    except W.WorktreeError as exc:
        return run.record(c.failed(started, f"not promoted: {exc}"))
    if promotion.state != W.PROMOTED:
        failure = c.failed(started, f"not promoted: {promotion.reason}")
        if promotion.conflicts:
            # A conflict is not a dead end: it comes back as the task that
            # resolves it, with both sides' requirements and diffs.
            task = manager.integration_task(promotion)
            output = {"integration_task": task}
            try:
                output["integration_objective"] = _persist_integration_task(
                    run, folder, task)
            except O.CompileError as exc:
                failure = failure.finish(error=(
                    f"{failure.error}; the integration task was not persisted: {exc}"))
            failure = failure.finish(output=output)
        return run.record(failure)
    return run.record(c.succeeded(
        started,
        output={"branch": promotion.target, "merge_commit": promotion.merge_commit,
                "rollback_target": promotion.rollback_target},
        verification=c.Verification(
            method="git_merge",
            evidence=(f"{promotion.branch} merged into {promotion.target} at "
                      f"{promotion.merge_commit[:12]}; rollback target "
                      f"{promotion.rollback_target[:12]}"))))
