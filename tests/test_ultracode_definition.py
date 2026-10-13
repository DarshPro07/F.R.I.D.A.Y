"""UCW-002: a FRIDAY-native workflow is a versioned definition (steps with a
role, an executor kind, typed handoffs and depends_on) that compiles to an
ULTRA-001 spec, never names a model, and honours an owner role directive."""
from __future__ import annotations

import asyncio
import copy
import json

import pytest

from friday.ultracode import definition as D
from friday.ultracode import spec as S

_FINDINGS = {"type": "object", "required": ["findings"],
             "properties": {"findings": {"type": "array", "items": {"type": "string"}}}}
_NOTES = {"type": "object", "required": ["notes"],
          "properties": {"notes": {"type": "string"}}}
_PATCH = {"type": "object", "required": ["patch"],
          "properties": {"patch": {"type": "string"}}}
_TESTED = {"type": "object", "required": ["passed"],
           "properties": {"passed": {"type": "boolean"}}}
_VERDICT = {"type": "object", "required": ["accept"],
            "properties": {"accept": {"type": "boolean"}}}


@pytest.fixture(autouse=True)
def _empty_profile_store(tmp_path, monkeypatch):
    """Parsing reads the route profile store; pin it so no machine's routes leak in."""
    monkeypatch.setenv("FRIDAY_MODEL_PROFILES", str(tmp_path / "model_profiles.json"))


def build_capability(version: int = 1) -> dict:
    """friday.build_capability: two independent lookups, a build, a
    deterministic test run, and one deep judge."""
    return {
        "workflow_id": "friday.build_capability", "version": version,
        "budget_tokens": 200000,
        "steps": [
            {"id": "scan", "role": "scout", "kind": "agent", "template": "lookup",
             "tier": "economy", "cap": 4000, "depends_on": [], "inputs": [],
             "outputs": copy.deepcopy(_FINDINGS)},
            {"id": "review", "role": "reviewer", "kind": "agent", "template": "check",
             "tier": "economy", "cap": 4000, "depends_on": [], "inputs": [],
             "outputs": copy.deepcopy(_NOTES)},
            {"id": "build", "role": "builder", "kind": "agent", "template": "builder",
             "tier": "standard", "cap": 40000, "depends_on": ["scan", "review"],
             "inputs": [{"from": "scan", "fields": ["findings"]},
                        {"from": "review", "fields": ["notes"]}],
             "outputs": copy.deepcopy(_PATCH)},
            {"id": "test", "role": "tester", "kind": "deterministic",
             "executor": "pytest.run", "depends_on": ["build"],
             "inputs": [{"from": "build", "fields": ["patch"]}],
             "outputs": copy.deepcopy(_TESTED)},
            {"id": "verdict", "role": "judge", "kind": "agent", "template": "judge",
             "tier": "deep", "cap": 20000, "depends_on": ["build", "test"],
             "inputs": [{"from": "test", "fields": ["passed"]}],
             "outputs": copy.deepcopy(_VERDICT)},
        ],
    }


def _step(raw: dict, step_id: str) -> dict:
    return next(s for s in raw["steps"] if s["id"] == step_id)


# --- round trip and compile ------------------------------------------------------

def test_a_definition_round_trips_and_compiles_to_an_ultra001_spec():
    definition = D.parse_definition(json.dumps(build_capability()))
    again = D.parse_definition(json.dumps(definition.to_dict()))
    assert again == definition and again.to_dict() == definition.to_dict()
    compiled = D.compile_definition(definition)
    assert isinstance(compiled.spec, S.Spec)
    assert compiled.spec.id == "friday.build_capability@v1"
    assert [s.id for s in compiled.spec.stages] == ["scan", "review", "build", "test", "verdict"]
    by_id = {s.id: s for s in compiled.spec.stages}
    assert by_id["build"].barrier and by_id["verdict"].barrier      # joins wait for all
    assert not by_id["scan"].barrier and not by_id["test"].barrier
    assert by_id["build"].schema == _PATCH and by_id["verdict"].tier == "deep"
    assert compiled.plan == S.compile_plan(S.parse_spec(compiled.plan))
    assert compiled.order == ("scan", "review", "build", "test", "verdict")


