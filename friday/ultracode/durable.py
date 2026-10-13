"""UCW-002 on ULTRA-002: a workflow definition run as a DAG by the durable engine.

`runner.start_run` refuses a definition's flat spec because it chains stages
between barriers and sends every stage to a model. This module plans the
definition itself instead: one durable objective task per step, and the
task's `dependencies` are exactly the step's `depends_on`. So the engine, not
this module, decides when a step starts: independent steps have no barrier
between them, and a dependent becomes ready only when every step it depends on
has succeeded. `DefinitionPort` is the capability port that runs one step:

- it reads each upstream result from the ULTRA-002 result cache by the key the
  upstream task returned, and validates every handoff field against the
  producer's outputs schema before anything is dispatched (`HandoffRefused`,
  naming `steps[i].inputs[j].fields[k]`);
- a step whose role resolved UNAVAILABLE (an owner pin to a route that is not
  available, or no route at all) is refused (`StepUnavailable`) and no other
  model is substituted;
- a `deterministic` step goes to its registered executor and never to a model;
  an `agent` step goes to the model its `Resolution` chose;
- a result that violates the step's own outputs schema is refused and never
  cached, so a retry runs the step again.

Every dispatch, the executor's too, goes through the run's `DispatchGate`
(ULTRA-003 budget and governor lease). All refusals are `DefinitionError`s, so
the engine fails the task without a retry and skips its dependents.
`start_definition_run` records every step's resolution (model and reason) on the
run as `EVENT_RESOLUTIONS` before any step runs.
"""
from __future__ import annotations

from typing import Mapping

from friday import objectives
from friday.ultracode.cache import ResultCache, result_key
from friday.ultracode.definition import (Agent, Compiled, DefinitionError, Executor,
                                         Resolution, STATUS_UNAVAILABLE, _mismatch)
from friday.ultracode.runner import DispatchGate

CAPABILITY = "ultracode_definition_step"
MANIFEST = [{"id": CAPABILITY,
             "description": "run one step of an ultracode workflow definition",
             "arguments": {"spec_id": "str", "stage_ids": "list", "inputs": "list",
                           "sources": "list", "from_keys": "bool"}}]
EVENT_RESOLUTIONS = "ultracode.resolutions"

__all__ = ["CAPABILITY", "DefinitionPort", "EVENT_RESOLUTIONS", "HandoffRefused", "MANIFEST",
           "StepUnavailable", "plan_definition_tasks", "start_definition_run"]


class HandoffRefused(DefinitionError):
    """A dependent's handoff did not validate; the step was not dispatched."""


class StepUnavailable(DefinitionError):
    """The step's role has no available model (a pin is never substituted)."""


def plan_definition_tasks(compiled: Compiled) -> list[dict]:
    """The `compile_objective` task list: one task per step, in dependency
    order, depending on exactly the tasks of the step's `depends_on`."""
    task_of = {step_id: f"t{i + 1}" for i, step_id in enumerate(compiled.order)}
    by_id = {step.id: step for step in compiled.definition.steps}
    tasks = []
    for step_id in compiled.order:
        sources = list(by_id[step_id].depends_on)
        tasks.append({
            "capability": CAPABILITY,
            "arguments": {"spec_id": compiled.spec.id, "stage_ids": [step_id],
                          "inputs": [f"{{{{tasks.{task_of[dep]}.key}}}}" for dep in sources],
                          "sources": sources, "from_keys": True},
            "dependencies": [task_of[dep] for dep in sources],
            "surface": [f"ultracode/{compiled.spec.id}/{task_of[step_id]}"],
        })
    return tasks


def _check_resolutions(compiled: Compiled, resolutions: Mapping[str, Resolution]) -> None:
    for step in compiled.definition.steps:
        if step.id not in resolutions:
            raise DefinitionError(f"resolutions.{step.id}",
                                  "every step needs its resolve_roles result before it runs")


