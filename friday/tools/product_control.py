"""
MCP adapter for product processing.

Six tools, at the level the model should be operating at. Deliberately NOT
`product_normalize_row`, `product_run_classifier`, `product_dedupe_internal`
and six more: the pipeline has nine stages, and exposing them individually
would invite the model to orchestrate them by hand - badly, differently each
time, and without the graph's skip semantics. It asks for a catalogue to be
processed; the DAG decides the order.

Transport only. The run resolution, the summary shaping, the outcome filters
and the export writer moved to `friday/toolsets/products.py`, because all of
it was domain work that happened to be written inside `@mcp.tool()` functions
- which meant six registered capabilities that a durable objective could not
reach. The pipeline itself was never here and is untouched.

The docstrings stay. They are the model-facing contract and each paragraph in
them is a measured failure: the run that was reported as "underway" after it
finished, the "no products actually failed" said while holding a list of
failures, the `only="failed"` that FastMCP silently dropped.

`run_id` is the continuity handle and Friday's database is the state
authority, so a run survives the model losing the thread, the server
restarting, and the boss asking about it tomorrow from a new conversation.
"""

from __future__ import annotations

import os

from friday import contracts as c
from friday.policy import PolicyEngine, PolicyError
from friday.toolsets import products as PT
from friday.toolsets.products import (
    ExportResult, RunList, RunResult, RunStatus, RunSummary, default_pipeline,
    reset_store, store,
)

global _engine
_engine: PolicyEngine | None = None

__all__ = [
    'register',
    'store',
    'reset_store',
    'default_pipeline',
    'RunSummary',
    'RunStatus',
    'RunList',
    'RunResult',
    'ExportResult',
]


def _get_engine() -> PolicyEngine:
    global _engine
    if _engine is None:
        _engine = PolicyEngine()
        for tool_id in (t.strip() for t in
                        os.getenv("ADA_PREAPPROVED_TOOLS", "").split(",") if t.strip()):
            try:
                _engine.approve_for_session(tool_id)
            except PolicyError:
                continue
    return _engine


def _payload(result: c.ActionResult) -> dict:
    """
    The dict the ActionResult carries, which is what these tools return.

    Every product capability puts its full payload in `output` even when it
    refuses, so the `error` field the model reads is always in the shape it
    expects rather than an empty dict where a RunSummary belongs.
    """
    return dict(result.output or {})


def _call(request: str, fn, *args, **kwargs) -> dict:
    run = c.Run.create(request, capability="product")
    return _payload(fn(run, *args, engine=_get_engine(), **kwargs))


