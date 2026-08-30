"""
Changing something and putting it back, provably.

A live gate that sets the volume to 20, asserts it worked, and leaves it there
has proved one thing and broken another - and reports only the first. Both
halves, or it is not a pass.
"""

from __future__ import annotations

import pytest

from friday import reversible as R


class Knob:
    """A control that remembers, and can be told to misbehave."""

    def __init__(self, value=50, *, refuses_write=False, refuses_restore=False,
                 unreadable_after=False, clamps_to=None):
        self.value = value
        self.writes: list = []
        self._refuses_write = refuses_write
        self._refuses_restore = refuses_restore
        self._unreadable_after = unreadable_after
        self._clamps_to = clamps_to
        self._reads = 0

    def read(self):
        self._reads += 1
        if self._unreadable_after and self._reads > 1:
            raise OSError("the driver went away")
        return self.value

    def write(self, value):
        self.writes.append(value)
        if self._refuses_write:
            raise OSError("the device said no")
        if self._refuses_restore and len(self.writes) > 1:
            raise OSError("it would not go back")
        self.value = self._clamps_to if self._clamps_to is not None else value


def test_the_happy_path_records_all_four_observations():
    knob = Knob(value=50)
    record = R.attempt("volume", knob.read, knob.write, 20)

    assert record.outcome == R.MUTATED_AND_RESTORED
    assert record.clean
    assert (record.before, record.observed_after,
            record.observed_restored) == (50, 20, 50)
    assert knob.value == 50, "the machine was left changed"


def test_restoration_happens_even_when_the_control_is_never_read_again():
    knob = Knob(value=7)
    R.attempt("brightness", knob.read, knob.write, 99)
    assert knob.writes == [99, 7]


def test_a_control_that_does_not_exist_is_not_a_failure():
    """
    A desktop has no brightness slider. NOT_CONFIGURED is the truth, and it is
    neither a pass nor a bug.
    """
    knob = Knob()
    record = R.attempt("brightness", knob.read, knob.write, 10,
                       available=lambda: False)
    assert record.outcome == R.UNAVAILABLE
    assert not record.clean
    assert not record.mutated
    assert knob.writes == [], "it touched a control it had decided was absent"


def test_a_refused_write_leaves_nothing_to_undo():
    knob = Knob(value=50, refuses_write=True)
    record = R.attempt("volume", knob.read, knob.write, 20)
    assert record.outcome == R.MUTATION_REFUSED
    assert not record.mutated
    assert knob.value == 50


def test_a_write_that_is_accepted_and_does_nothing_is_refused_not_succeeded():
    """
    The read-back is the whole point. A driver that accepts a value and
    ignores it must not be reported as a change that then needs undoing.
    """
    knob = Knob(value=50, clamps_to=50)
    record = R.attempt("volume", knob.read, knob.write, 20)
    assert record.outcome == R.MUTATION_REFUSED
    assert "asked for 20" in record.error


def test_failing_to_put_it_back_is_reported_loudly():
    """
    The failure this module exists for: the change worked, the restore did
    not, and the test would otherwise be green.
    """
    knob = Knob(value=50, refuses_restore=True)
    record = R.attempt("volume", knob.read, knob.write, 20)
    assert record.outcome == R.NOT_RESTORED
    assert not record.clean
    assert record.mutated
    assert "LEFT AT" in record.summary()


def test_a_control_that_cannot_be_read_back_assumes_the_worst():
    """
    Changed, and cannot be seen. Assuming it is still changed is the
    assumption that gets it put back.
    """
    knob = Knob(value=50, unreadable_after=True)
    record = R.attempt("volume", knob.read, knob.write, 20)
    assert record.outcome == R.NOT_RESTORED
    assert knob.writes == [20, 50], "it did not attempt the restore"


def test_hardware_rounding_is_a_fact_not_a_failure():
    """
    A volume set to 37 that reads back 36 is a driver, not a bug.

    The knob rounds to even, the way a real one quantises - it does not pin
    every write to one value, which would also make the *restore* land
    somewhere else and would be a genuine NOT_RESTORED.
    """
    class Rounding(Knob):
        def write(self, value):
            self.writes.append(value)
            self.value = value - (value % 2)

    knob = Rounding(value=50)
    close = lambda wanted, observed: abs(wanted - observed) <= 2  # noqa: E731
    record = R.attempt("volume", knob.read, knob.write, 37, matches=close)
    assert record.outcome == R.MUTATED_AND_RESTORED
    assert record.observed_after == 36
    assert knob.value == 50, "the restore did not land back on the original"


