"""
EXP-001: a bounded experiment buys cheaper evidence than a speculative loop.

The load-bearing test here is the benchmark
(`test_the_cheap_evidence_route_beats_the_speculative_loop`): the same
question, the same priced measurements and the same world, run once through
the experiment engine and once through the retry shape it replaces. The rest
holds the pieces the benchmark depends on - the ladder really is sized by
`execution_economics.verification_depth`, the loop really does stop when the
evidence stops arriving, and the gate in `continuous.py` really does park a
run that has climbed its ladder.
"""
from __future__ import annotations

import asyncio

import pytest

from friday import execution_economics as ee
from friday import experiment_engine as X
from friday import objectives as O
from friday.continuous import ContinuousTaskExecutor
from friday.objectives import compile_objective
from friday.store import Store
from friday.contracts import PERSON


# --- the two worlds the benchmark runs in ----------------------------------

#: The hypothesis is true: every depth of evidence comes back supporting it.
def world_where_the_hypothesis_holds(measurement):
    return True, "passed"


#: The hypothesis is false, and the system says so the same way every time -
#: which is what makes a loop with no competing hypothesis keep going.
def world_where_the_hypothesis_is_wrong(measurement):
    return False, "AssertionError: expected the gate to deny, it allowed"


def an_experiment():
    """One real question, sized from a real task description."""
    return X.for_task(
        "fix the retry path in friday/continuous.py so a stuck task stops",
        hypothesis="the retry guard never fires",
        competing_hypothesis="the verifier reports the wrong outcome",
        variables=("the task's fingerprint", "the worker's model tier"))


# --- the acceptance criterion ---------------------------------------------

@pytest.mark.parametrize("world,engine_status", [
    (world_where_the_hypothesis_holds, X.RESOLVED),
    (world_where_the_hypothesis_is_wrong, X.REFUTED),
])
def test_the_cheap_evidence_route_beats_the_speculative_loop(world,
                                                             engine_status):
    """EXP-001's acceptance, as a comparative benchmark.

    Both routes face the same world and pay the same published prices for
    the same measurements. The engine wins on cost because it buys the
    cheapest discriminating rung its risk justifies, and it wins on result
    because it can conclude the hypothesis was wrong instead of retrying.
    """
    experiment = an_experiment()
    engine = experiment.run(world)
    loop = X.speculative_loop(experiment, world)

    # The engine settles the question, either way, inside its budget.
    assert engine.status == engine_status, engine.reason
    assert engine.decided, engine.reason
    assert engine.cost <= experiment.budget

    # Cheaper in evidence spend in both worlds - which is the unit that is
    # actually billed, a test run being the expensive part of an attempt.
    assert engine.cost < loop.cost, (
        f"{engine.cost} evidence units vs the loop's {loop.cost}")

    # And never a worse result: in the world where the hypothesis holds both
    # routes reach it, and in the world where it does not only the engine
    # reaches anything at all.
    if engine_status == X.RESOLVED:
        assert loop.conclusion == engine.conclusion == experiment.hypothesis
        # It gets there in more calls than the lucky loop's single deep run,
        # and that is the trade: two cheap measurements for a sixth of the
        # evidence bill. Calls are not the cost unit; depth is.
        assert engine.calls > loop.calls
        assert engine.cost < loop.cost / 5
    else:
        assert loop.status == X.EXHAUSTED and not loop.conclusion
        assert engine.conclusion == experiment.competing_hypothesis
        # Here the engine wins on both counts, because a loop that cannot be
        # wrong spends its whole attempt budget finding that out.
        assert engine.calls < loop.calls


def test_where_the_win_comes_from_is_sizing_and_stopping():
    """The honest accounting, so the benchmark is not read as magic.

    Against a loop that somehow guessed the right scope, the engine's two
    cheap rungs cost slightly MORE than one deeper rung when the hypothesis
    happens to be true - buying evidence is not free. The win is the other
    two columns: the unsized loop overpays by the whole depth of the ladder,
    and no loop can conclude it was wrong.
    """
    experiment = an_experiment()
    cap = X.depth_cap(ee.classify_task(
        "fix the retry path in friday/continuous.py so a stuck task stops"))

    right = experiment.run(world_where_the_hypothesis_holds)
    unsized = X.speculative_loop(experiment,
                                 world_where_the_hypothesis_holds)
    guessed = X.speculative_loop(experiment,
                                 world_where_the_hypothesis_holds, depth=cap)
    assert unsized.cost > right.cost > guessed.cost

    # In the world that actually costs money - the one where you are wrong -
    # even the lucky loop pays several times over and learns nothing.
    wrong = experiment.run(world_where_the_hypothesis_is_wrong)
    guessed_wrong = X.speculative_loop(
        experiment, world_where_the_hypothesis_is_wrong, depth=cap)
    assert wrong.cost < guessed_wrong.cost
    assert wrong.status == X.REFUTED and guessed_wrong.status == X.EXHAUSTED


