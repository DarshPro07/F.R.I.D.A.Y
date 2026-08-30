"""
The MCP surface of product processing: the boundary, and the counting.

The pipeline itself is proved in test_products.py and the two golden gates.
What is proved here is the adapter - the part the model actually touches -
because two things about it are easy to get wrong in a way no pipeline test
would notice:

  the path    "process this catalogue" is a file read whose path the *model*
              chooses. Without the jail it is a read primitive pointed at any
              file on the disk, with the contents coming back as product
              fields.
  the counts  a collapsed duplicate is not a processed product, and a count
              that quietly includes work not done is an unverified claim
              arriving through arithmetic.

Nothing here touches the network: the catalogue rows use hosts that are
refused before any lookup, or none at all.
"""
from __future__ import annotations
import asyncio
import pytest
from mcp.server.fastmcp import FastMCP
from friday.fsjail import FileJail
from friday.store import Store
from friday.toolsets import files as F
from friday.tools import product_control
from friday.toolsets import products as PT
CATALOGUE = 'sku,title,price,image\nA-1,Cotton Shirt,10.00,\nA-2,Wool Scarf,12.00,\nA-2,Wool Scarf,12.00,\nA-3,Denim Jacket,banana,\n'


@pytest.fixture
def server(tmp_path):
    """A server whose store, and whose jail, are this test's alone."""
    product_control.reset_store(Store(tmp_path / "products.sqlite3"))
    F.reset_jail(FileJail(roots=(tmp_path,)))
    mcp = FastMCP(name="test")
    product_control.register(mcp)
    yield mcp
    product_control.reset_store(None)
    F.reset_jail(None)


def call(server, name, **arguments):
    _, structured = asyncio.run(server.call_tool(name, arguments))
    return structured


def catalogue_in(directory, text: str = CATALOGUE):
    path = directory / "products.csv"
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_a_catalogue_outside_the_roots_is_refused(server, tmp_path):
    outside = tmp_path.parent / "elsewhere.csv"
    outside.write_text(CATALOGUE, encoding="utf-8")
    result = call(server, "product_process", path=str(outside))
    assert result["outcome"] == "FAILED"
    assert "outside the permitted roots" in result["error"]
    assert result["input_rows"] == 0, "it read the file before refusing it"


def test_a_protected_file_inside_a_root_is_still_refused(server, tmp_path):
    """The roots are wide; the denylist is the part that protects anything."""
    secret = tmp_path / ".env"
    secret.write_text("sku,title\nGOOGLE_API_KEY,hunter2\n", encoding="utf-8")
    result = call(server, "product_process", path=str(secret))
    assert result["outcome"] == "FAILED"
    assert "protected pattern" in result["error"]


def test_a_refused_path_is_a_result_not_an_exception(server, tmp_path):
    """The model must be able to read the refusal, not see a dead transport."""
    result = call(server, "product_process", path=str(tmp_path / "nothing.csv"))
    assert result["error"]
    assert result["run_id"] == ""


def test_export_will_not_write_outside_the_roots(server, tmp_path):
    run_id = call(server, "product_process",
                  path=catalogue_in(tmp_path))["run_id"]
    escape = tmp_path.parent / "escaped.csv"
    result = call(server, "product_export", run_id=run_id, path=str(escape))
    assert "outside the permitted roots" in result["error"]
    assert not escape.exists()


def test_export_inside_the_roots_works(server, tmp_path):
    run_id = call(server, "product_process",
                  path=catalogue_in(tmp_path))["run_id"]
    wanted = tmp_path / "out" / "export.csv"
    result = call(server, "product_export", run_id=run_id, path=str(wanted))
    assert result["rows"] > 0
    assert wanted.is_file()


def test_a_duplicate_is_counted_as_deduplicated_not_as_processed(server, tmp_path):
    summary = call(server, "product_process", path=catalogue_in(tmp_path))
    assert summary["input_rows"] == 4
    assert summary["canonical_products"] == 3, "the identical row was not collapsed"
    assert summary["deduplicated"] == 1
    assert summary["processed"] < summary["input_rows"], (
        "a collapsed row was counted as a product that went through the pipeline")


