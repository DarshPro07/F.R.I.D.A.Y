"""
RSCH-002: a provider preference must be backed by FRIDAY's own benchmark.

The benchmark runs every registered FridayResearchProvider over one fixed,
hashed case set and records quality, cost and latency. Stand-in providers and a
fake clock keep it off the network and off the wall clock.
"""

from __future__ import annotations

import asyncio
import dataclasses
from urllib.parse import urlsplit

import pytest

from friday import contracts as c
from friday import research_bench as B
from friday import research_providers as P


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class StandIn:
    """Answers each benchmark question from a canned host list; no network."""

    def __init__(self, name, clock, delay, hosts_for, chars=100, fail=False):
        self.name = name
        self.clock = clock
        self.delay = delay
        self.hosts_for = hosts_for
        self.chars = chars
        self.fail = fail

    async def research(self, run, question, depth):
        self.clock.now += self.delay
        if self.fail:
            raise RuntimeError("upstream down")
        sources = tuple({"url": f"https://{h}/page", "title": question, "chars": self.chars}
                        for h in self.hosts_for(question))
        return P.ProviderResult(provider=self.name, question=question, depth=depth,
                                status=c.SUCCEEDED, sources=sources)


def expected(question):
    return next(case for case in B.CASES if case.question == question).expected_hosts


def exact(question):
    return expected(question)


def noisy(question):
    return expected(question)[:1] + ("noise.example", "spam.example")


def providers(clock):
    return {
        "exact": StandIn("exact", clock, delay=2.0, hosts_for=exact, chars=300),
        "noisy": StandIn("noisy", clock, delay=1.0, hosts_for=noisy, chars=100),
    }


def bench(registry, clock):
    return asyncio.run(B.run_benchmark(registry, clock=clock))


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def report(clock):
    return bench(providers(clock), clock)


@pytest.fixture(autouse=True)
def pinned_preference(monkeypatch):
    monkeypatch.setattr(P, "_PREFERENCE", P.Preference(provider="web", report_id=""))


def claim_for(report, provider="exact"):
    return {"provider": provider, "report_id": report.report_id}


def test_the_case_set_is_fixed_and_its_hash_is_pinned():
    assert B.case_set_hash(B.CASES) == B.CASE_SET_HASH
    edited = (dataclasses.replace(B.CASES[0], question=B.CASES[0].question + " today"),) + B.CASES[1:]
    assert B.case_set_hash(edited) != B.CASE_SET_HASH
    assert len({case.id for case in B.CASES}) == len(B.CASES) >= 3


def test_the_benchmark_scores_quality_cost_and_latency_per_provider(report):
    assert report.case_set_hash == B.CASE_SET_HASH
    scores = {s.provider: s for s in report.scores}
    assert set(scores) == {"exact", "noisy"}
    n = len(B.CASES)
    assert scores["exact"].cases == n and scores["exact"].failures == 0
    assert scores["exact"].quality == 1.0
    assert 0.0 < scores["noisy"].quality < 1.0
    assert scores["exact"].latency_s == 2.0 and scores["noisy"].latency_s == 1.0
    per_case_exact = sum(len(case.expected_hosts) for case in B.CASES) * 300
    assert scores["exact"].cost_chars == per_case_exact
    # off-inquiry sources never occur here, so the noisy provider pays for all three each case
    assert scores["noisy"].cost_chars == n * 3 * 100


def test_a_failing_provider_scores_zero_and_cannot_crash_the_benchmark(clock):
    registry = providers(clock)
    registry["down"] = StandIn("down", clock, delay=1.0, hosts_for=exact, fail=True)
    scores = {s.provider: s for s in bench(registry, clock).scores}
    assert scores["down"].failures == len(B.CASES)
    assert scores["down"].quality == 0.0
    assert scores["exact"].quality == 1.0


def test_the_report_is_sealed_and_deterministic(clock, report):
    other = Clock()
    again = bench(providers(other), other)
    assert again.report_id == report.report_id
    assert B.verify_report(report)
    forged = dataclasses.replace(
        report, scores=tuple(dataclasses.replace(s, quality=1.0) for s in report.scores))
    assert not B.verify_report(forged)


def test_an_empty_registry_has_nothing_to_benchmark(clock):
    with pytest.raises(ValueError, match="no providers"):
        bench({}, clock)


def test_a_preference_citing_a_supporting_report_is_accepted(report):
    decision = B.evaluate_preference(claim_for(report))
    assert decision.accepted, decision.reason
    assert decision.provider == "exact" and decision.report_id == report.report_id


def test_an_uncited_preference_is_rejected(report):
    for claim in ({"provider": "exact"}, {"provider": "exact", "report_id": ""},
                  {"provider": "exact", "report_id": None}, {"provider": "exact", "report_id": 7}):
        decision = B.evaluate_preference(claim)
        assert not decision.accepted
        assert "cite" in decision.reason


def test_a_preference_citing_an_unknown_report_is_rejected(report):
    decision = B.evaluate_preference({"provider": "exact", "report_id": "f" * 64})
    assert not decision.accepted and "unknown report" in decision.reason


