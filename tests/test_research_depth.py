"""
RSCH-001: research routed by depth (R0-R5) against an immutable
CanonicalInquiry, with a drift guard, budgets and an information-gain stop.

Pure: no network. The fetcher is a stub.
"""

from __future__ import annotations

import dataclasses

import pytest

from friday.toolsets import research as R


def test_one_fact_question_stays_at_r1():
    assert R.route_depth("Who founded OpenAI?") == "R1"


def test_trivial_question_is_never_raised_to_r4_or_r5_even_when_asked():
    assert R.route_depth("What is the capital of France?", requested="R5") == "R1"
    assert R.route_depth("When was Python released?", requested="R4") == "R1"


def test_broad_question_routes_deep():
    q = ("Compare the leading vector databases in depth, analyze their trade-offs "
         "and write a comprehensive report on the state of the art")
    assert R.route_depth(q) in {"R4", "R5"}


def test_requested_depth_is_honoured_for_a_non_trivial_question():
    q = "Compare Postgres and MySQL replication"
    assert R.route_depth(q, requested="R2") == "R2"


def test_blank_question_needs_no_research():
    assert R.route_depth("   ") == "R0"


def test_every_depth_has_a_budget_and_budgets_grow():
    sizes = [R.DEPTH_BUDGETS[d].max_sources for d in ("R0", "R1", "R2", "R3", "R4", "R5")]
    assert sizes == sorted(sizes) and sizes[0] == 0 and sizes[1] == 1


def test_canonical_inquiry_is_immutable():
    inq = R.CanonicalInquiry.from_question("Compare Postgres and MySQL replication")
    with pytest.raises(dataclasses.FrozenInstanceError):
        inq.question = "something else"  # type: ignore[misc]
    assert "postgres" in inq.terms and "replication" in inq.terms
    assert isinstance(inq.terms, frozenset)


def test_off_inquiry_branch_is_dropped_and_on_inquiry_kept():
    inq = R.CanonicalInquiry.from_question("Compare Postgres and MySQL replication")
    branches = [
        {"url": "https://a", "title": "Postgres logical replication explained"},
        {"url": "https://b", "title": "Best celebrity diets of 2026"},
        {"url": "https://c", "title": "MySQL replication lag"},
    ]
    kept, rejected = R.filter_branches(inq, branches)
    assert [b["url"] for b in kept] == ["https://a", "https://c"]
    assert [b["url"] for b in rejected] == ["https://b"]
    assert rejected[0]["reason"] == "off_inquiry"


def _page(url, text):
    return {"url": url, "ok": True, "title": text, "markdown": text}


def test_explore_drops_drifted_branch_without_fetching_it():
    inq = R.CanonicalInquiry.from_question("Compare Postgres and MySQL replication")
    fetched: list[str] = []

    def fetch(branch):
        fetched.append(branch["url"])
        return _page(branch["url"], branch["title"])

    out = R.explore(inq, "R3", [
        {"url": "https://a", "title": "Postgres replication"},
        {"url": "https://b", "title": "Celebrity diets"},
    ], fetch)
    assert fetched == ["https://a"]
    assert [r["url"] for r in out["rejected"]] == ["https://b"]


def test_budget_stop_is_recorded():
    inq = R.CanonicalInquiry.from_question("Who founded OpenAI?")
    branches = [{"url": f"https://x/{i}", "title": f"OpenAI founders {i}"} for i in range(4)]

    out = R.explore(inq, "R1", branches, lambda b: _page(b["url"], b["title"]))
    assert len(out["pages"]) == 1
    assert out["stop"]["reason"] == "budget_sources"
    assert out["stop"]["depth"] == "R1"


def test_char_budget_stop_is_recorded():
    inq = R.CanonicalInquiry.from_question("Compare Postgres and MySQL replication")
    big = "postgres mysql replication " * 2000
    branches = [{"url": f"https://x/{i}", "title": "Postgres MySQL replication"} for i in range(3)]
    out = R.explore(inq, "R2", branches, lambda b: _page(b["url"], big))
    assert out["stop"]["reason"] == "budget_chars"


def test_stops_when_expected_information_gain_is_exhausted():
    inq = R.CanonicalInquiry.from_question("Compare Postgres and MySQL replication")
    same = "postgres mysql replication"
    branches = [{"url": f"https://x/{i}", "title": "Postgres MySQL replication"} for i in range(6)]
    out = R.explore(inq, "R4", branches, lambda b: _page(b["url"], same))
    assert out["stop"]["reason"] == "no_information_gain"
    assert len(out["pages"]) < 6


def test_exhausted_branches_record_completion():
    inq = R.CanonicalInquiry.from_question("Compare Postgres and MySQL replication")
    out = R.explore(inq, "R3", [{"url": "https://a", "title": "Postgres replication"}],
                    lambda b: _page(b["url"], "postgres replication details"))
    assert out["stop"]["reason"] == "exhausted"


def test_r0_fetches_nothing():
    inq = R.CanonicalInquiry.from_question("Postgres replication")
    out = R.explore(inq, "R0", [{"url": "https://a", "title": "Postgres replication"}],
                    lambda b: 1 / 0)
    assert out["pages"] == [] and out["stop"]["reason"] == "budget_sources"


def test_pages_naming_new_facts_are_gain_once_the_terms_are_covered():
    inq = R.CanonicalInquiry.from_question("Compare Postgres and MySQL replication")
    branches = [{"url": f"https://x/{i}", "title": "Postgres MySQL replication"} for i in range(6)]

    def fetch(b):
        i = int(b["url"].rsplit("/", 1)[1])
        return _page(b["url"], "postgres mysql replication: " + " ".join(
            f"replica Host{i}r{j} lagged {10 * i + j} seconds;" for j in range(5)))

    out = R.explore(inq, "R4", branches, fetch)
    assert out["stop"]["reason"] == "exhausted"
    assert len(out["pages"]) == 6
