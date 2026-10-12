"""
Codebase-aware skill memory (INTAKE-006): per-fact knowledge.

`friday.skills.dependencies` tracks what a whole skill depends on. A generated
coding skill is really a set of project facts ("the launcher builds with
PyInstaller 6", "settings live in friday/config.py"), each true only while the
code it was read from stays the same. Here every fact is stored with

    the evidence that verified it (a passing test or a read-back),
    the file, glob or package version it depends on, with its fingerprint,
    the commit and codebase it was verified at.

`sweep()` re-fingerprints the active facts. A fact whose dependency changed
becomes NEEDS_REVALIDATION and its skill is marked the same way; every fact
and skill that does not depend on the change stays ACTIVE. A fact returns only
through `revalidate_fact()` with new passing evidence, which re-baselines its
fingerprints. A skill an intake produced also records the upstream commit and
the package versions it was verified against (`record_intake`).

Facts are tied to a skill by name in the skill registry's own database.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from friday.skills import contracts as K
from friday.skills import dependencies as D

FACT_ACTIVE = "ACTIVE"
FACT_STALE = "NEEDS_REVALIDATION"

EVIDENCE_KINDS = ("test", "readback")

SCHEMA = """
CREATE TABLE IF NOT EXISTS skill_facts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    skill           TEXT NOT NULL,
    fact            TEXT NOT NULL,
    status          TEXT NOT NULL,
    stale_reason    TEXT NOT NULL DEFAULT '',
    evidence_kind   TEXT NOT NULL,
    evidence_ref    TEXT NOT NULL,
    verified_commit TEXT NOT NULL DEFAULT '',
    codebase_id     TEXT NOT NULL DEFAULT '',
    verified_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS skill_facts_by_skill ON skill_facts (skill, status);
