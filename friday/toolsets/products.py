"""
Product catalogue processing, as capabilities.

Transport extraction, not a redesign. The pipeline lives in
`friday/products.py` and the stages in `friday/product_stages.py`; neither is
touched here. What moved is everything that had accumulated in the MCP
adapter: run resolution, the summary shaping, the outcome filters, the export
writer. All of it was real domain work that happened to be written inside
`@mcp.tool()` functions, which meant a durable objective could not process a
catalogue, retry a failure, or export a result - six capabilities registered,
none reachable.

Everything the adapter guaranteed is preserved deliberately:

  run_id continuity     the store is the state authority, so a run survives a
                        restart and can be asked about tomorrow
  failures as results   a row that failed is COMPLETED/PARTIAL with reasons,
                        not a transport error
  the jail              both the catalogue read and the export write go
                        through the same filesystem jail as files_read,
                        because "process this catalogue" is a file read chosen
                        by an untrusted caller
  safe_to_mutate        a *mutating* call refuses to guess which run was meant
  literal outcomes      FAILED means the status FAILED; the English sense of
                        "which products failed" is `needs_attention`

The payloads are unchanged too, and the adapter returns them as they are. The
ActionResult wraps them rather than replacing them: the model keeps reading
the same deterministic fields, and the objective executor gets evidence.
"""

from __future__ import annotations

import json
from typing import TypedDict

from friday import contracts as c
from friday import products as P
from friday import runcontext as RC
from friday import runstate as RS
from friday.config import DATA_DIR
from friday.fsjail import JailError
from friday.policy import PolicyEngine, default_engine
from friday.store import Store
from friday.toolsets.files import jail
from friday.toolsets.system import APPROVAL_PREFIX

EXECUTION_SCOPE = "local_machine"

_store: Store | None = None

#: The run this process most recently started.
#:
#: "Retry those" means the catalogue we were just talking about, not the
#: newest row in a database that also holds every gate run from last night.
#: Friday is one person's assistant on one machine, so "this process" and
#: "this conversation" are the same thing here. It lives in the toolset rather
#: than the adapter now, so a run started by a durable objective and a run
#: started over MCP are the same "that one".
_active_run_id: str = ""


def store() -> Store:
    global _store
    if _store is None:
        _store = Store()
    return _store


def reset_store(new: Store | None = None) -> None:
    global _store, _active_run_id
    _store = new
    _active_run_id = ""


def active_run_id() -> str:
    return _active_run_id


# ---------------------------------------------------------------------------
# The shapes the model receives
# ---------------------------------------------------------------------------
#
# TypedDicts rather than plain dicts because FastMCP derives an `outputSchema`
# from the return annotation, and a plain `-> dict` produces none at all -
# measured on the installed SDK (1.27.0). With a schema the result also
# arrives as `structuredContent`, so the model reads deterministic state and
# writes the sentence, instead of parsing a sentence we wrote. They live here
# with the implementation and the adapter imports them for its annotations.


class RunSummary(TypedDict):
    run_id: str
    #: Two fields because they answer two questions - see friday/runstate.py.
    #: Handed a single `status: "PARTIAL"` for a catalogue that had finished,
    #: the model reported "processing is underway, two done so far", which is
    #: what the word means in ordinary English. COMPLETED/PARTIAL has one
    #: reading.
    execution_state: str
    outcome: str
    source: str
    input_rows: int
    canonical_products: int
    processed: int
    deduplicated: int
    partial: int
    quarantined: int
    failed: int
    duplicates_conflicting: int
    #: What THIS call re-ran, empty for a first pass. Without it product_retry
    #: returns run-level counts that are - correctly - unchanged by a retry
    #: that failed the same way, and the model reads "nothing changed" as
    #: "there was nothing to retry" and reports that it did nothing. It had
    #: just re-run A-204. A call must be able to say what it did.
    retried: list
    stages: list
    error: str


class RunStatus(TypedDict):
    run_id: str
    execution_state: str
    outcome: str
    #: Which run this is and why it was picked, when the caller gave no id.
    resolved_by: str
    records_done: int
    total_records: int
    started_at: str
    finished_at: str
    error: str


class RunList(TypedDict):
    runs: list
    error: str


class ExportResult(TypedDict):
    path: str
    rows: int
    run_id: str
    error: str


class RunResult(TypedDict):
    run_id: str
    execution_state: str
    outcome: str
    #: The direct answer to "which products failed?", as plain keys, and FIRST
    #: - with it last, behind every product's provenance, the model read the
    #: front of the payload and reported no failures while holding this list.
    did_not_succeed: list
    failures: list
    products: list
    resolved_by: str
    error: str


