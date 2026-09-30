# Plan: BH-OPT-01 - more verified engineering per token and per minute

**Date:** 2026-09-30
**Status:** In progress
**Source:** `00-owner-directive-2026-09-30.md` (verbatim). The owner: "we are not optimizing for fewer tokens. We're
optimizing for more verified engineering per token and per minute", then "DO IT SAME FOR FRIDAY TOO".

## Reading of "same for FRIDAY too" (assumed; revisit if wrong)

Two targets, one design: (1) the builder - how FRIDAY's build is run from this repository; (2) FRIDAY's own runtime -
how FRIDAY runs Hermes and coding workers for the person. The builder's rules are BUILD-001..010 and BUILD-EFF-001/002
below (a build-harness spec, not product requirements; the PRD stays the product authority). FRIDAY's counterparts are
product requirements ORCH-001..013 in PRD Amendment A7 (§101).

## Build-harness requirements (owner's text)

- BUILD-001 DAG parallel execution: independent requirements run concurrently through isolated workers.
- BUILD-002 Sparse agent policy: spawn only for parallelism, specialisation, context isolation or independent verification.
- BUILD-003 Per-agent budget: bounded tokens, turns, tool calls, research calls, wall time (soft limit: report; hard: stop).
- BUILD-004 Artifact handoffs: structured artifacts and references, not transcripts (a status packet of 100-300 tokens).
- BUILD-005 Model cascade: the least costly model predicted to meet the requirement, escalating on measured need.
- BUILD-006 Isolated worktrees for concurrent code workers.
- BUILD-007 Output compression with recoverable raw artifacts; one compressor per stage unless benchmarked.
- BUILD-008 Test impact scheduling: targeted tests while iterating; the full suite at checkpoints, push, release.
- BUILD-009 Agent ROI: verified work against tokens, wall time, retries and compute per strategy.
- BUILD-010 Harness minimalism: nothing becomes a default unless evaluation shows it helps.
- BUILD-EFF-001: median tokens per representative slice -60% against the current baseline, verification rate held.
- BUILD-EFF-002: median wall time -40% on parallelisable slices, no rise in regressions.

## Slice BH-OPT-01

| # | Deliverable | Owner | Depends on |
|---|---|---|---|
| 1 | `scripts/token_attribution.py`: per-turn tokens from Claude Code transcripts (input, cache read/write, output), context growth attributed to tool results by tool and size; a TokenWasteReport. First run: this session, the 117.9k/h baseline. | worker A (Sonnet, worktree) | - |
| 2 | `scripts/test_impact.py` (changed files -> affected test files through the tests' import graph) and `scripts/pytest_brief.py` (runs pytest, full log to an artifact, prints counts and failing ids only). | worker B (Sonnet, worktree) | - |
| 3 | RTK A/B spike: a pinned release, sandboxed (not on PATH, no hooks, no `rtk init`), raw vs RTK on representative git/search/pytest commands - size, and whether a failure's id and message survive. | worker C (Sonnet) | - |
| 4 | `scripts/build_kernel.py`: a deterministic ready queue over the v7 ledger (dependencies, priorities, predicted file surfaces), waves, concurrency from the resource governor (at most 2 to start), headless workers in git worktrees with budgets, a zero-token watcher (exit, heartbeat, timeout), `evidence.json` per worker. | controller | 1, 2 |
| 5 | OLD vs NEW on three unresolved P0s: verification, tokens, wall time, model calls, file reads, test runtime, regressions. | controller | 4 |

Beads (installed on this machine) is weighed against the ledger-backed queue in step 4; only one is kept.

## Constraints

- House rules 14 and 15: workers never commit (the controller integrates), never touch `data/ada.sqlite3`, run tests
  with `F:/F.R.I.D.A.Y/.venv-build/Scripts/python.exe`, report in 250 words or fewer with file:line evidence.
- No bulk install of ECC or other catalogues; the Technology Scout is paused during the P0 burn-down.
- RTK is measured, not adopted, until the A/B says so; raw output always stays recoverable.
- The full four-chunk suite runs at checkpoints only.

## Results so far (2026-09-30)

- Steps 1-4 built by Sonnet workers (34-64k tokens each) and integrated by the controller: token attribution,
  test impact + brief pytest output, the RTK A/B (not adopted as a default: lossy where it saves), the build kernel
  (commits 7dc03bc, 5915ba7, 23e98ed).
- Step 5, first data point (not yet the A/B): BROW-001's increment through the kernel - one headless Sonnet worker,
  23 turns, 150 s, $0.36, ~0.67M tokens (0.62M cache reads) - verified and integrated (e46a8fd). The same session's
  controller spent ~0.39M cache-read tokens on EACH of its own turns and about six turns reviewing and integrating,
  so the controller, not the worker, is now the cost. Next: run waves from a fresh, small controller session.

## Resume here (fresh session)

1. `python scripts/build_kernel.py concurrency` and `waves --cap 2`; with under ~2 GB available run one worker.
2. `python scripts/build_kernel.py run-wave --cap N` (background); on exit read `data/build_runs/<REQ>.json`.
3. Verify each worker: its diff, one mutation of its core change, `scripts/test_impact.py --direct` + `scripts/pytest_brief.py`;
   copy the files in, update the ledger entry honestly, commit, `git worktree remove`.
4. Full four-chunk suite only at a checkpoint (the last: 258e3c0, 4,753 passed).
