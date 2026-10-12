"""UCW-009: the Workflow Compiler.

Repeated successful agent trajectories of one task class are compiled into a UCW-002
definition of deterministic steps, and promoted only when a replay of that definition
over the source events produces the same verified outcome at measurably fewer tokens.
A compiled workflow that diverges at run time hands the event to the agent path and is
marked for revalidation; it is not used again until a new replay promotes the next
version. Extends WF-005 (detect, compile) and WF-010 (promotion threshold) with the
equivalence replay and the fallback.

Assumed (owner not asked):
- A trajectory step is one call of a named executor with a recorded result, run in
  order, so the compiled definition is a chain of deterministic steps. A step that
  needed a model (empty executor) is not compilable in this slice.
- The outcome of a trajectory or a run is the last step's result.
- "Same verified outcome" means equal to the recorded outcome AND accepted by the
  caller's independent `verify(event, outcome)`; "measured" tokens come from the
  caller's cumulative `meter()` read around each replay.
- The person's policy is `Policy`: `off` never compiles, `propose` replays and records
  the proposal with its expected savings, `compile` also registers the definition.
- An executor error is not a divergence: it propagates to the caller, never swallowed.
- Promotions, divergences and the revalidation marks live in memory; persisting them
  is a later wiring step.
- `succeeded` on a source trajectory is the caller's flag; its recorded outcome is not
  re-run through `verify`. A trajectory id may appear once; a repeat raises.
- The meter must be monotonic; a frozen meter reads as zero cost, a backwards one raises.
  A missing executor or an executor error during the compile replay raises after a
  REPLAY_ERROR promotion is recorded; at run() a missing executor raises DefinitionError.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Mapping

from friday.ultracode import definition as D

OFF, PROPOSE, COMPILE = "off", "propose", "compile"
MODES = (OFF, PROPOSE, COMPILE)

PROMOTED = "PROMOTED"                        # registered; the compiled form is live
PROPOSED = "PROPOSED"                        # replay proved it; the policy waits for the person
REFUSED_BY_POLICY = "REFUSED_BY_POLICY"
INSUFFICIENT = "INSUFFICIENT"                # fewer successes than the policy requires
DIVERGENT_TRAJECTORIES = "DIVERGENT_TRAJECTORIES"
NOT_COMPILABLE = "NOT_COMPILABLE"
INVALID = "INVALID"                          # the draft fails the UCW-002 schema
OUTCOME_MISMATCH = "OUTCOME_MISMATCH"        # a cheaper candidate that fails the outcome check
NOT_CHEAPER = "NOT_CHEAPER"
REPLAY_ERROR = "REPLAY_ERROR"                # an executor raised during the replay; it propagates

COMPILED_PATH, AGENT_PATH = "compiled", "agent"
_ACTIVE, _REVALIDATE = "ACTIVE", "REVALIDATE"

_TYPES = ((bool, "boolean"), (int, "integer"), (float, "number"), (str, "string"),
          (list, "array"), (dict, "object"), (type(None), "null"))

Executors = Mapping[str, D.Executor]


@dataclass(frozen=True)
class Policy:
    """The person's rule for compiling: how much evidence and how much saving."""
    mode: str = PROPOSE
    min_successes: int = 3
    min_savings: float = 0.0

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        if isinstance(self.min_successes, bool) or not isinstance(self.min_successes, int) \
                or self.min_successes < 1:
            raise ValueError("min_successes must be an integer >= 1")
        if not isinstance(self.min_savings, (int, float)) or not 0 <= self.min_savings < 1:
            raise ValueError("min_savings must be a fraction in [0, 1)")


@dataclass(frozen=True)
class TrajectoryStep:
    role: str
    executor: str          # "" when the step needed a model
    result: dict


@dataclass(frozen=True)
class Trajectory:
    id: str
    task_class: str
    event: dict
    steps: tuple[TrajectoryStep, ...]
    succeeded: bool
    tokens: int            # measured tokens the agent spent on this run

    @property
    def outcome(self) -> dict | None:
        return self.steps[-1].result if self.steps else None


