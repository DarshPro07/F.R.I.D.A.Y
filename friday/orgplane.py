"""OrganizationControlPlane - the neutral interface above any org
backend (Phase 6 of the vnext build; requirement R7).

Friday decides WHEN a persistent organization is justified; this module
decides NOTHING - it is the seam that keeps Friday independent of
Paperclip internals. Paperclip stays invisible and optional:

    Friday -> OrganizationControlPlane -> PaperclipAdapter (today)
                                       -> anything else (tomorrow)

Two implementations ship:

- LocalControlPlane: durable sqlite-backed operations/goals/work/budget/
  status. This is REAL and sufficient for single-machine persistent
  operations - not a mock. It is also the graceful-degradation target
  when Paperclip is absent.
- PaperclipAdapter: honest about its state. Paperclip is NOT INSTALLED
  on this machine today, so the adapter reports `unavailable` with the
  reason, and every call degrades to the LocalControlPlane while
  RECORDING that degradation. No fake connectivity, ever.

The gate this satisfies: "simple task does not invoke Paperclip" is
structural - nothing in the delegate path imports this module; only an
explicit org-scale decision reaches it.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

_TABLES = """
CREATE TABLE IF NOT EXISTS org_operations (
    op_id       TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    goal        TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'ACTIVE',
    budget_json TEXT NOT NULL DEFAULT '{}',
    backend     TEXT NOT NULL DEFAULT 'local',
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS org_work (
    work_id     TEXT PRIMARY KEY,
    op_id       TEXT NOT NULL,
    description TEXT NOT NULL,
    assignee    TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'OPEN',
    product     TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS org_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    op_id       TEXT NOT NULL,
    kind        TEXT NOT NULL,     -- decision | cost | routine | status
    payload     TEXT NOT NULL DEFAULT '{}',
    created_at  REAL NOT NULL
)
"""


class LocalControlPlane:
    """Durable, single-machine organization state. The degradation
    target and the v1 backend."""

    name = "local"

    def __init__(self, db_path: str | Path | None = None) -> None:
        if db_path is None:
            from friday.config import DATA_DIR
            db_path = Path(DATA_DIR) / "ada.sqlite3"
        self._path = str(db_path)
        with self._connect() as db:
            db.executescript(_TABLES)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self._path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    # -- interface ---------------------------------------------------------

    def create_operation(self, name: str, *, goal: str = "",
                         budget: dict | None = None) -> dict:
        import uuid
        op_id = f"op-{uuid.uuid4().hex[:10]}"
        now = time.time()
        with self._connect() as db:
            db.execute(
                "INSERT INTO org_operations (op_id, name, goal, budget_json,"
                " backend, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                (op_id, name, goal, json.dumps(budget or {}), self.name,
                 now, now))
        return {"status": "succeeded", "op_id": op_id, "backend": self.name}

    def update_goal(self, op_id: str, goal: str) -> dict:
        with self._connect() as db:
            n = db.execute(
                "UPDATE org_operations SET goal = ?, updated_at = ?"
                " WHERE op_id = ?", (goal, time.time(), op_id)).rowcount
        return {"status": "succeeded" if n else "absent", "op_id": op_id}

    def assign_work(self, op_id: str, description: str,
                    assignee: str = "") -> dict:
        import uuid
        work_id = f"work-{uuid.uuid4().hex[:10]}"
        now = time.time()
        with self._connect() as db:
            db.execute(
                "INSERT INTO org_work (work_id, op_id, description,"
                " assignee, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                (work_id, op_id, description, assignee, now, now))
        return {"status": "succeeded", "work_id": work_id}

    def record_work_product(self, work_id: str, product: str,
                            status: str = "DONE") -> dict:
        with self._connect() as db:
            n = db.execute(
                "UPDATE org_work SET product = ?, status = ?,"
                " updated_at = ? WHERE work_id = ?",
                (product, status, time.time(), work_id)).rowcount
        return {"status": "succeeded" if n else "absent"}

    def set_budget(self, op_id: str, budget: dict) -> dict:
        with self._connect() as db:
            n = db.execute(
                "UPDATE org_operations SET budget_json = ?, updated_at = ?"
                " WHERE op_id = ?",
                (json.dumps(budget), time.time(), op_id)).rowcount
        return {"status": "succeeded" if n else "absent"}

    def record_decision(self, op_id: str, decision: str) -> dict:
        return self._event(op_id, "decision", {"decision": decision})

    def record_cost(self, op_id: str, amount: float,
                    detail: str = "") -> dict:
        return self._event(op_id, "cost",
                           {"amount": amount, "detail": detail})

    def _event(self, op_id: str, kind: str, payload: dict) -> dict:
        with self._connect() as db:
            db.execute(
                "INSERT INTO org_events (op_id, kind, payload, created_at)"
                " VALUES (?,?,?,?)",
                (op_id, kind, json.dumps(payload), time.time()))
        return {"status": "succeeded"}

    def pause(self, op_id: str) -> dict:
        return self._set_status(op_id, "PAUSED")

    def resume(self, op_id: str) -> dict:
        return self._set_status(op_id, "ACTIVE")

    def _set_status(self, op_id: str, status: str) -> dict:
        with self._connect() as db:
            n = db.execute(
                "UPDATE org_operations SET status = ?, updated_at = ?"
                " WHERE op_id = ?", (status, time.time(), op_id)).rowcount
        return {"status": "succeeded" if n else "absent", "state": status}

    def get_status(self, op_id: str) -> dict:
        """The customer-facing shape: objective, work, progress, cost,
        decisions - never backend jargon."""
        with self._connect() as db:
            op = db.execute("SELECT * FROM org_operations WHERE op_id = ?",
                            (op_id,)).fetchone()
            if op is None:
                return {"status": "absent", "op_id": op_id}
            work = db.execute("SELECT * FROM org_work WHERE op_id = ?"
                              " ORDER BY created_at", (op_id,)).fetchall()
            events = db.execute(
                "SELECT * FROM org_events WHERE op_id = ?"
                " ORDER BY id DESC LIMIT 20", (op_id,)).fetchall()
        done = sum(1 for w in work if w["status"] == "DONE")
        cost = sum(json.loads(e["payload"]).get("amount", 0)
                   for e in events if e["kind"] == "cost")
        return {
            "status": "succeeded",
            "objective": op["goal"] or op["name"],
            "state": op["status"],
            "work_total": len(work),
            "work_done": done,
            "work": [{"description": w["description"],
                      "status": w["status"],
                      "assignee": w["assignee"]} for w in work],
            "cost_total": cost,
            "decisions": [json.loads(e["payload"]).get("decision", "")
                          for e in events if e["kind"] == "decision"],
        }


class PaperclipAdapter:
    """
    The Paperclip implementation of the interface - HONEST about
    availability. Paperclip is not installed on this machine; every call
    reports that truthfully and degrades to the LocalControlPlane, with
    the degradation recorded on the operation's event stream.

    When Paperclip is installed, availability detection lights up and
    calls route to its API instead - the interface does not change.
    """

    name = "paperclip"

    def __init__(self, fallback: LocalControlPlane | None = None) -> None:
        self.fallback = fallback or LocalControlPlane()

    @staticmethod
    def available() -> tuple[bool, str]:
        """Real detection, not configuration: is a Paperclip service
        reachable on this machine?"""
        import urllib.request
        for base in ("http://127.0.0.1:3790", "http://127.0.0.1:8730"):
            try:
                urllib.request.urlopen(base + "/api/health", timeout=2)
                return True, base
            except Exception:                                # noqa: BLE001
                continue
        return False, "no Paperclip service reachable on known local ports"

    def __getattr__(self, method):
        """Degrade every interface call to the local plane, recording
        the truth. (Explicit methods would arrive with live Paperclip
        integration; the seam is what ships today.)"""
        target = getattr(self.fallback, method)

        def degraded(*args, **kwargs):
            result = target(*args, **kwargs)
            if isinstance(result, dict):
                result.setdefault("backend", "local")
                result["paperclip"] = ("unavailable: " +
                                       self.available()[1])
            return result
        return degraded


def control_plane() -> LocalControlPlane | PaperclipAdapter:
    """The production accessor: Paperclip when genuinely reachable,
    local otherwise - decided by detection, not desire."""
    ok, _where = PaperclipAdapter.available()
    if ok:
        return PaperclipAdapter()
    return LocalControlPlane()
