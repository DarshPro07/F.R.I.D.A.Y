"""Status and user controls for durable continuous runs."""
from __future__ import annotations
import json
import os
from friday import runcontext as RC
from friday.continuity import ContinuityManager
from friday.store import DEFAULT_DB, Store
_store: Store | None = None
_manager: ContinuityManager | None = None


def reset_store(store: Store | None = None) -> None:
    global _manager, _store
    _store = store
    _manager = ContinuityManager(store) if store is not None else None


def manager() -> ContinuityManager:
    global _manager, _store
    if _manager is None:
        _store = Store(os.getenv('ADA_DB') or DEFAULT_DB)
        _manager = ContinuityManager(_store)
    return _manager


def _rows() -> list[dict]:
    return [{'run_id': snapshot.run_id, 'source': snapshot.objective, 'label': snapshot.state} for snapshot in manager().list_runs(limit=50)]


def _resolve(run_id: str, about: str, *, mutate: bool):
    rows = _rows()
    if run_id:
        exact = [row for row in rows if row['run_id'] == run_id]
        if not exact:
            return (None, {'status': 'not_found', 'error': f"no continuous run {run_id}", 'may_claim_completion': False})
        return (run_id, None)
    resolution = RC.resolve(rows, hint=about, noun='continuous run')
    if not resolution:
        return (None, {'status': 'not_found', 'error': resolution.reason, 'may_claim_completion': False})
    if mutate and not resolution.safe_to_mutate:
        return (None, {'status': 'ambiguous', 'error': resolution.reason, 'candidates': list(resolution.candidates), 'may_claim_completion': False})
    return (resolution.run_id, None)


def _snapshot(run_id: str, *, basis: str = '') -> dict:
    snapshot = manager().status(run_id)
    tasks = list(snapshot.tasks)
    current = next((task for task in tasks if task['status'] in {'pending', 'runnable', 'running', 'unknown', 'waiting'}), None)
    return {'run_id': snapshot.run_id, 'objective': snapshot.objective, 'state': snapshot.state, 'outcome': snapshot.outcome, 'provenance': snapshot.provenance, 'completed_tasks': [task for task in tasks if task['status'] == 'succeeded'], 'current_task': current, 'failed_tasks': [task for task in tasks if task['status'] in {'failed', 'unknown'}], 'next_wake': snapshot.wake, 'counters': snapshot.counters, 'last_events': manager().events(run_id, limit=10), 'resolution_basis': basis, 'may_claim_completion': snapshot.state == 'completed' and snapshot.outcome == 'succeeded'}


def register(mcp):
    @mcp.tool()
    def run_status(run_id: str = '', about: str = '') -> dict:
        """
        Report completed work, current task, failures, budgets, and the exact
        next wake for a long-running objective. Use `about` when the person
        names the work naturally instead of knowing Friday's run id.
        """
        resolved, error = _resolve(run_id, about, mutate=False)
        return error or _snapshot(resolved)

    @mcp.tool()
    def run_list(limit: int = 10) -> dict:
        """List recent long-running objectives and their durable states."""
        runs = manager().list_runs(limit=max(1, min(limit, 50)))
        return {'runs': [{'run_id': run.run_id, 'objective': run.objective, 'state': run.state, 'outcome': run.outcome, 'next_wake': run.wake} for run in runs], 'may_claim_completion': False}

    @mcp.tool()
    def run_pause(run_id: str = '', about: str = '') -> dict:
        """Pause one exact active objective. Ambiguity is refused."""
        resolved, error = _resolve(run_id, about, mutate=True)
        if error:
            return error
        manager().pause_run(resolved, 'person paused the run')
        return _snapshot(resolved)

    @mcp.tool()
    def run_resume(run_id: str = '', about: str = '') -> dict:
        """Resume one exact paused objective. Ambiguity is refused."""
        resolved, error = _resolve(run_id, about, mutate=True)
        if error:
            return error
        manager().resume_run(resolved)
        return _snapshot(resolved)

    @mcp.tool()
    def run_cancel(run_id: str = '', about: str = '') -> dict:
        """Cancel one exact objective permanently. Ambiguity is refused."""
        resolved, error = _resolve(run_id, about, mutate=True)
        if error:
            return error
        manager().cancel_run(resolved, 'person cancelled the run')
        return _snapshot(resolved)