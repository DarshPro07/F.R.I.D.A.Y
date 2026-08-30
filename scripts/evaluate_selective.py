"""
Measure the selective router: coverage, precision, and what it costs to be wrong.

Run:  .venv/Scripts/python.exe scripts/evaluate_selective.py development
      .venv/Scripts/python.exe scripts/evaluate_selective.py calibration --curve
      .venv/Scripts/python.exe scripts/evaluate_selective.py holdout --i-am-promoting

Accuracy is not the number. These are:

    FALSE ACTION        it acted, and the action was wrong
    DANGEROUS           the wrong action ends something
    FALSE ABSTENTION    it declined something it could have done - a cost, but
                        the cost is one cloud turn, which is today's bill

A false abstention is a missed saving. A false action is somebody's work. They
are not commensurable and are never added together.

The holdout is locked. Opening it requires saying so on the command line,
because the moment its failures are inspected it stops being held out and a new
one is needed for the next promotion decision.
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
import statistics
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from friday import reflex as X                  # noqa: E402
from friday import selective as SEL             # noqa: E402

CORPUS = pathlib.Path(__file__).resolve().parent.parent / "data" / "corpus"
ABSTAIN = "ABSTAIN"


def load(split: str) -> list[dict]:
    path = CORPUS / f"{split}.jsonl"
    if not path.exists():
        raise SystemExit(f"no corpus at {path}; run scripts/build_corpus.py")
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def judge(records: list[dict], *, margin: float | None = None) -> dict:
    """Run the router over a split and count everything that matters."""
    if margin is not None:
        original, SEL.MARGIN = SEL.MARGIN, margin

    tally: collections.Counter = collections.Counter()
    by_category: dict[str, collections.Counter] = collections.defaultdict(
        collections.Counter)
    latencies: list[float] = []
    false_actions: list[dict] = []
    dangerous: list[dict] = []
    abstentions: collections.Counter = collections.Counter()
    blame: collections.Counter = collections.Counter()

    try:
        for record in records:
            context = SEL.Context(active=dict(
                (record.get("context") or {}).get("active") or {}))
            decision = SEL.decide(record["text"], context=context)
            latencies.append(decision.milliseconds)
            wanted = record["expect"]
            category = record["category"]

            if decision.routes:
                tally["acted"] += 1
                by_category[category]["acted"] += 1
                if wanted == ABSTAIN:
                    tally["false_action"] += 1
                    by_category[category]["false_action"] += 1
                    entry = {"text": record["text"], "acted": decision.capability,
                             "wanted": wanted, "category": category}
                    false_actions.append(entry)
                    if X.is_dangerous(decision.capability):
                        dangerous.append(entry)
                elif decision.capability == wanted:
                    tally["correct"] += 1
                    by_category[category]["correct"] += 1
                    if _arguments_ok(decision, record):
                        tally["correct_arguments"] += 1
                else:
                    tally["false_action"] += 1
                    by_category[category]["false_action"] += 1
                    entry = {"text": record["text"], "acted": decision.capability,
                             "wanted": wanted, "category": category}
                    false_actions.append(entry)
                    if X.is_dangerous(decision.capability):
                        dangerous.append(entry)
            else:
                tally["abstained"] += 1
                abstentions[decision.abstained] += 1
                blame[decision.blame] += 1
                by_category[category]["abstained"] += 1
                if wanted == ABSTAIN:
                    tally["correct_abstention"] += 1
                    by_category[category]["correct_abstention"] += 1
                else:
                    tally["false_abstention"] += 1
                    by_category[category]["false_abstention"] += 1
    finally:
        if margin is not None:
            SEL.MARGIN = original

    total = len(records)
    acted = tally["acted"]
    return {
        "total": total,
        "coverage": round(acted / total, 4) if total else 0.0,
        "acted": acted,
        "correct": tally["correct"],
        "correct_arguments": tally["correct_arguments"],
        "precision": round(tally["correct"] / acted, 4) if acted else 0.0,
        "false_actions": tally["false_action"],
        "false_action_rate": round(tally["false_action"] / total, 5)
        if total else 0.0,
        "dangerous": len(dangerous),
        "correct_abstention": tally["correct_abstention"],
        "false_abstention": tally["false_abstention"],
        "latency": _latency(latencies),
        "abstentions": dict(abstentions.most_common()),
        "blame": dict(blame.most_common()),
        "by_category": {key: dict(value) for key, value in by_category.items()},
        "worst": false_actions[:25],
        "dangerous_cases": dangerous[:25],
    }


def _arguments_ok(decision, record: dict) -> bool:
    """Whether every required argument was supplied with something."""
    from friday import capability_runtime as R

    required = R.required_arguments(decision.capability) or ()
    return all(str(decision.arguments.get(name) or "").strip()
               for name in required)


def _latency(values: list[float]) -> dict:
    if not values:
        return {}
    ordered = sorted(values)
    return {
        "median": round(statistics.median(ordered), 2),
        "p95": round(ordered[max(0, int(len(ordered) * 0.95) - 1)], 2),
        "max": round(ordered[-1], 2),
    }


def curve(records: list[dict], margins) -> list[dict]:
    """
    Coverage against error, so an operating point is chosen rather than
    stumbled into. §23: the question is not "is it good", it is "at what
    coverage does it stop being safe".
    """
    rows = []
    for margin in margins:
        report = judge(records, margin=margin)
        rows.append({
            "margin": margin,
            "coverage": report["coverage"],
            "precision": report["precision"],
            "false_actions": report["false_actions"],
            "dangerous": report["dangerous"],
        })
    return rows


# ---------------------------------------------------------------------------


def _show(report: dict, name: str) -> None:
    print(f"=== {name} : {report['total']} utterances ===")
    print(f"coverage           {report['acted']} ({report['coverage']:.1%})")
    print(f"precision (local)  {report['precision']:.1%}")
    print(f"FALSE ACTIONS      {report['false_actions']} "
          f"({report['false_action_rate']:.3%})")
    print(f"  DANGEROUS        {report['dangerous']}   <- must be 0")
    print(f"correct arguments  {report['correct_arguments']} of "
          f"{report['correct']} correct routes")
    print(f"correct abstention {report['correct_abstention']}")
    print(f"false abstention   {report['false_abstention']}")
    latency = report["latency"]
    if latency:
        print(f"latency            median {latency['median']}ms  "
              f"p95 {latency['p95']}ms  max {latency['max']}ms")
    print()
    print("by category:")
    for category, counts in sorted(report["by_category"].items()):
        acted = counts.get("acted", 0)
        wrong = counts.get("false_action", 0)
        print(f"  {category:16s} acted {acted:5d}  wrong {wrong:4d}  "
              f"abstained {counts.get('abstained', 0):5d}")
    print()
    print("why it abstained:")
    for reason, count in list(report["abstentions"].items())[:12]:
        print(f"  {reason:24s} {count}")
    if report["dangerous_cases"]:
        print()
        print("DANGEROUS false actions:")
        for case in report["dangerous_cases"]:
            print(f"  {case['text']!r} -> {case['acted']}")
    if report["worst"]:
        print()
        print("false actions (first 12):")
        for case in report["worst"][:12]:
            print(f"  [{case['category']:12s}] {case['text']!r} -> "
                  f"{case['acted']} (wanted {case['wanted']})")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("split", choices=("development", "calibration",
                                          "holdout"))
    parser.add_argument("--curve", action="store_true",
                        help="sweep the margin and print coverage vs error")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--i-am-promoting", action="store_true",
                        help="required to open the holdout")
    arguments = parser.parse_args()

    if arguments.split == "holdout" and not arguments.i_am_promoting:
        print("The holdout is locked. Opening it spends it: once its failures\n"
              "have been read it is evaluation history, and the next promotion\n"
              "needs a fresh one. Pass --i-am-promoting if that is what this is.",
              file=sys.stderr)
        return 2

    records = load(arguments.split)
    report = judge(records)

    if arguments.json:
        print(json.dumps(report, indent=2))
        return 0

    _show(report, arguments.split)

    if arguments.curve:
        print()
        print("coverage vs error, sweeping the winner-margin threshold:")
        print(f"  {'margin':>7s} {'coverage':>9s} {'precision':>10s} "
              f"{'false':>7s} {'dangerous':>10s}")
        for row in curve(records, (0, 2, 4, 6, 8, 12, 16, 24, 32)):
            print(f"  {row['margin']:7.0f} {row['coverage']:9.1%} "
                  f"{row['precision']:10.1%} {row['false_actions']:7d} "
                  f"{row['dangerous']:10d}")

    print()
    ok = report["dangerous"] == 0
    print(f"dangerous false actions: {report['dangerous']}  "
          f"-> {'PASSES the hard gate' if ok else 'FAILS the hard gate'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
