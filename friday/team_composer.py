"""
FRIDAY composes a team for a goal itself, and makes only what is missing
(AGENT-004).

The order is the point:

  1. Search what is registered - the `friday.roles` catalogue, roles the owner
     approved for registration, and live temporary roles - and reuse a role
     that covers each need.
  2. Search teams - a live temporary team or a registered `AgentRegistry`
     team that already holds every needed role is returned as it is.
  3. Only for a need nothing covers, create a *temporary* role. Every
     temporary role and every temporary team carries an `expires_at`; after
     it passes they are deleted, so an idle gap costs nothing.

A temporary role that keeps succeeding across `promote_after` distinct
objectives is *proposed* for registration. It becomes registered only when
`approve` is called with an approver; a single request never registers an
agent. Every decision is appended to `agent_team_ledger`, which refuses edits
and outlives the rows it describes.

What this module does not do is decide which roles a goal needs: the planner
supplies `RoleNeed`s (title, focus, the words that would mark a goal as
needing it). With no needs given it falls back to `roles.compile_team`.
Planner text is untrusted data: ids, lengths and counts are bounded and
nothing in a need is ever evaluated.
"""

from __future__ import annotations

import json
import logging
import math
import re
import time
import uuid
from dataclasses import dataclass
from typing import Callable

from friday import roles
from friday.agent_registry import AgentRegistry
from friday.store import Store, now_iso

logger = logging.getLogger("friday-agent")

DEFAULT_TTL_S = 24 * 3600.0
MAX_TTL_S = 7 * 24 * 3600.0
DEFAULT_PROMOTE_AFTER = 3
MAX_NEEDS = 8

_ID = re.compile(r"^[a-z][a-z0-9-]{1,39}$")
_MAX_TITLE, _MAX_FOCUS, _MAX_DELIVERS = 80, 400, 200
_MAX_KEYWORDS, _MAX_KEYWORD = 8, 40
_MAX_GOAL = 500

PROPOSED = "proposed"
APPROVED = "approved"


class ComposerError(ValueError):
    """The composer refused a call that would break an invariant."""


@dataclass(frozen=True)
class RoleNeed:
    """One capability a goal needs, as the planner describes it."""

    id: str
    title: str
    focus: str
    #: Lower-case words that mark a goal (or another role) as this capability.
    keywords: tuple[str, ...] = ()
    delivers: str = ""


