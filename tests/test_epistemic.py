"""
Tests for friday/epistemic.py (EPI-001, PRD section 35).

The record is only worth having if it refuses to be vague: a claim has to say
how it is known, and "sourced" has to mean there is a source.
"""
from __future__ import annotations

import math

import pytest

from friday import epistemic as E


def test_a_claim_carries_every_field_the_prd_names():
    record = E.LedgerClaim(
        claim="Godot is free", status=E.SOURCED,
        sources=["https://a.example"], evidence="the licence page says MIT",
        confidence=0.9, freshness="stable", as_of="2026-01-10T00:00:00+00:00",
        expires_at="2026-04-10T00:00:00+00:00",
        assumptions=["the licence has not changed"],
        contradictions=["a fork is paid"], scope="drone-game")

    shown = record.as_dict()

    for key in ("claim", "status", "sources", "evidence", "confidence",
                "freshness", "assumptions", "contradictions", "scope"):
        assert key in shown
    assert shown["sources"] == ["https://a.example"]
    assert shown["id"] == record.id


def test_every_prd_status_is_accepted_and_no_other():
    for status in E.STATUSES:
        sources = ("https://a.example",) if status == E.SOURCED else ()
        assert E.LedgerClaim(claim="c", status=status, sources=sources).status == status
    with pytest.raises(ValueError):
        E.LedgerClaim(claim="c", status="believed")


def test_a_sourced_claim_must_name_its_sources():
    with pytest.raises(ValueError):
        E.LedgerClaim(claim="c", status=E.SOURCED)


@pytest.mark.parametrize("confidence", [-0.1, 1.1, math.nan])
def test_confidence_outside_zero_to_one_is_refused(confidence):
    with pytest.raises(ValueError):
        E.LedgerClaim(claim="c", status=E.SPECULATIVE, confidence=confidence)


def test_a_claim_with_no_words_is_refused():
    with pytest.raises(ValueError):
        E.LedgerClaim(claim="   ", status=E.SPECULATIVE)


def test_a_bare_string_source_is_one_source_not_its_letters():
    record = E.LedgerClaim(claim="c", status=E.SOURCED,
                           sources="https://a.example")

    assert record.sources == ("https://a.example",)


def test_the_id_follows_the_claim_and_scope_not_its_formatting():
    base = E.LedgerClaim(claim="Godot is free.", status=E.SPECULATIVE)
    reworded = E.LedgerClaim(claim="  godot   is FREE", status=E.INFERRED,
                             confidence=0.4)
    elsewhere = E.LedgerClaim(claim="Godot is free", status=E.SPECULATIVE,
                              scope="other-project")

    assert base.id == reworded.id
    assert base.id != elsewhere.id
    assert base.id.startswith("clm_")
