"""UCW-002: a FRIDAY-native workflow as a versioned definition.

A definition is `workflow_id`, `version` and `steps`. Each step names a `role`
and an executor `kind` (a model `agent`, or a `deterministic` executor), says
which steps it `depends_on`, which fields it takes from each (`inputs`, its
typed handoff) and the JSON-Schema subset it hands on (`outputs`). It never
names a model: a model is chosen at run time, per role, from the tier the step
declares (ULTRA-006) and an owner role directive that may pin or exclude a
model. `compile_definition` reduces a definition to one ULTRA-001 `Spec`.

Every violation raises a typed `DefinitionError` whose `field` names where
(`steps[2].depends_on[1]`), the way `spec.SpecError` does.

Assumed (owner not asked): the ULTRA-001 stage list is linear, so the DAG is
flattened in dependency order with ties kept in declaration order, and a join
(a step with more than one dependency) or a step that sets `barrier` compiles
to a barrier stage.

LIMITATION, stated so nobody trusts the flat spec: it is a validation and
costing artefact, not a runnable plan. It drops each step's `executor`,
`depends_on` and `inputs`. The ULTRA-002 durable runner chains every stage
between two barriers into one pipeline, so independent roots would be chained
and a `deterministic` stage would be dispatched to a model agent. The compiled
spec is therefore a `DefinitionSpec` (`durable_runner_safe` is False) and
`Compiled.require_durable_runner_safe()` always refuses; so does
`runner.start_run` (NotDurableRunnerSafe). The DAG semantics
(no barrier between independent steps, a dependent starts only when its handoff
validates, a deterministic step goes to its executor and never a model) are
delivered only by `run_definition`, in process.
"""
from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Mapping, Sequence

from friday import model_capabilities
from friday.execution_economics import TIER_ECONOMY
from friday.ultracode.spec import Spec, SpecError, _check_schema, compile_plan, parse_spec
from friday.ultracode.tiers import choose_model

KINDS = ("agent", "deterministic")
#: A step may not carry any of these when no model is called.
_MODEL_FIELDS = ("template", "tier", "effort", "cap", "dry_rounds")
_STEP_KEYS = ("id", "role", "kind", "executor", "template", "tier", "effort", "cap",
              "dry_rounds", "barrier", "depends_on", "inputs", "outputs")
_DEFINITION_KEYS = ("workflow_id", "version", "budget_tokens", "steps")
_MODEL_KEYS = ("model", "model_id", "models")
#: Model families FRIDAY routes to besides the declared catalogue: the CLI
#: providers (cli_model_provider) and the Hermes subscription routes
#: (hermes_model_gateway_worker), and the common vendor families.
_EXTRA_FAMILIES = ("claude", "gpt", "gemini", "sonnet", "opus", "haiku", "llama", "mistral",
                   "qwen", "deepseek", "grok", "kimi", "minimax", "codex", "glm", "gemma",
                   "copilot")
#: Words that appear in catalogue ids but name a size or channel, not a model.
_NOT_A_FAMILY = frozenset({"flash", "lite", "pro", "preview", "mini", "nano", "latest"})


def _families() -> tuple[str, ...]:
    """Every family the declared model catalogue names, plus the extra families."""
    found = set(_EXTRA_FAMILIES)
    for _backend, model_id in model_capabilities.CAPABILITIES:
        for part in re.split(r"[-_:.]", model_id.lower().rsplit("/", 1)[-1]):
            word = re.match(r"[a-z]+", part)
            if word and len(word.group()) >= 3 and word.group() not in _NOT_A_FAMILY:
                found.add(word.group())
    return tuple(sorted(found))


#: A concrete model or vendor family, built from FRIDAY's own catalogue so a
#: newly declared model is refused without editing this file. Tiers (economy,
#: standard, deep) are not model names. The o-series (o1, o3-mini) is matched
#: by shape because a bare "o3" is too short to take from an id.
MODEL_NAME = re.compile(
    r"(?<![a-z0-9])((" + "|".join(map(re.escape, _families())) + r")(?![a-z])|o[1-9](?![a-z0-9]))",
    re.IGNORECASE)

STATUS_PINNED, STATUS_CHOSEN = "PINNED", "CHOSEN"
STATUS_UNAVAILABLE, STATUS_DETERMINISTIC = "UNAVAILABLE", "DETERMINISTIC"


class DefinitionError(ValueError):
    """A workflow definition that cannot be run. `field` names where."""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(f"{field}: {message}")
        self.field = field


