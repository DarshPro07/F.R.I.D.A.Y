"""
Product processing: a per-record graph, not a linear script.

The shape that makes this worth building is that a missing image must not
prevent normalisation, classification or pricing analysis - but it must
prevent the export that needs the image. That is a graph statement, and a
pipeline written as a sequence of `for` loops cannot make it.

    INGEST -> VALIDATE -> NORMALIZE -+-> ENRICH   -> CLASSIFY -+-> DEDUPE
                                     +-> IMAGES   -> PROCESS  -+
                                                                -> GENERATE
                                                                -> EXPORT

Four things this refuses to do, each because the alternative hides a failure:

  one bad row never fails the batch        it is quarantined, by itself, with
                                           the reason, and the other records
                                           carry on
  a partial record is never "succeeded"    a product with structured data and
                                           no generated copy is PARTIAL, and
                                           says which stage did not run
  no silent overwrite on duplicates        an exact duplicate is deduped; a
                                           conflicting one becomes a conflict
                                           record, because "last write wins"
                                           is a decision nobody made
  no output without provenance             every generated field carries where
                                           it came from and how, so the
                                           catalogue can be explained rather
                                           than only produced

Two invariants from elsewhere in this codebase apply here and are enforced
rather than assumed:

  crash/resume     a run is durable. Records already finished are not redone,
                   and a killed batch resumes as the same run_id.
  stale evidence   a stage result belongs to a run only if it was produced by
                   that run. The verifier must never see an earlier run's
                   export and call this one successful.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import re
import time
from dataclasses import dataclass, field

from friday import contracts as c
from friday import dag, netguard

logger = logging.getLogger("friday.products")

SCHEMA_VERSION = 1

# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

#: Per record. Deliberately not a boolean: "did the batch work" is not a
#: question with two answers once one product in four hundred has no image.
SUCCEEDED = "SUCCEEDED"
PARTIAL = "PARTIAL"
FAILED = "FAILED"
QUARANTINED = "QUARANTINED"

#: A stage that timed out or hit a transient dependency is retryable; a stage
#: given a bad row is not, and retrying it is just a slower failure.
FAILED_RETRYABLE = "FAILED_RETRYABLE"

RECORD_STATUSES = (SUCCEEDED, PARTIAL, FAILED, QUARANTINED)

#: What happened to a *record*, as distinct from how its stages went.
#:
#: These exist because SUCCEEDED was carrying two different meanings: a
#: product that went through every stage, and a duplicate that was correctly
#: collapsed. Both are "fine", and only one of them was generated, priced and
#: exported - so "I successfully processed 41 products" was true of 40. A
#: count that quietly includes work not done is the same class of problem as
#: an unverified claim, arriving through arithmetic instead of prose.
PROCESSED = "PROCESSED"
DEDUPLICATED = "DEDUPLICATED"

RECORD_OUTCOMES = (PROCESSED, DEDUPLICATED, PARTIAL, QUARANTINED, FAILED)


class ProductError(ValueError):
    """The batch or its definition is wrong in a way that must stop it."""


# ---------------------------------------------------------------------------
# Untrusted input: URLs
# ---------------------------------------------------------------------------

def safe_url(raw: str, *, allow_private: bool = False) -> str:
    """
    Validate a URL from a spreadsheet or a model, at *row validation* time.

    A product feed is untrusted input in exactly the way a prompt is: an image
    URL of `http://169.254.169.254/latest/meta-data/` is a request for cloud
    credentials wearing a picture's clothes.

    This is deliberately the weaker of the two checks. It accepts a host that
    does not resolve, because a catalogue must not fail ingestion the day DNS
    is briefly unreachable. Passing here is NOT permission to fetch - the
    connection-time gate in friday.netguard resolves again at the moment it
    connects, pins the connection to the address it validated, and revalidates
    every redirect. See INVARIANTS.md.
    """
    try:
        return netguard.check(raw, allow_private=allow_private)["url"]
    except netguard.UrlRefused as exc:
        raise ProductError(str(exc)) from exc


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------


def provenance(value, *, source, method: str, confidence: float | None = None) -> dict:
    """
    A field, and where it came from.

    An enriched catalogue whose fields cannot be traced is a catalogue nobody
    can correct: "material: cotton" is a different thing when it was read from
    the supplier's column than when a model inferred it from prose, and only
    one of those should be argued with.
    """
    record = {
        "value": value,
        "source": list(source) if isinstance(source, (list, tuple)) else source,
        "method": method,
    }
    if confidence is not None:
        record["confidence"] = round(float(confidence), 3)
    return record


DIRECT = "direct"
DERIVED = "derived"
INFERRED = "llm_extract"
GENERATED = "llm_generate"


# ---------------------------------------------------------------------------
# Records and runs
# ---------------------------------------------------------------------------


def hashed(value) -> str:
    """A stable hash of a record's content, for idempotency and change detection."""
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:32]


