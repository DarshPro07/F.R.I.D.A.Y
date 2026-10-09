"""
The durable runtime's backend interface (PRD v7 DUR-002).

The durable kernel (DUR-001: the append-only event log, leases, task rows,
exactly-once deliveries) is driven through the operations named in
KERNEL_OPERATIONS. The FRIDAY-native backend is the SQLite `Store`, which is
the only one the desktop product needs. A server or enterprise deployment
may supply another (Temporal) as a provider; absent, it reports UNAVAILABLE
and nothing else changes. No Temporal code lives here.
"""
from __future__ import annotations

from typing import Callable

from friday import fabric

NATIVE = "native"
TEMPORAL = "temporal"
BACKENDS = (NATIVE, TEMPORAL)

#: What ContinuousTaskExecutor and RunWatchdog call on their store.
KERNEL_OPERATIONS = (
    "acquire_objective_lease", "append_objective_approval", "append_objective_event",
    "append_objective_evidence", "create_objective_delivery", "finish_objective_run",
    "objective_event_counts", "objective_events", "objective_events_named",
    "objective_ledger", "objective_run", "objective_runs",
    "objective_task", "objective_tasks", "recent_objective_events",
    "release_objective_lease", "stop_latched", "touch_objective_run",
    "update_objective_task", "update_objective_task_if",
)


class BackendUnavailable(RuntimeError):
    """The requested durable backend cannot be used; `state` says how."""

    state = fabric.UNAVAILABLE


def missing_operations(backend) -> list[str]:
    """The kernel operations `backend` does not provide."""
    return [op for op in KERNEL_OPERATIONS if not callable(getattr(backend, op, None))]


def backend_status(name: str, temporal: Callable[[], object] | None = None) -> dict:
    """Whether a backend is usable. Native is always READY; Temporal is never
    required, so without a provider it is UNAVAILABLE rather than an error."""
    if name == NATIVE:
        return {"backend": NATIVE, "state": fabric.READY, "required": True}
    if name == TEMPORAL:
        return {"backend": TEMPORAL, "required": False,
                "state": fabric.READY if temporal is not None else fabric.UNAVAILABLE}
    raise ValueError(f"unknown durable backend {name!r}; expected one of {BACKENDS}")


def select_backend(name: str, *, native, temporal: Callable[[], object] | None = None):
    """The backend the kernel runs on. `native` is the FRIDAY-native backend
    (the Store); `temporal` is an optional provider that builds the Temporal
    one. Raises BackendUnavailable for a backend that is absent, will not
    start, or lacks a kernel operation."""
    if backend_status(name, temporal)["state"] != fabric.READY:
        raise BackendUnavailable("the Temporal backend is not installed or configured")
    if name == NATIVE:
        return native
    try:
        backend = temporal()
    except Exception as exc:  # a provider that cannot start is unavailable, not a crash
        raise BackendUnavailable(f"the Temporal backend did not start: {exc}") from exc
    gaps = missing_operations(backend)
    if gaps:
        raise BackendUnavailable(f"the Temporal backend lacks: {', '.join(gaps)}")
    return backend