# --- the pieces the benchmark stands on -----------------------------------

def test_the_ladder_is_sized_by_verification_depth():
    """`verification_depth` stays the owner of "how much evidence is enough";
    the engine only prices the rungs it sanctions."""
    tiny = ee.classify_task("rename the constant FOO in friday/objectives.py, "
                            "a one line change")
    core = ee.classify_task("redesign the auth core so every endpoint "
                            "enforces the gate")
    assert X.depth_cap(tiny) == ee.verification_depth(tiny)[0]
    assert X.ladder_for(X.depth_cap(tiny)) == (ee.TARGETED,)
    assert X.ladder_for(X.depth_cap(core)) == (
        ee.TARGETED, ee.AFFECTED, ee.INTEGRATION, ee.FULL)
    # A tiny change may not buy a full regression at all: its whole evidence
    # budget is one targeted run.
    assert X.evidence_budget(X.depth_cap(tiny)) == X.DEPTH_COST[ee.TARGETED]
    assert X.evidence_budget(X.depth_cap(core)) > X.DEPTH_COST[ee.FULL]


def test_the_cheap_rung_is_bought_first():
    """Selection is information per unit cost, so a shallow test asked while
    you know nothing outranks a deep one."""
    experiment = X.for_task(
        "redesign the auth core so every endpoint enforces the gate",
        hypothesis="the gate is missing", competing_hypothesis="it is wired")
    result = experiment.run(world_where_the_hypothesis_holds)
    assert [a.depth for a in result.attempts] == [ee.TARGETED, ee.AFFECTED]
    assert result.status == X.RESOLVED
    # It never reached for the full regression it was allowed to buy.
    assert result.cost < X.DEPTH_COST[ee.FULL]


def test_expected_information_gain_falls_as_the_belief_hardens():
    """Why the loop stops: the second identical-strength measurement is worth
    less than the first, and at the extremes nothing is worth buying."""
    gains = [X.expected_info_gain(p, 0.6) for p in (0.5, 0.8, 0.95, 0.999)]
    assert gains == sorted(gains, reverse=True)
    assert gains[0] > 0.2 and gains[-1] < 0.01
    # A coin flip is not evidence at any belief.
    assert X.expected_info_gain(0.5, 0.0) == 0.0


def test_the_same_evidence_twice_stops_the_loop():
    """`continuous.py`'s rule applied to evidence: a second look that returns
    the same failure under the same hypothesis is not a second data point,
    and counting it would walk the belief to confidence on one observation
    seen twice."""
    probe = X.Measurement
    experiment = X.Experiment(
        hypothesis="the resolver is slow",
        competing_hypothesis="the network is down",
        measurements=tuple(probe(f"probe-{i}", ee.TARGETED,
                                 discriminates=0.2) for i in range(1, 4)),
        stop=X.StopCriteria(confidence=0.99))
    same = experiment.run(
        lambda m: (False, "TimeoutError: resolver timed out after 30 s"))
    assert same.status == X.STUCK, same.reason
    assert same.calls == 2, "it stopped on the repeat, not at the attempt cap"
    assert same.attempts[-1].info_gain == 0.0
    assert same.attempts[-1].belief_after == same.attempts[-2].belief_after

    # Three genuinely different failures are three data points, so the same
    # experiment runs them all and reports it learned too little - a
    # different answer from "no information left".
    distinct = experiment.run(lambda m: (False, f"Error inside {m.name}"))
    assert distinct.status == X.INCONCLUSIVE
    assert distinct.calls == 3


def test_running_out_of_evidence_budget_is_a_result_not_a_retry():
    experiment = X.Experiment(
        hypothesis="a", competing_hypothesis="b", budget=5.0,
        measurements=(X.Measurement("cheap", ee.TARGETED, discriminates=0.3),
                      X.Measurement("whole-suite", ee.FULL)),
        stop=X.StopCriteria(confidence=0.99))
    result = experiment.run(world_where_the_hypothesis_holds)
    assert result.status == X.OVER_BUDGET
    assert result.cost <= experiment.budget
    assert "out of evidence budget" in result.reason