@dataclass
class StageResult:
    stage: str
    status: str
    attempts: int = 1
    error: str | None = None
    warnings: list[str] = field(default_factory=list)
    took_ms: int = 0
    output_hash: str = ""
    #: run_id + product_key + stage + input_hash + schema version. Two runs
    #: over the same unchanged row produce the same key for a stage, so a
    #: retry can tell "the same work" from "the product changed underneath me".
    idempotency_key: str = ""

    def to_dict(self) -> dict:
        return {
            "stage": self.stage, "status": self.status, "attempts": self.attempts,
            "error": self.error, "warnings": self.warnings,
            "took_ms": self.took_ms, "output_hash": self.output_hash,
            "idempotency_key": self.idempotency_key,
        }


@dataclass
class ProductRecord:
    """One product, and everything that happened to it."""

    product_key: str
    source_row: dict
    run_id: str
    input_hash: str = ""
    fields: dict = field(default_factory=dict)      # name -> provenance record
    stages: dict[str, StageResult] = field(default_factory=dict)
    status: str = SUCCEEDED
    quarantine_reason: str = ""
    #: How many identical rows this record stood in for. Reported separately
    #: from processing, never folded into it.
    collapsed: int = 0

    def __post_init__(self) -> None:
        if not self.input_hash:
            self.input_hash = hashed(self.source_row)

    def key_for(self, stage: str) -> str:
        return hashed([self.run_id, self.product_key, stage, self.input_hash,
                       SCHEMA_VERSION])

    def value(self, name, default=None):
        entry = self.fields.get(name)
        return entry["value"] if entry else default

    def set(self, name: str, value, **provenance_kwargs) -> None:
        self.fields[name] = provenance(value, **provenance_kwargs)

    def resolve_status(self) -> str:
        """
        The record's own verdict, derived from its stages rather than declared.

        A record is SUCCEEDED only when every stage that ran succeeded and none
        was skipped. Anything less says so - which is the point: a product with
        good structured data and no generated copy is genuinely useful and
        genuinely not finished, and one word has to carry that.
        """
        if self.status == QUARANTINED:
            return QUARANTINED
        outcomes = [s.status for s in self.stages.values()]
        if not outcomes:
            return FAILED
        if all(o == SUCCEEDED for o in outcomes):
            return SUCCEEDED
        if any(o == SUCCEEDED for o in outcomes):
            return PARTIAL
        return FAILED

    def to_dict(self) -> dict:
        return {
            "product_key": self.product_key, "run_id": self.run_id,
            "status": self.resolve_status(), "input_hash": self.input_hash,
            "output_hash": hashed(self.fields),
            "quarantine_reason": self.quarantine_reason,
            "collapsed": self.collapsed,
            "source_row": self.source_row,
            "fields": self.fields,
            "stages": {name: s.to_dict() for name, s in self.stages.items()},
        }


# ---------------------------------------------------------------------------
# Stages
# ---------------------------------------------------------------------------


@dataclass
class Stage:
    """
    One step in the per-record graph.

    `needs` is what makes a missing image cost only the stages that actually
    need the image. `retries` applies to FAILED_RETRYABLE outcomes only:
    retrying a row whose price says "banana" is a slower way to fail.
    """

    name: str
    run: object                       # (record, context) -> None | str | dict
    needs: tuple[str, ...] = ()
    retries: int = 0
    #: A stage may be declared optional, meaning its failure downgrades the
    #: record to PARTIAL rather than failing it. Export is not optional.
    optional: bool = False


MAX_RETRIES = 3