def test_a_compiled_plan_never_names_a_model():
    compiled = D.compile_definition(D.parse_definition(build_capability()))
    assert not D.MODEL_NAME.search(json.dumps(compiled.plan))
    assert not D.MODEL_NAME.search(json.dumps(build_capability()))


# --- typed errors naming the field ------------------------------------------------

def test_a_cycle_raises_a_typed_error_naming_the_step():
    raw = build_capability()
    _step(raw, "scan")["depends_on"] = ["verdict"]
    with pytest.raises(D.CycleError) as caught:
        D.parse_definition(raw)
    assert caught.value.field == "steps[0].depends_on"
    assert "scan" in str(caught.value) and "verdict" in str(caught.value)


def test_a_step_depending_on_itself_is_a_cycle():
    raw = build_capability()
    _step(raw, "review")["depends_on"] = ["review"]
    with pytest.raises(D.CycleError) as caught:
        D.parse_definition(raw)
    assert caught.value.field == "steps[1].depends_on"


def test_a_dependency_on_an_unknown_step_names_the_field():
    raw = build_capability()
    _step(raw, "build")["depends_on"] = ["scan", "ghost"]
    with pytest.raises(D.UnknownStepError) as caught:
        D.parse_definition(raw)
    assert caught.value.field == "steps[2].depends_on[1]"
    assert "ghost" in str(caught.value)


def test_a_handoff_field_the_producer_schema_lacks_names_the_field():
    raw = build_capability()
    _step(raw, "build")["inputs"][0]["fields"] = ["findings", "confidence"]
    with pytest.raises(D.HandoffSchemaError) as caught:
        D.parse_definition(raw)
    assert caught.value.field == "steps[2].inputs[0].fields[1]"
    assert "confidence" in str(caught.value)


def test_a_handoff_from_a_step_it_does_not_depend_on_is_refused():
    raw = build_capability()
    _step(raw, "verdict")["inputs"] = [{"from": "scan", "fields": ["findings"]}]
    with pytest.raises(D.HandoffSchemaError) as caught:
        D.parse_definition(raw)
    assert caught.value.field == "steps[4].inputs[0].from"


@pytest.mark.parametrize("path, value, field", [
    ("model", "claude-sonnet-5", "steps[2].model"),
    ("role", "gpt-5-builder", "steps[2].role"),
    ("template", "sonnet", "steps[2].template"),
])
def test_a_model_name_inside_a_step_names_the_field(path, value, field):
    raw = build_capability()
    _step(raw, "build")[path] = value
    with pytest.raises(D.ModelNameError) as caught:
        D.parse_definition(raw)
    assert caught.value.field == field


@pytest.mark.parametrize("key, value", [("tier", "economy"), ("template", "check"),
                                        ("effort", "low"), ("cap", 100)])
def test_a_deterministic_step_that_calls_a_model_names_the_field(key, value):
    raw = build_capability()
    _step(raw, "test")[key] = value
    with pytest.raises(D.DeterministicModelError) as caught:
        D.parse_definition(raw)
    assert caught.value.field == f"steps[3].{key}"


def test_an_agent_step_without_a_tier_is_refused_and_unknown_fields_named():
    raw = build_capability()
    del _step(raw, "scan")["tier"]
    with pytest.raises(D.DefinitionError) as caught:
        D.parse_definition(raw)
    assert caught.value.field == "steps[0].tier"
    raw = build_capability()
    raw["steps"][0]["colour"] = "red"
    with pytest.raises(D.DefinitionError) as caught:
        D.parse_definition(raw)
    assert caught.value.field == "steps[0].colour"


# --- scheduling: no barrier between independents, handoff gates dependents --------

