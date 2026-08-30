"""
The eight failure journeys.

A product pipeline is only worth anything on the bad day, so the tests are
organised by what goes wrong rather than by which function is being called. In
every one of them the question is the same: does the failure stay the size it
actually is, or does it grow to swallow the batch?

    1  bad row                one row invalid           others continue
    2  missing image          image branch fails        text branch continues
    3  invalid url            refused before fetching   no SSRF surface
    4  duplicate sku          exact vs conflicting      no silent overwrite
    5  timeout                retried, then retryable   independents continue
    6  llm failure            content unavailable       data still exportable
    7  crash mid-batch        resume, same run_id       nothing done twice
    8  stale evidence         a previous run's export   never counts as this one's
"""

from __future__ import annotations

import pytest

from friday import products as P
from friday.store import Store

# ---------------------------------------------------------------------------
# A pipeline shaped like the real one
# ---------------------------------------------------------------------------


def validate(record, context):
    row = record.source_row
    if not str(row.get("sku", "")).strip():
        raise P.Quarantine("no sku")
    try:
        price = float(str(row.get("price", "")).replace("$", ""))
    except ValueError as exc:
        raise P.Quarantine(f"price {row.get('price')!r} is not a number") from exc
    record.set("sku", row["sku"], source="source.sku", method=P.DIRECT)
    record.set("price", price, source="source.price", method=P.DIRECT)


def normalize(record, context):
    title = " ".join(str(record.source_row.get("title", "")).split()).title()
    record.set("title", title, source="source.title", method=P.DERIVED)


def images(record, context):
    url = str(record.source_row.get("image", "")).strip()
    if not url:
        raise ValueError("no image url on this row")
    record.set("image", P.safe_url(url), source="source.image", method=P.DIRECT)


def process_image(record, context):
    record.set("thumbnail", record.value("image") + "?w=200",
               source="image", method=P.DERIVED)


def enrich(record, context):
    if context.get("enrich_timeout"):
        raise P.Retryable("the enrichment service timed out")
    record.set("material", "cotton", source="product_description",
               method=P.INFERRED, confidence=0.91)


def classify(record, context):
    record.set("category", "apparel", source=["title", "material"],
               method=P.INFERRED, confidence=0.84)


def generate(record, context):
    if context.get("llm_down"):
        raise P.Retryable("the model is unavailable")
    record.set("seo_title", f"{record.value('title')} - buy now",
               source=["title", "category"], method=P.GENERATED)


def export(record, context):
    context.setdefault("exported", []).append(record.product_key)


def build_pipeline() -> P.Pipeline:
    """
    The graph. `generate` needs the text branch only, so a missing image costs
    the image branch and the export - and nothing else.
    """
    return P.Pipeline([
        P.Stage("validate", validate),
        P.Stage("normalize", normalize, needs=("validate",)),
        P.Stage("images", images, needs=("normalize",)),
        P.Stage("process_image", process_image, needs=("images",)),
        P.Stage("enrich", enrich, needs=("normalize",), retries=1),
        P.Stage("classify", classify, needs=("enrich",)),
        P.Stage("generate", generate, needs=("classify",), retries=1),
        P.Stage("export", export, needs=("generate", "process_image")),
    ])


ROW = {"sku": "A-1", "title": "  blue   shirt ", "price": "19.99",
       "image": "https://example.com/a.jpg"}


@pytest.fixture
def store(tmp_path):
    fresh = Store(tmp_path / "p.db")
    yield fresh
    fresh.close()


@pytest.fixture
def batch(store):
    return P.Batch(build_pipeline(), store, source="test.csv")


def records(rows, run_id) -> list[P.ProductRecord]:
    return [P.ProductRecord(product_key=P.product_key(row) or f"row-{i}",
                            source_row=row, run_id=run_id)
            for i, row in enumerate(rows)]


def run(batch, rows, context=None, **kwargs) -> dict:
    made = records(rows, batch.run_id)
    batch.start(len(made))
    return batch.process(made, context or {}, **kwargs)


