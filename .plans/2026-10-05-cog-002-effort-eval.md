# Plan: COG-002 adaptive effort vs always-deep golden eval

**Date:** 2026-10-05
**Status:** In Progress
**Services:** objective-engine (execution_economics.RouteOutcomes seam), scripts/ (harness only)

## Objective
PRD v7 COG-002 acceptance: "Beats always-deep cost at quality threshold."
`choose_route` + `resolve_effort` already adapt tier/effort to consequence and
blast radius; nothing proves the adaptation pays. Add a deterministic golden
eval that runs one fixed, human-labelled task suite through the adaptive policy,
an always-deep baseline and an always-economy floor, and records quality/cost.

## Design
- `scripts/golden_effort.py` (pattern: `golden_*.py`): `SUITE` of labelled tasks,
  each with `min_tier` = the cheapest tier a human says does the work right
  first pass. The labels are written independently of the keyword tables.
- Quality = first-pass adequacy (policy tier >= `min_tier`). Safety floor: no
  deep-labelled task may be routed below deep. Cost = price-weighted tokens; a
  task routed too low also pays a deep repair run (the H6 "rework" rule).
- Verdict `beats_always_deep` = adaptive adjusted cost < always-deep AND quality
  >= `QUALITY_THRESHOLD` AND floor intact. Always-economy must FAIL the gate, so
  the threshold has teeth.
- Every (policy, task) is written to `RouteOutcomes` (a throwaway db unless
  `--db` is given - never the live ledger) and read back through the new
  `RouteOutcomes.by_run_prefix` seam; `execution_value` totals are reported.
- Frozen result versioned at `docs/architecture/effort-policy-baseline.json`;
  `--freeze` rewrites it, a normal run fails on regression against it.

## Assumptions (PRD gives no numbers; revisit if wrong)
- `QUALITY_THRESHOLD` = 0.90 first-pass adequacy. Assumed: the PRD names a
  threshold but never sets it.
- Price ladder economy:standard:deep = 1:3:5 (haiku:sonnet:opus list-price
  ratio), token volume held constant across tiers (understates deep thinking
  cost, so conservative against the claim). A test shows the verdict holds for
  other monotone ladders.

## DOD
- [ ] tests/test_golden_effort.py + RouteOutcomes.by_run_prefix test pass
- [ ] baseline frozen and matches a fresh run
