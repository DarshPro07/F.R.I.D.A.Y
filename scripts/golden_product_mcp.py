#!/usr/bin/env python3
"""
Product processing through MCP, as the model would actually use it.

The pipeline gate proves the pipeline. This proves the *integration*: that a
run_id survives the process that created it, that "which products failed?" is
answered by reading a recorded run rather than by doing the work again, and
that a retry does not duplicate what already succeeded.

The restart is real - the last section runs in a separate interpreter with no
memory of anything, and has to find the run in the database.

    python scripts/golden_product_mcp.py
"""

from __future__ import annotations

import asyncio
import csv
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from mcp.server.fastmcp import FastMCP  # noqa: E402

from friday.store import Store  # noqa: E402
from friday.tools import product_control  # noqa: E402

DB = ROOT / "data" / "product_mcp_gate.sqlite3"

CATALOGUE = [
    {"sku": "T-100", "title": "Blue Cotton Shirt", "price": "29.99",
     "image": "https://example.com/1.jpg", "description": "A cotton shirt."},
    {"sku": "T-101", "title": "Leather Boots", "price": "89.00",
     "image": "https://example.com/2.jpg", "description": "Leather boots."},
    {"sku": "T-102", "title": "Wool Scarf", "price": "19.50",
     "image": "", "description": "A wool scarf."},              # no image
    {"sku": "T-103", "title": "Denim Jacket", "price": "banana",
     "image": "https://example.com/4.jpg"},                      # bad price
    {"sku": "T-104", "title": "Silk Tie", "price": "15.00",
     "image": "http://169.254.169.254/latest/meta-data/"},       # ssrf
    {"sku": "T-105", "title": "Canvas Bag", "price": "22.00",
     "image": "https://example.com/6.jpg"},
    {"sku": "T-105", "title": "Canvas Bag", "price": "22.00",
     "image": "https://example.com/6.jpg"},                      # exact dup
    {"sku": "T-106", "title": "Linen Hat", "price": "12.00",
     "image": "https://host-that-does-not-resolve.invalid/7.jpg"},  # retryable
]


def check(passed: bool, message: str, detail: str = "") -> bool:
    print(f"  [{'PASS' if passed else 'FAIL'}] {message}")
    if detail:
        print(f"         {detail}")
    return passed


def build_server() -> FastMCP:
    server = FastMCP(name="product-gate")
    product_control.register(server)
    return server


async def call(server, name: str, **arguments):
    """Call a tool the way the model does, and read the structured half."""
    content, structured = await server.call_tool(name, arguments)
    return structured


def write_catalogue() -> Path:
    # Inside the project, not in %TEMP%: the catalogue path is chosen by the
    # model, so product_process puts it through the same jail as files_read,
    # and %TEMP% is not one of the permitted roots.
    path = ROOT / "data" / "gate" / "products.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["sku", "title", "price", "image", "description"])
        writer.writeheader()
        for row in CATALOGUE:
            writer.writerow({k: row.get(k, "") for k in writer.fieldnames})
    return path


RESTART = r"""
import asyncio, json, sys
sys.path.insert(0, r"{root}")
from mcp.server.fastmcp import FastMCP
from friday.store import Store
from friday.tools import product_control

product_control.reset_store(Store(r"{db}"))
server = FastMCP(name="restarted")
product_control.register(server)

async def main():
    _, status = await server.call_tool("product_status", {{"run_id": "{run_id}"}})
    _, result = await server.call_tool("product_result",
                                       {{"run_id": "{run_id}", "only": "partial"}})
    print(json.dumps({{"status": status, "partial": len(result["products"])}}))

asyncio.run(main())
"""