def test_the_outcomes_account_for_every_canonical_product(server, tmp_path):
    summary = call(server, "product_process", path=catalogue_in(tmp_path))
    counted = (summary["processed"] + summary["partial"]
               + summary["quarantined"] + summary["failed"])
    assert counted == summary["canonical_products"]


def test_an_unparseable_price_is_quarantined_and_named(server, tmp_path):
    run_id = call(server, "product_process",
                  path=catalogue_in(tmp_path))["run_id"]
    result = call(server, "product_result", run_id=run_id, outcome="QUARANTINED")
    assert [p["product_key"] for p in result["products"]] == ["A-3"]
    assert "banana" in (result["products"][0]["quarantine_reason"] or "")


def test_reading_a_run_does_not_reprocess_it(server, tmp_path):
    run_id = call(server, "product_process",
                  path=catalogue_in(tmp_path))["run_id"]
    before = [r["output_hash"] for r in product_control.store().product_records(run_id)]
    call(server, "product_result", run_id=run_id)
    call(server, "product_status", run_id=run_id)
    after = [r["output_hash"] for r in product_control.store().product_records(run_id)]
    assert before == after


def test_a_returned_run_states_that_it_is_over(server, tmp_path):
    """
    Measured failure: handed `status: "PARTIAL"` and nothing else, the model
    told the boss "processing is underway, two done so far" - and on the next
    turn, holding six recorded failures, "no products have failed yet". The
    run had finished before the first sentence was spoken. PARTIAL means two
    different things in English, so completion is stated rather than implied.
    """
    summary = call(server, "product_process", path=catalogue_in(tmp_path))
    assert summary["execution_state"] == "COMPLETED"
    assert summary["outcome"] == "PARTIAL", "this catalogue has failing rows"
    result = call(server, "product_result", run_id=summary["run_id"])
    assert result["execution_state"] == "COMPLETED"


def test_which_products_failed_is_answered_without_inference(server, tmp_path):
    """
    A-3's price is "banana", so it is QUARANTINED - and no record in this run
    carries the literal status FAILED. Asked "which products failed?" over
    exactly that shape, the model answered "none did". True of the string,
    false of the question.
    """
    run_id = call(server, "product_process",
                  path=catalogue_in(tmp_path))["run_id"]
    result = call(server, "product_result", run_id=run_id)
    assert "A-3" in result["did_not_succeed"]
    assert not any(p["outcome"] == "FAILED" for p in result["products"]), \
        "this test is only meaningful while no record is literally FAILED"


def test_outcome_is_literal_and_needs_attention_is_the_wide_one(server, tmp_path):
    """
    Two filters because there are two questions, and one word cannot be both.

    `outcome="FAILED"` means the status FAILED - on this run, nothing, because
    A-3's price is "banana" and that is QUARANTINED. `needs_attention=True` is
    what "which products failed?" means in English, and it is a different
    query. The earlier design had `only="failed"` mean the second, which read
    well in one conversation and put a conversational meaning inside a machine
    API that Claude and the automations engine also call.
    """
    run_id = call(server, "product_process",
                  path=catalogue_in(tmp_path))["run_id"]

    strict = call(server, "product_result", run_id=run_id, outcome="FAILED")
    assert "A-3" not in strict["did_not_succeed"], "outcome stopped being literal"

    wide = call(server, "product_result", run_id=run_id, needs_attention=True)
    assert "A-3" in wide["did_not_succeed"]
    assert wide["products"], "the wide filter answers nothing"

    clean = call(server, "product_result", run_id=run_id, needs_attention=False)
    assert all(p["outcome"] == "SUCCEEDED" for p in clean["products"])