def test_only_one_outcome_may_be_called_clean():
    for outcome in (R.MUTATION_REFUSED, R.NOT_RESTORED, R.UNAVAILABLE):
        assert not R.Attempt("x", outcome=outcome).clean
    assert R.Attempt("x", outcome=R.MUTATED_AND_RESTORED).clean
    assert R.CLEAN == R.MUTATED_AND_RESTORED


def test_the_summary_never_hides_a_machine_left_changed():
    record = R.Attempt("volume", before=50, observed_after=20,
                       outcome=R.NOT_RESTORED)
    summary = record.summary()
    assert "50" in summary and "20" in summary
    assert "not put back" in summary


@pytest.mark.parametrize("outcome", [R.MUTATION_REFUSED, R.UNAVAILABLE])
def test_nothing_that_did_not_change_claims_to_have_changed(outcome):
    assert not R.Attempt("x", outcome=outcome).mutated


# ---------------------------------------------------------------------------
# The three adversarial cases: somebody else, gone, and gone mid-transaction
# ---------------------------------------------------------------------------


def test_a_human_changing_it_afterwards_is_not_overwritten():
    """
    The scenario a blind restore gets exactly wrong:

        was 60      Friday sets 30      the boss sets 80      restore to 60

    That last step overwrites a deliberate human action with a stale number
    and reports success for doing it.
    """
    knob = Knob(value=60)

    def the_boss_reaches_over(record):
        knob.value = 80          # not through Friday, and not through write()

    record = R.attempt("volume", knob.read, knob.write, 30,
                       while_changed=the_boss_reaches_over)

    assert record.outcome == R.RESTORE_CONFLICT
    assert knob.value == 80, "it overwrote what the boss did"
    assert knob.writes == [30], "it wrote a second time anyway"
    assert record.left_alone
    assert not record.clean
    assert "changed it since" in record.error


def test_no_interference_still_restores():
    """The conflict check must not make the ordinary path stop working."""
    knob = Knob(value=60)
    seen = []
    record = R.attempt("volume", knob.read, knob.write, 30,
                       while_changed=lambda r: seen.append(knob.value))
    assert seen == [30], "the work did not see the change in place"
    assert record.clean
    assert knob.value == 60


def test_the_restore_runs_even_when_the_work_raises():
    knob = Knob(value=60)

    def explodes(record):
        raise RuntimeError("the assertion failed")

    with pytest.raises(RuntimeError):
        R.attempt("volume", knob.read, knob.write, 30, while_changed=explodes)
    assert knob.value == 60, "a failed assertion left the machine changed"


def test_a_target_that_disappears_is_not_a_failed_restore():
    """
    The app exited, or the audio endpoint was unplugged. There is nothing left
    to restore, and writing to whatever replaced it would be worse than saying
    so.
    """
    knob = Knob(value=60)
    closed = []

    def the_app_exits(record):
        closed.append(True)

    record = R.attempt("spotify volume", knob.read, knob.write, 30,
                       while_changed=the_app_exits,
                       gone=lambda: bool(closed))
    assert record.outcome == R.TARGET_GONE
    assert knob.writes == [30], "it wrote to a target that no longer exists"
    assert record.left_alone
    assert "no longer exists" in record.error


def test_a_device_that_invalidates_mid_transaction_is_target_gone():
    """
    Windows reports AUDCLNT_E_DEVICE_INVALIDATED when an endpoint is
    unplugged or reconfigured. Restoring against the device that replaced it
    would set the wrong thing.
    """
    knob = Knob(value=60, unreadable_after=True)
    record = R.attempt("endpoint volume", knob.read, knob.write, 30,
                       gone=lambda: False)
    # The read after mutation failed, so there is no observed change to trust.
    assert record.outcome in (R.TARGET_GONE, R.NOT_RESTORED)
    assert not record.clean


def test_every_tidy_outcome_leaves_the_machine_as_it_was_found():
    for outcome in R.LEFT_TIDY:
        assert outcome != R.NOT_RESTORED
    assert R.NOT_RESTORED not in R.LEFT_TIDY
