#!/usr/bin/env python3
"""
MODEL-003: the baseline a learned model router has to beat.

PRD v7 section 28 builds a learned router only after a baseline exists. This
is that baseline, as a versioned evaluation report, and the gate a learned
candidate will be judged by. No learned router is built here, and no model is
called: the baseline is the production router (`execution_economics.
choose_route` over `classify_task`), scored against the human-labelled
`min_tier` suite that `golden_effort` (COG-002) owns, reusing its cost model.

The suite is split by a hash of the task id into a development half, which a
candidate may be tuned on, and a locked holdout, which promotion is judged on.
A candidate is any `Callable[[str], str]` (task text -> tier). The arena
(`friday/arena.py`, kind MODEL_ROUTE) compares it with the baseline and two
floors, always-deep and always-economy; it promotes only if it clears all of:

    minimum_cases               enough labelled tasks were measured
    covers_every_tier           the suite has economy, standard and deep tasks
    quality_not_below_baseline  first-pass quality >= the baseline's
    cost_below_baseline         rework-adjusted cost strictly below the baseline's
    no_deep_task_below_deep     no deep-labelled task routed below deep

The baseline is the reference, so it cannot beat itself: it fails
`cost_below_baseline` by construction and the report's winner stays empty
until a candidate clears every gate.

    python scripts/golden_model_router.py                         # development split vs the frozen baseline
    python scripts/golden_model_router.py --holdout --i-am-promoting
    python scripts/golden_model_router.py --freeze --i-am-promoting

Opening the holdout spends it, so it needs `--i-am-promoting`, as in
`evaluate_selective.py`. `--freeze` records the baseline's holdout numbers and
therefore needs it too.

Assumptions, revisit if wrong: the 1:3:5 price ladder and the labels are
COG-002's, and the baseline was built against that same suite, so it is not
blind to it. `headroom` in the report says how much a better router could still
save on each split; where it is 0 nothing can beat the baseline on that split,
and the suite needs harder, fresh labelled tasks before it can certify a learned
router. `MINIMUM_CASES` is an assumption: the PRD names no number.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from friday import arena  # noqa: E402
from friday import execution_economics as ee  # noqa: E402
from scripts import golden_effort as effort  # noqa: E402

BASELINE = ROOT / "docs" / "architecture" / "model-router-baseline.json"

#: Bump when the split, the gates or the verdict rule change. The suite, the
#: labels and the cost model are `golden_effort`'s; re-freeze when its
#: SUITE_VERSION moves.
SUITE_VERSION = 1

DEVELOPMENT, HOLDOUT = "development", "holdout"
#: A task is held out when sha256(task id) % SPLIT_MODULUS == 0. Hashing the id
#: keeps a task in its split when the suite grows.
SPLIT_MODULUS = 3

#: Assumed: the fewest labelled tasks a comparison may rest on.
MINIMUM_CASES = 10

GATES = ("minimum_cases", "covers_every_tier", "quality_not_below_baseline",
         "cost_below_baseline", "no_deep_task_below_deep")

BASELINE_ID, ALWAYS_DEEP_ID, ALWAYS_ECONOMY_ID = (
    "baseline", "always_deep", "always_economy")
RESERVED = (BASELINE_ID, ALWAYS_DEEP_ID, ALWAYS_ECONOMY_ID)

#: A router takes the task text and names a tier (economy, standard or deep).
Router = Callable[[str], str]


def baseline_router(text: str) -> str:
    """The simple baseline: production routing, exactly as it runs."""
    return ee.choose_route(ee.classify_task(text)).tier


def always_deep_router(text: str) -> str:
    return ee.TIER_DEEP


def always_economy_router(text: str) -> str:
    return ee.TIER_ECONOMY


def oracle(task: effort.Task) -> tuple[str, str]:
    """The label itself: the cheapest tier that does the task right."""
    return "oracle", task.min_tier


def split_of(task: effort.Task) -> str:
    digest = hashlib.sha256(task.id.encode("utf-8")).digest()
    return HOLDOUT if int.from_bytes(digest, "big") % SPLIT_MODULUS == 0 \
        else DEVELOPMENT


def split_suite(suite: Iterable[effort.Task] = effort.SUITE
                ) -> dict[str, tuple[effort.Task, ...]]:
    suite = tuple(suite)
    return {name: tuple(t for t in suite if split_of(t) == name)
            for name in (DEVELOPMENT, HOLDOUT)}


def _policy(name: str, router: Router):
    """A `golden_effort.evaluate` policy (Task -> (level, tier)) from a router.
    A router that names something that is not a tier is a failed candidate."""
    def policy(task: effort.Task) -> tuple[str, str]:
        tier = router(task.text)
        if tier not in effort.TIER_RANK:
            raise ValueError(f"router {name!r} returned {tier!r}, not a tier")
        return "router", tier
    policy.__name__ = name
    return policy


def _summary(name: str, router: Router, tasks: tuple[effort.Task, ...]) -> dict:
    return effort.evaluate(_policy(name, router), suite=tasks)["summary"]


def headroom(tasks: Iterable[effort.Task], baseline: Router = baseline_router) -> dict:
    """What a label-perfect router would save over `baseline` on `tasks`. Zero
    means the baseline is already optimal there and nothing can beat it."""
    tasks = tuple(tasks)
    if not tasks:
        return {"oracle_cost": 0.0, "baseline_cost": 0.0, "headroom_pct": 0.0}
    best = effort.evaluate(oracle, suite=tasks)["summary"]["cost_adjusted"]
    now = _summary(BASELINE_ID, baseline, tasks)["cost_adjusted"]
    return {"oracle_cost": best, "baseline_cost": now,
            "headroom_pct": round(100 * (1 - best / now), 1)}


def _scorer(routers: Mapping[str, Router], tasks: tuple[effort.Task, ...],
            minimum_cases: int) -> arena.Scorer:
    if not tasks:       # nothing was measured: no divide, no promotion
        return lambda candidate_id: arena.Score(
            gates={"minimum_cases": False}, cases=0)
    base = _summary(BASELINE_ID, routers[BASELINE_ID], tasks)
    tiers = {t.min_tier for t in tasks}

    def scorer(candidate_id: str) -> arena.Score:
        s = base if candidate_id == BASELINE_ID else _summary(
            candidate_id, routers[candidate_id], tasks)
        savings = 1 - s["cost_adjusted"] / base["cost_adjusted"]
        return arena.Score(
            gates={
                "minimum_cases": len(tasks) >= minimum_cases,
                "covers_every_tier": tiers == set(effort.TIER_RANK),
                "quality_not_below_baseline": s["quality"] >= base["quality"],
                "cost_below_baseline": s["cost_adjusted"] < base["cost_adjusted"],
                "no_deep_task_below_deep": not s["floor_violations"]},
            cases=len(tasks), score=savings,
            metrics={"quality": s["quality"],
                     "cost_adjusted": s["cost_adjusted"],
                     "savings_vs_baseline": round(savings, 4),
                     "under_provisioned": len(s["under_provisioned"]),
                     "over_provisioned": len(s["over_provisioned"]),
                     "floor_violations": len(s["floor_violations"])})
    return scorer


def compare_routers(candidates: Mapping[str, Router], *,
                    tasks: Iterable[effort.Task],
                    baseline: Router = baseline_router,
                    minimum_cases: int = MINIMUM_CASES,
                    provenance: Mapping[str, object] | None = None
                    ) -> arena.ArenaReport:
    """The arena report for `candidates` against the baseline and the two floors
    on `tasks`. Record it in an `arena.ArenaStore` and pass its id to
    `arena.promote`; the baseline is listed first so it keeps ties."""
    clash = sorted(set(candidates) & set(RESERVED))
    if clash:
        raise ValueError(f"{clash} are reserved for the reference routers")
    routers = {BASELINE_ID: baseline, ALWAYS_DEEP_ID: always_deep_router,
               ALWAYS_ECONOMY_ID: always_economy_router, **candidates}
    return arena.compare(arena.ArenaKind.MODEL_ROUTE, list(routers),
                         _scorer(routers, tuple(tasks), minimum_cases),
                         provenance=provenance)


def _split_report(tasks: tuple[effort.Task, ...]) -> dict:
    report = compare_routers({}, tasks=tasks,
                             provenance={"commit": "", "date": ""})
    detail = _summary(BASELINE_ID, baseline_router, tasks)
    tiers: dict[str, int] = {}
    for task in tasks:
        tiers[task.min_tier] = tiers.get(task.min_tier, 0) + 1
    return {
        "tasks": len(tasks), "tiers": dict(sorted(tiers.items())),
        "headroom": headroom(tasks),
        "baseline_detail": {k: detail[k] for k in (
            "under_provisioned", "over_provisioned", "floor_violations")},
        "winner": report.winner,
        "results": [r.to_dict() for r in report.results]}


def build_report(splits: Iterable[str] = (DEVELOPMENT, HOLDOUT), *,
                 provenance: Mapping[str, object] | None = None) -> dict:
    """The baseline-only report for the named splits. JSON-safe."""
    wanted = tuple(splits)
    by_split = split_suite()
    return {
        "suite_version": SUITE_VERSION,
        "effort_suite_version": effort.SUITE_VERSION,
        "learned_router": "none built",
        "provenance": dict(provenance) if provenance else None,
        "split_rule": f"sha256(task id) % {SPLIT_MODULUS} == 0 -> {HOLDOUT}",
        "minimum_cases": MINIMUM_CASES,
        "gates": list(GATES),
        "cost_model": {"tokens_per_task": effort.TOKENS_PER_TASK,
                       "price": dict(sorted(effort.TIER_PRICE.items())),
                       "repair": "deep run on top of the wasted attempt"},
        "splits": {name: _split_report(by_split[name]) for name in wanted},
    }


def measurements(report: dict) -> dict:
    """The part of a report that must reproduce: everything but provenance."""
    return {k: v for k, v in report.items() if k != "provenance"}


def _baseline_result(split: dict) -> dict:
    return next(r for r in split["results"] if r["candidate_id"] == BASELINE_ID)


def compare_to_baseline(report: dict, baseline: dict) -> list[str]:
    """Regressions of the baseline router against the frozen report, for the
    splits `report` holds; empty = none."""
    for key in ("suite_version", "effort_suite_version"):
        if report[key] != baseline[key]:
            return [f"suite version mismatch on {key}: {report[key]} != "
                    f"frozen {baseline[key]}; re-freeze after review"]
    problems = []
    for name, now in report["splits"].items():
        was = baseline["splits"].get(name)
        if was is None:
            problems.append(f"{name}: not in the frozen baseline")
            continue
        m_now, m_was = (_baseline_result(s)["metrics"] for s in (now, was))
        if m_now["quality"] < m_was["quality"]:
            problems.append(f"{name}: baseline quality fell "
                            f"{m_was['quality']} -> {m_now['quality']}")
        if m_now["cost_adjusted"] > m_was["cost_adjusted"]:
            problems.append(f"{name}: baseline cost rose "
                            f"{m_was['cost_adjusted']} -> {m_now['cost_adjusted']}")
        new_floor = sorted(set(now["baseline_detail"]["floor_violations"])
                           - set(was["baseline_detail"]["floor_violations"]))
        if new_floor:
            problems.append(f"{name}: deep tasks routed below deep: {new_floor}")
    return problems


def _intrinsic_failures(report: dict) -> list[str]:
    """Gates the baseline fails for a reason other than being unable to beat
    itself. A report with these cannot be a baseline to freeze."""
    failures = []
    for name, split in report["splits"].items():
        for gate in _baseline_result(split)["failed_gates"]:
            if gate != "cost_below_baseline":
                failures.append(f"{name}: baseline fails {gate}")
    return failures


def _git_provenance() -> dict:
    from friday import golden
    stamp = golden.provenance()
    return {"commit": stamp["commit"], "date": stamp["date"]}


def _print(report: dict) -> None:
    print("=" * 72)
    print(f"MODEL ROUTER BASELINE  suite v{report['suite_version']}  "
          f"learned router: {report['learned_router']}")
    print("=" * 72)
    for name, split in report["splits"].items():
        print(f"\n  {name}: {split['tasks']} tasks {split['tiers']}  "
              f"headroom {split['headroom']['headroom_pct']}%  "
              f"winner {split['winner'] or 'none'}")
        for r in split["results"]:
            m = r["metrics"]
            print(f"    {r['candidate_id']:15} quality {m['quality']:.4f}  cost "
                  f"{m['cost_adjusted']:>10,.0f}  failed {r['failed_gates']}")
        detail = split["baseline_detail"]
        print(f"    baseline under-provisioned: {detail['under_provisioned']}")
        print(f"    baseline over-provisioned : {detail['over_provisioned']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--holdout", action="store_true",
                        help="also score the locked holdout split")
    parser.add_argument("--freeze", action="store_true",
                        help="rewrite the versioned baseline (opens the holdout)")
    parser.add_argument("--i-am-promoting", action="store_true",
                        help="required to open the holdout")
    args = parser.parse_args(argv)

    opens_holdout = args.holdout or args.freeze
    if opens_holdout and not args.i_am_promoting:
        print("The holdout is locked. Opening it spends it: once its failures\n"
              "have shaped a router it no longer measures anything. Say so with\n"
              "--i-am-promoting.")
        return 2

    splits = (DEVELOPMENT, HOLDOUT) if opens_holdout else (DEVELOPMENT,)
    report = build_report(splits, provenance=_git_provenance()
                          if args.freeze else None)
    _print(report)

    if args.freeze:
        failures = _intrinsic_failures(report)
        for failure in failures:
            print(f"[FAIL] {failure}")
        if failures:
            print("refusing to freeze a baseline that fails its own gates")
            return 1
        with open(BASELINE, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(report, fh, indent=2, sort_keys=True)
            fh.write("\n")
        print(f"froze {BASELINE.relative_to(ROOT)}")
        return 0

    if not BASELINE.exists():
        print("[FAIL] no baseline; run with --freeze --i-am-promoting")
        return 1
    problems = compare_to_baseline(
        report, json.loads(BASELINE.read_text(encoding="utf-8")))
    for problem in problems:
        print(f"[FAIL] regression: {problem}")
    if not problems:
        print("\n[OK] the baseline router is unchanged against the frozen report")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