def _runtime(log: list, outputs: dict, gate: asyncio.Event | None = None):
    started: dict[str, asyncio.Event] = {}

    async def agent(step, inputs, model):
        log.append(("start", step.id, model))
        started.setdefault(step.id, asyncio.Event()).set()
        if gate is not None and step.id in ("scan", "review"):
            # each independent step waits until BOTH have started: only possible
            # if neither waited for the other (no barrier).
            await asyncio.wait_for(_both(started), timeout=5)
        log.append(("inputs", step.id, inputs))
        return copy.deepcopy(outputs[step.id])

    async def run_tests(step, inputs):
        log.append(("start", step.id, None))
        log.append(("inputs", step.id, inputs))
        return copy.deepcopy(outputs[step.id])

    return agent, {"pytest.run": run_tests}


async def _both(started):
    while not {"scan", "review"} <= started.keys():
        await asyncio.sleep(0)


_GOOD = {"scan": {"findings": ["a"]}, "review": {"notes": "n"}, "build": {"patch": "p"},
         "test": {"passed": True}, "verdict": {"accept": True}}


@pytest.mark.asyncio
async def test_independent_steps_start_without_a_barrier_and_dependents_get_their_handoff():
    compiled = D.compile_definition(D.parse_definition(build_capability()))
    log: list = []
    agent, executors = _runtime(log, _GOOD, gate=asyncio.Event())
    outcome = await D.run_definition(compiled, agent=agent, executors=executors)
    assert {sid: o.state for sid, o in outcome.items()} == {
        "scan": "DONE", "review": "DONE", "build": "DONE", "test": "DONE", "verdict": "DONE"}
    starts = [e[1] for e in log if e[0] == "start"]
    assert set(starts[:2]) == {"scan", "review"}        # both before anything dependent
    assert starts[2:] == ["build", "test", "verdict"]
    handed = {e[1]: e[2] for e in log if e[0] == "inputs"}
    assert handed["build"] == {"scan": {"findings": ["a"]}, "review": {"notes": "n"}}
    assert handed["test"] == {"build": {"patch": "p"}}      # only the declared fields
    assert handed["verdict"] == {"test": {"passed": True}}


@pytest.mark.asyncio
async def test_a_dependent_step_does_not_start_when_its_handoff_does_not_validate():
    raw = build_capability()
    del _step(raw, "scan")["outputs"]["required"]            # `findings` is optional...
    compiled = D.compile_definition(D.parse_definition(raw))
    bad = dict(_GOOD, scan={})                               # ...and scan omits it
    log: list = []
    agent, executors = _runtime(log, bad)
    outcome = await D.run_definition(compiled, agent=agent, executors=executors)
    assert outcome["scan"].state == "DONE"
    assert outcome["review"].state == "DONE"                 # independent work finished
    assert outcome["build"].state == "REFUSED"
    assert outcome["build"].field == "steps[2].inputs[0].fields[0]"
    assert outcome["test"].state == outcome["verdict"].state == "BLOCKED"
    assert [e[1] for e in log if e[0] == "start"] == ["scan", "review"] or \
        {e[1] for e in log if e[0] == "start"} == {"scan", "review"}


@pytest.mark.asyncio
async def test_a_missing_handoff_field_refuses_the_dependent():
    raw = build_capability()
    del _step(raw, "build")["outputs"]["required"]           # `patch` is optional...
    compiled = D.compile_definition(D.parse_definition(raw))
    agent, executors = _runtime([], dict(_GOOD, build={"other": 1}))
    outcome = await D.run_definition(compiled, agent=agent, executors=executors)
    assert outcome["build"].state == "DONE"      # its own output is not its handoff...
    assert outcome["test"].state == "REFUSED"    # ...the consumer's check refuses it
    assert outcome["test"].field == "steps[3].inputs[0].fields[0]"
    assert outcome["verdict"].state == "BLOCKED"


@pytest.mark.asyncio
async def test_a_deterministic_step_without_a_registered_executor_is_refused_up_front():
    compiled = D.compile_definition(D.parse_definition(build_capability()))
    agent, _ = _runtime([], _GOOD)
    with pytest.raises(D.DefinitionError) as caught:
        await D.run_definition(compiled, agent=agent, executors={})
    assert caught.value.field == "steps[3].executor"


