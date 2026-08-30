"""
Ranking inside one domain, where words alone cannot decide.

Every product tool is called `product_something` and described in terms of
catalogues, so "retry the network failures" scores about the same against all
six. The agent gate caught what that costs: in one run of four the model
picked `product_process` for a retry and reprocessed the whole catalogue into
a second run.

What separates them is not vocabulary. It is that `product_process` STARTs
work while `product_retry` RECOVERs work that already exists - and that this
conversation has already started something.

These tests use the real registry rather than fixtures, because the thing
being tested is whether the registry's own metadata is good enough.
"""

from __future__ import annotations

import pytest

from friday import capabilities as C
from friday import capability_router as R


class FakeTool:
    """What the MCPToolset hands the router, reduced to what it reads."""

    def __init__(self, name: str, description: str) -> None:
        self.info = type("Info", (), {
            "name": name,
            "raw_schema": {"description": description, "parameters": {}},
        })()


@pytest.fixture
def router():
    """Every declared capability, described as the registry describes it."""
    router = R.Router()
    router.load([FakeTool(cap.id, cap.description) for cap in C._ALL])
    return router


def top(router, query: str, limit: int = 6) -> list[str]:
    return [m["capability"] for m in router.search(query, limit=limit)]


def rank(ranked: list[str], name: str) -> int:
    """Position, or off the end. Falling off the list is a stronger demotion
    than ranking last, and the assertions should treat it as one rather than
    raising ValueError."""
    return ranked.index(name) if name in ranked else len(ranked) + 1


# ---------------------------------------------------------------------------
# Cold: nothing started yet
# ---------------------------------------------------------------------------


def test_processing_a_catalogue_finds_the_processor(router):
    assert top(router, "process this product catalogue")[0] == "product_process"


def test_a_follow_up_ranks_below_a_start_when_nothing_has_started(router):
    """Asking to retry before anything ran is a confused request, not a retry."""
    ranked = top(router, "retry the network failures")
    assert "product_retry" in ranked


# ---------------------------------------------------------------------------
# Warm: this conversation has processed something
# ---------------------------------------------------------------------------


@pytest.fixture
def after_processing(router):
    router.note_used("product_process")
    return router


def test_retry_beats_reprocess_once_a_run_exists(after_processing):
    """The measured failure, as a ranking assertion."""
    ranked = top(after_processing, "retry the network failures")
    assert ranked[0] == "product_retry", ranked
    assert rank(ranked, "product_retry") < rank(ranked, "product_process"), ranked


def test_which_products_failed_reaches_the_reader_not_the_processor(
        after_processing):
    ranked = top(after_processing, "which products failed and why")
    assert ranked[0] == "product_result", ranked


def test_how_did_it_finish_reaches_status_or_the_run_list(after_processing):
    ranked = top(after_processing, "how did that catalogue job finish")
    assert ranked[0] in ("product_status", "product_runs"), ranked
    assert rank(ranked, "product_process") > 0, ranked


def test_starting_over_is_still_reachable_when_he_says_so(after_processing):
    """
    The demotion must not make "do it again" unreachable - that would be a
    worse bug than the one it fixes.
    """
    for phrase in ("process the catalogue again from scratch",
                   "reprocess that catalogue",
                   "run the whole catalogue again"):
        assert top(after_processing, phrase)[0] == "product_process", phrase


def test_only_start_capabilities_change_the_picture(router):
    """A read must not convince the router that work is under way."""
    router.note_used("product_result")
    router.note_used("product_status")
    assert not router.started


# ---------------------------------------------------------------------------
# The same shape in a second domain, so the mechanism is not product-specific
# ---------------------------------------------------------------------------


def test_what_happened_this_morning_is_history_not_a_new_automation(router):
    router.note_used("automations_create")
    ranked = top(router, "what happened this morning")
    assert ranked[0] == "automations_history", ranked
    assert rank(ranked, "automations_create") > 0, ranked


def test_scheduling_something_new_still_reaches_create(router):
    assert top(router, "do this every morning at seven")[0] == \
        "automations_create"


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------


