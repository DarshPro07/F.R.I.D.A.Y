"""
The durable record of every agent FRIDAY spawns (AGENT-001).

Team activity is derived from this ledger, not from what a model says it did
or from what happens to be in memory, so it is still correct after a restart.

Two tables carry it (declared in `friday/store.py`):

  agent_runs    one row per agent; `run_id` is the executor run id, so the
                ledger and `executor_runs` describe the same run.
  agent_events  append-only action evidence; the table refuses UPDATE and
                DELETE, and every event carries `evidence_refs`.

Two rules decide what a run *is*:

  * A run is ACTIVE only after the executor wrote a `worker_started` event.
    `start()` records a claim (STARTING); a claim is not a worker.
  * A run the ledger believes is RUNNING must still have a live process.
    `RunManager.is_live` is the judge. A dead one is INTERRUPTED, which
    invites a resume, never a RUNNING that nobody is working on. A claim that
    ends PARTIAL before any worker started is INTERRUPTED the same way.
"""

from __future__ import annotations

import json

from friday.executors.runs import RunManager
from friday.store import Store, now_iso

STARTING = "STARTING"
RUNNING = "RUNNING"
INTERRUPTED = "INTERRUPTED"
SUCCEEDED = "SUCCEEDED"
FAILED = "FAILED"
CANCELLED = "CANCELLED"

TERMINAL = frozenset({SUCCEEDED, FAILED, CANCELLED})

#: The one event that moves a run from claimed to running.
WORKER_STARTED = "worker_started"

#: A run that ended short of a verdict. Not final, but no process is behind it.
PARTIAL = "partial"

_ROUTE_PARTS = 4   # provider/model/version/billing_class


class LedgerError(ValueError):
    """The ledger refused a write that would make it lie."""