# ---------------------------------------------------------------------------
# It has to work on the good day first
# ---------------------------------------------------------------------------


def test_a_clean_row_goes_all_the_way_through(batch, store):
    summary = run(batch, [ROW])
    assert summary["status"] == P.SUCCEEDED
    row = store.product_records(batch.run_id)[0]
    assert row["status"] == P.SUCCEEDED
    assert row["fields"]["seo_title"]["value"] == "Blue Shirt - buy now"


def test_every_generated_field_says_where_it_came_from(batch, store):
    run(batch, [ROW])
    fields = store.product_records(batch.run_id)[0]["fields"]

    assert fields["sku"]["method"] == P.DIRECT
    assert fields["sku"]["source"] == "source.sku"
    assert fields["material"]["method"] == P.INFERRED
    assert fields["material"]["confidence"] == 0.91
    assert fields["seo_title"]["method"] == P.GENERATED
    assert fields["seo_title"]["source"] == ["title", "category"]


def test_a_direct_field_carries_no_invented_confidence(batch, store):
    """Confidence on a copied value would be theatre."""
    run(batch, [ROW])
    assert "confidence" not in store.product_records(batch.run_id)[0]["fields"]["sku"]


# ---------------------------------------------------------------------------
# 1. A bad row
# ---------------------------------------------------------------------------


def test_a_bad_row_is_quarantined_and_the_others_continue(batch, store):
    rows = [ROW,
            {**ROW, "sku": "B-2", "price": "banana"},
            {**ROW, "sku": "C-3"}]
    summary = run(batch, rows)

    by_key = {r["product_key"]: r for r in store.product_records(batch.run_id)}
    assert by_key["B-2"]["status"] == P.QUARANTINED
    assert "banana" in by_key["B-2"]["quarantine_reason"]
    assert by_key["A-1"]["status"] == P.SUCCEEDED
    assert by_key["C-3"]["status"] == P.SUCCEEDED
    assert summary["status"] == P.PARTIAL, "one bad row must not fail the batch"


def test_a_row_with_no_sku_is_quarantined_not_guessed(batch, store):
    run(batch, [{**ROW, "sku": ""}])
    assert store.product_records(batch.run_id)[0]["status"] == P.QUARANTINED


def test_a_quarantined_row_stops_at_the_stage_that_refused_it(batch, store):
    run(batch, [{**ROW, "price": "banana"}])
    stages = store.product_records(batch.run_id)[0]["stages"]
    assert stages["validate"]["status"] == P.QUARANTINED
    assert "normalize" not in stages, "it kept processing an invalid record"


# ---------------------------------------------------------------------------
# 2. A missing image
# ---------------------------------------------------------------------------


def test_a_missing_image_costs_only_the_image_branch(batch, store):
    """The reason this is a graph. Text work must not wait on a picture."""
    run(batch, [{**ROW, "image": ""}])
    row = store.product_records(batch.run_id)[0]
    stages = row["stages"]

    assert stages["images"]["status"] == P.FAILED
    assert stages["process_image"]["status"] == "SKIPPED"
    assert stages["normalize"]["status"] == P.SUCCEEDED
    assert stages["enrich"]["status"] == P.SUCCEEDED
    assert stages["classify"]["status"] == P.SUCCEEDED
    assert stages["generate"]["status"] == P.SUCCEEDED
    assert row["status"] == P.PARTIAL, "usable data, unfinished product"


def test_a_missing_image_does_prevent_the_export_that_needs_it(batch, store):
    run(batch, [{**ROW, "image": ""}])
    stages = store.product_records(batch.run_id)[0]["stages"]
    assert stages["export"]["status"] == "SKIPPED"
    assert "process_image" in stages["export"]["error"]


def test_the_skipped_stage_says_what_it_was_waiting_for(batch, store):
    run(batch, [{**ROW, "image": ""}])
    stages = store.product_records(batch.run_id)[0]["stages"]
    assert "images" in stages["process_image"]["error"]


