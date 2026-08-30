"""
The second diagnostic provider, so failover has somewhere to fail over to.

`fabric.dummy` can be told to break; this one always works. Together they are
the smallest pair that can prove `call_with_fallback` actually walks the chain
rather than reporting the first provider's error as the family's error.

It is `cheap` where dummy is `free` so the router's cost ordering puts dummy
first deterministically, which is what makes the fallback test meaningful.
"""

from __future__ import annotations

from friday import fabric

CALLS = 0


def health(handle) -> dict:
    return {"state": fabric.READY, "detail": "always up, that is the job"}


def call(operation: str, handle, **arguments):
    global CALLS
    CALLS += 1
    if operation == "ping":
        return "pong-from-backup"
    if operation == "echo":
        return arguments.get("value")
    raise fabric.FabricError(f"dummy_backup has no operation {operation!r}")


DESCRIPTOR = fabric.Provider(
    id="dummy_backup",
    family="diagnostic",
    upstream="",
    operations=("ping", "echo"),
    risk="low",
    license_mode=fabric.BUILTIN_LICENSE,
    integration_mode=fabric.BUILTIN,
    cost_class="cheap",
    notes="Failover target for fabric.dummy. Always healthy.",
)
