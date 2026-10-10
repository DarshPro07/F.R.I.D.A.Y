"""
One registry for the three things an agent can be (AGENT-002).

  Registered Agent  a long-lived role (`friday.roles.Role`) plus a personality,
                    skills, permissions and a preferred model strategy. It is
                    a row. It costs nothing while idle: registering starts no
                    process and adds no ledger run.
  Agent Instance    one temporary worker made from a registered agent. It
                    binds exactly one model route, skill set, scoped
                    workspace and budget, and is claimed in the AGENT-001
                    ledger under its role. `instance_id` is the ledger
                    `run_id`, so the ledger and the registry name one thing.
  Team              a reusable group of registered roles with workflow
                    relationships between them. Extending a team adds
                    membership and edges; it never edits a role.

The role text stays in `friday.roles` (the one catalogue); the registry only
holds what a role does not: how this agent behaves and what it may spend.
Which route to pick is the routing policy's job; the instance takes it as an
input, as `specialist.compose` does.
"""

from __future__ import annotations

import json
import math
import uuid
from pathlib import PurePath

from friday import roles, specialist
from friday.agent_ledger import CANCELLED, AgentLedger, LedgerError
from friday.store import Store, now_iso

INSTANCE_BOUND = "instance_bound"


class RegistryError(ValueError):
    """The registry refused a write that would leave it inconsistent."""


