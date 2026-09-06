# Remaining Requirements — Implementation Ledger

Two tracks, deliberately separated. Nothing merges into the release
candidate until Track A has a defensible terminal result and Track B's
phases carry evidence.

| Track | Worktree | Branch | Base |
|-------|----------|--------|------|
| A — release/soak hardening | `D:/friday-soak-fix` | `fix/soak-modern-standby` | `030795f` |
| B — product expansion | `D:/friday-product-phase1` | `product/phase1-context` | `030795f` |
| candidate (frozen, running) | `E:/friday-tony-stark-demo-main` | `main` | `030795f` |

Statuses: `NOT_AUDITED` → `EXISTING` / `PARTIAL` / `BROKEN` / `MISSING` →
`IMPLEMENTING` → `TESTING` → `VERIFIED` / `BLOCKED` / `DEFERRED_WITH_REASON`.
**VERIFIED requires evidence. Code existing is not verification.**

---

## Track A — soak / release hardening

| ID | Requirement | Was | Status | Evidence |
|----|-------------|-----|--------|----------|
| A-051 | 8-hour production soak | — | **BLOCKED_EXTERNAL** | attempt 3 lost 2h38m to Modern Standby; see below |
| A-051.1 | Sleep suppression that is real, not claimed | BROKEN | VERIFIED | `friday/power_request.py`; refused a live run on battery, exit 3 |
| A-051.2 | Deadlines counted in execution opportunity | BROKEN | VERIFIED | `friday/active_clock.py`; `QueryUnbiasedInterruptTimePrecise` bound on this host |
| A-051.3 | Host-suspension telemetry | MISSING | VERIFIED | `SuspensionDetector.report()` — wall vs active vs suspended |
| A-051.4 | Harness refuses an unqualifiable environment | MISSING | VERIFIED | `scripts/keep_awake.py` exit 3 with the reason named |

**Attempt 3 diagnosis (evidence, not inference).** Timestamps align to the
second:

    19:18:05  last soak log line
    19:18:06  Kernel-Power 105 — power source change (to battery)
    19:23:52  Kernel-Power 506 — entering Modern Standby   (5m46s later)
    21:55:52  Kernel-Power 507 — exiting Modern Standby
    21:56:13  soak log resumes

An *independent* watchdog process froze in the same window, which is what
makes this a host event rather than a product deadlock. Product telemetry
through the whole run: RSS 155 → 168 MB, threads 20–28, handles ~1,430,
**0 errors across 7,359 cycles**. The single violation (`worker crash: run
RUN-90866a6e197b not recovered`) fired 4 seconds after resume — a wall-clock
deadline expiring over a period with zero execution opportunity.

Microsoft's documented rule is the mechanism: on Modern Standby, power
requests block sleep **indefinitely on AC** and are **terminated five
minutes after the sleep timeout on DC**. 5m46s is that rule firing.

Consequence for qualification: **this machine cannot host an 8-hour A-051
run on battery.** Not a code defect and not fixable in code.

---

## Track B — product expansion

### Phase 1 — Context + Routing Intelligence

| ID | Requirement | Was | Status | Evidence |
|----|-------------|-----|--------|----------|
| FR-101a | Child budget leases (parent dominates) | MISSING | VERIFIED | probe: child got fresh 400k while parent had 1k left; 12 tests, 2 plants red |
| FR-101b | Objective-scoped **context** budget | PARTIAL | BLOCKED | needs `memory_stack` wiring — full-suite surface, waits for soak |
| FR-102 | Provenance + trust/privacy classes | PARTIAL (write side only) | VERIFIED | 19 tests, 3 plants red; found a real bug (`_ranked` never selected `source`) |
| FR-103 | Retrieval router + coverage semantics | MISSING | VERIFIED | 49 tests across 3 modules; 7 plants red |
| FR-104 | Task-class mapping, no second taxonomy | EXISTING | VERIFIED | 14 tests; drift plant (`T2_LLM_TOOL`) → 3 red |
| FR-105 | Width/depth planner | EXISTING (`choose_route`) | VERIFIED | 14 tests; "complexity alone does not buy parallelism" |
| FR-106 | Model fitness router | PARTIAL | PARTIAL | evidence-driven `executor_router` + `RouteOutcomes` verified (10 tests); model-side registry has data, no consumer — **deferred pending evals, per instruction** |

### Phases 2–7

`NOT_AUDITED`. Not started: Phase 1's remaining item (FR-101b) and every
later phase touch `memory_stack`, the live delegation path, or browser/
telephony surfaces — none of which can be honestly verified without the full
suite, which must not run while a soak is measuring.

---

## Bugs found in my own new code (Track B)

| What | Root cause | Fix |
|------|-----------|-----|
| `TypeError: cannot convert dictionary update sequence` | `_ranked` built dicts from rows but `row_factory` was never set; aggregation passed because tuples suffice | set `conn.row_factory` in `query()` |
| Every row's trust came back `REPORTED` | `_ranked` never SELECTed `source` — the column trust is derived from; a scraped page and a worker note were indistinguishable | added `source` to the projection; a row without it is UNTRUSTED, never defaulted |
| `"refunds"` returned the 500 newest rows | substring matching: `refunds` ≠ `refund`, every row scores 0, ranking degrades to newest-first | recorded as a test; it is the concrete argument for a semantic source |

Three of my own tests were wrong before they were right — guessing
`execution_value`'s schema, inventing a `choose(available=...)` parameter,
and asserting a repo-wide rename was "mechanical". Each was corrected by
reading the implementation, not by adjusting the code to match the guess.

---

## Red-green evidence (every guard planted, observed red, restored)

| Plant | Red |
|-------|-----|
| `T2_LLM_TOOL` added to `TASK_CLASSES` | 3 |
| TOP_K allowed to back a numeric claim | 3 |
| Counting stops demanding complete coverage | 2 |
| Time range closed instead of half-open | 1 |
| Column allow-list removed | 2 |
| Dropped filters stop downgrading coverage | 1 |
| Ranked query windows before scoring (8c4c38d) | 1 |
| Missing database reports 0 | 1 |
| Lease ignores the parent | 6 |
| Reserve ignored | 1 |
| Missing `source` defaults to OWNER | 2 |
| Untrusted fragments become obeyable | 5 |
| No provenance → every row directive | 3 |

---

## Genuine external blockers

1. **A-051 qualification** — needs the machine on AC for 8 uninterrupted
   hours. Documented OS behaviour, not a code defect. The harness now
   refuses to start rather than producing a false verdict.
2. **FR-101b and Phases 2–7** — need the full regression suite, which must
   not run while a soak is measuring the candidate.
