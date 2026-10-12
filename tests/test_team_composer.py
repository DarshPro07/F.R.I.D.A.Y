"""
AGENT-004: FRIDAY composes a team for a goal itself - registered roles first,
temporary roles only for the gap, every temporary role and team expiring, and
a repeatedly useful temporary role proposed (never silently made) permanent.
"""
from __future__ import annotations

import pytest

from friday import hermes_team, roles
from friday.agent_registry import AgentRegistry, RegistryError
from friday.store import Store
from friday.team_composer import Composer, ComposerError, RoleNeed

HOUR = 3600.0


class Clock:
    def __init__(self, now: float = 1_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "composer.sqlite3")
    yield s
    s.close()


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def comp(store, clock):
    return Composer(store, clock=clock, promote_after=2)


SECURITY = RoleNeed(id="security-reviewer", title="Security Reviewer",
                    focus="Read it as an attacker would.",
                    keywords=("security", "untrusted"))
HARDWARE = RoleNeed(id="hardware-researcher", title="Hardware Researcher",
                    focus="Find the datasheet before the part is chosen.",
                    keywords=("hardware", "datasheet", "stepper"))
FIRMWARE = RoleNeed(id="firmware-developer", title="Firmware Developer",
                    focus="The board has 32 KB; count them.",
                    keywords=("firmware", "embedded"))
INTERFACE = RoleNeed(id="interface-developer", title="Interface Developer",
                     focus="The operator has gloves on.",
                     keywords=("touchscreen", "hmi"))
TESTER = RoleNeed(id="print-tester", title="Print Tester",
                  focus="A print that finishes is not a print that is right.",
                  keywords=("test",))
PRINTER = (HARDWARE, FIRMWARE, INTERFACE, TESTER)


def _count(store, table):
    return store._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_a_goal_the_registered_roles_cover_creates_no_role(comp, store):
    got = comp.compose("review the login flow for security", needs=(SECURITY,),
                       ttl_s=HOUR)
    assert got["created"] == []
    assert got["roles"] == ["security"]
    assert comp.live_roles() == ()
    assert _count(store, "agent_temp_roles") == 0


def test_a_gap_creates_only_the_missing_roles_each_with_an_expiry(comp, clock):
    got = comp.compose("build a 3D printer controller", needs=PRINTER,
                       ttl_s=2 * HOUR)
    assert got["created"] == ["hardware-researcher", "firmware-developer",
                              "interface-developer"]
    assert got["reused"] == ["tests"]          # the tester is already registered
    assert got["roles"] == ["hardware-researcher", "firmware-developer",
                            "interface-developer", "tests"]
    live = comp.temp_roles()
    assert {r["role_id"] for r in live} == set(got["created"])
    assert all(r["expires_at"] == clock.now + 2 * HOUR for r in live)
    team = comp.teams()[0]
    assert team["team_id"] == got["team_id"]
    assert team["expires_at"] == clock.now + 2 * HOUR
    assert team["roles"] == got["roles"]


def test_the_team_is_recorded_in_the_ledger(comp):
    got = comp.compose("build a 3D printer controller", needs=PRINTER,
                       ttl_s=HOUR)
    kinds = [(e["kind"], e["subject"]) for e in comp.ledger()]
    assert ("role_created", "firmware-developer") in kinds
    assert ("team_composed", got["team_id"]) in kinds
    composed = [e for e in comp.ledger("team_composed")][0]
    assert composed["detail"]["goal"] == "build a 3D printer controller"
    assert composed["detail"]["roles"] == got["roles"]


def test_after_expiry_the_roles_and_team_are_gone_but_the_ledger_remembers(
        comp, clock, store):
    comp.compose("build a 3D printer controller", needs=PRINTER, ttl_s=HOUR)
    clock.now += HOUR + 1
    assert comp.live_roles() == ()
    assert comp.teams() == []
    assert _count(store, "agent_temp_roles") == 0
    assert _count(store, "agent_temp_teams") == 0
    assert [e["subject"] for e in comp.ledger("role_expired")] == [
        "hardware-researcher", "firmware-developer", "interface-developer"]
    assert len(comp.ledger("team_expired")) == 1
    # a goal for the same gap now has to create them again
    again = comp.compose("build a 3D printer controller", needs=PRINTER,
                         ttl_s=HOUR)
    assert len(again["created"]) == 3


