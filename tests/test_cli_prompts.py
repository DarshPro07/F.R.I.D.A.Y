"""
FR-COD-003: FRIDAY must not guess on a consequential CLI question, and must
not sit blocked on a trivial one either.

`friday/executors/cli_prompts.py` is a pure parser plus a decision function -
no model call, no subprocess - which is what lets every branch be driven
directly here instead of trusted on the strength of "it looked right". The
two negative controls at the bottom are the actual safety property: a
decision function that can be made to answer a destructive question, or to
guess when nothing supports a guess, is not a safety mechanism at all.
"""
from __future__ import annotations

from friday.executors import cli_prompts as CP

# ---------------------------------------------------------------------------
# fixtures - realistic CLI output, built once and reused by name
# ---------------------------------------------------------------------------

NUMBERED_DOT = (
    "Which package manager should I use?\n"
    "1. npm\n"
    "2. yarn (recommended)\n"
    "3. pnpm\n"
)

NUMBERED_PAREN_FREE_TEXT = (
    "Which frontend framework should this project use?\n"
    "1) React\n"
    "2) Vue\n"
    "3) Svelte\n"
    "4) provide your own answer\n"
)

BRACKET_STYLE = (
    "Pick a deployment target:\n"
    "[1] Staging\n"
    "[2] Production (recommended)\n"
)

YES_NO_DEFAULT_YES = "Run the test suite now? [Y/n]"

YES_NO_DEFAULT_NO = "Delete the feature branch after merge? [y/N]"

NO_RECOMMENDATION_NO_MATCH = (
    "Which linter should be used?\n"
    "1. ESLint\n"
    "2. TSLint\n"
    "3. Biome\n"
)

DESTRUCTIVE_WITH_RECOMMENDATION = (
    "1) force-push and overwrite remote  (recommended)\n"
    "2) cancel\n"
)

NUMBERED_PLAN_NOT_A_QUESTION = (
    "Here is my plan:\n"
    "1. Read the existing config\n"
    "2. Update the schema\n"
    "3. Run the migration\n"
)

STACK_TRACE = (
    'Traceback (most recent call last):\n'
    '  File "app.py", line 42, in <module>\n'
    '    raise ValueError("bad config")\n'
    "ValueError: bad config\n"
)

PROGRESS_LINE = "Downloading dependencies... 3/10 packages installed\n"

PROSE = (
    "I updated the retry logic in uploader.py and re-ran the full test "
    "suite; all 12 tests passed. Let me know if you would like "
    "documentation updates as well."
)


# ---------------------------------------------------------------------------
# detect()
# ---------------------------------------------------------------------------


def test_detect_numbered_dot_style_with_recommendation():
    prompt = CP.detect(NUMBERED_DOT)
    assert prompt is not None
    assert prompt.question == "Which package manager should I use?"
    assert [o.index for o in prompt.options] == ["1", "2", "3"]
    assert prompt.options[1].recommended is True
    assert prompt.options[0].recommended is False
    assert prompt.free_text_allowed is False


def test_detect_numbered_paren_style_with_free_text_option():
    prompt = CP.detect(NUMBERED_PAREN_FREE_TEXT)
    assert prompt is not None
    assert len(prompt.options) == 4
    assert prompt.options[3].free_text is True
    assert prompt.free_text_allowed is True
    # the real choices are not themselves flagged free-text
    assert not any(o.free_text for o in prompt.options[:3])


def test_detect_bracket_style():
    prompt = CP.detect(BRACKET_STYLE)
    assert prompt is not None
    assert [o.index for o in prompt.options] == ["1", "2"]
    assert prompt.options[1].label.startswith("Production")
    assert prompt.options[1].recommended is True


def test_detect_yes_no_default_yes():
    prompt = CP.detect(YES_NO_DEFAULT_YES)
    assert prompt is not None
    assert prompt.question == "Run the test suite now?"
    by_index = {o.index: o for o in prompt.options}
    assert by_index["y"].recommended is True
    assert by_index["n"].recommended is False


def test_detect_yes_no_default_no():
    prompt = CP.detect(YES_NO_DEFAULT_NO)
    assert prompt is not None
    by_index = {o.index: o for o in prompt.options}
    assert by_index["n"].recommended is True
    assert by_index["y"].recommended is False


