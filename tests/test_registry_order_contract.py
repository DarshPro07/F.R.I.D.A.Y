"""Registry order is the planner's shortlist priority - a behavioral
contract, not an accident (RC1 finding #10).

The defect this guards: 16 vnext (READ, SYSTEM) capabilities were
appended mid-registry and crowded system_resource_usage out of the
planner's 8-slot candidate shortlist, silently misrouting "check my
computer" to system_battery. The planner tests caught one phrase;
this test pins the CONTRACT: daily drivers must appear in the
shortlist for their own shape, whatever gets added later.
"""
from friday import planner as P
from friday import semantics as S
DAILY_DRIVERS = {'system_resource_usage': 'Friday, check my computer.', 'apps_open': 'Open Paint.'}


def test_daily_drivers_stay_in_their_shortlists():
    for capability_id in DAILY_DRIVERS:
        operation, target = S.for_capability(capability_id)
        shortlist = P.candidates(operation, target)
        assert capability_id in shortlist, f"{capability_id} fell out of the {operation}/{target} shortlist {shortlist} - a capability was likely added too early in the registry; operating-layer surfaces belong at the tail (see RC1 finding #10)"


def test_daily_driver_phrases_still_route():
    for capability_id, phrase in DAILY_DRIVERS.items():
        plan = P.plan_objective(phrase)
        chosen = {goal.capability for goal in plan.goals}
        assert capability_id in chosen, f"{phrase!r} no longer reaches {capability_id}: got {chosen}"


def test_operating_layer_sits_behind_daily_drivers():
    """The vnext operating-layer capabilities share the (READ, SYSTEM)
    shape with real system reads; registry order must keep the system
    reads ahead of them."""
    shortlist = P.candidates('READ', 'SYSTEM')
    operating_layer = {'spend_gate', 'policy_snapshot', 'contract_pending_questions'}
    system_reads = [cid for cid in shortlist if cid not in operating_layer]
    assert system_reads, 'the READ/SYSTEM shortlist is all operating-layer - daily drivers were crowded out again'