# ---------------------------------------------------------------------------
# 3. An invalid URL
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("url,reason", [
    ("file:///c:/windows/win.ini", "scheme"),
    ("ftp://example.com/a.jpg", "scheme"),
    ("http://localhost/a.jpg", "this machine"),
    ("http://127.0.0.1/a.jpg", "loopback"),
    ("http://[::1]/a.jpg", "loopback"),
    ("http://169.254.169.254/latest/meta-data/", "metadata"),
    ("http://10.0.0.5/a.jpg", "private"),
    ("http://192.168.1.1/admin", "private"),
    ("http://172.16.0.1/a.jpg", "private"),
    ("http://0.0.0.0/a.jpg", "0.0.0.0"),
])
def test_a_url_from_a_spreadsheet_is_untrusted_input(url, reason):
    """
    169.254.169.254 is the cloud metadata endpoint. A feed row is exactly as
    untrusted as a prompt, and a plausible-looking field is not evidence.
    """
    with pytest.raises(P.ProductError, match=reason):
        P.safe_url(url)


def test_an_ordinary_url_is_allowed():
    assert P.safe_url("https://example.com/a.jpg") == "https://example.com/a.jpg"


def test_a_private_url_can_be_permitted_explicitly():
    """Refused by default, allowed when someone decides so - never silently."""
    assert P.safe_url("http://10.0.0.5/a.jpg", allow_private=True)


def test_an_unresolvable_host_is_not_treated_as_hostile():
    """Refusing here would make row validation depend on DNS being reachable."""
    assert P.safe_url("https://nonexistent.invalid/a.jpg")


def test_a_bad_url_fails_only_the_image_branch(batch, store):
    run(batch, [{**ROW, "image": "http://169.254.169.254/latest/meta-data/"}])
    row = store.product_records(batch.run_id)[0]
    assert row["stages"]["images"]["status"] == P.FAILED
    assert row["stages"]["generate"]["status"] == P.SUCCEEDED
    assert row["status"] == P.PARTIAL


# ---------------------------------------------------------------------------
# 4. A duplicate SKU
# ---------------------------------------------------------------------------


def test_an_exact_duplicate_is_deduplicated(batch):
    made = records([ROW, dict(ROW)], batch.run_id)
    verdict = P.classify_duplicates(made)
    assert len(verdict["unique"]) == 1
    assert verdict["exact"][0]["copies"] == 2
    assert verdict["conflicts"] == []


def test_a_conflicting_duplicate_is_never_silently_merged(batch):
    """
    Same key, different content. Keeping whichever arrived last is a decision
    nobody made and nobody can see afterwards.
    """
    made = records([ROW, {**ROW, "price": "29.99"}], batch.run_id)
    verdict = P.classify_duplicates(made)

    assert verdict["exact"] == []
    assert verdict["conflicts"][0]["product_key"] == "A-1"
    assert len(verdict["conflicts"][0]["input_hashes"]) == 2
    assert all(r.status == P.QUARANTINED for r in verdict["unique"])


def test_a_collapsed_duplicate_is_not_counted_as_a_processed_product(batch, store):
    """
    "I successfully processed 41 products" was true of 40 products and one
    row that was correctly collapsed. Both are fine outcomes; only one of them
    was generated, priced and exported, and one word was carrying both.
    """
    made = records([ROW, dict(ROW), {**ROW, "sku": "B-2"}], batch.run_id)
    verdict = P.classify_duplicates(made)
    batch.start(len(verdict["unique"]))
    batch.process(verdict["unique"], {"_duplicates": verdict})
    summary = batch.summarise(input_rows=3, duplicates=verdict)

    assert summary["input_rows"] == 3
    assert summary["canonical_products"] == 2
    assert summary["outcomes"][P.PROCESSED] == 2
    assert summary["outcomes"][P.DEDUPLICATED] == 1
    assert summary["duplicates_exact"] == 1


def test_distinct_products_are_left_alone(batch):
    made = records([ROW, {**ROW, "sku": "B-2"}], batch.run_id)
    verdict = P.classify_duplicates(made)
    assert len(verdict["unique"]) == 2
    assert not verdict["exact"] and not verdict["conflicts"]