#: What a single product can have come out as. Literal, machine meaning.
RECORD_OUTCOMES = frozenset({P.SUCCEEDED, P.PARTIAL, P.FAILED, P.QUARANTINED})


def default_pipeline() -> P.Pipeline:
    """
    The stages a catalogue goes through.

    Kept out of products.py because the *engine* should not know which stages
    exist - that is what makes it reusable for a second kind of feed without
    editing it.
    """
    from friday.product_stages import build

    return build()


def _resolve(run_id: str, hint: str = "") -> RC.Resolution:
    """
    Which run the caller means. `run_id` empty is the normal case.

    The boss cannot know a run id Friday invented, and asking him for one is
    how this failed: a fresh session was asked "how did that catalogue job
    finish?" and answered "do you have the run id?".
    """
    if run_id.strip():
        return RC.Resolution(run_id.strip(), RC.EXPLICIT_RUN_ID,
                             "the run id you gave me", 1, RC.CERTAIN)
    return RC.resolve(store().product_runs(limit=10), hint=hint,
                      noun="catalogue run", active_run_id=_active_run_id)


def _basis(found: RC.Resolution) -> str:
    """How this run was arrived at, and why, in one readable string."""
    return f"{found.basis}: {found.reason}"


def _is_retryable(row: dict) -> bool:
    """Does this product have a failure worth another attempt?"""
    return any(stage.get("status") == P.FAILED_RETRYABLE
               for stage in (row["stages"] or {}).values())


def _blank_summary(error: str) -> RunSummary:
    return {"run_id": "", "execution_state": RS.COMPLETED,
            "outcome": RS.FAILED, "source": "", "input_rows": 0,
            "canonical_products": 0, "processed": 0, "deduplicated": 0,
            "partial": 0, "quarantined": 0, "failed": 0,
            "duplicates_conflicting": 0, "retried": [], "stages": [],
            "error": error}


def _summarise(summary: dict, retried: list | None = None) -> RunSummary:
    outcomes = summary.get("outcomes") or {}
    return {
        "run_id": summary.get("run_id", ""),
        # This one always ran to the end before returning; the outcome is the
        # only open question.
        **RS.describe(summary.get("status", P.FAILED), finished=True),
        "source": summary.get("source", ""),
        "input_rows": summary.get("input_rows", 0),
        "canonical_products": summary.get("canonical_products", 0),
        "processed": outcomes.get(P.PROCESSED, 0),
        "deduplicated": outcomes.get(P.DEDUPLICATED, 0),
        "partial": outcomes.get(P.PARTIAL, 0),
        "quarantined": outcomes.get(P.QUARANTINED, 0),
        "failed": outcomes.get(P.FAILED, 0),
        "duplicates_conflicting": summary.get("duplicates_conflicting", 0),
        "retried": list(retried or []),
        "stages": summary.get("stages", []),
        "error": "",
    }


def _refused(run: c.Run, tool_id: str, payload: dict, error: str,
             *, status: str = c.FAILED) -> c.ActionResult:
    """
    A refusal that still carries the shape the caller expects.

    The adapter returns `result.output` verbatim, and the model reads
    `error` off it. An ActionResult with no output would leave the MCP
    contract holding an empty dict where a RunSummary belongs.
    """
    return run.record(c.started(run.run_id, tool_id).finish(
        status=status, error=error, output={**payload, "error": error}))


def _gate(run: c.Run, tool_id: str, engine: PolicyEngine,
          payload: dict) -> c.ActionResult | None:
    verdict = engine.decide(tool_id)
    if verdict.allowed:
        return None
    return _refused(run, tool_id, payload,
                    f"{APPROVAL_PREFIX}: {verdict.reason} [{verdict.decision}]",
                    status=c.CANCELLED)


def _empty_status(run_id: str, resolved_by: str = "") -> RunStatus:
    return {"run_id": run_id, "execution_state": RS.INTERRUPTED,
            "outcome": RS.PENDING, "resolved_by": resolved_by,
            "records_done": 0, "total_records": 0, "started_at": "",
            "finished_at": "", "error": ""}


def _empty_result(run_id: str, resolved_by: str = "") -> RunResult:
    return {"run_id": run_id, "execution_state": RS.INTERRUPTED,
            "outcome": RS.PENDING, "resolved_by": resolved_by,
            "did_not_succeed": [], "failures": [], "products": [], "error": ""}


