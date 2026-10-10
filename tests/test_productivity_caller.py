"""EVAL-005: a finished objective run writes its own productivity report.

The real Store and the real ContinuousTaskExecutor finish a run through
`_finish`; only the tool port is scripted. The gateway's measurement
(`route_outcomes`) is seeded in the same temp database, so the report that
lands on disk is the join the PRD names, with no model call anywhere.
"""
from __future__ import annotations

import json
import logging

import pytest

from friday import objectives as O
from friday import evolution_governor as EG
from friday import productivity
from friday.continuous import ContinuousTaskExecutor
from friday.contracts import PERSON
from friday.execution_economics import RouteOutcomes
from friday.objectives import compile_objective
from friday.store import Store

ITEMS = ("alpha", "bravo")


class WorkPort:
    """Each item's result names the Hermes run that did the work."""

    async def __call__(self, capability: str, arguments: dict):
        return {"ok": True, "work_run_id": f"hermes-{arguments['item']}"}


def _compile(store: Store) -> str:
    run = compile_objective(
        store, request="process the batch",
        tasks=[{"capability": "process_item", "arguments": {"item": item}}
               for item in ITEMS],
        manifest=[{"id": "process_item", "description": "process one item"}],
        objective_summary="process the batch", provenance=PERSON, attended=True)
    return run["run_id"]


def _seed_tokens(db) -> None:
    outcomes = RouteOutcomes(db)
    for item, prompt, output in (("alpha", 30_000, 10_000),
                                 ("bravo", 50_000, 10_000)):
        outcomes.record(f"hermes-{item}", task_class="code", route_level="L1",
                        tier="standard", prompt_tokens=prompt,
                        output_tokens=output, duration_s=1.0,
                        status="COMPLETE")


async def _run(store: Store, run_id: str) -> None:
    executor = ContinuousTaskExecutor(store, WorkPort(), executor_id="eval-005")
    executor.portion_budget = len(ITEMS)
    await executor.start(run_id)
    executor.stop()


@pytest.mark.asyncio
async def test_a_completed_run_writes_its_productivity_report(tmp_path):
    db = tmp_path / "ada.sqlite3"
    store = Store(db)
    _seed_tokens(db)
    run_id = _compile(store)

    await _run(store, run_id)

    assert store.objective_run(run_id)["status"] == O.RunStatus.COMPLETED
    path = tmp_path / "productivity" / f"{run_id}.json"
    assert path.is_file(), "the finished run left no productivity report"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["run_id"] == run_id
    assert data["run_status"] == O.RunStatus.COMPLETED
    assert data["verified_units"] == 2
    assert data["tokens"] == {"prompt": 80_000, "output": 20_000,
                              "total": 100_000}
    assert data["units_per_100k"] == pytest.approx(2.0)
    assert data["requirements_closed"] == productivity.UNMEASURED
    assert data["defects_fixed"] == productivity.UNMEASURED


@pytest.mark.asyncio
async def test_a_report_that_cannot_be_written_is_logged_and_the_run_still_finishes(
        tmp_path, monkeypatch, caplog):
    db = tmp_path / "ada.sqlite3"
    store = Store(db)
    run_id = _compile(store)

    def refuse(*args, **kwargs):
        raise OSError("disk is read-only")

    monkeypatch.setattr(productivity, "write_report", refuse)
    with caplog.at_level(logging.WARNING, logger="friday-productivity"):
        await _run(store, run_id)

    assert store.objective_run(run_id)["status"] == O.RunStatus.COMPLETED
    logged = [r for r in caplog.records if r.name == "friday-productivity"
              and r.levelno >= logging.WARNING and run_id in r.getMessage()
              and "productivity" in r.getMessage()]
    assert logged, "the failed report write was not logged"
    assert logged[0].exc_info, "the log line dropped the cause"
    assert not (tmp_path / "productivity").exists()


def test_the_default_database_joins_the_gateway_ledger_and_any_other_does_not(
        tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(productivity, "write_report",
                        lambda run_id, *, db_path=None: seen.append(db_path))
    default = tmp_path / "default.sqlite3"
    other = tmp_path / "other.sqlite3"
    monkeypatch.setenv("ADA_DB", str(default))

    productivity.write_run_report("RUN-1", default)
    productivity.write_run_report("RUN-2", other)

    assert seen == [None, other]


def _cancelled_report(tmp_path, run_id):
    path = tmp_path / "productivity" / f"{run_id}.json"
    assert path.is_file(), "the cancelled run left no productivity report"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["run_id"] == run_id
    assert data["run_status"] == O.RunStatus.CANCELLED
    return data


def test_a_run_cancelled_through_the_executor_writes_its_report(tmp_path):
    store = Store(tmp_path / "ada.sqlite3")
    run_id = _compile(store)
    executor = ContinuousTaskExecutor(store, WorkPort(), executor_id="eval-005")

    assert executor.cancel(run_id, reason="person said stop") is True

    assert store.objective_run(run_id)["status"] == O.RunStatus.CANCELLED
    _cancelled_report(tmp_path, run_id)


def test_a_run_cancelled_through_the_engine_writes_its_report(tmp_path):
    store = Store(tmp_path / "ada.sqlite3")
    run_id = _compile(store)

    assert O.cancel_run(store, run_id=run_id, reason="person said stop",
                        executor_id="eval-005") is True

    _cancelled_report(tmp_path, run_id)


def test_an_evolution_experiment_stop_writes_its_report(tmp_path):
    store = Store(tmp_path / "ada.sqlite3")
    run_id = _compile(store)

    class Governor:
        released = None

        def release(self, lease):
            self.released = lease

    gov = Governor()
    stop = EG._stop(store, gov, run_id, "lease-1", {"reason": "preempted"})

    assert stop.stopped is True and gov.released == "lease-1"
    _cancelled_report(tmp_path, run_id)


def test_a_failed_report_on_a_cancel_path_is_logged_and_the_cancel_stands(
        tmp_path, monkeypatch, caplog):
    store = Store(tmp_path / "ada.sqlite3")
    run_id = _compile(store)
    monkeypatch.setattr(productivity, "write_report",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("ro")))

    with caplog.at_level(logging.WARNING, logger="friday-productivity"):
        assert O.cancel_run(store, run_id=run_id, reason="stop",
                            executor_id="eval-005") is True

    assert store.objective_run(run_id)["status"] == O.RunStatus.CANCELLED
    assert any(run_id in r.getMessage() and r.exc_info for r in caplog.records)


def test_a_backend_without_a_sqlite_file_logs_one_line_and_skips(caplog):
    class Kernel:     # only the documented kernel operations, no .path
        pass

    with caplog.at_level(logging.WARNING, logger="friday-productivity"):
        assert productivity.report_finished_run(Kernel(), "RUN-9") is None

    records = [r for r in caplog.records if "RUN-9" in r.getMessage()]
    assert len(records) == 1
    assert "UNAVAILABLE" in records[0].getMessage()
    assert not records[0].exc_info