def test_a_conflicting_duplicate_is_recorded_rather_than_dropped(batch, store):
    made = records([ROW, {**ROW, "price": "29.99"}], batch.run_id)
    verdict = P.classify_duplicates(made)
    batch.start(len(verdict["unique"]))
    batch.process(verdict["unique"], {})

    rows = store.product_records(batch.run_id)
    assert rows and all(r["status"] == P.QUARANTINED for r in rows)
    assert "conflicting" in rows[0]["quarantine_reason"]


# ---------------------------------------------------------------------------
# 5. A timeout
# ---------------------------------------------------------------------------


def test_a_timeout_is_retried_up_to_its_cap_then_marked_retryable(batch, store):
    run(batch, [ROW], {"enrich_timeout": True})
    stage = store.product_records(batch.run_id)[0]["stages"]["enrich"]

    assert stage["status"] == P.FAILED_RETRYABLE
    assert stage["attempts"] == 2, "retries must stop at the declared number"
    assert "timed out" in stage["error"]


def test_a_timeout_does_not_stop_an_independent_branch(batch, store):
    run(batch, [ROW], {"enrich_timeout": True})
    stages = store.product_records(batch.run_id)[0]["stages"]
    assert stages["images"]["status"] == P.SUCCEEDED
    assert stages["process_image"]["status"] == P.SUCCEEDED
    assert stages["classify"]["status"] == "SKIPPED"


def test_retries_are_capped_at_definition_time():
    with pytest.raises(P.ProductError, match="retries"):
        P.Pipeline([P.Stage("x", lambda r, c: None, retries=99)])


# ---------------------------------------------------------------------------
# 6. The model falls over
# ---------------------------------------------------------------------------


def test_content_generation_failing_is_not_ingestion_failing(batch, store):
    """
    The distinction that matters commercially: the structured catalogue is
    still correct and still exportable, and only the copy is missing.
    """
    run(batch, [ROW], {"llm_down": True})
    row = store.product_records(batch.run_id)[0]

    assert row["status"] == P.PARTIAL
    assert row["stages"]["generate"]["status"] == P.FAILED_RETRYABLE
    assert row["fields"]["price"]["value"] == 19.99
    assert row["fields"]["category"]["value"] == "apparel"
    assert "seo_title" not in row["fields"]


def test_a_batch_where_the_model_is_down_is_partial_not_failed(batch):
    summary = run(batch, [ROW, {**ROW, "sku": "B-2"}], {"llm_down": True})
    assert summary["status"] == P.PARTIAL
    assert summary["counts"][P.PARTIAL] == 2
    assert summary["counts"][P.FAILED] == 0


# ---------------------------------------------------------------------------
# 7. A crash in the middle
# ---------------------------------------------------------------------------


def test_a_batch_resumes_without_redoing_what_it_finished(store):
    """100 records, killed after 47. The 47 are not produced twice."""
    rows = [{**ROW, "sku": f"S-{i:03d}"} for i in range(100)]
    first = P.Batch(build_pipeline(), store, source="big.csv")
    first.start(len(rows))
    first.process(records(rows[:47], first.run_id), {})
    assert len(store.product_records(first.run_id)) == 47

    resumed = P.Batch(build_pipeline(), store, run_id=first.run_id,
                      source="big.csv")
    summary = resumed.process(records(rows, first.run_id), {})

    assert resumed.run_id == first.run_id, "a resumed batch is the same run"
    assert summary["resumed_skipped"] == 47
    assert len(store.product_records(first.run_id)) == 100
    keys = [r["product_key"] for r in store.product_records(first.run_id)]
    assert len(keys) == len(set(keys)), "a record was processed twice"


def test_resume_can_be_turned_off_deliberately(store):
    rows = [ROW]
    first = P.Batch(build_pipeline(), store)
    first.start(1)
    first.process(records(rows, first.run_id), {})
    again = P.Batch(build_pipeline(), store, run_id=first.run_id)
    assert again.process(records(rows, first.run_id), {},
                         resume=False)["resumed_skipped"] == 0


