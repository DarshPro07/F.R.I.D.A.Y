"""EVAL-005: token productivity, as verified useful work per 100K tokens.

The inputs already exist in separate places and nothing joined them:

  * what the gateway measured - `route_outcomes` (friday/execution_economics.py),
    one row per terminal Hermes run, keyed by `work_run_id`, carrying prompt
    and output tokens, wall time and rework;
  * what the objective engine verified - `objective_tasks` (friday/store.py),
    where a SUCCEEDED task is a verified unit and its `result` carries the
    `work_run_id` of the Hermes run that did the work.

`report(run_id)` joins the two for one objective run. It is read-only: it
opens the database in SQLite's read-only mode, so a missing database is an
error rather than a freshly created empty one.

Honesty rules, because a ratio is easy to flatter:

  * Tokens count for every task that has a measured run, verified or not - a
    failed attempt still cost what it cost.
  * Only verified units whose tokens were measured are priced. A SUCCEEDED
    task with no work run, or whose run left no outcome row, is reported as
    `unmeasured_verified_units` and left out of the ratio, never counted as
    free work.
  * `units_per_100k` is None, not 0, when there are no measured tokens to
    divide by. Zero is a real answer (tokens spent, nothing verified).
  * `requirements_closed` and `defects_fixed` are named in the PRD but have no
    source anywhere in the tree, so they are the string UNMEASURED until one
    is named. They are not invented.

  * ORCH-009: `tool_calls` is the task attempts (what the objective budget
    counts), and `model_calls` is the model gateway's own ledger for the run
    (calls, retries, tokens, input by the five sources of `input_split`). That ledger is a separate
    SQLite file, so it is read only from a `telemetry` the caller names, or
    the default ledger when the default database is in use; otherwise it is
    UNMEASURED. A gate refusal (budget, data class, scope) was never sent, so it
    carries no tokens or time; a provider safeguard refusal was sent, so it does.
    The units-per-100K figure stays on the worker-measured tokens.

`wall_time_s` is the summed worker-measured durations; `objective_wall_time_s`
is the objective run's own start-to-finish time (None until it finishes).

The report carries no timestamp, so two runs over the same rows are equal.
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

#: Marker for a PRD dimension that has no data source yet.
UNMEASURED = "UNMEASURED"

TOKENS_PER_UNIT = 100_000

_SUCCEEDED = "SUCCEEDED"
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_BATCH = 500        # well under SQLite's bound-variable limit


def _resolve_db(db_path: str | Path | None) -> Path:
    if db_path is None:
        from friday.store import DEFAULT_DB
        db_path = os.getenv("ADA_DB") or DEFAULT_DB
    return Path(db_path)


def _connect_readonly(path: Path):
    from friday.dbconn import ledger_connection
    return ledger_connection(f"{path.resolve().as_uri()}?mode=ro", uri=True)


def _work_run_id(raw_result: str | None) -> str:
    """The Hermes run a task's result names, or '' when it names none."""
    if not raw_result:
        return ""
    try:
        result = json.loads(raw_result)
    except ValueError:
        return ""
    if not isinstance(result, dict):
        return ""
    return str(result.get("work_run_id") or "")


def _outcomes_for(db, work_run_ids: list[str]) -> dict[str, dict]:
    has_table = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table'"
        " AND name='route_outcomes'").fetchone()
    if not has_table:
        return {}
    found: dict[str, dict] = {}
    for start in range(0, len(work_run_ids), _BATCH):
        batch = work_run_ids[start:start + _BATCH]
        marks = ",".join("?" * len(batch))
        for row in db.execute(
                "SELECT work_run_id, prompt_tokens, output_tokens, duration_s,"
                f" rework FROM route_outcomes WHERE work_run_id IN ({marks})",
                batch):
            found[row["work_run_id"]] = dict(row)
    return found


