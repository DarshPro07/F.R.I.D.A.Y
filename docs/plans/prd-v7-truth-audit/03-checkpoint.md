# Checkpoint: PRD v7 build, Slice 2 steps 1-7 and the review fixes

**Date:** 2026-09-26
**Plan:** `02-build-sequence.md` (approved by the owner 2026-09-25)

## OBJECTIVE

Build PRD v7 slice by slice until every applicable requirement is terminal
(VERIFIED / BLOCKED_EXTERNAL / DEFERRED_BY_PRD / NOT_APPLICABLE / REJECTED_WITH_EVIDENCE).

## HEAD

`030795f`, tree uncommitted. Agents never commit; the owner does (rule 14). No push, so CI is NOT_RUN.

## COMPLETED (Slice 1)

| Row | Result | Evidence |
|---|---|---|
| TRUST-001 | VERIFIED | `tests/test_trust.py::test_a_payload_that_declares_itself_trusted_stays_untrusted` plus the browser nonce tests; mutations red |
| COG-001 | VERIFIED (scope: the task pipeline) | `tests/test_zero_frontier_calls.py`: 0 model and 0 non-loopback network calls end to end; negative control moves the counter |
| NEURAL-002 | VERIFIED | `tests/test_neural_boundary.py`: AST scan plus 16 planted primitives; plants in real modules turn it red |
| CORE-001 | PARTIAL | policy now at dispatch (`hermes_control.py:113-120`); verifier not on the return path (V7-D07) |
| EVAL-001 (a) | PARTIAL | `graded()` tested on every path; suite pollution of the live record fixed (V7-D05) |
| VERIFY-002 | PARTIAL | registry, meta-test and release gate; the real gate reads FAIL 11/12 (lossy compaction is owned by CTX-003) |
| TRUST-002 | VERIFIED -> PARTIAL | lineage dropped on the durable path (V7-D04) |
| SEC-004 | stays VERIFIED | self-confirmation hole V7-D01 found and fixed |

Ledger: `scripts/prd_ledger.py` `PROGRESS_V7`; `--set v7 --check` is clean. v7 now has 25 VERIFIED and 17 unresolved P0 (was 19).

## BLOCKED

- VOICE-001 latency: real LiveKit credentials (owner).
- PKG-001: a clean Windows VM or Windows Sandbox (owner).
- CI on the exact commit: needs commit and push authorisation (owner).
- `data/evaluation/attempts.json` quarantine: owner action (V7-D05).

## FAILED TESTS

None in the targeted runs. `tests/test_browser_primitives.py::test_live_*` need `playwright`,
which `.venv-build` lacks; they are `@pytest.mark.live` and outside the offline gate.

## KEY DECISIONS

- SANDBOX is not the default for coding runs (owner, 2026-09-25): HOST stays the default, and SEC-005
  is met by a FRIDAY-enforced envelope.
- A red-path registry entry with no test keeps the release gate at FAIL; a planned control is not a control.
- A control counts only if at least one of its cases PASSED (exit 0 with every case skipped is not evidence).

## ARTIFACTS

- New tests: `tests/test_neural_boundary.py`, `tests/test_negative_controls.py`,
  `tests/negative_controls.py`, `tests/test_zero_frontier_calls.py`, `tests/test_browser_confirmation.py`.
- Extended tests: `tests/test_evaluation.py`, `tests/test_release_gate.py`, `tests/test_trust.py`,
  `tests/test_file_control.py`, `tests/test_browser_primitives.py`, `tests/test_prd_ledger.py`,
  `tests/conftest.py`.
- Product: `friday/browser.py`, `friday/toolsets/web.py`, `friday/tools/web_control.py`,
  `friday/tools/file_control.py`, `friday/tools/hermes_control.py`, `friday/continuous.py` (comment),
  `scripts/release_gate.py`, `scripts/prd_ledger.py`.
- Docs: `docs/implementation/PRD_DEFECT_REGISTER.md`, this file, regenerated `docs/implementation/PRD_V7_LEDGER.md`,
  `data/prd_v7_requirements.json`, `data/prd_v7_verdict.json`.

## REVIEWS

- **Slice 1, `friday-final-reviewer`: SHIP-WITH-FIXES.** All four fixes applied and re-verified:
  1. TRUST-001's text scoped to runs that carry their provenance, pointing to TRUST-002.
  2. COG-001 now covers five requests, has a negative control for every counted door, and a static by-name-import
     guard.
  3. The negative-control gate judges every named test through JUnit, allows only listed host skips, and refuses
     stale reports.
  4. The NEURAL-002 scan catches direct weight edits (`.data` in-place ops, `.grad`, `requires_grad`).
