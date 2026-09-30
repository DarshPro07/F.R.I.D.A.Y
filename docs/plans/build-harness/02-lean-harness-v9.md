# Plan: BH-OPT-02 - the lean harness (owner's v9 program prompt, 2026-09-30)

**Status:** Approved by the owner ("continue build it and then do not stop and continue with the prd prompt").
**Sources:** the owner's messages of 2026-09-30 in the session transcript (da60dc4e-...jsonl): the harness research
reply and the "MASTER AUTONOMOUS END-TO-END PRD ENGINEERING PROGRAM - v9 LEAN HARNESS" prompt. To be copied verbatim
into `00-owner-directive-2026-09-30b.md` with `scratchpad/extract_owner_text.py`-style extraction (marker
"v9 LEAN HARNESS") by the next session - do not retype it.

## Rulings carried (owner, 2026-09-30)

- Cua: no spike now; P0 burn-down first (A5). Integrate earlier only if a current P0 cannot be solved cleanly without it.
- A7 reading confirmed: FRIDAY's own self-development runtime eventually invokes this same harness; protected-kernel
  changes need stronger authorisation; self-development never modifies production directly.
- No new framework installs (Superpowers/ECC/OMC/gstack/Gajae/Serena/repo-mapper are references or later benchmarks).
  Technology Scout paused except for a measured gap. `/find-skills` was run on the orientation-cost gap (repo map /
  code context): first attempt blocked (safety classifier gave no verdict twice); re-run 2026-09-30 22:50 with
  `npx skills find` for "repo map", "code context", "codebase map" - no hit builds a per-task code map (hits are
  architecture-advice and unrelated-domain skills). Nothing installed. The installed `ast-grep` / `ast-grep-outline`
  skills already cover the outline primitive, so `code_capsule.py` builds on `ast` (stdlib) and needs no new dependency.
- Push/merge to a shared remote still needs explicit permission; local commits continue.

## The ten upgrades (the owner's list), in build order

Controller target for an ordinary requirement: controller < 3k tokens, capsule ~1k, worker 10-20k, review 0-5k,
integration 0 unless conflict.

| # | Upgrade | Where | Notes |
|---|---|---|---|
| 9 | Worktree creation mutex + base-SHA / branch / path / common-dir assertions before a worker starts | build_kernel.py | serialize `git worktree add`; record base SHA in the run |
| 8 | SHIP / SCOUT worker modes + investigation gate | build_kernel.py | SCOUT: read-only tools, ~8 turns, findings JSON; SCOUT first when the gap/files/acceptance are unknown |
| 10 | Adaptive worker budget classes (MICRO/NORMAL/COMPLEX/P0) + one continuation on measured progress | build_kernel.py | continuation via `--resume <session>` with extra budget, once |
| 6 | Read budget (stated in the packet; reads and bytes measured from the stream log) | build_kernel.py | post-hoc metric first |
| 4/5 | WorkerRepoCapsule / CodeContextBroker v1: cited files' ast outline + direct tests + importers, ~1k tokens | scripts/code_capsule.py | RepoMap/Serena later, only as a benchmarked backend |
| 3 | Acceptance fast path: the worker's new tests run RED against the base code and GREEN with the change | scripts/build_integrate.py | deterministic negative control replaces an LLM reviewer for low/medium risk |
| 2 | Deterministic merge fast path: worker commits on build/<req>; check base SHA, file overlap; cherry-pick; affected tests; revert on failure | scripts/build_integrate.py | workers commit in their own branch (owner v9 §51); integration is code |
| 7 | Review economics: a fresh verifier only for P0/security/trust boundary, budget <= 25% of the implementer's, JSON receipt PASS/FAIL/INSUFFICIENT_EVIDENCE | scripts/build_integrate.py | |
| 1 | Fresh controller per wave: `scripts/build_program.py` loops waves in code; state in `data/build_state.json` + `docs/implementation/PRD_CURRENT_STATE.md`; an LLM is called only for escalations | scripts/build_program.py | total-spend cap per run; stops on no ready work, RED pressure, repeated failures |
| - | Ledger application from worker receipts (structured fields) by the integrator | build_integrate.py | only the integrator edits the ledger |

Split for parallel workers (disjoint files): A = build_kernel.py (9, 8, 10, 6); B = code_capsule.py (4/5);
C = build_integrate.py (3, 2, 7, ledger application); then D = build_program.py (1) once A-C land.

## Status 2026-09-30 23:45 - A, B, C, D landed

All four ran as kernel workers (Sonnet, no MCP/hooks), each verified by its tests plus mutation runs (A 7/7,
C 10/10, B 1/1, D 10/11 killed; D's survivor is a redundant spend check covered by wave trimming).

| Worker | Commit | Turns | Cost | Wall |
|---|---|---|---|---|
| A kernel (9, 8, 10, 6) | 8617c31 | 34 | $1.09 | 277 s |
| B capsule (4/5) | 451906d | 17 | $0.36 | 155 s |
| C integrator (3, 2, 7) | 7e7d5c3 | ~25 | $0.73 | ~200 s |
| D program (1) | 07ab267 | 31 | $0.81 | 205 s |

D was the first worker integrated by `build_integrate` itself: RED (collection) at base, GREEN at the commit,
cherry-picked, affected tests green, worktree and branch removed - 29 s, no model.

Defects the real runs found that the workers' own tests could not (all fixed test-first):
- a17b9e7: the kernel-side commit used an exclude pathspec for `.build`; git exits non-zero when a pathspec
  names a path `.gitignore` ignores, so BH-D's commit failed with the work staged.
- 8617c31 (controller review): scouts ran with `--permission-mode acceptEdits`, which approves edits whatever
  the allowlist says; scouts now run in `default` mode.
- 59815a0: `build_program` rewrote the tracked `PRD_CURRENT_STATE.md` after every wave, and `build_integrate`
  refuses a checkout with tracked changes - every wave after the first would have been parked.

Rulings (reversible; consequence if wrong in brackets):
- The kernel's code commits a DONE worker's diff on `build/<req>`; the model never commits. Reconciles v9 §51
  with house rule 14. [workers would need commit rights - an allowlist change]
- `build_program` owns only the `<!-- build-program:begin/end -->` block of `PRD_CURRENT_STATE.md` and writes
  it once per program run; `data/build_state.json` is the per-wave state. [the doc lags a crashed run]
- A row integrated PARTIAL twice is parked (escalation `partial`), not relaunched by every run.
- Default spend cap $10/run fits one P0-class worker per wave; raise `--max-usd` for two.

## Resume (fresh session)

Current: harness complete at 59815a0. Next: `build_program.py --priority P0` in the background, then read
`data/build_state.json` (stop_reason, waves, parked, escalations) - not the worker transcripts.

Older resume steps, kept for the record:

1. `git status`, branch `prd-v7/slice-7-adaptive-hardware`, HEAD f12f027 or later; no stray `.worktrees/`.
2. Copy the owner's v9 prompt and research reply verbatim into this folder (extraction, not retyping).
3. Dispatch A, B, C as Sonnet workers in HEAD-based worktrees (`git worktree add .worktrees/<task> -b build/<task> HEAD`),
   verify each (tests, one mutation), integrate, commit; then D.
4. Then run the program on the P0 waves (`build_kernel.py waves`), full suite only at checkpoints
   (last full: 258e3c0, 4,753 passed).
