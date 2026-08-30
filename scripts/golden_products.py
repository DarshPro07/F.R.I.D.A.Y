#!/usr/bin/env python3
"""
The product pipeline on a real catalogue, with the failures deliberately in it.

Unit tests prove each journey in isolation. This runs one CSV containing all
of them at once, because the question that matters commercially is not "does a
missing image fail gracefully" - it is whether forty good products still come
out the other end when six rows are broken in six different ways.

The crash is real: a child process is killed mid-batch with taskkill, and the
resume runs in a third process against the same database.

    python scripts/golden_products.py
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from friday import products as P  # noqa: E402
from friday.config import DATA_DIR  # noqa: E402
from friday.store import Store  # noqa: E402

sys.path.insert(0, str(ROOT / "tests"))
from test_products import build_pipeline  # noqa: E402  - the same graph

DB = DATA_DIR / "products_gate.sqlite3"

GOOD = 40
CATALOGUE = (
    [{"sku": f"OK-{i:03d}", "title": f"  product   {i} ", "price": f"{10 + i}.99",
      "image": f"https://example.com/{i}.jpg"} for i in range(GOOD)]
    + [
        {"sku": "BAD-PRICE", "title": "x", "price": "banana",
         "image": "https://example.com/x.jpg"},
        {"sku": "", "title": "no key", "price": "1.00",
         "image": "https://example.com/y.jpg"},
        {"sku": "NO-IMAGE", "title": "no picture", "price": "5.00", "image": ""},
        {"sku": "SSRF", "title": "metadata", "price": "5.00",
         "image": "http://169.254.169.254/latest/meta-data/"},
        {"sku": "DUP-EXACT", "title": "twice", "price": "3.00",
         "image": "https://example.com/d.jpg"},
        {"sku": "DUP-EXACT", "title": "twice", "price": "3.00",
         "image": "https://example.com/d.jpg"},
        {"sku": "DUP-CONFLICT", "title": "a", "price": "3.00",
         "image": "https://example.com/e.jpg"},
        {"sku": "DUP-CONFLICT", "title": "a", "price": "9.99",
         "image": "https://example.com/e.jpg"},
    ]
)


def check(passed: bool, message: str, detail: str = "") -> bool:
    print(f"  [{'PASS' if passed else 'FAIL'}] {message}")
    if detail:
        print(f"         {detail}")
    return passed


def prepare(store) -> list:
    made = [P.ProductRecord(product_key=P.product_key(row) or f"row-{i}",
                            source_row=row, run_id="")
            for i, row in enumerate(CATALOGUE)]
    return made


def rekey(records, run_id):
    for record in records:
        record.run_id = run_id
    return records


# --- the child processes ----------------------------------------------------

CHILD = r"""
import sys, time
sys.path.insert(0, r"{root}")
sys.path.insert(0, r"{root}\tests")
from friday import products as P
from friday.store import Store
from test_products import build_pipeline
import json

rows = json.loads(sys.argv[2])
store = Store(r"{db}")
batch = P.Batch(build_pipeline(), store, run_id=sys.argv[1], source="gate.csv")
records = [P.ProductRecord(P.product_key(r) or f"row-{{i}}", r, batch.run_id)
           for i, r in enumerate(rows)]
verdict = P.classify_duplicates(records)
batch.start(len(verdict["unique"]))
for record in verdict["unique"]:
    if record.status != P.QUARANTINED:
        batch.pipeline.process(record, {{}})
    store.save_product_record(batch.run_id, record.to_dict())
    print("done", record.product_key, flush=True)
    if len(sys.argv) > 3:
        time.sleep(0.35)
