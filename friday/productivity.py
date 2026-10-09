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

The report carries no timestamp, so two runs over the same rows are equal.
"""
from __future__ import annotations

import json
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


def report(run_id: str, *, db_path: str | Path | None = None) -> dict:
    """Verified work per 100K tokens for one objective run.

    Raises KeyError for a run the database does not know.
    """
    with _connect_readonly(_resolve_db(db_path)) as db:
        run = db.execute(
            "SELECT status FROM objective_runs WHERE run_id = ?",
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

    return {
        "run_id": run_id,
        "run_status": run["status"],
        "tasks": dict(sorted(by_status.items())),
        "verified_units": verified,
        "unmeasured_verified_units": verified - measured_verified,
        "tokens": {"prompt": prompt, "output": output, "total": total},
        "retries": attempt_retries + rework,
        "wall_time_s": round(sum(o["duration_s"] for o in measured), 3),
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
