"""ULTRA-007: the ultracode mode switch and the substantive-task gate.

Mode off: no workflow objective is made. Mode on: a trivial task still skips
it and a substantive one (planner classification) compiles a workflow
objective. The mode adds no approval bypass: every CONFIRM stays a CONFIRM.
"""
from __future__ import annotations

import pytest

from friday import policy as P
from friday.contracts import PERSON
from friday.store import Store
from friday.ultracode import mode as M
from tests.test_ultracode_definition import _empty_profile_store  # noqa: F401  (autouse)
from tests.test_ultracode_durable import _compiled, _resolutions

TRIVIAL = "pause the music"
SUBSTANTIVE = ("refactor the scheduler across store.py, planner.py and objectives.py, "
               "then verify it with the full test suite")


@pytest.fixture(autouse=True)
def _mode_file(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "MODE_FILE", tmp_path / "ultracode_mode.json")


def _start(store, request):
    return M.maybe_start(store, request, _compiled(), resolutions=_resolutions(),
                         provenance=PERSON, attended=True)


def test_the_mode_is_off_until_turned_on_and_the_choice_persists():
    assert M.is_on() is False
    assert M.set_on(True) is True and M.is_on() is True
    assert M.MODE_FILE.exists()
    assert M.set_on(False) is False and M.is_on() is False


def test_a_corrupt_mode_file_reads_as_off():
    M.MODE_FILE.write_text("{not json", encoding="utf-8")
    assert M.is_on() is False


def test_mode_off_makes_no_workflow_objective_even_for_substantive_work():
    store = Store(":memory:")
    assert M.substantive(SUBSTANTIVE)
    assert _start(store, SUBSTANTIVE) is None
    assert store.objective_runs(limit=10) == []


def test_mode_on_a_trivial_task_skips_the_workflow():
    store = Store(":memory:")
    M.set_on(True)
    assert not M.substantive(TRIVIAL)
    assert _start(store, TRIVIAL) is None
    assert store.objective_runs(limit=10) == []


def test_mode_on_a_substantive_task_compiles_a_workflow_objective():
    store = Store(":memory:")
    M.set_on(True)
    run = _start(store, SUBSTANTIVE)
    assert run is not None
    runs = store.objective_runs(limit=10)
    assert [r["run_id"] for r in runs] == [run["run_id"]]
    assert len(store.objective_tasks(run["run_id"])) == len(_compiled().order)


def test_the_mode_adds_no_approval_bypass():
    before = P.resolve_policy(P.DANGEROUS)
    confirm = {c for c, d in before.items() if d == P.CONFIRM}
    assert confirm, "the destructive categories need a confirmation nonce"
    M.set_on(True)
    after = P.resolve_policy(P.DANGEROUS)
    assert {c for c, d in after.items() if d == P.CONFIRM} == confirm
