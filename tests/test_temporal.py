"""
Temporal grounding: the agent must know what year it is.

The failure: asked whether a business idea was viable "in 2026" while running
on 16 August 2026, Friday replied "let me look into the future viability ...
it's a bit of a projection". It reasoned from its training cutoff and treated
the present as forecastable. get_current_time existed; nothing told the model
it needed it.
"""

from __future__ import annotations

from datetime import datetime

import pytest

import agent_friday as A


def test_temporal_context_states_the_actual_date():
    now = datetime(2026, 8, 16, 4, 21)
    text = A.temporal_context(now)
    assert "2026" in text
    assert "16 August 2026" in text
    assert "Sunday" in text


def test_temporal_context_says_the_year_is_not_upcoming():
    text = A.temporal_context(datetime(2026, 8, 16, 4, 21))
    assert "not upcoming" in text.lower()
    assert "overrides your training data" in text.lower()


def test_instructions_lead_with_the_date():
    """It has to be at the top, not buried under the persona."""
    text = A.build_instructions(datetime(2026, 8, 16, 4, 21))
    assert text.startswith("## CURRENT DATE")
    assert text.index("2026") < text.index("F.R.I.D.A.Y.")


def test_instructions_still_contain_the_persona():
    text = A.build_instructions(datetime(2026, 8, 16, 4, 21))
    assert "F.R.I.D.A.Y." in text
    assert "boss" in text


@pytest.mark.parametrize("phrase", [
    "the future", "projection", "as of my last update", "a hedge",
])
def test_the_prompt_names_the_failure_modes_it_forbids(phrase):
    text = A.build_instructions()
    assert phrase.split()[0].lower() in text.lower()


def test_the_prompt_tells_it_to_search_for_current_things():
    text = A.build_instructions()
    lowered = text.lower()
    assert "web search" in lowered
    assert "not limited to what you were trained on" in lowered
    assert "do not answer a current-events question from memory" in lowered


def test_the_date_is_stamped_at_construction_not_hardcoded():
    early = A.temporal_context(datetime(2026, 1, 1, 9, 0))
    later = A.temporal_context(datetime(2027, 6, 5, 9, 0))
    assert "2026" in early and "2027" in later
    assert early != later


def test_get_current_time_is_still_available_for_precision():
    """The prompt defers to the tool for exact timing, so it must exist."""
    from friday import capabilities

    assert "get_current_time" in capabilities.CAPABILITIES
    assert "call get_current_time" in A.build_instructions()
