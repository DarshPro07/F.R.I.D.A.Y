"""CTX-002: the staged tool router, tool cards and label adjudication."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from friday import capability_router as R
from friday import tool_cards, tool_models, tool_router

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import label_adjudication as LA  # noqa: E402

IDS = ["music_search", "music_play_mood", "files_read", "files_info"]
TEXTS = ["find songs by name", "play a song for a mood", "read a text file", "size of a file"]


class FakeChooser:
    def __init__(self, answer=None, error: Exception | None = None):
        self.answer, self.error, self.seen = answer, error, []

    def choose(self, query, cards):
        self.seen.append((query, cards))
        if self.error:
            raise self.error
        return self.answer


def staged(chooser=None, *, thresholds=None, ranking=None):
    ranking = ranking if ranking is not None else ["music_search", "music_play_mood"]
    return tool_router.StagedRouter(
        IDS, TEXTS, shipped=lambda q: list(ranking), chooser=chooser,
        thresholds=thresholds or tool_router.Thresholds(), top_k=3)


EAGER = tool_router.Thresholds(agree=0.0, margin=0.0)


def test_a_confident_retrieval_routes_directly_without_a_model_call():
    chooser = FakeChooser({"decision": "TOOL", "tool_id": "files_info", "confidence": 1})
    route = staged(chooser, thresholds=EAGER).route("find songs by name")
    assert (route.decision, route.stage, route.uncertain) == ("TOOL", 1, False)
    assert route.tool_id == "music_search" and chooser.seen == []


def test_an_unsure_retrieval_hands_the_top_cards_to_the_chooser():
    chooser = FakeChooser({"decision": "TOOL", "tool_id": "music_play_mood", "confidence": 0.8})
    route = staged(chooser).route("something to listen to")
    assert (route.stage, route.tool_id, route.confidence) == (2, "music_play_mood", 0.8)
    (query, cards), = chooser.seen
    assert query == "something to listen to" and 0 < len(cards) <= 3
    assert all(card.split(":")[0] in route.candidates for card in cards)


@pytest.mark.parametrize("decision", ["CLARIFY", "ABSTAIN"])
def test_the_chooser_may_decline_to_pick_a_tool(decision):
    chooser = FakeChooser({"decision": decision, "tool_id": None, "confidence": 0.6})
    route = staged(chooser).route("do that thing")
    assert (route.decision, route.tool_id, route.stage) == (decision, None, 2)


@pytest.mark.parametrize("answer", [
    {"decision": "TOOL", "tool_id": "power_shutdown", "confidence": 0.9},   # not offered
    {"decision": "TOOL", "tool_id": "music_search", "confidence": 0.9, "run": "rm -rf"},
    {"decision": "MAYBE", "tool_id": "music_search", "confidence": 0.9},
    "music_search",
    None,
])
def test_a_chooser_answer_is_untrusted(answer):
    route = staged(FakeChooser(answer)).route("something to listen to")
    assert route.uncertain and route.stage == 1 and route.tool_id == route.candidates[0]


def test_a_failing_chooser_never_fails_the_request():
    route = staged(FakeChooser(error=TimeoutError("gateway"))).route("something to listen to")
    assert route.uncertain and route.tool_id == route.candidates[0]
    assert "TimeoutError" in route.reason


def test_without_a_chooser_the_top_candidate_is_marked_uncertain():
    route = staged().route("something to listen to")
    assert route.uncertain and route.stage == 1 and "music_search" in route.candidates


def test_an_out_of_range_confidence_is_discarded():
    chooser = FakeChooser({"decision": "TOOL", "tool_id": "music_search", "confidence": 7})
    assert staged(chooser).route("something to listen to").confidence == 0.0


def test_the_gateway_chooser_parses_strict_json_and_fenced_json():
    seen = []

    def infer(system, user):
        seen.append((system, user))
        return '```json\n{"decision": "TOOL", "tool_id": "a", "confidence": 0.5}\n```'

    answer = tool_router.GatewayChooser(infer).choose('say "hi"', ["a: does a", "b: does b"])
    assert answer == {"decision": "TOOL", "tool_id": "a", "confidence": 0.5}
    assert "does a" in seen[0][1] and json.dumps('say "hi"') in seen[0][1]
    assert "TOOL" in seen[0][0] and "CLARIFY" in seen[0][0] and "ABSTAIN" in seen[0][0]


def test_the_default_thresholds_route_nothing_the_sweep_could_not_justify():
    t = tool_router.Thresholds()
    assert (t.agree, t.margin) == (1.0, 0.4)


# --- cards --------------------------------------------------------------------


def test_every_long_tail_tool_has_a_card():
    from mcp.server.fastmcp import FastMCP

    from friday.tools import register_all_tools

    server = FastMCP(name="test")
    register_all_tools(server)
    names = {t.name for t in asyncio.run(server.list_tools())}
    missing = sorted(names - set(R.CORE_TOOLS) - set(tool_cards.CARDS))
    assert not missing, f"tools without a card: {missing}"
    assert not sorted(set(tool_cards.CARDS) - names), "a card for a tool that is not registered"


def test_a_card_names_real_siblings_and_says_what_it_does():
    for name, card in tool_cards.CARDS.items():
        assert card.does and card.domain, name
        assert name not in card.confusable_with, name
        assert set(card.confusable_with) <= set(tool_cards.CARDS), name


def test_the_index_leaves_out_the_negative_words_the_chooser_sees():
    index = tool_cards.index_text("process_close")
    compact = tool_cards.compact("process_close")
    assert "not: end a program that refuses to close" in compact
    assert "vs process_terminate" in compact
    assert "refuses" not in index and "process_terminate" not in index


def test_a_tool_without_a_card_still_gets_a_chooser_line():
    assert tool_cards.compact("memory_search", "Search memory.\nMore.") == "memory_search: Search memory. More."


def test_tool_text_adds_the_card_only_when_asked():
    plain = tool_router.tool_text("files_info", "Size of a file.", cards=False)
    rich = tool_router.tool_text("files_info", "Size of a file.", cards=True)
    assert "last modified" not in plain and "last modified" in rich


# --- model pins ---------------------------------------------------------------


def test_the_comparison_embedder_is_pinned_by_revision_and_hash():
    pin = tool_models.PINS["tool_embedder_base"]
    assert pin.repo == "BAAI/bge-base-en-v1.5" and pin.dim == 768 and pin.kind == "embedder"
    assert pin.revision == "a5beb1e3e68b9ab74eb54cfd186867f64f240e1a"
    assert dict(pin.files)["onnx/model.onnx"].startswith("9bc579acdba2")
    assert tool_models.PINS["tool_reranker"].kind == "reranker"


# --- label adjudication -------------------------------------------------------


def row(text, expect, **extra):
    return {"id": "x", "text": text, "expect": expect, **extra}


def test_two_tools_that_both_carry_out_the_request_are_both_correct():
    got = LA.adjudicate(row("delete the old draft", "files_delete"))
    assert got["also_correct"] == ["files_recycle"] and got["reason"].startswith("A:delete-file")
    assert LA.adjudicate(row("put that file in the bin", "files_recycle"))["also_correct"] == ["files_delete"]


@pytest.mark.parametrize("text, expect", [
    ("delete the old draft forever", "files_delete"),
    ("close the frozen program", "apps_close"),
    ("turn spotify down to 20", "volume_set"),
    ("overwrite the notes file", "files_write"),
])
def test_a_cue_only_one_tool_satisfies_blocks_the_substitution(text, expect):
    assert LA.adjudicate(row(text, expect)) is None


def test_a_bare_pronoun_imperative_is_a_clarify_request_only_without_context():
    assert LA.adjudicate(row("close it", "apps_close"))["clarify"] is True
    assert LA.adjudicate(row("close it", "apps_close", context={"last": "notepad"})) is None
    assert LA.adjudicate(row("close it", "apps_close", turns=[{"role": "user"}])) is None
    assert LA.adjudicate(row("close it", "ABSTAIN")) is None


def test_the_overlay_is_current_and_never_rewrites_an_original_label():
    assert json.dumps(LA.build(), indent=1, ensure_ascii=False) + "\n" == LA.OVERLAY.read_text(encoding="utf-8")
    for entry in LA.build()["rows"].values():
        assert set(entry) <= {"also_correct", "clarify", "reason"} and entry["reason"]


# --- the benchmark's own honesty ------------------------------------------------

import bench_tool_disclosure as BENCH  # noqa: E402


def test_a_named_embedder_that_cannot_load_stops_the_run_instead_of_being_skipped(monkeypatch, tmp_path):
    monkeypatch.setenv("FRIDAY_MODELS_DIR", str(tmp_path))
    tool_models.unload()
    try:
        with pytest.raises(SystemExit, match="not available"):
            BENCH.resolve_embedder(tool_models.EMBEDDER.id)
    finally:
        tool_models.unload()
    with pytest.raises(SystemExit, match="unknown embedder"):
        BENCH.resolve_embedder("tool_embedder_typo")
    assert BENCH.resolve_embedder("none") is None


def test_the_sweep_counts_a_direct_route_on_an_abstain_request_as_wrong():
    # (ranked, agree, margin, acceptable): two confident-and-right rows, one confident-and-wrong.
    rows = [(["a"], 1.0, 0.5, {"a"}), (["b"], 1.0, 0.5, {"b"}), (["c"], 1.0, 0.5, {"x"}),
            (["d"], 0.5, 0.0, {"d"})]
    swept = BENCH.sweep_thresholds(rows, [(1.0, 0.5)])
    cell = next(g for g in swept["grid"] if g["agree"] == 1.0 and g["margin"] == 0.4)
    assert cell["direct_share"] == 0.75
    assert cell["direct_precision"] == round(2 / 4, 4)      # the abstain request counts against it
    assert cell["abstain_routed_directly"] == 1.0
    assert swept["chosen"] is None or swept["chosen"]["direct_precision"] >= BENCH.TARGET_PRECISION


def test_the_sweep_picks_the_largest_direct_share_that_meets_the_target():
    rows = [(["a"], 1.0, 0.5, {"a"})] * 3 + [(["z"], 0.5, 0.0, {"q"})]
    swept = BENCH.sweep_thresholds(rows, [(0.5, 0.0)])
    assert swept["chosen"]["direct_precision"] >= BENCH.TARGET_PRECISION
    assert swept["chosen"]["direct_share"] == 0.75