def register(mcp):

    @mcp.tool()
    def product_process(path: str, source: str = "") -> RunSummary:
        """
        Process a product catalogue - a CSV or JSON file - end to end.

        This runs the whole catalogue before it returns. `execution_state`
        says whether it is over and `outcome` says how it went, because one
        field answering both produced "processing is underway, two done so
        far" about a run that had finished. COMPLETED with outcome PARTIAL
        means it is over and some rows did not work. Never report a
        COMPLETED run as "underway".

        The run_id is the handle for product_status and product_result.
        Friday's database holds the run, so it survives a restart and can be
        asked about tomorrow.

        A batch is rarely all-or-nothing, and the numbers say so separately:
        `input_rows` is what arrived, `canonical_products` is what there were
        once identical rows were collapsed, and `processed` counts only the
        products that actually went through the pipeline. Report those
        distinctly - "processed 41" is not true when one of them was a
        collapsed duplicate.

        Rows that fail are not errors here. A status of PARTIAL means the run
        worked and some rows did not; ask product_result which ones.

        The file must sit inside the permitted file roots, same as files_read.
        """
        return _call(f"process {path}", PT.product_process, path, source)

    @mcp.tool()
    def product_status(run_id: str = "", about: str = "") -> RunStatus:
        """
        How a catalogue run is doing. Answers "how did that job finish?".

        `run_id` is optional and usually unknown - leave it empty and Friday
        resolves which run was meant, saying which in `resolved_by`. `about`
        narrows it when the boss named something: a file name, part of an id.
        Never ask him for a run id; he cannot know one Friday invented.

        Reads the database, so it works after a restart and in a conversation
        that never saw the run start. It never reprocesses anything.
        """
        return _call("how did that run go", PT.product_status, run_id, about)

    @mcp.tool()
    def product_result(run_id: str = "", outcome: str = "",
                       needs_attention: bool | None = None,
                       retryable: bool | None = None,
                       include_fields: bool = False,
                       about: str = "", only: str = "") -> RunResult:
        """
        What a run produced, per product.

        Filters, which combine (all of them must hold):

          outcome           exactly one of SUCCEEDED, PARTIAL, FAILED,
                            QUARANTINED. Literal and strict - FAILED means the
                            status FAILED and nothing else.
          needs_attention   true for every product that did not come out
                            clean, whichever way it went wrong. **This is what
                            "which products failed?" means.** Asked in
                            English, "failed" covers quarantined and partial,
                            and a run can plainly have gone wrong with no
                            record carrying the literal status FAILED.
          retryable         true for products whose failure is worth another
                            attempt - a timeout, a name that did not resolve -
                            as opposed to a price of "banana".

        `only="failed"` used to mean "needs_attention", which read well in one
        conversation and was the wrong place to put it: the machine meaning of
        a word must not depend on what an English speaker might have intended.
        Claude, the automations engine and forged skills are callers too. The
        translation from what he says to what these mean belongs above this
        line, not inside it.

        Use this to answer "which products failed?" - it reads the recorded
        run and must never be answered by processing the catalogue again.

        `include_fields` adds every product's field-level provenance - the
        value, where it came from and how it was derived. Off by default
        because it is large and is almost never what was asked; turn it on to
        answer questions about a specific product's data.

        Each failure names the stage that failed and why, so a retryable
        network problem is distinguishable from a row whose price was not a
        number.

        `failures` is final, not interim: the run is over by the time you can
        read it. `did_not_succeed` is the direct answer to "which products
        failed?" - anything not fully clean, whether quarantined, partly done
        or hard-failed. If it is non-empty, products failed. Saying "none
        failed" while holding it is a false report, and no product carrying
        the literal status FAILED is not the same thing as none failing.

        Answer with the keys. "One was quarantined and two were partial" is a
        tally, and the question was which.
        """
        return _call("what did that run produce", PT.product_result,
                     run_id, outcome, needs_attention, retryable,
                     include_fields, about, only)

    @mcp.tool()
    def product_retry(run_id: str = "", failure_class: str = "retryable",
                      about: str = "") -> RunSummary:
        """
        Re-run only the products that failed in a way worth retrying.

        `failure_class`: "retryable" (timeouts, model outages - the default),
        or "all" to include hard failures. Quarantined rows are never retried:
        a price of "banana" will still not be a number.

        Products that already succeeded are not touched, and the retry's
        evidence belongs to this run rather than being inherited from the
        attempt before it.

        `retried` lists what this call actually re-ran. Read it before
        speaking: an empty list means there was nothing to retry, and a
        non-empty one means those products were re-run - even if the run's
        counts are identical afterwards, which is what a retry that failed
        the same way looks like.
        """
        return _call("retry the failures", PT.product_retry,
                     run_id, failure_class, about)

    @mcp.tool()
    def product_runs(limit: int = 10) -> RunList:
        """
        Find a catalogue run when you do not have its id - newest first.

        That first line is the whole of what a capability search shows, so it
        says the thing the model needs at the moment it needs it. It used to
        read "Recent processing runs, newest first, with their run_ids", and
        asked "how did that catalogue job finish?" in a fresh conversation the
        model reached for product_status instead, then asked the boss for a
        run id it had no way of knowing. The id is right here.

        Do not say there is no record of a job until you have looked here.
        """
        return _call("what runs are there", PT.product_runs, limit)

    @mcp.tool()
    def product_export(run_id: str = "", path: str = "",
                       about: str = "") -> ExportResult:
        """
        Write a run's finished products to a CSV.

        Only products this run actually completed are written, and the file
        records which run produced it. Exporting a run that produced nothing
        fails rather than writing an empty file that looks like a result.
        """
        return _call("export that run", PT.product_export,
                     run_id, path, about)