def test_the_filters_combine_rather_than_override(server, tmp_path):
    run_id = call(server, "product_process",
                  path=catalogue_in(tmp_path))["run_id"]
    both = call(server, "product_result", run_id=run_id,
                outcome="QUARANTINED", needs_attention=False)
    assert both["products"] == [], "a contradiction returned something"


def test_retryable_is_its_own_question(server, tmp_path, monkeypatch):
    from friday import products as P

    def flaky(record, context):
        if record.product_key == 'A-1':
            raise P.Retryable('the supplier was busy')
        record.set('ok', True, source='test', method=P.DIRECT)
    monkeypatch.setattr(PT, 'default_pipeline', lambda: P.Pipeline([P.Stage('fetch', flaky)]))
    run_id = call(server, 'product_process', path=catalogue_in(tmp_path))['run_id']
    retryable = call(server, 'product_result', run_id=run_id, retryable=True)
    assert [p['product_key'] for p in retryable['products']] == ['A-1']
    permanent = call(server, 'product_result', run_id=run_id, retryable=False)
    assert 'A-1' not in [p['product_key'] for p in permanent['products']]


def test_the_removed_filter_is_refused_rather_than_ignored(server, tmp_path):
    """
    Measured on the installed SDK: FastMCP accepts unknown arguments and drops
    them. So deleting `only` would have left every old caller - Claude, the
    automations engine, a forged skill - getting EVERY product back with no
    error. A wrong answer wearing the shape of a right one, which is the exact
    failure class this whole batch exists to remove.
    """
    run_id = call(server, "product_process",
                  path=catalogue_in(tmp_path))["run_id"]
    result = call(server, "product_result", run_id=run_id, only="failed")
    assert "`only` was removed" in result["error"]
    assert "needs_attention" in result["error"], "it does not say what to use"
    assert result["products"] == []


def test_an_unknown_outcome_says_so_rather_than_answering_something_else(
        server, tmp_path):
    run_id = call(server, "product_process",
                  path=catalogue_in(tmp_path))["run_id"]
    result = call(server, "product_result", run_id=run_id, outcome="broken")
    assert "unknown outcome" in result["error"]
    assert result["products"] == []


def test_a_retry_refuses_when_it_would_have_to_guess_which_run(server, tmp_path):
    """
    Reading the newest of several runs and naming it is helpful. Re-running
    the newest because it happens to be newest is a coin toss with side
    effects, against a catalogue nobody asked about.
    """
    first = call(server, 'product_process', path=catalogue_in(tmp_path))
    other = tmp_path / 'second'
    other.mkdir()
    call(server, 'product_process', path=catalogue_in(other))
    PT._active_run_id = ''
    refused = call(server, 'product_retry')
    assert not refused['run_id']
    assert 'say which run' in refused['error']
    named = call(server, 'product_retry', run_id=first['run_id'])
    assert named['run_id'] == first['run_id']


def test_an_export_refuses_the_same_way(server, tmp_path):
    call(server, 'product_process', path=catalogue_in(tmp_path))
    other = tmp_path / 'second'
    other.mkdir()
    call(server, 'product_process', path=catalogue_in(other))
    PT._active_run_id = ''
    refused = call(server, 'product_export')
    assert 'say which run' in refused['error']
    assert refused['rows'] == 0


def test_the_run_this_session_started_is_not_a_guess(server, tmp_path):
    """
    The normal conversation: process a catalogue, then "retry those". The
    database holds older runs, and none of them are what he meant.
    """
    call(server, "product_process", path=catalogue_in(tmp_path))
    other = tmp_path / "second"
    other.mkdir()
    mine = call(server, "product_process", path=catalogue_in(other))

    retried = call(server, "product_retry")
    assert retried["run_id"] == mine["run_id"]
    assert not retried["error"]


