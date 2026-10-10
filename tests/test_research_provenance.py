"""
RSCH-003: research provenance and source independence.

Pure: no network, no model. Pages are the dicts `research.crawl_all` returns.
"""

from __future__ import annotations

from friday import research_provenance as P

ARTICLE = (
    "The council voted on Tuesday to approve the new harbour bridge after "
    "three years of debate. Engineers said construction would begin in spring "
    "and last roughly eighteen months, with the first lane opening to traffic "
    "before the end of the following year. Opponents argued the cost was too "
    "high, while supporters pointed to the jobs it would create across the "
    "region and the relief it would bring to the congested ferry route."
)
OTHER = (
    "Researchers at the institute published a study of coral bleaching along "
    "the northern reef, finding that warmer water arrived earlier each season. "
    "The survey covered forty sites over a decade and recorded the steepest "
    "decline in shallow lagoons, where recovery between heatwaves has slowed "
    "to a crawl according to the lead author."
)


def page(url, body, **extra):
    return {"url": url, "ok": True, "title": "t", "markdown": body, **extra}


def five_copies():
    return [
        page("https://a.example/news/bridge", ARTICLE, published="2026-10-01"),
        page("https://b.example/story", "Share this story\n\n" + ARTICLE),
        page("https://c.example/p/1", ARTICLE + "\n\nSubscribe to our newsletter."),
        page("https://d.example/x", ARTICLE.replace("Tuesday", "Wednesday")),
        page("https://e.example/y", "Reposted from A. " + ARTICLE),
    ]


def test_five_copied_articles_count_as_one_independent_source():
    report = P.analyze(five_copies(), today="2026-10-10")
    assert report["independent_sources"] == 1
    assert len(report["clusters"]) == 1
    assert len(report["clusters"][0]["members"]) == 5


def test_distinct_articles_stay_independent():
    pages = [page("https://a.example/1", ARTICLE), page("https://b.example/2", OTHER)]
    assert P.analyze(pages)["independent_sources"] == 2


def test_copies_plus_a_distinct_article_count_two():
    pages = five_copies() + [page("https://z.example/reef", OTHER)]
    assert P.analyze(pages)["independent_sources"] == 2


def test_failed_pages_are_not_sources():
    pages = [page("https://a.example/1", ARTICLE), {"url": "https://b.example/2", "ok": False}]
    report = P.analyze(pages)
    assert report["independent_sources"] == 1
    assert [r["url"] for r in report["sources"]] == ["https://a.example/1"]


def test_earliest_dated_copy_is_primary_and_the_rest_name_it_as_parent():
    report = P.analyze(five_copies(), today="2026-10-10")
    by_url = {r["url"]: r for r in report["sources"]}
    root = by_url["https://a.example/news/bridge"]
    assert root["role"] == "primary" and root["parent"] is None
    for url, record in by_url.items():
        if url != root["url"]:
            assert record["role"] == "derivative"
            assert record["parent"] == root["url"]


def test_without_dates_the_first_fetched_copy_is_primary():
    pages = [page("https://b.example/s", ARTICLE), page("https://a.example/s", ARTICLE)]
    by_url = {r["url"]: r for r in P.analyze(pages)["sources"]}
    assert by_url["https://b.example/s"]["role"] == "primary"
    assert by_url["https://a.example/s"]["parent"] == "https://b.example/s"


def test_record_carries_date_freshness_and_quality():
    pages = [page("https://a.example/1", ARTICLE, published="2026-10-01"),
             page("https://b.example/2", OTHER)]
    by_url = {r["url"]: r for r in P.analyze(pages, today="2026-10-10")["sources"]}
    dated, undated = by_url["https://a.example/1"], by_url["https://b.example/2"]
    assert dated["date"] == "2026-10-01" and dated["age_days"] == 9
    assert undated["date"] is None and undated["age_days"] is None
    assert 0 <= undated["quality"] < dated["quality"] <= 1


def test_declared_supersession_is_recorded_on_the_older_source():
    pages = [page("https://a.example/v1", ARTICLE),
             page("https://a.example/v2", OTHER, supersedes=["https://a.example/v1"])]
    by_url = {r["url"]: r for r in P.analyze(pages)["sources"]}
    assert by_url["https://a.example/v1"]["superseded_by"] == "https://a.example/v2"
    assert by_url["https://a.example/v2"]["superseded_by"] is None


def test_a_claim_backed_only_by_copies_has_one_independent_support():
    claims = [{"claim": "The bridge was approved.",
               "urls": [p["url"] for p in five_copies()]}]
    report = P.analyze(five_copies(), claims=claims)
    support = report["claims"][0]
    assert support["independent_sources"] == 1
    assert support["corroborated"] is False
    assert len(support["supporting_urls"]) == 5


def test_a_claim_backed_by_two_independent_sources_is_corroborated():
    pages = five_copies() + [page("https://z.example/reef", OTHER)]
    claims = [{"claim": "Both.", "urls": ["https://c.example/p/1", "https://z.example/reef"]}]
    support = P.analyze(pages, claims=claims)["claims"][0]
    assert support["independent_sources"] == 2 and support["corroborated"] is True


def test_claim_citing_an_unfetched_url_does_not_count_it():
    claims = [{"claim": "x", "urls": ["https://nowhere.example/"]}]
    support = P.analyze(five_copies(), claims=claims)["claims"][0]
    assert support["independent_sources"] == 0
    assert support["unresolved_urls"] == ["https://nowhere.example/"]


def test_short_pages_cluster_only_when_identical():
    pages = [page("https://a.example/1", "Not found"), page("https://b.example/2", "Not found"),
             page("https://c.example/3", "Access denied")]
    assert P.analyze(pages)["independent_sources"] == 2


def test_analysis_is_deterministic():
    assert P.analyze(five_copies(), today="2026-10-10") == P.analyze(five_copies(), today="2026-10-10")
