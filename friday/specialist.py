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

The model route is an input (`provider/model/version/billing_class`). Which
route to pick is the routing policy's job, not this module's.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from friday import executor_router, roles
from friday.agent_ledger import AgentLedger
from friday.executors.claude_code import TaskBundle

COMPOSED = "composed"


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