def test_a_preference_the_report_does_not_support_is_rejected(report):
    decision = B.evaluate_preference(claim_for(report, "noisy"))
    assert not decision.accepted and "not the best" in decision.reason
    missing = B.evaluate_preference(claim_for(report, "ghost"))
    assert not missing.accepted and "not in report" in missing.reason


def test_a_report_for_another_case_set_is_rejected(clock):
    other_cases = (dataclasses.replace(B.CASES[0], question=B.CASES[0].question + " today"),) + B.CASES[1:]
    stale = asyncio.run(B.run_benchmark(providers(clock), cases=other_cases, clock=clock))
    assert stale.case_set_hash != B.CASE_SET_HASH
    decision = B.evaluate_preference(claim_for(stale))
    assert not decision.accepted and "different case set" in decision.reason


def test_a_tampered_report_in_the_registry_is_rejected(report, monkeypatch):
    forged = dataclasses.replace(
        report, scores=tuple(dataclasses.replace(s, quality=1.0) for s in report.scores))
    monkeypatch.setitem(B._RAN, report.report_id, forged)
    decision = B.evaluate_preference(claim_for(report, "noisy"))
    assert not decision.accepted and "does not match its contents" in decision.reason


def test_a_hand_built_sealed_report_that_was_never_run_is_rejected(report):
    fake_scores = (B.ProviderScore("web", 4, 0, 1.0, 10, 0.1),
                   B.ProviderScore("other", 4, 0, 0.5, 10, 0.1))
    fake = B.BenchReport(case_set_hash=B.CASE_SET_HASH, scores=fake_scores, report_id="")
    fake = dataclasses.replace(fake, report_id=B.seal(fake))
    assert B.verify_report(fake)
    decision = B.evaluate_preference({"provider": "web", "report_id": fake.report_id})
    assert not decision.accepted and "unknown report" in decision.reason


def test_a_single_provider_report_is_not_evidence(clock):
    solo = bench({"exact": StandIn("exact", clock, 1.0, exact)}, clock)
    decision = B.evaluate_preference(claim_for(solo))
    assert not decision.accepted and "fewer than two" in decision.reason


def test_a_provider_that_failed_every_case_cannot_win(clock):
    only = bench({"down": StandIn("down", clock, 1.0, exact, fail=True),
                  "empty": StandIn("empty", clock, 1.0, lambda q: ("elsewhere.example",), chars=500)},
                 clock)
    scores = {s.provider: s for s in only.scores}
    assert scores["down"].quality == 0 == scores["empty"].quality
    assert not B.evaluate_preference(claim_for(only, "down")).accepted
    assert not B.evaluate_preference(claim_for(only, "empty")).accepted


def test_failed_results_still_count_their_cost(clock):
    class FailsAfterReading(StandIn):
        async def research(self, run, question, depth):
            result = await super().research(run, question, depth)
            return dataclasses.replace(result, status=c.FAILED)

    scores = bench({"half": FailsAfterReading("half", clock, 1.0, exact, chars=50)}, clock).scores
    assert scores[0].failures == len(B.CASES) and scores[0].cost_chars > 0


def test_a_malformed_claim_is_data_not_a_crash(report):
    for claim in (None, "web", [], {"provider": 3, "report_id": report.report_id}):
        assert not B.evaluate_preference(claim).accepted


def test_adopting_an_accepted_preference_changes_the_default_provider(report, monkeypatch):
    monkeypatch.setitem(P.PROVIDERS, "exact", object())
    assert P.preferred_provider() == "web"
    decision = B.adopt_preference(claim_for(report))
    assert decision.accepted
    assert P.preferred_provider() == "exact"
    assert P.preference_report() == report.report_id


def test_a_rejected_preference_leaves_the_default_alone(report):
    decision = B.adopt_preference({"provider": "exact"})
    assert not decision.accepted
    assert P.preferred_provider() == "web" and P.preference_report() == ""


def test_the_provider_hook_refuses_an_uncited_preference():
    with pytest.raises(ValueError, match="benchmark report"):
        P.set_preferred("web", report_id="")


def test_the_provider_hook_refuses_a_citation_no_benchmark_supports(report, monkeypatch):
    monkeypatch.setitem(P.PROVIDERS, "exact", object())
    monkeypatch.setitem(P.PROVIDERS, "noisy", object())
    for name, rid in (("web", "not-a-report"), ("exact", "a" * 64),
                      ("noisy", report.report_id)):  # real report, but it does not back noisy
        with pytest.raises(ValueError, match="refused"):
            P.set_preferred(name, report_id=rid)
    assert P.preferred_provider() == "web" and P.preference_report() == ""


def test_research_with_uses_the_preferred_provider_when_none_is_named(clock, monkeypatch):
    canned = StandIn("canned", clock, 1.0, exact)
    own = bench({"canned": canned, "noisy": StandIn("noisy", clock, 1.0, noisy)}, clock)
    monkeypatch.setitem(P.PROVIDERS, "canned", canned)
    P.set_preferred("canned", report_id=own.report_id)
    run = c.Run.create("test", capability="web")
    result = asyncio.run(P.research_with(run, "Compare Raft and Paxos", requested="R2"))
    assert result.provider == "canned"


def test_hosts_in_the_case_set_are_plain_hostnames():
    for case in B.CASES:
        for host in case.expected_hosts:
            assert urlsplit(f"https://{host}/").hostname == host
