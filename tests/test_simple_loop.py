"""
COG-005: the low-overhead loop for a simple, bounded request.

The acceptance has four parts and each is pinned here: the action schema is
strict, the bounds hold (8 steps, 6,000 tokens, $0.10, one repeat), every
action goes through the dispatch port and the policy engine (never a process
of its own), and a task that turns out complex goes to the Objective OS whole
instead of growing in place.
"""
from __future__ import annotations

import asyncio
import inspect
import subprocess

import pytest

from friday import capability_runtime as CR
from friday import contracts as c
from friday import policy as P
from friday import simple_loop as SL

SIMPLE_REQUEST = "what is the battery level"


def action(**changes) -> dict:
    spec = {"intent": "read the battery level", "capability": "system_get_info",
            "arguments": {}, "postcondition": {"status": c.SUCCEEDED,
                                               "output_contains": "battery"},
            "confidence": 0.9}
    spec.update(changes)
    return spec


class Dispatch:
    """A dispatch port that records what it was asked and answers a fixed way."""

    def __init__(self, output="nothing useful", status=c.SUCCEEDED):
        self.calls: list[tuple[str, dict, c.Run]] = []
        self.output, self.status = output, status

    async def __call__(self, capability_id, arguments, *, run=None):
        self.calls.append((capability_id, dict(arguments), run))
        return {"status": self.status, "output": self.output,
                "error": None if self.status == c.SUCCEEDED else "refused"}


class Proposer:
    """Hands back a scripted proposal per step; the last one repeats."""

    def __init__(self, *proposals: SL.Proposal):
        self.proposals = list(proposals)
        self.seen: list[int] = []

    def __call__(self, state: SL.LoopState) -> SL.Proposal:
        self.seen.append(len(state.steps))
        index = min(len(self.seen) - 1, len(self.proposals) - 1)
        return self.proposals[index]


class Escalations:
    def __init__(self):
        self.requests: list[str] = []

    def __call__(self, run, request):
        self.requests.append(request)
        return {"status": c.SUCCEEDED, "run_id": "OBJ-1"}


def go(request, dispatch, proposer, escalations, **kwargs):
    return asyncio.run(SL.run_simple(request, dispatch, proposer,
                                     escalate=escalations, **kwargs))


# --- the schema ---------------------------------------------------------------

@pytest.mark.parametrize("raw", [
    "not an object",
    {k: v for k, v in action().items() if k != "capability"},
    {**action(), "surprise": 1},
    action(intent=""),
    action(capability="rm -rf /"),
    action(arguments=["a list"]),
    action(arguments={"approved_by": "me"}),
    action(arguments={"callback": object()}),
    action(postcondition={}),
    action(postcondition={"status": "failed"}),
    action(postcondition={"status": c.SUCCEEDED, "when": "soon"}),
    action(confidence=1.5),
    action(confidence=-0.1),
    action(confidence=float("nan")),
    action(confidence=True),
    action(confidence="high"),
])
def test_schema_rejects_missing_or_malformed_fields(raw):
    with pytest.raises(SL.SchemaError):
        SL.ActionSpec.from_dict(raw)


def test_schema_accepts_a_whole_action_and_normalises_it():
    spec = SL.ActionSpec.from_dict(action(confidence=1))
    assert spec.capability == "system_get_info"
    assert spec.confidence == 1.0
    assert spec.postcondition == {"status": c.SUCCEEDED, "output_contains": "battery"}


def test_a_malformed_proposal_runs_nothing_and_escalates():
    dispatch, escalations = Dispatch(), Escalations()
    result = go(SIMPLE_REQUEST, dispatch, Proposer(SL.Proposal(action={"intent": "x"})),
                escalations)
    assert dispatch.calls == []
    assert result.outcome == SL.ESCALATED and "not a valid action" in result.reason


# --- the gate and escalation ----------------------------------------------------

