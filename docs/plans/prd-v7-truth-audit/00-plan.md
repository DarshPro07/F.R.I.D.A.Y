# Plan: PRD v7.0 Phase 0 — repository truth audit

**Date:** 2026-09-25
**Status:** Phase 0 done. Results are in `01-findings.md`. The build sequence (`02-build-sequence.md`) awaits owner approval.
**Requirements authority:** `docs/PRD_V7.md` (sha256 `53d4a624c01cfffc9f95517ea84a851e02dcacc76539df209e2a607a26fda3b6`), transcribed from the owner's pasted v7.0 text; §91 holds 69 requirement IDs.
**Prior ledger (carried, not replaced):** `scripts/prd_ledger.py` — 169 rows against PRD v1.0 Universal Product Baseline; 57 VERIFIED, 9 unresolved P0 (regenerated `--json-only` at the start of this audit, `--check` clean).
**Services touched:** none in this phase. Output is ledger data plus one script extension (`scripts/prd_ledger.py` learns a second requirement set).

## Objective

PRD v7 §0, §86 (Phase 0) and CORE-002 require the current tree to be classified before anything is built, so that a new name in the PRD never becomes a duplicate subsystem. This phase classifies all 69 §91 requirements in the v7 vocabulary (`EXISTING_VERIFIED`, `EXISTING_UNVERIFIED`, `PARTIAL`, `BROKEN`, `MISSING`, `DUPLICATED`, `SUPERSEDED`, `DEFERRED_BY_PRD`, `NOT_APPLICABLE`), maps each to the v1.0 ledger rows whose evidence it can reuse, and produces a dependency-ordered build sequence. No product code changes.

Assumed the owner's request (the v7 PRD with `ultracode`) means "start implementing v7"; v7 itself makes this audit the mandatory first step. Build phases start from the ledger this produces. Revisit if wrong.

## Facts established before dispatch (STATICALLY_CONFIRMED)

- HEAD `030795f`, branch `main`, 86 dirty paths.
- 28 dirty files changed after the last ledger run (2026-09-23 19:20), including `friday/model_gateway.py`, `friday/honesty.py`, `friday/executors/claude_code.py`, `friday/executors/cli_prompts.py`, `friday/onboarding.py`, `friday/research_cache.py`, `friday/user_policy.py`, `friday/launcher.py`, `friday/reachability.py`, `friday/toolsets/research.py`, and new tests `test_autonomy_slider`, `test_browser_evidence_chain`, `test_cli_prompts`, `test_onboarding`, `test_prompt_transport`, `test_research_cache`, `test_route_fitness`.
- Last full deterministic run: 2026-09-23 18:42 on `030795f`, four chunks all exit 0, 4,053 passed (`data/prd_baseline/summary.txt`). It predates those 28 files.
- Test interpreter on this machine: `.venv-build/Scripts/python.exe` (there is no `.venv-verify` here).
- Host: 16 logical CPUs, 15.4 GB RAM with about 3.8 GB free at dispatch. Heavy jobs run one at a time.

## Method

1. **Audit (8 groups, 8–9 requirements each).** One read-only auditor per group. Model: Sonnet (rule 13: code reading). Auditors cite `path:line` they actually read, reuse v1.0 ledger evidence where the test still exists, and list the searches behind every `MISSING`. No test runs, because parallel pytest on this host is the failure mode recorded in memory.
2. **Adversarial verify (one per group).** Model: Opus (rule 13: review). The verifier tries to refute every `EXISTING_VERIFIED` claim (does the cited test assert the v7 acceptance evidence, or something weaker?) and every `MISSING` claim (does an implementation exist under another name? A wrong `MISSING` is how duplicate subsystems get built). It also spot-checks citations.
3. **One sequential targeted test run** over the tests cited for surviving `EXISTING_VERIFIED` claims. A row stays `EXISTING_VERIFIED` only if those tests pass on this tree in this session.
4. **Ledger extension.** The v7 rows go into the existing ledger tool as a second requirement set, with a `maps_to` link to v1.0 rows. `--check` enforces the same rules as before: no green without evidence, and no blocker-less `BLOCKED_EXTERNAL`.
5. **Build sequence.** Dependency-ordered slices, starting from the P0 gaps.

Agent count: 16 (8 auditors plus 8 verifiers). The owner asked for multi-agent orchestration (`ultracode`). Rule 15's fewest-agents rule is honoured by sizing groups so each agent carries 8–9 requirements, not one agent per requirement.

## Groups

| # | Group | Requirements | PRD sections |
|---|---|---|---|
| 1 | Truth & control | CORE-001, CORE-002, VERIFY-001, VERIFY-002, STOP-001, BROW-002, EVAL-001, EPI-001 | 0, 9, 35, 49–53, 79, 93 |
| 2 | Objective OS | INTENT-001, INTENT-002, OBJ-001, OBJ-002, OBJ-003, TRAJ-001, WORLD-001, EXP-001 | 10–18 |
| 3 | CortexOS | CTX-001…006, EVAL-002, FAIL-001 | 19, 36, 82 |
| 4 | Cognitive governor & models | COG-001…004, MODEL-001…005 | 20–23, 26–30, 83 |
| 5 | Learning, memory, skills | NEURAL-001, NEURAL-002, LEARN-001, MEM-001…003, SKILL-001…003 | 24–25, 31–38, 67–68, 84 |
| 6 | Trust & security | CAP-001, CAP-002, TRUST-001, TRUST-002, SEC-001…005 | 39–43, 60, 85 |
| 7 | Execution surfaces | NET-001, CYBER-001, CLI-001, CLI-002, CODE-001, BROW-001, VOICE-001, REMOTE-001, MOBILE-001 | 23, 44–48, 61–64 |
| 8 | Runtime & product | RES-001, PRO-001, SELF-001, SCOUT-001, UX-001, ONB-001, PKG-001, PKG-002, EVAL-003 | 27, 55–60, 69–73 |

## Done when

- [x] All 69 rows carry a v7 state, a v1.0 crosswalk (possibly empty), and a next action. Working rows carry a next action; `--check` enforces it.
- [x] Every `EXISTING_VERIFIED` row names a test that passed on this tree in this session. In all, 143 cited tests ran (178 passed, 1 skipped), plus the browser evidence-chain file (7/7) and the ledger tests (7/7).
- [x] Every `MISSING` row records the searches that support it. They are kept in `data/prd_v7_audit/final_rows.json`, field `searched`.
- [x] `scripts/prd_ledger.py --check` is clean for both requirement sets. It now also resolves every v7 citation against the tree.
- [x] `01-findings.md` (results) and `02-build-sequence.md` (dependency-ordered slices) are written.
- [x] Independent review: `friday-final-reviewer` ran two rounds. Every dispute was fixed at the rule rather than the row; see `01-findings.md`.

## Out of scope / owner decisions (not guessed)

- §89 benchmark questions (Mem0 vs OpenViking, the 8 GB local expert set, Browser Use vs Playwright/CDP, and so on).
- Which build phase starts first after this audit, beyond the default of P0 gaps first.
