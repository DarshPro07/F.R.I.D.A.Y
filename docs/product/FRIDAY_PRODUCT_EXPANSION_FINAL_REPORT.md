# FRIDAY Product Expansion — Final Report

Session of 2026-09-06. Two tracks, deliberately separated; neither merged
into the release candidate.

---

## 1. Executive summary

Ten defects were found and fixed today, every one by running the system
rather than reading it. Six landed on the release candidate earlier in the
session; four more were found in code I wrote today, by tests I wrote to
attack it.

The single outstanding gate — **A-051, the 8-hour production soak — has
never passed**, across three attempts. The reason is now proven rather than
suspected, and it is not a product defect: **this machine cannot host an
8-hour qualification run.** Both harness defects that hid that fact are
fixed and verified.

**Verdict: NOT PRODUCTION READY.** Seven of eight PRD §16 gates pass on
demand; Soak is `BLOCKED_EXTERNAL`.

---

## 2. Track A — final state

### Attempt 3 (commit `030795f`)

| Measure | Value |
|---------|-------|
| Cycles | 7,359 |
| Errors | **0** |
| Violations | 1 (see below) |
| Wall time | 5.02 h |
| **Suspended** | **2.65 h** |
| **Active** | **2.37 h** |
| RSS | 155.8 → 189.6 MB (flat) |
| Threads | 20–28 (flat) |
| Handles | ≤ 1,488 (flat) |

**The machine stopped executing.** Evidence, aligned to the second:

    19:18:05  last soak log line
    19:18:06  Kernel-Power 105 — power source change (to battery)
    19:23:52  Kernel-Power 506 — entering Modern Standby   (5m46s later)
    21:55:52  Kernel-Power 507 — exiting Modern Standby
    21:56:13  soak log resumes

An **independent watchdog process** froze in the same window. Two unrelated
processes stopping at one instant is a host event, not a deadlock. By
23:34 the machine was cycling into standby every ~7 minutes (506/507 at
23:09, 23:16, 23:25, 23:31) and the soak worker showed **0.00 s CPU over a
4-second sample** with 6,356 s accumulated — frozen, not spinning.

The lone violation (`worker crash: run RUN-90866a6e197b not recovered`)
fired **4 seconds after resume**: a wall-clock deadline expiring across a
period with zero execution opportunity.

**Attempt 3 cannot yield a qualifying verdict.** Its own gap detector was
built for exactly this and will refuse to report PASS. That refusal is
correct and is preserved, not overridden.

### Two harness defects, both fixed (`fix/soak-modern-standby`, `ad96479`)

**1. `keep_awake.py` claimed protection it did not have.**
`SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)` is correct on
Traditional Sleep (S3). This machine reports only `Standby (S0 Low Power
Idle)`. The script printed `suppressed = True` from a non-zero return value
while the machine slept three times.

Microsoft's documented rule is the mechanism: on Modern Standby, power
requests block sleep **indefinitely on AC** and are **terminated five
minutes after the sleep timeout on DC**. The 5m46s above *is that rule
firing*.

`friday/power_request.py` now holds `PowerRequestSystemRequired` +
`PowerRequestExecutionRequired` via `PowerCreateRequest`/`PowerSetRequest`
with a `REASON_CONTEXT`, clears them in `finally`, closes the handle,
surfaces `GetLastError`, and asks `powercfg /requests` for independent
confirmation — reporting *"unverified: needs elevation"* when it cannot
confirm rather than assuming success. `PowerRequestAwayModeRequired` is
deliberately unused (S3-only); a test greps the source to keep it that way.

**Verified live:** with the machine on battery, `keep_awake.py` exited 3 —
*"Modern Standby on battery … Connect AC."* That is the condition attempt 3
walked into blind.

**2. Deadlines counted time the machine was not running.**
`time.monotonic()` is **not** the fix on Windows — CPython maps it to
`QueryPerformanceCounter`, which keeps counting through Modern Standby and
would reproduce the bug. `friday/active_clock.py` binds
`QueryUnbiasedInterruptTimePrecise` (confirmed active on this host), where
*unbiased* means sleep is excluded, with a documented fallback chain whose
last resort **admits it is biased** rather than lying. Linux
`CLOCK_MONOTONIC` and macOS `mach_absolute_time` already have the right
semantics.

`SuspensionDetector` separates wall from active time; `Deadline` counts
execution opportunity, so a 60-second recovery window no longer expires
because a laptop slept for two hours.

19 new tests; 35 passed with the existing soak suite.

---

## 3. Track B — architecture

`product/phase1-context`, worktree `D:/friday-product-phase1`, six commits.

The audit came first and changed the plan: **six of seven Wave-1 items
already existed** under different names. Building the PRD's abstractions
beside them would have been drift on day one.

| PRD asks for | Already in tree | Action |
|---|---|---|
| FR-101 context budget | `TokenBudget`, `budget_for`, per-tier telemetry | extended |
| FR-104 T0–T6 | six task classes + `classify_task` (T0) | **mapped, not rebuilt** |
| FR-105 width/depth | `choose_route` | verified in place |
| FR-106 fitness | `executor_router` + `RouteOutcomes` | verified, PARTIAL |
| FR-102 provenance | `brain.UNTRUSTED_PROVENANCE` (write side) | read side added |
| FR-103 retrieval router | *nothing* | **built** |