class CycleError(DefinitionError):
    """The steps' depends_on form a cycle."""


class UnknownStepError(DefinitionError):
    """A step depends on a step the definition does not have."""


class HandoffSchemaError(DefinitionError):
    """A consumer expects a field the producer's schema lacks."""


class ModelNameError(DefinitionError):
    """A step names a concrete model; a definition never does."""


class DeterministicModelError(DefinitionError):
    """A deterministic step carries a model-dispatch field."""


class DirectiveError(DefinitionError):
    """An owner role directive that cannot be applied."""


@dataclass(frozen=True)
class Handoff:
    source: str
    fields: tuple[str, ...]

    def to_dict(self) -> dict:
        return {"from": self.source, "fields": list(self.fields)}


@dataclass(frozen=True)
class Step:
    id: str
    role: str
    kind: str
    outputs: dict
    depends_on: tuple[str, ...] = ()
    inputs: tuple[Handoff, ...] = ()
    executor: str = ""
    template: str = ""
    tier: str = ""
    effort: str = "medium"
    cap: int = 0
    dry_rounds: int = 0
    barrier: bool = False

    def to_dict(self) -> dict:
        out: dict = {"id": self.id, "role": self.role, "kind": self.kind,
                     "depends_on": list(self.depends_on),
                     "inputs": [h.to_dict() for h in self.inputs],
                     "outputs": json.loads(json.dumps(self.outputs)), "barrier": self.barrier}
        if self.kind == "deterministic":
            out["executor"] = self.executor
        else:
            out.update(template=self.template, tier=self.tier, effort=self.effort,
                       cap=self.cap, dry_rounds=self.dry_rounds)
        return out


@dataclass(frozen=True)
class Definition:
    workflow_id: str
    version: int
    budget_tokens: int
    steps: tuple[Step, ...]

    def to_dict(self) -> dict:
        return {"workflow_id": self.workflow_id, "version": self.version,
                "budget_tokens": self.budget_tokens,
                "steps": [s.to_dict() for s in self.steps]}

    def index(self, step_id: str) -> int:
        return next(i for i, s in enumerate(self.steps) if s.id == step_id)


# --- parsing -----------------------------------------------------------------------

def _wrap(field_: str, call, *args):
    """Run a spec.py validator, re-raising its SpecError as a DefinitionError."""
    try:
        return call(*args)
    except SpecError as exc:
        raise DefinitionError(exc.field, str(exc).split(": ", 1)[-1]) from exc


