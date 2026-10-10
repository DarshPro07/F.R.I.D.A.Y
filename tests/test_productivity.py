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


# -- ORCH-009: the same report carries tool calls and the model calls ----------


def _seed_gateway(path, objective_id):
    from friday import model_gateway as mg
    ledger = mg.GatewayTelemetry(path)
    split = json.dumps({"instructions": 300, "task": 400, "code": 50,
                        "tool_results": 100, "history": 50})
    ledger.record(objective_id=objective_id, task_class="STANDARD", status="ok",
                  input_tokens=900, output_tokens=100, latency_ms=2000,
                  retries=2, input_split=split)
    ledger.record(objective_id=objective_id, task_class="STANDARD", status="retried",
                  input_tokens=900, latency_ms=500, input_split=split)
    ledger.record(objective_id=objective_id, task_class="STANDARD", status="failed",
                  input_tokens=100, latency_ms=250, retries=1)  # an old row: no split
    ledger.record(objective_id=objective_id, task_class="STANDARD", status="refused",
                  input_tokens=50_000)                         # never sent anywhere
    ledger.record(objective_id="someone-else", task_class="STANDARD", status="ok",
                  input_tokens=7, output_tokens=7, retries=9)
    return ledger


def test_tool_calls_are_the_task_attempts(seeded):
    db, _, _ = seeded
    assert productivity.report("obj-a", db_path=db)["tool_calls"] == 3   # 1 + 2
    assert productivity.report("obj-b", db_path=db)["tool_calls"] == 4   # 1 + 3


def test_model_calls_report_the_gateway_ledger_by_source(seeded, tmp_path):
    db, _, _ = seeded
    ledger = _seed_gateway(tmp_path / "gateway.sqlite3", "obj-a")
    rep = productivity.report("obj-a", db_path=db, telemetry=ledger)

    assert rep["model_calls"] == {
        "calls": 3, "retried": 1, "failed": 1, "refused": 1, "retries": 3,
        "input_tokens": 1_900, "output_tokens": 100, "latency_s": 2.75,
        "input_by_source": {"code": 100, "history": 100, "instructions": 600,
                            "task": 800, "tool_results": 200},
        "unattributed_input_tokens": 100,
    }
    # The EVAL-005 figures are untouched by it.
    assert rep["units_per_100k"] == pytest.approx(2.0)


def test_an_objective_with_no_model_calls_reports_zeros_not_unmeasured(seeded, tmp_path):
    db, _, _ = seeded
    from friday import model_gateway as mg
    rep = productivity.report("obj-b", db_path=db,
                              telemetry=mg.GatewayTelemetry(tmp_path / "empty.sqlite3"))
    assert rep["model_calls"]["calls"] == 0
    assert rep["model_calls"]["input_by_source"] == {}


def test_without_a_gateway_ledger_the_model_calls_are_unmeasured(seeded):
    db, _, _ = seeded
    assert productivity.report("obj-a", db_path=db)["model_calls"] == productivity.UNMEASURED


def test_a_row_with_only_the_old_role_sources_is_unattributed_not_misfiled(seeded, tmp_path):
    db, _, _ = seeded
    from friday import model_gateway as mg
    ledger = mg.GatewayTelemetry(tmp_path / "gateway.sqlite3")
    ledger.record(objective_id="obj-a", task_class="STANDARD", status="ok",
                  input_tokens=900, input_sources=json.dumps({"system": 300, "user": 600}))
    ledger.record(objective_id="obj-a", task_class="STANDARD", status="ok",
                  input_tokens=40, input_split="{not json")
    calls = productivity.report("obj-a", db_path=db, telemetry=ledger)["model_calls"]
    assert calls["input_by_source"] == {}
    assert calls["unattributed_input_tokens"] == 940


def test_a_provider_safeguard_refusal_was_sent_so_its_spend_and_retries_count(seeded, tmp_path):
    db, _, _ = seeded
    from friday import model_gateway as mg
    ledger = mg.GatewayTelemetry(tmp_path / "gateway.sqlite3")
    split = json.dumps({"instructions": 100, "task": 400})
    # The route retried once, then the provider itself refused: that row ends the route.
    ledger.record(objective_id="obj-a", task_class="STANDARD", status="refused",
                  input_tokens=500, latency_ms=1200, retries=1, input_split=split,
                  entitlement_state="SAFEGUARD_REFUSED")
    # A gate refusal never reached a provider: it carries nothing.
    ledger.record(objective_id="obj-a", task_class="STANDARD", status="refused",
                  input_tokens=9_000, latency_ms=77, retries=4, input_split=split,
                  entitlement_state="BUDGET_EXHAUSTED")
    calls = productivity.report("obj-a", db_path=db, telemetry=ledger)["model_calls"]
    assert calls["refused"] == 2
    assert calls["calls"] == 1
    assert calls["retries"] == 1
    assert calls["input_tokens"] == 500
    assert calls["latency_s"] == 1.2
    assert calls["input_by_source"] == {"instructions": 100, "task": 400}


def test_a_malformed_split_is_unattributed_not_summed(seeded, tmp_path):
    db, _, _ = seeded
    from friday import model_gateway as mg
    ledger = mg.GatewayTelemetry(tmp_path / "gateway.sqlite3")
    for bad in ({"bogus": 5, "task": 3}, {"task": -5, "code": 10}, {"task": "x"},
                {"task": True}, [1, 2]):
        ledger.record(objective_id="obj-a", task_class="STANDARD", status="ok",
                      input_tokens=20, input_split=json.dumps(bad))
    calls = productivity.report("obj-a", db_path=db, telemetry=ledger)["model_calls"]
    assert calls["input_by_source"] == {}
    assert calls["unattributed_input_tokens"] == 100


def test_the_objective_wall_time_is_its_own_start_to_finish(seeded):
    import sqlite3
    db, _, _ = seeded
    assert productivity.report("obj-a", db_path=db)["objective_wall_time_s"] is None
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE objective_runs SET created_at=?, finished_at=? WHERE run_id=?",
                     ("2026-10-10T10:00:00+00:00", "2026-10-10T10:02:05+00:00", "obj-a"))
    rep = productivity.report("obj-a", db_path=db)
    assert rep["objective_wall_time_s"] == 125.0
    assert rep["wall_time_s"] == pytest.approx(30.5)