# ---------------------------------------------------------------------------
# The capabilities
# ---------------------------------------------------------------------------


def product_process(
    run: c.Run, path: str, source: str = "", *,
    engine: PolicyEngine = default_engine,
) -> c.ActionResult:
    """
    Process a product catalogue - a CSV or JSON file - end to end.

    Runs the whole catalogue before returning. Rows that fail are not errors:
    a PARTIAL outcome means the run worked and some rows did not, and
    `product_result` says which.
    """
    tool_id = "product.process"
    blank = _blank_summary("")
    blocked = _gate(run, tool_id, engine, blank)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    try:
        target = jail().resolve(path)
    except JailError as exc:
        return _refused(run, tool_id, blank, str(exc))
    if not target.is_file():
        return _refused(run, tool_id, blank, f"no such file: {target}")
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
        rows = (P.ingest_json(text) if target.suffix.lower() == ".json"
                else P.ingest_csv(text))
    except P.ProductError as exc:
        return _refused(run, tool_id, blank, str(exc))

    batch = P.Batch(default_pipeline(), store(), source=source or target.name)
    records = [P.ProductRecord(P.product_key(row) or f"row-{i}", row,
                               batch.run_id)
               for i, row in enumerate(rows)]
    verdict = P.classify_duplicates(records)
    global _active_run_id
    _active_run_id = batch.run_id
    batch.start(len(verdict["unique"]))
    batch.process(verdict["unique"], {"_duplicates": verdict})
    payload = _summarise(batch.summarise(input_rows=len(rows),
                                         duplicates=verdict))

    # Read the run back. `batch.process` returning is not evidence that the
    # run reached the database, which is the thing that has to survive a
    # restart for any of the other five capabilities to work.
    persisted = store().product_run(payload["run_id"])
    if persisted is None:
        return run.record(c.partial(
            started, f"{payload['run_id']} did not read back from the store",
            output=payload))

    return run.record(c.succeeded(
        started,
        output=payload,
        verification=c.Verification(
            method="run_readback",
            evidence=f"{payload['run_id']} read back from the store: "
                     f"{persisted['total_records']} record(s), status "
                     f"{persisted['status']}; {payload['input_rows']} input "
                     f"row(s) became {payload['canonical_products']} product(s)",
        ),
    ))


def product_status(
    run: c.Run, run_id: str = "", about: str = "", *,
    engine: PolicyEngine = default_engine,
) -> c.ActionResult:
    """How a catalogue run is doing. Never reprocesses anything."""
    tool_id = "product.status"
    blocked = _gate(run, tool_id, engine, _empty_status(run_id))
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    found = _resolve(run_id, about)
    run_id = found.run_id
    row = store().product_run(run_id) if run_id else None
    if row is None:
        return _refused(
            run, tool_id, _empty_status(run_id, _basis(found)),
            found.reason if not run_id else
            f"no run named {run_id!r} - call product_runs for the ids")

    payload: RunStatus = {
        "run_id": run_id,
        **RS.describe(row["status"], finished=bool(row["finished_at"])),
        "resolved_by": _basis(found),
        "records_done": len(store().product_records(run_id)),
        "total_records": row["total_records"],
        "started_at": row["started_at"] or "",
        "finished_at": row["finished_at"] or "", "error": "",
    }
    return run.record(c.succeeded(
        started,
        output=payload,
        verification=c.Verification(
            method="store_read",
            evidence=f"{run_id} read from the store: "
                     f"{payload['records_done']}/{payload['total_records']} "
                     f"record(s), {payload['execution_state']}/"
                     f"{payload['outcome']}",
        ),
    ))


