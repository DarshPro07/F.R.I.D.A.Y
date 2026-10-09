"""EVAL-005: token productivity is verified useful work per 100K tokens.

The report joins what the gateway measured (route_outcomes, keyed by the
Hermes work_run_id) with what the objective engine verified (objective_tasks
that SUCCEEDED, whose result carries that same work_run_id). Every case builds
its own temp database through the real Store schema; the live database is
never opened.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from friday import productivity
from friday.execution_economics import RouteOutcomes
from friday.store import Store


def _seed_run(store: Store, run_id: str) -> None:
    store.open_objective_run(run_id, request=f"req {run_id}",
                             objective_summary=f"obj {run_id}")


def _seed_task(store: Store, task_id: str, run_id: str, *, status: str,
               work_run_id: str | None = None, attempts: int = 1) -> None:
    store.save_objective_task(task_id=task_id, run_id=run_id,
                              capability="hermes.work", arguments="{}",
                              status=status, attempts=attempts)
    if work_run_id is not None:
        store.update_objective_task(task_id, result={"work_run_id": work_run_id})


def _seed_outcome(outcomes: RouteOutcomes, work_run_id: str, *, prompt: int,
                  output: int, duration_s: float = 1.0, rework: int = 0) -> None:
    outcomes.record(work_run_id, task_class="code", route_level="L1",
                    tier="standard", prompt_tokens=prompt,
                    output_tokens=output, duration_s=duration_s,
                    status="COMPLETE")
    for _ in range(rework):
        outcomes.mark_rework(work_run_id)


@pytest.fixture()
def seeded(tmp_path):
    db = tmp_path / "productivity.sqlite3"
    store = Store(db)
    outcomes = RouteOutcomes(db)

    # Objective A: two verified tasks, 100,000 tokens, one retry + one rework.
    _seed_run(store, "obj-a")
    _seed_task(store, "a1", "obj-a", status="SUCCEEDED", work_run_id="hermes-a1")
    _seed_task(store, "a2", "obj-a", status="SUCCEEDED", work_run_id="hermes-a2",
               attempts=2)
    _seed_outcome(outcomes, "hermes-a1", prompt=30_000, output=10_000,
                  duration_s=10.0)
    _seed_outcome(outcomes, "hermes-a2", prompt=50_000, output=10_000,
                  duration_s=20.5, rework=1)

    # Objective B: one verified task and one FAILED task that still burned tokens.
    _seed_run(store, "obj-b")
    _seed_task(store, "b1", "obj-b", status="SUCCEEDED", work_run_id="hermes-b1")
    _seed_task(store, "b2", "obj-b", status="FAILED", work_run_id="hermes-b2",
               attempts=3)
    _seed_outcome(outcomes, "hermes-b1", prompt=150_000, output=50_000)
    _seed_outcome(outcomes, "hermes-b2", prompt=40_000, output=10_000)
    return db, store, outcomes


def test_two_objectives_give_the_exact_per_100k_figure(seeded):
    db, _, _ = seeded
    a = productivity.report("obj-a", db_path=db)
    b = productivity.report("obj-b", db_path=db)

    assert (a["verified_units"], a["tokens"]["total"]) == (2, 100_000)
    assert a["units_per_100k"] == pytest.approx(2.0)
    assert (b["verified_units"], b["tokens"]["total"]) == (1, 250_000)
    assert b["units_per_100k"] == pytest.approx(0.4)
    assert a["tokens"] == {"prompt": 80_000, "output": 20_000, "total": 100_000}
    assert a["wall_time_s"] == pytest.approx(30.5)


def test_a_failed_task_adds_tokens_but_no_verified_unit(seeded):
    db, _, _ = seeded
    b = productivity.report("obj-b", db_path=db)

    # 200,000 from the verified task + 50,000 burned by the failed one.
    assert b["tokens"]["total"] == 250_000
    assert b["verified_units"] == 1
    assert b["tasks"]["FAILED"] == 1


def test_retries_are_attempts_beyond_the_first_plus_rework(seeded):
    db, _, _ = seeded
    assert productivity.report("obj-a", db_path=db)["retries"] == 2   # 1 + rework 1
    assert productivity.report("obj-b", db_path=db)["retries"] == 2   # attempts 3 -> 2


def test_zero_tokens_is_none_never_zero(tmp_path):
    db = tmp_path / "zero.sqlite3"
    store = Store(db)
    outcomes = RouteOutcomes(db)
    _seed_run(store, "obj-z")
    _seed_task(store, "z1", "obj-z", status="SUCCEEDED", work_run_id="hermes-z1")
    _seed_outcome(outcomes, "hermes-z1", prompt=0, output=0)

    rep = productivity.report("obj-z", db_path=db)
    assert rep["verified_units"] == 1
    assert rep["units_per_100k"] is None


def test_unmeasured_work_does_not_inflate_the_figure(tmp_path):
    db = tmp_path / "unmeasured.sqlite3"
    store = Store(db)
    outcomes = RouteOutcomes(db)
    _seed_run(store, "obj-u")
    _seed_task(store, "u1", "obj-u", status="SUCCEEDED", work_run_id="hermes-u1")
    _seed_task(store, "u2", "obj-u", status="SUCCEEDED")              # no work run
    _seed_task(store, "u3", "obj-u", status="SUCCEEDED",
               work_run_id="hermes-gone")                            # no outcome row
    _seed_outcome(outcomes, "hermes-u1", prompt=40_000, output=10_000)

    rep = productivity.report("obj-u", db_path=db)
    assert rep["verified_units"] == 3
    assert rep["unmeasured_verified_units"] == 2
    # Only the one unit whose tokens were measured is priced.
    assert rep["units_per_100k"] == pytest.approx(2.0)


def test_nothing_measured_at_all_is_none(tmp_path):
    db = tmp_path / "nometer.sqlite3"
    store = Store(db)                     # no RouteOutcomes table was ever made
    _seed_run(store, "obj-n")
    _seed_task(store, "n1", "obj-n", status="SUCCEEDED", work_run_id="hermes-n1")

    rep = productivity.report("obj-n", db_path=db)
    assert rep["units_per_100k"] is None
    assert rep["unmeasured_verified_units"] == 1


def test_requirements_and_defects_are_declared_unmeasured(seeded):
    db, _, _ = seeded
    rep = productivity.report("obj-a", db_path=db)
    assert rep["requirements_closed"] == productivity.UNMEASURED
    assert rep["defects_fixed"] == productivity.UNMEASURED


def test_the_report_is_deterministic_and_read_only(seeded):
    db, _, _ = seeded
    before = db.read_bytes()
    first = productivity.report("obj-a", db_path=db)
    second = productivity.report("obj-a", db_path=db)

    assert first == second
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert db.read_bytes() == before


def test_an_unknown_run_is_an_error_not_an_empty_report(seeded):
    db, _, _ = seeded
    with pytest.raises(KeyError):
        productivity.report("obj-nope", db_path=db)


def test_a_missing_database_is_not_created(tmp_path):
    missing = tmp_path / "absent.sqlite3"
    with pytest.raises(sqlite3.OperationalError):
        productivity.report("obj-a", db_path=missing)
    assert not missing.exists()


def test_write_report_writes_one_json_file_per_run(seeded, tmp_path):
    db, _, _ = seeded
    out = tmp_path / "out"
    path = productivity.write_report("obj-a", directory=out, db_path=db)

    assert path == out / "obj-a.json"
    assert json.loads(path.read_text(encoding="utf-8")) == \
        productivity.report("obj-a", db_path=db)


@pytest.mark.parametrize("bad", ["../escape", "a/b", "a\\b", "", "x" * 200])
def test_write_report_refuses_a_run_id_that_is_not_a_file_name(seeded, tmp_path, bad):
    db, _, _ = seeded
    with pytest.raises(ValueError):
        productivity.write_report(bad, directory=tmp_path / "out", db_path=db)
