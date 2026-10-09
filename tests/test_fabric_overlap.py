"""
CAP-FORGE-011: a repository whose capabilities an already-registered provider
covers is recorded REFERENCE_ONLY with the overlap as evidence, unless a
benchmark shows an improvement.
"""

from __future__ import annotations

import pytest

from friday import fabric

PIN = "a" * 40


def _provider(pid: str, ops: tuple[str, ...], *, family: str = "scraping",
              upstream: str = "someorg/repo", mode: str = fabric.ADAPTER,
              notes: str = "") -> fabric.Provider:
    return fabric.Provider(
        id=pid, family=family, upstream=upstream, operations=ops,
        risk="low", license_mode=fabric.PERMISSIVE, integration_mode=mode,
        commit=PIN if upstream else "", notes=notes)


@pytest.fixture
def load(monkeypatch):
    """Load a registry made of exactly these providers, in this order."""
    def _load(*providers: fabric.Provider) -> dict[str, fabric.Provider]:
        monkeypatch.setattr(
            fabric, "_discover", lambda: {p.id: p for p in providers})
        return fabric.reload()
    yield _load
    monkeypatch.undo()
    fabric.reload()


# --- find_overlaps ---------------------------------------------------------


def test_find_overlaps_names_the_provider_and_operation():
    mine = _provider("new", ("fetch", "crawl"))
    other = _provider("old", ("fetch", "screenshot"))
    assert fabric.find_overlaps(mine, [other]) == [("old", "fetch")]


def test_find_overlaps_ignores_other_families_and_reference_only_and_self():
    mine = _provider("new", ("fetch",))
    other_family = _provider("elsewhere", ("fetch",), family="search")
    paper = _provider("paper", ("fetch",), mode=fabric.REFERENCE_ONLY)
    assert fabric.find_overlaps(mine, [other_family, paper, mine]) == []


def test_find_overlaps_defaults_to_the_registry(load):
    load(_provider("old", ("fetch",)), _provider("new", ("fetch", "crawl")))
    assert fabric.find_overlaps(fabric.get("new")) == [("old", "fetch")]


# --- the policy, through registry() ----------------------------------------


def test_fully_covered_repository_is_recorded_reference_only_with_evidence(load):
    reg = load(_provider("old", ("fetch", "crawl")),
               _provider("dup", ("fetch", "crawl"), notes="Same idea."))
    assert reg["old"].integration_mode == fabric.ADAPTER
    dup = reg["dup"]
    assert dup.integration_mode == fabric.REFERENCE_ONLY
    assert dup.notes.startswith("Same idea.")
    assert "OVERLAP" in dup.notes and "declared ADAPTER" in dup.notes
    assert "old answers fetch, crawl" in dup.notes
    assert fabric.state("dup") == fabric.REFERENCE_ONLY


def test_recorded_provider_is_never_a_routing_candidate(load):
    load(_provider("old", ("fetch",)), _provider("dup", ("fetch",)))
    assert [p.id for p in fabric.candidates("scraping", "fetch")] == ["old"]


def test_repository_with_an_operation_nobody_answers_loads_normally(load):
    reg = load(_provider("old", ("fetch",)),
               _provider("new", ("fetch", "crawl")))
    assert reg["new"].integration_mode == fabric.ADAPTER
    assert "OVERLAP" not in reg["new"].notes


def test_coverage_may_come_from_several_providers_together(load):
    reg = load(_provider("a", ("fetch",)), _provider("b", ("crawl",)),
               _provider("both", ("fetch", "crawl")))
    assert reg["both"].integration_mode == fabric.REFERENCE_ONLY
    assert "a answers fetch" in reg["both"].notes
    assert "b answers crawl" in reg["both"].notes


def test_the_earlier_provider_is_the_one_that_stays(load):
    reg = load(_provider("first", ("fetch",)), _provider("second", ("fetch",)))
    assert reg["first"].integration_mode == fabric.ADAPTER
    assert reg["second"].integration_mode == fabric.REFERENCE_ONLY


def test_a_recorded_provider_does_not_cover_for_a_later_one(load):
    reg = load(_provider("a", ("fetch",)), _provider("dup", ("fetch",)),
               _provider("later", ("fetch", "crawl")))
    assert reg["later"].integration_mode == fabric.ADAPTER


def test_same_operations_in_another_family_do_not_overlap(load):
    reg = load(_provider("old", ("fetch",)),
               _provider("new", ("fetch",), family="search"))
    assert reg["new"].integration_mode == fabric.ADAPTER


def test_something_we_wrote_is_not_an_import(load):
    reg = load(_provider("old", ("fetch",)),
               _provider("ours", ("fetch",), upstream="", mode=fabric.BUILTIN))
    assert reg["ours"].integration_mode == fabric.BUILTIN


EVIDENCE = "scripts/bench_tool_disclosure.py"