class Composer:
    def __init__(self, store: Store, *, clock: Callable[[], float] = time.time,
                 promote_after: int = DEFAULT_PROMOTE_AFTER) -> None:
        if (not isinstance(promote_after, int) or isinstance(promote_after, bool)
                or promote_after < 1):
            raise ComposerError("promote_after is a whole number of objectives >= 1")
        self.store = store
        self._clock = clock
        self.promote_after = promote_after

    # -- composing ---------------------------------------------------------

    def compose(self, goal: str, *, needs: tuple[RoleNeed, ...] = (),
                files: int = 0, ttl_s: float = DEFAULT_TTL_S,
                objective_id: str = "") -> dict:
        """
        A team for `goal`: reuse first, create only the gap, all of it expiring.

        Returns team_id, roles (the team's members), created (new temporary
        role ids), reused (existing role ids that covered a need),
        reused_team and expires_at.
        """
        self._check_ttl(ttl_s)
        if not isinstance(goal, str) or not goal.strip():
            raise ComposerError("a team is composed for a goal")
        needs = tuple(needs)
        self._check_needs(needs)
        self.expire()

        known = self._known_roles()
        if needs:
            members, created, reused = self._cover(needs, known)
        else:
            chosen = roles.compile_team(goal, files=files,
                                        extra=self.live_roles())
            members = [r.id for r in chosen.roles]
            created, reused = [], list(members)

        if not created:
            found = self._find_team(members)
            if found:
                return {"team_id": found["team_id"], "roles": found["roles"],
                        "created": [], "reused": reused, "reused_team": True,
                        "expires_at": found.get("expires_at")}

        expires_at = self._clock() + float(ttl_s)
        team_id = f"tmp-{uuid.uuid4().hex[:10]}"
        with self.store._tx() as conn:
            for need in created:
                conn.execute(
                    "INSERT INTO agent_temp_roles (role_id, title, focus, "
                    "triggers, delivers, created_at, expires_at) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (need.id, need.title, need.focus,
                     json.dumps(self._keywords(need)), need.delivers,
                     now_iso(), expires_at))
                self._log(conn, "role_created", need.id, objective_id,
                          {"title": need.title, "expires_at": expires_at,
                           "team_id": team_id})
            conn.execute(
                "INSERT INTO agent_temp_teams (team_id, goal, roles, "
                "created_at, expires_at) VALUES (?,?,?,?,?)",
                (team_id, goal[:_MAX_GOAL], json.dumps(members), now_iso(),
                 expires_at))
            self._log(conn, "team_composed", team_id, objective_id, {
                "goal": goal[:_MAX_GOAL], "roles": members,
                "created": [n.id for n in created], "reused": reused,
                "expires_at": expires_at})
        logger.info("team_composer.compose team=%s created=%s reused=%s",
                    team_id, [n.id for n in created], reused)
        return {"team_id": team_id, "roles": members,
                "created": [n.id for n in created], "reused": reused,
                "reused_team": False, "expires_at": expires_at}

    # -- expiry ------------------------------------------------------------

    def expire(self) -> dict:
        """
        Delete every temporary role and team whose time has passed. A team
        also goes with any of its temporary roles. The ledger keeps the record.
        """
        now = self._clock()
        with self.store._tx() as conn:
            dead_roles = [r["role_id"] for r in conn.execute(
                "SELECT role_id FROM agent_temp_roles WHERE expires_at<=? "
                "ORDER BY rowid", (now,))]
            dead_teams = []
            for team in conn.execute(
                    "SELECT team_id, roles, expires_at FROM agent_temp_teams "
                    "ORDER BY rowid").fetchall():
                members = json.loads(team["roles"])
                if team["expires_at"] <= now or any(m in dead_roles
                                                    for m in members):
                    dead_teams.append(team["team_id"])
            for role_id in dead_roles:
                conn.execute("DELETE FROM agent_temp_roles WHERE role_id=?",
                             (role_id,))
                self._log(conn, "role_expired", role_id, "", {})
            for team_id in dead_teams:
                conn.execute("DELETE FROM agent_temp_teams WHERE team_id=?",
                             (team_id,))
                self._log(conn, "team_expired", team_id, "", {})
        return {"roles": dead_roles, "teams": dead_teams}

    # -- reads -------------------------------------------------------------

    def temp_roles(self) -> list[dict]:
        self.expire()
        return [dict(r, triggers=json.loads(r["triggers"]))
                for r in self.store._conn.execute(
                    "SELECT * FROM agent_temp_roles ORDER BY rowid")]

    def live_roles(self) -> tuple[roles.Role, ...]:
        """Roles beyond the catalogue that may staff a team now (`extra=`)."""
        self.expire()
        out = [self._role(r["role_id"], r["title"], r["focus"],
                          json.loads(r["triggers"]), r["delivers"])
               for r in self.store._conn.execute(
                   "SELECT * FROM agent_temp_roles ORDER BY rowid")]
        for p in self.proposals(APPROVED):
            d = p["definition"]
            out.append(self._role(p["role_id"], d["title"], d["focus"],
                                  d["triggers"], d["delivers"]))
        return tuple(out)

    def teams(self) -> list[dict]:
        self.expire()
        return [{"team_id": r["team_id"], "goal": r["goal"],
                 "roles": json.loads(r["roles"]),
                 "expires_at": r["expires_at"]}
                for r in self.store._conn.execute(
                    "SELECT * FROM agent_temp_teams ORDER BY rowid")]

    def ledger(self, kind: str = "") -> list[dict]:
        sql, args = "SELECT * FROM agent_team_ledger", ()
        if kind:
            sql, args = sql + " WHERE kind=?", (kind,)
        return [dict(r, detail=json.loads(r["detail"]))
                for r in self.store._conn.execute(sql + " ORDER BY event_id", args)]

    # -- promotion ---------------------------------------------------------

    def record_use(self, role_id: str, objective_id: str,
                   succeeded: bool) -> dict | None:
        """
        Note that a live temporary role was used in an objective. Returns the
        promotion proposal if this use is the one that earned it.
        """
        self.expire()
        if not isinstance(objective_id, str) or not objective_id.strip():
            raise ComposerError("a use belongs to an objective_id")
        if not isinstance(succeeded, bool):
            raise ComposerError("succeeded is true or false")
        row = self.store._conn.execute(
            "SELECT * FROM agent_temp_roles WHERE role_id=?",
            (role_id,)).fetchone()
        if row is None:
            raise ComposerError(
                f"{role_id!r} is not a live temporary role (unknown or expired)")
        with self.store._tx() as conn:
            self._log(conn, "role_used", role_id, objective_id,
                      {"succeeded": succeeded})
            wins = {e["objective_id"] for e in conn.execute(
                "SELECT objective_id, detail FROM agent_team_ledger "
                "WHERE kind='role_used' AND subject=?", (role_id,)).fetchall()
                if json.loads(e["detail"])["succeeded"]}
            existing = conn.execute(
                "SELECT status FROM agent_promotions WHERE role_id=?",
                (role_id,)).fetchone()
            if existing is not None:
                if existing["status"] == PROPOSED:
                    conn.execute("UPDATE agent_promotions SET objectives=? "
                                 "WHERE role_id=?", (len(wins), role_id))
                return None
            if len(wins) < self.promote_after:
                return None
            definition = {"title": row["title"], "focus": row["focus"],
                          "triggers": json.loads(row["triggers"]),
                          "delivers": row["delivers"]}
            conn.execute(
                "INSERT INTO agent_promotions (role_id, definition, objectives, "
                "status, proposed_at) VALUES (?,?,?,?,?)",
                (role_id, json.dumps(definition), len(wins), PROPOSED,
                 now_iso()))
            self._log(conn, "promotion_proposed", role_id, objective_id,
                      {"objectives": len(wins), "threshold": self.promote_after})
        return self._proposal(role_id)

    def proposals(self, status: str = "") -> list[dict]:
        sql, args = "SELECT role_id FROM agent_promotions", ()
        if status:
            sql, args = sql + " WHERE status=?", (status,)
        return [self._proposal(r["role_id"]) for r in
                self.store._conn.execute(sql + " ORDER BY rowid", args)]

    def approve(self, role_id: str, *, approver: str) -> dict:
        """Register a proposed role. Only a named approver can do this."""
        if not isinstance(approver, str) or not approver.strip():
            raise ComposerError("approval needs a named approver")
        self.expire()
        prop = self._proposal(role_id)
        if prop is None or prop["status"] != PROPOSED:
            raise ComposerError(f"{role_id!r} has no open promotion proposal")
        if prop["objectives"] < self.promote_after:
            raise ComposerError(
                f"{role_id!r} has {prop['objectives']} successful objectives; "
                f"{self.promote_after} are needed")
        with self.store._tx() as conn:
            conn.execute(
                "UPDATE agent_promotions SET status=?, decided_at=?, approver=? "
                "WHERE role_id=?", (APPROVED, now_iso(), approver, role_id))
        try:
            AgentRegistry(self.store).register(role_id)
        except Exception:
            with self.store._tx() as conn:
                conn.execute(
                    "UPDATE agent_promotions SET status=?, decided_at=NULL, "
                    "approver='' WHERE role_id=?", (PROPOSED, role_id))
            raise
        with self.store._tx() as conn:
            conn.execute("DELETE FROM agent_temp_roles WHERE role_id=?",
                         (role_id,))
            self._log(conn, "promotion_approved", role_id, "",
                      {"approver": approver})
        return self._proposal(role_id)

    # -- internals ---------------------------------------------------------

    def _known_roles(self) -> list[tuple[str, tuple[str, ...]]]:
        """(role_id, triggers) for every role that can cover a need, in order."""
        known = [(r.id, tuple(r.triggers)) for r in roles.CATALOGUE]
        known += [(r.id, tuple(r.triggers)) for r in self.live_roles()]
        return known

    def _cover(self, needs, known):
        members: list[str] = []
        created: list[RoleNeed] = []
        reused: list[str] = []
        for need in needs:
            hit = next((rid for rid, _ in known if rid == need.id), None)
            if hit is None:
                words = set(self._keywords(need))
                hit = next((rid for rid, trig in known
                            if words & set(trig)), None)
            if hit is not None:
                if hit not in members:
                    members.append(hit)
                if hit not in reused:
                    reused.append(hit)
            elif need.id not in members:
                members.append(need.id)
                created.append(need)
        return members, created, reused

    def _find_team(self, members: list[str]) -> dict | None:
        wanted = set(members)
        for team in self.teams():
            if wanted <= set(team["roles"]):
                return team
        for team in AgentRegistry(self.store).teams():
            if wanted <= set(team["roles"]):
                return dict(team, expires_at=None)
        return None

    def _proposal(self, role_id: str) -> dict | None:
        row = self.store._conn.execute(
            "SELECT * FROM agent_promotions WHERE role_id=?",
            (role_id,)).fetchone()
        if row is None:
            return None
        return dict(row, definition=json.loads(row["definition"]))

    @staticmethod
    def _role(role_id, title, focus, triggers, delivers) -> roles.Role:
        return roles.Role(id=role_id, title=title, focus=focus,
                          triggers=tuple(triggers), delivers=delivers,
                          cost=len(f"[{title}] {focus}"))

    @staticmethod
    def _keywords(need: RoleNeed) -> list[str]:
        return [k.strip().lower() for k in need.keywords]

    @staticmethod
    def _log(conn, kind, subject, objective_id, detail) -> None:
        conn.execute(
            "INSERT INTO agent_team_ledger (kind, subject, objective_id, "
            "detail, created_at) VALUES (?,?,?,?,?)",
            (kind, subject, objective_id, json.dumps(detail), now_iso()))

    @staticmethod
    def _check_ttl(ttl_s) -> None:
        if (isinstance(ttl_s, bool) or not isinstance(ttl_s, (int, float))
                or not math.isfinite(ttl_s) or not 0 < ttl_s <= MAX_TTL_S):
            raise ComposerError(
                f"ttl_s must be a finite number of seconds in (0, {MAX_TTL_S:.0f}]"
                " - every temporary role and team expires")

    @staticmethod
    def _check_needs(needs) -> None:
        if len(needs) > MAX_NEEDS:
            raise ComposerError(f"at most {MAX_NEEDS} needs per goal")
        seen = set()
        for need in needs:
            if not isinstance(need, RoleNeed):
                raise ComposerError(f"a need is a RoleNeed, not {type(need).__name__}")
            if not isinstance(need.id, str) or not _ID.match(need.id):
                raise ComposerError(f"need id {need.id!r} must match {_ID.pattern}")
            if need.id in seen:
                raise ComposerError(f"need {need.id!r} is listed twice")
            seen.add(need.id)
            for name, value, low, high in (
                    ("title", need.title, 1, _MAX_TITLE),
                    ("focus", need.focus, 1, _MAX_FOCUS),
                    ("delivers", need.delivers, 0, _MAX_DELIVERS)):
                if not isinstance(value, str) or not low <= len(value.strip()) \
                        or len(value) > high:
                    raise ComposerError(
                        f"need {need.id!r}: {name} must be {low}-{high} characters")
            words = need.keywords
            if (not isinstance(words, (tuple, list)) or not words
                    or len(words) > _MAX_KEYWORDS
                    or not all(isinstance(k, str) and 2 <= len(k.strip()) <= _MAX_KEYWORD
                               for k in words)):
                raise ComposerError(
                    f"need {need.id!r}: 1-{_MAX_KEYWORDS} keywords of 2-"
                    f"{_MAX_KEYWORD} characters")