- **Slice 2 design, `friday-tech-lead`: APPROVE-WITH-CHANGES.** The changes are folded into
  `04-slice-2-design.md` ("Review outcome" and "Sequencing (revised after review)").

## SUITE (Slice 1 tree)

`data/baseline_v7_slice1/RESULT.md`: **4,360 passed, 73 skipped, 0 failed** (unique tests, four chunks plus the
remainder of chunk 0). Chunk 0 hung once in `tests/test_dbconn.py`'s all-handles helper (V7-D09); that test
passes in a fresh process. The previous baseline (2026-09-23) was 4,053 passed, 73 skipped.

## SLICE 2 PROGRESS (2026-09-26)

| Step | Result |
|---|---|
| 1. Contract types | `Envelope`, `NetworkScope` and `Limits` live in `contracts.py`; narrow-only; the duplicate `sandbox.py` is retired; `execution.py` and `contracts.py` are kernel (V7-D10). |
| 2. Lineage on the durable path | Lineage recorded on `objective_runs` and schedules; legacy rows park. Command execution is unreachable from read material (V7-D13). TRUST-002 VERIFIED. |
| 3. Native enforcement | Capability membership and expiry are checked by the executor and by the runtime; file roots read vs write; token and tool-call limits tighten the budget; expiry parks; a session approval cannot lift a block. |
| 4. Shared confirmation book | The book is store-backed with atomic, instance-bound spends, and the spoken or typed yes is wired into both voice paths (V7-D08). |
| 5. NET-001 | Grants are issued, audited and revoked from any process; netguard scope covers fetch, research (both engines, V7-D14) and browser navigation. VERIFIED. |

| 6. CAP-002 | The capability set is judged at compile: read material or unattended is refused, the person confirms, and DANGEROUS runs as an audited standing yes. Seven ungated capabilities were categorised (V7-D15). VERIFIED. |
| 7. STOP-001 | A durable latch, grant revocation, parked work, per-process hooks with a watcher, PID kills, CLI and spoken triggers (no model), and an explicit resume that survives a restart. `server.main` fixes `uv run friday` (V7-D16). VERIFIED. |
| 8 (part). Worker environment | HOST coding workers get the allowlisted environment, not FRIDAY's secrets (V7-D18). Tool lists, network rules, fabric and Hermes compilation remain. |
| Hygiene found on the way | A bare `Store()` meant the live database regardless of `ADA_DB` (V7-D17), fixed at the store. |

Ledger: 29 VERIFIED, 13 unresolved P0. Suite over the Slice 1 tree: 4,360 passed, 0 failed.

Suite over the Slice 2 tree (`data/baseline_v7_slice2`, commit `030795f` plus the uncommitted tree):
4,472 passed, 2 failed (chunk 2). Both failures were traced to their roots and fixed:
`test_prompt_transport` (the worker allowlist stripped the fake CLI's own variables; fixed with named
passthroughs), and `test_orgplane` (V7-D19 and V7-D20 below).

## SLICE 2 REVIEW AND FIXES (2026-09-26)

`friday-final-reviewer` returned SHIP-WITH-FIXES with six findings. All six are dispositioned in
`04-slice-2-design.md` ("Implementation review"), each with a failing-first test and a mutation:
V7-D22 to V7-D27 in the register. Finding 4 was raised from Low to High: a worker poll during STOP could drive
a gateway restart. Found on the way: V7-D19 (six ledgers ignored `ADA_DB`; soak and golden runs wrote them
into the live database), V7-D20 (a test fixture left the config pointing at a deleted directory; a conftest
hook now catches any test that does this), and V7-D21 (Hermes reader threads died on every orderly stop).
Left open with evidence: V7-D28 (conversation `Run` lineage defaults, 69 sites, a design task for
TRUST-001/002) and V7-D29 (`secrets_list` policy categories disagree between aliases).

## PRD AMENDMENTS (2026-09-26, 2026-09-27)

- A1 (§95, Appendix C): domain partition and the updated controller; 37 rows. V7-D30 and V7-D31 fixed on the way.
- A2 (§96, Appendix D): durable execution kernel, behavioural contracts, signal bus, approval leases,
  just-in-time authority, telemetry, trajectory debugger, capability hints, A2A federation, WebMCP, the
  connectivity bake-off and the external eval pack; 14 rows (`DUR-001`...`EXCH-001`). P0: DUR-001, BCON-001,
  APPROVAL-001, JIT-001. §96.1 freezes the v8 core: no further named systems until the first baseline run.
