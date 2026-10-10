"""
The side-effect ledger's vocabulary and its one writer (PRD v7 SIDE-001).

A capability that changes something outside the process records one row per
change - capability, target, effect, reversibility, and the run and objective
it was for. The completion verifier (`Store.completion_evidence_gap`) reads
those rows back: a succeeded task that claims a side effect from a ledgered
capability, with no row, is not verified.

Reversibility is one of three words, so a reader can tell what an undo would
take without reading the capability:

    reversible     the person can undo it as they already know how
                   (a file in the Recycle Bin)
    compensable    a later, separate action undoes it (delete the file that
                   was created); nothing the change destroyed is lost
    irreversible   what it replaced or removed is gone

`LEDGERED` is the set of capabilities that write a row. The verifier holds
only these to the ledger, so wiring another capability is one entry here plus
one `record` call at its success path.
"""
from __future__ import annotations

from friday import contracts as c

REVERSIBLE = "reversible"
COMPENSABLE = "compensable"
IRREVERSIBLE = "irreversible"
REVERSIBILITIES = (REVERSIBLE, COMPENSABLE, IRREVERSIBLE)

#: Tool ids (the `tool_id` an ActionResult carries) that write a ledger row.
LEDGERED = frozenset({
    "files.write", "files.create", "files.delete", "files.delete_permanent",
})


def record(run: c.Run, *, capability: str, target: str, effect: str,
           reversibility: str) -> None:
    """Write the row for a side effect this run just made.

    Raises on a database error: the caller reports the action as partial,
    because a change with no row is exactly what the verifier refuses.
    """
    # The row goes to the store the run's objective is executing against (the
    # one the verifier reads), and carries that objective's id; a run that no
    # objective built (a conversation turn) falls back to the process store.
    ledger = run.ledger
    if ledger is None:
        from friday.toolsets import memory
        ledger = memory.store()
    objective = (run.correlation.objective if run.correlation else "") or (
        run.envelope.objective_id if run.envelope else "")
    ledger.record_side_effect(
        run_id=run.run_id, objective_id=objective, capability=capability,
        target=target, effect=effect, reversibility=reversibility)
