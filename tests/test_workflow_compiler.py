"""UCW-009: the Workflow Compiler turns repeated successful agent trajectories into a
FRIDAY-native definition only when a replay proves the same verified outcome at a
measured lower cost, and falls back to the agent path, marking the compiled form for
revalidation, when it diverges at run time."""
from __future__ import annotations

import asyncio

import pytest

from friday.ultracode import compiler as K
from friday.ultracode import definition as D

CLASS = "invoice.summary"
STEPS = (("worker", "fetch_invoice"), ("worker", "compute_total"), ("worker", "post_summary"))


def event(n: int) -> dict:
    return {"invoice": n}


def results(ev: dict) -> list[dict]:
    n = ev["invoice"]
    return [{"lines": [n, n + 1]}, {"total": 2 * n + 1}, {"summary": f"invoice {n}: {2 * n + 1}"}]


def trajectory(n: int, *, tokens: int = 1000, succeeded: bool = True, steps=STEPS,
               name: str = "") -> K.Trajectory:
    ev = event(n)
    return K.Trajectory(
        id=name or f"t{n}", task_class=CLASS, event=ev, succeeded=succeeded, tokens=tokens,
        steps=tuple(K.TrajectoryStep(role, executor, result)
                    for (role, executor), result in zip(steps, results(ev))))


class Rig:
    """Executors that reproduce the recorded results and charge a cheap, fixed cost."""

    def __init__(self, cost: int = 10, bad_step: str = "", boom: str = "") -> None:
        self.cost, self.bad_step, self.boom = cost, bad_step, boom
        self.spent = 0
        self.calls: list[str] = []

    def meter(self) -> int:
        return self.spent

    def executors_for(self, ev: dict) -> dict:
        recorded = dict(zip((e for _, e in STEPS), results(ev)))

        def make(name: str):
            async def run(step: D.Step, handed: dict) -> dict:
                self.calls.append(name)
                self.spent += self.cost
                if name == self.boom:
                    raise RuntimeError("upstream down")
                if name == self.bad_step:
                    return {"unexpected": True}
                return recorded[name]
            return run
        return {name: make(name) for name in recorded}


def same_as_expected(ev: dict, outcome: dict) -> bool:
    return outcome == results(ev)[-1]


def compile_with(rig: Rig, trajectories, *, policy=None, verify=same_as_expected,
                 compiler: K.WorkflowCompiler | None = None):
    compiler = compiler or K.WorkflowCompiler(policy=policy or K.Policy(mode=K.COMPILE))
    promotion = asyncio.run(compiler.compile(
        CLASS, trajectories, executors_for=rig.executors_for, verify=verify, meter=rig.meter))
    return compiler, promotion


def three(**kw):
    return [trajectory(n, **kw) for n in (1, 2, 3)]


def test_three_successes_compile_to_a_definition_that_replays_cheaper():
    rig = Rig()
    compiler, promotion = compile_with(rig, three())
    assert promotion.status == K.PROMOTED
    assert promotion.source_trajectories == ("t1", "t2", "t3")
    assert promotion.baseline_tokens == 3000
    assert promotion.replay_tokens == 90 < promotion.baseline_tokens
    assert 0.9 < promotion.savings < 1
    definition = compiler.registry.get(promotion.workflow_id, promotion.version)
    assert D.parse_definition(definition.to_dict()) == definition
    assert [s.executor for s in definition.steps] == [e for _, e in STEPS]
    assert all(s.kind == "deterministic" for s in definition.steps)
    assert definition.steps[2].depends_on == ("step2",)
    assert definition.steps[2].outputs["properties"]["summary"] == {"type": "string"}
    assert definition.steps[1].outputs["properties"]["total"] == {"type": "integer"}
    D.compile_definition(definition)
    assert compiler.promotions == [promotion]
    assert promotion.to_dict()["source_trajectories"] == ["t1", "t2", "t3"]


def test_a_proposing_policy_records_the_savings_but_registers_nothing():
    compiler, promotion = compile_with(Rig(), three(), policy=K.Policy(mode=K.PROPOSE))
    assert promotion.status == K.PROPOSED
    assert promotion.definition is not None and promotion.replay_tokens < promotion.baseline_tokens
    with pytest.raises(KeyError):
        compiler.registry.get(promotion.workflow_id)
    assert not compiler.is_active(promotion.workflow_id)


def test_a_policy_that_is_off_does_not_even_replay():
    rig = Rig()
    compiler, promotion = compile_with(rig, three(), policy=K.Policy(mode=K.OFF))
    assert promotion.status == K.REFUSED_BY_POLICY
    assert rig.calls == []
    assert promotion.source_trajectories == ("t1", "t2", "t3")


