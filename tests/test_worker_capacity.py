"""
Worker capacity: "at full capacity, marking as unavailable".

`friday_voice start` runs in production mode, where LiveKit pre-spawns
min(cpu_count, 4) idle job processes and refuses work above 0.7 CPU load. On
one busy laptop that meant four extra Python processes AND a worker that
declined jobs most of the time. Those defaults suit a fleet, not a desktop.
"""

from __future__ import annotations

import agent_friday as A


def test_idle_processes_are_not_the_fleet_default():
    """Production default is min(cpu_count, 4); on a 16-core box that is 4."""
    import os

    assert A.WORKER_IDLE_PROCESSES <= 2, (
        "pre-spawning several interpreters, each importing livekit and torch, "
        "is what loaded the machine in the first place"
    )
    assert A.WORKER_IDLE_PROCESSES >= 0
    assert os.cpu_count() is None or A.WORKER_IDLE_PROCESSES < (os.cpu_count() or 4)


def test_load_threshold_is_raised_but_still_legal():
    """LiveKit rejects a threshold above 1.0 outside development mode."""
    assert 0.7 < A.WORKER_LOAD_THRESHOLD <= 1.0


def test_worker_options_carry_the_tuning():
    options = A.worker_options()
    assert options.entrypoint_fnc is A.entrypoint
    assert options.num_idle_processes == A.WORKER_IDLE_PROCESSES
    assert options.load_threshold == A.WORKER_LOAD_THRESHOLD


def test_both_settings_are_overridable_by_env(monkeypatch):
    """A user on a spare machine may want the fleet behaviour back."""
    import importlib

    monkeypatch.setenv("ADA_IDLE_PROCESSES", "3")
    monkeypatch.setenv("ADA_LOAD_THRESHOLD", "0.75")
    reloaded = importlib.reload(A)
    try:
        assert reloaded.WORKER_IDLE_PROCESSES == 3
        assert reloaded.WORKER_LOAD_THRESHOLD == 0.75
    finally:
        monkeypatch.delenv("ADA_IDLE_PROCESSES", raising=False)
        monkeypatch.delenv("ADA_LOAD_THRESHOLD", raising=False)
        importlib.reload(A)


def test_options_are_accepted_by_livekit():
    """Construction is the real check: LiveKit validates threshold at build."""
    options = A.worker_options()
    assert options is not None
    from livekit.agents import WorkerOptions

    assert isinstance(options, WorkerOptions)
