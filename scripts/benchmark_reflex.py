"""
LEGACY_REGRESSION_SET. Frozen. Not evidence that anything generalises.

This corpus is Friday's own `intent_examples`, and it has now been read,
re-read and inspected repeatedly across router development. It still detects
regressions, which is worth keeping. It can no longer show that a new routing
rule works on language nobody had in mind when they wrote the rule.

For that, use `scripts/evaluate_selective.py` against the development,
calibration and holdout splits built by `scripts/build_corpus.py`.

Do not change production routing to make a remaining line here turn green
unless an independent test exposes the same defect.

---

Is the local reflex good enough to switch on? Measure, do not guess.

Run:  .venv/Scripts/python.exe scripts/benchmark_reflex.py
      .venv/Scripts/python.exe scripts/benchmark_reflex.py --gate-only

The corpus is not written down. It is derived from the capability registry,
where every capability already carries the phrases a request might use and -
more valuably - the phrases that mean a *different* capability. A hand-kept
benchmark would be wrong the first time somebody added a capability and forgot;
this one cannot drift, because it is the same data the semantic router is
built from.

    intent_examples     262 utterances, each with the capability it means
    negative_examples   233 utterances, each with a capability it does NOT mean
    escalations         requests that must never be handled locally at all

The number that decides whether this ships is not accuracy.

    FALSE ACTION RATE   a wrong capability that the gate admitted

A local router that is right 95% of the time and reboots the machine the other
5% is worse than no local router. Everything else - a miss, an escalation, a
refusal - costs exactly what Friday costs today, which is the current bill.
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from friday import capabilities as C          # noqa: E402
from friday import reflex as X                # noqa: E402

#: Requests that must reach the reasoning model. Written here rather than
#: derived, because the registry has no field for "this is not a tool call" -
#: which is itself the point: escalation is a property of the request, not of
#: any capability.
ESCALATIONS = (
    "research whether this startup idea makes sense",
    "explain why Friday keeps failing during provider fallback",
    "design a game and ask me all the questions you need",
    "compare React, Godot and Unreal for my requirements",
    "review this codebase architecture",
    "build a complete development plan",
    "what do you think I should do",
    "why is my computer slow",
    "is this a good way to structure the project",
    "help me decide between two job offers",
    "summarise what we agreed last week and what is still open",
    "write me a business case for this",
)


def corpus() -> tuple[list, list, list]:
    """(positives, negatives, escalations) from the live registry."""
    positives, negatives = [], []
    for capability in C._ALL:
        for phrase in capability.intent_examples:
            positives.append((phrase, capability.id))
        for phrase in capability.negative_examples:
            negatives.append((phrase, capability.id))
    return positives, negatives, list(ESCALATIONS)


def measure(agent=None, *, gate_only: bool = False) -> dict:
    positives, negatives, escalations = corpus()
    tally = collections.Counter()
    latencies: list[float] = []
    false_actions: list[dict] = []
    misses: list[dict] = []
    # Progress on stderr: a corpus of a few hundred takes minutes when
    # the model is in the loop, and a silent minute reads as a hang.
    seen = [0]
    total = len(positives) + len(negatives) + len(escalations)

    def run(text: str):
        outcome = X.route(text, agent=agent)
        if outcome.milliseconds:
            latencies.append(outcome.milliseconds)
        seen[0] += 1
        if seen[0] % 25 == 0:
            done = sum(latencies) / 1000 if latencies else 0
            print(f"  {seen[0]}/{total} ({done:.0f}s in the model)",
                  file=sys.stderr, flush=True)
        return outcome

    for text, expected in positives:
        outcome = run(text)
        if not outcome.acts:
            tally["positive_escalated"] += 1
            misses.append({"text": text, "expected": expected,
                           "why": outcome.escalated,
                           "proposed": outcome.proposed})
        elif outcome.capability == expected:
            tally["positive_correct"] += 1
        else:
            tally["positive_false_action"] += 1
            false_actions.append({"text": text, "expected": expected,
                                  "acted": outcome.capability,
                                  "confidence": outcome.confidence})

    # A negative example names a capability the phrase does NOT mean. Acting
    # on that exact capability is the clearest false action there is.
    for text, forbidden in negatives:
        outcome = run(text)
        if outcome.acts and outcome.capability == forbidden:
            tally["negative_false_action"] += 1
            false_actions.append({"text": text, "forbidden": forbidden,
                                  "acted": outcome.capability,
                                  "confidence": outcome.confidence})
        elif outcome.acts:
            tally["negative_acted_elsewhere"] += 1
        else:
            tally["negative_escalated"] += 1

    for text in escalations:
        outcome = run(text)
        if outcome.acts:
            tally["escalation_false_action"] += 1
            false_actions.append({"text": text, "expected": "ESCALATE",
                                  "acted": outcome.capability,
                                  "confidence": outcome.confidence})
        else:
            tally["escalation_correct"] += 1

    dangerous = [item for item in false_actions
                 if X.is_dangerous(item.get("acted", ""))]

    handled = tally["positive_correct"]
    attempted = len(positives)
    every_false = (tally["positive_false_action"]
                   + tally["negative_false_action"]
                   + tally["escalation_false_action"])
    judged = attempted + len(negatives) + len(escalations)

    return {
        "gate_only": gate_only,
        "corpus": {"positives": attempted, "negatives": len(negatives),
                   "escalations": len(escalations), "total": judged},
        "tally": dict(tally),
        "handled_locally": handled,
        "local_share": round(handled / attempted, 4) if attempted else 0.0,
        "false_actions": every_false,
        "false_action_rate": round(every_false / judged, 5) if judged else 0.0,
        # A false action on a dangerous capability is the one number that
        # must be zero; it is reported on its own so it cannot hide in a rate.
        "dangerous_false_actions": len(dangerous),
        "dangerous": dangerous[:10],
        "latency_ms": _latency(latencies),
        "worst": false_actions[:20],
        "misses": misses[:20],
    }


def _latency(values: list[float]) -> dict:
    if not values:
        return {}
    ordered = sorted(values)
    return {
        "calls": len(ordered),
        "median": round(ordered[len(ordered) // 2], 1),
        "p95": round(ordered[int(len(ordered) * 0.95) - 1], 1),
        "max": round(ordered[-1], 1),
    }


class _Deaf:
    """A stand-in that proposes nothing, to price the gate on its own.

    Everything escalates, so the false-action rate is zero by construction -
    which is the useful baseline: it is exactly what Friday costs today, and
    any real model has to beat it without spending that zero.
    """

    def complete(self, text, max_new_tokens=256):
        return {"type": "respond", "function_calls": [], "confidence": 0.0}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", default="needle-flat",
                        choices=("none", "deterministic", "needle-flat",
                                 "needle-domain"),
                        help="which router to measure")
    parser.add_argument("--gate-only", action="store_true",
                        help="alias for --arm none")
    parser.add_argument("--json", action="store_true", help="machine-readable")
    arguments = parser.parse_args()

    os.environ[X.ENV_FLAG] = "1"          # the benchmark is the switch
    X.reset()

    arm = "none" if arguments.gate_only else arguments.arm
    started = time.monotonic()
    if arm == "none":
        agent = _Deaf()
    elif arm == "deterministic":
        agent = X.Deterministic()
    elif arm == "needle-domain":
        agent = X.DomainGated()
    else:
        agent = X._agent()
        if agent is None:
            print(f"the local model is not available. "
                  f"pip install --no-deps {X.NEEDLE_PACKAGE}", file=sys.stderr)
            return 2
        print(f"model ready in {time.monotonic() - started:.1f}s "
              f"({len(X.tool_schemas())} capabilities offered)", file=sys.stderr)

    report = measure(agent, gate_only=arm == "none")
    report["arm"] = arm
    if arguments.json:
        print(json.dumps(report, indent=2))
        return 0

    corpus_counts = report["corpus"]
    print(f"arm               {report['arm']}")
    print(f"corpus            {corpus_counts['total']} utterances "
          f"({corpus_counts['positives']} positive, "
          f"{corpus_counts['negatives']} negative, "
          f"{corpus_counts['escalations']} must-escalate)")
    print(f"handled locally   {report['handled_locally']} "
          f"({report['local_share']:.1%} of the positives)")
    print(f"FALSE ACTIONS     {report['false_actions']} "
          f"({report['false_action_rate']:.3%})")
    print(f"  of which DANGEROUS  {report['dangerous_false_actions']}   <- must be 0")
    if report["latency_ms"]:
        latency = report["latency_ms"]
        print(f"latency           median {latency['median']}ms  "
              f"p95 {latency['p95']}ms  max {latency['max']}ms")
    print()
    for name, count in sorted(report["tally"].items()):
        print(f"  {name:28s} {count}")
    if report["worst"]:
        print("\nfalse actions:")
        for item in report["worst"]:
            print(f"  {item['text']!r} -> {item['acted']} "
                  f"(wanted {item.get('expected', 'not ' + str(item.get('forbidden')))})")
    print()
    if report["dangerous"]:
        print("\ndangerous false actions:")
        for item in report["dangerous"]:
            print(f"  {item['text']!r} -> {item['acted']}")
    verdict = ("SHIPPABLE" if report["dangerous_false_actions"] == 0
               and report["false_actions"] == 0
               and report["local_share"] > 0.3 else "NOT SHIPPABLE")
    print(f"verdict: {verdict}. {X.ENV_FLAG} stays off until this is "
          f"SHIPPABLE on a corpus this router has not been tuned against.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
