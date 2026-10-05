"""
The local model policy (PRD v7 section 29, MODEL-007; Amendment A3's 2 GB).

The numbers live in `friday.local_model_policy`; `test_local_experts` shows the
pool enforcing them on a simulated machine.
"""
from __future__ import annotations

import pytest

from friday import hardware_profile as hp
from friday import local_model_policy as LMP

POLICY = LMP.LocalModelPolicy()


def test_the_default_pack_cap_is_the_one_the_hardware_profile_reports():
    assert POLICY.artifact_cap_gb == LMP.ARTIFACT_CAP_GB == hp.MODEL_ARTIFACT_CAP_GB == 2.0
    assert POLICY.max_resident_general == 1
    assert (POLICY.min_ctx, POLICY.default_ctx, POLICY.max_ctx) == (4096, 4096, 8192)


def test_a_3_7_gb_artifact_is_refused_and_accepted_only_on_an_owner_override():
    refused = POLICY.check_onboarding("big", 3.7, "general")
    assert not refused.accepted and not refused.override
    assert "3.7 GB exceeds the 2 GB default-pack cap" in refused.reason and "owner override" in refused.reason
    accepted = POLICY.check_onboarding("big", 3.7, "general", owner_override=True,
                                       override_reason="  the owner's choice ")
    assert accepted.accepted and accepted.override
    assert accepted.reason.endswith("accepted on owner override: the owner's choice")
    assert accepted.to_dict()["override"] is True


@pytest.mark.parametrize("size", [0.1, 1.99, 2.0])
def test_an_artifact_within_the_cap_needs_no_override(size):
    verdict = POLICY.check_onboarding("small", size, "vision")
    assert verdict.accepted and not verdict.override


@pytest.mark.parametrize("size", [2.0001, 3.7, 40.0])
def test_every_kind_is_held_to_the_cap(size):
    for kind in ("", LMP.GENERAL, LMP.VISION, LMP.EMBEDDING):
        assert not POLICY.check_onboarding("m", size, kind).accepted


@pytest.mark.parametrize("size", [0, -1.0, float("nan"), float("inf"), "2", None])
def test_a_size_that_is_not_a_positive_number_is_refused_even_on_override(size):
    verdict = POLICY.check_onboarding("m", size, owner_override=True)
    assert not verdict.accepted and "not a size" in verdict.reason


def test_a_policy_must_be_coherent():
    for bad in ({"artifact_cap_gb": 0}, {"max_resident_general": 0},
                {"min_ctx": 8192, "default_ctx": 4096}, {"default_ctx": 16384}):
        with pytest.raises(ValueError):
            LMP.LocalModelPolicy(**bad)


def test_context_defaults_to_4k_and_a_request_is_held_to_4k_8k():
    assert POLICY.context_for() == 4096
    assert [POLICY.context_for(r) for r in (0, 4096, 6000, 8192, 131072)] == [4096, 4096, 6000, 8192, 8192]


def test_context_is_reduced_before_the_model_gives_up():
    # A 2 GB model costs 0.1 GB per 1K tokens of context: 2.8 GB at 8K, 2.4 GB at the 4K floor.
    fit = lambda room: POLICY.fit_context(size_gb=2.0, headroom_gb=room, requested=8192)  # noqa: E731
    assert fit(5.0) == 8192
    assert fit(2.55) == 5120                  # context stepped down, the model kept
    assert fit(2.45) == 4096                  # at the floor
    assert fit(2.35) is None                  # not even the floor fits


def test_a_larger_model_is_raised_only_with_context_at_the_floor():
    pack = [LMP.Candidate("small", 1.0), LMP.Candidate("mid", 1.5), LMP.Candidate("large", 2.0),
            LMP.Candidate("over_cap", 3.7)]
    # Room for the large model at the 4K floor but not at 8K: it is chosen, with the context that room buys.
    assert POLICY.choose_model(pack, 2.55, requested=8192) == LMP.Choice("large", 2.0, 5120)
    assert POLICY.choose_model(pack, 2.45, requested=8192) == LMP.Choice("large", 2.0, 4096)
    # Not even at the floor: the next model down, which keeps the full request.
    assert POLICY.choose_model(pack, 2.35, requested=8192) == LMP.Choice("mid", 1.5, 8192)
    # Plenty of room is still held to the cap: the 3.7 GB artifact is never chosen by default.
    assert POLICY.choose_model(pack, 9.0, requested=8192) == LMP.Choice("large", 2.0, 8192)
    assert POLICY.choose_model(pack, 1.45) == LMP.Choice("small", 1.0, 4096)
    assert POLICY.choose_model(pack, 0.8) is None


def test_the_default_context_for_a_room_never_drops_below_the_floor():
    assert POLICY.context_for_room(room_gb=1.47, model_gb=1.47) == 4096      # the room holds no context
    assert POLICY.context_for_room(room_gb=2.88, model_gb=2.0) == 8192
    assert POLICY.context_for_room(room_gb=2.45, model_gb=2.0) == 4096
