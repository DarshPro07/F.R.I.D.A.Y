"""SIDE-002: plan -> dry run -> expected effects -> approval -> real run.

The dry run is a pure read of the capability record: it never calls the
capability, it states what the call would touch, and the blast radius it
lands in sets the reasoning tier, whether a yes is required, and how deeply
the result is verified.
"""
from friday import capabilities, confirmation, dry_run
from friday import execution_economics as ee


def test_dry_run_states_effects_and_never_acts():
    plan = dry_run.dry_run("files_write", {"path": "a.txt"})
    assert plan.acted is False
    assert plan.tool_id == "files_write"
    assert plan.expected_effects
    assert any("write" in effect for effect in plan.expected_effects)
    assert plan.arguments == {"path": "a.txt"}


def test_read_only_capability_has_no_effects_and_needs_no_yes():
    read_only = next(c for c in capabilities.CAPABILITIES.values()
                     if c.side_effect == "read" and not c.requires_confirmation)
    plan = dry_run.dry_run(read_only.id)
    assert plan.blast_radius == "tiny"
    assert plan.requires_confirmation is False
    assert plan.expected_effects == ()
    assert plan.reasoning_tier == ee.TIER_ECONOMY


def test_larger_blast_radius_escalates_reasoning_confirmation_and_verification():
    order = ("tiny", "local", "integration", "core")
    tiers = {}
    for tool_id in ("volume_get", "files_write", "power_shutdown"):
        tiers[tool_id] = dry_run.dry_run(tool_id)
    assert tiers["power_shutdown"].blast_radius == "core"
    radius = [tiers[t].blast_radius for t in tiers]
    assert order.index(radius[0]) < order.index(radius[1]) < order.index(radius[2])
    core = tiers["power_shutdown"]
    assert core.requires_confirmation is True
    assert core.reasoning_tier == ee.TIER_DEEP
    assert core.verification == ee.FULL
    assert tiers["volume_get"].verification == ee.TARGETED


def test_blast_radius_floor_forces_confirmation_even_if_policy_allows():
    cap = capabilities.Capability(
        id="x_sync", description="sync", execution_scope="network",
        side_effect="external_action", risk="MEDIUM",
        requires_confirmation=False)
    plan = dry_run.dry_run("x_sync", capability=cap)
    assert plan.blast_radius == "integration"
    assert plan.requires_confirmation is True


def test_unknown_capability_fails_closed():
    plan = dry_run.dry_run("no_such_tool")
    assert plan.blast_radius == "core"
    assert plan.requires_confirmation is True
    assert plan.acted is False


def test_approval_question_shows_effects_and_binds_the_yes_to_the_action():
    book = confirmation.Book()
    plan, pending = dry_run.propose(book, "run-1", "power_shutdown",
                                    {"delay": 0})
    assert pending is not None
    for effect in plan.expected_effects:
        assert effect in pending.question
    book.approve(pending.nonce)
    other = book.consume(pending.nonce, run_id="run-1", action="power_restart",
                         target="power_restart", arguments={"delay": 0})
    assert other.ok is False
    same = book.consume(pending.nonce, run_id="run-1", action="power_shutdown",
                        target="power_shutdown", arguments={"delay": 0})
    assert same.ok is True


def test_propose_asks_nothing_when_no_yes_is_needed():
    book = confirmation.Book()
    plan, pending = dry_run.propose(book, "run-1", "volume_get")
    assert pending is None
    assert book.pending == {}
    assert plan.requires_confirmation is False
