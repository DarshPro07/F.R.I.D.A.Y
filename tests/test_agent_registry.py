"""
AGENT-002: Registered Agent, Agent Instance and Team in one durable registry.

The claims: registering a role costs nothing (no process, no ledger run); an
instance binds exactly one route, skill set, workspace and budget and is
recorded in the ledger under its role; one role can have instances on
different routes; a team is extended without touching the roles; all of it
survives a restart.
"""

from __future__ import annotations

import pytest

from friday import roles
from friday.agent_ledger import AgentLedger
from friday.agent_registry import AgentRegistry, RegistryError
from friday.store import Store

SONNET = "anthropic/claude-sonnet/5.5/subscription"
HAIKU = "anthropic/claude-haiku/4.5/subscription"
EXECUTOR = "claude"


@pytest.fixture
def db(tmp_path):
    return tmp_path / "registry.sqlite3"


@pytest.fixture
def store(db):
    s = Store(db)
    yield s
    s.close()


@pytest.fixture
def reg(store):
    return AgentRegistry(store)


def _runs(store):
    return store._conn.execute("SELECT COUNT(*) FROM agent_runs").fetchone()[0]


def _register(reg, role_id="architect"):
    return reg.register(role_id, personality="terse, skeptical",
                        skills=("design-review",),
                        permissions=("read_repo",),
                        model_strategy="cheapest capable")


def _instance(reg, route=SONNET, workspace="/w/a", budget=1.0, **extra):
    return reg.create_instance("architect", model_route=route,
                               executor_id=EXECUTOR, workspace=workspace,
                               budget_usd=budget, **extra)


def test_registering_a_role_starts_no_process_and_adds_no_ledger_run(reg, store):
    agent = _register(reg)
    assert agent["role_id"] == "architect"
    assert (agent["personality"], agent["model_strategy"]) == \
        ("terse, skeptical", "cheapest capable")
    assert agent["skills"] == ["design-review"]
    assert agent["permissions"] == ["read_repo"]
    assert _runs(store) == 0
    count = store._conn.execute("SELECT COUNT(*) FROM executor_runs").fetchone()[0]
    assert count == 0


def test_an_unknown_role_is_refused(reg):
    with pytest.raises(RegistryError, match="unknown role"):
        reg.register("no-such-role")


def test_an_instance_binds_one_route_skills_workspace_budget_and_is_ledgered(reg, store):
    _register(reg)
    inst = _instance(reg, skills=("design-review", "threat-model"),
                     budget=2.5, objective="shape the registry")
    assert inst["role_id"] == "architect"
    assert inst["model_route"] == SONNET
    assert inst["skills"] == ["design-review", "threat-model"]
    assert (inst["workspace"], inst["budget_usd"]) == ("/w/a", 2.5)
    assert inst["objective"] == "shape the registry"
    ledger = AgentLedger(store)
    run = ledger.run(inst["instance_id"])
    assert run["role_id"] == "architect"
    assert run["model_route"] == SONNET
    assert run["workspace"] == "/w/a"
    assert run["status"] == "STARTING"   # a claim, never a running worker
    assert ledger.active() == []


@pytest.mark.parametrize("missing", ["model_route", "workspace", "budget_usd"])
def test_an_instance_missing_a_binding_is_refused(reg, store, missing):
    _register(reg)
    args = dict(model_route=SONNET, workspace="/w/a", budget_usd=1.0)
    args[missing] = 0 if missing == "budget_usd" else ""
    with pytest.raises(RegistryError, match=missing):
        reg.create_instance("architect", executor_id=EXECUTOR, **args)
    assert _runs(store) == 0
    assert reg.instances() == []


def test_an_unregistered_role_cannot_be_instantiated(reg, store):
    with pytest.raises(RegistryError, match="not registered"):
        _instance(reg)
    assert _runs(store) == 0


def test_one_role_yields_two_instances_on_different_routes(reg, store):
    _register(reg)
    a = _instance(reg, SONNET, "/w/1")
    b = _instance(reg, HAIKU, "/w/2", 0.5)
    assert a["instance_id"] != b["instance_id"]
    by_role = AgentLedger(store).by_role("architect")
    assert {r["run_id"]: r["model_route"] for r in by_role} == {
        a["instance_id"]: SONNET, b["instance_id"]: HAIKU}
    assert sorted(i["instance_id"] for i in reg.instances("architect")) == \
        sorted([a["instance_id"], b["instance_id"]])


def test_a_team_lists_roles_and_relationships_and_extends_without_editing_roles(reg):
    _register(reg, "architect")
    _register(reg, "implementer")
    reg.create_team("build", roles=("architect", "implementer"),
                    relationships=(("architect", "implementer", "hands_off_to"),))
    before = {r: reg.get(r) for r in ("architect", "implementer")}
    _register(reg, "reviewer")
    reviewer = reg.get("reviewer")
    reg.extend_team("build", roles=("reviewer",),
                    relationships=(("implementer", "reviewer", "reviewed_by"),))
    team = reg.team("build")
    assert team["roles"] == ["architect", "implementer", "reviewer"]
    assert team["relationships"] == [
        {"from": "architect", "to": "implementer", "kind": "hands_off_to"},
        {"from": "implementer", "to": "reviewer", "kind": "reviewed_by"}]
    assert {r: reg.get(r) for r in ("architect", "implementer")} == before
    assert reg.get("reviewer") == reviewer