def test_non_simple_class_is_refused_and_escalates():
    dispatch, escalations = Dispatch(), Escalations()
    proposer = Proposer(SL.Proposal(action=action()))
    request = "delete all the files in my downloads folder"
    result = go(request, dispatch, proposer, escalations)
    assert result.outcome == SL.ESCALATED and "not a simple task" in result.reason
    assert dispatch.calls == [] and proposer.seen == []
    assert escalations.requests == [request]
    assert result.escalation == {"status": c.SUCCEEDED, "run_id": "OBJ-1"}


def test_mid_loop_complexity_signal_escalates_to_objective_os_without_growing_in_place():
    dispatch, escalations = Dispatch(), Escalations()
    proposer = Proposer(
        SL.Proposal(action=action(arguments={"a": 1})),
        SL.Proposal(complex_because="it needs three files compared"),
        SL.Proposal(action=action(arguments={"a": 2})))
    result = go(SIMPLE_REQUEST, dispatch, proposer, escalations)
    assert result.outcome == SL.ESCALATED and "complex" in result.reason
    assert len(dispatch.calls) == 1, "nothing ran after the signal"
    assert len(result.steps) == 1 and proposer.seen == [0, 1]
    assert escalations.requests == [SIMPLE_REQUEST], "the original request, once"


def test_asking_to_start_an_objective_is_an_escalation_not_an_action():
    dispatch, escalations = Dispatch(), Escalations()
    result = go(SIMPLE_REQUEST, dispatch,
                Proposer(SL.Proposal(action=action(capability="objective_start"))),
                escalations)
    assert dispatch.calls == []
    assert result.outcome == SL.ESCALATED and "objective" in result.reason


def test_a_low_confidence_guess_is_not_run():
    dispatch, escalations = Dispatch(), Escalations()
    result = go(SIMPLE_REQUEST, dispatch,
                Proposer(SL.Proposal(action=action(confidence=0.2))), escalations)
    assert dispatch.calls == [] and result.outcome == SL.ESCALATED
    assert "confidence" in result.reason


def test_the_default_escalation_starts_an_objective_with_the_callers_lineage(monkeypatch):
    from friday.toolsets import objectives as objective_tools

    started = []

    def objective_start(run, objective, **_):
        started.append((run, objective))
        return c.succeeded(c.started(run.run_id, "objectives.start"),
                           output="x",
                           verification=c.Verification(method="test", evidence="e"))

    monkeypatch.setattr(objective_tools, "objective_start", objective_start)
    caller = c.Run.create(SIMPLE_REQUEST, provenance=c.READ_MATERIAL)
    caller.attended = False
    answer = SL.escalate_to_objective_os(caller, "plan the move")
    (handoff, objective), = started
    assert objective == "plan the move" and answer["status"] == c.SUCCEEDED
    assert (handoff.provenance, handoff.attended) == (c.READ_MATERIAL, False)


# --- the bounds -----------------------------------------------------------------

def test_stops_at_8_steps():
    dispatch, escalations = Dispatch(), Escalations()
    counter = iter(range(100))

    def distinct(state):
        return SL.Proposal(action=action(arguments={"n": next(counter)}))

    result = go(SIMPLE_REQUEST, dispatch, distinct, escalations)
    assert len(dispatch.calls) == SL.MAX_STEPS == 8
    assert result.outcome == SL.ESCALATED and "8 actions" in result.reason
    assert escalations.requests == [SIMPLE_REQUEST]


