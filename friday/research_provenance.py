"""
RSCH-003: research provenance and source independence.

Five copies of one article are one source, not five. Fetched pages are grouped
by near-duplicate text (word shingles compared by Jaccard similarity), each
group is one independent source, and every page gets a provenance record:
where it came from, whether it is the primary or a derivative of it, its date,
quality, freshness, supersession, and how many independent sources support
each claim.

Deterministic and local: stdlib only, no network, no model. Pages are the dicts
`friday.toolsets.research.crawl_all` returns (`url`, `ok`, `markdown`), with
the optional `published` (ISO date) and `supersedes` (list of URLs) a fetcher
may add.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re

SHINGLE = 5          # words per shingle
SIMILARITY = 0.7     # Jaccard at or above this: the same underlying text


def _shingles(text: str) -> frozenset[str]:
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    if len(words) <= SHINGLE:
        grams = [" ".join(words)]  # a short page matches only an identical one
    else:
        grams = [" ".join(words[i:i + SHINGLE]) for i in range(len(words) - SHINGLE + 1)]
    return frozenset(hashlib.sha1(g.encode()).hexdigest()[:16] for g in grams)


def _similar(a: frozenset[str], b: frozenset[str]) -> bool:
    return len(a & b) / len(a | b) >= SIMILARITY


def _date(value) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _cluster(pages: list[dict]) -> list[list[int]]:
    """Single-link groups of near-duplicate pages, as input indexes, in input order."""
    prints = [_shingles(p.get("markdown") or "") for p in pages]
    parent = list(range(len(pages)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(pages)):
        for j in range(i + 1, len(pages)):
            if _similar(prints[i], prints[j]):
                parent[find(j)] = find(i)
    groups: dict[int, list[int]] = {}
    for i in range(len(pages)):
        groups.setdefault(find(i), []).append(i)
    return sorted(groups.values(), key=lambda g: g[0])


def analyze(pages: list[dict], claims: list[dict] | None = None,
            today: str | None = None) -> dict:
    """
    Cluster `pages` into independent sources and build the provenance records.

    `claims` is a list of {"claim": str, "urls": [str]}; each gets the number of
    distinct independent sources behind it, not the number of URLs.
    """
    now = _date(today) if today else dt.date.today()
    usable = [p for p in pages if p.get("ok")]
    dates = [_date(p.get("published")) if p.get("published") else None for p in usable]

    clusters = _cluster(usable)
    cluster_of: dict[str, int] = {}
    records: dict[str, dict] = {}
    out_clusters: list[dict] = []
    for number, group in enumerate(clusters):
        primary = min(group, key=lambda i: (dates[i] or dt.date.max, i))
        root = usable[primary]["url"]
        out_clusters.append({"id": number, "primary": root,
                             "members": [usable[i]["url"] for i in group]})
        for i in group:
            url = usable[i]["url"]
            cluster_of[url] = number
            when = dates[i]
            records[url] = {
                "url": url,
                "cluster": number,
                "role": "primary" if i == primary else "derivative",
                "parent": None if i == primary else root,
                "date": when.isoformat() if when else None,
                "age_days": max((now - when).days, 0) if when and now else None,
                "quality": 0.5 + (0.25 if when else 0) + (0.25 if url.startswith("https://") else 0),
                "superseded_by": None,
            }
    for page in usable:
        for old in page.get("supersedes") or []:
            if old in records and old != page["url"]:
                records[old]["superseded_by"] = page["url"]

    support = []
    for item in claims or []:
        urls = list(item.get("urls") or [])
        known = [u for u in urls if u in cluster_of]
        independent = len({cluster_of[u] for u in known})
        support.append({
            "claim": item.get("claim", ""),
            "supporting_urls": known,
            "unresolved_urls": [u for u in urls if u not in cluster_of],
            "independent_sources": independent,
            "corroborated": independent >= 2,
        })
    return {"independent_sources": len(clusters), "clusters": out_clusters,
            "sources": [records[p["url"]] for p in usable], "claims": support}
