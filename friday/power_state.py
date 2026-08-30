"""
The note Friday leaves itself before the machine may stop.

Every other capability can record its own outcome, because it is still running
when the outcome arrives. These cannot. Ask Windows to restart and Friday has
perhaps thirty seconds of life left; ask it to sleep and the answer may arrive
after the process has been frozen mid-sentence. Whatever was going to write
"that worked" does not run.

So the record is written **before** the request goes out, and settled
afterwards by a different run entirely - one that can see the machine's boot
time and compare it with what was recorded. That comparison is the evidence.
It is not the earlier run's word for anything, which is the point: the earlier
run is in no position to give its word, and a system that accepted one would
be believing a process about what happened after it stopped.

    remember()   before the call, always
    reconcile()  at startup, before anything else runs

The three-way answer:

    boot id changed              OBSERVED         it really did restart
    same, and the deadline gone  NOT_CARRIED_OUT  accepted, then stopped
    same, still inside the window  INITIATED      too early to say
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import psutil

from friday import contracts as c
from friday.store import Store

#: What may be recorded here. Exactly the actions the feature offers - no
#: more, so that the offered set, the recordable set and the policy set cannot
#: drift apart. Signing out is not among them in v1.
ACTIONS = ("SLEEP", "HIBERNATE", "SHUTDOWN", "RESTART")

#: How long after the request a machine that was going to do it would have.
#: Beyond this, still being up means it did not happen.
DEFAULT_GRACE = 180.0


def boot_id() -> str:
    """
    Which boot of this machine we are in, to the second.

    `psutil.boot_time()` is the wall-clock moment the machine started. Two
    runs in the same boot see the same value; a run after a restart sees a
    different one. That difference is the whole evidence base for saying a
    restart happened, and it is available to a process that was not there.

    Rounded to the second because the underlying value can wobble by
    microseconds between reads on Windows, and a boot identity that changes
    while nothing rebooted would report a restart every time it was asked.
    """
    return f"boot-{int(psutil.boot_time())}"


@dataclass(frozen=True)
class PendingPower:
    """One power request, and what became of it."""

    id: int
    run_id: str
    action: str
    outcome: str
    boot_id: str
    requested_at: str
    deadline_at: str
    settled_at: str = ""
    settled_by: str = ""
    detail: str = ""

    @property
    def overdue(self) -> bool:
        return _now() > datetime.fromisoformat(self.deadline_at)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def remember(store: Store, run_id: str, action: str, *,
             grace: float = DEFAULT_GRACE) -> PendingPower:
    """
    Write the note. Call this **before** issuing the request, never after.

    Returns the row so the caller can cite its id, and raises on an unknown
    action rather than recording something the reconciler will not understand.
    """
    if action not in ACTIONS:
        raise ValueError(f"{action!r} is not a recordable power action; "
                         f"known: {list(ACTIONS)}")

    requested = _now()
    deadline = requested + timedelta(seconds=grace)
    with store._tx() as conn:
        cursor = conn.execute(
            "INSERT INTO pending_power (run_id, action, outcome, boot_id, "
            "requested_at, deadline_at) VALUES (?, ?, ?, ?, ?, ?)",
            (run_id, action, c.INITIATED, boot_id(),
             requested.isoformat(), deadline.isoformat()))
        row_id = cursor.lastrowid

    return PendingPower(
        id=row_id, run_id=run_id, action=action, outcome=c.INITIATED,
        boot_id=boot_id(), requested_at=requested.isoformat(),
        deadline_at=deadline.isoformat())


def pending(store: Store) -> list[PendingPower]:
    """Everything still unsettled, oldest first."""
    rows = store._conn.execute(
        "SELECT * FROM pending_power WHERE outcome = ? ORDER BY requested_at",
        (c.INITIATED,)).fetchall()
    return [PendingPower(**dict(row)) for row in rows]


def _settle(store: Store, row: PendingPower, outcome: str, *,
            settled_by: str, detail: str) -> PendingPower:
    with store._tx() as conn:
        conn.execute(
            "UPDATE pending_power SET outcome = ?, settled_at = ?, "
            "settled_by = ?, detail = ? WHERE id = ?",
            (outcome, _now().isoformat(), settled_by, detail, row.id))
    return PendingPower(**{**row.__dict__, "outcome": outcome,
                           "settled_at": _now().isoformat(),
                           "settled_by": settled_by, "detail": detail})


def cancel(store: Store, row_id: int, *, settled_by: str,
           detail: str = "called back before it happened") -> None:
    """The person changed their mind inside the window."""
    with store._tx() as conn:
        conn.execute(
            "UPDATE pending_power SET outcome = ?, settled_at = ?, "
            "settled_by = ?, detail = ? WHERE id = ?",
            (c.NOT_CARRIED_OUT, _now().isoformat(), settled_by, detail,
             row_id))


def reconcile(store: Store, *, settled_by: str = "") -> list[PendingPower]:
    """
    Settle every unresolved power request against what the machine shows now.

    Run at startup, before anything else. Returns what changed - which is
    worth reporting, because "the restart you asked for did happen" is a thing
    a person may well want said on the way back.

    Each row is settled independently and oldest first: two requests can be
    outstanding at once (asked to sleep, changed to restart), and one of them
    being unresolvable says nothing about the other.
    """
    now_boot = boot_id()
    settler = settled_by or f"reconcile@{now_boot}"
    changed: list[PendingPower] = []

    for row in pending(store):
        if row.boot_id != now_boot:
            # The machine restarted between the request and now. This is
            # evidence the current run can see for itself, which is why it is
            # allowed to settle a claim made by a run that is long gone.
            changed.append(_settle(
                store, row, c.OBSERVED, settled_by=settler,
                detail=(f"requested during {row.boot_id}, and this is "
                        f"{now_boot} - the machine did restart")))
        elif row.overdue:
            changed.append(_settle(
                store, row, c.NOT_CARRIED_OUT, settled_by=settler,
                detail=(f"still running in {now_boot}, past the deadline - "
                        f"the request was accepted and something stopped it")))
        # Otherwise: still inside the window. Too early to say, and saying
        # anything now would be guessing.

    return changed


def wait_for_settlement(store: Store, row_id: int, seconds: float = 0.0
                        ) -> PendingPower | None:
    """The current state of one row. `seconds` polls, for tests and gates."""
    deadline = time.monotonic() + seconds
    while True:
        row = store._conn.execute(
            "SELECT * FROM pending_power WHERE id = ?", (row_id,)).fetchone()
        if row is None:
            return None
        record = PendingPower(**dict(row))
        if record.outcome != c.INITIATED or time.monotonic() >= deadline:
            return record
        time.sleep(0.1)
