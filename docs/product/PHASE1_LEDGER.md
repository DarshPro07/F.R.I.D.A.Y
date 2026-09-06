# Phase 1 Implementation Ledger

Work package: **FRIDAY Phase 1 Context + Routing Intelligence, end to end.**
Branch `product/phase1-context`, worktree `D:/friday-product-phase1`, forked
from `030795f`. Track B — nothing merges to the release candidate until
A-051's result is preserved.

Statuses: `NOT_AUDITED` → `EXISTING`/`PARTIAL`/`MISSING`/`BROKEN` →
`IMPLEMENTING` → `TESTING` → `VERIFIED`.
**VERIFIED means objective evidence exists. Code existing is not
verification.**

---

## Requirements

| # | Requirement | Status | Evidence |
|---|-------------|--------|----------|
| 0 | Map PRD onto existing architecture | VERIFIED | `docs/product/PHASE1_CONTEXT_ROUTING.md`; six of seven Wave-1 items already existed |
| FR-104 | Task classes mapped, no second taxonomy | VERIFIED | `tests/test_task_class_mapping.py` 14 passed; drift plant (`T2_LLM_TOOL`) → 3 red |
| FR-103a | `RetrievalIntent` / `RetrievalStrategy` | VERIFIED | `tests/test_retrieval_router.py` 32 passed |
| FR-103b | Coverage semantics (no top-K counts) | VERIFIED | 4 plants red; fixture proves newest-200 misses 2 of 4 |
| FR-103c | SQL/filter aggregation, parameterised | VERIFIED | injection + allow-list tests; plant (allow-list removed) → 2 red |
| FR-103d | Time-range filtering, half-open | VERIFIED | months partition 500 rows exactly; closed-range plant → red |
| FR-103e | `RetrievalSource` abstraction | VERIFIED | `MemorySource` implements it; `tests/test_retrieval_sources.py` 17 passed |
| FR-103f | Evidence-bearing `RetrievalResult` | VERIFIED | `test_evidence_points_at_real_rows` |
| FR-103g | `RetrievalRouter` | VERIFIED | routes count→aggregate, topic→ranked; refuses unsupported strategy |
| FR-103h | Protection against newest-N truncation | VERIFIED | plant B (window before scoring) → red |
| FR-103i | Structured vs approximate counts | VERIFIED | `describe_count`; dropped-filter plant → red |
| FR-103j | Lexical/semantic/hybrid selection | PARTIAL | lexical + hybrid routing work; **no semantic source exists** — see Limitations |
| FR-103k | Targeted benchmarks | IMPLEMENTING | next |
| FR-101a | Child budget leases (parent dominates) | VERIFIED | probe proved a child got a fresh 400k while parent had 1k left; `tests/test_objective_budget_lease.py` 12 passed; 2 plants red; 37 passed across all budget suites |
| FR-101b | Objective-scoped CONTEXT budget | NOT_AUDITED | needs `memory_stack` wiring - full-suite surface, blocked on soak |
| FR-102 | Context provenance | NOT_AUDITED | after FR-101 |
| FR-106 | Model fitness router | DEFERRED | needs an eval registry; no opinions-as-architecture |

---

## Files changed

| File | Purpose |
|------|---------|
| `friday/retrieval.py` (new, 392) | Intent, strategy, coverage, aggregate SQL builder, `describe_count` |
| `friday/retrieval_sources.py` (new) | `MemorySource`, `RetrievalRouter` |
| `friday/objective_budget.py` | `lease_for_child` - parent's remaining budget bounds every child |
| `tests/test_retrieval_router.py` (new) | 32 contract tests |
| `tests/test_retrieval_sources.py` (new) | 17 tests against a real SQLite corpus |
| `tests/test_task_class_mapping.py` (new) | 14 mapping/drift tests |
| `tests/test_objective_budget_lease.py` (new) | 12 lease tests |
| `tests/test_retrieval_scaling.py` (new) | 8 scaling/shape tests |
| `tests/conftest.py` | Collection-time checkout-identity guard |
| `docs/product/PHASE1_CONTEXT_ROUTING.md` (new) | Audit + sequence |