def product_result(
    run: c.Run, run_id: str = "", outcome: str = "",
    needs_attention: bool | None = None, retryable: bool | None = None,
    include_fields: bool = False, about: str = "", only: str = "", *,
    engine: PolicyEngine = default_engine,
) -> c.ActionResult:
    """
    What a run produced, per product.

    `needs_attention` is what "which products failed?" means in English -
    anything that did not come out clean, whichever way. `outcome` is literal
    and strict. Reads the recorded run; never processes the catalogue again.
    """
    tool_id = "product.result"
    blocked = _gate(run, tool_id, engine, _empty_result(run_id))
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    found = _resolve(run_id, about)
    run_id = found.run_id
    row = store().product_run(run_id) if run_id else None
    if row is None:
        return _refused(
            run, tool_id, _empty_result(run_id, _basis(found)),
            found.reason if not run_id else
            f"no run named {run_id!r} - call product_runs for the ids")

    described = RS.describe(row["status"], finished=bool(row["finished_at"]))
    base = {**_empty_result(run_id, _basis(found)), **described}

    # `only` is gone, and refusing it out loud is not the same as deleting it.
    # Measured on the installed SDK: FastMCP accepts unknown arguments and
    # drops them, so a caller still sending only="failed" would get EVERY
    # product back with no error - a wrong answer wearing the shape of a right
    # one.
    if only.strip():
        return _refused(
            run, tool_id, base,
            f"`only` was removed and {only!r} was ignored. Use "
            f"outcome=SUCCEEDED|PARTIAL|FAILED|QUARANTINED for the literal "
            f"status, needs_attention=true for everything that did not come "
            f"out clean, or retryable=true.")

    wanted = outcome.strip().upper()
    if wanted and wanted not in RECORD_OUTCOMES:
        return _refused(
            run, tool_id, base,
            f"unknown outcome {outcome!r}; use one of "
            f"{sorted(RECORD_OUTCOMES)}, or leave it empty and filter on "
            f"needs_attention or retryable")

    rows = store().product_records(run_id)
    examined = len(rows)
    if wanted:
        rows = [r for r in rows if r["status"] == wanted]
    if needs_attention is not None:
        rows = [r for r in rows
                if (r["status"] != P.SUCCEEDED) is needs_attention]
    if retryable is not None:
        rows = [r for r in rows if _is_retryable(r) is retryable]

    failures = []
    for record in rows:
        for name, stage in (record["stages"] or {}).items():
            if stage.get("status") not in (P.SUCCEEDED, None):
                # A stage carries the same two questions as a run: a SKIPPED
                # stage never ran, and that is not a quality judgement.
                failures.append({
                    "product_key": record["product_key"], "stage": name,
                    **RS.describe(stage.get("status")),
                    "error": stage.get("error"),
                    "retryable": stage.get("status") == P.FAILED_RETRYABLE,
                })

    payload: RunResult = {
        "run_id": run_id, **described,
        "resolved_by": _basis(found),
        "did_not_succeed": [r["product_key"] for r in rows
                            if r["status"] != P.SUCCEEDED],
        "failures": failures,
        "products": [{"product_key": r["product_key"],
                      "outcome": r["status"],
                      "quarantine_reason": r["quarantine_reason"],
                      **({"fields": r["fields"]} if include_fields else {})}
                     for r in rows],
        "error": "",
    }
    return run.record(c.succeeded(
        started,
        output=payload,
        verification=c.Verification(
            method="store_read",
            evidence=f"{examined} record(s) read for {run_id}, "
                     f"{len(rows)} matched the filters, "
                     f"{len(payload['did_not_succeed'])} did not succeed",
        ),
    ))


def product_retry(
    run: c.Run, run_id: str = "", failure_class: str = "retryable",
    about: str = "", *, engine: PolicyEngine = default_engine,
) -> c.ActionResult:
    """
    Re-run only the products that failed in a way worth retrying.

    Quarantined rows are never retried: a price of "banana" will still not be
    a number. `retried` lists what this call actually re-ran - an empty list
    means there was nothing to retry, and a non-empty one means those products
    were re-run even if the counts look identical afterwards.
    """
    tool_id = "product.retry"
    blank = _blank_summary("")
    blocked = _gate(run, tool_id, engine, blank)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    found = _resolve(run_id, about)
    if not found.safe_to_mutate:
        # Reading the newest of several runs and naming it is helpful.
        # Re-running the newest because it happens to be newest is a coin toss
        # with side effects, against a catalogue nobody asked about.
        return _refused(run, tool_id, blank,
                        f"{found.reason} - say which run to retry, or call "
                        f"product_runs to see them")
    run_id = found.run_id
    row = store().product_run(run_id)
    if row is None:
        return _refused(run, tool_id, blank, f"no run named {run_id!r}")

    records_now = store().product_records(run_id)
    wanted = []
    for record in records_now:
        if record["status"] == P.QUARANTINED:
            continue
        stages = record["stages"] or {}
        retryable = _is_retryable(record)
        hard = any(s.get("status") == P.FAILED for s in stages.values())
        if retryable or (hard and failure_class.strip().lower() == "all"):
            wanted.append(record)

    if not wanted:
        payload = _summarise({**(row["summary"] or {}), "run_id": run_id,
                              "source": row["source"]})
        return run.record(c.succeeded(
            started,
            output=payload,
            verification=c.Verification(
                method="store_read",
                evidence=f"{len(records_now)} record(s) examined in {run_id}; "
                         f"none had a failure worth retrying, so nothing ran",
            ),
        ))

    batch = P.Batch(default_pipeline(), store(), run_id=run_id,
                    source=row["source"])
    # The row as it arrived, read back from the store. Reconstructing it from
    # the processed fields would re-run the pipeline against a different input
    # than the one that failed.
    records = [P.ProductRecord(r["product_key"], r["source_row"] or {}, run_id)
               for r in wanted]
    batch.process(records, {}, resume=False)
    retried = [r.product_key for r in records]
    payload = _summarise(batch.summarise(), retried=retried)

    return run.record(c.succeeded(
        started,
        output=payload,
        verification=c.Verification(
            method="reprocessed",
            evidence=f"{len(retried)} product(s) re-run in {run_id}: "
                     f"{retried[:5]}{' ...' if len(retried) > 5 else ''}",
        ),
    ))


