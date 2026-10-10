"""
RSCH-002: FRIDAY's own benchmark for research providers.

Every FridayResearchProvider is run through the same `research_with` entry
over one fixed, hashed case set, and scored on quality (F1 of the hosts it
returns against the hosts the case expects), cost (characters it made FRIDAY
read) and latency. The scores are sealed into a report with a content id.

A provider preference is a claim, and a claim is accepted only when it cites a
report that run_benchmark itself produced in this process (the module-owned
registry below; a caller cannot supply or seal its own), that is intact, was
run on this case set, compared at least two providers, and ranks the claimed
provider first with some quality. An uncited claim is rejected, never defaulted.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import time
from typing import Callable, Mapping
from urllib.parse import urlsplit

from friday import contracts as c
from friday import research_providers as P


@dataclasses.dataclass(frozen=True)
class BenchCase:
    id: str
    question: str
    expected_hosts: tuple[str, ...]
    depth: str = "R2"


CASES: tuple[BenchCase, ...] = (
    BenchCase("replication", "Compare Postgres and MySQL logical replication trade-offs",
              ("www.postgresql.org", "dev.mysql.com")),
    BenchCase("consensus", "Compare Raft and Paxos consensus trade-offs",
              ("raft.github.io", "lamport.azurewebsites.net")),
    BenchCase("sqlite-journal", "Compare SQLite WAL and rollback journal modes",
              ("www.sqlite.org",)),
    BenchCase("python-concurrency", "Compare asyncio and threading for IO-bound Python workloads",
              ("docs.python.org", "realpython.com")),
)


def _digest(payload: object) -> str:
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def case_set_hash(cases: tuple[BenchCase, ...]) -> str:
    return _digest([dataclasses.asdict(case) for case in cases])


# Pinned: editing CASES without updating this makes every new report fail
# evaluate_preference, so the case set cannot drift under a standing preference.
CASE_SET_HASH = "a496ad1847edb0ca151139c9f8f4380c566fed64f55caf7d2b2e62338c25ee31"


@dataclasses.dataclass(frozen=True)
class ProviderScore:
    provider: str
    cases: int
    failures: int
    quality: float
    cost_chars: int
    latency_s: float


@dataclasses.dataclass(frozen=True)
class BenchReport:
    case_set_hash: str
    scores: tuple[ProviderScore, ...]
    report_id: str


@dataclasses.dataclass(frozen=True)
class PreferenceDecision:
    accepted: bool
    provider: str
    report_id: str
    reason: str


# Reports FRIDAY ran. The only way in is run_benchmark; evaluate_preference
# resolves a cited id here and nowhere else, so a hand-built (even correctly
# sealed) report is "not a benchmark FRIDAY ran".
_RAN: dict[str, "BenchReport"] = {}


def seal(report: BenchReport) -> str:
    """The content id of a report: its case-set hash and every score."""
    return _digest({"case_set_hash": report.case_set_hash,
                    "scores": [dataclasses.asdict(s) for s in report.scores]})


def verify_report(report: BenchReport) -> bool:
    return report.report_id == seal(report)


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def _f1(expected: tuple[str, ...], sources: tuple[dict, ...]) -> float:
    hosts = [_host(s.get("url", "")) for s in sources]
    want = set(expected)
    if not hosts or not want:
        return 0.0
    precision = sum(1 for h in hosts if h in want) / len(hosts)
    recall = len(want & set(hosts)) / len(want)
    return 0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall)


async def run_benchmark(providers: Mapping[str, P.FridayResearchProvider], *,
                        cases: tuple[BenchCase, ...] = CASES,
                        clock: Callable[[], float] = time.perf_counter) -> BenchReport:
    """Run every provider over every case through research_with and score it."""
    if not providers:
        raise ValueError("no providers to benchmark")
    scores = []
    for name in sorted(providers):
        quality = latency = 0.0
        cost = failures = 0
        for case in cases:
            run = c.Run.create("research-bench", capability="web")
            started = clock()
            result = await P.research_with(run, case.question, provider=name,
                                           requested=case.depth, providers=providers)
            latency += clock() - started
            # Cost is tallied for failed results too, so failing cheaply is not a saving.
            cost += sum(int(s.get("chars", 0) or 0) for s in result.sources + result.rejected)
            if result.status != c.SUCCEEDED:
                failures += 1
                continue
            quality += _f1(case.expected_hosts, result.sources)
        n = max(1, len(cases))
        scores.append(ProviderScore(provider=name, cases=len(cases), failures=failures,
                                    quality=round(quality / n, 4), cost_chars=cost,
                                    latency_s=round(latency / n, 6)))
    report = BenchReport(case_set_hash=case_set_hash(cases), scores=tuple(scores), report_id="")
    report = dataclasses.replace(report, report_id=seal(report))
    _RAN[report.report_id] = report
    return report


def _rank(score: ProviderScore) -> tuple[float, int, float]:
    return (-score.quality, score.cost_chars, score.latency_s)


def _reject(provider: str, report_id: str, reason: str) -> PreferenceDecision:
    return PreferenceDecision(False, provider, report_id, reason)


def evaluate_preference(claim: object) -> PreferenceDecision:
    """Accept a {"provider", "report_id"} claim only when a report FRIDAY ran backs it.

    Ties on quality fall to lower cost, then lower latency.
    """
    if not isinstance(claim, Mapping):
        return _reject("", "", "a preference claim must be a mapping")
    provider, report_id = claim.get("provider"), claim.get("report_id")
    if not isinstance(provider, str) or not provider:
        return _reject("", "", "a preference claim must name a provider")
    if not isinstance(report_id, str) or not report_id.strip():
        return _reject(provider, "", "an uncited preference is rejected: cite a benchmark report")
    report = _RAN.get(report_id)
    if report is None:
        return _reject(provider, report_id, "unknown report: it is not a benchmark FRIDAY ran")
    if not verify_report(report) or report.report_id != report_id:
        return _reject(provider, report_id, "report id does not match its contents")
    if report.case_set_hash != CASE_SET_HASH:
        return _reject(provider, report_id, "report was run on a different case set")
    ranked = sorted(report.scores, key=_rank)
    if provider not in {s.provider for s in ranked}:
        return _reject(provider, report_id, f"{provider!r} is not in report")
    if len(ranked) < 2:
        return _reject(provider, report_id, "report compares fewer than two providers")
    chosen = next(sc for sc in ranked if sc.provider == provider)
    if chosen.quality <= 0 or chosen.failures >= chosen.cases:
        return _reject(provider, report_id,
                       f"{provider!r} has no quality in the report (failed or found nothing)")
    if ranked[0].provider != provider:
        return _reject(provider, report_id,
                       f"{provider!r} is not the best provider in the report ({ranked[0].provider!r} is)")
    return PreferenceDecision(True, provider, report_id, "supported by the cited report")


def adopt_preference(claim: object) -> PreferenceDecision:
    """evaluate_preference, and on acceptance make it the default provider."""
    decision = evaluate_preference(claim)
    if decision.accepted:
        P.set_preferred(decision.provider, report_id=decision.report_id)
    return decision
