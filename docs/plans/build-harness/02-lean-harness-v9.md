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

## BH-OPT-03 - what the owner's v10 directive and the reaped run still require (2026-10-01)

v10 (`00-owner-directive-2026-10-01.md` when extracted) adds: the capsule must win an A/B before it is trusted
(§11), per-worker token/cost/ROI accounting (§19), concurrency from the governor sized for this host (§7),
and resume from artifacts after an interrupted session (§40). The reaped run shows why §7 is not optional:
the kernel's process governor had a provisional baseline (its own first reading), so a loaded host read NORMAL
and two workers were admitted at 3.1 GB available. No measured profile is stored on this host.

One worker (all four touch build_kernel.py), COMPLEX class, launched detached:

| # | Upgrade | Where |
|---|---|---|
| E | Resource-true concurrency: governor configured from a build-host profile (`build_kernel.py qualify` -> `data/build_runs/host_profile.json`, never the live profile); CRITICAL 0, HIGH 1, else min(cap, max_workers, headroom slots); slots = (available - reserve) / measured worker peak (default 1.5 GB) | build_kernel.py |
| F | Worker peak RSS measured per run (process tree, psutil) | build_kernel.py |
| G | Resumable killed runs: `.build/run.json` at launch; `resume REQ` relaunches `--resume <sid>`; the program resumes a resumable stale worktree once instead of parking it | both |
| H | Capsule A/B arm (stable 50/50 by id until decided), orientation metrics (searches, tool calls, tokens before first edit), `roi` report incl. AgentROI | both |

Product follow-ups stay with their rows, scheduled by the program: persisting the refined baseline across
restarts (RES-003 EXTEND) and measuring a worker's memory into the derived policy (RES-002 EXTEND).

### Status 2026-10-01 23:10 - BH-OPT-03 and BH-OPT-04 landed

| Change | Commit | How verified |
|---|---|---|
| BH-E: RAM-slot concurrency, build-host profile, peak RSS, resume, capsule A/B arm, `roi` | 185966c | 12/12 mutations killed; RED/GREEN via build_integrate |
| Worker-written commit subjects | 7b34181 | tests first |
| Verifier budget floor $1, 15 turns, explicit model, receipt keeps cost + stop reason | 4c15a45 | tests first; reproduced the budget-stop JSON shape |
| BH-F: repair loop - one squashed commit per run, `repair REQ`, program queues one repair before parking | 94a3f29 | 7/7 mutations killed; RED/GREEN via build_integrate |

Measured on this host: build-host profile stored (`data/build_runs/host_profile.json`); worker peaks 648 MB
(BROW-001 resume) and 409 MB (BH-F) against the 1.5 GB default. At 2.5 GB available the kernel computed 0
slots before any measurement; the measured peaks raise that to one worker.

BROW-001: the killed run resumed in 6 turns ($0.32). Its first integration hit VERIFIER_FAIL with an empty
receipt (the defect fixed in 4c15a45); the second, a real verdict ($0.12): the acceptance needs the golden
journey run, two tests in tests/test_capability_router.py share a name (one never runs), and execution
reliability is per capability, not per request shape. Those findings went back to the worker via `repair`.
CORE-001's killed run is still on disk, resumable.

### Status 2026-10-01 23:40 - program running on P0, stopped on host memory

- BROW-001 VERIFIED (ae9ece3): resume -> verifier FAIL (real findings) -> `repair` -> verifier PASS -> live golden
  journey re-run outside the worker (1 passed). Per-request-shape scoping ruled a refinement (its next action).
