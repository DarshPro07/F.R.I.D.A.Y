"""Autonomy slider (FR-POL-001) - compiling a named level into the
16-domain grid must never quietly unlock a constitutional class or a
consequential action. That failure would make "High Autonomy" mean "no
more kernel", which is exactly what the negative controls below pin."""
from __future__ import annotations

import itertools

import pytest

from friday import user_policy as up


def make(tmp_path):
    return up.UserPolicy(tmp_path / 'policy.sqlite3')


# ---------------------------------------------------------------------------
# compile_level - pure, total, monotone
# ---------------------------------------------------------------------------


def test_every_level_compiles():
    for level in up.LEVELS:
        grid = up.compile_level(level)
        assert all(state in ("AUTO", "CONFIRM", "DENY")
                   for state in grid.values())


def test_unknown_level_raises():
    with pytest.raises(ValueError):
        up.compile_level("Full Send")


def test_compile_level_covers_exactly_domains():
    for level in up.LEVELS:
        assert set(up.compile_level(level).keys()) == set(up.DOMAINS)


def test_compile_level_never_mentions_constitutional_names():
    for level in up.LEVELS:
        grid = up.compile_level(level)
        assert not (set(grid) & up.CONSTITUTIONAL_DENY)


def test_monotone_across_all_five_levels():
    """A domain AUTO at level N must stay AUTO at every level above N -
    checked over all 16 domains and all five levels, not a hand-picked
    few."""
    grids = [up.compile_level(level) for level in up.LEVELS]
    for domain in up.DOMAINS:
        seen_auto = False
        for level, grid in zip(up.LEVELS, grids):
            state = grid[domain]
            if seen_auto:
                assert state == "AUTO", (
                    f"{domain} regressed from AUTO to {state} at level "
                    f"{level!r} - the slider is not monotone")
            if state == "AUTO":
                seen_auto = True


def test_advise_is_confirm_except_research():
    grid = up.compile_level("Advise")
    assert grid["research"] == "AUTO"
    assert all(state == "CONFIRM" for domain, state in grid.items()
               if domain != "research")


def test_draft_adds_file_write_and_local_build():
    grid = up.compile_level("Draft")
    assert grid["research"] == grid["file_write"] == grid["local_build"] \
        == "AUTO"
    still_confirm = set(up.DOMAINS) - {"research", "file_write",
                                       "local_build"}
    assert all(grid[d] == "CONFIRM" for d in still_confirm)


def test_ask_before_acting_matches_current_defaults(tmp_path):
    """The installed default must be a no-op slider position - moving to
    this level from a fresh install changes nothing."""
    policy = make(tmp_path)
    assert up.compile_level("Ask Before Acting") == policy.snapshot()


def test_high_autonomy_keeps_exactly_the_four_consequential_on_confirm():
    grid = up.compile_level("High Autonomy")
    consequential = {"spend_money", "buy_domain", "budget_envelope_change",
                     "delete_data"}
    assert all(grid[d] == "CONFIRM" for d in consequential)
    assert all(grid[d] == "AUTO" for d in set(up.DOMAINS) - consequential)


def test_no_two_levels_compile_to_the_same_mapping():
    """Five names must mean five effective positions - a level that
    changes nothing relative to its neighbor is not a real choice. Iterate
    the pairs so a future edit that collapses two levels fails here,
    rather than hard-coding which pair to check."""
    for a, b in itertools.combinations(up.LEVELS, 2):
        assert up.compile_level(a) != up.compile_level(b), (
            f"{a!r} and {b!r} compile to the identical grid")


# ---------------------------------------------------------------------------
# set_level / level() - through grant(), audited, round-trips
# ---------------------------------------------------------------------------


def test_set_level_writes_one_event_per_changed_domain(tmp_path):
    """The expected changed domain(s) are DERIVED from compile_level's own
    diff between the two adjacent levels, not hard-coded by name - so the
    next time a domain moves between levels, this test follows that
    decision instead of going stale again."""
    policy = make(tmp_path)
    before = up.compile_level("Ask Before Acting")
    after = up.compile_level("Autonomous Routine Work")
    expected_changed = {d for d in up.DOMAINS if before[d] != after[d]}

    out = policy.set_level("Autonomous Routine Work",
                           reason="owner moved the slider")
    assert out["status"] == "succeeded"
    assert set(out["changed_domains"]) == expected_changed
    for domain in expected_changed:
        trail = policy.audit_trail(domain)
        assert trail[0]["state"] == after[domain]
        assert "Autonomous Routine Work" in trail[0]["reason"]
        assert "owner moved the slider" in trail[0]["reason"]


def test_set_level_leaves_unchanged_domains_without_new_events(tmp_path):
    policy = make(tmp_path)
    before = len(policy.audit_trail("research"))
    policy.set_level("Draft")  # research is AUTO at Draft and at defaults
    after = len(policy.audit_trail("research"))
    assert before == after == 0


def test_set_level_is_idempotent_in_domain_events(tmp_path):
    """Re-setting the level a policy is already at changes no domain
    state, so it writes no new per-domain grant events."""
    policy = make(tmp_path)
    policy.set_level("Ask Before Acting")
    trail_before = {d: len(policy.audit_trail(d)) for d in up.DOMAINS}
    policy.set_level("Ask Before Acting")
    trail_after = {d: len(policy.audit_trail(d)) for d in up.DOMAINS}
    assert trail_before == trail_after


def test_set_level_rejects_unknown_level(tmp_path):
    policy = make(tmp_path)
    with pytest.raises(ValueError):
        policy.set_level("nope")


def test_level_round_trips(tmp_path):
    policy = make(tmp_path)
    for level in up.LEVELS:
        policy.set_level(level)
        assert policy.level() == level


def test_level_infers_ask_before_acting_when_nothing_set(tmp_path):
    policy = make(tmp_path)
    assert policy.level() == "Ask Before Acting"


# ---------------------------------------------------------------------------
# NEGATIVE CONTROLS - a guard that cannot go red is not evidence.
# ---------------------------------------------------------------------------


def test_high_autonomy_does_not_touch_constitutional_classes(tmp_path):
    policy = make(tmp_path)
    policy.set_level("High Autonomy")
    for name in up.CONSTITUTIONAL_DENY:
        assert policy.state_of(name) == "DENY", (
            f"{name} was not DENY after High Autonomy")
    out = policy.grant("secret_exposure", "AUTO",
                       reason="the slider should not have unlocked this")
    assert out["status"] == "refused"
    assert policy.state_of("secret_exposure") == "DENY"
    trail = policy.audit_trail("secret_exposure")
    assert trail and trail[0]["state"] == "REFUSED"


def test_high_autonomy_keeps_consequential_domains_on_confirm(tmp_path):
    policy = make(tmp_path)
    policy.set_level("High Autonomy")
    for domain in ("spend_money", "buy_domain", "budget_envelope_change",
                   "delete_data"):
        assert policy.state_of(domain) == "CONFIRM", (
            f"{domain} was not CONFIRM at High Autonomy")
    # deploy_website is NOT consequential in this scheme: a bad deploy is
    # reversible by redeploying, so it graduates to AUTO at the top.
    assert policy.state_of("deploy_website") == "AUTO"