---

## Architectural decisions

1. **Map, don't duplicate.** The PRD's T0–T6 are conceptual. Six task classes
   and `execution_economics.classify_task` (T0, "no model calls") already
   existed; a parallel enum would have been drift on day one. The mapping is
   a test, and it asserts behaviour — tier, ceiling, reasoning effort — not
   names.
2. **Coverage is the load-bearing idea.** Only `COMPLETE` and
   `FILTERED_COMPLETE` may back a number. A top-K lexical scan cannot count:
   it returns best-scoring rows, which look like an answer and are not one.
3. **No model-authored SQL.** Aggregation goes through a parameterised
   builder over a fixed operation list with a per-table column allow-list.
   Table/column names cannot be bound parameters, so they are validated
   against the allow-list and refused otherwise.
4. **Half-open time ranges.** `[start, end)` — a closed range pulls a
   September 1st 00:00 row into "August".
5. **Rank first, slice second.** Ranking runs in SQL over every candidate;
   the slice comes after. Windowing before scoring is the 8c4c38d defect.
6. **A missing database is `UNKNOWN`, not 0.** Reporting zero for an absent
   source is a false answer.
7. **Dropped filters downgrade coverage.** If a requested filter has no
   column, the count answers a *different question* — it is `APPROXIMATE`
   with the dropped keys named.

---

## Failures found and fixed during this run

| What | Classification | Root cause | Fix |
|------|---------------|------------|-----|
| `rename all occurrences` asserted mechanical | **my test was wrong** | `_MECHANICAL` is filesystem/counting phrases; a repo-wide rename is a code change | Read the vocabulary; test corrected, code untouched |
| `TypeError: cannot convert dictionary update sequence` | real bug in new code | `_ranked` builds dicts from rows but `row_factory` was never set; aggregation passed because tuples suffice | Set `conn.row_factory = sqlite3.Row` in `query()` |
| "refunds" returned the 500 newest rows | **real product limitation** | substring matching: `refunds` does not match `refund`, every row scores 0, result degrades to newest-first | Recorded as `test_a_plural_query_term_does_not_match_the_singular_row`; motivates a real semantic source |

---

## Red-green evidence

Every guard was planted, observed red, restored, observed green.

| Plant | Red tests |
|-------|-----------|
| Lease ignores the parent (today's behaviour) | 6 |
| Reserve ignored | 1 |
| `T2_LLM_TOOL` added to `TASK_CLASSES` | 3 |
| TOP_K allowed to back a numeric claim | 3 |
| Counting stops demanding complete coverage | 2 |
| Time range closed instead of half-open | 1 |
| Column allow-list removed | 2 |
| Dropped filters stop downgrading coverage | 1 |
| Ranked query windows before scoring (8c4c38d) | 1 |
| Missing database reports 0 | 1 |

---

## Known limitations

- **There is no semantic retrieval.** `HYBRID` is lexical today. A question
  worded differently from the record will miss it, proven by the plural test.
  Closing this needs embeddings and a vector index — a real subsystem, not a
  flag, and it belongs to its own phase.
- **`MemorySource` is the only source.** Calendar, communications, codebase
  and live connectors are contract-only.
- **Aggregation covers `memories`** plus allow-list entries for `utterances`,
  `runs`, `tool_results`; only `memories` is exercised by tests.
- **No full-suite regression yet.** Targeted modules only while A-051 runs;
  `friday/retrieval*.py` are new files imported by nothing else, so blast
  radius on the candidate is zero until wired.

---

## Blockers

- **FR-101 objective-scoped budget** touches `model_gateway`/`memory_stack`,
  which have real regression surface. It cannot be honestly verified without
  the full suite, and the full suite must not run while the soak is
  measuring. Genuinely blocked on A-051 terminating — not on a decision.