class AgentLedger:
    def __init__(self, store: Store) -> None:
        self.store = store

    # -- writes ------------------------------------------------------------

    def start(self, run_id: str, *, task: str, workspace: str, executor_id: str,
              parent_run_id: str = "", role_id: str = "", model_route: str = "",
              evidence_refs: list[str] | None = None) -> dict:
        """
        Record that a run was claimed. It is not active until a worker starts.

        Idempotent per run_id: a resumed run keeps its original identity and
        goes back to STARTING with a `reopened` event.
        """
        if model_route and len(model_route.split("/")) != _ROUTE_PARTS:
            raise LedgerError(
                f"model_route {model_route!r} must be "
                "provider/model/version/billing_class")
        if parent_run_id and self._row(parent_run_id) is None:
            raise LedgerError(f"unknown parent_run_id {parent_run_id!r}")
        refs = evidence_refs or [f"agent_runs:{run_id}"]
        stamp = now_iso()
        with self.store._tx() as conn:
            existing = conn.execute(
                "SELECT run_id FROM agent_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE agent_runs SET status=?, ended_at=NULL WHERE run_id=?",
                    (STARTING, run_id))
                kind, message = "reopened", "run reopened"
            else:
                conn.execute(
                    "INSERT INTO agent_runs (run_id, parent_run_id, role_id, "
                    "executor_id, model_route, task, workspace, status, "
                    "started_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    (run_id, parent_run_id or None, role_id, executor_id,
                     model_route, task, workspace, STARTING, stamp))
                kind, message = "claimed", "run claimed"
            self._append(conn, run_id, kind, message, refs, stamp)
        return self.run(run_id)

    def event(self, run_id: str, kind: str, message: str,
              evidence_refs: list[str]) -> None:
        """Append one action-evidence event. No evidence, no event."""
        if not kind:
            raise LedgerError("an event needs a kind")
        if not evidence_refs:
            raise LedgerError(f"event {kind!r} on {run_id} carries no evidence_refs")
        stamp = now_iso()
        with self.store._tx() as conn:
            row = conn.execute(
                "SELECT status FROM agent_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if row is None:
                raise LedgerError(f"unknown run {run_id!r}")
            self._append(conn, run_id, kind, message, evidence_refs, stamp)
            if kind == WORKER_STARTED and row["status"] == STARTING:
                conn.execute("UPDATE agent_runs SET status=? WHERE run_id=?",
                             (RUNNING, run_id))
            elif kind == PARTIAL and row["status"] == STARTING:
                # reconcile() only judges RUNNING rows; a claim that ended
                # before any worker started would otherwise read STARTING.
                conn.execute("UPDATE agent_runs SET status=? WHERE run_id=?",
                             (INTERRUPTED, run_id))

    def finish(self, run_id: str, status: str, evidence_refs: list[str],
               message: str = "") -> None:
        """Close a run with how it ended."""
        if status not in TERMINAL:
            raise LedgerError(f"{status!r} is not a final status")
        if not evidence_refs:
            raise LedgerError(f"finish of {run_id} carries no evidence_refs")
        stamp = now_iso()
        with self.store._tx() as conn:
            if self._row(run_id) is None:
                raise LedgerError(f"unknown run {run_id!r}")
            conn.execute(
                "UPDATE agent_runs SET status=?, ended_at=? WHERE run_id=?",
                (status, stamp, run_id))
            self._append(conn, run_id, "finished", message or status,
                         evidence_refs, stamp)

    def bind_route(self, run_id: str, model_route: str,
                   evidence_refs: list[str]) -> bool:
        """
        Record the route a run is on when `start()` was not told one.

        A route the routing policy declared is never overwritten: this fills a
        gap, it does not second-guess a decision. Returns whether it wrote.
        """
        if len(model_route.split("/")) != _ROUTE_PARTS:
            raise LedgerError(
                f"model_route {model_route!r} must be "
                "provider/model/version/billing_class")
        if not evidence_refs:
            raise LedgerError(f"route of {run_id} carries no evidence_refs")
        stamp = now_iso()
        with self.store._tx() as conn:
            row = conn.execute(
                "SELECT model_route FROM agent_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if row is None:
                raise LedgerError(f"unknown run {run_id!r}")
            if row["model_route"]:
                return False
            conn.execute("UPDATE agent_runs SET model_route=? WHERE run_id=?",
                         (model_route, run_id))
            self._append(conn, run_id, "route_bound", model_route,
                         evidence_refs, stamp)
        return True

    def reconcile(self) -> list[str]:
        """
        Believe the world, not the row: a RUNNING run with no live process is
        INTERRUPTED. Returns the run ids that changed.
        """
        changed = []
        with self.store._tx() as conn:
            running = conn.execute(
                "SELECT run_id FROM agent_runs WHERE status=?", (RUNNING,)
            ).fetchall()
            for row in running:
                run_id = row["run_id"]
                executor = self.store.executor_run(run_id)
                if executor is not None and RunManager.is_live(executor):
                    continue
                conn.execute("UPDATE agent_runs SET status=? WHERE run_id=?",
                             (INTERRUPTED, run_id))
                self._append(conn, run_id, "interrupted",
                             "no live process behind a RUNNING run",
                             [f"executor_runs:{run_id}"], now_iso())
                changed.append(run_id)
        return changed

    # -- reads (state comes from the tables only) --------------------------

    def run(self, run_id: str) -> dict | None:
        self.reconcile()
        return self._row(run_id)

    def events(self, run_id: str) -> list[dict]:
        return [self._event(r) for r in self.store._conn.execute(
            "SELECT * FROM agent_events WHERE run_id=? ORDER BY event_id",
            (run_id,))]

    def children(self, run_id: str) -> list[dict]:
        self.reconcile()
        return [dict(r) for r in self.store._conn.execute(
            "SELECT * FROM agent_runs WHERE parent_run_id=? "
            "ORDER BY started_at, run_id", (run_id,))]

    def tree(self, run_id: str) -> dict:
        """The run and, recursively, every run it spawned."""
        run = self.run(run_id)
        if run is None:
            raise LedgerError(f"unknown run {run_id!r}")
        return {"run": run,
                "children": [self.tree(c["run_id"]) for c in self.children(run_id)]}

    def by_role(self, role_id: str) -> list[dict]:
        """Every run attributed to a role, whichever executor ran it (FAB-005)."""
        self.reconcile()
        return [dict(r) for r in self.store._conn.execute(
            "SELECT * FROM agent_runs WHERE role_id=? ORDER BY started_at, run_id",
            (role_id,))]

    def active(self) -> list[dict]:
        """Runs a worker started and whose process is still there."""
        self.reconcile()
        return [dict(r) for r in self.store._conn.execute(
            "SELECT * FROM agent_runs WHERE status=? AND EXISTS ("
            "  SELECT 1 FROM agent_events e WHERE e.run_id=agent_runs.run_id "
            "  AND e.kind=?) ORDER BY started_at, run_id",
            (RUNNING, WORKER_STARTED))]

    # -- internals ---------------------------------------------------------

    def _row(self, run_id: str) -> dict | None:
        row = self.store._conn.execute(
            "SELECT * FROM agent_runs WHERE run_id=?", (run_id,)).fetchone()
        return dict(row) if row else None

    @staticmethod
    def _append(conn, run_id: str, kind: str, message: str,
                evidence_refs: list[str], stamp: str) -> None:
        conn.execute(
            "INSERT INTO agent_events (run_id, kind, message, evidence_refs, "
            "created_at) VALUES (?,?,?,?,?)",
            (run_id, kind, message, json.dumps(list(evidence_refs)), stamp))

    @staticmethod
    def _event(row) -> dict:
        got = dict(row)
        got["evidence_refs"] = json.loads(got["evidence_refs"])
        return got