CREATE TABLE IF NOT EXISTS skill_fact_dependencies (
    fact_id     INTEGER NOT NULL,
    kind        TEXT NOT NULL,
    target      TEXT NOT NULL,
    fingerprint TEXT NOT NULL DEFAULT '',
    UNIQUE(fact_id, kind, target)
);
CREATE TABLE IF NOT EXISTS skill_intake_basis (
    skill           TEXT PRIMARY KEY,
    upstream_commit TEXT NOT NULL,
    upstream_url    TEXT NOT NULL DEFAULT '',
    packages        TEXT NOT NULL DEFAULT '{}',
    recorded_at     TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class Evidence:
    """What verified a fact: a test that passed or a read-back of the source."""
    kind: str
    ref: str
    passed: bool


def _require_evidence(evidence: Evidence) -> None:
    if evidence.kind not in EVIDENCE_KINDS:
        raise K.SkillError(f"evidence kind must be one of {EVIDENCE_KINDS}, not {evidence.kind!r}")
    if not str(evidence.ref).strip():
        raise K.SkillError("evidence needs a reference (the test id, or what was read back)")
    if not evidence.passed:
        raise K.SkillError("a fact enters the skill only after its verification passed")


class SkillFactMemory:
    def __init__(self, registry, graph: D.SkillDependencyGraph | None = None) -> None:
        self.registry = registry
        self.graph = graph or D.SkillDependencyGraph(registry)
        with self._connect() as db:
            db.executescript(SCHEMA)

    def _connect(self):
        from friday.dbconn import ledger_connection

        return ledger_connection(self.registry.path)

    # -- facts -----------------------------------------------------------------

    def add_fact(self, skill: str, fact: str, *, depends_on: list[str], evidence: Evidence,
                 commit: str = "", codebase_id: str = "") -> int:
        """Store one verified fact with the fingerprints it was verified at."""
        if self.registry.get(skill) is None:
            raise K.SkillError(f"no skill named {skill!r}")
        if not str(fact).strip():
            raise K.SkillError("a fact needs text")
        _require_evidence(evidence)
        deps = [D.classify(entry) for entry in depends_on]
        if not any(kind != D.API for kind, _ in deps):
            raise K.SkillError("a fact must depend on a file, glob, package or skill that can change")
        if not commit and not codebase_id:
            codebase_id, commit = self.graph.identity()
        rows = [(kind, target, self.graph.fingerprint(kind, target)) for kind, target in deps]
        with self._connect() as db:
            cursor = db.execute(
                "INSERT INTO skill_facts (skill, fact, status, evidence_kind, evidence_ref, verified_commit,"
                " codebase_id, verified_at) VALUES (?,?,?,?,?,?,?,?)",
                (skill, fact.strip(), FACT_ACTIVE, evidence.kind, evidence.ref, commit, codebase_id, K.now_iso()))
            fact_id = int(cursor.lastrowid)
            db.executemany(
                "INSERT OR REPLACE INTO skill_fact_dependencies (fact_id, kind, target, fingerprint) VALUES (?,?,?,?)",
                [(fact_id, kind, target, fingerprint) for kind, target, fingerprint in rows])
        return fact_id

    def facts(self, skill: str, *, status: str | None = None) -> list[dict]:
        query, args = "SELECT * FROM skill_facts WHERE skill = ?", [skill]
        if status:
            query, args = query + " AND status = ?", args + [status]
        with self._connect() as db:
            found = [dict(row) for row in db.execute(query + " ORDER BY id", args).fetchall()]
            for item in found:
                item["depends_on"] = [dict(row) for row in db.execute(
                    "SELECT kind, target, fingerprint FROM skill_fact_dependencies WHERE fact_id = ?"
                    " ORDER BY kind, target", (item["id"],)).fetchall()]
        return found

    def stale_facts(self, skill: str) -> list[dict]:
        return self.facts(skill, status=FACT_STALE)

    # -- change ----------------------------------------------------------------

    def _drift(self, deps: list[dict]) -> str:
        """Why a fact's baseline no longer holds ('' when it still does)."""
        changed, missing = [], []
        for row in deps:
            if row["kind"] == D.API:
                continue
            now = self.graph.fingerprint(row["kind"], row["target"])
            if now == D.MISSING and row["fingerprint"] != D.MISSING:
                missing.append(row["target"])
            elif now != row["fingerprint"]:
                changed.append(row["target"])
        parts = []
        if changed:
            parts.append("changed: " + ", ".join(changed))
        if missing:
            parts.append("missing: " + ", ".join(missing))
        return "; ".join(parts)

    def sweep(self, *, actor: str = "fact-memory") -> list[dict]:
        """Mark every active fact whose dependency drifted, and its skill.

        Returns one entry per affected skill: {"skill", "stale_facts"}. Facts
        and skills whose dependencies are unchanged are not touched.
        """
        with self._connect() as db:
            skills = [row["skill"] for row in db.execute(
                "SELECT DISTINCT skill FROM skill_facts WHERE status = ? ORDER BY skill", (FACT_ACTIVE,)).fetchall()]
        marked = []
        for skill in skills:
            stale = []
            for item in self.facts(skill, status=FACT_ACTIVE):
                reason = self._drift(item["depends_on"])
                if not reason:
                    continue
                with self._connect() as db:
                    db.execute("UPDATE skill_facts SET status = ?, stale_reason = ? WHERE id = ?",
                               (FACT_STALE, reason, item["id"]))
                item["status"], item["stale_reason"] = FACT_STALE, reason
                stale.append(item)
            if not stale:
                continue
            record = self.registry.get(skill)
            if record is not None and record.state == K.ACTIVE:
                self.registry.transition(
                    skill, K.NEEDS_REVALIDATION, actor=actor,
                    reason=f"{len(stale)} verified fact(s) depend on changed code: " + stale[0]["stale_reason"][:120])
            marked.append({"skill": skill, "stale_facts": stale})
        return marked

    def revalidate_fact(self, fact_id: int, evidence: Evidence, *, commit: str = "", codebase_id: str = "") -> None:
        """A stale fact is true again: new passing evidence re-baselines it."""
        _require_evidence(evidence)
        with self._connect() as db:
            row = db.execute("SELECT id FROM skill_facts WHERE id = ?", (fact_id,)).fetchone()
            deps = [dict(r) for r in db.execute(
                "SELECT kind, target FROM skill_fact_dependencies WHERE fact_id = ?", (fact_id,)).fetchall()]
        if row is None:
            raise K.SkillError(f"no fact {fact_id}")
        if not commit and not codebase_id:
            codebase_id, commit = self.graph.identity()
        with self._connect() as db:
            for dep in deps:
                db.execute("UPDATE skill_fact_dependencies SET fingerprint = ? WHERE fact_id = ? AND kind = ? AND target = ?",
                           (self.graph.fingerprint(dep["kind"], dep["target"]), fact_id, dep["kind"], dep["target"]))
            db.execute(
                "UPDATE skill_facts SET status = ?, stale_reason = '', evidence_kind = ?, evidence_ref = ?,"
                " verified_commit = ?, codebase_id = ?, verified_at = ? WHERE id = ?",
                (FACT_ACTIVE, evidence.kind, evidence.ref, commit, codebase_id, K.now_iso(), fact_id))

    # -- intake ----------------------------------------------------------------

    def record_intake(self, skill: str, *, upstream_commit: str, upstream_url: str = "",
                      packages: dict[str, str] | None = None) -> None:
        """What an intake-produced skill was verified against."""
        if self.registry.get(skill) is None:
            raise K.SkillError(f"no skill named {skill!r}")
        if not str(upstream_commit).strip():
            raise K.SkillError("an intake records the upstream commit it was verified against")
        with self._connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO skill_intake_basis (skill, upstream_commit, upstream_url, packages, recorded_at)"
                " VALUES (?,?,?,?,?)",
                (skill, upstream_commit.strip(), upstream_url, json.dumps(dict(packages or {}), sort_keys=True), K.now_iso()))

    def intake_basis(self, skill: str) -> dict | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM skill_intake_basis WHERE skill = ?", (skill,)).fetchone()
        if row is None:
            return None
        basis = dict(row)
        basis["packages"] = json.loads(basis["packages"])
        return basis
