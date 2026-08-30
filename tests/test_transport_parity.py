"""
The same work, asked for three ways, must come out the same.

Friday has three doors into a capability:

    the toolset function     called directly, in-process
    the MCP adapter          what the conversational model reaches
    the CapabilityRuntime    what a durable objective reaches

Before CORE-01 the third door did not exist for most capabilities, and before
CORE-02B it did not exist for these. The risk once it does is subtler than an
outright gap: two doors that both work and disagree. The MCP adapter used to
hold the run resolution, the counting and the export writer, so anything
reached another way would have been running different code by construction.

Serialisation may differ - the adapter returns a flat dict because
`friday/autolearn.py` reads fields off the top level of it, the runtime
returns an ActionResult with evidence attached. Outcomes may not.
"""

from __future__ import annotations

import asyncio

import pytest
from mcp.server.fastmcp import FastMCP

from friday import capability_runtime as R
from friday import contracts as c
from friday.fsjail import FileJail
from friday.store import Store
from friday.toolsets import files as F
from friday.toolsets import products as PT
from friday.tools import product_control

CATALOGUE = """sku,title,price,image
A-1,Cotton Shirt,10.00,
A-2,Wool Scarf,12.00,
A-2,Wool Scarf,12.00,
A-3,Denim Jacket,banana,
"""

#: The fields that describe what happened, as opposed to which run it was.
#: run_id and timestamps differ between three separate runs of the same
#: catalogue and are supposed to.
OUTCOME_FIELDS = ("execution_state", "outcome", "source", "input_rows",
                  "canonical_products", "processed", "deduplicated",
                  "partial", "quarantined", "failed",
                  "duplicates_conflicting")


@pytest.fixture
def catalogue(tmp_path):
    """A store and a jail belonging to this test alone."""
    product_control.reset_store(Store(tmp_path / "products.sqlite3"))
    F.reset_jail(FileJail(roots=(tmp_path,)))
    path = tmp_path / "products.csv"
    path.write_text(CATALOGUE, encoding="utf-8")
    yield str(path)
    product_control.reset_store(None)
    F.reset_jail(None)


def via_toolset(path: str) -> dict:
    run = c.Run.create("parity: toolset", capability="product")
    return dict(PT.product_process(run, path).output or {})


def via_mcp(path: str) -> dict:
    server = FastMCP(name="parity")
    product_control.register(server)
    _text, structured = asyncio.run(
        server.call_tool("product_process", {"path": path}))
    return structured


def via_runtime(path: str) -> dict:
    dispatch = R.build_dispatch()
    result = asyncio.run(dispatch("product_process", {"path": path}))
    return dict(result.get("output") or {})


def test_processing_a_catalogue_agrees_across_all_three_doors(catalogue):
    toolset = via_toolset(catalogue)
    mcp = via_mcp(catalogue)
    runtime = via_runtime(catalogue)

    # §24. If the fixture were wrong and every door returned an empty dict,
    # the comparisons below would all hold and prove nothing.
    assert toolset.get("input_rows") == 4, \
        f"the fixture did not process: {toolset}"

    for field in OUTCOME_FIELDS:
        assert toolset[field] == mcp[field] == runtime[field], (
            f"{field} differs by transport: toolset={toolset[field]!r} "
            f"mcp={mcp[field]!r} runtime={runtime[field]!r}")


def test_the_counting_is_the_same_answer_not_merely_the_same_shape(catalogue):
    """
    Four rows, one of them a duplicate of another and one with a price of
    "banana". The distinction between what arrived and what was processed is
    the thing that must survive the trip, in all three directions.
    """
    for door in (via_toolset, via_mcp, via_runtime):
        got = door(catalogue)
        assert got["input_rows"] == 4, f"{door.__name__}: {got}"
        assert got["canonical_products"] == 3, \
            f"{door.__name__} lost the duplicate collapse: {got}"
        assert got["input_rows"] != got["processed"], \
            f"{door.__name__} reported every arriving row as processed"


def test_only_the_runtime_carries_evidence(catalogue):
    """
    The transports are not identical and should not be. The runtime is the one
    that has to prove the work happened, because it is the one a durable
    objective believes.
    """
    run = c.Run.create("parity: evidence", capability="product")
    result = PT.product_process(run, catalogue)

    assert result.status == c.SUCCEEDED
    assert result.verification is not None, \
        "a product run succeeded with no verification"
    assert result.run_id in [r.run_id for r in run.results]
    assert "read back" in result.verification.evidence


def test_a_refusal_keeps_its_shape_on_every_transport(catalogue, tmp_path):
    """
    A refused call still has to return a RunSummary. The model reads `error`
    off the payload, and an empty dict where a summary belongs is how a
    refusal gets reported as a result.
    """
    outside = str(tmp_path.parent / "not-in-the-jail.csv")

    for door in (via_toolset, via_mcp, via_runtime):
        got = door(outside)
        assert got.get("error"), f"{door.__name__} refused without saying so"
        assert "run_id" in got and "input_rows" in got, \
            f"{door.__name__} returned {sorted(got)} instead of a summary"
        assert got["input_rows"] == 0


@pytest.mark.parametrize("capability_id, arguments", [
    ("profile_get", {}),
    ("product_runs", {"limit": 3}),
    ("get_current_time", {}),
])
def test_the_extracted_capabilities_run_from_a_durable_objective(
        capability_id, arguments):
    """
    The point of the whole exercise, stated as a test: these were registered
    and unreachable, and the failure was silent - an objective would plan a
    step, find nothing to call, and abandon the rest.
    """
    dispatch = R.build_dispatch()
    result = asyncio.run(dispatch(capability_id, arguments))

    assert result["status"] != "not_configured", \
        f"{capability_id} is registered but still unreachable"
    assert result.get("run_id"), f"{capability_id} ran with no run attached"
