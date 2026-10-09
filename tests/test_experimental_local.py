"""LAB-001 slice 1: the task fence for an EXPERIMENTAL_LOCAL_MODEL route.

Pure Python: the model is a fake callable, the pool is the real ExpertPool over
a simulated machine (see tests/test_local_experts.py). No weights, no network.
"""
from __future__ import annotations

import pytest

from friday import data_class as DC
from friday import experimental_local as X
from friday import local_experts as L
from tests.test_local_experts import build

SCHEMA = {"type": "object", "required": ["team"],
          "properties": {"team": {"type": "string", "enum": ["coding", "research"]},
                         "confidence": {"type": "number"}}}


class Fake:
    def __init__(self, reply: str = '{"team": "coding"}') -> None:
        self.reply, self.calls = reply, []

    def __call__(self, task: str) -> str:
        self.calls.append(task)
        return self.reply


@pytest.mark.parametrize("task", ["task_category", "team_activation",
                                  "instruction_to_schema", "agent_state_transition"])
def test_each_narrow_task_is_admitted_and_reaches_the_model(task):
    assert X.admit(task, DC.PUBLIC).admitted
    fake = Fake()
    assert X.run(task, DC.PUBLIC, fake, SCHEMA) == {"team": "coding"}
    assert fake.calls == [task]


@pytest.mark.parametrize("task", ["development", "architecture", "deep_research",
                                  "conversation", "approval", "", "Task_Category"])
def test_any_other_task_is_refused_before_the_model_is_called(task):
    verdict = X.admit(task, DC.PUBLIC)
    assert not verdict.admitted and verdict.reason
    fake = Fake()
    with pytest.raises(X.TaskRefused) as err:
        X.run(task, DC.PUBLIC, fake, SCHEMA)
    assert err.value.verdict.task == task
    assert fake.calls == []


def test_the_refusal_names_the_task():
    assert "conversation" in X.admit("conversation", DC.PUBLIC).reason


def test_secret_and_local_only_data_is_admitted_only_for_a_local_route():
    for cls in (DC.SECRET, DC.LOCAL_ONLY):
        assert X.admit("task_category", cls).admitted
        assert not X.admit("task_category", cls, local=False).admitted
    assert X.admit("task_category", DC.PUBLIC, local=False).admitted


def test_an_unknown_data_class_is_refused():
    assert not X.admit("task_category", "TOP_SECRET").admitted


def test_admit_never_raises_on_odd_input():
    for task, cls in ((None, DC.PUBLIC), (3, None), ("task_category", None)):
        assert not X.admit(task, cls).admitted


def test_invalid_json_is_rejected_so_the_caller_can_fall_back():
    for reply in ("not json", "[1]", '{"team": "legal"}', '{}',
                  '{"team": "coding", "confidence": "high"}'):
        with pytest.raises(X.OutputRejected):
            X.run("team_activation", DC.PUBLIC, Fake(reply), SCHEMA)


def test_a_model_exception_is_not_swallowed():
    def boom(task):
        raise RuntimeError("server down")
    with pytest.raises(RuntimeError):
        X.run("task_category", DC.PUBLIC, boom, SCHEMA)


def test_register_minimind_registers_a_general_expert_resident_only_in_the_pool():
    machine, _gov, pool = build(names=())
    verdict = X.register_minimind(pool, machine.loader(X.EXPERT_NAME, 0.2),
                                  machine.unloader(X.EXPERT_NAME), 0.2)
    assert verdict.accepted
    assert pool.status()["registered"] == [X.EXPERT_NAME]
    assert pool.resident() == [] and machine.loads == []   # registered, not resident
    outcome = pool.load(X.EXPERT_NAME)
    assert outcome.loaded and machine.loads == [X.EXPERT_NAME]
    assert pool.status()["registered"] and pool.resident()[0]["kind"] == "general"
    pool.close()


def test_an_artifact_over_the_2gb_policy_is_refused_at_registration():
    machine, _gov, pool = build(names=())
    with pytest.raises(L.OnboardingRefused):
        X.register_minimind(pool, machine.loader(X.EXPERT_NAME, 3.0),
                            machine.unloader(X.EXPERT_NAME), 3.0)
    assert pool.status()["registered"] == []
    pool.close()