class AgentRegistry:
    def __init__(self, store: Store) -> None:
        self.store = store

    # -- registered agents -------------------------------------------------

    def register(self, role_id: str, *, personality: str = "",
                 skills: tuple[str, ...] = (), permissions: tuple[str, ...] = (),
                 model_strategy: str = "") -> dict:
        """Register (or update) a role as a long-lived agent. Starts nothing."""
        if role_id not in roles.BY_ID:
            raise RegistryError(
                f"unknown role {role_id!r}; roles: {sorted(roles.BY_ID)}")
        with self.store._tx() as conn:
            conn.execute(
                "INSERT INTO agent_registry (role_id, personality, skills, "
                "permissions, model_strategy, registered_at) VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(role_id) DO UPDATE SET personality=excluded."
                "personality, skills=excluded.skills, permissions=excluded."
                "permissions, model_strategy=excluded.model_strategy",
                (role_id, personality, json.dumps(list(skills)),
                 json.dumps(list(permissions)), model_strategy, now_iso()))
        return self.get(role_id)

    def get(self, role_id: str) -> dict | None:
        row = self.store._conn.execute(
            "SELECT * FROM agent_registry WHERE role_id=?", (role_id,)).fetchone()
        return self._agent(row) if row else None

    def agents(self) -> list[dict]:
        return [self._agent(r) for r in self.store._conn.execute(
            "SELECT * FROM agent_registry ORDER BY rowid")]

    # -- instances ---------------------------------------------------------

    def create_instance(self, role_id: str, *, model_route: str,
                        executor_id: str, workspace: str, budget_usd: float,
                        skills: tuple[str, ...] | None = None,
                        objective: str = "", team_id: str = "",
                        allowed_tools: tuple[str, ...] = ()) -> dict:
        """
        Make one worker from a registered agent and claim it in the ledger.

        Every binding is required: a worker with no route, workspace or budget
        is not bounded, so nothing is written for one. `skills` defaults to
        the registered agent's. The ledger run is a claim (STARTING); starting
        the worker is the executor's job.
        """
        agent = self.get(role_id)
        if agent is None:
            raise RegistryError(f"role {role_id!r} is not registered")
        if not model_route:
            raise RegistryError("an instance needs a model_route")
        if not workspace or not isinstance(workspace, str):
            raise RegistryError("an instance needs a workspace")
        if PurePath(workspace).parent == PurePath(workspace):
            raise RegistryError(
                f"workspace {workspace!r} is a filesystem root, not a scope")
        if (not isinstance(budget_usd, (int, float)) or isinstance(budget_usd, bool)
                or not math.isfinite(budget_usd) or not budget_usd > 0):
            raise RegistryError(
                "an instance needs a finite budget_usd above zero")
        if team_id:
            team = self.team(team_id)
            if team is None:
                raise RegistryError(f"unknown team {team_id!r}")
            if role_id not in team["roles"]:
                raise RegistryError(f"role {role_id!r} is not on team {team_id!r}")
        bound_skills = list(agent["skills"] if skills is None else skills)
        try:
            spec = specialist.compose(
                role_id, executor_id=executor_id, model_route=model_route,
                skills=tuple(bound_skills), allowed_tools=tuple(allowed_tools),
                workspace_policy=specialist.WorkspacePolicy(
                    allowed_paths=(workspace,)))
        except specialist.SpecialistError as exc:
            raise RegistryError(str(exc)) from exc
        instance_id = f"inst-{uuid.uuid4().hex[:12]}"
        ledger = AgentLedger(self.store)
        try:
            specialist.record(ledger, spec, run_id=instance_id,
                              task=objective or role_id, workspace=workspace)
        except LedgerError as exc:
            raise RegistryError(str(exc)) from exc
        try:
            with self.store._tx() as conn:
                conn.execute(
                    "INSERT INTO agent_instances (instance_id, role_id, "
                    "team_id, executor_id, model_route, skills, workspace, "
                    "budget_usd, objective, created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (instance_id, role_id, team_id, executor_id, model_route,
                     json.dumps(bound_skills), workspace, float(budget_usd),
                     objective, now_iso()))
            ledger.event(instance_id, INSTANCE_BOUND, json.dumps({
                "instance_id": instance_id, "role_id": role_id,
                "team_id": team_id, "budget_usd": float(budget_usd)}),
                [f"agent_instances:{instance_id}"])
        except Exception as exc:
            # The claim is already in the ledger; do not leave it STARTING
            # with no instance row behind it.
            if self.instance(instance_id) is None:
                ledger.finish(instance_id, CANCELLED,
                              [f"agent_runs:{instance_id}"],
                              f"instance not recorded: {exc}")
            raise
        return self.instance(instance_id)

    def instance(self, instance_id: str) -> dict | None:
        row = self.store._conn.execute(
            "SELECT * FROM agent_instances WHERE instance_id=?",
            (instance_id,)).fetchone()
        return self._instance(row) if row else None

    def instances(self, role_id: str = "") -> list[dict]:
        sql, args = "SELECT * FROM agent_instances", ()
        if role_id:
            sql, args = sql + " WHERE role_id=?", (role_id,)
        return [self._instance(r) for r in
                self.store._conn.execute(sql + " ORDER BY rowid", args)]

    # -- teams -------------------------------------------------------------

    def create_team(self, team_id: str, *, roles: tuple[str, ...] = (),
                    relationships: tuple[tuple[str, str, str], ...] = (),
                    because: dict[str, str] | None = None) -> dict:
        """
        Make a team. Everything is checked first and written in one
        transaction, so a refused call leaves no team behind.
        """
        if not team_id:
            raise RegistryError("a team needs a team_id")
        if self.team(team_id) is not None:
            raise RegistryError(f"team {team_id!r} already exists")
        self._check_registered(roles)
        edges = self._check_edges(team_id, set(roles), relationships)
        with self.store._tx() as conn:
            conn.execute("INSERT INTO agent_teams (team_id, created_at) "
                         "VALUES (?,?)", (team_id, now_iso()))
            self._write_team(conn, team_id, roles, edges, because or {})
        return self.team(team_id)

    def team_from_goal(self, team_id: str, goal: str, *, files: int = 0,
                       budget: int = 1200) -> dict:
        """
        Make a team out of `friday.roles.compile_team(goal)`.

        The members are the roles compile_team chose, in its order, each with
        its reason. A chosen role that is not registered yet is registered
        with defaults; an already registered one is left as it is.
        """
        chosen = roles.compile_team(goal, files=files, budget=budget)
        ids = tuple(role.id for role in chosen.roles)
        if not ids:
            raise RegistryError("compile_team chose no roles for this goal")
        if not team_id:
            raise RegistryError("a team needs a team_id")
        if self.team(team_id) is not None:
            raise RegistryError(f"team {team_id!r} already exists")
        for role_id in ids:
            if self.get(role_id) is None:
                self.register(role_id)
        return self.create_team(team_id, roles=ids, because=dict(chosen.because))

    def extend_team(self, team_id: str, *, roles: tuple[str, ...] = (),
                    relationships: tuple[tuple[str, str, str], ...] = ()) -> dict:
        """Add members and edges to a team. Roles themselves are not touched."""
        current = self.team(team_id)
        if current is None:
            raise RegistryError(f"unknown team {team_id!r}")
        self._check_registered(roles)
        edges = self._check_edges(
            team_id, set(current["roles"]) | set(roles), relationships)
        with self.store._tx() as conn:
            self._write_team(conn, team_id, roles, edges, {})
        return self.team(team_id)

    def team(self, team_id: str) -> dict | None:
        conn = self.store._conn
        if conn.execute("SELECT 1 FROM agent_teams WHERE team_id=?",
                        (team_id,)).fetchone() is None:
            return None
        members = conn.execute(
            "SELECT role_id, reason FROM agent_team_roles WHERE team_id=? "
            "ORDER BY rowid", (team_id,)).fetchall()
        return {
            "team_id": team_id,
            "roles": [r["role_id"] for r in members],
            "because": {r["role_id"]: r["reason"] for r in members
                        if r["reason"]},
            "relationships": [
                {"from": r["from_role"], "to": r["to_role"], "kind": r["kind"]}
                for r in conn.execute(
                    "SELECT * FROM agent_team_edges WHERE team_id=? "
                    "ORDER BY rowid", (team_id,))],
        }

    def teams(self) -> list[dict]:
        return [self.team(r["team_id"]) for r in self.store._conn.execute(
            "SELECT team_id FROM agent_teams ORDER BY rowid")]

    # -- internals ---------------------------------------------------------

    def _check_registered(self, role_ids: tuple[str, ...]) -> None:
        for role_id in role_ids:
            if self.get(role_id) is None:
                raise RegistryError(f"role {role_id!r} is not registered")

    @staticmethod
    def _check_edges(team_id: str, members: set[str],
                     relationships) -> list[tuple[str, str, str]]:
        edges = []
        for edge in relationships:
            if not isinstance(edge, (tuple, list)) or len(edge) != 3 or not all(
                    isinstance(part, str) and part for part in edge):
                raise RegistryError(
                    f"a relationship is (from, to, kind), all non-empty: {edge!r}")
            src, dst, kind = edge
            for end in (src, dst):
                if end not in members:
                    raise RegistryError(
                        f"role {end!r} is not on team {team_id!r}")
            edges.append((src, dst, kind))
        return edges

    @staticmethod
    def _write_team(conn, team_id: str, role_ids, edges, because) -> None:
        for role_id in role_ids:
            conn.execute("INSERT OR IGNORE INTO agent_team_roles "
                         "(team_id, role_id, reason) VALUES (?,?,?)",
                         (team_id, role_id, because.get(role_id, "")))
        for src, dst, kind in edges:
            conn.execute("INSERT OR IGNORE INTO agent_team_edges "
                         "(team_id, from_role, to_role, kind) VALUES (?,?,?,?)",
                         (team_id, src, dst, kind))

    @staticmethod
    def _agent(row) -> dict:
        got = dict(row)
        for key in ("skills", "permissions"):
            got[key] = json.loads(got[key])
        return got

    @staticmethod
    def _instance(row) -> dict:
        got = dict(row)
        got["skills"] = json.loads(got["skills"])
        return got
