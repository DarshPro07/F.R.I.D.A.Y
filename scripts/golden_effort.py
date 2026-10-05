#!/usr/bin/env python3
"""
COG-002: does adaptive reasoning effort beat always-deep at the same quality?

Runs one fixed, human-labelled task suite through three policies - the real
adaptive one (`choose_route` -> tier -> `resolve_effort`), an always-deep
baseline, and an always-economy floor - and scores first-pass quality and cost.
No model is called: the policy under test is deterministic, and the quality
oracle is the label on each task (`min_tier`, the cheapest tier that does the
work right the first time), written independently of the keyword tables.

    python scripts/golden_effort.py            # evaluate, compare to the baseline
    python scripts/golden_effort.py --freeze   # rewrite the versioned baseline
    python scripts/golden_effort.py --db PATH  # keep the RouteOutcomes records

Cost model (PRD v7 gives no numbers; assumptions, revisit if wrong):
- price per token is 1:3:5 for economy:standard:deep, the haiku:sonnet:opus
  list-price ratio, and every task costs `TOKENS_PER_TASK` on every tier.
  Holding volume constant understates what deep thinking really burns, so it
  is conservative against the claim being tested.
- a task routed below its `min_tier` fails first pass and is repaired by a
  deep run, so it pays for the wasted attempt AND the repair (the H6 rule:
  rework is cost, never a discount on quality).

The verdict `beats_always_deep` needs all of: adaptive cost below always-deep,
first-pass quality at or above `QUALITY_THRESHOLD`, and no deep-labelled task
routed below deep. Every (policy, task) is recorded through
`execution_economics.RouteOutcomes` into a throwaway database (never the live
ledger) and read back, so the durable-ledger path is exercised on every run.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from friday import execution_economics as ee  # noqa: E402

BASELINE = ROOT / "docs" / "architecture" / "effort-policy-baseline.json"

#: Bump when SUITE, the cost model or the verdict rule changes.
SUITE_VERSION = 1

#: First-pass adequacy the adaptive policy must reach. Assumed: the PRD names
#: a threshold and never sets it.
QUALITY_THRESHOLD = 0.90

TOKENS_PER_TASK = 20_000
TIER_PRICE = {ee.TIER_ECONOMY: 1.0, ee.TIER_STANDARD: 3.0, ee.TIER_DEEP: 5.0}
TIER_RANK = {ee.TIER_ECONOMY: 0, ee.TIER_STANDARD: 1, ee.TIER_DEEP: 2}


@dataclass(frozen=True)
class Task:
    id: str
    text: str
    min_tier: str      # the label: cheapest tier that does this right first pass


E, S, D = ee.TIER_ECONOMY, ee.TIER_STANDARD, ee.TIER_DEEP

SUITE = (
    # Economy: mechanical lookups, direct answers, tiny bounded edits.
    Task("count-docs", "how many markdown files are in the docs folder", E),
    Task("locate-config", "where is the voice agent config file", E),
    Task("file-size", "what is the file size of logs/friday.log", E),
    Task("fix-typo", "fix the typo in the README heading", E),
    Task("docstring", "update the docstring on resolve_effort to mention "
                      "the none level", E),
    Task("bump-version", "bump version to 1.4.2 in pyproject.toml", E),
    Task("rename-var", "rename variable tmp to scratch in the digest loop", E),
    Task("capital", "what is the capital of France", E),
    Task("tokyo-time", "what time is it in Tokyo right now", E),
    Task("import-path", "change the import path in one line of the router", E),
    Task("default-timeout", "change the default timeout for the weather tool "
                            "to 8 seconds", E),
    # Standard: bounded engineering and review work in one area.
    Task("iso-parser", "write a function that parses ISO dates and add tests "
                       "for it", S),
    Task("pagination", "implement pagination for the objectives list "
                       "endpoint", S),
    Task("off-by-one", "fix the off-by-one error in the retry counter", S),
    Task("json-flag", "add a --json flag to the status report script", S),
    Task("dataclass-refactor", "refactor the digest formatter to use "
                               "dataclasses", S),
    Task("duration-helper", "create a helper that formats durations for the "
                            "status line", S),
    Task("parallel-docs", "update the docs and refresh the fixtures as two "
                          "independent workstreams in parallel", S),
    Task("log-audit", "audit the logging calls in the planner for leaked "
                      "prompt text", S),
    Task("pr-review", "review the pull request description and suggest "
                      "edits", S),
    Task("compare-stores", "compare three vector stores and recommend one "
                           "for the memory stack", S),
    Task("reconnect", "make the voice reconnect logic survive a flaky "
                      "network", S),
    Task("stale-cache", "stop the weather cache from serving stale results "
                        "after midnight", S),
    # Deep: high consequence, or reasoning across components.
    Task("exactly-once", "design the architecture for exactly-once delivery "
                         "of objective events", D),
    Task("scheduler-crash", "investigate why the scheduler crashes under "
                            "concurrent writers", D),
    Task("schema-migrate", "migrate the sessions table schema and backfill "
                           "the existing rows", D),
    Task("rotate-creds", "rotate the API credentials used by the payment "
                         "webhook", D),
    Task("tighten-perms", "tighten the permission checks on the file delete "
                          "tool", D),
    Task("prod-deploy", "deploy the new build to production", D),
    Task("nonce-proof", "prove the confirmation nonce cannot be replayed "
                        "across actions", D),
    Task("encrypt-tokens", "encrypt the stored session tokens at rest", D),
    Task("digest-race", "fix the race between the digest loop and the "
                        "objective writer", D),
    Task("auth-gate", "check that every endpoint enforces the auth gate", D),
)


def adaptive(task: Task) -> tuple[str, str]:
    """The policy under test, exactly as production routes: (level, tier)."""
    route = ee.choose_route(ee.classify_task(task.text))
    return route.level, route.tier


def always_deep(task: Task) -> tuple[str, str]:
    return ee.HERMES_DEEP, ee.TIER_DEEP


def always_economy(task: Task) -> tuple[str, str]:
    return ee.HERMES_SINGLE, ee.TIER_ECONOMY


POLICIES = {"adaptive": adaptive, "always_deep": always_deep,
            "always_economy": always_economy}


def evaluate(policy, *, prices: dict | None = None,
             suite: tuple[Task, ...] = SUITE) -> dict:
    """Score one policy (a name in POLICIES, or a callable Task -> (level,
    tier)). Returns {"summary": {...}, "rows": [...]}; summary is JSON-safe."""
    prices = prices or TIER_PRICE
    name = policy if isinstance(policy, str) else policy.__name__
    choose = POLICIES[policy] if isinstance(policy, str) else policy
    rows, tiers, efforts = [], {}, {}
    under, floor, over = [], [], []
    first_pass = adjusted = 0.0
    for task in suite:
        level, tier = choose(task)
        effort = ee.resolve_effort(tier)
        capable = TIER_RANK[tier] >= TIER_RANK[task.min_tier]
        cost = TOKENS_PER_TASK * prices[tier]
        first_pass += cost
        adjusted += cost
        if not capable:
            under.append(task.id)
            adjusted += TOKENS_PER_TASK * prices[ee.TIER_DEEP]   # the repair
            if task.min_tier == ee.TIER_DEEP:
                floor.append(task.id)
        elif TIER_RANK[tier] > TIER_RANK[task.min_tier]:
            over.append(task.id)
        tiers[tier] = tiers.get(tier, 0) + 1
        efforts[effort] = efforts.get(effort, 0) + 1
        rows.append({"task": task.id, "kind": ee.classify_task(task.text).kind,
                     "level": level, "tier": tier, "effort": effort,
                     "capable": capable, "cost": cost})
    return {
        "summary": {
            "policy": name, "tasks": len(suite),
            "quality": round((len(suite) - len(under)) / len(suite), 4),
            "under_provisioned": under, "floor_violations": floor,
            "over_provisioned": over,
            "cost_first_pass": first_pass, "cost_adjusted": adjusted,
            "tiers": dict(sorted(tiers.items())),
            "efforts": dict(sorted(efforts.items()))},
        "rows": rows}


def judge(adaptive_summary: dict, deep_summary: dict, *,
          threshold: float = QUALITY_THRESHOLD) -> dict:
    cheaper = (adaptive_summary["cost_adjusted"]
               < deep_summary["cost_adjusted"])
    quality_ok = adaptive_summary["quality"] >= threshold
    floor_ok = not adaptive_summary["floor_violations"]
    saved = 1 - (adaptive_summary["cost_adjusted"]
                 / deep_summary["cost_adjusted"])
    return {"cheaper": cheaper, "quality_ok": quality_ok,
            "floor_ok": floor_ok, "savings_pct": round(100 * saved, 1),
            "beats_always_deep": cheaper and quality_ok and floor_ok}


def record_outcomes(results: dict, db_path) -> dict:
    """Write every (policy, task) into RouteOutcomes and return each policy's
    summed `execution_value`, read back from the ledger. Token counts are the
    modelled price-weighted units, not measured usage; the `golden-eval`
    provider and the run-id prefix keep them apart from real runs."""
    outcomes = ee.RouteOutcomes(db_path)
    values = {}
    for name, result in results.items():
        prefix = f"golden-effort-v{SUITE_VERSION}/{name}/"
        for row in result["rows"]:
            outcomes.record(
                prefix + row["task"], task_class=row["kind"],
                route_level=row["level"], tier=row["tier"],
                provider="golden-eval", calls=1,
                prompt_tokens=int(row["cost"]),
                status="COMPLETE" if row["capable"] else "FAILED",
                verification="golden-label",
                rework=0 if row["capable"] else 1)
        values[name] = round(sum(
            outcomes.execution_value(r)
            for r in outcomes.by_run_prefix(prefix)), 2)
    return values


def build_report(db_path=None) -> dict:
    """Evaluate every policy, record to the ledger, and judge the result."""
    results = {name: evaluate(name) for name in POLICIES}
    if db_path is None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            values = record_outcomes(results, Path(tmp) / "golden.sqlite3")
    else:
        values = record_outcomes(results, db_path)
    policies = {name: {**result["summary"], "ledger_value": values[name]}
                for name, result in results.items()}
    return {
        "suite_version": SUITE_VERSION,
        "cost_model": {"tokens_per_task": TOKENS_PER_TASK,
                       "price": dict(sorted(TIER_PRICE.items())),
                       "repair": "deep run on top of the wasted attempt"},
        "quality_threshold": QUALITY_THRESHOLD,
        "policies": policies,
        "verdict": judge(policies["adaptive"], policies["always_deep"]),
        "rows": results["adaptive"]["rows"],
    }


def compare_to_baseline(report: dict, baseline: dict) -> list[str]:
    """Regressions of `report` against the frozen baseline; empty = none."""
    if report["suite_version"] != baseline["suite_version"]:
        return [f"suite version {report['suite_version']} != baseline "
                f"{baseline['suite_version']}: re-freeze after review"]
    now, was = report["policies"]["adaptive"], baseline["policies"]["adaptive"]
    problems = []
    if now["quality"] < was["quality"]:
        problems.append(f"adaptive quality fell {was['quality']} -> "
                        f"{now['quality']}")
    if now["cost_adjusted"] > was["cost_adjusted"]:
        problems.append(f"adaptive cost rose {was['cost_adjusted']} -> "
                        f"{now['cost_adjusted']}")
    new_floor = sorted(set(now["floor_violations"])
                       - set(was["floor_violations"]))
    if new_floor:
        problems.append(f"deep tasks routed below deep: {new_floor}")
    return problems


def frozen(report: dict) -> dict:
    """The part of a report that is versioned (drops the per-task rows)."""
    return {k: v for k, v in report.items() if k != "rows"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--freeze", action="store_true",
                        help="rewrite the versioned baseline")
    parser.add_argument("--db", help="keep RouteOutcomes records here")
    args = parser.parse_args()

    report = build_report(args.db)
    print("=" * 70)
    print(f"EFFORT POLICY EVAL  suite v{report['suite_version']}  "
          f"{report['policies']['adaptive']['tasks']} tasks")
    print("=" * 70)
    for name, p in report["policies"].items():
        print(f"  {name:15} quality {p['quality']:.4f}  cost "
              f"{p['cost_adjusted']:>10,.0f}  tiers {p['tiers']}")
    verdict = report["verdict"]
    adaptive_summary = report["policies"]["adaptive"]
    print(f"\n  under-provisioned: {adaptive_summary['under_provisioned']}")
    print(f"  over-provisioned : {adaptive_summary['over_provisioned']}")
    print(f"\n[{'PASS' if verdict['beats_always_deep'] else 'FAIL'}] adaptive "
          f"beats always-deep: {verdict['savings_pct']}% cheaper, quality "
          f"{adaptive_summary['quality']} (threshold {QUALITY_THRESHOLD}), "
          f"floor {'intact' if verdict['floor_ok'] else 'BROKEN'}")

    ok = verdict["beats_always_deep"]
    if args.freeze:
        if not ok:
            print("refusing to freeze a failing result")
            return 1
        with open(BASELINE, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(frozen(report), fh, indent=2, sort_keys=True)
            fh.write("\n")
        print(f"froze {BASELINE.relative_to(ROOT)}")
        return 0
    if BASELINE.exists():
        problems = compare_to_baseline(
            report, json.loads(BASELINE.read_text(encoding="utf-8")))
        for problem in problems:
            print(f"[FAIL] regression: {problem}")
        ok = ok and not problems
    else:
        print("[FAIL] no baseline; run with --freeze")
        ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
