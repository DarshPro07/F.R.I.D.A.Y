"""
Splitting "is it still going?" from "how did it turn out?".

The bug this exists to make unrepeatable: one `status: "PARTIAL"` field, and
the model told the boss that a finished catalogue was "still processing, two
done so far". Both readings of the word are reasonable. Only one field was
carrying them.
"""

from __future__ import annotations

import pytest

from friday import contracts as c
from friday import runstate as RS


@pytest.mark.parametrize("status,state,outcome", [
    ("PARTIAL", RS.COMPLETED, RS.PARTIAL),
    ("SUCCEEDED", RS.COMPLETED, RS.SUCCEEDED),
    ("FAILED", RS.COMPLETED, RS.FAILED),
    ("QUARANTINED", RS.COMPLETED, RS.QUARANTINED),
    ("running", RS.RUNNING, RS.PENDING),
    ("queued", RS.QUEUED, RS.PENDING),
    ("waiting_permission", RS.WAITING, RS.PENDING),
    ("cancelled", RS.INTERRUPTED, RS.FAILED),
    ("SKIPPED", RS.INTERRUPTED, RS.SKIPPED),
])
def test_each_legacy_status_says_two_things(status, state, outcome):
    assert RS.split(status) == (state, outcome)


def test_partial_is_finished_and_imperfect_not_half_done():
    """The sentence that started this: 'processing is underway'."""
    state, outcome = RS.split("PARTIAL")
    assert state == RS.COMPLETED, "PARTIAL still reads as in-progress"
    assert outcome == RS.PARTIAL


def test_the_store_overrules_the_word():
    """
    A run killed halfway says PARTIAL and has no finish time. Calling that
    COMPLETED would hide a crash behind a word about quality.
    """
    assert RS.split("PARTIAL", finished=False)[0] == RS.INTERRUPTED
    assert RS.split("PARTIAL", finished=False)[1] == RS.PARTIAL
    assert RS.split("running", finished=True)[0] == RS.COMPLETED


def test_an_unknown_status_is_not_reported_as_finished():
    """Fail towards 'still going', never towards a completion claim."""
    state, outcome = RS.split("something_new_nobody_mapped")
    assert state not in RS.TERMINAL
    assert outcome == RS.PENDING


def test_over_means_nothing_more_will_happen_not_that_it_worked():
    assert RS.is_over("SUCCEEDED")
    assert RS.is_over("cancelled")
    assert not RS.is_over("running")
    # Interrupted is over too - it stopped, and it will not restart itself.
    # That is a different claim from COMPLETED, which is why the two are
    # separate states rather than one boolean.
    assert RS.is_over("SUCCEEDED", finished=False)
    assert RS.split("SUCCEEDED", finished=False)[0] == RS.INTERRUPTED


def test_every_contract_status_has_a_split():
    """A status nobody mapped reports as still running, which would be a lie."""
    for status in c.ACTION_STATUSES + c.RUN_STATES:
        assert status.lower() in RS._SPLIT, f"{status} falls through to RUNNING"


def test_describe_is_the_two_fields_and_nothing_else():
    assert RS.describe("PARTIAL", finished=True) == {
        "execution_state": RS.COMPLETED, "outcome": RS.PARTIAL}