- Fixed on the way: VERIFIED ledger entries now carry evidence (9c5f438 - `--check` would have refused them);
  a parked requirement no longer stops the program (2ccccc9 - CORE-001's two rejections had stopped it).
- CORE-001 PARKED (verifier FAIL twice, worker itself `acceptance_met: false`); escalation in
  `data/build_state.json`. Needs an OWNER decision: must every Hermes delegation have a verifier, and how is a check
  derived from free-text acceptance? Also open: verifier blocks the gateway reader thread up to 120 s; verdict not
  written to the objective store; an `audit.sqlite3` appears in the delegation workspace. Branch build/core-001 kept.
- Third program run stopped at once on `pressure`: 3.1 GB available, reserve 1.5 GB, worker estimate 1.7 GB
  (CORE-001's measured peak; BH-F 409 MB, BROW-001 776 MB) -> 0 slots. Chrome held ~7 GB. Owner choices: free
  memory, accept a smaller reserve, or run on REFERENCE-B. The gate is not overridden by the controller.

## BH-OPT-05 - owner research and memory guidance (2026-10-02, `00-owner-research-2026-10-02.md`)

Landed now (owner asked for them before the P0 run): HIGH-pressure negative controls (2.9 GB -> 0, 4.5 GB -> 1);
a per-wave budget (default half the run budget; it limits extra workers, never the first); a costly wave without
progress (>= 25% of the run budget, nothing settled) stops the run; a pressure stop is recorded as
`status: WAITING_RESOURCES` with the governor's reason, not as a failure.

Next, in order (build-harness obligations, not PRD product rows):
1. Memory admission v2 (after the first P0 wave runs, as the owner sequenced it): physical AND commit headroom;
   predicted peak per requirement (its last measured run) else per worker class (p95) else default, with
   predicted-vs-actual error in each run record; a memory lease (check -> lease -> recheck -> spawn, released
   or replaced by measured use) so two kernels cannot spend the same headroom. Until it lands: `--cap 1`.
   Keep an append-only peak history per requirement: run records are overwritten per requirement, so
   CORE-001's 1.7 GB peak vanished when its next (lighter) run replaced the record.
1b. Model escalation (v10 §17/§18): when a requirement's repair is rejected again, one fresh attempt on a
   stronger model with the verifier's findings before it is parked (ACT-001: five concrete honesty-audit defects
   after its repair).
1c. BH-G (running 2026-10-02): the program crashed integrating DOMAIN-001 - ~all of tests/ selected, one
   900 s subprocess, TimeoutExpired uncaught, an unverified cherry-pick left on the branch (the controller reset
   it to dbc237c; the work is on build/domain-001). BH-G: chunked gate runs + TEST_TIMEOUT, the program resets
   and parks on any integrator crash, per-run accounting by stream offset, append-only peak history.
1d. BH-H (owner guidance `00-owner-directive-2026-10-02b.md`): attempt identity - an append-only invocation
   ledger (attempt_id, run_id, wave_id, req, cost, tokens, peak) that spend/ROI are projections of, with the
   owner's six regression cases; INVOCATION / WAVE / RUN / LIFETIME cost kept apart; retry context growth
   recorded; repair policy - attempt 2 may resume, attempt 3+ is a fresh session with a structured failure
   packet; `escalate REQ` - fresh session, stronger model, packet from the integration record; a program lock
   so a second program cannot start (master controller §3). Then CORE-001 runs through `escalate` with the
   owner's four-item packet: jailed derived check, audit.sqlite3 root cause, no acceptance contract is never
   COMPLETE, verdict persisted only when correlated to the objective run.
1e. BH-I - Sparse Agent Fabric (owner, `00-owner-directive-2026-10-02c.md`), after BH-H, before the memory
   lease and before CORE-001's escalation. Agents are short-lived compute units under the code controller; one
   RAM slot limits concurrency, not specialisation (sequential fresh specialists on this laptop, parallel on
   REFERENCE-B, same scheduler). Bounded scope - what the harness does not already have:
   - AgentJob records on the BH-H invocation ledger with roles (SCOUT, IMPLEMENTER, REPAIR, VERIFIER,
     ADVISOR) and durable states (QUEUED, STARTED, WAITING_RESOURCES, COMPLETE, FAILED) so a restarted
     program adopts running/finished jobs instead of re-launching them;
   - ScoutCapsule (likely files, symbols, tests, risk) handed to the builder - never the scout's transcript;
     FailureCapsule (failed / expected / observed / ruled out / reproduction / files / diff / tests) for every
     fresh repair; attempt 2 resumes only when the failure is localized, attempt 3+ is always fresh;
   - model cascade on repeated failure: normal -> same tier, different approach -> fresh strong model ->
     ADVISOR (strong model writes a diagnosis packet, a normal implementer applies it);
   - AgentUtilityScore: one deterministic function, with its reasons recorded per job, deciding scout or not,
     verifier or not, fresh or resume, parallel or sequential;
   - TestJob: a WIDE_IMPACT_CHANGE classification when the affected set is large, run as governor-sized chunks
     (BH-G gives the chunking); coding agents never wait on test runs;
   - MergeQueue: integrations serialized with base-staleness checks -> REBASE_REQUIRED instead of a blind pick.
   Already in place (not rebuilt): deterministic DAG/ready/waves, governor-derived concurrency, worktree
   isolation, model-free watcher, RED/GREEN + affected tests as subprocesses, one verifier only when warranted.
   After P0 runs reliably: orientation arena, testmon, Ponytail A/B - then freeze the harness.
2. TEST-IMPACT-001: pytest-testmon spike in .venv-build against `scripts/test_impact.py` on real changes
   (recall of the tests that actually failed, time saved); full suite still at every checkpoint; pytest-xdist
   only with a governor-chosen worker count, never `-n auto`.
3. ORIENT-001/002: orientation bake-off on the same real tasks - capsule arm data from `build_kernel.py roi`
   first, then Serena, codebase-memory-mcp and an Aider-style repo map in isolated sandboxes (never the live
   environment); keep one primary and one fallback, remove the losers.
4. HARNESS-EVAL-001: every claimed token/time saving is judged on paired end-to-end tasks, never on a tool's own
   counter. Ponytail is an A/B candidate; RTK stays rejected (FRIDAY's own measurement and JetBrains' paired
   benchmark agree) unless a paired FRIDAY benchmark reverses it.
Then stop harness work and drive P0 to zero. References only (design mines, not installs): GitNexus, Repomix,
code2prompt, Gas Town/Beads, Emdash, Claude Squad, DeepAgents, SWE-agent ACI, wshobson/agents, Superpowers,
Context7, OpenHands SDK.

### Status 2026-10-02 14:30 - BH-G, BH-H, BH-I landed; BH-J running

| Worker | Commit | Cost | Verified by |
|---|---|---|---|
| BH-G chunked gate, crash recovery, per-run spend, peak history | c161ba3 | $0.89 | 4/4 mutations, RED/GREEN |
| BH-H invocation ledger, fresh repair, escalate, program lock | 9181ca5 | $1.67 | 3/3 mutations, 251 harness tests, RED/GREEN |
| BH-I sparse agent fabric: job records + adoption, agent_utility, cascade to ADVISOR, wide impact, merge queue | 6abc63e | $5.14 | 4/4 mutations, replaced tests checked for weakening, RED/GREEN |

BH-I's own gaps: QUEUED/WAITING_RESOURCES job states defined but not written (folded into BH-J); parallel gate
chunks exercised only in unit tests (this host runs one). CORE-001's measured 1.7 GB peak was restored into
`data/build_runs/peaks.jsonl` (it predated the history) so admission stays conservative until BH-J predicts per
requirement. Product amendments recorded meanwhile: A8 Capability Forge (083ca38), A9 Workflow Fabric (472d561).

### Status 2026-10-02 15:45 - harness complete through BH-J; DOMAIN-001 in; CORE-001 ready to escalate

- BH-J memory admission v2 (36711f4) + the admit-lease test (79c5b0e). DOMAIN-001 INTEGRATED (0e92e8c, PARTIAL:
  the restricted-domain phrase list is an assumption awaiting the owner; refusal recording not wired to a live
  provider path). A8/A9 had broken tests/test_prd_ledger.py's pinned count - fixed in de18900.
- CORE-001: branch build/core-001 rebased onto de18900 (e49549f, no conflicts; its tests + DOMAIN-001's: 72 passed);
  `.worktrees/core-001/.build/run.json` base_sha updated. Next: `build_kernel.py escalate CORE-001 --model opus
  --packet docs/plans/build-harness/core-001-escalation-packet.md`, started with Start-Process when
  `build_kernel.py concurrency` shows a slot (needs ~3.2 GB available). The harness's slot watch was reaped for low
  memory at 15:45; nothing else was running.
- Then: `build_program.py --priority P0 --max-waves 6 --max-usd 15` (Start-Process). ACT-001 parked (verifier: five
  honesty-audit defects) is a cascade candidate.

## Dual track (owner, 2026-10-02, `00-owner-directive-2026-10-02f.md`) - FRIDAY is the product

The owner corrected the order: the build had been finishing its harness before building FRIDAY. From now on:

- Three lanes: PRODUCT (PRD requirements, P0 first), HARNESS (defects in the build machinery), RESEARCH
  (narrow, when a product decision needs it). Target rolling share: product 70-80 %, harness 20-25 %, research
  0-10 %. On a one-slot machine the lanes interleave; with two slots one is reserved for product while P0 work
  is runnable.
- A harness task outranks product only when it prevents a crash, corruption, an authority failure, wrong budget
  enforcement, unsafe admission, broken integration/rollback, or blocks all ready product work. Every harness task
  records its measured problem, product impact, expected gain, acceptance test and the product task that will
  dogfood it - and is judged on that real task, not on synthetic tests.
- HARNESS_CORE_READY (declared 2026-10-02, provisional until CORE-001 exercises fresh-session escalation): the
  program survives failures (BH-G), budget accounting is correct (BH-H), admission is safe (BH-J), workers are
  isolated (kernel worktrees), integration rolls back (build_integrate + BH-G), fresh-session escalation exists
  (BH-H). After it, harness work is reactive by default; BH-OPT-05 items 2-4 (testmon, orientation bake-off,
  Ponytail A/B) run only when a measured product problem asks for them. Lane-share accounting is built only if
  harness work starts competing with product again.
- Owner rulings in force: DOMAIN-001 uses `cbrne` and `cyber_sensitive` with a separate cyber intent/authority
  classification (ledger next action); CORE-001 escalates only after two consecutive readings of >= 3.5 GB
  available.

Live at 17:33 2026-10-02: a detached chain (pid 1672, log `data/build_runs/core_chain.log`) waits for two
consecutive readings of >= 3.5 GB available, then runs CORE-001's fresh Opus escalation with
`core-001-escalation-packet.md`, `build_integrate.py CORE-001`, and `build_program.py --priority P0 --max-waves 6
--max-usd 15`. While it waits, `data/build_state.json` records CORE-001 as WAITING_RESOURCES; after 6 h it records
MEMORY_ADMISSION_TIMEOUT (not FAILED) and exits. If the escalation still misses the four conditions, CORE-001 stays
parked with the new evidence and the program continues other P0 work.

## v12 - parallel product build and the EXE gate (owner, 2026-10-02, `00-owner-directive-2026-10-02g.md`)

- CORE-001 is PARKED_BY_OWNER (first-class non-terminal ledger state, 5b7a71c); the product lane runs through
  `scripts/product_chain.py` (admission-gated, bounded runs, $45 total default).
- Models: deterministic code for scheduling, tests and merges; Sonnet implementers; Opus for escalations, the advisor
  step and trust-boundary verification; parallel workers only as memory admission allows (one at a time on this
  host at ~1.7 GB predicted per P0 worker).
- EXE states: EXE_NOT_BUILT, EXE_BUILDING, EXE_BUILT_UNVERIFIED, EXE_VALIDATING, EXE_BLOCKED_EXTERNAL, EXE_READY.
  EXE_READY only after the release gate (launch without the source checkout, `--doctor` passing or naming accepted
  external blockers, state outside the bundle and surviving restart, golden journeys through the exe, full
  regression on the exact candidate, build identity recorded) - then `data/release/EXE_READY.json` and the owner is
  told. A PyInstaller success alone is never EXE_READY.
- Packaging lane: build candidate exes at checkpoints; packaging bugs are product bugs. Known release item:
  `build_windows.py` marks a build dirty from `git status --porcelain`, so untracked `data/` outputs make every
  build "344d62a+dirty" (channel dev) even with a clean tracked tree.

## Resume (fresh session)

`git status`; read `data/build_state.json` (parked, escalations, stop_reason); `build_kernel.py concurrency`;
then `build_program.py --priority P0 --max-waves 6 --max-usd 15` started with Start-Process (never as a harness
background task).

Earlier: harness complete at 59815a0. Next: `build_program.py --priority P0`, then read
`data/build_state.json` (stop_reason, waves, parked, escalations) - not the worker transcripts.

First P0 run (2026-09-30 23:44, `--max-waves 3 --max-usd 15`) was **killed by Claude Code's low-memory reaper**
one minute into wave 1 (2.3 of 15.4 GB free). The reaper kills the whole tree it launched, so both workers died
with it; `CREATE_NEW_PROCESS_GROUP` does not escape it. No state file was written. Left on disk, uncommitted:
- `.worktrees/brow-001` (build/brow-001): 5 files, +60 (action_path, capability_router, planner, routing_memory,
  test_capability_router); session `994c165a-9aa3-43e7-a07a-0b98a0426ba0`.
- `.worktrees/core-001` (build/core-001): hermes_control.py +64; session `bfe81b84-af46-4448-8e8d-b526f8638ff1`.
A new program run parks both as `stale worktree` (safe). Options: resume each session in its worktree
(`claude -p --resume <sid>`), or remove the worktrees and let the next run start them fresh.

Before the next run: start it detached (`Start-Process ... -WindowStyle Hidden`, then a Monitor on the log /
state file), never as a harness background task; on this host prefer `--cap 1` while headroom is this low. The
governor allowed 2 workers at launch - two `claude -p` workers plus their pytest runs outgrew the headroom, a
data point for the resource governor's thresholds. A kernel `resume REQ` command (session id from the stream's
init line) would make a killed run recoverable in code.

Older resume steps, kept for the record:

1. `git status`, branch `prd-v7/slice-7-adaptive-hardware`, HEAD f12f027 or later; no stray `.worktrees/`.
2. Copy the owner's v9 prompt and research reply verbatim into this folder (extraction, not retyping).
3. Dispatch A, B, C as Sonnet workers in HEAD-based worktrees (`git worktree add .worktrees/<task> -b build/<task> HEAD`),
   verify each (tests, one mutation), integrate, commit; then D.
4. Then run the program on the P0 waves (`build_kernel.py waves`), full suite only at checkpoints
   (last full: 258e3c0, 4,753 passed).
