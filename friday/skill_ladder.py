"""Skill candidates and the compaction recovery packet (Phase 7; R8/R10).

SKILL LADDER (the north star's decision, Friday decides - the boss
explicitly refused to adjudicate skill creation):

    lesson -> project memory          (default for one-off facts)
    lesson -> skill CANDIDATE         (when promotion criteria match)
    candidate -> validated skill      (after replay/eval, versioned)

Promotion criteria (R8): repeated procedure, expensive rediscovery,
safety-critical sequence, project-specific durable operational
knowledge. A candidate needs at least one criterion WITH evidence; a
skill needs a validation run. No per-task skill pollution: capture is
explicit, not a hook on every completion.

RECOVERY PACKET (R10 / build-pack 06): a tiny durable packet - goal,
state, last verified action, blocker, next action, decisions, pointers -
written at meaningful boundaries so compaction/restart resumes from
FILES, not from replayed conversation. Target <= 3000 chars.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

_TABLE = """
CREATE TABLE IF NOT EXISTS skill_candidates (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL,
    version      INTEGER NOT NULL DEFAULT 1,
    procedure    TEXT NOT NULL,
    criteria     TEXT NOT NULL DEFAULT '[]',   -- matched promotion criteria
    evidence     TEXT NOT NULL DEFAULT '',
    state        TEXT NOT NULL DEFAULT 'CANDIDATE',
                 -- CANDIDATE | VALIDATED | REJECTED | DEPRECATED
    validation   TEXT NOT NULL DEFAULT '',
    created_at   REAL NOT NULL,
    updated_at   REAL NOT NULL,
    UNIQUE(name, version)
)
"""

CRITERIA = ("repeated_procedure", "expensive_rediscovery",
            "safety_critical", "project_operational_knowledge")


class SkillLadder:
    def __init__(self, db_path: str | Path | None = None) -> None:
        if db_path is None:
            from friday.config import DATA_DIR
            db_path = Path(DATA_DIR) / "ada.sqlite3"
        self._path = str(db_path)
        with self._connect() as db:
            db.execute(_TABLE)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self._path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def capture(self, name: str, procedure: str, *,
                criteria: list[str], evidence: str) -> dict:
        """Record a candidate. Refused without a real criterion +
        evidence - that refusal IS the anti-pollution rule."""
        matched = [c for c in criteria if c in CRITERIA]
        if not matched:
            return {"status": "refused",
                    "note": "no promotion criterion matched - store as "
                            "project memory instead",
                    "criteria": list(CRITERIA)}
        if not evidence.strip():
            return {"status": "refused",
                    "note": "a candidate needs evidence (what happened, "
                            "where, cost of rediscovery)"}
        now = time.time()
        with self._connect() as db:
            row = db.execute(
                "SELECT MAX(version) AS v FROM skill_candidates"
                " WHERE name = ?", (name,)).fetchone()
            version = (row["v"] or 0) + 1
            db.execute(
                "INSERT INTO skill_candidates (name, version, procedure,"
                " criteria, evidence, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?)",
                (name, version, procedure, json.dumps(matched), evidence,
                 now, now))
        return {"status": "captured", "name": name, "version": version,
                "criteria": matched}

    def validate(self, name: str, *, passed: bool,
                 validation: str) -> dict:
        """Candidates become skills only through a validation run -
        'run succeeded -> mutate skill' is exactly the anti-pattern
        this state machine forbids."""
        state = "VALIDATED" if passed else "REJECTED"
        with self._connect() as db:
            n = db.execute(
                "UPDATE skill_candidates SET state = ?, validation = ?,"
                " updated_at = ? WHERE name = ? AND version ="
                " (SELECT MAX(version) FROM skill_candidates WHERE"
                " name = ?)",
                (state, validation, time.time(), name, name)).rowcount
        return {"status": "succeeded" if n else "absent", "state": state}

    def deprecate(self, name: str, reason: str) -> dict:
        with self._connect() as db:
            n = db.execute(
                "UPDATE skill_candidates SET state = 'DEPRECATED',"
                " validation = ?, updated_at = ? WHERE name = ?",
                (f"deprecated: {reason}", time.time(), name)).rowcount
        return {"status": "succeeded" if n else "absent"}

    def current(self, name: str) -> dict | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM skill_candidates WHERE name = ? AND"
                " state != 'DEPRECATED' ORDER BY version DESC LIMIT 1",
                (name,)).fetchone()
        return dict(row) if row else None

    def listing(self, state: str = "") -> list[dict]:
        with self._connect() as db:
            if state:
                rows = db.execute(
                    "SELECT name, version, state, criteria FROM"
                    " skill_candidates WHERE state = ? ORDER BY name",
                    (state,)).fetchall()
            else:
                rows = db.execute(
                    "SELECT name, version, state, criteria FROM"
                    " skill_candidates ORDER BY name, version").fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# recovery packet
# ---------------------------------------------------------------------------

PACKET_LIMIT = 3000


def write_recovery_packet(path: str | Path, *, objective: str, state: str,
                          last_verified: str, blocker: str = "",
                          next_action: str = "",
                          decisions: list[str] | None = None,
                          pointers: list[str] | None = None) -> dict:
    """The tiny durable resume packet. Hard-capped at PACKET_LIMIT chars:
    a packet that needs more than 3k is smuggling conversation history,
    so fields are truncated newest-first rather than the cap raised."""
    packet = {
        "written_at": time.time(),
        "objective": objective[:400],
        "state": state[:400],
        "last_verified_action": last_verified[:400],
        "blocker": blocker[:300],
        "next_action": next_action[:300],
        "decisions": [d[:200] for d in (decisions or [])[:6]],
        "pointers": [p[:200] for p in (pointers or [])[:10]],
    }
    text = json.dumps(packet, indent=1)
    while len(text) > PACKET_LIMIT and (packet["decisions"]
                                        or packet["pointers"]):
        if packet["pointers"]:
            packet["pointers"].pop()
        elif packet["decisions"]:
            packet["decisions"].pop()
        text = json.dumps(packet, indent=1)
    Path(path).write_text(text, encoding="utf-8")
    return {"status": "written", "chars": len(text), "path": str(path)}


def read_recovery_packet(path: str | Path) -> dict | None:
    p = Path(path)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except ValueError:
        return None
