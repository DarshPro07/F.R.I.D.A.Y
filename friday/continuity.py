"""Durable task and wake state for objectives that outlive one model turn."""
from __future__ import annotations
import json
import uuid
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import Callable
from friday import contracts as c
from friday.store import Store
IMMEDIATE = 'immediate'
SCHEDULED_RETRY = 'scheduled_retry'
TOOL_COMPLETION = 'tool_completion'
PROVIDER_AVAILABLE = 'provider_available'
EXTERNAL_STATE = 'external_state'
USER_INPUT = 'user_input'
USER_SECRET = 'user_secret'
PERMISSION = 'permission'
WAKE_KINDS = frozenset({IMMEDIATE, SCHEDULED_RETRY, TOOL_COMPLETION, PROVIDER_AVAILABLE, EXTERNAL_STATE, USER_INPUT, USER_SECRET, PERMISSION})
TERMINAL_STATES = frozenset({'partial', 'failed', 'cancelled', 'completed'})


class ContinuityInvariantError(ValueError):
    """A controlled run would no longer have a truthful continuation path."""
    pass


class StaleClaim(RuntimeError):
    """A lease or wake generation was replaced before this worker wrote."""
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _loads(raw: str | None, fallback):
    try:
        return json.loads(raw or '')
    except (TypeError, ValueError):
        return fallback


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


@dataclass(frozen=True)
class PortionBudget:
    max_actions: int = 3
    max_elapsed_seconds: int = 300
    max_model_tokens: int = 32000


@dataclass(frozen=True)
class RunBudget:
    max_actions: int = 72
    max_elapsed_seconds: int = 3600
    max_model_tokens: int = 250000
    max_portions: int = 24
    max_scheduler_retries: int = 6


@dataclass(frozen=True)
class WakeCondition:
    kind: str
    detail: str = ''
    due_at: datetime | None = None
    task_id: str = ''
    signal_key: str = ''

    def __post_init__(self) -> None:
        if self.kind not in WAKE_KINDS:
            raise ContinuityInvariantError(f"unknown wake kind {self.kind!r}")
        if self.kind in {USER_INPUT, USER_SECRET, PERMISSION} and not self.detail.strip():
            raise ContinuityInvariantError(f"{self.kind} wake requires an exact boundary")
        if self.kind in {TOOL_COMPLETION, PROVIDER_AVAILABLE, EXTERNAL_STATE} and not self.signal_key.strip():
            raise ContinuityInvariantError(f"{self.kind} wake requires a signal key")

    @classmethod
    def immediate(cls, detail: str = '') -> 'WakeCondition':
        return cls(IMMEDIATE, detail=detail)

    def with_task(self, task_id: str) -> 'WakeCondition':
        return replace(self, task_id=task_id)


@dataclass(frozen=True)
class RunSnapshot:
    run_id: str
    objective: str
    state: str
    outcome: str | None
    provenance: str
    attended: bool
    tasks: tuple[dict, ...]
    wake: dict | None
    counters: dict
    portion_budget: dict
    total_budget: dict
    checkpoint_version: int
    budget_exhausted: str = ''


@dataclass(frozen=True)
class PortionClaim:
    run_id: str
    portion_id: str
    task_id: str
    objective: str
    provenance: str
    attended: bool
    wake_generation: int
    lease_token: str
    worker_id: str
    checkpoint_summary: str = ''
    evidence_refs: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class AttemptReservation:
    attempt_id: str
    status: str
    execute: bool


