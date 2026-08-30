"""
The provider that exists so the fabric's failure modes can be tested.

Every real upstream fails in ways we cannot trigger on demand: a network is
down, a model is rate-limited, a sidecar took the port. That makes a failing
fabric test ambiguous - is the fabric wrong, or is Scrapling having a day?

This provider has no external dependency and does exactly what it is told. Set
`BEHAVIOUR` and it succeeds, refuses to start, reports degraded, demands auth,
or raises mid-call. So every fabric behaviour - lazy start, idempotent
activation, health honesty, fallback, crash isolation, WorkRun correlation - is
proved against something deterministic first, and a failure in a real
provider's test is then a fact about that provider.

It stays in the tree for the same reason a multimeter stays in the drawer.
"""

from __future__ import annotations

from friday import fabric

#: What the next start/health/call should do. Tests set this; production never
#: touches it, which is why "ok" is the default and the descriptor is `low`
#: risk with no permissions.
BEHAVIOUR = "ok"

#: Counts every start() the fabric performs. The idempotence test reads this:
#: two activate() calls must leave it at one.
STARTS = 0


class DummyCrash(RuntimeError):
    """Raised on purpose, so a test can prove the fabric contains it."""


def start():
    global STARTS
    if BEHAVIOUR == "start_fails":
        raise DummyCrash("dummy was told not to start")
    STARTS += 1
    return {"started": STARTS}


def stop(handle) -> None:
    return None


def health(handle) -> dict:
    if BEHAVIOUR == "degraded":
        return {"state": fabric.DEGRADED, "detail": "told to be degraded"}
    if BEHAVIOUR == "auth":
        return {"state": fabric.AUTH_REQUIRED,
                "detail": "told to require a credential"}
    if BEHAVIOUR == "unavailable":
        return {"state": fabric.UNAVAILABLE, "detail": "told to be down"}
    if BEHAVIOUR == "health_raises":
        raise DummyCrash("dummy health probe was told to raise")
    return {"state": fabric.READY, "detail": f"handle {handle}"}


def call(operation: str, handle, **arguments):
    if BEHAVIOUR == "call_raises":
        raise DummyCrash("dummy call was told to raise")
    if operation == "echo":
        return arguments.get("value")
    if operation == "ping":
        return "pong"
    raise fabric.FabricError(f"dummy has no operation {operation!r}")


DESCRIPTOR = fabric.Provider(
    id="dummy",
    family="diagnostic",
    upstream="",
    operations=("ping", "echo"),
    risk="low",
    license_mode=fabric.BUILTIN_LICENSE,
    integration_mode=fabric.BUILTIN,
    cost_class="free",
    # Restored from the .pyc oracle: the DESCRIPTOR call's KW_NAMES tuple is
    # (..., 'cost_class', 'fallbacks', 'notes') and ("dummy_backup",) sits in
    # its consts. The transcript Write predates this argument being added.
    fallbacks=("dummy_backup",),
    notes="Test instrument. Deterministic failure modes via BEHAVIOUR.",
)
