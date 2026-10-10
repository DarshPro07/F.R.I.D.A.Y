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

ULTRA-003: `start_run` records the spec's token budget on the run, and a
`DispatchGate` held by the port holds every agent dispatch to it. Before a
dispatch the gate asks `objective_budget.check` against recorded spend, then
asks the governor for a worker lease. A spent budget pauses the run, logs the
stages that will not run, and refuses the dispatch; the gate checks again
after each call returns, so a budget crossed by that call is logged before the
engine's own boundary check can pause the run with nothing listed. A governor
that will not admit refuses the dispatch too, and the engine parks the task
without spending an attempt. The port cannot be built without a gate. Every
cap that cuts a list logs what it cut.
"""
from __future__ import annotations

import logging
from typing import Awaitable, Callable

from friday import governor as governor_module
from friday import objective_budget as budget_module
from friday import objectives
from friday.ultracode.cache import ResultCache, result_key
from friday.ultracode.spec import Spec, Stage

logger = logging.getLogger(__name__)

EVENT_ITEMS_DROPPED = "ultracode.items_dropped"
EVENT_STAGES_DROPPED = "ultracode.stages_dropped"
#: In the blocker of a run this module paused: the run has its drop list.
DROPPED_MARK = "stage(s) dropped"

CAPABILITY = "ultracode_segment"
MANIFEST = [{"id": CAPABILITY,
             "description": "run one segment of an ultracode workflow, stage by stage",
             "arguments": {"spec_id": "str", "stage_ids": "list", "inputs": "list",
                           "from_keys": "bool"}}]

#: agent(stage, inputs, model) -> the stage's result. Inputs are the item
#: refs for the first segment's first stage, otherwise earlier results.
Agent = Callable[[Stage, list, str], Awaitable[dict]]
ModelFor = Callable[[Stage], str]

__all__ = ["BudgetStop", "CAPABILITY", "DROPPED_MARK", "DispatchGate",
           "EVENT_ITEMS_DROPPED", "NotDurableRunnerSafe",
           "EVENT_STAGES_DROPPED", "MANIFEST", "ResultCache", "StagePort", "cap_items",
           "plan_tasks", "start_run"]


class NotDurableRunnerSafe(ValueError):
    """`start_run` was given a spec whose `durable_runner_safe` is False.

    UCW-002: a definition's flat spec has lost depends_on, inputs and executors,
    so this runner would chain independent steps in one pipeline and send a
    deterministic step to a model agent. Raised before any run exists."""

    def __init__(self, spec_id: str) -> None:
        self.spec_id = spec_id
        super().__init__(
            f"{spec_id} is not runnable on the durable runner: its spec is flattened, so "
            "depends_on is lost, independent steps would be chained in one pipeline and a "
            "deterministic step would be sent to a model agent")


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


def cap_items(spec: Spec, items: list[str]) -> tuple[list[str], list[str]]:
    """Split the items at the first stage's cap: (kept, dropped).

    Assumed: a stage's `cap` is the most items it takes in, and fan-out happens
    once, so the first stage's cap bounds the run's items."""
    refs = [str(item) for item in items]
    cap = spec.stages[0].cap
    return refs[:cap], refs[cap:]


def start_run(store, spec: Spec, items: list[str], *, provenance: str,
              attended: bool, manifest: list[dict] | None = None) -> dict:
    """Admit the run as one durable objective; the engine takes it from here.

    The spec's token budget becomes the run's budget here, and items beyond
    the first stage's cap are logged on the run, not run. A spec that says
    `durable_runner_safe = False` (a compiled definition) is refused first."""
    if not getattr(spec, "durable_runner_safe", True):
        raise NotDurableRunnerSafe(spec.id)
    kept, dropped = cap_items(spec, items)
    run = objectives.compile_objective(
        store, request=f"ultracode {spec.id}", tasks=plan_tasks(spec, kept),
        manifest=MANIFEST if manifest is None else manifest,
        objective_summary=f"ultracode workflow {spec.id} over {len(kept)} item(s)",
        provenance=provenance, attended=attended)
    run_id = run["run_id"]
    store.touch_objective_run(run_id, cost_budget_tokens=spec.budget_tokens)
    if dropped:
        store.append_objective_event(
            run_id, EVENT_ITEMS_DROPPED,
            detail={"stage": spec.stages[0].id, "cap": spec.stages[0].cap,
                    "offered": len(kept) + len(dropped), "dropped": dropped})
    return store.objective_run(run_id) or run


class BudgetStop(governor_module.Refused):
    """The run's budget is spent. A `Refused`, so the engine parks the task
    without spending an attempt; the run itself is already paused."""

    def __init__(self, reason: str) -> None:
        super().__init__(governor_module.Decision(
            governor_module.SHED, governor_module.WORKER, reason, pressure=""))


class DispatchGate:
    """Every agent dispatch passes the run budget, then the governor."""

    def __init__(self, store, *, governor=None, telemetry=None, work_log=None) -> None:
        self._store = store
        self._governor_source = governor
        self._telemetry = telemetry
        self._work_log = work_log

    def _governor(self):
        if self._governor_source is not None:
            return self._governor_source()
        return governor_module.governor()

    @staticmethod
    def _run_id(run) -> str:
        if run is None:
            raise ValueError("a gated ultracode port needs the task's run to find its budget")
        return run.envelope.objective_id

    def admit(self, run, stage: Stage, arguments: dict) -> str:
        """The governor lease for this dispatch. Raises `BudgetStop` when the
        recorded spend is at the budget, `governor.Refused` when the governor
        will not admit another worker now, `ValueError` without the task's run."""
        run_id = self._run_id(run)
        telemetry, work_log = self._sources()
        verdict = budget_module.check(self._store, run_id, next_capability=CAPABILITY,
                                      telemetry=telemetry, work_log=work_log)
        if not verdict.allowed:
            self._stop(run_id, stage, arguments, verdict)
        decision = self._governor().admit(
            governor_module.WORKER, label=f"ultracode/{arguments['spec_id']}/{stage.id}",
            objective_id=run_id, spawn_reason=governor_module.PARALLEL)
        if not decision.admitted:
            raise governor_module.Refused(decision)
        return decision.lease

    def release(self, lease: str) -> None:
        self._governor().release(lease)

    def settle(self, run, stage: Stage, arguments: dict) -> None:
        """After a dispatch returned: check the recorded spend again. A call that
        crossed the budget pauses the run and logs every stage still to run, here,
        because the engine's own check at the next task boundary lists nothing.
        The finished result is kept; the next dispatch is refused by `admit`."""
        run_id = self._run_id(run)
        telemetry, work_log = self._sources()
        verdict = budget_module.check(self._store, run_id, next_capability=CAPABILITY,
                                      telemetry=telemetry, work_log=work_log)
        if verdict.allowed or self._already_stopped(run_id):
            return
        dropped = self._dropped(run_id, stage, arguments, finished=True)
        if dropped:
            self._record_stop(run_id, verdict, dropped)

    def _sources(self):
        """The gateway ledger and the Hermes work log, opened as the engine
        opens them. Both are optional; one that cannot be opened counts as no
        spend from that source, so the failure is logged as a warning."""
        telemetry, work_log = self._telemetry, self._work_log
        if telemetry is None:
            try:
                from friday.model_gateway import GatewayTelemetry
                telemetry = GatewayTelemetry()
            except Exception:  # noqa: BLE001 - optional source, as in the engine
                logger.warning("ultracode budget: gateway telemetry unavailable, "
                               "its tokens count as 0", exc_info=True)
        if work_log is None:
            try:
                from friday.hermes_bridge import WorkRunLog
                work_log = WorkRunLog()
            except Exception:  # noqa: BLE001 - optional source, as in the engine
                logger.warning("ultracode budget: hermes work log unavailable, "
                               "its usage counts as 0", exc_info=True)
        return telemetry, work_log

    def _already_stopped(self, run_id: str) -> bool:
        """True when this gate has paused the run already and listed its drops."""
        row = self._store.objective_run(run_id) or {}
        return (row.get("status") == objectives.RunStatus.PAUSED
                and DROPPED_MARK in (row.get("blocker") or ""))

    def _stop(self, run_id: str, stage: Stage, arguments: dict, verdict) -> None:
        # Several tasks can reach a spent budget together: the first one lists
        # the drops, the rest only refuse their own dispatch.
        if not self._already_stopped(run_id):
            self._record_stop(run_id, verdict,
                              self._dropped(run_id, stage, arguments, finished=False))
        raise BudgetStop(f"budget exhausted ({verdict.dimension}): {verdict.reason}")

    def _record_stop(self, run_id: str, verdict, dropped: list[dict]) -> None:
        names = ", ".join(f"{d['task']}/{d['stage']}" for d in dropped)
        reason = (f"budget exhausted ({verdict.dimension}): {verdict.reason}; "
                  f"{len(dropped)} {DROPPED_MARK}: {names}")
        self._store.touch_objective_run(
            run_id, status=objectives.RunStatus.PAUSED, next_wake=None, blocker=reason)
        self._store.append_objective_event(
            run_id, budget_module.EVENT_BUDGET_EXHAUSTED,
            detail={"dimension": verdict.dimension, "reason": verdict.reason,
                    "limit": verdict.limit, "spend": vars(verdict.spend)})
        self._store.append_objective_event(
            run_id, EVENT_STAGES_DROPPED,
            detail={"dimension": verdict.dimension, "reason": verdict.reason,
                    "dropped": dropped})

    def _dropped(self, run_id: str, stage: Stage, arguments: dict, *,
                 finished: bool) -> list[dict]:
        """The stages that will not run: every stage of each task that has not
        finished, and for this task those after `stage` once it has finished
        (`finished`), or from `stage` on when the dispatch was refused."""
        tasks = self._store.objective_tasks(run_id)
        stage_ids = list(arguments["stage_ids"])
        current = next((t for t in tasks
                        if t["status"] == objectives.TaskStatus.RUNNING
                        and t["arguments"].get("stage_ids") == stage_ids
                        and (arguments["from_keys"]
                             or t["arguments"].get("inputs") == arguments["inputs"])), None)
        dropped = []
        for task in tasks:
            if task["status"] in objectives.TASK_TERMINAL:
                continue
            ids = task["arguments"].get("stage_ids") or []
            if current is not None and task["task_id"] == current["task_id"]:
                at = ids.index(stage.id)
                ids = ids[at + 1:] if finished else ids[at:]
            dropped.extend({"task": task["task_id"], "stage": sid} for sid in ids)
        return dropped


class StagePort:
    """The engine's capability port for `CAPABILITY`: one task's stages in order."""

    def __init__(self, spec: Spec, agent: Agent, cache: ResultCache,
                 model_for: ModelFor, gate: DispatchGate) -> None:
        self._stages = {stage.id: stage for stage in spec.stages}
        self._spec_id = spec.id
        self._agent = agent
        self._cache = cache
        self._model_for = model_for
        self._gate = gate

    async def __call__(self, capability: str, arguments: dict, run=None) -> dict:
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
                result = await self._dispatch(stage, values, model, arguments, run)
                self._cache.put(key, result)
            refs, values = [key], [result]
        return {"key": key, "stages": len(stage_ids), "hits": hits}

    async def _dispatch(self, stage: Stage, values: list, model: str,
                        arguments: dict, run) -> dict:
        lease = self._gate.admit(run, stage, arguments)
        try:
            result = await self._agent(stage, values, model)
        finally:
            self._gate.release(lease)
        self._gate.settle(run, stage, arguments)
        return result

    def _upstream(self, key: str) -> dict:
        found = self._cache.get(key)
        if found is None:
            raise LookupError(f"an upstream result is missing from the cache: {key[:12]}")
        return found