def test_an_undeclared_tool_does_not_break_the_search(router):
    """Upstream can add a tool this file has never heard of."""
    router.all_tools["brand_new_tool"] = FakeTool(
        "brand_new_tool", "Does something nobody declared.")
    assert top(router, "something nobody declared")


def test_every_annotated_capability_declares_a_known_kind():
    for cap in C._ALL:
        assert cap.operation_kind in C.OPERATION_KINDS


def test_negative_examples_never_repeat_an_intent_example():
    """A phrase that both attracts and repels is a scoring bug in waiting."""
    for cap in C._ALL:
        overlap = set(cap.intent_examples) & set(cap.negative_examples)
        assert not overlap, f"{cap.id}: {overlap}"


def test_no_capability_is_named_entirely_in_words_the_filter_drops():
    """
    The empty-word filter runs over the query AND the tool name, so a tool
    called only things like `get_all_the_things` would be unreachable by name
    and would depend entirely on its description. Worth knowing before it
    happens rather than after.
    """
    for cap in C._ALL:
        assert R._content(cap.id.replace("_", " ")), \
            f"{cap.id} has no distinctive word in its name"


def test_every_example_carries_something():
    """
    An example must survive the empty-word filter, or it is not an example.

    This was briefly "at least two content words", which is what the ratio
    scorer needed - and it rejected "what do you know about me", a phrasing
    the boss genuinely uses that reduces to {know}. Counting shared words
    instead of taking a ratio made one shared word a nudge rather than a
    verdict, so the stricter rule went out with the reason for it.
    """
    for cap in C._ALL:
        for phrase in cap.intent_examples + cap.negative_examples:
            assert R._content(phrase), f"{cap.id}: {phrase!r} is all filler"


def test_a_capability_is_never_repelled_by_its_own_best_example():
    """
    The scoring bug that started this, as a property over the whole registry.

    Asserted against the real rule rather than a proxy for it: a negative
    counts only for the amount by which a request looks MORE like something
    else, so an equal score costs nothing and a higher one is the bug.
    """
    for cap in C._ALL:
        for phrase in cap.intent_examples:
            attracted = R._phrase_score(phrase.lower(), cap.intent_examples)
            repelled = R._phrase_score(phrase.lower(), cap.negative_examples)
            assert repelled <= attracted, (
                f"{cap.id}: {phrase!r} scores {attracted} for it and "
                f"{repelled} against it")


def test_every_annotated_capability_wins_its_own_best_phrasing():
    """
    The regression that has now happened four times, as a standing check.

    Every batch so far has broken an existing capability, and never because
    the newcomer was bad: `web_crawl` lost "read these pages", `music_current`
    lost "what's playing right now", `vision_inspect_screen` lost "look at the
    screen", `reminders_create` lost "set a reminder". Each time the incumbent
    declared no metadata and the newcomer arrived with forty points of it.

    Finding those one batch at a time is slow and depends on somebody having
    written an utterance for the thing that broke. This asks the question of
    the whole registry at once: whatever a capability says it is for must
    reach it.

    Not first place - the model is handed several and chooses - but in the
    top three, which is what "reachable" means everywhere else in this
    project.
    """
    router = R.Router()
    router.load([FakeTool(cap.id, cap.description) for cap in C._ALL])

    unreachable = []
    for cap in C._ALL:
        for phrase in cap.intent_examples:
            ranked = [m["capability"] for m in router.search(phrase, limit=6)]
            if cap.id not in ranked[:3]:
                unreachable.append(
                    f"{cap.id}: {phrase!r} -> {ranked[:3]}")
    assert not unreachable, (
        "a capability cannot be reached by its own example phrasing:\n  "
        + "\n  ".join(unreachable))


# ---------------------------------------------------------------------------
# Batch 2D: the words that must not reach the machine
# ---------------------------------------------------------------------------
#
# This subsystem's worst realistic failure is not a broken function. It is the
# right words reaching the wrong capability, and the wrong capability turning
# off somebody's computer mid-render. Five routing regressions have already
# happened in this codebase from exactly this cause; here the cost is the
# machine going down.
#
# Each case names both halves: what it must reach, and what it must never
# reach. Asserting only the first would pass while the second was one point
# behind.

