"""
The ledger: what FRIDAY knows about every skill, in every tier.

The SKILL.md is the source for name, description and version; everything
else here is FRIDAY's judgment - state, risk, trust, quality, versions with
snapshots for rollback, dependencies with fingerprints, usage outcomes,
evaluation reports. A change of state is a row in `skill_history` with who
asked and why; a skill that quietly became ACTIVE is the failure this table
makes impossible (the same shape as `forged_skill_history`).

Per-call connections through `friday.dbconn.ledger_connection`, like every
other ledger, so no handle outlives its with-block (tests/test_dbconn.py).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from friday.skills import contracts as K

SCHEMA = """
CREATE TABLE IF NOT EXISTS skills (
    name             TEXT PRIMARY KEY,
    description      TEXT NOT NULL DEFAULT '',
    version          TEXT NOT NULL DEFAULT '1.0.0',
    scope            TEXT NOT NULL,
    tier             TEXT NOT NULL,
    state            TEXT NOT NULL,
    path             TEXT NOT NULL DEFAULT '',
    source           TEXT NOT NULL DEFAULT '',
    source_url       TEXT NOT NULL DEFAULT '',
    author           TEXT NOT NULL DEFAULT '',
    trust            TEXT NOT NULL,
    risk             TEXT NOT NULL,
    content_hash     TEXT NOT NULL DEFAULT '',
    category         TEXT NOT NULL DEFAULT '',
    tags             TEXT NOT NULL DEFAULT '[]',
    platforms        TEXT NOT NULL DEFAULT '[]',
    permissions      TEXT NOT NULL DEFAULT '[]',
    dependencies     TEXT NOT NULL DEFAULT '[]',
    tests            TEXT NOT NULL DEFAULT '[]',
    triggers         TEXT NOT NULL DEFAULT '[]',
    never_for        TEXT NOT NULL DEFAULT '[]',
    task             TEXT NOT NULL DEFAULT '',
    publish          TEXT NOT NULL DEFAULT 'local',
    quality_score    REAL NOT NULL DEFAULT 0,
    usage_count      INTEGER NOT NULL DEFAULT 0,
    success_count    INTEGER NOT NULL DEFAULT 0,
    failure_count    INTEGER NOT NULL DEFAULT 0,
    correction_count INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    last_verified    TEXT NOT NULL DEFAULT '',
    codebase_id      TEXT NOT NULL DEFAULT '',
    codebase_commit  TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS skill_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    from_state  TEXT,
    to_state    TEXT NOT NULL,
    reason      TEXT NOT NULL,
    actor       TEXT NOT NULL,
    at          TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS skill_versions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT NOT NULL,
    version        TEXT NOT NULL,
    content_hash   TEXT NOT NULL,
    snapshot_path  TEXT NOT NULL DEFAULT '',
    verified       INTEGER NOT NULL DEFAULT 0,
    evaluation     TEXT NOT NULL DEFAULT '{}',
    note           TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL,
    UNIQUE(name, version)
);
CREATE TABLE IF NOT EXISTS skill_usage (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    version       TEXT NOT NULL DEFAULT '',
    outcome       TEXT NOT NULL,
    latency_ms    INTEGER NOT NULL DEFAULT 0,
    cost          REAL NOT NULL DEFAULT 0,
    tokens_loaded INTEGER NOT NULL DEFAULT 0,
    objective     TEXT NOT NULL DEFAULT '',
    at            TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS skill_dependencies (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    kind        TEXT NOT NULL,
    target      TEXT NOT NULL,
    fingerprint TEXT NOT NULL DEFAULT '',
    checked_at  TEXT NOT NULL,
    UNIQUE(name, kind, target)
);
CREATE TABLE IF NOT EXISTS skill_evaluations (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    version       TEXT NOT NULL,
    content_hash  TEXT NOT NULL,
    nonce         TEXT NOT NULL UNIQUE,
    passed        INTEGER NOT NULL,
    report        TEXT NOT NULL,
    at            TEXT NOT NULL
);
"""

_LIST_FIELDS = ("tags", "platforms", "permissions", "dependencies", "tests", "triggers", "never_for")

USAGE_OUTCOMES = ("success", "failure", "corrected")


def _row_to_record(row: sqlite3.Row) -> K.SkillRecord:
    data = dict(row)
    for key in _LIST_FIELDS:
        raw = data.get(key) or "[]"
        try:
            data[key] = list(json.loads(raw))
        except (TypeError, ValueError):
            data[key] = []
    return K.SkillRecord.from_dict(data)


class SkillRegistry:
    def __init__(self, db_path: str | Path | None = None) -> None:
        if db_path is None:
            import os

            from friday.config import DATA_DIR
            from friday.store import DEFAULT_DB

            db_path = os.getenv("ADA_DB") or DEFAULT_DB
            Path(DATA_DIR).mkdir(parents=True, exist_ok=True)
        self._path = str(db_path)
        Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript(SCHEMA)

    @property
    def path(self) -> str:
        return self._path

    def _connect(self):
        from friday.dbconn import ledger_connection

        return ledger_connection(self._path)

    # -- records -----------------------------------------------------------

    def upsert(self, record: K.SkillRecord) -> K.SkillRecord:
        if record.state not in K.STATES:
            raise K.SkillError(f"unknown state {record.state!r}")
        if record.scope not in K.SCOPES:
            raise K.SkillError(f"unknown scope {record.scope!r}")
        if record.risk not in K.RISK_CLASSES:
            raise K.SkillError(f"unknown risk {record.risk!r}")
        problem = K.valid_name(record.name)
        if problem:
            raise K.SkillError(problem)
        record.updated_at = K.now_iso()
        data = record.to_dict()
        for key in _LIST_FIELDS:
            data[key] = json.dumps(list(data.get(key) or []))
        columns = ", ".join(data)
        marks = ", ".join("?" for _ in data)
        updates = ", ".join(f"{key} = excluded.{key}" for key in data if key not in ("name", "created_at"))
        with self._connect() as db:
            db.execute(f"INSERT INTO skills ({columns}) VALUES ({marks}) "
                       f"ON CONFLICT(name) DO UPDATE SET {updates}", tuple(data.values()))
        return record

    def get(self, name: str) -> K.SkillRecord | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM skills WHERE name = ?", (name,)).fetchone()
        return _row_to_record(row) if row else None

    def list(self, *, state: str | None = None, tier: str | None = None,
             scope: str | None = None) -> list[K.SkillRecord]:
        clauses, values = [], []
        for column, value in (("state", state), ("tier", tier), ("scope", scope)):
            if value:
                clauses.append(f"{column} = ?")
                values.append(value)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        with self._connect() as db:
            rows = db.execute(f"SELECT * FROM skills{where} ORDER BY name", tuple(values)).fetchall()
        return [_row_to_record(row) for row in rows]

    def delete(self, name: str) -> bool:
        with self._connect() as db:
            gone = db.execute("DELETE FROM skills WHERE name = ?", (name,)).rowcount
        return bool(gone)

    # -- state --------------------------------------------------------------

    def transition(self, name: str, to_state: str, *, reason: str, actor: str) -> K.SkillRecord:
        """Move a skill between states along the transition table, recording
        who asked and why. Guards that need more than the table (evidence,
        risk, trust) live in friday.skills.lifecycle and call this last."""
        if to_state not in K.STATES:
            raise K.TransitionRefused(f"unknown state {to_state!r}")
        record = self.get(name)
        if record is None:
            raise K.TransitionRefused(f"no skill named {name!r}")
        if to_state == record.state:
            return record
        if to_state not in K.TRANSITIONS.get(record.state, ()):
            raise K.TransitionRefused(f"{name}: {record.state} -> {to_state} is not a legal transition")
        if not reason.strip():
            raise K.TransitionRefused("a transition needs a reason")
        now = K.now_iso()
        with self._connect() as db:
            db.execute("UPDATE skills SET state = ?, updated_at = ? WHERE name = ?", (to_state, now, name))
            db.execute("INSERT INTO skill_history (name, from_state, to_state, reason, actor, at) VALUES (?,?,?,?,?,?)",
                       (name, record.state, to_state, reason, actor, now))
        record.state = to_state
        record.updated_at = now
        return record

    def note(self, name: str, reason: str, *, actor: str) -> None:
        """A history row without a state change: a refused update, a rollback
        that landed on the same state, an owner's remark."""
        record = self.get(name)
        state = record.state if record else ""
        with self._connect() as db:
            db.execute("INSERT INTO skill_history (name, from_state, to_state, reason, actor, at) VALUES (?,?,?,?,?,?)",
                       (name, state, state, reason, actor, K.now_iso()))

    def history(self, name: str) -> list[dict]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM skill_history WHERE name = ? ORDER BY id", (name,)).fetchall()
        return [dict(row) for row in rows]

    # -- versions -------------------------------------------------------------

    def record_version(self, name: str, version: str, content_hash: str, *,
                       snapshot_path: str = "", evaluation: K.EvaluationReport | None = None,
                       note: str = "") -> dict:
        verified = bool(evaluation and evaluation.passed and evaluation.content_hash == content_hash)
        payload = json.dumps(evaluation.to_dict()) if evaluation else "{}"
        now = K.now_iso()
        with self._connect() as db:
            db.execute(
                "INSERT INTO skill_versions (name, version, content_hash, snapshot_path, verified, evaluation, note, created_at)"
                " VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(name, version) DO UPDATE SET"
                " content_hash = excluded.content_hash, snapshot_path = excluded.snapshot_path,"
                " verified = excluded.verified, evaluation = excluded.evaluation, note = excluded.note",
                (name, version, content_hash, snapshot_path, int(verified), payload, note, now))
        return {"name": name, "version": version, "verified": verified, "snapshot_path": snapshot_path}

    def versions(self, name: str) -> list[dict]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM skill_versions WHERE name = ? ORDER BY id", (name,)).fetchall()
        found = []
        for row in rows:
            data = dict(row)
            data["verified"] = bool(data["verified"])
            found.append(data)
        return found

    def last_verified_version(self, name: str, *, before: str | None = None) -> dict | None:
        """The newest verified version, optionally older than `before`."""
        candidates = [v for v in self.versions(name) if v["verified"]]
        if before is not None:
            limit = K.parse_version(before)
            candidates = [v for v in candidates if K.parse_version(v["version"]) < limit]
        if not candidates:
            return None
        return max(candidates, key=lambda v: K.parse_version(v["version"]))

    # -- evaluations ----------------------------------------------------------

    def record_evaluation(self, report: K.EvaluationReport) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT INTO skill_evaluations (name, version, content_hash, nonce, passed, report, at)"
                " VALUES (?,?,?,?,?,?,?)",
                (report.skill, report.version, report.content_hash, report.nonce, int(report.passed),
                 json.dumps(report.to_dict()), report.at))
            if report.passed:
                db.execute("UPDATE skills SET last_verified = ?, updated_at = ? WHERE name = ?",
                           (report.at, K.now_iso(), report.skill))

    def evaluation_on_record(self, report: K.EvaluationReport) -> bool:
        """Whether this exact report (nonce, hash, verdict) was recorded by the
        evaluator - the check the lifecycle makes before activating."""
        with self._connect() as db:
            row = db.execute(
                "SELECT passed, content_hash, version FROM skill_evaluations WHERE nonce = ? AND name = ?",
                (report.nonce, report.skill)).fetchone()
        return bool(row and row["passed"] and row["content_hash"] == report.content_hash
                    and row["version"] == report.version)

    # -- usage ---------------------------------------------------------------

    def record_usage(self, name: str, outcome: str, *, version: str = "", latency_ms: int = 0,
                     cost: float = 0.0, tokens_loaded: int = 0, objective: str = "") -> None:
        if outcome not in USAGE_OUTCOMES:
            raise K.SkillError(f"unknown outcome {outcome!r} ({', '.join(USAGE_OUTCOMES)})")
        column = {"success": "success_count", "failure": "failure_count", "corrected": "correction_count"}[outcome]
        now = K.now_iso()
        with self._connect() as db:
            db.execute("INSERT INTO skill_usage (name, version, outcome, latency_ms, cost, tokens_loaded, objective, at)"
                       " VALUES (?,?,?,?,?,?,?,?)",
                       (name, version, outcome, int(latency_ms), float(cost), int(tokens_loaded), objective, now))
            db.execute(f"UPDATE skills SET usage_count = usage_count + 1, {column} = {column} + 1, updated_at = ?"
                       " WHERE name = ?", (now, name))

    def usage(self, name: str, limit: int = 100) -> list[dict]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM skill_usage WHERE name = ? ORDER BY id DESC LIMIT ?",
                              (name, limit)).fetchall()
        return [dict(row) for row in rows]

    def stats(self, name: str, limit: int = 1000) -> dict:
        """Rates and averages over the recorded usage rows."""
        rows = self.usage(name, limit=limit)
        judged = [r for r in rows if r["outcome"] in ("success", "failure")]
        successes = sum(1 for r in judged if r["outcome"] == "success")
        timed = [r["latency_ms"] for r in rows if r["latency_ms"] > 0]
        priced = [r["cost"] for r in rows if r["cost"] > 0]
        return {
            "uses": len(rows),
            "corrections": sum(1 for r in rows if r["outcome"] == "corrected"),
            "success_rate": round(successes / len(judged), 3) if judged else None,
            "failure_rate": round((len(judged) - successes) / len(judged), 3) if judged else None,
            "average_latency_ms": round(sum(timed) / len(timed)) if timed else 0,
            "average_cost": round(sum(priced) / len(priced), 6) if priced else 0.0,
        }

    def set_quality(self, name: str, score: float) -> None:
        with self._connect() as db:
            db.execute("UPDATE skills SET quality_score = ?, updated_at = ? WHERE name = ?",
                       (float(score), K.now_iso(), name))

    # -- dependencies ---------------------------------------------------------

    def set_dependencies(self, name: str, entries: list[tuple[str, str, str]]) -> None:
        """Replace the dependency rows: (kind, target, fingerprint)."""
        now = K.now_iso()
        with self._connect() as db:
            db.execute("DELETE FROM skill_dependencies WHERE name = ?", (name,))
            db.executemany(
                "INSERT INTO skill_dependencies (name, kind, target, fingerprint, checked_at) VALUES (?,?,?,?,?)",
                [(name, kind, target, fingerprint, now) for kind, target, fingerprint in entries])

    def dependencies(self, name: str) -> list[dict]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM skill_dependencies WHERE name = ? ORDER BY kind, target",
                              (name,)).fetchall()
        return [dict(row) for row in rows]

    def all_dependencies(self) -> list[dict]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM skill_dependencies ORDER BY name, kind, target").fetchall()
        return [dict(row) for row in rows]

    def update_fingerprint(self, name: str, kind: str, target: str, fingerprint: str) -> None:
        with self._connect() as db:
            db.execute("UPDATE skill_dependencies SET fingerprint = ?, checked_at = ? WHERE name = ? AND kind = ? AND target = ?",
                       (fingerprint, K.now_iso(), name, kind, target))
