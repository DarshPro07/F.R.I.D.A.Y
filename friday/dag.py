"""
Dependency ordering, shared by the automation engine and the product pipeline.

Both need the same two things, and the second is the one that matters: an
order to run in, and a rule for what happens to the things downstream of a
failure. A step whose dependency did not succeed is *skipped and recorded as
skipped* - not run into the same failure, and not silently dropped.

That rule is the whole difference between a graph and a list. A list stops, or
carries on regardless; a graph knows that a missing image should not prevent
classification, while it must prevent the export that needs the image.
"""

from __future__ import annotations


class CycleError(ValueError):
    """Steps depend on each other. The message names them."""


def topological(nodes: dict[str, list[str]]) -> list[str]:
    """
    Kahn's algorithm over ``{name: [names it needs]}``.

    Ties are broken alphabetically so a run is reproducible: an order that
    varies between runs makes two failures impossible to compare.
    """
    pending = {name: set(needs) for name, needs in nodes.items()}
    for name, needs in pending.items():
        missing = needs - set(pending)
        if missing:
            raise CycleError(
                f"{name!r} needs {', '.join(sorted(missing))}, which does not exist")
        if name in needs:
            raise CycleError(f"{name!r} needs itself")

    ordered: list[str] = []
    while pending:
        ready = sorted(name for name, needs in pending.items() if not needs)
        if not ready:
            raise CycleError(
                "these depend on each other in a cycle: "
                + ", ".join(sorted(pending)))
        ordered.extend(ready)
        for name in ready:
            del pending[name]
        for needs in pending.values():
            needs.difference_update(ready)
    return ordered


def blocked_by(needs: list[str], failed: set[str]) -> list[str]:
    """Which of this step's dependencies did not succeed."""
    return sorted(set(needs) & failed)
