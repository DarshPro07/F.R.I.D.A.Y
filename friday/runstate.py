"""
Two questions about a run, which are not the same question.

    is it still going?      QUEUED RUNNING WAITING COMPLETED INTERRUPTED
    how did it turn out?    SUCCEEDED PARTIAL FAILED QUARANTINED
                            DEDUPLICATED SKIPPED PENDING

Friday reported both through one field, and it cost a false statement to the
boss. Handed `status: "PARTIAL"` for a catalogue that had finished processing,
the model said "processing is underway, two done so far" - which is what
PARTIAL means in ordinary English, and not what it means here. A prompt line
saying "PARTIAL means finished" would have patched that one sentence. It would
not have touched the next field with the same shape.

    {"execution_state": "COMPLETED", "outcome": "PARTIAL"}

has one reading: it is over, and some of it did not work. There is nothing
left to infer, which is the point - the fix belongs in the schema, not in the
instructions.

The legacy single-status vocabularies stay where they are, inside each
subsystem. This module is the boundary they are translated at, so the
translation lives in one place rather than in six reporting surfaces.
"""

from __future__ import annotations

# --- is it still going? ----------------------------------------------------

QUEUED = "QUEUED"
RUNNING = "RUNNING"
WAITING = "WAITING"          # blocked on a human, a permission, a secret
COMPLETED = "COMPLETED"      # it ran to the end, whatever the outcome
INTERRUPTED = "INTERRUPTED"  # it stopped before the end: crash, kill, skip

EXECUTION_STATES = (QUEUED, RUNNING, WAITING, COMPLETED, INTERRUPTED)

#: Nothing more will happen without a new call.
TERMINAL = (COMPLETED, INTERRUPTED)

# --- how did it turn out? --------------------------------------------------

SUCCEEDED = "SUCCEEDED"
PARTIAL = "PARTIAL"
FAILED = "FAILED"
QUARANTINED = "QUARANTINED"
DEDUPLICATED = "DEDUPLICATED"
SKIPPED = "SKIPPED"
PENDING = "PENDING"          # too early to say

#: Asked for, accepted, and not yet resolved. The distinction this whole
#: module exists for arrives in its sharpest form here: Friday's execution is
#: COMPLETED - it made the call and has nothing left to do - while the outcome
#: is INITIATED, because the machine has not restarted yet and may not.
#: One field carrying both would have to choose, and either choice is a lie.
INITIATED = "INITIATED"

#: Evidence, after the fact, that it happened.
OBSERVED = "OBSERVED"

#: Accepted and then it did not happen.
NOT_CARRIED_OUT = "NOT_CARRIED_OUT"

#: Friday was not allowed to, or the machine cannot.
NOT_PERMITTED = "NOT_PERMITTED"
UNSUPPORTED = "UNSUPPORTED"

#: Friday has the capability and cannot run it on this path. Not the machine's
#: fault, and not the same as the capability not existing.
NOT_CONFIGURED = "NOT_CONFIGURED"

OUTCOMES = (SUCCEEDED, PARTIAL, FAILED, QUARANTINED, DEDUPLICATED, SKIPPED,
            PENDING, INITIATED, OBSERVED, NOT_CARRIED_OUT, NOT_PERMITTED,
            UNSUPPORTED, NOT_CONFIGURED)

#: One legacy status -> the two things it was trying to say at once. Covers
#: contracts.ACTION_STATUSES, contracts.RUN_STATES and the product vocabulary.
_SPLIT: dict[str, tuple[str, str]] = {
    'queued': (QUEUED, PENDING),
    'received': (QUEUED, PENDING),
    'planning': (RUNNING, PENDING),
    'running': (RUNNING, PENDING),
    'working': (RUNNING, PENDING),
    'waiting_permission': (WAITING, PENDING),
    'waiting_user_secret': (WAITING, PENDING),
    'succeeded': (COMPLETED, SUCCEEDED),
    'completed': (COMPLETED, SUCCEEDED),
    'partial': (COMPLETED, PARTIAL),
    'failed': (COMPLETED, FAILED),
    'failed_retryable': (COMPLETED, FAILED),
    'quarantined': (COMPLETED, QUARANTINED),
    'deduplicated': (COMPLETED, DEDUPLICATED),
    'processed': (COMPLETED, SUCCEEDED),
    'cancelled': (INTERRUPTED, FAILED),
    'skipped': (INTERRUPTED, SKIPPED),
    'initiated': (COMPLETED, INITIATED),
    'observed': (COMPLETED, OBSERVED),
    'not_carried_out': (COMPLETED, NOT_CARRIED_OUT),
    'not_permitted': (COMPLETED, NOT_PERMITTED),
    'unsupported': (COMPLETED, UNSUPPORTED),
    'not_configured': (COMPLETED, NOT_CONFIGURED),
}


def split(status: str | None, *, finished: bool | None = None
          ) -> tuple[str, str]:
    """
    (execution_state, outcome) for a legacy single-field status.

    `finished` is what the store actually knows - a `finished_at` timestamp,
    a terminal flag - and it overrules the status word, because those two can
    genuinely disagree. A run killed halfway through says PARTIAL and has no
    finish time: that is INTERRUPTED/PARTIAL, and calling it COMPLETED would
    hide a crash behind a word about quality.
    """
    state, outcome = _SPLIT.get((status or "").strip().lower(),
                                (RUNNING, PENDING))
    if finished is False and state == COMPLETED:
        state = INTERRUPTED
    elif finished is True and state not in TERMINAL:
        state = COMPLETED
    return state, outcome


def describe(status: str | None, *, finished: bool | None = None) -> dict:
    """The pair as a dict, for merging into a reported result."""
    state, outcome = split(status, finished=finished)
    return {"execution_state": state, "outcome": outcome}


def is_over(status: str | None, *, finished: bool | None = None) -> bool:
    return split(status, finished=finished)[0] in TERMINAL
