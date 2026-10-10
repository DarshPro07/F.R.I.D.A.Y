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


def test_something_we_wrote_covers_an_import_whatever_the_module_order(load):
    reg = load(_provider("a_import", ("fetch",)),
               _provider("z_ours", ("fetch",), upstream="", mode=fabric.BUILTIN))
    assert reg["z_ours"].integration_mode == fabric.BUILTIN
    assert reg["a_import"].integration_mode == fabric.REFERENCE_ONLY
    assert "z_ours answers fetch" in reg["a_import"].notes


def test_something_we_wrote_is_named_once_as_evidence(load):
    reg = load(_provider("ours", ("fetch", "crawl"), upstream="",
                         mode=fabric.BUILTIN),
               _provider("dup", ("fetch", "crawl")))
    assert reg["dup"].notes.count("ours answers") == 1


EVIDENCE ="scripts/bench_tool_disclosure.py"


@pytest.fixture
def bench_repo(monkeypatch, tmp_path):
    """A stand-in repo root holding one measurement file, `bench/result.txt`."""
    monkeypatch.setattr(fabric, "_REPO_ROOT", tmp_path.resolve())
    (tmp_path / "bench").mkdir()

    def _write(text: str) -> str:
        (tmp_path / "bench" / "result.txt").write_text(text, encoding="utf-8")
        return "bench/result.txt"
    return _write


def _measured(name: str, value: float, rival: str, rival_value: float,
              better: str = "higher") -> str:
    """An evidence file body holding one measurement line."""
    return (f"MEASURED pages/s: {name} = {value} vs {rival} = {rival_value} "
            f"({better} is better)\n")


@pytest.mark.parametrize("cite", [
    "evidence: {path}",
    "(evidence: {path}).",
])
def test_stated_benchmark_improvement_keeps_the_import(load, bench_repo, cite):
    path = bench_repo(_measured("better", 42.0, "old", 20.0))
    reg = load(_provider("old", ("fetch",)),
               _provider("better", ("fetch",),
                         notes=f"BENCHMARK_IMPROVEMENT: 2.1x pages/s, "
                               f"bench_scrape 2026-10-01 "
                               f"{cite.format(path=path)}"))
    assert reg["better"].integration_mode == fabric.ADAPTER
    assert "OVERLAP" not in reg["better"].notes


def test_evidence_may_name_the_upstream_instead_of_the_provider_id(
        load, bench_repo):
    path = bench_repo(_measured("someorg/repo", 42.0, "old", 20.0))
    reg = load(_provider("old", ("fetch",)),
               _provider("better", ("fetch",),
                         notes=f"BENCHMARK_IMPROVEMENT: 2.1x pages/s "
                               f"evidence: {path}"))
    assert reg["better"].integration_mode == fabric.ADAPTER


@pytest.mark.parametrize("text", ["", "old was measured at 40 pages/s"])
def test_evidence_that_never_names_the_provider_is_not_honoured(
        load, bench_repo, text):
    path = bench_repo(text)
    reg = load(_provider("old", ("fetch",)),
               _provider("claimant", ("fetch",),
                         notes=f"BENCHMARK_IMPROVEMENT: 2x evidence: {path}"))
    claimant = reg["claimant"]
    assert claimant.integration_mode == fabric.REFERENCE_ONLY
    assert "not honoured" in claimant.notes
    assert "never names 'claimant'" in claimant.notes


def test_a_lower_is_better_measurement_that_improves_keeps_the_import(
        load, bench_repo):
    path = bench_repo(_measured("better", 80.0, "old", 120.0, better="lower"))
    reg = load(_provider("old", ("fetch",)),
               _provider("better", ("fetch",),
                         notes=f"BENCHMARK_IMPROVEMENT: 80ms evidence: {path}"))
    assert reg["better"].integration_mode == fabric.ADAPTER


def test_evidence_naming_the_provider_without_a_measurement_is_not_honoured(
        load, bench_repo):
    path = bench_repo("claimant is the better scraper, trust me; old is slow\n")
    reg = load(_provider("old", ("fetch",)),
               _provider("claimant", ("fetch",),
                         notes=f"BENCHMARK_IMPROVEMENT: faster evidence: {path}"))
    claimant = reg["claimant"]
    assert claimant.integration_mode == fabric.REFERENCE_ONLY
    assert "not honoured" in claimant.notes
    assert "records no measurement" in claimant.notes


@pytest.mark.parametrize("value, rival_value, better", [
    (20.0, 42.0, "higher"),     # slower
    (42.0, 42.0, "higher"),     # no change
    (120.0, 80.0, "lower"),     # slower
    (80.0, 80.0, "lower"),      # no change
])
def test_a_measurement_that_shows_no_improvement_is_not_honoured(
        load, bench_repo, value, rival_value, better):
    path = bench_repo(_measured("claimant", value, "old", rival_value, better))
    reg = load(_provider("old", ("fetch",)),
               _provider("claimant", ("fetch",),
                         notes=f"BENCHMARK_IMPROVEMENT: 2x evidence: {path}"))
    claimant = reg["claimant"]
    assert claimant.integration_mode == fabric.REFERENCE_ONLY
    assert "shows no improvement" in claimant.notes


def test_a_measurement_against_a_provider_that_does_not_cover_it_is_refused(
        load, bench_repo):
    path = bench_repo(_measured("claimant", 99.0, "stranger", 1.0))
    reg = load(_provider("old", ("fetch",)),
               _provider("claimant", ("fetch",),
                         notes=f"BENCHMARK_IMPROVEMENT: 99x evidence: {path}"))
    claimant = reg["claimant"]
    assert claimant.integration_mode == fabric.REFERENCE_ONLY
    assert "compares" in claimant.notes and "'old'" in claimant.notes


def test_a_measurement_of_the_covering_provider_alone_is_refused(
        load, bench_repo):
    path = bench_repo(_measured("old", 99.0, "claimant", 1.0))
    reg = load(_provider("old", ("fetch",)),
               _provider("claimant", ("fetch",),
                         notes=f"BENCHMARK_IMPROVEMENT: 99x evidence: {path}"))
    assert reg["claimant"].integration_mode == fabric.REFERENCE_ONLY


def test_an_unrelated_file_in_the_repository_is_not_evidence(load):
    assert (fabric._REPO_ROOT / EVIDENCE).is_file()
    reg = load(_provider("old", ("fetch",)),
               _provider("claimant", ("fetch",),
                         notes=f"BENCHMARK_IMPROVEMENT: 2x evidence: {EVIDENCE}"))
    assert reg["claimant"].integration_mode == fabric.REFERENCE_ONLY


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