---

## 4. Requirement compliance matrix

| ID | Status | Evidence |
|----|--------|----------|
| FR-101a child budget leases | VERIFIED | 12 tests, 2 plants |
| FR-101b objective context budget | BLOCKED | needs full suite |
| FR-102 provenance | VERIFIED | 19 tests, 3 plants |
| FR-103 retrieval router | VERIFIED | 49 tests, 7 plants |
| FR-104 task mapping | VERIFIED | 14 tests, drift plant |
| FR-105 width/depth | VERIFIED | 14 tests |
| FR-106 model fitness | PARTIAL | 10 tests; deferred pending evals |
| A-051 soak | BLOCKED_EXTERNAL | see §2 |
| Phases 2–7 | NOT_AUDITED | blocked behind full suite |

---

## 5. Bugs found

**In the release candidate (earlier today, all fixed and CI-green):**

    accd5c4  memory graph rebuilt from every row on every delegation
    225195d  claimed she opened the Start Menu having called nothing
    7fba09c  gateway thread-per-request wedged under memory pressure
    8c4c38d  tier-1 retrieval only ranked the newest 200 rows
    6314dca  health condemned whole providers on one model's evidence
    f81c2d4  local_only routed private context to a public endpoint

**In Track B code I wrote today, found by tests I wrote to attack it:**

| Bug | Root cause |
|-----|-----------|
| `TypeError` on ranked rows | `row_factory` never set; aggregation passed because tuples suffice |
| Every row's trust came back `REPORTED` | `_ranked` never SELECTed `source` — the column trust derives from |
| `"refunds"` returned the 500 newest rows | substring matching: `refunds` ≠ `refund`; ranking silently degraded |
| Harness reported sleep suppression it did not have | S3 API on an S0 machine |

Three of my own **tests** were wrong before they were right — inventing
`execution_value`'s schema, inventing a `choose(available=…)` parameter, and
asserting a repo-wide rename was "mechanical". Each was corrected by reading
the implementation, never by bending code to match the guess.

---

## 6. Privacy and security results

- `local_only` locality is **proven from the endpoint**, never from a
  provider id; unknown and LAN addresses are not local (`f81c2d4`).
- `TrustLevel` derives from the row's `source` using the **same regex the
  write side uses**, so the two boundaries cannot drift. Only OWNER and
  SYSTEM may be obeyed; a scraped page stays quotable and inert.
- A row missing its `source` column is UNTRUSTED, never defaulted — an
  unfetched column must not become an obeyable instruction.
- Aggregation SQL is parameterised with a per-table column allow-list; no
  model writes SQL.

---

## 7. Context / retrieval results

`Coverage` is the load-bearing idea: only `COMPLETE` and
`FILTERED_COMPLETE` may back a number. A top-K lexical scan cannot count —
it returns best-scoring rows, which look like an answer and are not one.

Scaling (100 / 1k / 10k / 50k rows):

    aggregate  18.09 → 8.07 → 5.28 → 3.92 ms   (flat, index-served)
    ranked     10.58 → 14.48 → 56.07 → 241.52 ms (linear, scores every row)
    count       2 → 11 → 104 → 516              (exact at every size)

A 50,000-row count moves **fewer than 10 rows** into Python.

---

## 8. Tests

    Track B   117 tests across 8 new modules
    Track A    19 new + 16 existing soak = 35
    candidate  4,022 green (full suite, frozen tree, PYTEST_EXIT=0)

Red-green discipline: **13 plants**, each observed red, restored, observed
green. Fixtures must prove they reproduce the defect they guard — the
coverage fixture asserts a newest-200 window finds 2 of 4 relevant rows
while a structured count finds all 4.

## 9. CI

    34019258917  8c4c38d  green
    34022915587  6314dca  green
    34030194790  218d895  green (all three jobs)

`218d895` adds only new files over `f81c2d4` (650 insertions, zero
modifications), so its green run covers both.

---

## 10. Known limitations

- **No semantic retrieval.** `HYBRID` is lexical; a differently-worded
  question misses. Proven by the plural test. Needs embeddings — its own
  phase, not a flag.
- **`MemorySource` is the only source.** Calendar, comms, codebase, live
  connectors are contract-only.
- **FR-106 has no eval registry.** Known-good routing retained rather than
  shipping opinions as architecture.
- **Phases 2–7 unaudited.**

---

## 11. Genuine external blockers

1. **A-051 qualification** — needs the machine on AC, undisturbed, for 8
   hours. Documented OS behaviour; the harness now refuses to start rather
   than manufacture a false verdict. **EXTERNAL QUALIFICATION BLOCKER.**
2. **FR-101b and Phases 2–7** — need the full regression suite, which must
   not run while a soak is measuring the candidate.

---

## 12. Final verdict

**NOT PRODUCTION READY** — and the release gate says so itself, exiting 1
rather than reporting an unearned pass.

What changed today is the *quality of the unknown*. This morning A-051 was
failing for unknown reasons. Tonight the reason is measured, documented
against vendor behaviour, and fixed in the harness — and the remaining
obstacle is a laptop that suspends, not code that breaks.

Attempt 4 needs one thing this session could not supply: **a machine on AC
power, left alone for eight hours.** With `ad96479` in place the harness
will now prove that precondition before it starts, and refuse if it cannot.