def test_a_live_team_is_found_before_anything_is_composed(comp, store):
    first = comp.compose("build a 3D printer controller", needs=PRINTER,
                         ttl_s=HOUR)
    second = comp.compose("tune the 3D printer controller", needs=PRINTER,
                          ttl_s=HOUR)
    assert second["team_id"] == first["team_id"]
    assert second["created"] == []
    assert second["reused_team"] is True
    assert _count(store, "agent_temp_teams") == 1


def test_a_registered_team_that_covers_the_goal_is_reused(comp, store):
    reg = AgentRegistry(store)
    reg.register("security")
    reg.create_team("sec-team", roles=("security",))
    got = comp.compose("review the login flow for security", needs=(SECURITY,),
                       ttl_s=HOUR)
    assert got["team_id"] == "sec-team"
    assert got["reused_team"] is True
    assert _count(store, "agent_temp_teams") == 0


def test_a_need_naming_a_registered_role_is_covered_by_it(comp):
    need = RoleNeed(id="architect", title="Architect", focus="f",
                    keywords=("zzz",))
    got = comp.compose("x", needs=(need,), ttl_s=HOUR)
    assert (got["created"], got["roles"]) == ([], ["architect"])


def test_a_live_temporary_role_covers_a_later_goal(comp):
    comp.compose("build a 3D printer controller", needs=(FIRMWARE,), ttl_s=HOUR)
    got = comp.compose("port the firmware to the new board", needs=(FIRMWARE,),
                       ttl_s=HOUR)
    assert got["created"] == []
    assert got["reused"] == ["firmware-developer"]


def test_no_needs_falls_back_to_compile_team_and_creates_nothing(comp):
    got = comp.compose("fix the crash when the list is empty", ttl_s=HOUR)
    assert got["created"] == []
    assert got["roles"] == [r.id for r in roles.compile_team(
        "fix the crash when the list is empty").roles]


@pytest.mark.parametrize("ttl", [0, -5, float("nan"), float("inf"), True,
                                 8 * 24 * HOUR])
def test_a_role_without_a_sane_expiry_is_refused(comp, store, ttl):
    with pytest.raises(ComposerError):
        comp.compose("x", needs=(FIRMWARE,), ttl_s=ttl)
    assert _count(store, "agent_temp_roles") == 0


@pytest.mark.parametrize("bad", [
    RoleNeed(id="Bad Id", title="t", focus="f", keywords=("k",)),
    RoleNeed(id="ok-id", title="t", focus="", keywords=("zzz",)),
    RoleNeed(id="ok-id", title="t", focus="f" * 2000, keywords=("zzz",)),
    RoleNeed(id="ok-id", title="t", focus="f", keywords=()),
])
def test_a_malformed_need_is_refused_and_writes_nothing(comp, store, bad):
    with pytest.raises(ComposerError):
        comp.compose("x", needs=(bad,), ttl_s=HOUR)
    assert _count(store, "agent_temp_roles") == 0
    assert _count(store, "agent_temp_teams") == 0
    assert comp.ledger() == []


def test_extra_temporary_roles_reach_compile_team_and_plan_team(comp):
    comp.compose("x", needs=(FIRMWARE,), ttl_s=HOUR)
    goal = "implement the firmware flashing feature for the board"
    base = roles.compile_team(goal)
    assert "firmware-developer" not in [r.id for r in base.roles]
    live = roles.compile_team(goal, extra=comp.live_roles())
    assert "firmware-developer" in [r.id for r in live.roles]
    temp = roles.Role(id="firmware-developer", title="Firmware Developer",
                      focus="f", triggers=("firmware", "flashing", "board"))
    picked = roles.compile_team(goal, extra=(temp,))
    assert "firmware-developer" in [r.id for r in picked.roles]
    assert hermes_team.plan_team(goal, extra=(temp,)) != ()
    with pytest.raises(ValueError):
        roles.compile_team(goal, extra=(roles.BY_ID["tests"],))


# --- promotion ------------------------------------------------------------