def test_a_team_edge_must_join_roles_on_the_team(reg):
    _register(reg, "architect")
    reg.create_team("t", roles=("architect",))
    with pytest.raises(RegistryError, match="not on team"):
        reg.extend_team("t", relationships=(("architect", "reviewer", "x"),))


def test_a_team_member_must_be_registered(reg):
    with pytest.raises(RegistryError, match="not registered"):
        reg.create_team("t", roles=("architect",))
    assert reg.teams() == []


def test_an_instance_is_attributed_to_its_team(reg, store):
    _register(reg)
    reg.create_team("t", roles=("architect",))
    inst = _instance(reg, team_id="t")
    assert inst["team_id"] == "t"
    kinds = [e["kind"] for e in AgentLedger(store).events(inst["instance_id"])]
    assert "instance_bound" in kinds


def test_an_instance_for_a_role_off_the_team_is_refused(reg):
    _register(reg, "architect")
    _register(reg, "implementer")
    reg.create_team("t", roles=("implementer",))
    with pytest.raises(RegistryError, match="not on team"):
        _instance(reg, team_id="t")


def test_the_registry_persists_across_a_restart(db):
    s1 = Store(db)
    r1 = AgentRegistry(s1)
    _register(r1)
    _register(r1, "implementer")
    r1.create_team("t", roles=("architect", "implementer"),
                   relationships=(("architect", "implementer", "hands_off_to"),))
    inst = _instance(r1, team_id="t")
    s1.close()
    s2 = Store(db)
    try:
        r2 = AgentRegistry(s2)
        assert r2.get("architect")["personality"] == "terse, skeptical"
        assert r2.team("t")["roles"] == ["architect", "implementer"]
        assert r2.instance(inst["instance_id"])["model_route"] == SONNET
        assert AgentLedger(s2).run(inst["instance_id"])["role_id"] == "architect"
    finally:
        s2.close()


def test_a_team_from_a_goal_lists_exactly_what_compile_team_chose(reg):
    goal = "implement the login handler and review it for security"
    chosen = roles.compile_team(goal)
    team = reg.team_from_goal("g", goal)
    assert chosen.roles and team["roles"] == [r.id for r in chosen.roles]
    assert team["because"] == dict(chosen.because)
    for role_id in team["roles"]:
        assert reg.get(role_id) is not None


def test_team_from_goal_keeps_an_existing_registration(reg):
    reg.register("implementer", personality="mine", skills=("x",))
    reg.team_from_goal("g", "implement the login handler")
    assert reg.get("implementer")["personality"] == "mine"


def test_the_registry_defines_no_role_of_its_own(reg, monkeypatch):
    extra = roles.Role(id="zz-extra", title="Extra", focus="f")
    with pytest.raises(RegistryError, match="unknown role"):
        reg.register("zz-extra")
    monkeypatch.setitem(roles.BY_ID, "zz-extra", extra)
    assert reg.register("zz-extra")["role_id"] == "zz-extra"
    assert not hasattr(reg, "CATALOGUE")


@pytest.mark.parametrize("edges", [
    (("architect", "implementer", "x"),),         # implementer not on team
    (("architect", "architect", ""),),            # empty kind
    (("architect", "architect"),),                # not a triple
    (("architect", None, "x"),),                  # not a string
])
def test_a_refused_create_team_leaves_no_team_and_a_retry_succeeds(reg, edges):
    _register(reg)
    _register(reg, "implementer")
    with pytest.raises(RegistryError):
        reg.create_team("t", roles=("architect",), relationships=edges)
    assert reg.team("t") is None
    assert reg.teams() == []
    ok = reg.create_team("t", roles=("architect",))
    assert ok["roles"] == ["architect"]


def test_a_refused_extend_team_changes_nothing(reg):
    _register(reg)
    _register(reg, "implementer")
    reg.create_team("t", roles=("architect",))
    with pytest.raises(RegistryError):
        reg.extend_team("t", roles=("implementer",),
                        relationships=(("architect", "ghost", "x"),))
    assert reg.team("t")["roles"] == ["architect"]


@pytest.mark.parametrize("budget", [float("inf"), float("nan"), -1, 0, True])
def test_an_unbounded_budget_is_refused(reg, store, budget):
    _register(reg)
    with pytest.raises(RegistryError, match="budget"):
        _instance(reg, budget=budget)
    assert _runs(store) == 0


@pytest.mark.parametrize("workspace", ["/"])
def test_a_filesystem_root_is_not_a_workspace(reg, store, workspace):
    _register(reg)
    with pytest.raises(RegistryError, match="root"):
        _instance(reg, workspace=workspace)
    assert _runs(store) == 0


def test_a_failed_instance_write_does_not_leave_a_live_claim(reg, store, monkeypatch):
    import friday.agent_registry as mod

    class Boom:
        def dumps(self, value, *a, **k):
            if isinstance(value, list):
                raise RuntimeError("disk full")
            return json_real.dumps(value, *a, **k)

        def __getattr__(self, name):
            return getattr(json_real, name)

    import json as json_real
    _register(reg)
    monkeypatch.setattr(mod, "json", Boom())
    with pytest.raises(RuntimeError, match="disk full"):
        _instance(reg)
    monkeypatch.undo()
    rows = store._conn.execute("SELECT status FROM agent_runs").fetchall()
    assert [r["status"] for r in rows] == ["CANCELLED"]
    assert reg.instances() == []