- A3 (§97, Appendix E): the local-model hardware rule (default artifacts under 2 GB, one resident general
  model, vision on demand, 4K-8K default context) and the prompt research corpus (REFERENCE_ONLY, licence
  unread, never copied in); 4 rows (MODEL-007, MODEL-008, PROMPT-001, PROMPT-002), none P0. Filed within the
  §96.1 freeze: PromptOS is recorded as context-engine obligations, not a new core system. The dev host measures
  15.4 GB RAM and an integrated GPU with 0.5 GB dedicated, not the stated 16 GB / 4 GB VRAM profile.
- Ledger now 124 requirements: 31 verified, 91 working, 21 unresolved P0, 55 P1; `--check` clean.

## SLICE 2 STEP 8 (2026-09-27)

- 8a Claude Code CLI: `brokers.within_envelope` compiles the launch (dontAsk; roots as `--add-dir`; no bare
  file-tool rule; `Edit(//root/**)` under write roots; WebFetch per network scope; no shell for READ_MATERIAL);
  the executor refuses before admission, clamps to expiry, stops past the tool-call limit. 17 tests, 13 mutations.
- 8b Hermes: the durable path reaches Hermes by router fallback (MCP, no Run), and the supervisor is the MCP
  server's, so a runtime toolset would be a second orchestrator. Instead the port names its objective to
  `RUN_BOUND_TOOLS`, `hermes_delegate` loads that objective's stored envelope itself (`bounded_by`: session cwd,
  scope in the task, refusal), and the supervisor interrupts at expiry. 10 tests, 8 mutations.
- 8c Fabric: sidecars are shared processes and no toolset reaches the fabric; a tripwire test names a toolset
  that starts to.
- 8d Child envelopes: `DevelopmentRun` narrows the parent for its sub-worker and skips the Hermes team path
  under an envelope. 3 tests, 4 mutations.
- Also fixed: V7-D32 (trace audit order), V7-D33 (silent run-record save failure).
- Baseline `data/baseline_v7_a1`: chunks 0, 1, 3 exit 0; chunk 2 two failures, both dispositioned (the ledger
  count raced my A2 edit; the trace test found V7-D32).
- Pending: regression run over the 31 touched suites, `friday-final-reviewer` on step 8, then PROGRESS_V7.

## SLICE 3: MEMORY TRUST (2026-09-27)

