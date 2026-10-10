"""LEARN-001: promotion of a neural/adaptor candidate, gated on regression.

A new adaptor version is promoted only if it does NOT regress the active one
on a held-out metric (the forgetting / regression rule PRD LEARN-001 names).
That is the SAME rule the skill ladder already proves - a bad new version
never replaces the active one (`friday/skills/lifecycle.py`, SK06). So rather
than build a second regression-gate mechanism, promotion routes through
`friday.arena` exactly as `SkillLadder` does: a candidate promotes only on a
stored arena report in which it passed every gate, and the gates include
`no_regression` (candidate holdout >= the active adaptor's holdout minus a
tolerance) and `holdout_floor` (an adaptor that is simply bad never promotes).

NEURAL-001 - training the small FRIDAY-owned policy networks - does not exist
yet. This module is the promotion seam it will feed: it takes an already
measured held-out score per candidate version and decides promotion; it does
not train anything.

Assumptions (revisit if wrong): a candidate is a version of a named adaptor
with a single held-out score; versions are assigned in capture order so a new
candidate always outranks the active one; a blocked candidate stays CANDIDATE
(it is not rejected - a retrain is a new version), and the previously active
version keeps serving. A below-floor first adaptor never promotes, so an
adaptor with no verified version is simply not yet in production.
"""
from __future__ import annotations

import math
import os
import time
from pathlib import Path
from typing import Mapping

from friday import arena

_TABLE = """
CREATE TABLE IF NOT EXISTS adaptor_candidates (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    version    INTEGER NOT NULL DEFAULT 1,
    holdout    REAL NOT NULL,
    cases      INTEGER NOT NULL DEFAULT 0,
    state      TEXT NOT NULL DEFAULT 'CANDIDATE',
               -- CANDIDATE | ACTIVE | SUPERSEDED | ROLLED_BACK
    report_id  TEXT NOT NULL DEFAULT '',
    note       TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE(name, version)
)
"""

#: Below this held-out score an adaptor never promotes, incumbent or not.
DEFAULT_FLOOR = 0.5

#: How far a new version may fall below the active one's held-out score before
#: it counts as a regression. Anything beyond this blocks promotion.
DEFAULT_TOLERANCE = 0.0


def arena_id(name: str, version: int) -> str:
    """The adaptor arena's candidate id: one version of one adaptor, so a
    report for v1 cannot promote v2."""
    return f"{name}@v{version}"