def test_reading_still_answers_when_mutating_would_not(server, tmp_path):
    call(server, 'product_process', path=catalogue_in(tmp_path))
    other = tmp_path / 'second'
    other.mkdir()
    newest = call(server, 'product_process', path=catalogue_in(other))
    PT._active_run_id = ''
    status = call(server, 'product_status')
    assert status['run_id'] == newest['run_id']
    assert 'LAST_DOMAIN_RUN' in status['resolved_by'], status['resolved_by']
    assert not status['error']


def test_the_answer_comes_before_the_provenance(server, tmp_path):
    """
    `did_not_succeed` was correct and last, behind every product's field-level
    provenance - and the model read the front of the payload and reported no
    failures while holding it. Order and size are part of the interface.
    """
    run_id = call(server, "product_process",
                  path=catalogue_in(tmp_path))["run_id"]
    result = call(server, "product_result", run_id=run_id)
    keys = list(result)
    assert keys.index("did_not_succeed") < keys.index("products")
    assert not any("fields" in p for p in result["products"]), \
        "field provenance is in the default answer again"

    detailed = call(server, "product_result", run_id=run_id, include_fields=True)
    assert all("fields" in p for p in detailed["products"]), \
        "asking for provenance did not produce it"


def test_a_retry_says_what_it_re_ran(server, tmp_path, monkeypatch):
    """
    A retry that fails the same way leaves every run-level count identical.
    Reading those counts, the model reported that there had been nothing to
    retry - having just re-run a product.

    The stage here fails retryably without a lookup: what is under test is
    which rows the adapter selects and what it says it did, not the image
    stage's DNS behaviour.
    """
    from friday import products as P

    def flaky(record, context):
        if record.product_key == 'A-1':
            raise P.Retryable('the supplier was busy')
        record.set('ok', True, source='test', method=P.DIRECT)
    monkeypatch.setattr(PT, 'default_pipeline', lambda: P.Pipeline([P.Stage('fetch', flaky)]))
    first = call(server, 'product_process', path=catalogue_in(tmp_path))
    assert first['retried'] == [], 'a first pass re-ran nothing'
    retried = call(server, 'product_retry', run_id=first['run_id'])
    assert retried['retried'] == ['A-1'], 'the one retryable product is not named as re-run'
    assert retried['outcome'] == first['outcome'], 'the run-level counts changed, so this no longer proves that `retried` is what tells the caller something happened'


def test_asking_without_a_run_id_still_gets_an_answer(server, tmp_path):
    """
    A fresh session asked "how did that catalogue job finish?" and replied
    "do you have the run id?". He cannot - Friday invented it.
    """
    run_id = call(server, "product_process",
                  path=catalogue_in(tmp_path))["run_id"]
    status = call(server, "product_status")
    assert status["run_id"] == run_id
    assert not status["error"]
    assert status["resolved_by"], "it picked a run without saying which"

    result = call(server, "product_result")
    assert result["run_id"] == run_id


def test_asking_about_nothing_at_all_says_there_is_nothing(server):
    status = call(server, "product_status")
    assert not status["run_id"]
    assert "no catalogue run" in status["error"]


def test_a_named_catalogue_picks_that_run(server, tmp_path):
    first = call(server, "product_process", path=catalogue_in(tmp_path))
    other = tmp_path / "winter"
    other.mkdir()
    second = call(server, "product_process", path=catalogue_in(other))

    assert first["run_id"] != second["run_id"]
    # Both files are called products.csv, so the source cannot separate them;
    # the id can, and that is what `about` is for.
    picked = call(server, "product_status", about=first["run_id"][-6:])
    assert picked["run_id"] == first["run_id"]


def test_an_unknown_run_is_a_result_with_a_reason(server):
    status = call(server, "product_status", run_id="RUN-does-not-exist")
    assert status["error"]
    assert status["execution_state"] != "COMPLETED"


def test_every_tool_advertises_an_output_schema(server):
    """Without one the model parses a sentence instead of reading state."""
    tools = asyncio.run(server.list_tools())
    assert tools, "nothing registered"
    assert all(t.outputSchema for t in tools), \
        [t.name for t in tools if not t.outputSchema]