- MEM-002: `Candidate.provenance` required, stored as `memories.trust_class`; read material cannot set a
  preference, record a decision, speak in the person's scopes, or overrule the person; the person's word replaces
  it. MEM-003: a poisoning lifecycle (write, age, cannot settle a worker's question, forget, repair).
- V7-D34 (security): the ledger records who wrote each entry; worker and read-material writes go through the gate.
- Found on the way: V7-D35 (executor stream limit, by the live CLI check), V7-D36 (ui_server daemon at import),
  V7-D37 (newest-run ties).
- Review (friday-final-reviewer): SHIP-WITH-FIXES, no guard broken. Dispositions, each with a failing-first test
  in `tests/test_memory_trust_legacy.py` and a mutation:
  1. Medium, legacy rows trusted: the migration marks rows from the read producers' code-set sources
     (`handoff:`, `skill:`, `worker:`, `resolved from `) READ_MATERIAL, once, as the column arrives. Legacy ledger
     entries: fail closed, not rendered as rules; the person restores one by marking `"origin": "person"`.
     *Assumed fail-closed because entries do not say who wrote them and a worker could write the ledger before
     V7-D34; revisit if the owner prefers another default.*
  2. Medium, `youtube.remember_channel` wrote a page-derived title unclassified: now READ_MATERIAL (and its silent
     `except: pass` logs).
  3. Low, only the newest row checked: every row a read candidate would retire is checked.
  4. Low, `Run.provenance` defaults to PERSON: the known V7-D28 residual, unchanged.

## SLICE 4: OBJECTIVE OS (2026-09-27, in progress)

- INTENT-001: objective contract (`contracts.ObjectiveContract`, `objective_runs.contract`), literal
  preservation (split, quotes, named paths and content, no invented words), purpose vs step, compile-time literal
  gate (`objectives.LiteralDropped`), end-to-end journey to a file on disk. V7-D39 (prohibition planned as the
  forbidden action) and V7-D40 (literals lost) fixed. Open: contract assumptions/open questions; V7-D38 (a turn that
  is both an idea and a command plans before raising questions).
- Suite over this tree (`data/baseline_v7_slice4a`): all four chunks exit 0, 4,597 passed.
- Ledger: SEC-005, MEM-002, MEM-003, INTENT-001 recorded PARTIAL with their residuals.
- TRAJ-001 (`friday/trajectory.py`): no-progress, same-cause, near-miss, burn and side-effect-start signals
  pause a drifting run (`trajectory.flagged`); a synthetic drift benchmark. Open: alignment of steps with the
  contract's goal and scope.
- A2 P0s, all PARTIAL with stated gaps: APPROVAL-001 (approval bound to plan hash, graph version and expiry),
  JIT-001 (capability source fingerprint re-checked before execution), BCON-001 (forbidden action classes,
  protected names, change ceiling in the envelope), DUR-001 (`side_effect.committed` before success; recovery
  completes from it; V7-D42 fixed).
- Suite over the tree with DUR-001 (`data/baseline_v7_slice4e`): all four chunks exit 0, 4,669 passed.

## SLICE 4 REVIEW AND CTX-003 (2026-09-27)

- `friday-final-reviewer` on the Slice 4 / A2 work: BLOCK, two high authority bypasses reproduced. All five
  findings fixed test-first: V7-D43 (an approval on one objective authorised another, or a call outside any
  objective), V7-D44 (`narrow()` could drop pinned capability versions, switching JIT-001 off), V7-D45 (work in
  flight on a Hermes worker not counted toward the change ceiling), V7-D46 (plan edits not held to the
  contract; latent), V7-D47 (a side-effecting step that succeeded after retries still paused the run).
- The V7-D43 fix exposed V7-D48: the golden bench dropped the Run on its way to the runtime and minted envelopes
  from the machine's roots; fixed, and all 164 golden cases pass with the runtime's checks now exercised.
- CTX-003 (`friday/compaction.py`): the critical fact set read from the objective's record, a validator that
  refuses a packet missing any fact; objective delegation to Hermes and the voice continuation packet both
  carry and check it. PARTIAL: the Claude Code development packet does not yet.
- VERIFY-002 VERIFIED: with the lossy-compaction control registered, the release gate's negative-control check
  reads PASS, 12/12 (`data/prd_v7_audit/negative_controls_2026-09-27`).
- Ledger: 124 requirements, 32 verified, 20 unresolved P0.

## SLICE 5: CORTEXOS CORE (2026-09-27)

- CTX-003 also wired into the voice continuation (`continuity_livekit`): the packet is built from the run's
  facts and checked before the claim is taken. Remaining seam: the Claude Code development packet.
- CTX-002: `capability_router.disclosure_report` and `scripts/bench_tool_disclosure.py` measure progressive
  tool disclosure over the full catalogue (190 tools): starting surface 83.9% smaller, every long-tail request
  at least 80% (worst 81.5%). Single-search recall on the development split is 75.9% (732/965), a stated gap;
  the locked holdout was not read. Evidence: `data/prd_v7_audit/ctx002_tool_disclosure_2026-09-27.json`.
- CTX-001: `model_gateway.compile_working_set` (section 19.2 order, 19.4 priorities, budgeted drops, P0 never
  dropped, per-section telemetry); an objective's model step carries its critical facts as data. No caller
  sets a budget yet.
- All three PARTIAL; unresolved P0 count unchanged at 20 (they were already counted PARTIAL).

## SLICE 6: RESOURCES AND BROWSER (2026-09-27)

- RES-001: `scripts/stress_low_ram.py` drives real memory pressure (a child allocates and touches up to 1.2 GB)
  against the governor's real sampler: ELEVATED shed optional work at +300 MB, HIGH queued the second worker at
  +600 MB, CRITICAL started nothing at +750 MB, and admission recovered after release. Slow test
  `test_real_memory_pressure_bounds_new_work`. Finding for the owner: this host idles at ~76% RAM, above the
  default 75% ELEVATED threshold, so optional work is shed at rest.
- BROW-001: `friday/action_path.py` (section 48 order, reliability demotion); capability search offers tied
  matches most direct path first without changing what is offered. The real browser journey is blocked here:
  Playwright is not installed in `.venv-build`.
- Both PARTIAL.

## SLICE 5/6 REVIEW (2026-09-27)

- `friday-final-reviewer`: SHIP-WITH-FIXES, no authority bypass, nothing fails open. Fixed test-first: V7-D49
  (medium, prompt injection: a failed task's tool output reached the Hermes worker among the facts after the
  goal, labelled to keep word for word; now the person's facts bind and recorded state is quoted data),
  V7-D50 (a bracket could close the working set's record frame; every block now quotes its lines), V7-D51
  (a bench that failed to start left its patches in place). Also recorded: the lossy-packet check cannot fire
  with today's builders; it guards later changes.

## OWNER ACTIONS ADDED 2026-09-27

- RESOLVED 2026-09-28 (Amendment A4): Playwright goes into the test environment here; the thresholds are not
  tuned by hand - RES-002/RES-003 profile the host and judge pressure against its baseline; both laptops are
  reference machines (REFERENCE-A this host, REFERENCE-B 16 GB / 4 GB VRAM); commit a checkpoint before
  further major slices.

## FOLLOW-UP CHECKPOINT (2026-09-28/29; Amendment A5)

- Committed 3d956ba (branch `prd-v7/foundations-checkpoint`). Since then: MCP-001 firewall, Amendment A4 (RES-002/003/004,
  MODEL-009/010, BG-001), the browser extra (V7-D53), BROW-001's real journey, BROW-004's readiness probe (BROWSER_READY
  from the product `.venv`, 2.67 s, 270 MB), TEST-ISOLATION-001 (session state root, live-store guard, runner live-state
  check), V7-D52 incident record (>=251 test rows in the live audit log, preserved), V7-D54, provider-shaped test
  fixtures replaced or assembled at run time, Amendment A5.
- CLASSIFIER_TRANSIENT_FAILURE (2026-09-28 ~20:45): the harness's safety classifier returned no verdict on consecutive
  shell commands; work stopped and resumed from this file. Rule: bounded retry, then persist state and start fresh -
  never resend until the session ends.
- REFERENCE-B (16 GB / 4 GB VRAM) runs owed by the owner: `python -m friday.browser_readiness`,
  `scripts/journey_hybrid_browser.py`, and Slice 7's hardware qualification once built.
- P0 order (owner, A5): browser readiness, adaptive resource governor (Slice 7, `06-slice-7-adaptive-hardware.md`),
  CORE-001, ACT-001, DOMAIN-001, MCP-001, EVAL-001, remaining security/isolation, CTX retrieval, Cyber boundary only.
  No large new architecture on the implementation branch meanwhile.
- V7-D55/V7-D56 (2026-09-29, found when the session's state root left the project): FRIDAY's work areas were jail
  roots only inside the project (packaged builds refused them), and from a source checkout the file tools could
  reach FRIDAY's own control state (store, audit trail, confirmation nonces, MCP pins, logs). Fixed in
  `friday/fsjail.py`; cited under SEC-003.
- Clean four-chunk suite on the follow-up tree (`data/baseline_v7_followup`, FRIDAY not running): all chunks exit 0,
  4,734 passed, 0 failed; `live_state unchanged (244 files under data/ and logs/)`. TEST-ISOLATION-001 VERIFIED.
- The foundation commit is rebuilt without provider-shaped literals (3d956ba differed only in eight test files'
  fixtures); nothing is pushed.

## NEXT

1. Remaining unresolved P0s per `02-build-sequence.md`: CORE-001,
   EVAL-001, DOMAIN-001, ACT-001, MCP-001, CYBER-002 (generic boundaries only), and the residuals of SEC-005,
   MEM-002/003, INTENT-001 (V7-D38), TRAJ-001, CTX-003 and the A2 P0s.
2. A second independent review once the next slice lands (the first one found two high bypasses).

## DO_NOT_REPEAT

- Do not bulk-normalise line endings; the tree mixes CRLF and LF (measure with Python bytes).
- Do not nest `--basetemp` paths; pytest creates only the leaf.
- Do not rename or clean live data files; hand that to the owner.
- Do not run tests in the live `.venv`; use `.venv-build` on this machine.
- A plant that breaks an import during conftest setup is red for the wrong reason; plant calls, or in modules the fixtures do not import.
- Do not trust a conversation summary's account of a review: read the reviewer's own report from the transcript
  before recording or disputing anything (the summary kept three of six findings).
- A mutation that stays green may be masked by a second layer; revert both layers before concluding a test is weak.
- Compile calls hidden in subprocess scripts inside tests are invisible to AST rewrites; grep the text too.
