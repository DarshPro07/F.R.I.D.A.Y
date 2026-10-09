"""
The durable runtime sits behind one interface (PRD v7 DUR-002).

The kernel (DUR-001) runs on a FRIDAY-native backend, the SQLite store, and
the same durability test must pass there with Temporal absent. Temporal is
an optional server-side backend behind the same interface: absent, it
reports UNAVAILABLE and the desktop product never needs it.
"""
from __future__ import annotations

import asyncio

import pytest

from friday import durable_runtime as D
from friday import fabric
from friday import objectives as O
from friday.continuous import ContinuousTaskExecutor
from friday.store import Store
from tests.test_durable_side_effects import _compile, _Died, _expire_lease, _recover


class _OtherBackend:
    """Stands in for a Temporal-backed implementation: it holds no state of
    its own and forwards every call, so it proves only that the interface is
    enough for the kernel to run on something that is not the Store."""

    def __init__(self, inner):
        self._inner = inner
        self.calls = 0

    def __getattr__(self, name):
        attr = getattr(self._inner, name)
        if callable(attr):
            self.calls += 1
        return attr


def _kill_after_commit_then_resume(backend, store) -> list[str]:
    """The DUR-001 test, run against any backend: the executor dies after the
    side effect commits and before success is recorded; a second executor
    resumes. Returns the side effects that happened."""
    run_id = _compile(store)
    effects: list[str] = []

    async def port(capability, arguments):
        effects.append(capability)
        return {"status": "succeeded", "output": {}, "side_effects": ["wrote note.txt"]}

    real_update = store.update_objective_task

    def dies_while_recording_success(task_id, **fields):
        if fields.get("status") == O.TaskStatus.SUCCEEDED:
            raise _Died()
        return real_update(task_id, **fields)

    store.update_objective_task = dies_while_recording_success
    first = ContinuousTaskExecutor(backend, call_capability=port, executor_id="first")
    first.stop()
    with pytest.raises(_Died):
        asyncio.run(first.start(run_id))
    store.update_objective_task = real_update
    _expire_lease(store, run_id)
    asyncio.run(_recover(backend, run_id, port))
    assert store.objective_run(run_id)["status"] == O.RUN_COMPLETED
    return effects


def test_the_native_backend_passes_the_durability_test_with_temporal_absent(tmp_path):
    store = Store(tmp_path / "ada.sqlite3")
    backend = D.select_backend("native", native=store)  # no temporal provider given
    assert backend is store
    assert _kill_after_commit_then_resume(backend, store) == ["files_create"]


def test_another_backend_behind_the_interface_passes_the_same_test(tmp_path):
    store = Store(tmp_path / "ada.sqlite3")
    other = _OtherBackend(store)
    backend = D.select_backend("temporal", native=store, temporal=lambda: other)
    assert backend is other
    assert _kill_after_commit_then_resume(backend, store) == ["files_create"]
    assert other.calls, "the kernel never went through the other backend"


def test_temporal_absent_reports_unavailable_and_native_is_unaffected(tmp_path):
    store = Store(tmp_path / "ada.sqlite3")
    with pytest.raises(D.BackendUnavailable) as err:
        D.select_backend("temporal", native=store)
    assert err.value.state == fabric.UNAVAILABLE
    assert D.backend_status("temporal") == {"backend": "temporal", "state": fabric.UNAVAILABLE,
                                            "required": False}
    assert D.backend_status("native")["state"] == fabric.READY
    assert D.select_backend("native", native=store) is store


def test_a_provider_that_raises_is_unavailable_not_a_crash(tmp_path):
    store = Store(tmp_path / "ada.sqlite3")

    def refuses():
        raise ConnectionError("no server")

    with pytest.raises(D.BackendUnavailable):
        D.select_backend("temporal", native=store, temporal=refuses)


def test_a_backend_missing_a_kernel_operation_is_refused(tmp_path):
    store = Store(tmp_path / "ada.sqlite3")

    class Partial:
        def append_objective_event(self, *a, **k): ...

    with pytest.raises(D.BackendUnavailable) as err:
        D.select_backend("temporal", native=store, temporal=Partial)
    assert "objective_events" in str(err.value)


def test_an_unknown_backend_name_is_refused(tmp_path):
    with pytest.raises(ValueError):
        D.select_backend("zookeeper", native=Store(tmp_path / "ada.sqlite3"))


def test_the_store_provides_every_operation_the_interface_names(tmp_path):
    assert D.missing_operations(Store(tmp_path / "ada.sqlite3")) == []


class _InterfaceOnly:
    """A backend that offers the store's operations named in KERNEL_OPERATIONS
    and nothing else, as a provider built from the interface alone would."""

    def __init__(self, inner):
        self._inner = inner

    def __getattr__(self, name):
        if name not in D.KERNEL_OPERATIONS:
            raise AttributeError(f"{name} is not a kernel operation")
        return getattr(self._inner, name)


def test_the_trajectory_check_runs_on_nothing_but_the_interface(tmp_path):
    from friday import trajectory
    store = Store(tmp_path / "ada.sqlite3")
    run_id = _compile(store)
    backend = D.select_backend("temporal", native=store, temporal=lambda: _InterfaceOnly(store))
    assert trajectory.for_run(backend, run_id).verdict == trajectory.ON_TRACK


def test_a_backend_without_the_whole_window_counts_is_refused(tmp_path):
    store = Store(tmp_path / "ada.sqlite3")
    for absent in ("objective_events_named", "objective_event_counts"):
        class Short(_InterfaceOnly):
            def __getattr__(self, name):
                if name == absent:
                    raise AttributeError(name)
                return super().__getattr__(name)

        with pytest.raises(D.BackendUnavailable) as err:
            D.select_backend("temporal", native=store, temporal=lambda: Short(store))
        assert absent in str(err.value)
