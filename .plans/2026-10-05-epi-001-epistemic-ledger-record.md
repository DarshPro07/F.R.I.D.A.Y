# Plan: EPI-001 epistemic ledger record

**Date:** 2026-10-05
**Status:** Done
**Services:** objective-engine (product, research_cache, promotion are one durable-objective path; no cross-service call)

## Objective
PRD section 35 gives an important claim one shape: status, sources, evidence,
confidence, freshness, assumptions, contradictions, scope. Today
`product.Claim` has evidence but no confidence/freshness and
`research_cache.Artifact` has confidence/freshness but no evidence/status, and
`promotion.Decision` cannot say which claims an engineering decision relied on.

## Features
### Feature 1: `friday/epistemic.py` - the one record
- **Small win:** `LedgerClaim` validates the schema and has a stable `id`
  (scope + normalised claim text), so two sources describing one claim agree on it.
- **DOD:** [x] tests/test_epistemic.py

### Feature 2: both existing types convert into it
- `product.Claim` gains `confidence`, `freshness`, `checked_at` (defaulted) and
  `to_ledger()`; `verify()` stamps freshness and checked_at when it read pages.
- `research_cache.Artifact.to_ledger()` yields one record per fact.
- **DOD:** [x] tests in test_product.py and test_research_cache.py

### Feature 3: `promotion.Decision.claims`
- `decide(..., claims=())` records the ids of the ledger claims relied on, on
  allowed and refused decisions alike; `as_dict()` carries them.
- **DOD:** [x] tests in test_promotion.py (127 passed: `pytest_brief.py -- tests/test_epistemic.py tests/test_product.py tests/test_promotion.py tests/test_research_cache.py`, run 2026-10-05)

## Assumptions (revisit if wrong)
- A claim's `status` is derived: sources -> `sourced`; a PREFERENCE -> `observed`
  (the boss said it); anything else -> `speculative`.
- No persistent ledger store in this slice; ids are content-derived so a link
  resolves by recomputation. A store is a follow-up.
- Linking does not change a decision's verdict (no new refusal reason).