def test_the_idempotency_key_distinguishes_the_same_work_from_changed_work(store):
    """run_id + product_key + stage + input_hash + schema version."""
    unchanged = P.ProductRecord("A-1", ROW, "RUN-1")
    same = P.ProductRecord("A-1", dict(ROW), "RUN-1")
    changed = P.ProductRecord("A-1", {**ROW, "price": "29.99"}, "RUN-1")
    other_run = P.ProductRecord("A-1", ROW, "RUN-2")

    assert unchanged.key_for("enrich") == same.key_for("enrich")
    assert unchanged.key_for("enrich") != changed.key_for("enrich")
    assert unchanged.key_for("enrich") != unchanged.key_for("classify")
    assert unchanged.key_for("enrich") != other_run.key_for("enrich")


# ---------------------------------------------------------------------------
# 8. Stale evidence
# ---------------------------------------------------------------------------


def test_an_earlier_runs_export_is_never_this_runs_proof(store):
    """
    The invariant that a real gate defect taught: RUN-A exports successfully,
    RUN-B fails before export. A verifier asking "does an export exist" finds
    RUN-A's and calls RUN-B a success.
    """
    good = P.Batch(build_pipeline(), store, source="feed.csv")
    good.start(1)
    good.process(records([ROW], good.run_id), {})
    assert len(good.exports()) == 1

    bad = P.Batch(build_pipeline(), store, source="feed.csv")
    bad.start(1)
    bad.process(records([ROW], bad.run_id), {"llm_down": True})

    assert bad.exports() == [], "it inherited the earlier run's export"
    assert len(good.exports()) == 1, "the earlier run's evidence was disturbed"
    assert bad.run_id != good.run_id


def test_two_runs_over_the_same_catalogue_keep_separate_evidence(store):
    first = P.Batch(build_pipeline(), store)
    first.start(1)
    first.process(records([ROW], first.run_id), {})
    second = P.Batch(build_pipeline(), store)
    second.start(1)
    second.process(records([ROW], second.run_id), {"llm_down": True})

    assert store.product_records(first.run_id)[0]["status"] == P.SUCCEEDED
    assert store.product_records(second.run_id)[0]["status"] == P.PARTIAL


def test_a_resumed_run_does_not_count_a_different_runs_records(store):
    other = P.Batch(build_pipeline(), store)
    other.start(1)
    other.process(records([ROW], other.run_id), {})

    fresh = P.Batch(build_pipeline(), store)
    fresh.start(1)
    assert fresh.done_keys() == set(), "it saw another run's completed work"


# ---------------------------------------------------------------------------
# The graph itself
# ---------------------------------------------------------------------------


def test_a_cycle_is_refused_at_definition_time():
    with pytest.raises(P.ProductError, match="cycle"):
        P.Pipeline([P.Stage("a", lambda r, c: None, needs=("b",)),
                    P.Stage("b", lambda r, c: None, needs=("a",))])


def test_a_stage_needing_something_nonexistent_is_refused():
    with pytest.raises(P.ProductError, match="does not exist"):
        P.Pipeline([P.Stage("a", lambda r, c: None, needs=("ghost",))])


def test_the_order_is_reproducible():
    """Two runs whose stage order differs cannot be compared."""
    assert build_pipeline().order == build_pipeline().order


def test_ingest_reads_csv_and_json():
    csv_rows = P.ingest_csv("sku,price\nA-1,10\nB-2,20\n")
    assert [r["sku"] for r in csv_rows] == ["A-1", "B-2"]
    assert P.ingest_json('{"products":[{"sku":"A-1"}]}')[0]["sku"] == "A-1"
    assert P.ingest_json('[{"sku":"A-1"}]')[0]["sku"] == "A-1"


def test_empty_input_fails_rather_than_producing_an_empty_run():
    with pytest.raises(P.ProductError):
        P.ingest_csv("sku,price\n")
    with pytest.raises(P.ProductError):
        P.ingest_json("[]")