def test_an_experiment_needs_something_to_lose_to():
    with pytest.raises(ValueError, match="competing hypothesis"):
        X.Experiment(hypothesis="it is the cache", competing_hypothesis="",
                     measurements=(X.Measurement("t", ee.TARGETED),))
    with pytest.raises(ValueError, match="not an experiment"):
        X.Experiment(hypothesis="same", competing_hypothesis="same",
                     measurements=(X.Measurement("t", ee.TARGETED),))
    with pytest.raises(ValueError, match="is a guess"):
        X.Experiment(hypothesis="a", competing_hypothesis="b",
                     measurements=())
    with pytest.raises(ValueError, match="is decisive"):
        X.Measurement("oracle", ee.TARGETED, discriminates=1.0)


# --- the wiring in continuous.py ------------------------------------------

class Caps:
    def __init__(self):
        self.calls = []
        self.arguments = []

    async def call(self, capability, arguments):
        self.calls.append(capability)
        self.arguments.append(dict(arguments))
        return {"ok": True}


MANIFEST = [{"id": "hermes_delegate", "description": "worker"}]


def _run(store, tasks):
    return compile_objective(store, request="evidence probe", tasks=tasks,
                             manifest=MANIFEST,
                             objective_summary="evidence probe",
                             provenance=PERSON, attended=True)["run_id"]


async def _drive(store, caps, run_id, seconds=3.0):
    engine = ContinuousTaskExecutor(store, caps.call,
                                    executor_id="evidence-test")
    engine.stop()
    await engine.start(run_id)
    deadline = asyncio.get_event_loop().time() + seconds
    while asyncio.get_event_loop().time() < deadline:
        row = store.objective_run(run_id)
        if row["status"] in O.RUN_TERMINAL or row["status"] == O.RUN_PAUSED:
            return row
        await asyncio.sleep(0.05)
    return store.objective_run(run_id)


def test_the_plan_climbs_a_rung_per_attempt_and_stops_at_the_cap():
    """`verification_depth`'s "escalate only if they surface consumers", read
    across a loop: cheap evidence first, one rung deeper per attempt, and
    never past the cap however many attempts the task is granted."""
    core = {"capability": "hermes_delegate",
            "arguments": {"task": "redesign the auth core so every endpoint "
                                  "enforces the gate"}}
    depths = [X.evidence_plan({**core, "attempts": n}).depth
              for n in range(6)]
    assert depths == [ee.TARGETED, ee.AFFECTED, ee.INTEGRATION, ee.FULL,
                      ee.FULL, ee.FULL]
    assert "held at the full cap" in X.evidence_plan(
        {**core, "attempts": 5}).reason

    # A one-line change never leaves the cheapest rung, whatever it costs in
    # attempts: its risk does not justify buying anything deeper.
    tiny = {"capability": "hermes_delegate",
            "arguments": {"task": "rename the constant FOO in "
                                  "friday/objectives.py, a one line change"}}
    assert {X.evidence_plan({**tiny, "attempts": n}).depth
            for n in range(4)} == {ee.TARGETED}


def test_the_plan_never_decides_whether_the_attempt_happens():
    """The attempt budget has an owner, and it is not this module. A plan that
    could refuse would be a second limiter, which parks runs their own owner
    was correctly retrying."""
    plan = X.evidence_plan({"capability": "hermes_delegate", "attempts": 99,
                            "arguments": {"task": "fix a thing"}})
    assert not hasattr(plan, "allowed")
    assert plan.depth in X.DEPTH_LADDER


@pytest.mark.asyncio
async def test_every_attempt_records_the_evidence_it_was_sized_for(tmp_path):
    """The wiring in the dispatch path, beside `objective_budget.check()`.

    The depth lands on the append-only event, not in the dispatch arguments:
    every worker capability here has a strict signature, so an extra keyword
    would be a TypeError rather than a hint.
    """
    store = Store(tmp_path / "x.sqlite3")
    caps = Caps()
    goal = "redesign the auth core so every endpoint enforces the gate"
    run_id = _run(store, [{"capability": "hermes_delegate",
                           "arguments": {"task": goal}}])
    await _drive(store, caps, run_id)
    assert caps.calls == ["hermes_delegate"], caps.calls
    assert "verification_depth" not in caps.arguments[0], \
        "a strict-signature capability must not be handed an extra keyword"

    started = [e for e in store.objective_events(run_id)
               if e["event"] == O.EVENT_TASK_STARTED]
    assert started, "the attempt was never recorded"
    detail = started[0]["detail"]
    assert detail["evidence_depth"] == ee.TARGETED, detail
    assert detail["evidence_cap"] == ee.FULL, (
        "a core change may buy the whole ladder; it just starts cheap")
    assert "cap full" in detail["evidence_reason"]
    store.close()