@pytest.mark.parametrize("cite", [
    f"evidence: {EVIDENCE}",
    f"(evidence: {EVIDENCE}).",
])
def test_stated_benchmark_improvement_keeps_the_import(load, cite):
    reg = load(_provider("old", ("fetch",)),
               _provider("better", ("fetch",),
                         notes=f"BENCHMARK_IMPROVEMENT: 2.1x pages/s, "
                               f"bench_scrape 2026-10-01 {cite}"))
    assert reg["better"].integration_mode == fabric.ADAPTER
    assert "OVERLAP" not in reg["better"].notes


def test_a_benchmark_claim_without_evidence_is_not_honoured(load):
    reg = load(_provider("old", ("fetch",)),
               _provider("claimant", ("fetch",),
                         notes="BENCHMARK_IMPROVEMENT: 2.1x pages/s"))
    claimant = reg["claimant"]
    assert claimant.integration_mode == fabric.REFERENCE_ONLY
    assert "not honoured" in claimant.notes
    assert "cites no 'evidence:" in claimant.notes


def test_a_benchmark_claim_whose_evidence_file_is_missing_is_not_honoured(load):
    reg = load(_provider("old", ("fetch",)),
               _provider("claimant", ("fetch",),
                         notes="BENCHMARK_IMPROVEMENT: 2.1x pages/s "
                               "evidence: scripts/no_such_benchmark.py"))
    claimant = reg["claimant"]
    assert claimant.integration_mode == fabric.REFERENCE_ONLY
    assert "'scripts/no_such_benchmark.py' is not a file" in claimant.notes


@pytest.mark.parametrize("cite", ["scripts", "docs/plans/ctx-002/../../.."])
def test_benchmark_evidence_must_be_a_file_inside_the_repository(load, cite):
    reg = load(_provider("old", ("fetch",)),
               _provider("claimant", ("fetch",),
                         notes=f"BENCHMARK_IMPROVEMENT: 2x evidence: {cite}"))
    assert reg["claimant"].integration_mode == fabric.REFERENCE_ONLY


def test_benchmark_evidence_outside_the_repository_is_not_followed(
        load, tmp_path):
    outside = tmp_path / "bench.txt"
    outside.write_text("2x faster")
    reg = load(_provider("old", ("fetch",)),
               _provider("claimant", ("fetch",),
                         notes=f"BENCHMARK_IMPROVEMENT: 2x "
                               f"evidence: {outside}"))
    assert outside.is_file()
    assert reg["claimant"].integration_mode == fabric.REFERENCE_ONLY


def test_a_recorded_providers_own_note_is_not_a_benchmark_claim(load):
    reg = load(_provider("old", ("fetch",)), _provider("dup", ("fetch",)))
    assert not fabric.has_benchmark_improvement(reg["dup"])


@pytest.mark.parametrize("notes", [
    "BENCHMARK_IMPROVEMENT:",
    "BENCHMARK_IMPROVEMENT:   ",
    "benchmarked against old and not promoted",
])
def test_an_empty_or_negative_benchmark_claim_is_not_an_improvement(load, notes):
    reg = load(_provider("old", ("fetch",)),
               _provider("claimant", ("fetch",), notes=notes))
    assert reg["claimant"].integration_mode == fabric.REFERENCE_ONLY


def test_an_already_reference_only_provider_is_left_as_declared(load):
    reg = load(_provider("old", ("fetch",)),
               _provider("paper", ("fetch",), mode=fabric.REFERENCE_ONLY,
                         notes="Read for patterns."))
    assert reg["paper"].notes == "Read for patterns."


# --- which provider is "existing" ------------------------------------------


def test_discovery_order_does_not_depend_on_what_pkgutil_yields(monkeypatch):
    natural = list(fabric._discover())
    assert len(natural) > 1
    real = fabric.pkgutil.iter_modules
    monkeypatch.setattr(fabric.pkgutil, "iter_modules",
                        lambda path: reversed(list(real(path))))
    assert list(fabric._discover()) == natural


# --- the real registry -----------------------------------------------------


def test_no_shipped_provider_is_recorded_by_the_overlap_policy():
    fabric.reload()
    recorded = [p.id for p in fabric.registry().values()
                if "OVERLAP:" in p.notes]
    assert recorded == [], (
        f"{recorded} are now REFERENCE_ONLY because earlier providers cover "
        f"them; add a BENCHMARK_IMPROVEMENT line to notes, or accept the "
        f"recording")


def test_accepted_overlaps_are_real_and_still_runnable():
    reg = fabric.reload()
    order = list(reg)
    for pid, reason in fabric.ACCEPTED_OVERLAPS.items():
        assert reason.strip(), f"{pid}: an accepted overlap needs a reason"
        provider = reg[pid]
        assert provider.integration_mode != fabric.REFERENCE_ONLY
        earlier = [reg[i] for i in order[:order.index(pid)]]
        covered = {op for _, op in fabric.find_overlaps(provider, earlier)}
        assert covered >= set(provider.operations), (
            f"{pid} no longer overlaps an earlier provider; drop it from "
            f"ACCEPTED_OVERLAPS")