def test_detect_returns_none_for_a_numbered_plan():
    """
    A numbered to-do list is not a question. Nothing here is marked
    recommended/default and nothing reads as an ask, so firing on this would
    be exactly the failure mode the detector exists to avoid: answering a
    question nobody asked.
    """
    assert CP.detect(NUMBERED_PLAN_NOT_A_QUESTION) is None


def test_detect_returns_none_for_a_stack_trace():
    assert CP.detect(STACK_TRACE) is None


def test_detect_returns_none_for_a_progress_line():
    assert CP.detect(PROGRESS_LINE) is None


def test_detect_returns_none_for_prose():
    assert CP.detect(PROSE) is None


def test_detect_returns_none_for_empty_text():
    assert CP.detect("") is None
    assert CP.detect("   \n  ") is None


# ---------------------------------------------------------------------------
# classify()
# ---------------------------------------------------------------------------


def test_classify_trivial_yes_no_with_explicit_default():
    prompt = CP.detect(YES_NO_DEFAULT_YES)
    assert CP.classify(prompt) == CP.TRIVIAL


def test_classify_ordinary_real_choice_with_a_recommendation():
    prompt = CP.detect(NUMBERED_DOT)
    assert CP.classify(prompt) == CP.ORDINARY


def test_classify_consequential_destructive_option():
    prompt = CP.detect(DESTRUCTIVE_WITH_RECOMMENDATION)
    assert CP.classify(prompt) == CP.CONSEQUENTIAL


# ---------------------------------------------------------------------------
# decide()
# ---------------------------------------------------------------------------


def test_decide_takes_the_recommended_option():
    prompt = CP.detect(NUMBERED_DOT)
    decision = CP.decide(prompt)
    assert decision.answer == "2"
    assert decision.escalate is False
    assert decision.confidence >= 0.9
    assert "recommended" in decision.reason


def test_decide_picks_the_objective_matching_option():
    prompt = CP.detect(NO_RECOMMENDATION_NO_MATCH)  # ESLint/TSLint/Biome, no marker
    decision = CP.decide(
        prompt, objective="We are standardising the whole repo on ESLint.")
    assert decision.answer == "1"
    assert decision.escalate is False
    assert "ESLint" in decision.evidence


def test_decide_escalates_below_the_confidence_floor():
    prompt = CP.detect(NO_RECOMMENDATION_NO_MATCH)
    # Same objective as the passing case, but the floor is raised past what
    # a single shared word can clear - proves the floor is load-bearing, not
    # a parameter nothing reads.
    decision = CP.decide(
        prompt, objective="We are standardising the whole repo on ESLint.",
        confidence_floor=0.9)
    assert decision.escalate is True
    assert decision.answer is None


def test_decide_never_selects_the_free_text_option():
    """
    Even when the free-text label is the only thing that overlaps the
    objective, it must never be auto-picked - there is no model call here to
    write the text such an answer would need.
    """
    prompt = CP.Prompt(
        question="Which endpoint should I use?",
        options=(
            CP.Option(index="1", label="Staging"),
            CP.Option(index="2", label="Custom endpoint", free_text=True),
        ),
        free_text_allowed=True,
    )
    decision = CP.decide(prompt, objective="use a custom endpoint for this")
    assert decision.answer != "2"
    assert decision.answer is None
    assert decision.escalate is True


# -- negative controls (mandatory) -----------------------------------------


def test_decide_escalates_on_a_destructive_option_even_when_recommended():
    """
    `1) force-push and overwrite remote  2) cancel`, with option 1 marked
    recommended. A decision function that can be talked into taking a
    destructive default because it was labelled "recommended" is not a
    safety mechanism - this is the case the CONSEQUENTIAL rule exists for.
    """
    prompt = CP.detect(DESTRUCTIVE_WITH_RECOMMENDATION)
    assert prompt is not None
    assert prompt.options[0].recommended is True  # confirms the setup is real

    decision = CP.decide(prompt, objective="clean up the remote branches",
                         confidence_floor=0.0)  # floor at 0: nothing but the
                                                 # CONSEQUENTIAL rule can save this
    assert decision.escalate is True
    assert decision.answer is None


def test_decide_escalates_with_no_recommendation_and_no_objective_overlap():
    """No default, no objective, nothing to go on - must ask, not guess."""
    prompt = CP.detect(NO_RECOMMENDATION_NO_MATCH)
    decision = CP.decide(prompt, objective="")
    assert decision.escalate is True
    assert decision.answer is None
    assert decision.confidence < 0.6
