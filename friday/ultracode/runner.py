"""ULTRA-002: an ultracode run is durable objective tasks and nothing else.

`plan_tasks` turns a spec and its items into the task list `compile_objective`
takes; the objective engine alone decides when each task starts, retries it
and resumes it. This module owns no scheduling loop: `StagePort` is the
capability port the engine calls, and it only runs one task's stages in order.

Shape of the plan. A barrier stage waits for every item, so it opens a new
segment. Everything else is a pipeline: the first segment is one task per
item, so an item moves from stage to stage as its own input arrives and never
waits for a slower sibling. Each task declares a surface of its own, which is
how the engine knows siblings may run together. A segment after a barrier is
one task over all the earlier tasks' results (assumed: fan-out happens once,
at the first segment).

Each stage's result is cached by `cache.result_key` (stage, input refs, model,
schema). A task the engine runs again after a stop calls only the stages that
never finished.
"""
from __future__ import annotations

from typing import Awaitable, Callable

from friday import objectives
from friday.ultracode.cache import ResultCache, result_key
from friday.ultracode.spec import Spec, Stage

CAPABILITY = "ultracode_segment"
MANIFEST = [{"id": CAPABILITY,
             "description": "run one segment of an ultracode workflow, stage by stage",
             "arguments": {"spec_id": "str", "stage_ids": "list", "inputs": "list",
                           "from_keys": "bool"}}]

#: agent(stage, inputs, model) -> the stage's result. Inputs are the item
#: refs for the first segment's first stage, otherwise earlier results.
Agent = Callable[[Stage, list, str], Awaitable[dict]]
ModelFor = Callable[[Stage], str]

__all__ = ["CAPABILITY", "MANIFEST", "ResultCache", "StagePort", "plan_tasks", "start_run"]


def _segments(spec: Spec) -> list[list[Stage]]:
    """Stages in order; a barrier stage starts a new segment."""
    segments: list[list[Stage]] = []
    for stage in spec.stages:
        if stage.barrier and segments:
            segments.append([stage])
        elif segments:
            segments[-1].append(stage)
        else:
            segments.append([stage])
    return segments


def plan_tasks(spec: Spec, items: list[str]) -> list[dict]:
    """The `compile_objective` task list for this spec over these items."""
    refs = [str(item) for item in items]
    if not refs:
        raise ValueError("an ultracode run needs at least one item")
    tasks: list[dict] = []
    previous: list[str] = []
    for index, segment in enumerate(_segments(spec)):
        stage_ids = [stage.id for stage in segment]
        if index == 0:
            groups = [[ref] for ref in refs]
        else:
            groups = [[f"{{{{tasks.{task_id}.key}}}}" for task_id in previous]]
        made: list[str] = []
        for group in groups:
            task_id = f"t{len(tasks) + 1}"
            tasks.append({
                "capability": CAPABILITY,
                "arguments": {"spec_id": spec.id, "stage_ids": stage_ids, "inputs": group,
                              "from_keys": index > 0},
                "dependencies": [] if index == 0 else list(previous),
                "surface": [f"ultracode/{spec.id}/{task_id}"],
            })
            made.append(task_id)
        previous = made
    return tasks


def start_run(store, spec: Spec, items: list[str], *, provenance: str,
              attended: bool, manifest: list[dict] | None = None) -> dict:
    """Admit the run as one durable objective; the engine takes it from here."""
    return objectives.compile_objective(
        store, request=f"ultracode {spec.id}", tasks=plan_tasks(spec, items),
        manifest=MANIFEST if manifest is None else manifest,
        objective_summary=f"ultracode workflow {spec.id} over {len(items)} item(s)",
        provenance=provenance, attended=attended)


class StagePort:
    """The engine's capability port for `CAPABILITY`: one task's stages in order."""

    def __init__(self, spec: Spec, agent: Agent, cache: ResultCache,
                 model_for: ModelFor) -> None:
        self._stages = {stage.id: stage for stage in spec.stages}
        self._spec_id = spec.id
        self._agent = agent
        self._cache = cache
        self._model_for = model_for

    async def __call__(self, capability: str, arguments: dict) -> dict:
        if capability != CAPABILITY:
            raise ValueError(f"not an ultracode capability: {capability!r}")
        if arguments["spec_id"] != self._spec_id:
            raise ValueError(
                f"task is for spec {arguments['spec_id']!r}, not {self._spec_id!r}")
        refs = [str(ref) for ref in arguments["inputs"]]
        # Upstream segments hand over cache keys; the first segment, item refs.
        values: list = [self._upstream(ref) for ref in refs] if arguments["from_keys"]             else list(refs)
        key, hits = "", 0
        stage_ids = arguments["stage_ids"]
        for stage_id in stage_ids:
            stage = self._stages[stage_id]
            model = self._model_for(stage)
            key = result_key(spec_id=self._spec_id, stage_id=stage.id,
                             input_refs=refs, model=model, schema=stage.schema)
            cached = self._cache.get(key)
            if cached is not None:
                hits += 1
                result = cached
            else:
                result = await self._agent(stage, values, model)
                self._cache.put(key, result)
            refs, values = [key], [result]
        return {"key": key, "stages": len(stage_ids), "hits": hits}

    def _upstream(self, key: str) -> dict:
        found = self._cache.get(key)
        if found is None:
            raise LookupError(f"an upstream result is missing from the cache: {key[:12]}")
        return found
