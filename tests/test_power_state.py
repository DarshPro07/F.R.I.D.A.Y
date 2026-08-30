"""
Settling what Friday could not stay to watch.

The whole module exists for one situation: Friday asks the machine to restart,
and then stops existing. Nothing it was going to write gets written. So the
note goes down first, and a later run - a different process, after a different
boot - decides what happened by looking at the machine rather than by trusting
a record left by something that is no longer running.

These tests prove that without restarting anything, by fabricating the boot
identity that a real restart would have produced.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from friday import contracts as c
from friday import power_state as P
from friday.store import Store


@pytest.fixture
def store():
    store = Store(":memory:")
    yield store
    store.close()


def _age(store: Store, row_id: int, *, boot: str | None = None,
         seconds_ago: float = 0.0) -> None:
    """Rewrite a row's history, so a test need not wait for real time."""
    past = datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)
    with store._tx() as conn:
        if boot is not None:
            conn.execute("UPDATE pending_power SET boot_id = ? WHERE id = ?",
                         (boot, row_id))
        if seconds_ago:
            conn.execute(
                "UPDATE pending_power SET requested_at = ?, deadline_at = ? "
                "WHERE id = ?",
                (past.isoformat(), past.isoformat(), row_id))


# ---------------------------------------------------------------------------
# The note itself
# ---------------------------------------------------------------------------


def test_a_request_is_recorded_as_initiated_not_succeeded(store):
    row = P.remember(store, "run-1", "RESTART")
    assert row.outcome == c.INITIATED
    assert row.outcome != c.SUCCEEDED


def test_only_the_actions_the_feature_offers_can_be_recorded(store):
    """
    The offered set, the recordable set and the policy set are the same five,
    and this is the one that would let them drift. LOGOFF was in an earlier
    draft of all three except the first.
    """
    with pytest.raises(ValueError) as caught:
        P.remember(store, "run-1", "LOGOFF")
    assert "LOGOFF" in str(caught.value)


def test_the_boot_identity_is_stable_within_one_boot(store):
    """
    Read twice, same answer. An identity that wobbles would report a restart
    every time it was asked - boot_time() can vary by microseconds between
    reads, which is why it is rounded.
    """
    assert P.boot_id() == P.boot_id()


# ---------------------------------------------------------------------------
# Settling it afterwards
# ---------------------------------------------------------------------------


def test_a_restart_that_happened_settles_as_observed(store):
    """
    The machine came back. The evidence is that the boot identity recorded
    with the request is not the one we are in now - visible to a process that
    was not there when the request was made.
    """
    row = P.remember(store, "run-1", "RESTART")
    _age(store, row.id, boot="boot-1")             # as if from a previous boot

    changed = P.reconcile(store)
    assert len(changed) == 1
    assert changed[0].outcome == c.OBSERVED
    assert "did restart" in changed[0].detail


def test_a_restart_that_never_happened_settles_as_not_carried_out(store):
    """
    Accepted, and then something stopped it - an application objected, or the
    request was aborted. The machine is still up in the same boot, past the
    point where it would have gone.
    """
    row = P.remember(store, "run-1", "RESTART")
    _age(store, row.id, seconds_ago=600)

    changed = P.reconcile(store)
    assert len(changed) == 1
    assert changed[0].outcome == c.NOT_CARRIED_OUT
    assert changed[0].outcome != c.FAILED, "this is not a failure to ask"


def test_a_request_still_inside_its_window_is_left_alone(store):
    """
    Too early to say. Settling it now would be guessing, and a guess recorded
    as an outcome is indistinguishable from an observation later.
    """
    P.remember(store, "run-1", "SHUTDOWN")
    assert P.reconcile(store) == []
    assert len(P.pending(store)) == 1


def test_two_pending_requests_settle_independently(store):
    """
    Asked to sleep, changed their mind, asked to restart. One being
    unresolvable says nothing about the other.
    """
    first = P.remember(store, "run-1", "SLEEP")
    second = P.remember(store, "run-2", "RESTART")
    _age(store, first.id, seconds_ago=600)         # overdue, same boot

    changed = P.reconcile(store)
    assert len(changed) == 1
    assert changed[0].id == first.id
    assert changed[0].outcome == c.NOT_CARRIED_OUT
    assert [row.id for row in P.pending(store)] == [second.id]


def test_settling_is_not_repeated(store):
    row = P.remember(store, "run-1", "RESTART")
    _age(store, row.id, boot="boot-1")
    assert len(P.reconcile(store)) == 1
    assert P.reconcile(store) == [], "a settled row was settled again"


def test_a_cancelled_request_is_not_carried_out_rather_than_failed(store):
    row = P.remember(store, "run-1", "RESTART")
    P.cancel(store, row.id, settled_by="run-1")

    settled = P.wait_for_settlement(store, row.id)
    assert settled.outcome == c.NOT_CARRIED_OUT
    assert "called back" in settled.detail
    assert P.reconcile(store) == [], "a cancelled row was reconciled anyway"


# ---------------------------------------------------------------------------
# Whose evidence is it
# ---------------------------------------------------------------------------


def test_a_settled_record_names_both_runs_and_claims_nothing_for_the_first(
        store):
    """
    FR-034, at the one point in this feature where it can break.

    Reconciliation is the only place Friday cites evidence produced outside
    the run doing the citing. That is allowed here precisely because the
    evidence - the machine's boot identity - is something the settling run
    reads for itself. What must never happen is the record implying the
    original run observed the restart: it did not, it was not there, and it
    stopped before the machine came back.
    """
    row = P.remember(store, "run-asked", "RESTART")
    _age(store, row.id, boot="boot-1")

    settled = P.reconcile(store)[0]

    assert settled.run_id == "run-asked", "lost who asked"
    assert settled.settled_by and settled.settled_by != "run-asked", \
        "the run that asked cannot be the run that observed"
    assert P.boot_id() in settled.settled_by


def test_removing_the_boot_comparison_would_be_caught(store):
    """
    The mutation check. If `reconcile` stopped comparing boot identities and
    settled everything overdue as OBSERVED, this is the case that would start
    lying - a machine that never restarted, reported as having restarted.
    """
    row = P.remember(store, "run-1", "RESTART")
    _age(store, row.id, seconds_ago=600)           # overdue, SAME boot

    settled = P.reconcile(store)[0]
    assert settled.outcome == c.NOT_CARRIED_OUT, (
        "same boot and overdue must never settle as OBSERVED - the machine "
        "is demonstrably still up")