def test_a_role_used_in_n_distinct_objectives_is_proposed_not_registered(
        comp, store):
    comp.compose("build a 3D printer controller", needs=(FIRMWARE,), ttl_s=HOUR)
    assert comp.record_use("firmware-developer", "obj-1", True) is None
    assert comp.record_use("firmware-developer", "obj-1", True) is None  # same
    proposal = comp.record_use("firmware-developer", "obj-2", True)
    assert proposal["role_id"] == "firmware-developer"
    assert proposal["status"] == "proposed"
    assert proposal["objectives"] == 2
    assert comp.proposals() == [proposal]
    assert AgentRegistry(store).get("firmware-developer") is None
    assert comp.ledger("promotion_proposed")


def test_failed_use_does_not_count_toward_promotion(comp):
    comp.compose("x", needs=(FIRMWARE,), ttl_s=HOUR)
    comp.record_use("firmware-developer", "obj-1", True)
    assert comp.record_use("firmware-developer", "obj-2", False) is None
    assert comp.proposals() == []


def test_a_proposal_survives_the_role_expiring_and_registers_only_on_approval(
        comp, clock, store):
    comp.compose("x", needs=(FIRMWARE,), ttl_s=HOUR)
    comp.record_use("firmware-developer", "obj-1", True)
    comp.record_use("firmware-developer", "obj-2", True)
    clock.now += HOUR + 1
    assert comp.live_roles() == ()
    assert [p["status"] for p in comp.proposals()] == ["proposed"]
    reg = AgentRegistry(store)
    with pytest.raises(RegistryError):
        reg.register("firmware-developer")        # not approved: still unknown
    approved = comp.approve("firmware-developer", approver="owner")
    assert approved["status"] == "approved"
    assert reg.get("firmware-developer")["role_id"] == "firmware-developer"
    assert comp.ledger("promotion_approved")[0]["detail"]["approver"] == "owner"
    # a registered role covers a later gap instead of being created again
    later = comp.compose("port the firmware", needs=(FIRMWARE,), ttl_s=HOUR)
    assert later["created"] == []
    assert later["reused"] == ["firmware-developer"]


def test_approval_needs_a_proposal_and_an_approver(comp):
    comp.compose("x", needs=(FIRMWARE,), ttl_s=HOUR)
    comp.record_use("firmware-developer", "obj-1", True)   # below N
    with pytest.raises(ComposerError):
        comp.approve("firmware-developer", approver="owner")
    comp.record_use("firmware-developer", "obj-2", True)
    with pytest.raises(ComposerError):
        comp.approve("firmware-developer", approver="")
    with pytest.raises(ComposerError):
        comp.approve("no-such-role", approver="owner")


def test_a_single_request_never_produces_a_registered_agent(comp, store):
    comp.compose("build a 3D printer controller", needs=PRINTER, ttl_s=HOUR)
    for role_id in ("hardware-researcher", "firmware-developer"):
        comp.record_use(role_id, "only-objective", True)
        comp.record_use(role_id, "only-objective", True)
    assert comp.proposals() == []
    assert AgentRegistry(store).agents() == []


def test_the_promotion_threshold_is_configured_not_fixed(store, clock):
    comp = Composer(store, clock=clock, promote_after=3)
    comp.compose("x", needs=(FIRMWARE,), ttl_s=HOUR)
    comp.record_use("firmware-developer", "o1", True)
    assert comp.record_use("firmware-developer", "o2", True) is None
    assert comp.record_use("firmware-developer", "o3", True) is not None
    with pytest.raises(ComposerError):
        Composer(store, clock=clock, promote_after=0)


def test_using_an_unknown_or_expired_role_is_refused(comp, clock):
    with pytest.raises(ComposerError):
        comp.record_use("firmware-developer", "o1", True)
    comp.compose("x", needs=(FIRMWARE,), ttl_s=HOUR)
    clock.now += HOUR + 1
    with pytest.raises(ComposerError):
        comp.record_use("firmware-developer", "o1", True)


def test_the_ledger_refuses_edits(comp, store):
    comp.compose("x", needs=(FIRMWARE,), ttl_s=HOUR)
    import sqlite3
    with pytest.raises(sqlite3.DatabaseError):
        store._conn.execute("DELETE FROM agent_team_ledger")