class AdaptorLadder:
    def __init__(self, db_path: str | Path | None = None, *,
                 floor: float = DEFAULT_FLOOR,
                 tolerance: float = DEFAULT_TOLERANCE,
                 strict: bool = False) -> None:
        if db_path is None:
            from friday.store import DEFAULT_DB
            db_path = os.getenv("ADA_DB") or DEFAULT_DB
        self._path = str(db_path)
        self.floor = float(floor)
        self.tolerance = float(tolerance)
        #: LAB-004: a strict ladder promotes only a candidate that BEATS the
        #: incumbent; the default still promotes a tie within the tolerance.
        self.strict = bool(strict)
        with self._connect() as db:
            db.execute(_TABLE)

    def _connect(self):
        from friday.dbconn import ledger_connection
        return ledger_connection(self._path)

    # -- capture ------------------------------------------------------------

    def capture(self, name: str, *, holdout: float, cases: int = 0,
                note: str = "") -> dict:
        """Record a measured adaptor candidate as a new version. Capture does
        not promote - it only files the held-out score the gate will read."""
        value = float(holdout)
        if not math.isfinite(value):
            return {"status": "refused",
                    "note": "holdout score must be a finite number"}
        if int(cases) < 0:
            return {"status": "refused", "note": "cases cannot be negative"}
        now = time.time()
        with self._connect() as db:
            row = db.execute(
                "SELECT MAX(version) AS v FROM adaptor_candidates"
                " WHERE name = ?", (name,)).fetchone()
            version = (row["v"] or 0) + 1
            db.execute(
                "INSERT INTO adaptor_candidates (name, version, holdout,"
                " cases, note, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                (name, version, value, int(cases), note, now, now))
        return {"status": "captured", "name": name, "version": version,
                "holdout": value}

    # -- promotion ----------------------------------------------------------

    def _scorer(self, baseline: float | None, candidate: str = "",
                extra_gates: Mapping[str, bool] | None = None):
        """The per-candidate scorer the arena replays. The incumbent is scored
        against nothing, so it always clears `no_regression`; the challenger
        (`candidate`) is scored against `baseline` and carries `extra_gates`."""
        def score(arena_id_str: str) -> arena.Score:
            name, _, vtxt = arena_id_str.rpartition("@v")
            with self._connect() as db:
                row = db.execute(
                    "SELECT holdout, cases FROM adaptor_candidates"
                    " WHERE name = ? AND version = ?",
                    (name, int(vtxt))).fetchone()
            if row is None:
                raise KeyError(arena_id_str)
            holdout = float(row["holdout"])
            against = baseline if arena_id_str == candidate else None
            gates = {"holdout_floor": holdout >= self.floor,
                     "no_regression": against is None
                     or holdout >= against - self.tolerance}
            if self.strict:
                gates["strict_win"] = against is None or holdout > against
            if arena_id_str == candidate:
                gates.update({str(k): bool(v) for k, v in (extra_gates or {}).items()})
            return arena.Score(gates=gates, cases=int(row["cases"]), score=holdout,
                               metrics={"holdout": holdout})
        return score

    def promote(self, name: str, *, store: arena.ArenaStore,
                version: int | None = None,
                provenance: Mapping[str, object] | None = None,
                baseline: float | None = None,
                extra_gates: Mapping[str, bool] | None = None) -> dict:
        """Promote a captured candidate (the latest, or `version`) iff it clears
        the floor and does not regress the active adaptor. The comparison is run
        as an arena of [active, candidate] and the decision is `arena.promote`'s
        - a regression fails the `no_regression` gate, promotion is refused, and
        the active version keeps serving. Nothing promotes on a claim; it
        promotes on the stored report.

        `baseline` is the score to beat when the caller measured the incumbent
        itself on the same cases (LAB-004: the current routing, which is no
        adaptor row); it replaces the active adaptor's stored holdout.
        `extra_gates` are caller-measured gates (name -> passed) the candidate
        must also pass, such as "no previously passing case is lost"."""
        cand = self._row(name, version)
        if cand is None:
            return {"status": "absent", "name": name}
        if cand["state"] == "ACTIVE":
            return {"status": "already_active", "version": cand["version"]}
        active = self.active(name)
        if baseline is None:
            baseline = float(active["holdout"]) if active else None
        else:
            baseline = float(baseline)
        cand_arena = arena_id(name, cand["version"])
        ids: list[str] = []
        if active and active["version"] != cand["version"]:
            ids.append(arena_id(name, active["version"]))
        ids.append(cand_arena)
        report = arena.compare(arena.ArenaKind.NEURAL_ADAPTOR, ids,
                               self._scorer(baseline, cand_arena, extra_gates),
                               provenance=provenance)
        store.record(report)
        try:
            arena.promote(arena.ArenaKind.NEURAL_ADAPTOR, cand_arena,
                          report_id=report.report_id, store=store)
        except arena.PromotionRefused as exc:
            self._note(name, cand["version"], f"blocked: {exc}")
            return {"status": "blocked", "reason": str(exc),
                    "active_version": active["version"] if active else None,
                    "report_id": report.report_id}
        now = time.time()
        with self._connect() as db:
            if active and active["version"] != cand["version"]:
                db.execute(
                    "UPDATE adaptor_candidates SET state = 'SUPERSEDED',"
                    " updated_at = ? WHERE name = ? AND version = ?",
                    (now, name, active["version"]))
            db.execute(
                "UPDATE adaptor_candidates SET state = 'ACTIVE', report_id = ?,"
                " updated_at = ? WHERE name = ? AND version = ?",
                (report.report_id, now, name, cand["version"]))
        return {"status": "promoted", "version": cand["version"],
                "previous": active["version"] if active else None,
                "report_id": report.report_id}

    def rollback(self, name: str) -> dict:
        """One call back: the active version becomes ROLLED_BACK and the newest
        version it superseded serves again; with none, no adaptor serves and
        the caller's own routing is what runs."""
        now = time.time()
        with self._connect() as db:
            row = db.execute(
                "SELECT version FROM adaptor_candidates WHERE name = ? AND"
                " state = 'ACTIVE' ORDER BY version DESC LIMIT 1", (name,)).fetchone()
            if row is None:
                return {"status": "nothing_to_roll_back", "name": name}
            current = row["version"]
            prev = db.execute(
                "SELECT version FROM adaptor_candidates WHERE name = ? AND"
                " state = 'SUPERSEDED' AND version < ? ORDER BY version DESC LIMIT 1",
                (name, current)).fetchone()
            db.execute(
                "UPDATE adaptor_candidates SET state = 'ROLLED_BACK', updated_at = ?"
                " WHERE name = ? AND version = ?", (now, name, current))
            if prev is not None:
                db.execute(
                    "UPDATE adaptor_candidates SET state = 'ACTIVE', updated_at = ?"
                    " WHERE name = ? AND version = ?", (now, name, prev["version"]))
        return {"status": "rolled_back", "from": current,
                "to": prev["version"] if prev is not None else None}

    # -- reads --------------------------------------------------------------

    def active(self, name: str) -> dict | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM adaptor_candidates WHERE name = ? AND"
                " state = 'ACTIVE' ORDER BY version DESC LIMIT 1",
                (name,)).fetchone()
        return dict(row) if row else None

    #: The production adaptor for `name`, or None if none is verified yet.
    current = active

    def listing(self, state: str = "") -> list[dict]:
        with self._connect() as db:
            if state:
                rows = db.execute(
                    "SELECT name, version, state, holdout FROM"
                    " adaptor_candidates WHERE state = ? ORDER BY name, version",
                    (state,)).fetchall()
            else:
                rows = db.execute(
                    "SELECT name, version, state, holdout FROM"
                    " adaptor_candidates ORDER BY name, version").fetchall()
        return [dict(r) for r in rows]

    def _row(self, name: str, version: int | None) -> dict | None:
        with self._connect() as db:
            if version is not None:
                row = db.execute(
                    "SELECT * FROM adaptor_candidates WHERE name = ? AND"
                    " version = ?", (name, version)).fetchone()
            else:
                row = db.execute(
                    "SELECT * FROM adaptor_candidates WHERE name = ?"
                    " ORDER BY version DESC LIMIT 1", (name,)).fetchone()
        return dict(row) if row else None

    def _note(self, name: str, version: int, text: str) -> None:
        with self._connect() as db:
            db.execute(
                "UPDATE adaptor_candidates SET note = ?, updated_at = ?"
                " WHERE name = ? AND version = ?",
                (text[:300], time.time(), name, version))