@dataclass(frozen=True)
class Promotion:
    """The record of one compile attempt, kept whatever the verdict."""
    status: str
    task_class: str
    workflow_id: str
    version: int
    reason: str
    source_trajectories: tuple[str, ...]
    baseline_tokens: int = 0
    replay_tokens: int = 0
    savings: float = 0.0
    definition: D.Definition | None = None

    def to_dict(self) -> dict:
        return {"status": self.status, "task_class": self.task_class,
                "workflow_id": self.workflow_id, "version": self.version,
                "reason": self.reason, "source_trajectories": list(self.source_trajectories),
                "baseline_tokens": self.baseline_tokens, "replay_tokens": self.replay_tokens,
                "savings": self.savings,
                "definition": self.definition.to_dict() if self.definition else None}


@dataclass(frozen=True)
class RunResult:
    path: str
    outcome: dict | None
    fell_back: bool
    reason: str = ""


def _json_type(value) -> str:
    return next(name for kind, name in _TYPES if isinstance(value, kind))


def _outputs(results: list[dict]) -> dict:
    """The schema every recorded result satisfies: the fields all of them carry."""
    properties = {}
    for name in results[0]:
        if not all(name in r for r in results):
            continue
        kinds = {_json_type(r[name]) for r in results}
        if kinds == {"integer", "number"}:
            kinds = {"number"}
        if len(kinds) == 1:
            properties[name] = {"type": kinds.pop()}
    return {"type": "object", "properties": properties, "required": sorted(properties)}


async def _no_model(step: D.Step, handed: dict, model: "str | None") -> dict:
    raise RuntimeError(f"compiled step {step.id!r} reached a model call")


def _first_unfinished(outcomes: Mapping[str, D.StepOutcome], order: tuple[str, ...]) -> str:
    for step_id in order:
        got = outcomes[step_id]
        if got.state != "DONE":
            return f"{step_id} {got.state}: {got.reason}"
    return ""


