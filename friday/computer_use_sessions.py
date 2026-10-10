"""
Computer-use session registry (PRD v7 CUA-008).

Concurrent computer-use sessions must not act on each other's windows. A
session here has an owner (the objective and the worker that opened it), a
lease with an expiry, the set of windows and applications it holds, and
resource limits. The lease is a real resource-governor lease
(`friday/governor.py`, kind BROWSER, the governor's kind for an automation
session that drives a UI), so the governor's caps, pressure gate, deadline
reaping and `owned_processes()` record cover these sessions the same as any
other admitted work.

Rules, all enforced here and each refusal recorded (`refusals()`):

  * a second session on a window or application another live session holds
    is refused (WINDOW_TAKEN / APP_TAKEN), naming the holder;
  * an expired lease frees its windows: the session becomes EXPIRED, its
    governor lease is returned, and a later `authorize()` raises
    `SessionStopped`; a lease the governor took back is REVOKED the same way;
  * a session may act only on windows in its own set (NOT_IN_SET) and only up
    to its action limit (ACTION_LIMIT); it may hold at most `max_windows`.

The application boundary applies between sessions that both declare the
application: a session that declares no `apps` is bounded by its windows only.

Governor queue: a QUEUE answer from `admit()` is recorded by the governor as a
waiting entry. The registry turns it into a refusal (nobody waits) and returns
one governor queue entry per lease it releases (close and expiry use the
governor's default `release()`). The governor has no call to withdraw a queue
entry or to ask "would you admit" without recording, so several refused opens
in a row leave more entries than releases retire; closing that needs a
governor seam (a withdraw or dry-run call), not a workaround here.

Window and application names are caller-supplied identifiers (untrusted
data); they are compared normalised (trimmed, case-folded) and never
interpreted.

The registry is pure bookkeeping: it does not click, type or capture.
Opening a session before a computer-use step acts, and calling `authorize()`
for each action, is the wiring step that belongs in `friday/computer_use.py`.
"""
from __future__ import annotations

import logging
import math
import threading
import time
import uuid
from dataclasses import dataclass, field

from friday import governor as _governor

logger = logging.getLogger(__name__)

# -- session states ----------------------------------------------------------
ACTIVE = "active"
CLOSED = "closed"
EXPIRED = "expired"      # the lease ran out
REVOKED = "revoked"      # the governor took the lease back

# -- refusal codes -----------------------------------------------------------
WINDOW_TAKEN = "window_taken"
APP_TAKEN = "app_taken"
NOT_IN_SET = "window_not_in_session"
WINDOW_LIMIT = "window_limit"
ACTION_LIMIT = "action_limit"
NOT_ADMITTED = "governor_did_not_admit"

DEFAULT_TTL_S = 300.0
DEFAULT_MAX_WINDOWS = 4
DEFAULT_MAX_ACTIONS = 500
_HISTORY = 200


class SessionRefused(RuntimeError):
    """A session or an action the registry will not allow. `record` is the
    entry appended to `refusals()`."""

    def __init__(self, record: dict) -> None:
        super().__init__(f"{record['reason_code']}: {record['detail']}")
        self.record = record


class SessionStopped(RuntimeError):
    """The session is no longer active (closed, expired or revoked)."""


def _norm(name: str) -> str:
    out = str(name).strip().casefold()
    if not out:
        raise ValueError("a window or application name must not be empty")
    return out


@dataclass
class Session:
    session_id: str
    objective_id: str
    worker: str
    lease: str
    windows: frozenset
    apps: frozenset
    opened_at: float
    expires_at: float
    max_windows: int
    max_actions: int
    actions: int = 0
    state: str = ACTIVE
    ended_at: float | None = field(default=None)

    def to_dict(self) -> dict:
        return {"session_id": self.session_id,
                "owner": {"objective_id": self.objective_id, "worker": self.worker},
                "lease": self.lease, "state": self.state,
                "windows": sorted(self.windows), "apps": sorted(self.apps),
                "opened_at": self.opened_at, "expires_at": self.expires_at,
                "ended_at": self.ended_at, "actions": self.actions,
                "limits": {"max_windows": self.max_windows,
                           "max_actions": self.max_actions}}


