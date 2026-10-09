"""The task fence for an EXPERIMENTAL_LOCAL_MODEL route (PRD v7 section 112, LAB-001).

A MiniMind-class model is far too small to hold a conversation or judge an
action, so it is admitted for four narrow jobs and nothing else: classifying a
task's category, choosing which team to activate, turning an instruction into a
small structured schema, and proposing an agent-state transition. Anything else
(development, architecture, research, the main conversational turn, or the
approval of a sensitive action) is refused before the model is called.

The model is resident only inside the `local_experts.ExpertPool`, so the
2 GB artifact cap and the one-general-model rule of `local_model_policy` apply
unchanged. Its output is a proposal: it is checked against a small schema, and
`OutputRejected` tells the caller to use the deterministic route. A model output
is never an approval; the policy engine and `confirmation` still decide.

This module is the fence only. Gateway wiring and the local server come later.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable

from friday import data_class as DC
from friday import local_experts as L
from friday import local_model_policy as LMP

ROUTE = "EXPERIMENTAL_LOCAL_MODEL"
EXPERT_NAME = "minimind-3-64m"

TASK_CATEGORY = "task_category"
TEAM_ACTIVATION = "team_activation"
INSTRUCTION_TO_SCHEMA = "instruction_to_schema"
AGENT_STATE_TRANSITION = "agent_state_transition"

NARROW_TASKS = (TASK_CATEGORY, TEAM_ACTIVATION, INSTRUCTION_TO_SCHEMA, AGENT_STATE_TRANSITION)

#: Data that may never reach a non-local backend (`data_class._REFUSE_OFF_LOCAL`).
_LOCAL_ONLY_CLASSES = (DC.SECRET, DC.LOCAL_ONLY)


class OutputRejected(ValueError):
    """The model's output is not valid for the schema; fall back to the deterministic route."""


@dataclass(frozen=True)
class Verdict:
    admitted: bool
    reason: str
    task: str = ""


class TaskRefused(Exception):
    """`admit` refused the task; `verdict` says why. The model was not called."""

    def __init__(self, verdict: Verdict) -> None:
        super().__init__(verdict.reason)
        self.verdict = verdict


def admit(narrow_task: str, data_class: str, *, local: bool = True) -> Verdict:
    """Whether `narrow_task` over `data_class` data may go to the experimental
    route. `local` says whether the backend is on this machine. Never raises."""
    task = narrow_task if isinstance(narrow_task, str) else ""
    if task not in NARROW_TASKS:
        return Verdict(False, f"task {task!r} is not one of the narrow tasks "
                              f"{', '.join(NARROW_TASKS)}; it is refused before the model is called.", task)
    if data_class not in DC.CLASSES:
        return Verdict(False, f"data class {data_class!r} is unknown; refused for task {task!r}.", task)
    if not local and data_class in _LOCAL_ONLY_CLASSES:
        return Verdict(False, f"{data_class} data may only go to a local route; refused for task {task!r}.", task)
    return Verdict(True, f"task {task!r} is a narrow task.", task)


def register_minimind(pool: L.ExpertPool, load: Callable[[], object],
                      unload: Callable[[], None], size_gb: float) -> LMP.Verdict:
    """Onboard the model into `pool` as a general expert. An artifact over the
    policy's cap raises `local_experts.OnboardingRefused`."""
    return pool.register(EXPERT_NAME, size_gb, load, unload, kind=LMP.GENERAL)


_TYPES = {"object": dict, "array": list, "string": str, "boolean": bool,
          "integer": int, "number": (int, float)}


def _check(value: object, spec: dict, path: str) -> None:
    kind = spec.get("type")
    if kind:
        ok = isinstance(value, _TYPES[kind]) and not (isinstance(value, bool) and kind in ("integer", "number"))
        if not ok:
            raise OutputRejected(f"{path}: expected {kind}")
    if "enum" in spec and value not in spec["enum"]:
        raise OutputRejected(f"{path}: not one of {spec['enum']}")
    if isinstance(value, dict):
        for key in spec.get("required", ()):
            if key not in value:
                raise OutputRejected(f"{path}.{key}: required")
        for key, sub in spec.get("properties", {}).items():
            if key in value:
                _check(value[key], sub, f"{path}.{key}")


def validate_output(text: str, schema: dict) -> object:
    """Parse `text` as JSON and check it against `schema` (type, enum, required,
    properties). Returns the parsed value or raises `OutputRejected`."""
    try:
        value = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise OutputRejected(f"output is not JSON: {exc}") from exc
    _check(value, schema, "$")
    return value


def run(narrow_task: str, data_class: str, call: Callable[[str], str], schema: dict,
        *, local: bool = True) -> object:
    """Admit the task, call the model, validate its output. A refused task
    raises `TaskRefused` without calling `call`; bad output raises
    `OutputRejected`. A failure of `call` itself propagates."""
    verdict = admit(narrow_task, data_class, local=local)
    if not verdict.admitted:
        raise TaskRefused(verdict)
    return validate_output(call(narrow_task), schema)