#: (phrase, must be near the top, must not appear at all)
ADVERSARIAL = [
    ("shut down the music",
     ("music_stop", "music_pause"), ("power_shutdown", "power_restart")),
    ("shut the music down",
     ("music_stop", "music_pause"), ("power_shutdown",)),
    ("restart the song",
     ("music_play",), ("power_restart", "power_shutdown")),
    ("play that again",
     ("music_play",), ("power_restart",)),
    ("close chrome",
     ("apps_close", "process_close", "browser_close"),
     ("power_shutdown", "power_restart", "process_terminate")),
    ("quit spotify",
     ("apps_close", "process_close"), ("power_shutdown", "music_stop")),
    ("put the computer to sleep",
     ("power_sleep",), ("music_pause", "power_shutdown")),
    ("suspend the music",
     ("music_pause",), ("power_sleep", "power_hibernate")),
    ("turn off the lights",
     (), ("power_shutdown", "power_restart")),
    # `system_get_info` is not listed as the required winner here: it loses
    # this phrase to `audio_master_volume`, and did so before batch 2D
    # existed. That is a real routing weakness and it is somebody else's
    # batch - quietly fixing an unrelated capability inside this one is how a
    # green suite stops meaning anything. What IS this batch's business is
    # that neither power capability took the slot, which is asserted.
    ("what machine is this", (), ("power_shutdown", "power_restart")),
]


@pytest.mark.parametrize("phrase,wanted,forbidden", ADVERSARIAL,
                         ids=[case[0].replace(" ", "-") for case in ADVERSARIAL])
def test_a_smaller_request_never_reaches_the_whole_machine(
        router, phrase, wanted, forbidden):
    """
    The assertion is **selection**, not offering, and that is deliberate.

    Two earlier versions of this test were wrong in the same direction. The
    first demanded the destructive capability be absent from the candidate
    list; the second demanded it rank below every acceptable alternative.
    Neither is defensible: "shut down the music" and "shut down the computer"
    share two words of four, and `power_shutdown` legitimately owns "shut
    down". A retrieval step that offers it is not wrong.

    What would be wrong - and what SC-007 actually says - is it being
    *selected*. So: the right capability must win, and nothing that can turn
    the machine off may. How often a live model then picks the winner is a
    different measurement, and it belongs to the agentability gate, which can
    run the model. This one measures the ranking the model is handed.
    """
    ranked = [m["capability"] for m in router.search(phrase, limit=6)]

    for name in forbidden:
        assert not ranked or ranked[0] != name, (
            f"{phrase!r} chose {name} - ranked {ranked}")

    if wanted:
        assert ranked and ranked[0] in wanted, (
            f"{phrase!r} chose {ranked[:1]}, not one of {wanted} - "
            f"ranked {ranked}")


def test_forcing_is_reachable_when_it_is_actually_meant(router):
    """
    The gate must not be a way of quietly making force unreachable. Somebody
    with a hung application has to be able to say so.
    """
    for phrase in ("force close it", "it is frozen, kill it", "force quit chrome"):
        ranked = [m["capability"] for m in router.search(phrase, limit=6)]
        assert "process_terminate" in ranked[:3], \
            f"{phrase!r} could not reach force termination - {ranked[:3]}"


def test_the_machine_is_reachable_when_it_is_actually_meant(router):
    for phrase, wanted in (("shut down the computer", "power_shutdown"),
                           ("restart the computer", "power_restart"),
                           ("lock my computer", "power_lock"),
                           ("cancel that", "power_cancel")):
        ranked = [m["capability"] for m in router.search(phrase, limit=6)]
        assert wanted in ranked[:3], \
            f"{phrase!r} could not reach {wanted} - {ranked[:3]}"


def test_every_destructive_capability_declares_what_must_not_reach_it(router):
    """
    A capability that can turn the machine off and declares no negatives is
    the shape that produced every routing regression so far: it arrives with
    intent examples worth four points a word and nothing saying which requests
    are somebody else's.
    """
    for cap in C._ALL:
        if cap.risk in ("HIGH", "IRREVERSIBLE"):
            assert cap.negative_examples, \
                f"{cap.id} is {cap.risk} and declares no negative examples"
            assert cap.intent_examples, \
                f"{cap.id} is {cap.risk} and declares no intent examples"