# --- versions -----------------------------------------------------------------------

def test_a_bumped_version_keeps_the_old_version_runnable():
    registry = D.DefinitionRegistry()
    registry.register(D.parse_definition(build_capability(1)))
    v2 = build_capability(2)
    _step(v2, "scan")["cap"] = 8000
    registry.register(D.parse_definition(v2))
    assert registry.versions("friday.build_capability") == (1, 2)
    old = D.compile_definition(registry.get("friday.build_capability", 1))
    new = D.compile_definition(registry.get("friday.build_capability"))      # latest
    assert old.spec.id == "friday.build_capability@v1" and old.spec.stages[0].cap == 4000
    assert new.spec.id == "friday.build_capability@v2" and new.spec.stages[0].cap == 8000


def test_registering_the_same_version_with_other_content_is_refused_but_a_repeat_is_not():
    registry = D.DefinitionRegistry()
    registry.register(D.parse_definition(build_capability(1)))
    registry.register(D.parse_definition(build_capability(1)))              # idempotent
    changed = build_capability(1)
    _step(changed, "scan")["cap"] = 1
    with pytest.raises(D.DefinitionError) as caught:
        registry.register(D.parse_definition(changed))
    assert caught.value.field == "version"
    with pytest.raises(KeyError):
        registry.get("friday.build_capability", 9)


# --- role directives ----------------------------------------------------------------

_ROUTES = {"economy": ["m-small"], "standard": ["m-mid-a", "m-mid-b"], "deep": ["m-big"]}
_AVAILABLE = frozenset({"m-small", "m-mid-a", "m-mid-b", "m-big"})


def _resolve(directives, available=_AVAILABLE, bench=None):
    definition = D.parse_definition(build_capability())
    return {r.step: r for r in D.resolve_roles(
        definition, directives, routes=_ROUTES, available=available, bench=bench or {})}


def test_a_role_pin_is_honoured_and_recorded_with_its_reason():
    got = _resolve({"builder": {"pin": "m-mid-b"}})
    build = got["build"]
    assert (build.status, build.model) == ("PINNED", "m-mid-b")
    assert "builder" in build.reason and "m-mid-b" in build.reason
    assert build.to_dict()["role"] == "builder" and build.to_dict()["model"] == "m-mid-b"
    assert got["verdict"].status == "CHOSEN" and got["verdict"].model == "m-big"


def test_a_pin_to_an_unavailable_route_reports_unavailable_and_does_not_substitute():
    got = _resolve({"builder": {"pin": "m-gone"}})
    build = got["build"]
    assert build.status == "UNAVAILABLE" and build.model is None
    assert "m-gone" in build.reason
    # an unpinned role next to it is untouched
    assert got["scan"].model == "m-small"


def test_an_exclusion_removes_a_model_and_the_measured_bench_picks_among_the_rest():
    bench = {"m-mid-a": {"n": 5, "wins": 5, "cost": 9.0}, "m-mid-b": {"n": 5, "wins": 5, "cost": 2.0}}
    assert _resolve({}, bench=bench)["build"].model == "m-mid-b"
    got = _resolve({"builder": {"exclude": ["m-mid-b"]}}, bench=bench)
    assert got["build"].model == "m-mid-a" and got["build"].status == "CHOSEN"
    none_left = _resolve({"builder": {"exclude": ["m-mid-a", "m-mid-b"]}})
    assert none_left["build"].status == "UNAVAILABLE" and none_left["build"].model is None


def test_a_deterministic_step_resolves_to_no_model():
    got = _resolve({})["test"]
    assert got.status == "DETERMINISTIC" and got.model is None


def test_a_directive_that_pins_and_excludes_the_same_model_is_refused():
    with pytest.raises(D.DirectiveError) as caught:
        _resolve({"builder": {"pin": "m-mid-a", "exclude": ["m-mid-a"]}})
    assert caught.value.field == "directives.builder"
    with pytest.raises(D.DirectiveError) as caught:
        _resolve({"builder": {"pinn": "m-mid-a"}})
    assert caught.value.field == "directives.builder.pinn"