def test_a_candidate_that_is_cheaper_but_fails_the_outcome_check_is_not_promoted():
    rig = Rig(bad_step="post_summary")
    compiler, promotion = compile_with(rig, three())
    assert promotion.status == K.OUTCOME_MISMATCH
    assert promotion.replay_tokens < promotion.baseline_tokens
    with pytest.raises(KeyError):
        compiler.registry.get(promotion.workflow_id)


def test_an_independent_check_that_rejects_the_replayed_outcome_blocks_promotion():
    compiler, promotion = compile_with(Rig(), three(), verify=lambda ev, outcome: False)
    assert promotion.status == K.OUTCOME_MISMATCH
    assert "outcome check" in promotion.reason
    assert not compiler.is_active(promotion.workflow_id)


def test_a_replay_that_is_not_cheaper_is_not_promoted():
    compiler, promotion = compile_with(Rig(cost=1000), three(tokens=1000))
    assert promotion.status == K.NOT_CHEAPER
    assert promotion.replay_tokens >= promotion.baseline_tokens
    with pytest.raises(KeyError):
        compiler.registry.get(promotion.workflow_id)


def test_the_policy_can_demand_a_larger_saving():
    policy = K.Policy(mode=K.COMPILE, min_savings=0.99)
    compiler, promotion = compile_with(Rig(), three(), policy=policy)
    assert promotion.status == K.NOT_CHEAPER
    assert "0.99" in promotion.reason


def test_fewer_than_the_required_successes_is_not_compiled():
    rig = Rig()
    compiler, promotion = compile_with(rig, [trajectory(1), trajectory(2),
                                             trajectory(3, succeeded=False)])
    assert promotion.status == K.INSUFFICIENT
    assert "2 of 3" in promotion.reason
    assert rig.calls == []
    assert promotion.definition is None


def test_the_required_number_follows_the_policy():
    compiler, promotion = compile_with(
        Rig(), three() + [trajectory(4), trajectory(5)], policy=K.Policy(mode=K.COMPILE, min_successes=5))
    assert promotion.status == K.PROMOTED
    assert len(promotion.source_trajectories) == 5


def test_trajectories_that_took_different_routes_are_not_one_procedure():
    other = (("worker", "fetch_invoice"), ("worker", "post_summary"), ("worker", "compute_total"))
    compiler, promotion = compile_with(Rig(), [trajectory(1), trajectory(2),
                                               trajectory(3, steps=other)])
    assert promotion.status == K.DIVERGENT_TRAJECTORIES
    assert promotion.definition is None


def test_a_step_that_needed_a_model_cannot_be_compiled_to_a_deterministic_workflow():
    steps = (("worker", "fetch_invoice"), ("worker", ""), ("worker", "post_summary"))
    compiler, promotion = compile_with(Rig(), three(steps=steps))
    assert promotion.status == K.NOT_COMPILABLE
    assert "step 2" in promotion.reason


def test_a_trajectory_of_another_task_class_is_refused():
    odd = K.Trajectory(id="x", task_class="other", event={}, succeeded=True, tokens=1,
                       steps=trajectory(1).steps)
    with pytest.raises(ValueError, match="task class"):
        compile_with(Rig(), three() + [odd])


def test_a_policy_is_validated():
    with pytest.raises(ValueError):
        K.Policy(mode="sometimes")
    with pytest.raises(ValueError):
        K.Policy(min_successes=0)
    with pytest.raises(ValueError):
        K.Policy(min_savings=1.0)


def run_compiled(compiler, rig, workflow_id, ev, agent_calls, verify=same_as_expected):
    async def agent_path(e: dict) -> dict:
        agent_calls.append(e)
        return results(e)[-1]
    return asyncio.run(compiler.run(workflow_id, ev, executors_for=rig.executors_for,
                                    verify=verify, agent_path=agent_path))


def test_a_run_that_matches_stays_on_the_compiled_path():
    rig = Rig()
    compiler, promotion = compile_with(rig, three())
    rig.calls.clear()
    agent_calls: list = []
    ran = run_compiled(compiler, rig, promotion.workflow_id, event(7), agent_calls)
    assert (ran.path, ran.fell_back) == (K.COMPILED_PATH, False)
    assert ran.outcome == results(event(7))[-1]
    assert agent_calls == [] and rig.calls == [e for _, e in STEPS]
    assert compiler.is_active(promotion.workflow_id)