class WorkflowCompiler:
    def __init__(self, registry: D.DefinitionRegistry | None = None,
                 policy: Policy | None = None) -> None:
        self.registry = registry if registry is not None else D.DefinitionRegistry()
        self.policy = policy if policy is not None else Policy()
        self.promotions: list[Promotion] = []
        self.divergences: list[dict] = []
        self._state: dict[str, str] = {}

    def is_active(self, workflow_id: str) -> bool:
        return self._state.get(workflow_id) == _ACTIVE

    def needs_revalidation(self, workflow_id: str) -> bool:
        return self._state.get(workflow_id) == _REVALIDATE

    def _record(self, promotion: Promotion) -> Promotion:
        self.promotions.append(promotion)
        return promotion

    async def compile(self, task_class: str, trajectories: list[Trajectory], *,
                      executors_for: Callable[[dict], Executors],
                      verify: Callable[[dict, dict], bool],
                      meter: Callable[[], int]) -> Promotion:
        """Compile `task_class` from its successful trajectories, or record why not."""
        for t in trajectories:
            if t.task_class != task_class:
                raise ValueError(f"trajectory {t.id!r} is of task class {t.task_class!r}, "
                                 f"not {task_class!r}")
        seen: set[str] = set()
        for t in trajectories:
            if t.id in seen:
                raise ValueError(f"duplicate trajectory id {t.id!r}: one recorded run cannot "
                                 "count as more than one success")
            seen.add(t.id)
        used = [t for t in trajectories if t.succeeded and t.steps]
        sources = tuple(t.id for t in used)
        workflow_id = f"compiled.{task_class}"
        version = max(self.registry.versions(workflow_id), default=0) + 1

        def refuse(status: str, reason: str, **more) -> Promotion:
            return self._record(Promotion(status, task_class, workflow_id, version, reason,
                                          sources, **more))

        if self.policy.mode == OFF:
            return refuse(REFUSED_BY_POLICY, "the person's policy does not compile workflows")
        if len(used) < self.policy.min_successes:
            return refuse(INSUFFICIENT, f"{len(used)} of {self.policy.min_successes} required "
                                        f"successful trajectories for {task_class!r}")
        shapes = {tuple((s.role, s.executor) for s in t.steps) for t in used}
        if len(shapes) > 1:
            return refuse(DIVERGENT_TRAJECTORIES,
                          f"{len(shapes)} different procedures among the successes; "
                          "they are not one repeatable procedure")
        for i, step in enumerate(used[0].steps):
            if not step.executor:
                return refuse(NOT_COMPILABLE, f"step {i + 1} needed a model; this compiler "
                                              "emits deterministic steps only")
            if not all(isinstance(t.steps[i].result, dict) for t in used):
                return refuse(NOT_COMPILABLE, f"step {i + 1} has a result that is not an object")

        baseline = sum(t.tokens for t in used)
        steps = []
        for i, step in enumerate(used[0].steps):
            steps.append({"id": f"step{i + 1}", "role": step.role, "kind": "deterministic",
                          "executor": step.executor,
                          "depends_on": [f"step{i}"] if i else [],
                          "outputs": _outputs([t.steps[i].result for t in used])})
        try:
            definition = D.parse_definition({
                "workflow_id": workflow_id, "version": version,
                "budget_tokens": max(1, baseline // len(used)), "steps": steps})
            compiled = D.compile_definition(definition)
        except D.DefinitionError as exc:
            return refuse(INVALID, str(exc), baseline_tokens=baseline)

        replay_tokens, failures = 0, []
        for t in used:
            before = meter()
            try:
                outcomes = await D.run_definition(compiled, agent=_no_model,
                                                  executors=executors_for(t.event))
            except Exception as exc:
                refuse(REPLAY_ERROR, f"replay of {t.id} raised {type(exc).__name__}: {exc}",
                       baseline_tokens=baseline, definition=definition)
                raise
            spent = meter() - before
            if spent < 0:
                raise ValueError(f"the token meter went backwards during the replay of {t.id} "
                                 f"({spent}); a measured saving needs a monotonic meter")
            replay_tokens += spent
            stuck = _first_unfinished(outcomes, compiled.order)
            outcome = outcomes[compiled.order[-1]].result
            if stuck:
                failures.append(f"{t.id}: {stuck}")
            elif outcome != t.outcome:
                failures.append(f"{t.id}: replayed outcome differs from the recorded one")
            elif not verify(t.event, outcome):
                failures.append(f"{t.id}: the outcome check rejected the replayed outcome")
        saved = 1 - replay_tokens / baseline if baseline > 0 else 0.0
        measured = dict(baseline_tokens=baseline, replay_tokens=replay_tokens,
                        savings=saved, definition=definition)
        if failures:
            return refuse(OUTCOME_MISMATCH, "outcome check failed on replay; " + "; ".join(failures),
                          **measured)
        if replay_tokens >= baseline or saved < self.policy.min_savings:
            return refuse(NOT_CHEAPER, f"replay used {replay_tokens} of {baseline} tokens "
                                       f"(saves {saved:.2f}); the policy needs at least "
                                       f"{self.policy.min_savings}", **measured)
        if self.policy.mode == PROPOSE:
            return refuse(PROPOSED, f"replay matched all {len(used)} outcomes and saves "
                                    f"{saved:.2f} of the tokens; awaiting the person", **measured)
        self.registry.register(definition)
        self._state[workflow_id] = _ACTIVE
        return refuse(PROMOTED, f"replay matched all {len(used)} outcomes and saves "
                                f"{saved:.2f} of the tokens", **measured)

    async def run(self, workflow_id: str, event: dict, *,
                  executors_for: Callable[[dict], Executors],
                  verify: Callable[[dict, dict], bool],
                  agent_path: Callable[[dict], Awaitable[dict]]) -> RunResult:
        """Run the compiled form; on divergence run the agent path and mark it."""
        state = self._state.get(workflow_id)
        if state != _ACTIVE:
            reason = ("awaiting revalidation" if state == _REVALIDATE
                      else f"no compiled workflow {workflow_id!r}")
            return RunResult(AGENT_PATH, await agent_path(event), False, reason)
        definition = self.registry.get(workflow_id)
        compiled = D.compile_definition(definition)
        outcomes = await D.run_definition(compiled, agent=_no_model,
                                          executors=executors_for(event))
        reason = _first_unfinished(outcomes, compiled.order)
        outcome = outcomes[compiled.order[-1]].result
        if not reason and not verify(event, outcome):
            reason = "the outcome check rejected the compiled outcome"
        if not reason:
            return RunResult(COMPILED_PATH, outcome, False)
        self._state[workflow_id] = _REVALIDATE
        self.divergences.append({"workflow_id": workflow_id, "version": definition.version,
                                 "reason": reason})
        return RunResult(AGENT_PATH, await agent_path(event), True, reason)