def _text(value, field_: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DefinitionError(field_, "must be a non-empty string")
    return value


def _int(value, field_: str, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise DefinitionError(field_, f"must be an integer >= {minimum}")
    return value


def _names(value, field_: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise DefinitionError(field_, "must be a list of strings")
    return tuple(_text(v, f"{field_}[{i}]") for i, v in enumerate(value))


def _scan_for_models(value, path: str) -> None:
    if isinstance(value, str):
        if MODEL_NAME.search(value):
            raise ModelNameError(path, f"names a model ({value!r}); a definition names a "
                                       "role and a tier, the runtime picks the model")
    elif isinstance(value, dict):
        for key, sub in value.items():
            _scan_for_models(sub, f"{path}.{key}")
    elif isinstance(value, list):
        for i, sub in enumerate(value):
            _scan_for_models(sub, f"{path}[{i}]")


def _step(raw, index: int) -> Step:
    at = f"steps[{index}]"
    if not isinstance(raw, dict):
        raise DefinitionError(at, "must be an object")
    for key, value in raw.items():
        if key in _MODEL_KEYS:
            raise ModelNameError(f"{at}.{key}", "a step never names a model")
        _scan_for_models(value, f"{at}.{key}")
    for key in raw:
        if key not in _STEP_KEYS:
            raise DefinitionError(f"{at}.{key}", "unknown field")
    for key in ("id", "role", "kind", "outputs"):
        if key not in raw:
            raise DefinitionError(f"{at}.{key}", "is required")
    kind = raw["kind"]
    if kind not in KINDS:
        raise DefinitionError(f"{at}.kind", f"must be one of {KINDS}")
    barrier = raw.get("barrier", False)
    if not isinstance(barrier, bool):
        raise DefinitionError(f"{at}.barrier", "must be true or false")
    outputs = raw["outputs"]
    _wrap(f"{at}.outputs", _check_schema, outputs, f"{at}.outputs")
    if outputs.get("type") != "object":
        raise DefinitionError(f"{at}.outputs.type", "a step hands on an object of named fields")
    handoffs = raw.get("inputs", [])
    if not isinstance(handoffs, list):
        raise DefinitionError(f"{at}.inputs", "must be a list")
    inputs = []
    for j, item in enumerate(handoffs):
        ref = f"{at}.inputs[{j}]"
        if not isinstance(item, dict):
            raise DefinitionError(ref, "must be an object")
        for key in item:
            if key not in ("from", "fields"):
                raise DefinitionError(f"{ref}.{key}", "unknown field")
        for key in ("from", "fields"):
            if key not in item:
                raise DefinitionError(f"{ref}.{key}", "is required")
        fields = _names(item["fields"], f"{ref}.fields")
        if not fields:
            raise DefinitionError(f"{ref}.fields", "must name at least one field")
        inputs.append(Handoff(_text(item["from"], f"{ref}.from"), fields))
    common = dict(id=_text(raw["id"], f"{at}.id"), role=_text(raw["role"], f"{at}.role"),
                  kind=kind, outputs=json.loads(json.dumps(outputs)), barrier=barrier,
                  depends_on=_names(raw.get("depends_on", []), f"{at}.depends_on"),
                  inputs=tuple(inputs))
    if kind == "deterministic":
        for key in _MODEL_FIELDS:
            if key in raw:
                raise DeterministicModelError(
                    f"{at}.{key}", "a deterministic step calls no model, so it has no "
                                   f"{key}; use kind 'agent' for a model step")
        if "executor" not in raw:
            raise DefinitionError(f"{at}.executor", "is required for a deterministic step")
        return Step(executor=_text(raw["executor"], f"{at}.executor"), **common)
    if "executor" in raw:
        raise DefinitionError(f"{at}.executor", "only a deterministic step has an executor")
    for key in ("template", "tier", "cap"):
        if key not in raw:
            raise DefinitionError(f"{at}.{key}", "is required for an agent step")
    return Step(template=_text(raw["template"], f"{at}.template"),
                tier=_text(raw["tier"], f"{at}.tier"),
                effort=_text(raw.get("effort", "medium"), f"{at}.effort"),
                cap=_int(raw["cap"], f"{at}.cap", 1),
                dry_rounds=_int(raw.get("dry_rounds", 0), f"{at}.dry_rounds", 0), **common)


def _find_cycle(steps: tuple[Step, ...], stuck: set[str]) -> tuple[int, list[str]]:
    """A cycle reachable from the first stuck step: its first step's index and path."""
    by_id = {s.id: s for s in steps}
    index = {s.id: i for i, s in enumerate(steps)}
    node = next(s.id for s in steps if s.id in stuck)
    path: list[str] = []
    while node not in path:
        path.append(node)
        node = next(d for d in by_id[node].depends_on if d in stuck)
    cycle = path[path.index(node):]
    return index[cycle[0]], cycle + [cycle[0]]


def _order(steps: tuple[Step, ...]) -> tuple[str, ...]:
    """Dependency order, ties in declaration order. Raises CycleError."""
    done: list[str] = []
    waiting = list(steps)
    while waiting:
        ready = next((s for s in waiting if all(d in done for d in s.depends_on)), None)
        if ready is None:
            where, cycle = _find_cycle(steps, {s.id for s in waiting})
            raise CycleError(f"steps[{where}].depends_on",
                             "depends_on forms a cycle: " + " -> ".join(cycle))
        done.append(ready.id)
        waiting.remove(ready)
    return tuple(done)


def parse_definition(source) -> Definition:
    """Validate a definition given as JSON text or an already-decoded dict."""
    if isinstance(source, (str, bytes)):
        try:
            source = json.loads(source)
        except ValueError as exc:
            raise DefinitionError("$", f"not valid JSON ({exc})") from exc
    if not isinstance(source, dict):
        raise DefinitionError("$", "a definition is a JSON object")
    for key in source:
        if key not in _DEFINITION_KEYS:
            raise DefinitionError(key, "unknown field")
    for key in _DEFINITION_KEYS:
        if key not in source:
            raise DefinitionError(key, "is required")
    raw_steps = source["steps"]
    if not isinstance(raw_steps, list) or not raw_steps:
        raise DefinitionError("steps", "must be a non-empty list")
    steps = tuple(_step(raw, i) for i, raw in enumerate(raw_steps))
    known: dict[str, Step] = {}
    for i, step in enumerate(steps):
        if step.id in known:
            raise DefinitionError(f"steps[{i}].id", f"duplicate step id {step.id!r}")
        known[step.id] = step
    for i, step in enumerate(steps):
        for j, dep in enumerate(step.depends_on):
            if dep not in known:
                raise UnknownStepError(f"steps[{i}].depends_on[{j}]",
                                       f"depends on {dep!r}, which is not a step")
    _order(steps)
    for i, step in enumerate(steps):
        for j, handoff in enumerate(step.inputs):
            ref = f"steps[{i}].inputs[{j}]"
            if handoff.source not in step.depends_on:
                raise HandoffSchemaError(f"{ref}.from", f"{handoff.source!r} is not in "
                                                        "this step's depends_on")
            offered = known[handoff.source].outputs.get("properties", {})
            for k, name in enumerate(handoff.fields):
                if name not in offered:
                    raise HandoffSchemaError(
                        f"{ref}.fields[{k}]",
                        f"{name!r} is not in the outputs schema of {handoff.source!r}")
    return Definition(workflow_id=_text(source["workflow_id"], "workflow_id"),
                      version=_int(source["version"], "version", 1),
                      budget_tokens=_int(source["budget_tokens"], "budget_tokens", 1),
                      steps=steps)


# --- compile -----------------------------------------------------------------------

@dataclass(frozen=True)
class DefinitionSpec(Spec):
    """The flat ULTRA-001 spec of a definition. It validates and costs the
    workflow but loses depends_on, inputs and executors, so the durable runner
    would run it wrong (see the module docstring). A caller that starts runs
    must check `durable_runner_safe` and refuse when it is False."""

    durable_runner_safe = False


@dataclass(frozen=True)
class Compiled:
    definition: Definition
    spec: DefinitionSpec
    plan: dict
    order: tuple[str, ...]

    def require_durable_runner_safe(self) -> Spec:
        """Always refuses: the flat spec must not go to runner.start_run."""
        raise DefinitionError(
            "spec", f"{self.spec.id} is not runnable on the durable runner: it chains "
                    "independent steps in one pipeline and would send a deterministic step "
                    "to a model. Run it with run_definition")


def compile_definition(definition: Definition) -> Compiled:
    """The ULTRA-001 spec for this definition, stages in dependency order."""
    order = _order(definition.steps)
    by_id = {s.id: s for s in definition.steps}
    stages = []
    for step_id in order:
        step = by_id[step_id]
        barrier = step.barrier or len(step.depends_on) > 1
        if step.kind == "deterministic":
            stages.append({"id": step.id, "template": "deterministic", "barrier": barrier,
                           "schema": step.outputs, "tier": TIER_ECONOMY, "cap": 1})
        else:
            stages.append({"id": step.id, "template": step.template, "barrier": barrier,
                           "schema": step.outputs, "tier": step.tier, "effort": step.effort,
                           "cap": step.cap, "dry_rounds": step.dry_rounds})
    raw = {"id": f"{definition.workflow_id}@v{definition.version}",
           "budget_tokens": definition.budget_tokens, "stages": stages}
    try:
        parsed = parse_spec(raw)
        spec = DefinitionSpec(id=parsed.id, budget_tokens=parsed.budget_tokens,
                              stages=parsed.stages)
        plan = compile_plan(spec)
    except SpecError as exc:
        # Name the step as the definition spells it, not the flattened stage.
        found = re.match(r"stages\[(\d+)\](.*)", exc.field)
        if found is None:
            raise DefinitionError(exc.field, str(exc).split(": ", 1)[-1]) from exc
        where = definition.index(order[int(found.group(1))])
        raise DefinitionError(f"steps[{where}]{found.group(2)}",
                              str(exc).split(": ", 1)[-1]) from exc
    return Compiled(definition=definition, spec=spec, plan=plan, order=order)


# --- versions ----------------------------------------------------------------------

class DefinitionRegistry:
    """Definitions by (workflow_id, version); a bump never removes the old one."""

    def __init__(self) -> None:
        self._by_key: dict[tuple[str, int], Definition] = {}

    def register(self, definition: Definition) -> None:
        key = (definition.workflow_id, definition.version)
        held = self._by_key.get(key)
        if held is not None and held != definition:
            raise DefinitionError("version", f"{definition.workflow_id} v{definition.version} "
                                             "is already registered with other content; bump "
                                             "the version")
        self._by_key[key] = definition

    def versions(self, workflow_id: str) -> tuple[int, ...]:
        return tuple(sorted(v for w, v in self._by_key if w == workflow_id))

    def get(self, workflow_id: str, version: int | None = None) -> Definition:
        held = self.versions(workflow_id)
        if not held:
            raise KeyError(f"no workflow {workflow_id!r}")
        wanted = held[-1] if version is None else version
        if (workflow_id, wanted) not in self._by_key:
            raise KeyError(f"workflow {workflow_id!r} has no version {wanted}")
        return self._by_key[(workflow_id, wanted)]


# --- role directives ---------------------------------------------------------------

@dataclass(frozen=True)
class Resolution:
    """The runtime choice for one step and why: kept as the run's record."""
    step: str
    role: str
    status: str
    model: str | None
    reason: str
    tier: str = ""
    evidence: tuple[dict, ...] = field(default=())

    def to_dict(self) -> dict:
        return {"step": self.step, "role": self.role, "status": self.status,
                "model": self.model, "reason": self.reason, "tier": self.tier,
                "evidence": [dict(row) for row in self.evidence]}


def _directive(role: str, raw) -> tuple[str | None, frozenset[str]]:
    at = f"directives.{role}"
    if not isinstance(raw, Mapping):
        raise DirectiveError(at, "must be an object")
    for key in raw:
        if key not in ("pin", "exclude"):
            raise DirectiveError(f"{at}.{key}", "unknown field (pin, exclude)")
    pin = raw.get("pin")
    if pin is not None and (not isinstance(pin, str) or not pin.strip()):
        raise DirectiveError(f"{at}.pin", "must be a non-empty string")
    exclude = raw.get("exclude", [])
    if not isinstance(exclude, (list, tuple)) or not all(isinstance(m, str) for m in exclude):
        raise DirectiveError(f"{at}.exclude", "must be a list of strings")
    if pin is not None and pin in exclude:
        raise DirectiveError(at, f"pins and excludes {pin!r}")
    return pin, frozenset(exclude)


def resolve_roles(definition: Definition, directives: Mapping[str, Mapping], *,
                  routes: Mapping[str, Sequence[str]], available,
                  bench: Mapping[str, dict] | None = None) -> tuple[Resolution, ...]:
    """Pick the model for each step. An owner pin is honoured when its route is
    available and otherwise reported UNAVAILABLE - never swapped for another."""
    parsed = {role: _directive(role, raw) for role, raw in directives.items()}
    known = {step.role for step in definition.steps}
    for role in parsed:
        if role not in known:
            raise DirectiveError(f"directives.{role}", f"no step has the role {role!r} "
                                 f"(roles: {sorted(known)}); a directive for it would be "
                                 "silently ignored")
    out = []
    for step in definition.steps:
        if step.kind == "deterministic":
            out.append(Resolution(step.id, step.role, STATUS_DETERMINISTIC, None,
                                  f"deterministic executor {step.executor}; no model"))
            continue
        pin, exclude = parsed.get(step.role, (None, frozenset()))
        if pin is not None:
            if pin in available:
                out.append(Resolution(step.id, step.role, STATUS_PINNED, pin,
                                      f"owner directive pins role {step.role} to {pin}",
                                      step.tier))
            else:
                out.append(Resolution(step.id, step.role, STATUS_UNAVAILABLE, None,
                                      f"owner directive pins role {step.role} to {pin}, "
                                      "which is not available; no substitute was chosen",
                                      step.tier))
            continue
        candidates = [m for m in routes.get(step.tier, ()) if m in available and m not in exclude]
        left = f" (excluded for role {step.role}: {sorted(exclude)})" if exclude else ""
        if not candidates:
            out.append(Resolution(step.id, step.role, STATUS_UNAVAILABLE, None,
                                  f"no available {step.tier} route{left}", step.tier))
            continue
        choice = choose_model(step.tier, candidates, bench or {})
        out.append(Resolution(step.id, step.role, STATUS_CHOSEN, choice.model,
                              choice.reason + left, step.tier, choice.evidence))
    return tuple(out)


# --- running -----------------------------------------------------------------------

@dataclass(frozen=True)
class StepOutcome:
    """DONE, REFUSED (its handoff did not validate; `field` says where),
    INVALID (it ran but its own result violates its outputs schema; never DONE),
    UNAVAILABLE (no route for its role) or BLOCKED (an upstream step did not finish)."""
    step: str
    state: str
    result: dict | None = None
    field: str = ""
    reason: str = ""
    model: str | None = None


Agent = Callable[[Step, dict, "str | None"], Awaitable[dict]]
Executor = Callable[[Step, dict], Awaitable[dict]]

_JSON_TYPES = {"object": dict, "array": list, "string": str, "null": type(None)}


def _mismatch(value, schema: dict) -> str:
    """Why `value` is not an instance of the schema subset, or ''."""
    kind = schema["type"]
    if kind == "boolean":
        ok = isinstance(value, bool)
    elif kind == "integer":
        ok = isinstance(value, int) and not isinstance(value, bool)
    elif kind == "number":
        ok = isinstance(value, (int, float)) and not isinstance(value, bool)
    else:
        ok = isinstance(value, _JSON_TYPES[kind])
    if not ok:
        return f"expected {kind}, got {type(value).__name__}"
    if "enum" in schema and value not in schema["enum"]:
        return f"not one of {schema['enum']}"
    if kind == "object":
        for name in schema.get("required", []):
            if name not in value:
                return f"missing {name!r}"
        for name, sub in schema.get("properties", {}).items():
            if name in value and (why := _mismatch(value[name], sub)):
                return f"{name}: {why}"
    if kind == "array" and "items" in schema:
        for i, item in enumerate(value):
            if why := _mismatch(item, schema["items"]):
                return f"[{i}]: {why}"
    return ""


async def run_definition(compiled: Compiled, *, agent: Agent,
                         executors: Mapping[str, Executor],
                         resolutions: Mapping[str, Resolution] | None = None
                         ) -> dict[str, StepOutcome]:
    """Run the steps as a DAG. A step with no unfinished dependency starts at
    once - there is no barrier between independent steps - and a dependent step
    starts only after every field it takes validates against the producer's
    schema. A refused, unavailable or blocked step leaves its dependents
    BLOCKED; independent work carries on. Agent and executor errors propagate."""
    definition = compiled.definition
    for i, step in enumerate(definition.steps):
        if step.kind == "deterministic" and step.executor not in executors:
            raise DefinitionError(f"steps[{i}].executor",
                                  f"no executor registered for {step.executor!r}")
    by_id = {s.id: s for s in definition.steps}
    tasks: dict[str, asyncio.Task] = {}

    async def one(step: Step) -> StepOutcome:
        index = definition.index(step.id)
        upstream = {dep: await tasks[dep] for dep in step.depends_on}
        late = [dep for dep, got in upstream.items() if got.state != "DONE"]
        if late:
            return StepOutcome(step.id, "BLOCKED", reason=f"{late} did not finish")
        chosen = (resolutions or {}).get(step.id)
        if chosen is not None and chosen.status == STATUS_UNAVAILABLE:
            return StepOutcome(step.id, "UNAVAILABLE", reason=chosen.reason)
        handed: dict[str, dict] = {}
        for j, handoff in enumerate(step.inputs):
            produced = upstream[handoff.source].result
            schema = by_id[handoff.source].outputs["properties"]
            for k, name in enumerate(handoff.fields):
                at = f"steps[{index}].inputs[{j}].fields[{k}]"
                if not isinstance(produced, dict) or name not in produced:
                    return StepOutcome(step.id, "REFUSED", field=at, reason=(
                        f"{handoff.source!r} did not produce {name!r}"))
                if why := _mismatch(produced[name], schema[name]):
                    return StepOutcome(step.id, "REFUSED", field=at, reason=(
                        f"{handoff.source!r}.{name} does not match its schema: {why}"))
                handed.setdefault(handoff.source, {})[name] = produced[name]
        model = chosen.model if chosen is not None else None
        if step.kind == "deterministic":
            result = await executors[step.executor](step, handed)
        else:
            result = await agent(step, handed, model)
        if why := _mismatch(result, step.outputs):
            return StepOutcome(step.id, "INVALID", result=result, model=model,
                               field=f"steps[{index}].outputs",
                               reason=f"result does not match its outputs schema: {why}")
        return StepOutcome(step.id, "DONE", result=result, model=model,
                           reason=chosen.reason if chosen is not None else "")

    for step_id in compiled.order:
        tasks[step_id] = asyncio.ensure_future(one(by_id[step_id]))
    try:
        done = await asyncio.gather(*tasks.values())
    except BaseException:
        for task in tasks.values():
            task.cancel()
        await asyncio.gather(*tasks.values(), return_exceptions=True)
        raise
    return {outcome.step: outcome for outcome in done}