def test_stops_at_6000_tokens_and_at_0_10_usd():
    counter = iter(range(100))

    def distinct(tokens, usd):
        return lambda state: SL.Proposal(action=action(arguments={"n": next(counter)}),
                                         tokens=tokens, cost_usd=usd)

    # 2,000 a step: the third reaches 6,000 exactly and runs; a fourth is not asked for.
    dispatch, escalations = Dispatch(), Escalations()
    result = go(SIMPLE_REQUEST, dispatch, distinct(2000, 0.0), escalations)
    assert len(dispatch.calls) == 3 and result.tokens == 6000
    assert result.outcome == SL.ESCALATED and "budget is spent" in result.reason

    # A proposal that lands past the ceiling is not run.
    dispatch, escalations = Dispatch(), Escalations()
    result = go(SIMPLE_REQUEST, dispatch, distinct(4000, 0.0), escalations)
    assert len(dispatch.calls) == 1 and result.tokens == 8000
    assert result.outcome == SL.ESCALATED and "not run" in result.reason

    # $0.04 a step: $0.08 is inside the ceiling, $0.12 is past it.
    dispatch, escalations = Dispatch(), Escalations()
    result = go(SIMPLE_REQUEST, dispatch, distinct(10, 0.04), escalations)
    assert len(dispatch.calls) == 2 and result.cost_usd == pytest.approx(0.12)
    assert result.outcome == SL.ESCALATED and "not run" in result.reason

    # Exactly $0.10 is allowed; the next step is not started.
    dispatch, escalations = Dispatch(), Escalations()
    result = go(SIMPLE_REQUEST, dispatch, distinct(10, 0.05), escalations)
    assert len(dispatch.calls) == 2 and result.cost_usd == pytest.approx(0.10)
    assert "budget is spent" in result.reason


def test_a_proposer_cannot_report_a_negative_cost():
    with pytest.raises(ValueError):
        SL.Proposal(tokens=-1)
    with pytest.raises(ValueError):
        SL.Proposal(cost_usd=float("nan"))


def test_second_identical_action_is_allowed_third_is_refused():
    dispatch, escalations = Dispatch(), Escalations()
    proposer = Proposer(SL.Proposal(action=action(arguments={"same": "args"})))
    result = go(SIMPLE_REQUEST, dispatch, proposer, escalations)
    assert len(dispatch.calls) == 2, "one repeat, no more"
    assert result.outcome == SL.ESCALATED and "third time" in result.reason
    assert len(result.steps) == 2


# --- dispatch, policy, no process --------------------------------------------------

class Gate(P.PolicyEngine):
    def __init__(self, decision):
        super().__init__(autonomy=P.GUARDED)
        self.decision = decision

    def decide(self, tool_id):
        return P.Verdict(tool_id, "test", self.decision, f"{self.decision} by the test gate")