@pytest.mark.asyncio
async def test_an_unavailable_step_is_not_started_and_blocks_its_dependents():
    compiled = D.compile_definition(D.parse_definition(build_capability()))
    resolutions = {r.step: r for r in D.resolve_roles(
        compiled.definition, {"builder": {"pin": "m-gone"}}, routes=_ROUTES,
        available=_AVAILABLE, bench={})}
    log: list = []
    agent, executors = _runtime(log, _GOOD)
    outcome = await D.run_definition(compiled, agent=agent, executors=executors,
                                     resolutions=resolutions)
    assert outcome["build"].state == "UNAVAILABLE"
    assert outcome["test"].state == outcome["verdict"].state == "BLOCKED"
    assert "build" not in [e[1] for e in log if e[0] == "start"]
    models = {e[1]: e[2] for e in log if e[0] == "start"}
    assert models["scan"] == "m-small"                 # the recorded choice reaches the agent


# --- review repairs --------------------------------------------------------------------

@pytest.mark.parametrize("name", ["kimi-k2", "minimax-m2", "codex", "o3-mini", "glm-4",
                                  "gemma-2", "gpt-5.5", "Sonnet", "o1"])
def test_a_model_the_fleet_routes_to_is_refused_wherever_a_step_names_it(name):
    raw = build_capability()
    _step(raw, "build")["template"] = name
    with pytest.raises(D.ModelNameError) as caught:
        D.parse_definition(raw)
    assert caught.value.field == "steps[2].template"


def test_every_model_in_the_declared_catalogue_is_refused():
    from friday import model_capabilities
    for _backend, model_id in model_capabilities.CAPABILITIES:
        assert D.MODEL_NAME.search(model_id), model_id


@pytest.mark.parametrize("name", ["zorblax-7-turbo", "Zorblax"])
def test_a_route_of_a_new_family_is_refused_once_it_is_profiled(name):
    """A family in neither the catalogue nor the static list becomes routable
    when MODEL-004 profiles it; from then on a step may not name it."""
    from friday import model_capabilities as MC
    raw = build_capability()
    _step(raw, "build")["template"] = name
    D.parse_definition(copy.deepcopy(raw))          # not yet a route FRIDAY can reach
    MC.record_profile(MC.ModelCapabilities(
        model_id="vendorx/zorblax-7-turbo", provider="vendorx", backend="openrouter",
        vision=False, tools=False, structured_output=False, realtime_audio=False,
        max_context=None, relative_latency="medium", relative_cost="unknown"),
        [], profiled_at="2026-10-13T00:00:00Z")
    with pytest.raises(D.ModelNameError) as caught:
        D.parse_definition(raw)
    assert caught.value.field == "steps[2].template"


def _profile(model_id: str, provider: str) -> None:
    from friday import model_capabilities as MC
    MC.record_profile(MC.ModelCapabilities(
        model_id=model_id, provider=provider, backend="openrouter",
        vision=False, tools=False, structured_output=False, realtime_audio=False,
        max_context=None, relative_latency="medium", relative_cost="unknown"),
        [], profiled_at="2026-10-13T00:00:00Z")


@pytest.mark.parametrize("named", ["llama-4-scout", "meta-llama/llama-4-scout",
                                   "sonar-deep-research", "Sonar"])
def test_profiling_a_real_route_does_not_turn_role_or_tier_words_into_models(named):
    """Onboarding an ordinary route (its id tail holds `scout`, `deep`,
    `research`) must not make the canonical definition unparseable; the
    route itself, and its leading family word, are still refused."""
    _profile("meta-llama/llama-4-scout", "meta-llama")
    _profile("perplexity/sonar-deep-research", "perplexity")
    D.parse_definition(build_capability())              # role scout, tier deep
    raw = build_capability()
    _step(raw, "review")["role"] = "research"
    _step(raw, "scan")["template"] = "lookup"
    D.parse_definition(copy.deepcopy(raw))
    _step(raw, "build")["template"] = named
    with pytest.raises(D.ModelNameError) as caught:
        D.parse_definition(raw)
    assert caught.value.field == "steps[2].template"