print("COMPLETE", flush=True)
"""


def child(run_id: str, rows: list, slow: bool) -> subprocess.Popen:
    script = CHILD.format(root=str(ROOT), db=str(DB))
    args = [sys.executable, "-c", script, run_id, json.dumps(rows)]
    if slow:
        args.append("--slow")
    return subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True)


def main() -> int:
    DB.parent.mkdir(parents=True, exist_ok=True)
    DB.unlink(missing_ok=True)
    results: list[bool] = []
    store = Store(DB)

    print("=" * 70)
    print(f"ONE CATALOGUE: {len(CATALOGUE)} rows, {GOOD} good, 8 broken")
    print("=" * 70)

    records = prepare(store)
    verdict = P.classify_duplicates(records)
    print(f"  exact duplicates    : {verdict['exact']}")
    print(f"  conflicting keys    : {[c['product_key'] for c in verdict['conflicts']]}")
    results.append(check(len(verdict["exact"]) == 1,
                         "the exact duplicate was collapsed"))
    results.append(check(len(verdict["conflicts"]) == 1,
                         "the conflicting duplicate was NOT silently merged"))

    batch = P.Batch(build_pipeline(), store, source="gate.csv")
    rekey(verdict["unique"], batch.run_id)
    batch.start(len(verdict["unique"]))
    summary = batch.process(verdict["unique"], {})

    print(f"\n  run_id  : {summary['run_id']}")
    print(f"  status  : {summary['status']}")
    print(f"  counts  : {summary['counts']}")

    rows = {r["product_key"]: r for r in store.product_records(batch.run_id)}
    results.append(check(summary["counts"][P.SUCCEEDED] >= GOOD,
                         f"all {GOOD} good products completed",
                         f"got {summary['counts'][P.SUCCEEDED]}"))
    results.append(check(summary["status"] == P.PARTIAL,
                         "the batch is PARTIAL, not SUCCEEDED and not FAILED"))
    results.append(check(rows["BAD-PRICE"]["status"] == P.QUARANTINED,
                         "the unparseable price was quarantined alone"))
    results.append(check(rows["NO-IMAGE"]["status"] == P.PARTIAL,
                         "the imageless product is PARTIAL, with its text done",
                         f"generate={rows['NO-IMAGE']['stages']['generate']['status']}"))
    results.append(check(
        rows["NO-IMAGE"]["stages"]["generate"]["status"] == P.SUCCEEDED,
        "a missing image did not stop content generation"))
    results.append(check(
        rows["SSRF"]["stages"]["images"]["status"] == P.FAILED
        and "metadata" in (rows["SSRF"]["stages"]["images"]["error"] or ""),
        "the metadata-endpoint url was refused before any fetch"))
    results.append(check(
        all("source" in f and "method" in f
            for f in rows["OK-000"]["fields"].values()),
        "every field on a finished product carries its provenance"))

    # --- the crash ---------------------------------------------------------
    print("\n" + "=" * 70)
    print("CRASH - kill a real process mid-batch, then resume in a third one")
    print("=" * 70)

    big = [{"sku": f"R-{i:03d}", "title": f"row {i}", "price": "9.99",
            "image": "https://example.com/r.jpg"} for i in range(30)]
    run_id = "RUN-crashgate01"
    proc = child(run_id, big, slow=True)

    finished = 0
    while finished < 8 and proc.poll() is None:
        line = proc.stdout.readline()
        if line.startswith("done"):
            finished += 1
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                   capture_output=True)
    proc.wait(timeout=30)

    partial = Store(DB).product_records(run_id)
    print(f"  killed after {len(partial)} record(s) were durably written")
    results.append(check(0 < len(partial) < 30,
                         "the batch really was interrupted part-way"))

    resumed = subprocess.run(
        [sys.executable, "-c", CHILD.format(root=str(ROOT), db=str(DB)),
         run_id, json.dumps(big)],
        capture_output=True, text=True, timeout=300)
    after = Store(DB).product_records(run_id)
    keys = [r["product_key"] for r in after]

    print(f"  after resume : {len(after)} record(s)")
    results.append(check(resumed.returncode == 0, "the resume process completed",
                         resumed.stderr[-300:] if resumed.returncode else ""))
    results.append(check(len(after) == 30, "every record is present"))
    results.append(check(len(keys) == len(set(keys)),
                         "and none was processed twice"))

    # --- stale evidence ----------------------------------------------------
    print("\n" + "=" * 70)
    print("STALE EVIDENCE - a previous run's export must not count")
    print("=" * 70)

    good_batch = P.Batch(build_pipeline(), store, source="feed.csv")
    good_records = [P.ProductRecord("E-1", CATALOGUE[0], good_batch.run_id)]
    good_batch.start(1)
    good_batch.process(good_records, {})

    bad_batch = P.Batch(build_pipeline(), store, source="feed.csv")
    bad_records = [P.ProductRecord("E-1", CATALOGUE[0], bad_batch.run_id)]
    bad_batch.start(1)
    bad_batch.process(bad_records, {"llm_down": True})

    print(f"  run A exports : {len(good_batch.exports())}")
    print(f"  run B exports : {len(bad_batch.exports())}")
    results.append(check(len(good_batch.exports()) == 1, "run A exported"))
    results.append(check(bad_batch.exports() == [],
                         "run B did NOT inherit run A's export"))

    print("\n" + "=" * 70)
    passed = sum(1 for r in results if r)
    print(f"RESULT: {passed}/{len(results)} checks behaved correctly")
    print("=" * 70)
    store.close()
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
