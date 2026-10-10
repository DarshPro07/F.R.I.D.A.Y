"""
A temporary specialist, composed per task (FAB-005).

    specialist = role + model route + executor + task skills
                 + allowed tools + workspace policy

The role's identity and instructions come from FRIDAY's registry
(`friday.roles.BY_ID`), never from a provider conversation, so the same role
can run on a different executor with the same skills and limits and its
result is still the role's. The composition is written to the agent ledger
(`friday.agent_ledger`) at claim time, which is what attributes the run to
the role.

`dispatch` is the run itself: it claims the run, hands the bundle to the
executor the specialist was composed for, and writes what came back onto the
same ledger run, so the result is the role's whichever executor produced it.

The model route is an input (`provider/model/version/billing_class`). Which
route to pick is the routing policy's job, not this module's.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from friday import contracts as c
from friday import executor_router, roles
from friday.agent_ledger import CANCELLED, FAILED, SUCCEEDED, TERMINAL, AgentLedger
from friday.executors.claude_code import TaskBundle

COMPOSED = "composed"
RESULT = "result"

#: How an executor's result status ends the role's run. PARTIAL is absent on
#: purpose: that run is not over and the ledger has no final status for it, so
#: it stays open (`RunManager.close` makes the same choice).
_ENDS = {c.SUCCEEDED: SUCCEEDED, c.FAILED: FAILED, c.CANCELLED: CANCELLED}


class SpecialistError(ValueError):
    """The composition names something FRIDAY does not have or cannot drive."""


@dataclass(frozen=True)
class WorkspacePolicy:
    #: Paths the specialist may touch; empty means the executor's own default.
    allowed_paths: tuple[str, ...] = ()
    #: A git worktree, not the live checkout.
    isolate: bool = True


@dataclass(frozen=True)
class Specialist:
    role_id: str
    executor_id: str
    model_route: str
    skills: tuple[str, ...]
    allowed_tools: tuple[str, ...]
    workspace: WorkspacePolicy

    def bundle(self, goal: str, workspace: str, **fields) -> TaskBundle:
        """The work packet for this specialist; the role's text is the registry's."""
        role = roles.BY_ID[self.role_id]
        context = [role.instructions()]
        if self.skills:
            context.append("Skills for this task: " + ", ".join(self.skills))
        constraints = []
        if self.allowed_tools:
            constraints.append("Allowed tools: " + ", ".join(self.allowed_tools))
        return TaskBundle(
            goal=goal, workspace=workspace, role=self.role_id,
            context=tuple(context) + tuple(fields.pop("context", ())),
            constraints=tuple(constraints) + tuple(fields.pop("constraints", ())),
            allowed_paths=self.workspace.allowed_paths,
            isolate=self.workspace.isolate, **fields)


def compose(role_id: str, *, executor_id: str, model_route: str,
            skills: tuple[str, ...] = (), allowed_tools: tuple[str, ...] = (),
            workspace_policy: WorkspacePolicy | None = None) -> Specialist:
    if role_id not in roles.BY_ID:
        raise SpecialistError(f"unknown role {role_id!r}; roles: {sorted(roles.BY_ID)}")
    executor = executor_router.BY_ID.get(executor_id)
    if executor is None:
        raise SpecialistError(f"unknown executor {executor_id!r}")
    if not executor.buildable:
        raise SpecialistError(
            f"executor {executor_id!r} is declared but Friday has no builder for it")
    return Specialist(role_id=role_id, executor_id=executor_id,
                      model_route=model_route, skills=tuple(skills),
                      allowed_tools=tuple(allowed_tools),
                      workspace=workspace_policy or WorkspacePolicy())


def record(ledger: AgentLedger, specialist: Specialist, *, run_id: str, task: str,
           workspace: str, parent_run_id: str = "") -> dict:
    """Claim the run for the role and keep the composition as ledger evidence."""
    row = ledger.start(run_id, task=task, workspace=workspace,
                       executor_id=specialist.executor_id,
                       role_id=specialist.role_id,
                       model_route=specialist.model_route,
                       parent_run_id=parent_run_id)
    ledger.event(run_id, COMPOSED, json.dumps({
        "skills": list(specialist.skills),
        "allowed_tools": list(specialist.allowed_tools),
        "allowed_paths": list(specialist.workspace.allowed_paths),
        "isolate": specialist.workspace.isolate,
    }), [f"agent_runs:{run_id}"])
    return row


async def dispatch(ledger: AgentLedger, specialist: Specialist, goal: str,
                   workspace: str, *, parent_run_id: str = "",
                   **fields) -> c.ActionResult:
    """
    Run the specialist on its executor and attach the result to its ledger run.

    The executor is built from `specialist.executor_id` here, never passed in,
    so the executor the ledger names is the one that ran. `fields` are
    `TaskBundle` fields (`run_id`, `acceptance`, ...). Nothing is claimed for a
    run that cannot start.
    """
    try:
        executor = executor_router.build(specialist.executor_id, ledger.store)
    except (LookupError, NotImplementedError, FileNotFoundError) as exc:
        raise SpecialistError(str(exc)) from exc
    bundle = specialist.bundle(goal, workspace, **fields)
    run_id = bundle.run_id
    record(ledger, specialist, run_id=run_id, task=goal, workspace=workspace,
           parent_run_id=parent_run_id)
    try:
        result = await executor.execute(bundle)
    except Exception as exc:
        ledger.finish(run_id, FAILED, [f"agent_runs:{run_id}"],
                      f"executor raised {type(exc).__name__}")
        raise
    _attach(ledger, specialist, run_id, result)
    return result


def _attach(ledger: AgentLedger, specialist: Specialist, run_id: str,
            result: c.ActionResult) -> None:
    verification = result.verification
    refs = [f"agent_runs:{run_id}"]
    ledger.event(run_id, RESULT, json.dumps({
        "executor": specialist.executor_id,
        "tool_id": result.tool_id,
        "status": result.status,
        "error": (result.error or "")[:400],
        "verification": None if verification is None else {
            "method": verification.method,
            "evidence": verification.evidence[:1000]},
    }), refs)
    if result.status == c.PARTIAL:
        return
    # An executor that writes through the ledger itself (claude_code) has
    # already closed the run; closing it again would add a second "finished".
    if ledger.run(run_id)["status"] not in TERMINAL:
        ledger.finish(run_id, _ENDS.get(result.status, FAILED), refs,
                      (result.error or "")[:400])