def _elapsed_s(start, finish):
    """Seconds from the objective run's start to its finish, or None while it is
    unfinished or when either stamp is not an ISO time (never a guess)."""
    from datetime import datetime
    try:
        a, b = datetime.fromisoformat(start), datetime.fromisoformat(finish)
        return round(max((b - a).total_seconds(), 0.0), 3)
    except (TypeError, ValueError):
        return None


def _split(raw, allowed) -> dict:
    """A row's `input_split` as {source: tokens}, or {} when it is not a trustworthy
    split: unparseable, not an object, a key outside `allowed`, or a value that is
    not a non-negative integer. Such a row is unattributed, never partly summed."""
    try:
        split = json.loads(raw or "{}")
    except (ValueError, TypeError):
        return {}
    if not isinstance(split, dict):
        return {}
    for name, tokens in split.items():
        if name not in allowed or isinstance(tokens, bool)                 or not isinstance(tokens, int) or tokens < 0:
            return {}
    return split


def _model_calls(telemetry) -> dict:
    """The gateway ledger's rows for one objective, summed (ORCH-009).

    Input by source reads each row's `input_split` (instructions, task, code,
    tool results, history); a row without a readable split, such as one written
    before the column existed, is counted as unattributed rather than filed
    under another name. `retries` sums the retries the gateway itself recorded
    per row (`gateway_calls.retries`); `retried` counts the rows it retried.

    Only a row that reached a provider is a call and carries spend. `refused` counts
    every refused row and `failed` every failed one, so neither is a subset of
    `calls`: a gate refusal and a request that found no route were never sent.
    """
    from .model_gateway import INPUT_SOURCES
    out = {"calls": 0, "retried": 0, "failed": 0, "refused": 0, "retries": 0,
           "input_tokens": 0, "output_tokens": 0, "latency_ms": 0,
           "input_by_source": {}, "unattributed_input_tokens": 0}
    for row in telemetry:
        status = row["status"]
        state = row.get("entitlement_state")
        if status == "refused":
            out["refused"] += 1
            # A gate refusal (budget, data class, scope) never reached a provider
            # and carries nothing. A provider safeguard refusal did: the call was
            # sent, so its tokens, time and the route's retries are real spend.
            if state != "SAFEGUARD_REFUSED":
                continue
        elif state == "NO_ROUTE":
            # The gateway found no eligible route, so nothing was dispatched; the
            # row's input_tokens is the compiled context's size, not a billed call.
            out["failed"] += 1
            continue
        out["calls"] += 1
        if status in ("retried", "failed"):
            out[status] += 1
        out["input_tokens"] += row["input_tokens"]
        out["output_tokens"] += row["output_tokens"]
        out["latency_ms"] += row["latency_ms"]
        out["retries"] += row["retries"]
        sources = _split(row.get("input_split"), INPUT_SOURCES)
        if not sources:
            out["unattributed_input_tokens"] += row["input_tokens"]
            continue
        for name, tokens in sources.items():
            out["input_by_source"][name] = out["input_by_source"].get(name, 0) + tokens
    out["latency_s"] = round(out.pop("latency_ms") / 1000, 3)
    out["input_by_source"] = dict(sorted(out["input_by_source"].items()))
    return out


