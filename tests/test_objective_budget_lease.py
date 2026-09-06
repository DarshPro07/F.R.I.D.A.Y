"""FR-101: a child objective may not outspend its parent.

The gap this closes, measured on the tree before it was written: a parent
COMPLEX objective that had spent 399,000 of its 400,000-token ceiling could
delegate a child created with a FRESH 400,000, and nothing bounded how many
such children it made. `objectives.py` gives every run `budget_for(class)
.objective_ceiling` with no reference to who asked for it.

These tests use a real `Store` rather than a mock, because the lease reads
recorded spend through `measure()` and a mock would simply agree with
whatever the lease believes.
"""
from __future__ import annotations

import pathlib
import sqlite3

import pytest

from friday import model_gateway as mg
from friday import objective_budget as OB
from friday.store import Store


class Telemetry:
    """Minimal stand-in for GatewayTelemetry: `measure` calls
    `for_objective(run_id)` and sums input+output tokens."""

    def __init__(self, rows=()):
        self.rows = list(rows)

    def for_objective(self, run_id):
        return [r for r in self.rows if r.get("objective_id") == run_id]

    def spend(self, run_id, tokens):
        self.rows.append({"objective_id": run_id, "input_tokens": tokens,
                          "output_tokens": 0})


@pytest.fixture
def store(tmp_path):
    return Store(str(tmp_path / "objectives.sqlite3"))


def _run(store, run_id, *, task_class="COMPLEX", ceiling=None):
    store.open_objective_run(run_id, request=run_id, objective_summary=run_id)
    store.touch_objective_run(
        run_id, task_class=task_class,
        cost_budget_tokens=(mg.budget_for(task_class).objective_ceiling
                            if ceiling is None else ceiling))
    return run_id


class TestTheParentBudgetDominates:
    def test_a_child_cannot_be_leased_more_than_the_parent_has_left(self, store):
        tel = Telemetry()
        _run(store, "parent")
        tel.spend("parent", 399_000)          # 1,000 left of 400,000

        lease = OB.lease_for_child(store, "parent", requested=400_000, telemetry=tel)
        assert lease == 1_000

    def test_the_documented_defect_is_what_the_lease_prevents(self, store):
        """Without a lease the child ceiling is the class ceiling - proven
        here so the test names the behaviour it replaced."""
        tel = Telemetry()
        _run(store, "parent")
        tel.spend("parent", 399_000)

        naive = mg.budget_for("COMPLEX").objective_ceiling
        leased = OB.lease_for_child(store, "parent", requested=naive, telemetry=tel)
        assert naive == 400_000
        assert leased == 1_000
        assert leased < naive

    def test_a_modest_request_is_granted_in_full(self, store):
        tel = Telemetry()
        _run(store, "parent")
        tel.spend("parent", 100_000)          # 300,000 left
        assert OB.lease_for_child(store, "parent", requested=50_000, telemetry=tel) == 50_000

    def test_an_exhausted_parent_leases_nothing(self, store):
        tel = Telemetry()
        _run(store, "parent")
        tel.spend("parent", 400_000)
        assert OB.lease_for_child(store, "parent", requested=10_000, telemetry=tel) == 0

    def test_an_overspent_parent_leases_nothing_rather_than_a_negative(self, store):
        tel = Telemetry()
        _run(store, "parent")
        tel.spend("parent", 450_000)
        assert OB.lease_for_child(store, "parent", requested=10_000, telemetry=tel) == 0

    def test_children_in_sequence_cannot_together_exceed_the_parent(self, store):
        """The case that made the gap unbounded: many children, each within
        its own ceiling, together far beyond the parent's."""
        tel = Telemetry()
        _run(store, "parent")
        granted = 0
        for i in range(10):
            lease = OB.lease_for_child(store, "parent", requested=100_000, telemetry=tel)
            granted += lease
            tel.spend("parent", lease)        # the child's spend lands on the parent
        assert granted <= mg.budget_for("COMPLEX").objective_ceiling
        assert OB.lease_for_child(store, "parent", requested=1_000, telemetry=tel) == 0

    def test_a_reserve_keeps_something_for_the_parent_to_finish_with(self, store):
        """A child that consumes the last token leaves the parent unable to
        verify or report."""
        tel = Telemetry()
        _run(store, "parent")
        tel.spend("parent", 300_000)          # 100,000 left
        lease = OB.lease_for_child(store, "parent", requested=100_000,
                                   telemetry=tel, reserve_fraction=0.1)
        assert lease == 90_000

    def test_an_unlimited_parent_dominates_nothing(self, store):
        """LONG_RUNNING carries ceiling 0 = unlimited; a lease from it is
        the request unchanged, not zero."""
        tel = Telemetry()
        _run(store, "parent", ceiling=0)
        assert OB.lease_for_child(store, "parent", requested=25_000, telemetry=tel) == 25_000

    def test_an_unknown_parent_leases_the_request(self, store):
        """No parent row = not a delegation; do not silently zero a
        legitimate top-level objective."""
        assert OB.lease_for_child(store, "nonexistent", requested=5_000) == 5_000

    def test_a_negative_request_cannot_produce_a_negative_lease(self, store):
        tel = Telemetry()
        _run(store, "parent")
        assert OB.lease_for_child(store, "parent", requested=-5, telemetry=tel) == 0


class TestTheLeaseDoesNotDisturbExistingChecks:
    def test_check_still_allows_a_fresh_run(self, store):
        _run(store, "solo")
        assert OB.check(store, "solo", telemetry=Telemetry()).allowed is True

    def test_check_still_refuses_an_exhausted_run(self, store):
        tel = Telemetry()
        _run(store, "solo")
        tel.spend("solo", 400_000)
        verdict = OB.check(store, "solo", telemetry=tel)
        assert verdict.allowed is False
        assert verdict.dimension == "tokens"
