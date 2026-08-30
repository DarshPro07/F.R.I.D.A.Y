"""
Changing something and putting it back, provably.

Batch 2C starts touching state that belongs to the person using the machine -
the volume, the mute, the brightness. A live gate that sets the volume to 20,
asserts it worked, and leaves it there has proved one thing and broken
another, and the report says only the first:

    "Friday successfully changed the volume"        true
    "Friday left the machine as it found it"        never checked

So the check is both halves, and a mutation test passes only when the change
was observed AND the restoration was observed. Not "restore was attempted" -
`finally: put_it_back()` swallowing an exception is exactly how a machine is
left changed while a test reports green.

The same record is useful outside tests. An ActionResult that carries
`previous_value` lets a multi-step task that fails halfway put back what it
moved, which is not possible if the only record of the old volume was a local
variable in a function that has returned.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

# What a reversible change can end up being.
MUTATED_AND_RESTORED = "MUTATED_AND_RESTORED"   # both halves observed
MUTATION_REFUSED = "MUTATION_REFUSED"           # nothing changed; nothing to undo
NOT_RESTORED = "NOT_RESTORED"                   # changed, and still changed
UNAVAILABLE = "NOT_CONFIGURED"                  # no such control on this machine

#: Somebody else moved it after Friday did, so Friday left it alone.
#:
#: The scenario, which a blind restore gets exactly wrong:
#:
#:     was 60      Friday sets 30      the boss sets 80      Friday "restores" 60
#:
#: The last step overwrites a deliberate human action with a stale value, and
#: reports success for doing it. Restoring is only correct while the value is
#: still the one Friday wrote.
RESTORE_CONFLICT = "RESTORE_CONFLICT"

#: The thing that held the state no longer exists - the app exited, the audio
#: endpoint was unplugged. Not a failure to restore: there is nothing left to
#: restore, and inventing a target to write to would be worse.
TARGET_GONE = "TARGET_GONE"

#: The only outcome a live gate may report as a pass.
CLEAN = MUTATED_AND_RESTORED

#: Outcomes that leave the machine as it was found, whether or not anything
#: was changed. A live gate may finish on any of these.
LEFT_TIDY = (MUTATED_AND_RESTORED, MUTATION_REFUSED, UNAVAILABLE,
             RESTORE_CONFLICT, TARGET_GONE)


@dataclass
class Attempt:
    """
    What actually happened, in enough detail to be argued with.

    `observed_after` is deliberately separate from `requested`: hardware
    rounds, drivers clamp, and a volume set to 37 that reads back as 36 is a
    fact rather than a failure. The caller decides what counts as close
    enough; this only records both.
    """

    control: str
    before: Any = None
    requested: Any = None
    observed_after: Any = None
    observed_restored: Any = None
    outcome: str = UNAVAILABLE
    error: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def mutated(self) -> bool:
        return self.outcome in (MUTATED_AND_RESTORED, NOT_RESTORED,
                                RESTORE_CONFLICT)

    @property
    def left_alone(self) -> bool:
        """True when Friday deliberately did not put something back."""
        return self.outcome in (RESTORE_CONFLICT, TARGET_GONE)

    @property
    def clean(self) -> bool:
        """Changed, seen to change, put back, and seen to be back."""
        return self.outcome == MUTATED_AND_RESTORED

    def summary(self) -> str:
        if self.outcome == UNAVAILABLE:
            return f"{self.control}: not available on this machine"
        if self.outcome == TARGET_GONE:
            return f"{self.control}: the target is gone; nothing to restore"
        if self.outcome == RESTORE_CONFLICT:
            return (f"{self.control}: left at {self.observed_restored!r} - "
                    f"Friday set {self.observed_after!r} and something else "
                    f"changed it since, so it was not overwritten")
        if self.outcome == MUTATION_REFUSED:
            return f"{self.control}: refused ({self.error})"
        if self.outcome == NOT_RESTORED:
            return (f"{self.control}: LEFT AT {self.observed_after!r}, was "
                    f"{self.before!r} - the machine was not put back")
        return (f"{self.control}: {self.before!r} -> {self.observed_after!r} "
                f"-> {self.observed_restored!r}")


def attempt(
    control: str,
    read: Callable[[], Any],
    write: Callable[[Any], None],
    value: Any,
    *,
    matches: Callable[[Any, Any], bool] | None = None,
    available: Callable[[], bool] | None = None,
    gone: Callable[[], bool] | None = None,
    while_changed: Callable[[Attempt], None] | None = None,
) -> Attempt:
    """
    Snapshot, change, read back, do the work, check nobody else moved it,
    restore, read back again.

    `matches(wanted, observed)` decides whether the hardware did what was
    asked. Defaults to equality, and is worth overriding for anything a driver
    rounds.

    `while_changed` runs with the change in place - it is where a live gate
    puts its assertions, and where a person gets the chance to reach for the
    volume knob themselves. Whatever it raises, the restore still runs, which
    is the point of doing this here rather than trusting every call site to
    write `finally` correctly.

    `gone()` says the thing that held the state no longer exists: the app
    exited, the audio endpoint was unplugged. Restoring then has no target,
    and inventing one would be worse than admitting it.
    """
    same = matches or (lambda wanted, observed: wanted == observed)
    record = Attempt(control=control, requested=value)

    if available is not None:
        try:
            if not available():
                record.outcome = UNAVAILABLE
                return record
        except Exception as exc:                        # noqa: BLE001
            record.outcome = UNAVAILABLE
            record.error = f"{type(exc).__name__}: {exc}"
            return record

    try:
        record.before = read()
    except Exception as exc:                            # noqa: BLE001
        record.outcome = UNAVAILABLE
        record.error = f"could not read {control}: {type(exc).__name__}: {exc}"
        return record

    try:
        write(value)
    except Exception as exc:                            # noqa: BLE001
        record.outcome = MUTATION_REFUSED
        record.error = f"{type(exc).__name__}: {exc}"
        return record

    try:
        record.observed_after = read()
    except Exception as exc:                            # noqa: BLE001
        # It was changed and cannot be seen. Assume the worst - that it is
        # still changed - because that is the assumption that gets it put back.
        record.outcome = NOT_RESTORED
        record.error = f"could not read back {control}: {exc}"
        _restore(record, write, read, same)
        return record

    if not same(value, record.observed_after):
        # The write was accepted and the control did not move. Nothing to undo,
        # and reporting success here is what the read-back exists to prevent.
        record.outcome = MUTATION_REFUSED
        record.error = (f"asked for {value!r}, {control} is "
                        f"{record.observed_after!r}")
        return record

    try:
        if while_changed is not None:
            while_changed(record)
    finally:
        _restore(record, write, read, same, gone)
    return record


def _restore(record: Attempt, write, read, same, gone=None) -> None:
    """
    Put it back - unless somebody else has moved it since, or it is gone.

    The compare before the write is the part that matters. Friday set 30, the
    boss reached over and set 80, and a blind restore to 60 overwrites a
    deliberate human action with a stale number and calls it cleanup.
    """
    if gone is not None:
        try:
            if gone():
                record.outcome = TARGET_GONE
                record.error = (f"{record.control} no longer exists - the app "
                                f"closed or the device went away")
                return
        except Exception:                               # noqa: BLE001
            pass                                        # decided by the read

    try:
        current = read()
    except Exception as exc:                            # noqa: BLE001
        if gone is not None:
            record.outcome = TARGET_GONE
            record.error = f"{record.control} could not be read back: {exc}"
            return
        # Cannot compare, so cannot detect a conflict - but Friday knows it
        # changed this and knows what it was. Putting it back blind is the
        # better risk: leaving the boss's volume where a test left it needs
        # only a transient read error, while overwriting him needs a read
        # error AND him having reached for the knob in the same few seconds.
        record.error = (f"could not check {record.control} before restoring, "
                        f"so it was put back without comparing: {exc}")
        try:
            write(record.before)
        except Exception as write_exc:                  # noqa: BLE001
            record.error += f"; and the restore failed too: {write_exc}"
        record.outcome = NOT_RESTORED
        return

    if record.observed_after is not None and not same(record.observed_after,
                                                      current):
        record.outcome = RESTORE_CONFLICT
        record.observed_restored = current
        record.error = (f"{record.control} was left at {current!r}: Friday "
                        f"set {record.observed_after!r} and something else "
                        f"changed it since")
        return

    try:
        write(record.before)
    except Exception as exc:                            # noqa: BLE001
        record.outcome = NOT_RESTORED
        record.error = f"could not restore {record.control}: {exc}"
        return
    try:
        record.observed_restored = read()
    except Exception as exc:                            # noqa: BLE001
        record.outcome = NOT_RESTORED
        record.error = f"could not confirm {record.control} was restored: {exc}"
        return
    record.outcome = (MUTATED_AND_RESTORED
                      if same(record.before, record.observed_restored)
                      else NOT_RESTORED)
    if record.outcome == NOT_RESTORED:
        record.error = (f"{record.control} was {record.before!r} and is now "
                        f"{record.observed_restored!r}")