def report(run_id: str, *, db_path: str | Path | None = None,
           telemetry=None) -> dict:
    """Verified work per 100K tokens for one objective run.

    `telemetry` is a `GatewayTelemetry`; None means the default ledger when
    `db_path` is also the default, else the model calls are UNMEASURED.
    Raises KeyError for a run the database does not know.
    """
    with _connect_readonly(_resolve_db(db_path)) as db:
        run = db.execute(
            "SELECT status, created_at, finished_at FROM objective_runs WHERE run_id = ?",
            (run_id,)).fetchone()
        if run is None:
            raise KeyError(run_id)
        tasks = db.execute(
            "SELECT task_id, status, attempts, result FROM objective_tasks"
            " WHERE run_id = ? ORDER BY rowid", (run_id,)).fetchall()
        work_ids = {t["task_id"]: _work_run_id(t["result"]) for t in tasks}
        outcomes = _outcomes_for(
            db, sorted({w for w in work_ids.values() if w}))

    by_status: dict[str, int] = {}
    verified = measured_verified = 0
    attempt_retries = 0
    for task in tasks:
        by_status[task["status"]] = by_status.get(task["status"], 0) + 1
        attempt_retries += max((task["attempts"] or 0) - 1, 0)
        if task["status"] == _SUCCEEDED:
            verified += 1
            if work_ids[task["task_id"]] in outcomes:
                measured_verified += 1

    # One Hermes run is one measurement, however many tasks name it.
    measured = [outcomes[w] for w in sorted({w for w in work_ids.values()
                                             if w in outcomes})]
    prompt = sum(o["prompt_tokens"] for o in measured)
    output = sum(o["output_tokens"] for o in measured)
    total = prompt + output
    rework = sum(o["rework"] for o in measured)

    if telemetry is None and db_path is None:
        from friday.model_gateway import GatewayTelemetry
        telemetry = GatewayTelemetry()

    return {
        "run_id": run_id,
        "run_status": run["status"],
        "tasks": dict(sorted(by_status.items())),
        "verified_units": verified,
        "unmeasured_verified_units": verified - measured_verified,
        "tokens": {"prompt": prompt, "output": output, "total": total},
        "retries": attempt_retries + rework,
        "tool_calls": sum(t["attempts"] or 0 for t in tasks),
        "model_calls": (_model_calls(telemetry.for_objective(run_id))
                        if telemetry is not None else UNMEASURED),
        "wall_time_s": round(sum(o["duration_s"] for o in measured), 3),
        "objective_wall_time_s": _elapsed_s(run["created_at"], run["finished_at"]),
        "units_per_100k": (measured_verified / total * TOKENS_PER_UNIT
                           if total > 0 else None),
        "requirements_closed": UNMEASURED,
        "defects_fixed": UNMEASURED,
    }


def write_report(run_id: str, *, directory: str | Path | None = None,
                 db_path: str | Path | None = None) -> Path:
    """Write `<run_id>.json` and return its path.

    `directory` defaults to `productivity/` beside the database. The run id
    becomes a file name, so anything that is not a plain name is refused.
    """
    if not _RUN_ID_RE.match(run_id):
        raise ValueError(f"run id is not a safe file name: {run_id!r}")
    data = report(run_id, db_path=db_path)
    out = Path(directory) if directory is not None \
        else _resolve_db(db_path).parent / "productivity"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{run_id}.json"
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")
    return path


def write_run_report(run_id: str, store_path: str | Path) -> Path:
    """The report for a run the objective engine just finished.

    `store_path` is the database the finishing Store is on. When it is the
    default database the report is written with `db_path=None`, so the model
    gateway's own ledger is joined in as `model_calls` (ORCH-009); any other
    database is a self-contained one and gets the plain report.
    """
    path = Path(store_path)
    default = _resolve_db(None)
    try:
        same = path.resolve() == default.resolve()
    except OSError:
        same = False
    return write_report(run_id, db_path=None if same else path)


logger = logging.getLogger("friday-productivity")


def report_finished_run(store, run_id: str) -> Path | None:
    """EVAL-005: write the report for a run that has just been finished.

    The one caller-facing entry point for every place an objective run ends
    (completed, partial, failed, cancelled). The report observes a run that
    already has its verdict, so nothing here may change that verdict: a
    failure is logged with its cause and the run id, never raised and never
    silent. Returns the report path, or None when none was written.
    """
    db_path = getattr(store, "path", None)
    if db_path is None:
        logger.warning("productivity report for objective %s is UNAVAILABLE: "
                       "the store backend has no SQLite file", run_id)
        return None
    try:
        return write_run_report(run_id, db_path)
    except Exception:
        logger.warning("productivity report for objective %s not written",
                       run_id, exc_info=True)
        return None
