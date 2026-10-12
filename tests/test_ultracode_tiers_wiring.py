"""ULTRA-006: the tier choice reaches the runner. `choose_models` picks a model
per stage of a spec, `start_run` logs each choice with its evidence rows on the
run, and `model_for` is the `ModelFor` the stage port dispatches with."""
from __future__ import annotations

from pathlib import Path

import pytest

from friday.contracts import PERSON
from friday.store import Store
from friday.ultracode import runner as R
from friday.ultracode import spec as S
from friday.ultracode import tiers as T

DEFAULT = Path(__file__).resolve().parent.parent / "docs/plans/ultracode/workflows/default.json"

CANDIDATES = {"economy": ["haiku-4", "haiku-3"], "standard": ["sonnet-5"], "deep": ["opus-5"]}
BENCH = {"haiku-4": {"n": 4, "wins": 4, "cost": 2.0},
         "haiku-3": {"n": 4, "wins": 4, "cost": 0.8}}


class OpenGate:
    def admit(self, run, stage, arguments) -> str:
        return ""

    def release(self, lease: str) -> None:
        pass

    def settle(self, run, stage, arguments) -> None:
        pass


class RecordingAgent:
    def __init__(self) -> None:
        self.models: dict[str, str] = {}

    async def __call__(self, stage, inputs, model):
        self.models[stage.id] = model
        return {"ok": True, "findings": [], "files": [], "tests_pass": True, "passed": True,
                "objections": [], "verdict": "accept"}


def test_choose_models_gives_every_stage_its_tiers_cheapest_capable_model():
    spec = S.load_spec(DEFAULT)
    choices = T.choose_models(spec, CANDIDATES, BENCH)
    assert {sid: c.model for sid, c in choices.items()} == {
        "scan": "haiku-3", "build": "sonnet-5", "check": "haiku-3",
        "doubt": "haiku-3", "verdict": "opus-5"}
    assert {sid: c.tier for sid, c in choices.items()} == T.resolve_tiers(spec)


def test_a_tier_with_no_candidate_is_an_error_not_a_guess():
    spec = S.load_spec(DEFAULT)
    with pytest.raises(ValueError, match="economy"):
        T.choose_models(spec, {"standard": ["sonnet-5"], "deep": ["opus-5"]}, BENCH)


def test_start_run_logs_each_choice_with_the_rows_it_rests_on():
    store = Store(":memory:")
    spec = S.load_spec(DEFAULT)
    choices = T.choose_models(spec, CANDIDATES, BENCH)
    run = R.start_run(store, spec, ["a"], provenance=PERSON, attended=True, choices=choices)
    (event,) = store.objective_events_named(run["run_id"], (R.EVENT_MODELS_CHOSEN,))
    rows = {row["stage"]: row for row in event["detail"]["stages"]}
    assert list(rows) == [stage.id for stage in spec.stages]
    assert rows["scan"]["model"] == "haiku-3" and rows["scan"]["tier"] == "economy"
    assert {e["model"]: e["cost_per_win"] for e in rows["scan"]["evidence"]} == {
        "haiku-3": pytest.approx(0.2), "haiku-4": pytest.approx(0.5)}
    assert rows["build"]["evidence"] == []
    assert "no measured" in rows["build"]["reason"]


def test_start_run_refuses_choices_that_leave_a_stage_out():
    store = Store(":memory:")
    spec = S.load_spec(DEFAULT)
    choices = T.choose_models(spec, CANDIDATES, BENCH)
    del choices["verdict"]
    with pytest.raises(ValueError, match="verdict"):
        R.start_run(store, spec, ["a"], provenance=PERSON, attended=True, choices=choices)
    assert store.objective_runs() == []


@pytest.mark.asyncio
async def test_the_port_dispatches_every_stage_with_its_chosen_model():
    store = Store(":memory:")
    spec = S.load_spec(DEFAULT)
    choices = T.choose_models(spec, CANDIDATES, BENCH)
    agent = RecordingAgent()
    run = R.start_run(store, spec, ["a"], provenance=PERSON, attended=True, choices=choices)
    port = R.StagePort(spec, agent, R.ResultCache(store, "ws"), R.model_for(choices), OpenGate())
    first, second = store.objective_tasks(run["run_id"])
    done = await port(R.CAPABILITY, first["arguments"], run)
    await port(R.CAPABILITY, {**second["arguments"], "inputs": [done["key"]]}, run)
    assert agent.models == {"scan": "haiku-3", "build": "sonnet-5", "check": "haiku-3",
                            "doubt": "haiku-3", "verdict": "opus-5"}