@pytest.mark.parametrize("word", ["economy", "standard", "deep", "scout", "builder",
                                  "pytest.run", "lookup", "check", "judge", "protocol"])
def test_role_tier_and_executor_words_are_not_taken_for_models(word):
    assert not D.MODEL_NAME.search(word)


def test_the_flat_spec_is_not_runnable_on_the_durable_runner_and_says_so():
    from friday.ultracode import runner
    compiled = D.compile_definition(D.parse_definition(build_capability()))
    segments = [[s.id for s in seg] for seg in runner._segments(compiled.spec)]
    # the limitation, exactly: roots are chained in one pipeline, and the
    # deterministic `test` stage sits in a pipeline the runner gives to a model
    assert segments == [["scan", "review"], ["build", "test"], ["verdict"]]
    assert compiled.spec.durable_runner_safe is False
    with pytest.raises(D.DefinitionError) as caught:
        compiled.require_durable_runner_safe()
    assert caught.value.field == "spec" and "run_definition" in str(caught.value)


def test_a_directive_for_a_role_no_step_has_is_refused_not_ignored():
    with pytest.raises(D.DirectiveError) as caught:
        _resolve({"buidler": {"pin": "m-mid-a"}})        # typo of "builder"
    assert caught.value.field == "directives.buidler"
    assert "builder" in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [{"patch": 7}, {}, {"nope": "x"}])
async def test_a_step_result_that_violates_its_outputs_schema_is_not_done(bad):
    compiled = D.compile_definition(D.parse_definition(build_capability()))
    log: list = []
    agent, executors = _runtime(log, {**_GOOD, "build": bad})
    outcome = await D.run_definition(compiled, agent=agent, executors=executors)
    assert outcome["build"].state == "INVALID"
    assert outcome["build"].field == "steps[2].outputs"
    assert outcome["test"].state == outcome["verdict"].state == "BLOCKED"
    assert "test" not in [e[1] for e in log if e[0] == "start"]


@pytest.mark.asyncio
async def test_a_terminal_step_result_is_checked_against_its_schema_too():
    compiled = D.compile_definition(D.parse_definition(build_capability()))
    agent, executors = _runtime([], {**_GOOD, "verdict": {"accept": "yes"}})
    outcome = await D.run_definition(compiled, agent=agent, executors=executors)
    assert outcome["verdict"].state == "INVALID"
    assert outcome["verdict"].field == "steps[4].outputs"


# --- the durable runner refuses a flattened DAG (UCW-002 gap 1) --------------------

def test_start_run_refuses_a_compiled_definition_spec_and_creates_no_run():
    from friday.contracts import PERSON
    from friday.store import Store
    from friday.ultracode import runner as R

    store = Store(":memory:")
    compiled = D.compile_definition(D.parse_definition(json.dumps(build_capability())))
    with pytest.raises(R.NotDurableRunnerSafe) as caught:
        R.start_run(store, compiled.spec, ["a"], provenance=PERSON, attended=True)
    assert caught.value.spec_id == "friday.build_capability@v1"
    assert "depends_on" in str(caught.value) and "deterministic" in str(caught.value)
    assert isinstance(caught.value, ValueError)
    assert store.objective_runs(limit=10) == []          # refused before any run exists


def test_start_run_still_admits_a_plain_ultra001_spec():
    from friday.contracts import PERSON
    from friday.store import Store
    from friday.ultracode import runner as R

    store = Store(":memory:")
    spec = S.parse_spec({"id": "plain", "budget_tokens": 1000, "stages": [
        {"id": "s1", "template": "lookup", "barrier": False, "schema": _NOTES,
         "tier": "economy", "cap": 10}]})
    run = R.start_run(store, spec, ["a"], provenance=PERSON, attended=True)
    assert run["run_id"]
