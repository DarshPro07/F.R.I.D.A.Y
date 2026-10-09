"""
EVO-003: authority zones for self-evolution changes, decided by PolicyEngine.

Z1 local extension, Z2 operational intelligence, Z3 core product code, Z4
protected trust roots. The zone comes from the paths a change touches, never
from what the builder says it is; the builder never verifies its own change.
"""
from __future__ import annotations

import pytest

from friday import confirmation
from friday import policy as P


def _change(paths, **kw):
    base = dict(paths=tuple(paths), built_by="builder-1", verified_by="verifier-1",
                heldout_passed=False, judged_by=(), rollback="revert:abc1234")
    base.update(kw)
    return P.EvolutionChange(**base)


@pytest.fixture
def engine():
    return P.PolicyEngine(autonomy=P.GUARDED)


def test_zone_comes_from_the_paths_and_the_highest_wins():
    assert P.zone_of(["skills/weather/SKILL.md"]) == P.Z1
    assert P.zone_of(["friday/route_benchmarks.py"]) == P.Z2
    assert P.zone_of(["friday/store.py"]) == P.Z3
    assert P.zone_of(["friday/secret_broker.py"]) == P.Z4
    assert P.zone_of(["skills/weather/SKILL.md", "friday/store.py"]) == P.Z3
    assert P.zone_of(["skills/weather/SKILL.md", "friday/policy.py"]) == P.Z4
    # a path the table does not know is core code, not a local extension
    assert P.zone_of(["friday/brand_new_module.py"]) == P.Z3


def test_z1_skill_auto_promotes_after_independent_verification(engine):
    v = engine.decide_promotion(_change(["skills/weather/SKILL.md"]))
    assert v.decision == P.AUTO and v.allowed


def test_nothing_promotes_without_a_verifier_other_than_the_builder(engine):
    for who in ("", "builder-1"):
        v = engine.decide_promotion(
            _change(["skills/weather/SKILL.md"], verified_by=who))
        assert v.decision == P.DENY and "verif" in v.reason


def test_z2_routing_change_needs_a_held_out_benchmark(engine):
    paths = ["friday/route_benchmarks.py"]
    assert engine.decide_promotion(_change(paths)).decision == P.DENY
    v = engine.decide_promotion(_change(paths, heldout_passed=True))
    assert v.decision == P.AUTO


def test_z3_promotes_autonomously_when_verified_independently_with_rollback(engine):
    # owner ruling 2026-10-08c: Z1-Z3 promote on their own after independent
    # verification, with automatic rollback
    v = engine.decide_promotion(_change(["friday/store.py"]))
    assert v.decision == P.AUTO and v.allowed


def test_z3_without_a_rollback_is_never_auto(engine):
    v = engine.decide_promotion(_change(["friday/store.py"], rollback=""))
    assert v.decision == P.CONFIRM and not v.allowed
    assert "rollback" in v.reason


def test_z3_verified_by_its_own_builder_is_never_auto(engine):
    v = engine.decide_promotion(
        _change(["friday/store.py"], verified_by="builder-1"))
    assert v.decision == P.CONFIRM and not v.allowed
    assert "verif" in v.reason
    v = engine.decide_promotion(_change(["friday/store.py"], verified_by=""))
    assert v.decision == P.CONFIRM and "verif" in v.reason


def test_z1_without_a_rollback_is_never_auto(engine):
    v = engine.decide_promotion(_change(["skills/weather/SKILL.md"], rollback=""))
    assert v.decision == P.CONFIRM and not v.allowed
    assert "rollback" in v.reason


def test_z2_without_a_rollback_is_never_auto(engine):
    v = engine.decide_promotion(_change(
        ["friday/route_benchmarks.py"], heldout_passed=True, rollback=""))
    assert v.decision == P.CONFIRM and not v.allowed
    assert "rollback" in v.reason


def test_a_whitespace_rollback_does_not_count(engine):
    v = engine.decide_promotion(_change(["friday/store.py"], rollback="   "))
    assert v.decision == P.CONFIRM


def test_z4_is_proposal_only_in_every_autonomy_mode():
    for mode in P.AUTONOMY_MODES:
        v = P.PolicyEngine(autonomy=mode).decide_promotion(
            _change(["friday/secret_broker.py"], heldout_passed=True))
        assert v.decision == P.CONFIRM and not v.allowed
        assert "proposal" in v.reason


def test_a_session_approval_cannot_pre_approve_a_promotion(engine):
    # evolution.promote is not a callable tool (approving it raises); the
    # promotion tools a person could say yes to are the selfdev ones
    for tool in ("selfdev_promote", "development_promote"):
        engine.approve_for_session(tool)
        assert engine.decide(tool).allowed
    with pytest.raises(P.PolicyError):
        engine.approve_for_session("evolution.promote")
    no_rollback = engine.decide_promotion(_change(["friday/store.py"], rollback=""))
    assert no_rollback.decision == P.CONFIRM and not no_rollback.allowed
    self_checked = engine.decide_promotion(
        _change(["friday/store.py"], verified_by="builder-1"))
    assert self_checked.decision == P.CONFIRM and not self_checked.allowed
    z4 = engine.decide_promotion(_change(["friday/secret_broker.py"],
                                         heldout_passed=True))
    assert z4.decision == P.CONFIRM and not z4.allowed


def test_z4_is_never_auto_whatever_the_other_fields_say(engine):
    for kw in ({}, {"verified_by": ""}, {"rollback": ""},
               {"verified_by": "builder-1"}, {"heldout_passed": True}):
        v = engine.decide_promotion(_change(["friday/secret_broker.py"], **kw))
        assert v.decision == P.CONFIRM and not v.allowed
        assert "proposal" in v.reason


def test_negative_control_candidate_that_edits_its_own_tests_is_rejected(engine):
    v = engine.decide_promotion(_change(
        ["skills/weather/SKILL.md", "tests/test_weather_skill.py"],
        judged_by=("tests/test_weather_skill.py",)))
    assert v.decision == P.DENY and "judge" in v.reason


def test_negative_control_candidate_that_edits_the_verifier_is_rejected(engine):
    v = engine.decide_promotion(_change(
        ["skills/weather/SKILL.md", "friday/evolution_lab.py"],
        judged_by=("friday/evolution_lab.py",)))
    assert v.decision == P.DENY and "judge" in v.reason
    # and a kernel-path edit is refused as a judge edit even if the candidate
    # forgot to list it as one
    v = engine.decide_promotion(_change(["tests/conftest.py"]))
    assert v.decision == P.DENY and "judge" in v.reason


def test_path_spelling_does_not_launder_a_judge_edit(engine):
    v = engine.decide_promotion(_change(
        ["./Tests\\test_weather_skill.py"],
        judged_by=("tests/test_weather_skill.py",)))
    assert v.decision == P.DENY


def test_z3_verdict_is_spent_through_a_bound_confirmation(engine):
    change = _change(["friday/store.py"], rollback="")
    v = engine.decide_promotion(change)
    assert v.needs_confirmation
    book = confirmation.Book()
    ask = book.ask("evo-1", "evolution.promote", P.promotion_target(change),
                   "Promote this core change?")
    book.approve(ask.nonce)
    assert book.consume(ask.nonce, run_id="evo-1", action="evolution.promote",
                        target=P.promotion_target(change)).ok
    other = _change(["friday/store.py", "friday/objectives.py"])
    ask2 = book.ask("evo-1", "evolution.promote", P.promotion_target(change), "q")
    book.approve(ask2.nonce)
    assert not book.consume(ask2.nonce, run_id="evo-1", action="evolution.promote",
                            target=P.promotion_target(other)).ok
