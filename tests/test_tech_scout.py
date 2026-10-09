"""
Technology Scout (SCOUT-001): a trending repository is never installed on
popularity. A candidate is recorded, and only a measured need - a metric that
is currently short of its requirement, with evidence - lets it past the
provenance/licence review into quarantine intake. The quarantine intake is an
injected seam, so these tests drive it with a stub that records what it was
handed; nothing here installs, imports or activates anything.
"""
from __future__ import annotations

import pytest

from friday import fabric
from friday.toolsets import tech_scout as TS

SHA = "a" * 40


def repo(**kw):
    base = dict(name="shiny-agent", kind="repo", source="https://github.com/acme/shiny-agent",
                licence="MIT", commit=SHA, stars=91_000, integration_mode=fabric.SIDECAR)
    base.update(kw)
    return TS.Candidate(**base)


def need(**kw):
    base = dict(metric="recall@5", observed=0.61, required=0.80,
                evidence="arena:report-123")
    base.update(kw)
    return TS.MeasuredNeed(**base)


class Intake:
    """The quarantine intake: records every candidate it is handed."""

    def __init__(self):
        self.taken = []

    def __call__(self, candidate):
        self.taken.append(candidate)
        return f"quarantine/{candidate.name}"


@pytest.fixture
def scout(tmp_path):
    return TS.TechScout(tmp_path / "tech_scout.json")


def test_a_trending_repo_with_no_measured_need_is_archived_and_never_taken_in(scout):
    intake = Intake()
    before = set(fabric.registry())
    rec = scout.consider(repo(stars=250_000), need=None, intake=intake)
    assert rec.outcome == TS.ARCHIVED
    assert "measured need" in rec.reason
    assert intake.taken == []
    assert set(fabric.registry()) == before


def test_a_need_that_is_already_met_is_not_a_need(scout):
    intake = Intake()
    rec = scout.consider(repo(), need=need(observed=0.93), intake=intake)
    assert rec.outcome == TS.ARCHIVED and intake.taken == []


def test_popularity_never_decides_the_outcome(scout):
    intake = Intake()
    a = scout.consider(repo(stars=0), need=need(), intake=intake)
    b = scout.consider(repo(name="other", source="https://github.com/acme/other",
                            stars=500_000), need=need(), intake=intake)
    assert a.outcome == b.outcome == TS.QUARANTINED


def test_a_measured_need_sends_a_reviewed_candidate_to_quarantine_not_to_install(scout):
    intake = Intake()
    rec = scout.consider(repo(), need=need(), intake=intake)
    assert rec.outcome == TS.QUARANTINED
    assert rec.quarantine_ref == "quarantine/shiny-agent"
    assert [c.name for c in intake.taken] == ["shiny-agent"]
    assert rec.need.metric == "recall@5" and rec.need.evidence == "arena:report-123"
    assert "shiny-agent" not in {p for p in fabric.registry()}


def test_a_lower_is_better_metric_is_judged_the_right_way_round(scout):
    slow = need(metric="p95_ms", observed=900, required=400, higher_is_better=False)
    assert scout.consider(repo(), need=slow, intake=Intake()).outcome == TS.QUARANTINED
    fast = need(metric="p95_ms", observed=200, required=400, higher_is_better=False)
    assert scout.consider(repo(name="x", source="https://x"), need=fast,
                          intake=Intake()).outcome == TS.ARCHIVED


@pytest.mark.parametrize("override,why", [
    (dict(commit=""), "pin"),
    (dict(commit="main"), "pin"),
    (dict(licence=""), "licence"),
    (dict(licence="Proprietary-EULA"), "licence"),
    (dict(licence="AGPL-3.0", integration_mode=fabric.ADAPTER), "isolated"),
])
def test_a_candidate_that_fails_review_is_rejected_even_with_a_need(scout, override, why):
    intake = Intake()
    rec = scout.consider(repo(**override), need=need(), intake=intake)
    assert rec.outcome == TS.REJECTED and why in rec.reason
    assert intake.taken == []


def test_copyleft_is_allowed_only_behind_a_process_boundary(scout):
    rec = scout.consider(repo(licence="AGPL-3.0", integration_mode=fabric.SIDECAR),
                         need=need(), intake=Intake())
    assert rec.outcome == TS.QUARANTINED


def test_an_absent_intake_reports_unavailable_and_is_retried(scout):
    cand = repo()
    first = scout.consider(cand, need=need(), intake=None)
    assert first.outcome == TS.UNAVAILABLE
    intake = Intake()
    second = scout.consider(cand, need=need(), intake=intake)
    assert second.outcome == TS.QUARANTINED and len(intake.taken) == 1


def test_a_quarantined_candidate_is_not_taken_in_twice_and_survives_restart(tmp_path):
    path = tmp_path / "tech_scout.json"
    intake = Intake()
    TS.TechScout(path).consider(repo(), need=need(), intake=intake)
    again = TS.TechScout(path).consider(repo(), need=need(), intake=intake)
    assert again.outcome == TS.QUARANTINED and len(intake.taken) == 1
    assert TS.TechScout(path).record(repo().candidate_id).quarantine_ref == "quarantine/shiny-agent"


def test_an_intake_that_raises_is_unavailable_not_a_crash(scout):
    def broken(candidate):
        raise RuntimeError("quarantine root missing")
    rec = scout.consider(repo(), need=need(), intake=broken)
    assert rec.outcome == TS.UNAVAILABLE and "quarantine root missing" in rec.reason


def test_a_need_must_name_its_metric_and_its_evidence():
    with pytest.raises(ValueError):
        need(evidence=" ")
    with pytest.raises(ValueError):
        need(metric="")
    with pytest.raises(ValueError):
        repo(kind="rumour")


def test_a_corrupt_ledger_is_an_error_not_an_empty_one(tmp_path):
    path = tmp_path / "tech_scout.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(TS.ScoutStoreError):
        TS.TechScout(path).record("x")