class Pipeline:
    """A named graph of stages, executed per record."""

    def __init__(self, stages: list[Stage]) -> None:
        if not stages:
            raise ProductError("a pipeline needs at least one stage")
        self.stages = {stage.name: stage for stage in stages}
        if len(self.stages) != len(stages):
            raise ProductError("two stages share a name")
        for stage in stages:
            if not 0 <= stage.retries <= MAX_RETRIES:
                raise ProductError(
                    f"stage {stage.name!r}: retries must be 0..{MAX_RETRIES}")
        try:
            self.order = dag.topological(
                {name: list(stage.needs) for name, stage in self.stages.items()})
        except dag.CycleError as exc:
            raise ProductError(str(exc)) from exc

    def process(self, record: ProductRecord, context: dict) -> ProductRecord:
        """
        Run one record through the graph.

        Never raises for a stage failure. A failed stage is a recorded outcome,
        because the caller's next question is always "which ones, and why", and
        an exception answers neither.
        """
        failed: set[str] = set()
        for name in self.order:
            stage = self.stages[name]
            blocked = dag.blocked_by(list(stage.needs), failed)
            if blocked:
                record.stages[name] = StageResult(
                    stage=name, status="SKIPPED", attempts=0,
                    error=f"needs {', '.join(blocked)}, which did not succeed",
                    idempotency_key=record.key_for(name))
                failed.add(name)
                continue

            result = self._attempt(stage, record, context)
            record.stages[name] = result
            if result.status not in (SUCCEEDED,):
                failed.add(name)
                if record.status == QUARANTINED:
                    break
        record.status = record.resolve_status()
        return record

    def _attempt(self, stage: Stage, record: ProductRecord,
                 context: dict) -> StageResult:
        started = time.monotonic()
        key = record.key_for(stage.name)
        attempts, error, warnings = 0, None, []

        for attempt in range(stage.retries + 1):
            attempts = attempt + 1
            try:
                outcome = stage.run(record, context)
            except Quarantine as exc:
                record.status = QUARANTINED
                record.quarantine_reason = str(exc)
                return StageResult(stage=stage.name, status=QUARANTINED,
                                   attempts=attempts, error=str(exc),
                                   took_ms=_ms(started), idempotency_key=key)
            except Retryable as exc:
                error = str(exc)
                if attempt < stage.retries:
                    continue
                return StageResult(stage=stage.name, status=FAILED_RETRYABLE,
                                   attempts=attempts, error=error,
                                   took_ms=_ms(started), idempotency_key=key)
            except Exception as exc:
                return StageResult(stage=stage.name, status=FAILED,
                                   attempts=attempts,
                                   error=f"{type(exc).__name__}: {exc}",
                                   took_ms=_ms(started), idempotency_key=key)
            else:
                if isinstance(outcome, dict):
                    warnings = list(outcome.get("warnings") or [])
                break

        return StageResult(stage=stage.name, status=SUCCEEDED, attempts=attempts,
                           warnings=warnings, took_ms=_ms(started),
                           output_hash=hashed(record.fields),
                           idempotency_key=key)


def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


class Quarantine(Exception):
    """This record cannot be processed at all. It is set aside, not retried."""


class Retryable(Exception):
    """A transient failure - a timeout, a rate limit, a model that fell over."""


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------

KEY_FIELDS = ("sku", "id", "product_id", "handle")


def ingest_csv(text: str) -> list[dict]:
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        raise ProductError("the csv has no rows")
    return [{(k or "").strip(): (v or "").strip() for k, v in row.items()}
            for row in rows]


def ingest_json(text: str) -> list[dict]:
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ProductError(f"not valid json: {exc}") from exc
    if isinstance(data, dict):
        data = data.get("products") or data.get("items") or [data]
    if not isinstance(data, list) or not data:
        raise ProductError("the json has no product list")
    return [row for row in data if isinstance(row, dict)]


def product_key(row: dict) -> str:
    for name in KEY_FIELDS:
        value = str(row.get(name, "")).strip()
        if value:
            return value
    return ""


# ---------------------------------------------------------------------------
# Duplicates
# ---------------------------------------------------------------------------

DUPLICATE_EXACT = "exact"
DUPLICATE_CONFLICT = "conflict"


def classify_duplicates(records: list[ProductRecord]) -> dict:
    """
    Group by key, and split "the same row twice" from "two different rows
    claiming the same key".

    The second is not a duplicate, it is a disagreement, and resolving it by
    keeping whichever arrived last is a decision nobody made and nobody can
    see afterwards.
    """
    by_key: dict[str, list[ProductRecord]] = {}
    for record in records:
        by_key.setdefault(record.product_key, []).append(record)

    exact, conflicts, unique = [], [], []
    for key, group in by_key.items():
        if len(group) == 1:
            unique.append(group[0])
            continue
        hashes = {record.input_hash for record in group}
        if len(hashes) == 1:
            kept = group[0]
            # The survivor is still processed; the copies it stood in for are
            # counted as DEDUPLICATED so "processed 41 products" cannot be
            # said of 40 products and one collapsed row.
            kept.collapsed = len(group) - 1
            unique.append(kept)
            exact.append({"product_key": key, "copies": len(group),
                          "kind": DUPLICATE_EXACT})
        else:
            conflicts.append({
                "product_key": key, "copies": len(group),
                "kind": DUPLICATE_CONFLICT,
                "input_hashes": sorted(hashes),
                "reason": "same key, different content - not merged",
            })
            for record in group:
                record.status = QUARANTINED
                record.quarantine_reason = (
                    f"duplicate key {key!r} with conflicting content")
                unique.append(record)
    return {"unique": unique, "exact": exact, "conflicts": conflicts}