def test_a_compiled_run_that_diverges_falls_back_and_is_marked_for_revalidation():
    good = Rig()
    compiler, promotion = compile_with(good, three())
    drifted = Rig(bad_step="compute_total")
    agent_calls: list = []
    ran = run_compiled(compiler, drifted, promotion.workflow_id, event(7), agent_calls)
    assert (ran.path, ran.fell_back) == (K.AGENT_PATH, True)
    assert ran.outcome == results(event(7))[-1] and agent_calls == [event(7)]
    assert "step2" in ran.reason
    assert compiler.needs_revalidation(promotion.workflow_id)
    assert not compiler.is_active(promotion.workflow_id)
    assert compiler.divergences[-1]["workflow_id"] == promotion.workflow_id
    # While it waits for revalidation the compiled form is not used at all.
    drifted.calls.clear()
    again = run_compiled(compiler, drifted, promotion.workflow_id, event(8), agent_calls)
    assert again.path == K.AGENT_PATH and drifted.calls == []
    assert "revalidation" in again.reason


def test_a_compiled_outcome_the_check_rejects_falls_back_too():
    rig = Rig()
    compiler, promotion = compile_with(rig, three())
    agent_calls: list = []
    ran = run_compiled(compiler, rig, promotion.workflow_id, event(7), agent_calls,
                       verify=lambda ev, outcome: False)
    assert ran.fell_back and agent_calls == [event(7)]
    assert "outcome check" in ran.reason
    assert compiler.needs_revalidation(promotion.workflow_id)


def test_an_executor_error_still_reaches_the_caller_and_is_not_swallowed():
    compiler, promotion = compile_with(Rig(), three())
    agent_calls: list = []
    with pytest.raises(RuntimeError, match="upstream down"):
        run_compiled(compiler, Rig(boom="compute_total"), promotion.workflow_id, event(7),
                     agent_calls)
    assert agent_calls == []


def test_revalidation_by_a_new_replay_issues_the_next_version_and_reactivates():
    rig = Rig()
    compiler, first = compile_with(rig, three())
    run_compiled(compiler, Rig(bad_step="compute_total"), first.workflow_id, event(7), [])
    assert compiler.needs_revalidation(first.workflow_id)
    _, second = compile_with(rig, three(), compiler=compiler)
    assert (second.status, second.version) == (K.PROMOTED, first.version + 1)
    assert second.workflow_id == first.workflow_id
    assert compiler.is_active(first.workflow_id) and not compiler.needs_revalidation(first.workflow_id)
    assert compiler.registry.versions(first.workflow_id) == (first.version, second.version)


def test_a_workflow_that_was_never_promoted_runs_on_the_agent_path():
    compiler = K.WorkflowCompiler()
    agent_calls: list = []
    ran = run_compiled(compiler, Rig(), "compiled.unknown", event(1), agent_calls)
    assert ran.path == K.AGENT_PATH and ran.fell_back is False
    assert agent_calls == [event(1)]


def test_one_trajectory_submitted_three_times_is_not_three_successes():
    rig = Rig()
    t = trajectory(1)
    with pytest.raises(ValueError, match="t1"):
        compile_with(rig, [t, t, t])
    assert rig.calls == []


def test_two_different_runs_that_share_an_id_are_refused():
    with pytest.raises(ValueError, match="duplicate"):
        compile_with(Rig(), [trajectory(1), trajectory(2, name="t1"), trajectory(3)])


def test_a_meter_that_goes_backwards_is_not_a_saving():
    rig = Rig()
    rig.spent = 10_000

    def backwards() -> int:
        rig.spent -= 500
        return rig.spent
    compiler = K.WorkflowCompiler(policy=K.Policy(mode=K.COMPILE))
    with pytest.raises(ValueError, match="meter"):
        asyncio.run(compiler.compile(CLASS, three(), executors_for=rig.executors_for,
                                     verify=same_as_expected, meter=backwards))
    assert compiler.registry.versions(f"compiled.{CLASS}") == ()


def test_a_replay_error_is_recorded_and_still_propagates():
    compiler = K.WorkflowCompiler(policy=K.Policy(mode=K.COMPILE))
    rig = Rig(boom="compute_total")
    with pytest.raises(RuntimeError, match="upstream down"):
        asyncio.run(compiler.compile(CLASS, three(), executors_for=rig.executors_for,
                                     verify=same_as_expected, meter=rig.meter))
    assert [p.status for p in compiler.promotions] == [K.REPLAY_ERROR]
    assert "upstream down" in compiler.promotions[0].reason
    assert not compiler.is_active(f"compiled.{CLASS}")