@pytest.mark.parametrize("decision", [P.ASK, P.DENY])
def test_every_action_goes_through_dispatch_and_policy_never_subprocess(
        decision, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("the simple loop started a process")

    for name in ("Popen", "run", "call", "check_call", "check_output"):
        monkeypatch.setattr(subprocess, name, forbidden)

    dispatch = CR.build_dispatch(engine=Gate(decision))
    escalations = Escalations()
    result = go(SIMPLE_REQUEST, dispatch, Proposer(SL.Proposal(action=action())),
                escalations)
    assert result.outcome == SL.BLOCKED
    assert decision in result.reason and "test gate" in result.reason
    assert [step.status for step in result.steps] == [c.CANCELLED]
    assert escalations.requests == [], "a refused action is not routed around"
    assert "subprocess" not in inspect.getsource(SL)


def test_the_loop_reaches_a_real_capability_through_the_runtime():
    dispatch = CR.build_dispatch(engine=P.PolicyEngine(autonomy=P.DANGEROUS))
    result = go(SIMPLE_REQUEST, dispatch,
                Proposer(SL.Proposal(action=action(
                    postcondition={"status": c.SUCCEEDED, "output_contains": "hostname"}))),
                Escalations())
    assert result.outcome == SL.DONE
    assert result.steps[0].status == c.SUCCEEDED


def test_each_action_carries_the_run_and_a_bounded_envelope():
    dispatch, escalations = Dispatch(output="battery 80%"), Escalations()
    go(SIMPLE_REQUEST, dispatch, Proposer(SL.Proposal(action=action())), escalations)
    (capability, arguments, run), = dispatch.calls
    envelope = run.envelope
    assert envelope is not None and envelope.run_id == run.run_id
    assert envelope.tool_call_limit == SL.MAX_STEPS
    assert envelope.token_limit == SL.MAX_TOKENS
    assert "objective_start" in envelope.blocked_capabilities
    assert envelope.seconds_left() <= SL.LOOP_LIFETIME.total_seconds()


def test_an_envelope_refusal_blocks_rather_than_escalates():
    run = c.Run.create(SIMPLE_REQUEST)
    run.envelope = c.Envelope(run_id=run.run_id, allowed_capabilities=("files_list",))
    dispatch, escalations = Dispatch(), Escalations()
    result = go(SIMPLE_REQUEST, dispatch, Proposer(SL.Proposal(action=action())),
                escalations, run=run)
    assert dispatch.calls == [] and result.outcome == SL.BLOCKED
    assert "envelope" in result.reason and escalations.requests == []


def test_an_unregistered_capability_is_observed_not_fatal():
    async def unknown(capability_id, arguments, *, run=None):
        raise LookupError(f"no capability is registered as {capability_id!r}")

    proposer = Proposer(SL.Proposal(action=action(capability="not_a_thing")),
                        SL.Proposal(action=action()))
    result = asyncio.run(SL.run_simple(SIMPLE_REQUEST, unknown, proposer,
                                       escalate=Escalations()))
    assert result.steps[0].status == c.FAILED and "not_a_thing" in result.steps[0].error
    assert proposer.seen[:2] == [0, 1], "the proposer saw the failure and went on"


# --- finishing -------------------------------------------------------------------

def test_postcondition_met_finishes_in_few_actions():
    dispatch, escalations = Dispatch(output={"battery": "80%"}), Escalations()
    result = go(SIMPLE_REQUEST, dispatch,
                Proposer(SL.Proposal(action=action(), tokens=300, cost_usd=0.002)),
                escalations)
    assert result.outcome == SL.DONE and len(dispatch.calls) == 1
    assert result.steps[0].met and result.tokens == 300
    assert escalations.requests == []


def test_an_unmet_first_action_is_observed_and_the_next_one_can_finish():
    outputs = iter(["no data", "battery 80%"])

    async def dispatch(capability_id, arguments, *, run=None):
        return {"status": c.SUCCEEDED, "output": next(outputs), "error": None}

    seen_outputs = []

    def propose(state):
        seen_outputs.append([step.output for step in state.steps])
        return SL.Proposal(action=action(arguments={"try": len(state.steps)}))

    result = asyncio.run(SL.run_simple(SIMPLE_REQUEST, dispatch, propose,
                                       escalate=Escalations()))
    assert result.outcome == SL.DONE and len(result.steps) == 2
    assert seen_outputs == [[], ["no data"]]


def test_a_failed_status_never_satisfies_a_postcondition():
    dispatch, escalations = Dispatch(output="battery 80%", status=c.FAILED), Escalations()
    result = go(SIMPLE_REQUEST, dispatch,
                Proposer(SL.Proposal(action=action(postcondition={"status": c.SUCCEEDED}))),
                escalations)
    assert result.outcome != SL.DONE
    assert not any(step.met for step in result.steps)


# --- escalating after an action already ran ------------------------------------------

def _escalate_after(capability, status=c.SUCCEEDED):
    """One unmet action on `capability`, then the proposer calls the task complex."""
    dispatch, escalations = Dispatch(status=status), Escalations()
    proposer = Proposer(
        SL.Proposal(action=action(capability=capability,
                                  postcondition={"status": c.SUCCEEDED,
                                                 "output_contains": "never said"})),
        SL.Proposal(complex_because="it needs a plan"))
    return go(SIMPLE_REQUEST, dispatch, proposer, escalations), dispatch, escalations


def test_escalation_is_held_when_a_step_that_cannot_be_repeated_already_ran():
    # The planner reads only the request, so handing it over now would plan the
    # reminder a second time.
    result, dispatch, escalations = _escalate_after("reminders_create")
    assert len(dispatch.calls) == 1
    assert result.outcome == SL.BLOCKED
    assert "reminders_create" in result.reason and "step 1" in result.reason
    assert escalations.requests == []
    assert [step.status for step in result.steps] == [c.SUCCEEDED]


@pytest.mark.parametrize("capability", ["system_get_info", "audio_session_volume"])
def test_escalation_still_hands_over_after_steps_that_are_safe_to_repeat(capability):
    result, dispatch, escalations = _escalate_after(capability)
    assert len(dispatch.calls) == 1 and result.outcome == SL.ESCALATED
    assert escalations.requests == [SIMPLE_REQUEST]


def test_escalation_still_hands_over_when_the_step_that_ran_did_nothing():
    for status in (c.FAILED, c.NOT_CARRIED_OUT):
        result, dispatch, escalations = _escalate_after("reminders_create", status=status)
        assert result.outcome == SL.ESCALATED, status
        assert escalations.requests == [SIMPLE_REQUEST]


def test_escalation_is_held_for_a_step_the_registry_cannot_vouch_for():
    result, _, escalations = _escalate_after("not_in_the_registry")
    assert result.outcome == SL.BLOCKED and escalations.requests == []


# --- the router and the model proposer ---------------------------------------------

def test_route_simple_returns_none_for_non_simple():
    dispatch = Dispatch()
    proposer = Proposer(SL.Proposal(action=action()))
    result = asyncio.run(SL.route_simple(
        "delete all the files in my downloads folder", dispatch, proposer))
    assert result is None
    assert dispatch.calls == [] and proposer.seen == []


def test_route_simple_runs_simple():
    dispatch = Dispatch(output={"battery": "80%"})
    proposer = Proposer(SL.Proposal(action=action(), tokens=300, cost_usd=0.002))
    result = asyncio.run(SL.route_simple(SIMPLE_REQUEST, dispatch, proposer))
    assert result is not None and result.outcome == SL.DONE
    assert len(dispatch.calls) == 1 and proposer.seen == [0]


def test_model_proposer_meters_tokens_and_cost():
    import json as _json

    counter = iter(range(100))
    seen_states = []

    def complete(state):
        seen_states.append(len(state.steps))
        return _json.dumps(action(arguments={"n": next(counter)})), 10, 0.04

    dispatch, escalations = Dispatch(), Escalations()
    result = go(SIMPLE_REQUEST, dispatch, SL.model_proposer(complete), escalations)
    # $0.04 a step: $0.08 is inside the $0.10 ceiling, $0.12 is past it and not run.
    assert len(dispatch.calls) == 2 and result.tokens == 30
    assert result.cost_usd == pytest.approx(0.12)
    assert result.outcome == SL.ESCALATED and "not run" in result.reason
    assert seen_states == [0, 1, 2]


def test_model_proposer_complex_because_escalates():
    import json as _json

    def complete(state):
        return _json.dumps({"complex_because": "needs several systems"}), 5, 0.001

    dispatch, escalations = Dispatch(), Escalations()
    result = go(SIMPLE_REQUEST, dispatch, SL.model_proposer(complete), escalations)
    assert result.outcome == SL.ESCALATED and "needs several systems" in result.reason
    assert dispatch.calls == []


@pytest.mark.parametrize("text", ["not json at all", "", "[1, 2]", "null"])
def test_model_proposer_bad_json_is_rejected(text):
    dispatch, escalations = Dispatch(), Escalations()
    result = go(SIMPLE_REQUEST, dispatch,
                SL.model_proposer(lambda state: (text, 7, 0.001)), escalations)
    assert dispatch.calls == []
    assert result.outcome == SL.ESCALATED and "not a valid action" in result.reason
    assert result.tokens == 7