class SessionRegistry:
    """The sessions of one process. `governor` and `clock` are injectable so
    tests drive admission and time deterministically."""

    def __init__(self, *, governor: _governor.Governor | None = None, clock=time.time) -> None:
        self._governor = governor
        self._clock = clock
        # Re-entrant: opening a session asks the governor for pressure, which
        # may run another session's cleanup on this same thread.
        self._lock = threading.RLock()
        self._sessions: dict[str, Session] = {}
        self._refusals: list[dict] = []

    # -- helpers --------------------------------------------------------------

    def _gov(self) -> _governor.Governor:
        return self._governor if self._governor is not None else _governor.governor()

    def _refuse(self, code: str, detail: str, *, objective_id: str, worker: str,
                resource: str = "", held_by: str = "") -> SessionRefused:
        record = {"at": self._clock(), "reason_code": code, "detail": detail,
                  "objective_id": objective_id, "worker": worker,
                  "resource": resource, "held_by": held_by}
        with self._lock:
            self._refusals.append(record)
            del self._refusals[:-_HISTORY]
        logger.warning("computer-use session refused (%s): %s", code, detail)
        return SessionRefused(record)

    def _end(self, s: Session, state: str) -> None:
        s.state = state
        s.ended_at = self._clock()

    def _sweep(self) -> None:
        """End every session whose lease ran out or that the governor no longer
        holds, returning its governor lease. Called before any decision."""
        now = self._clock()
        with self._lock:
            live = [s for s in self._sessions.values() if s.state == ACTIVE]
            if not live:
                return
            held = {x["lease"] for x in self._gov().active_leases()}
            for s in live:
                if now >= s.expires_at:
                    self._end(s, EXPIRED)
                    self._gov().release(s.lease)
                elif s.lease not in held:
                    self._end(s, REVOKED)

    def _on_governor_stop(self, session_id: str) -> None:
        """The governor's cleanup hook: it is stopping this lease (deadline,
        pressure) and will release it. Must not block."""
        with self._lock:
            s = self._sessions.get(session_id)
            if s is not None and s.state == ACTIVE:
                self._end(s, EXPIRED if self._clock() >= s.expires_at else REVOKED)

    def _holder(self, resource: str, *, apps: bool = False, skip: str = "") -> Session | None:
        for s in self._sessions.values():
            if s.state == ACTIVE and s.session_id != skip and resource in (s.apps if apps else s.windows):
                return s
        return None

    # -- the registry ---------------------------------------------------------

    def open(self, *, objective_id: str, worker: str, windows, apps=(),
             ttl_s: float = DEFAULT_TTL_S, max_windows: int = DEFAULT_MAX_WINDOWS,
             max_actions: int = DEFAULT_MAX_ACTIONS) -> Session:
        if not str(objective_id).strip() or not str(worker).strip():
            raise ValueError("a computer-use session needs an owning objective and worker")
        if isinstance(windows, (str, bytes)) or isinstance(apps, (str, bytes)):
            raise ValueError("windows and apps must be collections of names, not one string")
        wins = frozenset(_norm(w) for w in windows)
        app_set = frozenset(_norm(a) for a in apps)
        if not wins:
            raise ValueError("a computer-use session needs at least one window")
        if not math.isfinite(ttl_s) or ttl_s <= 0:
            raise ValueError("ttl_s must be a finite positive number of seconds")
        if max_windows < 1 or max_actions < 1:
            raise ValueError("max_windows and max_actions must be positive")
        who = {"objective_id": objective_id, "worker": worker}
        with self._lock:
            self._sweep()
            if len(wins) > max_windows:
                raise self._refuse(WINDOW_LIMIT, f"{len(wins)} windows exceed the limit of {max_windows}", **who)
            for w in sorted(wins):
                held = self._holder(w)
                if held is not None:
                    raise self._refuse(WINDOW_TAKEN, f"window {w!r} is held by session {held.session_id}",
                                       resource=w, held_by=held.session_id, **who)
            for a in sorted(app_set):
                held = self._holder(a, apps=True)
                if held is not None:
                    raise self._refuse(APP_TAKEN, f"application {a!r} is held by session {held.session_id}",
                                       resource=a, held_by=held.session_id, **who)
            session_id = f"cua-{uuid.uuid4().hex[:12]}"
            decision = self._gov().admit(
                _governor.BROWSER, label=f"computer-use:{session_id}",
                objective_id=objective_id, owner=worker, deadline_s=ttl_s,
                cleanup=lambda: self._on_governor_stop(session_id))
            if not decision.admitted:
                raise self._refuse(NOT_ADMITTED, decision.reason, **who)
            now = self._clock()
            s = Session(session_id=session_id, objective_id=objective_id, worker=worker,
                        lease=decision.lease, windows=wins, apps=app_set, opened_at=now,
                        expires_at=now + ttl_s, max_windows=max_windows, max_actions=max_actions)
            self._sessions[session_id] = s
            return s

    def authorize(self, session_id: str, window: str) -> None:
        """Permit one action on `window` by this session, or raise. Counts the
        action against the session limit."""
        w = _norm(window)
        with self._lock:
            self._sweep()
            s = self._get(session_id)
            if s.state != ACTIVE:
                raise SessionStopped(f"session {session_id} is {s.state}")
            who = {"objective_id": s.objective_id, "worker": s.worker}
            if w not in s.windows:
                other = self._holder(w, skip=session_id)
                raise self._refuse(NOT_IN_SET, f"window {w!r} is not in session {session_id}",
                                   resource=w, held_by=other.session_id if other else "", **who)
            if s.actions >= s.max_actions:
                raise self._refuse(ACTION_LIMIT, f"session {session_id} used its {s.max_actions} actions",
                                   resource=w, **who)
            s.actions += 1

    def close(self, session_id: str) -> None:
        """End the session and return its lease. Idempotent."""
        with self._lock:
            self._sweep()
            s = self._get(session_id)
            if s.state == ACTIVE:
                self._end(s, CLOSED)
                self._gov().release(s.lease)

    def _get(self, session_id: str) -> Session:
        s = self._sessions.get(session_id)
        if s is None:
            raise KeyError(f"unknown computer-use session {session_id!r}")
        return s

    def describe(self, session_id: str) -> dict:
        with self._lock:
            self._sweep()
            return self._get(session_id).to_dict()

    def active(self) -> list[dict]:
        with self._lock:
            self._sweep()
            return [s.to_dict() for s in self._sessions.values() if s.state == ACTIVE]

    def refusals(self) -> list[dict]:
        with self._lock:
            return list(self._refusals)


_REGISTRY: SessionRegistry | None = None
_REGISTRY_LOCK = threading.Lock()


def registry() -> SessionRegistry:
    """The process-wide registry, on the process governor. Every caller that
    opens computer-use sessions must use this one, or two sessions can hold
    the same window."""
    global _REGISTRY
    with _REGISTRY_LOCK:
        if _REGISTRY is None:
            _REGISTRY = SessionRegistry()
        return _REGISTRY


def configure(new: SessionRegistry | None) -> None:
    """Test seam."""
    global _REGISTRY
    with _REGISTRY_LOCK:
        _REGISTRY = new