def start_definition_run(store, compiled: Compiled, *, resolutions: Mapping[str, Resolution],
                         provenance: str, attended: bool) -> dict:
    """Admit the definition as one durable objective; the engine schedules it.

    Refused, before any run exists, when a step has no resolution. The
    definition's token budget becomes the run's budget, and every step's
    runtime choice and reason is recorded on the run."""
    _check_resolutions(compiled, resolutions)
    run = objectives.compile_objective(
        store, request=f"ultracode {compiled.spec.id}", tasks=plan_definition_tasks(compiled),
        manifest=MANIFEST,
        objective_summary=f"ultracode workflow definition {compiled.spec.id}",
        provenance=provenance, attended=attended)
    run_id = run["run_id"]
    store.touch_objective_run(run_id, cost_budget_tokens=compiled.spec.budget_tokens)
    store.append_objective_event(
        run_id, EVENT_RESOLUTIONS,
        detail={"resolutions": [resolutions[step_id].to_dict() for step_id in compiled.order]})
    return store.objective_run(run_id) or run


class DefinitionPort:
    """The engine's capability port for `CAPABILITY`: runs one step."""

    def __init__(self, compiled: Compiled, *, agent: Agent, executors: Mapping[str, Executor],
                 resolutions: Mapping[str, Resolution], cache: ResultCache,
                 gate: DispatchGate) -> None:
        definition = compiled.definition
        for i, step in enumerate(definition.steps):
            if step.kind == "deterministic" and step.executor not in executors:
                raise DefinitionError(f"steps[{i}].executor",
                                      f"no executor registered for {step.executor!r}")
        _check_resolutions(compiled, resolutions)
        self._compiled = compiled
        self._steps = {step.id: step for step in definition.steps}
        self._stages = {stage.id: stage for stage in compiled.spec.stages}
        self._agent = agent
        self._executors = dict(executors)
        self._resolutions = dict(resolutions)
        self._cache = cache
        self._gate = gate

    async def __call__(self, capability: str, arguments: dict, run=None) -> dict:
        if capability != CAPABILITY:
            raise ValueError(f"not an ultracode definition capability: {capability!r}")
        spec_id = self._compiled.spec.id
        if arguments["spec_id"] != spec_id:
            raise ValueError(f"task is for {arguments['spec_id']!r}, not {spec_id!r}")
        (step_id,) = arguments["stage_ids"]
        step = self._steps[step_id]
        index = self._compiled.definition.index(step_id)
        refs = [str(ref) for ref in arguments["inputs"]]
        upstream = dict(zip(arguments["sources"], refs))
        chosen = self._resolutions[step_id]
        if chosen.status == STATUS_UNAVAILABLE:
            raise StepUnavailable(f"resolutions.{step_id}", f"{STATUS_UNAVAILABLE}: {chosen.reason}")
        handed = self._handoff(step, index, upstream)
        model = chosen.model if step.kind == "agent" else f"deterministic:{step.executor}"
        key = result_key(spec_id=spec_id, stage_id=step_id, input_refs=refs, model=model,
                         schema=step.outputs)
        cached = self._cache.get(key)
        if cached is not None:
            return {"key": key, "step": step_id, "hit": True}
        lease = self._gate.admit(run, self._stages[step_id], arguments)
        try:
            if step.kind == "deterministic":
                result = await self._executors[step.executor](step, handed)
            else:
                result = await self._agent(step, handed, chosen.model)
        finally:
            self._gate.release(lease)
        self._gate.settle(run, self._stages[step_id], arguments)
        if why := _mismatch(result, step.outputs):
            raise DefinitionError(f"steps[{index}].outputs",
                                  f"result does not match its outputs schema: {why}")
        self._cache.put(key, result)
        return {"key": key, "step": step_id, "hit": False}

    def _handoff(self, step, index: int, upstream: Mapping[str, str]) -> dict[str, dict]:
        """The typed fields this step takes, each validated against its producer's schema."""
        handed: dict[str, dict] = {}
        for j, handoff in enumerate(step.inputs):
            produced = self._cache.get(upstream[handoff.source])
            if produced is None:
                raise LookupError(f"{handoff.source!r}'s result is missing from the cache")
            schema = self._steps[handoff.source].outputs["properties"]
            for k, name in enumerate(handoff.fields):
                at = f"steps[{index}].inputs[{j}].fields[{k}]"
                if not isinstance(produced, dict) or name not in produced:
                    raise HandoffRefused(at, f"{handoff.source!r} did not produce {name!r}")
                if why := _mismatch(produced[name], schema[name]):
                    raise HandoffRefused(at, f"{handoff.source!r}.{name} does not match its "
                                             f"schema: {why}")
                handed.setdefault(handoff.source, {})[name] = produced[name]
        return handed