class ContinuityManager:
    """Owns controlled-run transitions and their exactly-one-wake invariant."""

    def __init__(self, store: Store, *, clock: Callable[[], datetime] = _utcnow, lease_seconds: int = 30) -> None:
        self.store = store
        self.clock = clock
        self.lease_seconds = lease_seconds

    def start_run(self, objective: str, *, initial_task: str, provenance: str = c.PERSON, attended: bool = True, portion_budget: PortionBudget | None = None, total_budget: RunBudget | None = None) -> RunSnapshot:
        if not objective.strip() or not initial_task.strip():
            raise ContinuityInvariantError('objective and initial task must not be empty')
        if provenance not in c.PROVENANCES:
            raise ContinuityInvariantError(f"unknown provenance {provenance!r}")
        now = _iso(self.clock())
        run_id, task_id = c.new_run_id(), _id('TASK')
        portion = portion_budget or PortionBudget()
        total = total_budget or RunBudget()
        counters = {'actions': 0, 'elapsed_seconds': 0, 'model_tokens': 0, 'portions': 0, 'scheduler_retries': 0}
        with self.store._tx() as conn:
            conn.execute('INSERT INTO runs (run_id, request, state, capability, created_at, updated_at, error, attended, provenance) VALUES (?,?,?,?,?,?,?,?,?)', (run_id, objective, 'working', None, now, now, None, 1 if attended else 0, provenance))
            conn.execute('INSERT INTO run_controls (run_id, portion_budget, total_budget, counters, wake_generation) VALUES (?,?,?,?,1)', (run_id, json.dumps(asdict(portion)), json.dumps(asdict(total)), json.dumps(counters)))
            conn.execute('INSERT INTO run_tasks (task_id, run_id, description, status, idempotency_key, created_at, updated_at) VALUES (?,?,?,?,?,?,?)', (task_id, run_id, initial_task, 'runnable', f"initial:{task_id}", now, now))
            conn.execute('INSERT INTO run_wakes (run_id, generation, kind, task_id, due_at, detail, created_at) VALUES (?,?,?,?,?,?,?)', (run_id, 1, IMMEDIATE, task_id, now, 'objective accepted', now))
            self._event(conn, run_id, 'objective_accepted', objective, task_id=task_id, at=now)
            self._validate(conn, run_id)
        return self.status(run_id)

    def status(self, run_id: str) -> RunSnapshot:
        row = self.store._conn.execute('SELECT r.*, rc.outcome, rc.portion_budget, rc.total_budget, rc.counters, rc.checkpoint_version FROM runs r JOIN run_controls rc ON rc.run_id=r.run_id WHERE r.run_id=?', (run_id,)).fetchone()
        if row is None:
            raise LookupError(f"no controlled run {run_id}")
        tasks = tuple((dict(task) for task in self.store._conn.execute('SELECT * FROM run_tasks WHERE run_id=? ORDER BY created_at, task_id', (run_id,))))
        wake_row = self.store._conn.execute('SELECT * FROM run_wakes WHERE run_id=?', (run_id,)).fetchone()
        wake = dict(wake_row) if wake_row else None
        counters = _loads(row['counters'], {})
        outcome = row['outcome'] or ''
        exhausted = counters.get('budget_exhausted') or (f"run:{outcome.split(':', 1)[1]}" if outcome.startswith('budget_exhausted:') else '')
        return RunSnapshot(run_id=run_id, objective=row['request'], state=row['state'], outcome=row['outcome'], provenance=row['provenance'], attended=bool(row['attended']), tasks=tasks, wake=wake, counters=counters, portion_budget=_loads(row['portion_budget'], {}), total_budget=_loads(row['total_budget'], {}), checkpoint_version=int(row['checkpoint_version']), budget_exhausted=exhausted)

    def claim_next_due(self, worker_id: str) -> PortionClaim | None:
        self.recover_expired_leases()
        now = _iso(self.clock())
        rows = self.store._conn.execute("SELECT w.run_id FROM run_wakes w JOIN runs r ON r.run_id=w.run_id WHERE w.kind IN (?,?) AND (w.due_at IS NULL OR w.due_at<=?) AND r.state NOT IN ('completed','partial','failed','cancelled') ORDER BY w.created_at, w.run_id", (IMMEDIATE, SCHEDULED_RETRY, now)).fetchall()
        for row in rows:
            claim = self.claim_run(row['run_id'], worker_id)
            if claim is not None:
                return claim

    def recover_expired_leases(self) -> list[str]:
        now = _iso(self.clock())
        recovered = []
        with self.store._tx() as conn:
            rows = conn.execute("SELECT r.run_id, rc.wake_generation, w.task_id, w.kind, p.portion_id FROM runs r JOIN run_controls rc ON rc.run_id=r.run_id JOIN run_wakes w ON w.run_id=r.run_id LEFT JOIN run_portions p ON p.run_id=r.run_id AND p.status IN ('claimed','running') WHERE rc.lease_token IS NOT NULL AND rc.lease_until<=? AND r.state NOT IN ('completed','partial','failed','cancelled')", (now,)).fetchall()
            for row in rows:
                running_attempts = conn.execute("SELECT attempt_id FROM run_task_attempts WHERE run_id=? AND portion_id=? AND status='running'", (row['run_id'], row['portion_id'])).fetchall()
                generation = int(row['wake_generation']) + 1
                if running_attempts:
                    kind = EXTERNAL_STATE
                    signal_key = f"reconcile:{row['portion_id']}"
                    due_at = None
                    state = 'waiting_external'
                    detail = 'reconcile side effects from an interrupted attempt'
                    conn.execute("UPDATE run_task_attempts SET status='unknown', finished_at=?, error='worker lease expired before result persistence' WHERE run_id=? AND portion_id=? AND status='running'", (now, row['run_id'], row['portion_id']))
                    conn.execute("UPDATE run_tasks SET status='unknown', updated_at=? WHERE task_id=?", (now, row['task_id']))
                else:
                    kind = IMMEDIATE
                    signal_key = ''
                    due_at = now
                    state = 'working'
                    detail = 'recover after expired worker lease'
                    conn.execute("UPDATE run_tasks SET status='runnable', updated_at=? WHERE task_id=? AND status='running'", (now, row['task_id']))
                conn.execute('UPDATE run_controls SET lease_owner=NULL, lease_token=NULL, lease_until=NULL, wake_generation=? WHERE run_id=?', (generation, row['run_id']))
                conn.execute('UPDATE runs SET state=?, error=?, updated_at=? WHERE run_id=?', (state, detail, now, row['run_id']))
                conn.execute('UPDATE run_wakes SET generation=?, kind=?, due_at=?, detail=?, signal_key=?, created_at=? WHERE run_id=?', (generation, kind, due_at, detail, signal_key, now, row['run_id']))
                conn.execute("UPDATE run_portions SET status='interrupted', finished_at=? WHERE portion_id=?", (now, row['portion_id']))
                self._event(conn, row['run_id'], 'lease_expired', detail, task_id=row['task_id'], portion_id=row['portion_id'], at=now)
                self._validate(conn, row['run_id'])
                recovered.append(row['run_id'])
            pass
        return recovered

    def list_runs(self, limit: int = 20) -> list[RunSnapshot]:
        rows = self.store._conn.execute('SELECT r.run_id FROM runs r JOIN run_controls rc ON rc.run_id=r.run_id ORDER BY r.updated_at DESC LIMIT ?', (limit,)).fetchall()
        return [self.status(row['run_id']) for row in rows]

    def events(self, run_id: str, *, after: int = 0, limit: int = 50) -> list[dict]:
        return [dict(row) for row in self.store._conn.execute('SELECT * FROM run_events WHERE run_id=? AND event_id>? ORDER BY event_id LIMIT ?', (run_id, after, limit))]

    def claim_run(self, run_id: str, worker_id: str) -> PortionClaim | None:
        if not worker_id.strip():
            raise ValueError('worker_id must not be empty')
        now_dt = self.clock()
        now, lease_until = _iso(now_dt), _iso(now_dt + timedelta(seconds=self.lease_seconds))
        with self.store._tx() as conn:
            row = conn.execute('SELECT r.request, r.state, r.provenance, r.attended, r.created_at, rc.*, w.kind, w.task_id, w.due_at, w.generation FROM runs r JOIN run_controls rc ON rc.run_id=r.run_id JOIN run_wakes w ON w.run_id=r.run_id WHERE r.run_id=?', (run_id,)).fetchone()
            if row is None or row['state'] in TERMINAL_STATES:
                return None
            if row['kind'] not in {IMMEDIATE, SCHEDULED_RETRY}:
                return None
            if row['due_at'] and row['due_at'] > now:
                return None
            if row['lease_token'] and row['lease_until'] and row['lease_until'] > now:
                return None
            exhausted = self._budget_exhausted(row, now_dt)
            if exhausted:
                self._finish_budget(conn, run_id, exhausted, now)
                self._validate(conn, run_id)
                return None
            portion_id, lease_token = _id('PORTION'), uuid.uuid4().hex
            generation = int(row['generation']) + 1
            counters = _loads(row['counters'], {})
            counters['portions'] = int(counters.get('portions', 0)) + 1
            counters.pop('budget_exhausted', None)  # a fresh portion starts with a fresh portion budget
            conn.execute('UPDATE run_controls SET lease_owner=?, lease_token=?, lease_until=?, wake_generation=?, counters=? WHERE run_id=?', (worker_id, lease_token, lease_until, generation, json.dumps(counters), run_id))
            conn.execute("UPDATE runs SET state='working', error=NULL, updated_at=? WHERE run_id=?", (now, run_id))
            conn.execute('UPDATE run_wakes SET generation=?, kind=?, due_at=NULL, detail=?, signal_key=?, created_at=? WHERE run_id=?', (generation, TOOL_COMPLETION, 'active portion must settle', portion_id, now, run_id))
            conn.execute('INSERT INTO run_portions (portion_id, run_id, wake_generation, lease_token, status, started_at) VALUES (?,?,?,?,?,?)', (portion_id, run_id, generation, lease_token, 'running', now))
            conn.execute("UPDATE run_tasks SET status='running', updated_at=? WHERE task_id=?", (now, row['task_id']))
            self._event(conn, run_id, 'portion_started', 'bounded portion started', task_id=row['task_id'], portion_id=portion_id, at=now)
            self._validate(conn, run_id)
            checkpoint = conn.execute('SELECT summary, evidence_refs FROM run_checkpoints WHERE run_id=? ORDER BY version DESC LIMIT 1', (run_id,)).fetchone()
            return PortionClaim(run_id=run_id, portion_id=portion_id, task_id=row['task_id'], objective=row['request'], provenance=row['provenance'], attended=bool(row['attended']), wake_generation=generation, lease_token=lease_token, worker_id=worker_id, checkpoint_summary=checkpoint['summary'] if checkpoint else '', evidence_refs=tuple(_loads(checkpoint['evidence_refs'], [])) if checkpoint else ())

    def checkpoint(self, claim: PortionClaim, *, summary: str, wake: WakeCondition | None, completed_task_ids: tuple[str, ...] = (), evidence_refs: tuple[str, ...] = ()) -> RunSnapshot:
        if wake is None:
            raise ContinuityInvariantError('a non-terminal checkpoint requires one wake')
        now = _iso(self.clock())
        with self.store._tx() as conn:
            control = conn.execute('SELECT lease_token, wake_generation, checkpoint_version FROM run_controls WHERE run_id=?', (claim.run_id,)).fetchone()
            if control is None or control['lease_token'] != claim.lease_token or int(control['wake_generation']) != claim.wake_generation:
                raise StaleClaim(f"stale claim for {claim.run_id}")
            task_id = wake.task_id or claim.task_id
            task = conn.execute('SELECT run_id FROM run_tasks WHERE task_id=?', (task_id,)).fetchone()
            if task is None or task['run_id'] != claim.run_id:
                raise ContinuityInvariantError('checkpoint wake task must exist in the current run')
            for task_id in completed_task_ids:
                conn.execute("UPDATE run_tasks SET status='succeeded', evidence_refs=?, updated_at=? WHERE run_id=? AND task_id=?", (json.dumps(list(evidence_refs)), now, claim.run_id, task_id))
            self._unlock_tasks(conn, claim.run_id, now)
            if claim.task_id not in completed_task_ids:
                conn.execute("UPDATE run_tasks SET status='runnable', evidence_refs=?, updated_at=? WHERE task_id=?", (json.dumps(list(evidence_refs)), now, claim.task_id))
            generation = claim.wake_generation + 1
            task_id = wake.task_id or claim.task_id
            due_at = wake.due_at
            if wake.kind == IMMEDIATE and due_at is None:
                due_at = self.clock()
            conn.execute('UPDATE run_controls SET lease_owner=NULL, lease_token=NULL, lease_until=NULL, wake_generation=?, checkpoint_version=? WHERE run_id=?', (generation, int(control['checkpoint_version']) + 1, claim.run_id))
            state = self._state_for_wake(wake.kind)
            conn.execute('UPDATE runs SET state=?, error=NULL, updated_at=? WHERE run_id=?', (state, now, claim.run_id))
            conn.execute('UPDATE run_wakes SET generation=?, kind=?, task_id=?, due_at=?, detail=?, signal_key=?, created_at=? WHERE run_id=?', (generation, wake.kind, task_id, _iso(due_at) if due_at else None, wake.detail, wake.signal_key, now, claim.run_id))
            conn.execute("UPDATE run_portions SET status='checkpointed', finished_at=? WHERE portion_id=?", (now, claim.portion_id))
            version = int(control['checkpoint_version']) + 1
            conn.execute('INSERT INTO run_checkpoints (run_id, version, portion_id, current_task_id, completed_tasks, evidence_refs, wake_generation, summary, created_at) VALUES (?,?,?,?,?,?,?,?,?)', (claim.run_id, version, claim.portion_id, task_id, json.dumps(list(completed_task_ids)), json.dumps(list(evidence_refs)), generation, summary[:2000], now))
            self._event(conn, claim.run_id, 'checkpointed', summary[:400], task_id=task_id, portion_id=claim.portion_id, evidence_refs=evidence_refs, at=now)
            self._validate(conn, claim.run_id)
        return self.status(claim.run_id)

    def signal_wake(self, run_id: str, *, kind: str, signal_key: str) -> RunSnapshot:
        if kind not in {TOOL_COMPLETION, PROVIDER_AVAILABLE, EXTERNAL_STATE}:
            raise ContinuityInvariantError(f"{kind!r} is not externally signalable")
        now = _iso(self.clock())
        with self.store._tx() as conn:
            row = conn.execute('SELECT r.state, w.kind, w.signal_key, w.generation FROM runs r LEFT JOIN run_wakes w ON w.run_id=r.run_id WHERE r.run_id=?', (run_id,)).fetchone()
            if row is None:
                raise LookupError(f"no run {run_id}")
            if row['state'] in TERMINAL_STATES or row['kind'] != kind or row['signal_key'] != signal_key:
                return self.status(run_id)
            unknown = conn.execute("SELECT COUNT(*) FROM run_tasks WHERE run_id=? AND status='unknown'", (run_id,)).fetchone()[0]
            if unknown:
                return self.status(run_id)
            generation = int(row['generation']) + 1
            conn.execute('UPDATE run_controls SET wake_generation=? WHERE run_id=?', (generation, run_id))
            conn.execute("UPDATE run_wakes SET generation=?, kind=?, due_at=?, detail=?, signal_key='', created_at=? WHERE run_id=?", (generation, IMMEDIATE, now, f"{kind} signal received", now, run_id))
            conn.execute("UPDATE runs SET state='working', error=NULL, updated_at=? WHERE run_id=?", (now, run_id))
            self._event(conn, run_id, 'wake_signalled', f"{kind} became ready", at=now)
            self._validate(conn, run_id)
        return self.status(run_id)

    def provider_failed(self, claim: PortionClaim, *, error: str, retryable: bool) -> RunSnapshot:
        now_dt = self.clock()
        now = _iso(now_dt)
        with self.store._tx() as conn:
            control = self._assert_claim(conn, claim)
            counters = _loads(control['counters'], {})
            retries = int(counters.get('scheduler_retries', 0)) + 1
            counters['scheduler_retries'] = retries
            budget = _loads(control['total_budget'], {})
            can_retry = retryable and retries <= int(budget['max_scheduler_retries'])
            if not can_retry:
                conn.execute("UPDATE runs SET state='failed', error=?, updated_at=? WHERE run_id=?", (error, now, claim.run_id))
                conn.execute('UPDATE run_controls SET outcome=?, counters=?, lease_owner=NULL, lease_token=NULL, lease_until=NULL WHERE run_id=?', ('provider_retries_exhausted' if retryable else 'provider_failure', json.dumps(counters), claim.run_id))
                conn.execute('DELETE FROM run_wakes WHERE run_id=?', (claim.run_id,))
                conn.execute("UPDATE run_portions SET status='interrupted', finished_at=? WHERE portion_id=?", (now, claim.portion_id))
                self._event(conn, claim.run_id, 'provider_failed', error[:400], task_id=claim.task_id, portion_id=claim.portion_id, at=now)
            else:
                backoff = (2, 10, 30, 60, 120, 300)[retries - 1]
                due = _iso(now_dt + timedelta(seconds=backoff))
                generation = claim.wake_generation + 1
                conn.execute("UPDATE runs SET state='retrying', error=?, updated_at=? WHERE run_id=?", (error, now, claim.run_id))
                conn.execute('UPDATE run_controls SET outcome=NULL, counters=?, lease_owner=NULL, lease_token=NULL, lease_until=NULL, wake_generation=? WHERE run_id=?', (json.dumps(counters), generation, claim.run_id))
                conn.execute("UPDATE run_tasks SET status='runnable', updated_at=? WHERE task_id=?", (now, claim.task_id))
                conn.execute("UPDATE run_wakes SET generation=?, kind=?, due_at=?, detail=?, signal_key='', created_at=? WHERE run_id=?", (generation, SCHEDULED_RETRY, due, f"provider retry {retries} after {backoff}s", now, claim.run_id))
                conn.execute("UPDATE run_portions SET status='interrupted', finished_at=? WHERE portion_id=?", (now, claim.portion_id))
                self._event(conn, claim.run_id, 'provider_retry_scheduled', f"retry {retries} scheduled after {backoff}s", task_id=claim.task_id, portion_id=claim.portion_id, at=now)
            self._validate(conn, claim.run_id)
        return self.status(claim.run_id)

    def reserve_attempt(self, claim: PortionClaim, *, task_id: str, idempotency_key: str) -> AttemptReservation:
        if not idempotency_key.strip():
            raise ValueError('idempotency_key must not be empty')
        now = _iso(self.clock())
        with self.store._tx() as conn:
            self._assert_claim(conn, claim)
            task = conn.execute('SELECT run_id FROM run_tasks WHERE task_id=?', (task_id,)).fetchone()
            if task is None or task['run_id'] != claim.run_id:
                raise ContinuityInvariantError('attempt task must belong to the claimed run')
            existing = conn.execute('SELECT attempt_id, status FROM run_task_attempts WHERE run_id=? AND idempotency_key=?', (claim.run_id, idempotency_key)).fetchone()
            if existing:
                return AttemptReservation(existing['attempt_id'], existing['status'], False)
            attempt_id = _id('ATTEMPT')
            conn.execute("INSERT INTO run_task_attempts (attempt_id, task_id, run_id, portion_id, idempotency_key, status, started_at) VALUES (?,?,?,?,?,'running',?)", (attempt_id, task_id, claim.run_id, claim.portion_id, idempotency_key, now))
            conn.execute('UPDATE run_tasks SET attempt_count=attempt_count+1, updated_at=? WHERE task_id=?', (now, task_id))
            return AttemptReservation(attempt_id, 'running', True)

    def record_actions(self, claim: PortionClaim, *, count: int, evidence_refs: tuple[str, ...] = ()) -> RunSnapshot:
        if count < 0:
            raise ValueError('action count cannot be negative')
        now = _iso(self.clock())
        with self.store._tx() as conn:
            control = self._assert_claim(conn, claim)
            counters = _loads(control['counters'], {})
            counters['actions'] = int(counters.get('actions', 0)) + count
            conn.execute('UPDATE run_controls SET counters=? WHERE run_id=?', (json.dumps(counters), claim.run_id))
            conn.execute('UPDATE run_portions SET action_count=action_count+? WHERE portion_id=?', (count, claim.portion_id))
            if count:
                self._event(conn, claim.run_id, 'actions_observed', f"{count} tool action(s) settled", task_id=claim.task_id, portion_id=claim.portion_id, evidence_refs=evidence_refs, at=now)
            self._enforce_budgets(conn, claim, self.clock())
        return self.status(claim.run_id)

    def record_model_tokens(self, claim: PortionClaim, count: int) -> RunSnapshot:
        if count < 0:
            raise ValueError('model token count cannot be negative')
        with self.store._tx() as conn:
            control = self._assert_claim(conn, claim)
            counters = _loads(control['counters'], {})
            counters['model_tokens'] = int(counters.get('model_tokens', 0)) + count
            conn.execute('UPDATE run_controls SET counters=? WHERE run_id=?', (json.dumps(counters), claim.run_id))
            conn.execute('UPDATE run_portions SET model_tokens=model_tokens+? WHERE portion_id=?', (count, claim.portion_id))
            self._enforce_budgets(conn, claim, self.clock())
        return self.status(claim.run_id)

    def remaining_budget(self, claim: PortionClaim) -> dict:
        """What this portion and this run may still spend, for pre-call sizing.

        A caller that knows the remaining headroom can shrink the next request
        instead of discovering the cap only after the tokens are gone.
        """
        control = self.store._conn.execute('SELECT portion_budget, total_budget, counters FROM run_controls WHERE run_id=?', (claim.run_id,)).fetchone()
        portion = self.store._conn.execute('SELECT action_count, model_tokens FROM run_portions WHERE portion_id=?', (claim.portion_id,)).fetchone()
        if control is None or portion is None:
            raise LookupError(f"no claimed portion {claim.portion_id}")
        pb, tb, counters = _loads(control['portion_budget'], {}), _loads(control['total_budget'], {}), _loads(control['counters'], {})
        return {
            'portion': {'model_tokens': max(0, int(pb.get('max_model_tokens', 0)) - int(portion['model_tokens'])), 'actions': max(0, int(pb.get('max_actions', 0)) - int(portion['action_count']))},
            'run': {'model_tokens': max(0, int(tb.get('max_model_tokens', 0)) - int(counters.get('model_tokens', 0))), 'actions': max(0, int(tb.get('max_actions', 0)) - int(counters.get('actions', 0))), 'portions': max(0, int(tb.get('max_portions', 0)) - int(counters.get('portions', 0)))},
        }

    def _enforce_budgets(self, conn, claim: PortionClaim, now: datetime) -> None:
        """Stop at the spend, not at the next claim.

        Tokens arrive after the fact from provider usage events, and the only
        old check ran when the NEXT portion was claimed -- so single portions
        burned 229k against a 32k cap and runs ended 100k past a 250k total.
        A crossed total budget ends the run here; a crossed portion budget
        records one event and marks the claim so the caller stops this portion.
        """
        stamp = _iso(now)
        row = conn.execute('SELECT rc.counters, rc.portion_budget, rc.total_budget, r.created_at FROM run_controls rc JOIN runs r ON r.run_id=rc.run_id WHERE rc.run_id=?', (claim.run_id,)).fetchone()
        total = self._budget_exhausted(row, now)
        if total:
            self._finish_budget(conn, claim.run_id, total, stamp)
            self._validate(conn, claim.run_id)
            return
        counters = _loads(row['counters'], {})
        if counters.get('budget_exhausted'):
            return
        portion = conn.execute('SELECT action_count, model_tokens FROM run_portions WHERE portion_id=?', (claim.portion_id,)).fetchone()
        budget = _loads(row['portion_budget'], {})
        for name, spent in (('model_tokens', portion['model_tokens']), ('actions', portion['action_count'])):
            cap = int(budget.get(f"max_{name}", 0))
            if cap and int(spent) >= cap:
                counters['budget_exhausted'] = f"portion:{name}"
                conn.execute('UPDATE run_controls SET counters=? WHERE run_id=?', (json.dumps(counters), claim.run_id))
                self._event(conn, claim.run_id, 'budget_exhausted', f"portion:{name} {spent}/{cap}", task_id=claim.task_id, portion_id=claim.portion_id, at=stamp)
                return

    def reserve_narration(self, claim: PortionClaim, *, milestone_key: str, speech_id: str) -> bool:
        """Atomically reserve one semantic milestone narration for this run."""
        if not milestone_key.strip() or not speech_id.strip():
            raise ValueError('milestone_key and speech_id must not be empty')
        now = _iso(self.clock())
        with self.store._tx() as conn:
            self._assert_claim(conn, claim)
            inserted = conn.execute('INSERT OR IGNORE INTO run_narrations (run_id, milestone_key, speech_id, created_at) VALUES (?,?,?,?)', (claim.run_id, milestone_key, speech_id, now)).rowcount == 1
            if inserted:
                self._event(conn, claim.run_id, 'narration_reserved', milestone_key, task_id=claim.task_id, portion_id=claim.portion_id, at=now)
        return inserted

    def settle_attempt(self, claim: PortionClaim, attempt_id: str, *, status: str, result_ref: str = '', error: str = '') -> AttemptReservation:
        if status not in {'cancelled', 'failed', 'succeeded', 'unknown'}:
            raise ValueError(f"cannot settle attempt as {status!r}")
        if status == 'succeeded' and not result_ref.strip():
            raise ContinuityInvariantError('a successful attempt requires a result reference')
        now = _iso(self.clock())
        with self.store._tx() as conn:
            self._assert_claim(conn, claim)
            row = conn.execute('SELECT run_id, status FROM run_task_attempts WHERE attempt_id=?', (attempt_id,)).fetchone()
            if row is None or row['run_id'] != claim.run_id:
                raise ContinuityInvariantError('attempt does not belong to the claimed run')
            if row['status'] != 'running':
                return AttemptReservation(attempt_id, row['status'], False)
            conn.execute('UPDATE run_task_attempts SET status=?, finished_at=?, result_ref=?, error=? WHERE attempt_id=?', (status, now, result_ref or None, error or None, attempt_id))
            return AttemptReservation(attempt_id, status, False)

    def settle_abandoned_attempt(self, claim: PortionClaim, attempt_id: str, *, error: str) -> AttemptReservation:
        """Mark an old portion's running side effect unknown without reviving it."""
        now = _iso(self.clock())
        with self.store._tx() as conn:
            row = conn.execute('SELECT run_id, portion_id, status FROM run_task_attempts WHERE attempt_id=?', (attempt_id,)).fetchone()
            if row is None or row['run_id'] != claim.run_id or row['portion_id'] != claim.portion_id:
                raise ContinuityInvariantError('abandoned attempt does not belong to the original portion')
            if row['status'] == 'running':
                conn.execute("UPDATE run_task_attempts SET status='unknown', finished_at=?, error=? WHERE attempt_id=?", (now, error[:400], attempt_id))
                return AttemptReservation(attempt_id, 'unknown', False)
            return AttemptReservation(attempt_id, row['status'], False)

    def add_task(self, claim: PortionClaim, *, description: str, dependencies: tuple[str, ...] = (), verification_required: bool = False) -> str:
        if not description.strip():
            raise ContinuityInvariantError('task description must not be empty')
        now, task_id = _iso(self.clock()), _id('TASK')
        with self.store._tx() as conn:
            self._assert_claim(conn, claim)
            for dependency in dependencies:
                row = conn.execute('SELECT run_id FROM run_tasks WHERE task_id=?', (dependency,)).fetchone()
                if row is None or row['run_id'] != claim.run_id:
                    raise ContinuityInvariantError('task dependency must belong to this run')
            status = 'pending' if dependencies else 'runnable'
            conn.execute('INSERT INTO run_tasks (task_id, run_id, description, status, dependencies, idempotency_key, verification_required, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)', (task_id, claim.run_id, description, status, json.dumps(list(dependencies)), f"task:{task_id}", 1 if verification_required else 0, now, now))
        return task_id

    def pause_run(self, run_id: str, reason: str) -> RunSnapshot:
        return self._control_wait(run_id, state='paused', kind=USER_INPUT, detail=reason or 'resume this run')

    def resume_run(self, run_id: str) -> RunSnapshot:
        now = _iso(self.clock())
        with self.store._tx() as conn:
            row = conn.execute('SELECT r.state, rc.wake_generation FROM runs r JOIN run_controls rc ON rc.run_id=r.run_id WHERE r.run_id=?', (run_id,)).fetchone()
            if row is None:
                raise LookupError(f"no controlled run {run_id}")
            if row['state'] == 'cancelled':
                return self.status(run_id)
            if row['state'] in TERMINAL_STATES:
                return self.status(run_id)
            generation = int(row['wake_generation']) + 1
            task = conn.execute("SELECT task_id FROM run_tasks WHERE run_id=? AND status IN ('running','runnable','waiting','pending') ORDER BY created_at LIMIT 1", (run_id,)).fetchone()
            conn.execute("UPDATE runs SET state='working', error=NULL, updated_at=? WHERE run_id=?", (now, run_id))
            conn.execute('UPDATE run_controls SET wake_generation=? WHERE run_id=?', (generation, run_id))
            conn.execute('INSERT INTO run_wakes (run_id, generation, kind, task_id, due_at, detail, signal_key, created_at) VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(run_id) DO UPDATE SET generation=excluded.generation, kind=excluded.kind, task_id=excluded.task_id, due_at=excluded.due_at, detail=excluded.detail, signal_key=excluded.signal_key, created_at=excluded.created_at', (run_id, generation, IMMEDIATE, task['task_id'] if task else None, now, 'person resumed the run', '', now))
            self._event(conn, run_id, 'resumed', 'person resumed the run', at=now)
            self._validate(conn, run_id)
        return self.status(run_id)

    def cancel_run(self, run_id: str, reason: str) -> RunSnapshot:
        now = _iso(self.clock())
        with self.store._tx() as conn:
            row = conn.execute('SELECT state FROM runs WHERE run_id=?', (run_id,)).fetchone()
            if row is None:
                raise LookupError(f"no run {run_id}")
            if row['state'] not in TERMINAL_STATES:
                conn.execute("UPDATE runs SET state='cancelled', error=?, updated_at=? WHERE run_id=?", (reason, now, run_id))
                conn.execute("UPDATE run_controls SET outcome='cancelled', lease_owner=NULL, lease_token=NULL, lease_until=NULL, wake_generation=wake_generation+1 WHERE run_id=?", (run_id,))
                conn.execute('DELETE FROM run_wakes WHERE run_id=?', (run_id,))
                conn.execute("UPDATE run_portions SET status='cancelled', finished_at=? WHERE run_id=? AND status IN ('claimed','running')", (now, run_id))
                conn.execute("UPDATE run_tasks SET status='cancelled', updated_at=? WHERE run_id=? AND status NOT IN ('succeeded','failed','cancelled')", (now, run_id))
                self._event(conn, run_id, 'cancelled', reason, at=now)
            self._validate(conn, run_id)
        return self.status(run_id)

    def complete_run(self, claim: PortionClaim, *, verification_task_id: str, evidence_refs: tuple[str, ...]) -> RunSnapshot:
        if not evidence_refs:
            raise ContinuityInvariantError('verification requires evidence')
        now = _iso(self.clock())
        with self.store._tx() as conn:
            self._assert_claim(conn, claim)
            observed = set()
            for row in conn.execute("SELECT evidence_refs FROM run_events WHERE run_id=? AND kind='actions_observed'", (claim.run_id,)):
                observed.update((str(ref) for ref in _loads(row['evidence_refs'], [])))
            missing = set(evidence_refs) - observed
            if missing:
                raise ContinuityInvariantError('verification evidence was not observed by the current run: ' + ', '.join(sorted(missing)))
            verification = conn.execute('SELECT run_id, verification_required FROM run_tasks WHERE task_id=?', (verification_task_id,)).fetchone()
            if verification is None or verification['run_id'] != claim.run_id or not verification['verification_required']:
                raise ContinuityInvariantError('a verification task from this run is required')
            conn.execute("UPDATE run_tasks SET status='succeeded', evidence_refs=?, updated_at=? WHERE task_id=?", (json.dumps(list(evidence_refs)), now, verification_task_id))
            remaining = conn.execute("SELECT task_id FROM run_tasks WHERE run_id=? AND status!='succeeded'", (claim.run_id,)).fetchall()
            if remaining:
                raise ContinuityInvariantError(f"cannot complete; {len(remaining)} task(s) are not succeeded")
            conn.execute("UPDATE runs SET state='completed', error=NULL, updated_at=? WHERE run_id=?", (now, claim.run_id))
            conn.execute("UPDATE run_controls SET outcome='succeeded', lease_owner=NULL, lease_token=NULL, lease_until=NULL WHERE run_id=?", (claim.run_id,))
            conn.execute('DELETE FROM run_wakes WHERE run_id=?', (claim.run_id,))
            conn.execute("UPDATE run_portions SET status='completed', finished_at=? WHERE portion_id=?", (now, claim.portion_id))
            self._event(conn, claim.run_id, 'completed', 'verification succeeded', task_id=verification_task_id, portion_id=claim.portion_id, evidence_refs=evidence_refs, at=now)
            self._validate(conn, claim.run_id)
        return self.status(claim.run_id)

    def _control_wait(self, run_id: str, *, state: str, kind: str, detail: str) -> RunSnapshot:
        now = _iso(self.clock())
        with self.store._tx() as conn:
            row = conn.execute('SELECT r.state, rc.wake_generation FROM runs r JOIN run_controls rc ON rc.run_id=r.run_id WHERE r.run_id=?', (run_id,)).fetchone()
            if row is None:
                raise LookupError(f"no controlled run {run_id}")
            if row['state'] in TERMINAL_STATES:
                return self.status(run_id)
            generation = int(row['wake_generation']) + 1
            task = conn.execute("SELECT task_id FROM run_tasks WHERE run_id=? AND status IN ('running','runnable','waiting','pending') ORDER BY created_at LIMIT 1", (run_id,)).fetchone()
            conn.execute('UPDATE runs SET state=?, error=?, updated_at=? WHERE run_id=?', (state, detail, now, run_id))
            conn.execute('UPDATE run_controls SET lease_owner=NULL, lease_token=NULL, lease_until=NULL, wake_generation=? WHERE run_id=?', (generation, run_id))
            conn.execute("UPDATE run_portions SET status='interrupted', finished_at=? WHERE run_id=? AND status IN ('claimed','running')", (now, run_id))
            conn.execute("UPDATE run_tasks SET status='runnable', updated_at=? WHERE run_id=? AND status='running'", (now, run_id))
            conn.execute('INSERT INTO run_wakes (run_id, generation, kind, task_id, due_at, detail, signal_key, created_at) VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(run_id) DO UPDATE SET generation=excluded.generation, kind=excluded.kind, task_id=excluded.task_id, due_at=excluded.due_at, detail=excluded.detail, signal_key=excluded.signal_key, created_at=excluded.created_at', (run_id, generation, kind, task['task_id'] if task else None, None, detail, '', now))
            self._event(conn, run_id, state, detail, at=now)
            self._validate(conn, run_id)
        return self.status(run_id)

    @staticmethod
    def _assert_claim(conn, claim: PortionClaim):
        control = conn.execute('SELECT lease_token, wake_generation, checkpoint_version, counters, total_budget FROM run_controls WHERE run_id=?', (claim.run_id,)).fetchone()
        if control is None or control['lease_token'] != claim.lease_token or int(control['wake_generation']) != claim.wake_generation:
            raise StaleClaim(f"stale claim for {claim.run_id}")
        return control

    @staticmethod
    def _unlock_tasks(conn, run_id: str, now: str) -> None:
        pending = conn.execute("SELECT task_id, dependencies FROM run_tasks WHERE run_id=? AND status='pending'", (run_id,)).fetchall()
        for task in pending:
            dependencies = _loads(task['dependencies'], [])
            if not dependencies:
                ready = True
            else:
                marks = ','.join(('?' for _ in dependencies))
                count = conn.execute(f"SELECT COUNT(*) FROM run_tasks WHERE run_id=? AND task_id IN ({marks}) AND status='succeeded'", (run_id, *dependencies)).fetchone()[0]
                ready = count == len(dependencies)
            if ready:
                conn.execute("UPDATE run_tasks SET status='runnable', updated_at=? WHERE task_id=?", (now, task['task_id']))

    @staticmethod
    def _state_for_wake(kind: str) -> str:
        return {IMMEDIATE: 'working', SCHEDULED_RETRY: 'retrying', TOOL_COMPLETION: 'waiting_tool', PROVIDER_AVAILABLE: 'waiting_external', EXTERNAL_STATE: 'waiting_external', USER_INPUT: 'waiting_user', USER_SECRET: 'waiting_secret', PERMISSION: 'waiting_permission'}[kind]

    @staticmethod
    def _budget_exhausted(row, now: datetime) -> str:
        counters = _loads(row['counters'], {})
        # Stored JSON from an older run may lack a key; a KeyError here would
        # be swallowed upstream and silently end enforcement (review, 2026-09-03).
        budget = {**asdict(RunBudget()), **_loads(row['total_budget'], {})}
        if int(counters.get('portions', 0)) >= int(budget['max_portions']):
            return 'portions'
        if int(counters.get('actions', 0)) >= int(budget['max_actions']):
            return 'actions'
        if int(counters.get('model_tokens', 0)) >= int(budget['max_model_tokens']):
            return 'model_tokens'
        created = datetime.fromisoformat(row['created_at'])
        if (now - created).total_seconds() >= int(budget['max_elapsed_seconds']):
            return 'elapsed_time'
        return ''

    def _finish_budget(self, conn, run_id: str, exhausted: str, now: str) -> None:
        outcome = f"budget_exhausted:{exhausted}"
        conn.execute("UPDATE runs SET state='partial', error=?, updated_at=? WHERE run_id=?", (outcome, now, run_id))
        conn.execute('UPDATE run_controls SET outcome=?, lease_owner=NULL, lease_token=NULL, lease_until=NULL WHERE run_id=?', (outcome, run_id))
        conn.execute('DELETE FROM run_wakes WHERE run_id=?', (run_id,))
        self._event(conn, run_id, 'budget_exhausted', outcome, at=now)

    @staticmethod
    def _event(conn, run_id: str, kind: str, message: str, *, task_id: str = '', portion_id: str = '', evidence_refs=(), at: str) -> None:
        conn.execute('INSERT INTO run_events (run_id, task_id, portion_id, kind, message, evidence_refs, created_at) VALUES (?,?,?,?,?,?,?)', (run_id, task_id or None, portion_id or None, kind, message, json.dumps(list(evidence_refs)), at))

    @staticmethod
    def _validate(conn, run_id: str) -> None:
        row = conn.execute('SELECT state FROM runs WHERE run_id=?', (run_id,)).fetchone()
        if row is None:
            raise ContinuityInvariantError(f"missing run {run_id}")
        wake_count = conn.execute('SELECT COUNT(*) FROM run_wakes WHERE run_id=?', (run_id,)).fetchone()[0]
        expected = 0 if row['state'] in TERMINAL_STATES else 1
        if wake_count != expected:
            raise ContinuityInvariantError(f"{run_id}: state {row['state']} requires {expected} wake, found {wake_count}")