async def main() -> int:
    DB.parent.mkdir(parents=True, exist_ok=True)
    DB.unlink(missing_ok=True)
    product_control.reset_store(Store(DB))
    server = build_server()
    results: list[bool] = []

    catalogue = write_catalogue()
    print("=" * 70)
    print('"Process this catalogue."')
    print("=" * 70)

    tools = {t.name: t for t in await server.list_tools()}
    print(f"  tools exposed : {sorted(n for n in tools if n.startswith('product_'))}")
    results.append(check(
        all(tools[n].outputSchema for n in tools if n.startswith("product_")),
        "every product tool advertises an output schema"))
    results.append(check(
        not any(n.startswith("product_normalize") or n.startswith("product_stage")
                for n in tools),
        "no per-stage tools leaked into the model's surface"))

    summary = await call(server, "product_process", path=str(catalogue))
    print(json.dumps(summary, indent=2)[:700])
    run_id = summary["run_id"]

    results.append(check(bool(run_id), "a run_id came back"))
    results.append(check(summary["execution_state"] == "COMPLETED"
                         and summary["outcome"] == "PARTIAL",
                         "COMPLETED/PARTIAL - it ran to the end, and some "
                         "rows did not work"))
    results.append(check(summary["input_rows"] == 8,
                         f"input_rows counts the file: {summary['input_rows']}"))
    results.append(check(summary["canonical_products"] == 7,
                         f"canonical_products excludes the duplicate: "
                         f"{summary['canonical_products']}"))
    results.append(check(summary["deduplicated"] == 1,
                         "the collapsed row is reported as deduplicated, "
                         "not as processed"))
    results.append(check(
        summary["processed"] + summary["partial"] + summary["quarantined"]
        + summary["failed"] == summary["canonical_products"],
        "the outcome counts add up to the canonical products"))
    results.append(check(not summary["error"],
                         "a partly-failed batch is a result, not an error"))

    # --- "Which products failed?" -----------------------------------------
    print("\n" + "=" * 70)
    print('"Which products failed?"')
    print("=" * 70)

    before = Store(DB).product_records(run_id)
    result = await call(server, "product_result", run_id=run_id)
    after = Store(DB).product_records(run_id)

    for failure in result["failures"]:
        print(f"    {failure['product_key']:<8} {failure['stage']:<14} "
              f"{failure['execution_state']:<12} {failure['outcome']:<12} "
              f"retryable={failure['retryable']}")
    results.append(check(bool(result["failures"]), "it can say which and why"))
    results.append(check(
        [r["output_hash"] for r in before] == [r["output_hash"] for r in after],
        "asking read the run - it did not reprocess the catalogue"))
    results.append(check(
        any(f["product_key"] == "T-104" and "metadata" in (f["error"] or "")
            for f in result["failures"]),
        "the metadata-endpoint row is named with its reason"))
    quarantined = await call(server, "product_result", run_id=run_id,
                             only="quarantined")
    results.append(check(
        any(p["product_key"] == "T-103" for p in quarantined["products"]),
        "the unparseable price is quarantined, filterable on its own"))

    # --- retry -------------------------------------------------------------
    print("\n" + "=" * 70)
    print('"Retry only the network failures."')
    print("=" * 70)

    before_rows = {r["product_key"]: r for r in Store(DB).product_records(run_id)}
    succeeded_before = {k for k, r in before_rows.items()
                        if r["status"] == "SUCCEEDED"}
    retryable = [k for k, r in before_rows.items()
                 if any(s.get("status") == "FAILED_RETRYABLE"
                        for s in (r["stages"] or {}).values())]
    print(f"  retryable products : {retryable}")

    # Without this the next three checks pass vacuously: product_retry returns
    # early when nothing is retryable, and "nothing broke" is not evidence
    # that the retry path works.
    results.append(check(bool(retryable),
                         "there is genuinely something to retry",
                         "otherwise the checks below prove nothing"))

    retried = await call(server, "product_retry", run_id=run_id,
                         failure_class="retryable")
    records_after = Store(DB).product_records(run_id)
    after_rows = {r["product_key"]: r for r in records_after}
    keys = [r["product_key"] for r in records_after]

    print(f"  after retry : {retried['execution_state']} / {retried['outcome']}")
    # Compare the write timestamp, not the stage dict. A retry that fails the
    # same way produces an identical dict - same status, same attempts, same
    # idempotency key - so "the dict changed" would demand that the retry
    # *succeed* in order to count as having run. What is being proved here is
    # that it ran.
    results.append(check(
        all(after_rows[k]["at"] > before_rows[k]["at"] for k in retryable),
        "the retried products were re-written by this retry",
        f"{[(k, before_rows[k]['at'], after_rows[k]['at']) for k in retryable]}"))
    results.append(check(
        all(after_rows[k]["source_row"] for k in retryable),
        "the retry re-ran the original row, not an empty one"))
    results.append(check(len(keys) == len(set(keys)),
                         "no product was duplicated by the retry"))
    results.append(check(
        succeeded_before <= {r["product_key"] for r in records_after
                             if r["status"] == "SUCCEEDED"},
        "products that already succeeded were not lost"))
    results.append(check(retried["run_id"] == run_id,
                         "the retry belongs to the same run"))

    # --- export ------------------------------------------------------------
    print("\n" + "=" * 70)
    print("EXPORT")
    print("=" * 70)
    export = await call(server, "product_export", run_id=run_id)
    print(f"  {export['rows']} row(s) -> {export['path']}")
    results.append(check(export["rows"] > 0 and Path(export["path"]).is_file(),
                         "an export exists on disk"))
    results.append(check(
        (await call(server, "product_export", run_id="RUN-nothing"))["error"] != "",
        "exporting a run that does not exist fails rather than writing nothing"))

    # --- restart -----------------------------------------------------------
    print("\n" + "=" * 70)
    print('RESTART - "How did that catalogue job finish?"')
    print("=" * 70)

    completed = subprocess.run(
        [sys.executable, "-c",
         RESTART.format(root=str(ROOT), db=str(DB), run_id=run_id)],
        capture_output=True, text=True, timeout=300)
    if completed.returncode != 0:
        results.append(check(False, "a fresh process could read the run",
                             completed.stderr[-400:]))
    else:
        reloaded = json.loads(completed.stdout.strip().splitlines()[-1])
        print(json.dumps(reloaded, indent=2)[:400])
        results.append(check(reloaded["status"]["run_id"] == run_id,
                             "a process with no memory found the run by id"))
        results.append(check(
            reloaded["status"]["execution_state"] == "COMPLETED",
            "and knows it finished"))
        results.append(check(reloaded["partial"] >= 1,
                             "and can still say which products were partial"))

    print("\n" + "=" * 70)
    passed = sum(1 for r in results if r)
    print(f"RESULT: {passed}/{len(results)} checks behaved correctly")
    print("=" * 70)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