def product_runs(
    run: c.Run, limit: int = 10, *, engine: PolicyEngine = default_engine,
) -> c.ActionResult:
    """Find a catalogue run when you do not have its id - newest first."""
    tool_id = "product.status"
    blocked = _gate(run, tool_id, engine, {"runs": [], "error": ""})
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    rows = store().product_runs(limit=max(1, limit))
    return run.record(c.succeeded(
        started,
        output={"runs": rows, "error": ""},
        verification=c.Verification(
            method="store_read",
            evidence=f"{len(rows)} run(s) listed from the store"
                     + (f", newest {rows[0].get('run_id')}" if rows else ""),
        ),
    ))


def product_export(
    run: c.Run, run_id: str = "", path: str = "", about: str = "", *,
    engine: PolicyEngine = default_engine,
) -> c.ActionResult:
    """
    Write a run's finished products to a CSV.

    Only products this run actually completed are written. Exporting a run
    that produced nothing fails rather than writing an empty file that looks
    like a result.
    """
    tool_id = "product.export"
    blank: ExportResult = {"path": "", "rows": 0, "run_id": run_id, "error": ""}
    blocked = _gate(run, tool_id, engine, blank)
    if blocked:
        return blocked
    started = c.started(run.run_id, tool_id)

    found = _resolve(run_id, about)
    if not found.safe_to_mutate:
        return _refused(run, tool_id, {**blank, "run_id": ""},
                        f"{found.reason} - say which run to export")
    run_id = found.run_id
    row = store().product_run(run_id)
    if row is None:
        return _refused(run, tool_id, {**blank, "run_id": run_id},
                        f"no run named {run_id!r}")

    rows = [r for r in store().product_records(run_id)
            if r["status"] in (P.SUCCEEDED, P.PARTIAL)]
    if not rows:
        return _refused(run, tool_id, {**blank, "run_id": run_id},
                        f"{run_id} produced no exportable products")

    try:
        target = (jail().resolve(path) if path
                  else DATA_DIR / "exports" / f"{run_id}.csv")
    except JailError as exc:
        return _refused(run, tool_id, {**blank, "run_id": run_id}, str(exc))

    target.parent.mkdir(parents=True, exist_ok=True)
    names = sorted({name for r in rows for name in (r["fields"] or {})})
    import csv as _csv

    with target.open("w", newline="", encoding="utf-8") as handle:
        writer = _csv.writer(handle)
        writer.writerow(["product_key", "status", *names])
        for record in rows:
            writer.writerow([
                record["product_key"], record["status"],
                *[json.dumps((record["fields"].get(n) or {}).get("value", ""),
                             default=str).strip('"') for n in names],
            ])

    payload: ExportResult = {"path": str(target), "rows": len(rows),
                             "run_id": run_id, "error": ""}

    # Read the file back. A `write` that did not raise is not evidence that
    # bytes reached the disk, and an export is the one product capability
    # whose whole result is a file somebody else will open.
    if not target.exists():
        return run.record(c.partial(
            started, f"{target} does not exist after writing it",
            output=payload))
    written = target.read_text(encoding="utf-8").splitlines()
    verification = c.Verification(
        method="file_readback",
        evidence=f"{target} exists at {target.stat().st_size} bytes, "
                 f"{len(written)} line(s) including the header, for "
                 f"{len(rows)} exported product(s)")
    return run.record(c.succeeded(
        started,
        output=payload,
        artifacts=(c.new_artifact(
            run_id=run.run_id, type="file", title=f"{run_id} products",
            path_or_uri=str(target), producer="product.export",
            verification=verification,
            metadata={"rows": len(rows), "product_run_id": run_id}),),
        verification=verification,
    ))