# ---------------------------------------------------------------------------
# The batch: durable, resumable, and unable to inherit an earlier run's proof
# ---------------------------------------------------------------------------


class Batch:
    """
    One processing run over many records.

    Durable by design. A batch of four hundred products that dies at
    forty-seven and starts again from one is not a pipeline, it is an
    apology - so each finished record is written as it completes and a resumed
    run keeps its own `run_id` rather than inventing a new one.
    """

    def __init__(self, pipeline: Pipeline, store, *, run_id: str = "",
                 source: str = "") -> None:
        self.pipeline = pipeline
        self.store = store
        self.run_id = run_id or c.new_run_id()
        self.source = source

    # -- lifecycle ---------------------------------------------------------

    def start(self, total: int) -> None:
        self.store.start_product_run(
            self.run_id, source=self.source, total=total,
            schema_version=SCHEMA_VERSION,
            stages=[stage for stage in self.pipeline.order])

    def done_keys(self) -> set[str]:
        """
        Which product keys this run has already finished.

        Scoped to `self.run_id` on purpose, and this is the stale-evidence
        rule rather than an optimisation: a record completed by an *earlier*
        run must not let this run skip work, and an earlier run's export must
        never be read as this run's proof.
        """
        return {row["product_key"]
                for row in self.store.product_records(self.run_id)}

    def process(self, records: list[ProductRecord], context: dict | None = None,
                *, resume: bool = True) -> dict:
        context = dict(context or {})
        already = self.done_keys() if resume else set()
        processed, skipped = [], 0

        for record in records:
            if record.product_key in already:
                skipped += 1
                continue
            if record.status == QUARANTINED:
                # Set aside before the graph ran - a conflicting duplicate.
                self.store.save_product_record(self.run_id, record.to_dict())
                processed.append(record)
                continue
            self.pipeline.process(record, context)
            self.store.save_product_record(self.run_id, record.to_dict())
            processed.append(record)

        return self.summarise(resumed_skipped=skipped,
                              duplicates=context.get('_duplicates'))

    def summarise(self, *, resumed_skipped: int = 0,
                  input_rows: int = 0, duplicates: dict | None = None) -> dict:
        """
        The run's own account of itself, with rows and products kept apart.

        `input_rows` is what arrived; `canonical_products` is what there
        actually were once identical rows were collapsed; `processed` is how
        many of those went through the pipeline. Reporting one number for all
        three is how "I successfully processed 41 products" gets said about 40
        products and a duplicate.
        """
        rows = self.store.product_records(self.run_id)
        duplicates = duplicates or {}
        counts = {status: 0 for status in RECORD_STATUSES}
        collapsed = 0
        for row in rows:
            counts[row["status"]] = counts.get(row["status"], 0) + 1
            collapsed += int(row.get("collapsed") or 0)

        if counts[SUCCEEDED] and not (counts[FAILED] or counts[QUARANTINED]
                                      or counts[PARTIAL]):
            verdict = SUCCEEDED
        elif counts[SUCCEEDED] or counts[PARTIAL]:
            verdict = PARTIAL
        else:
            verdict = FAILED

        summary = {
            "run_id": self.run_id, "source": self.source,
            "schema_version": SCHEMA_VERSION,
            "status": verdict,
            # Rows in, products out, and the difference explained.
            "input_rows": input_rows or (len(rows) + collapsed),
            "canonical_products": len(rows),
            "outcomes": {
                PROCESSED: counts[SUCCEEDED],
                DEDUPLICATED: collapsed,
                PARTIAL: counts[PARTIAL],
                QUARANTINED: counts[QUARANTINED],
                FAILED: counts[FAILED],
            },
            "duplicates_exact": len(duplicates.get("exact") or []),
            "duplicates_conflicting": len(duplicates.get("conflicts") or []),
            "counts": counts,          # kept: existing callers read this
            "records": len(rows), "resumed_skipped": resumed_skipped,
            "stages": list(self.pipeline.order),
        }
        self.store.finish_product_run(self.run_id, status=verdict,
                                      summary=summary)
        return summary

    # -- evidence ----------------------------------------------------------

    def exports(self) -> list[dict]:
        """
        Records this run exported. Never another run's.

        The check that makes this necessary: a verifier that asks "does an
        export exist" will find the previous run's and call this one a
        success. It has to ask "did *this* run produce one".
        """
        return [row for row in self.store.product_records(self.run_id)
                if row["stages"].get("export", {}).get("status") == SUCCEEDED]
