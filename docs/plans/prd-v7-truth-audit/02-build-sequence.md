# Plan: PRD v7 build sequence (after Phase 0)

**Date:** 2026-09-25
**Status:** Approved by the owner 2026-09-25. Slice 1 in progress.
**Owner decisions (2026-09-25):** (1) this sequence approved; (2) SANDBOX is *not* the default for coding runs — FRIDAY lives on and controls this machine, so `backend=HOST` stays the default and SANDBOX stays opt-in. SEC-005 is therefore met by a scope envelope FRIDAY enforces itself (see Slice 2), not by OS isolation; (3) LiveKit credentials and a clean VM are not yet provided, so VOICE-001 latency and PKG-001 stay blocked on them.
**Source:** the 19 open P0 rows in `docs/implementation/PRD_V7_LEDGER.md`, then the P1/P2 rows by PRD §86 phase.
**Services:** objective-engine, mcp-tools, fabric, voice-brain.

## Ordering rule

1. **Cheap proofs before construction.** Several P0 rows need only the test that the acceptance evidence names. Each test either flips its row to VERIFIED or exposes a real defect, which is worth knowing before anything is built on top of it.
2. **Authority before capability.** The envelope, network scope and composition checks decide what every later worker may do, so they come before the new subsystems that spawn workers.
3. **Extend the existing seam** that `01-findings.md` names for each concept. No parallel `friday/context/` package, and no second `governor.py`.

Every slice follows the same loop:

- plan approved;
- scoped `friday-*` builder writes a failing test first, then implements;
- targeted tests;
- `friday-qa-engineer` verifies;
- `friday-final-reviewer` (Opus) reviews;
- a `PROGRESS_V7` entry with evidence goes into `scripts/prd_ledger.py`;
- the four-chunk suite runs via `friday-monitor`, one heavy job at a time;
- the owner commits (agents never commit).

## Slice 1: cheap proofs (tests only; no new subsystem)

| Row | Service / agent | Small win (the test) |
|---|---|---|
| TRUST-001 | mcp-tools / friday-security-engineer | A Run built from read material whose payload says `"trusted": true` / `provenance: PERSON` is still denied `power.shutdown` through `capability_runtime`, with provenance unchanged. |
| COG-001 | objective-engine / friday-objective-engineer | A call-counting fake gateway records 0 frontier calls for a deterministic request through the real admission and dispatch path. A negative control that bypasses the classifier records more than 0. |
| CORE-001 | objective-engine / friday-objective-engineer | A Hermes-reported success with a failing verifier is rejected by FRIDAY's objective and policy layer. If the design gates policy at dispatch rather than at return, the test pins that and the row says so. |
| EVAL-001 (part a) | objective-engine / friday-qa-engineer | First tests for `friday/evaluation.py::graded()`, which has none today. |
| NEURAL-002 | objective-engine / friday-qa-engineer | An architecture test forbids weight-update primitives (optimizer step, weight save, fit) outside a candidate/promotion path. A planted violation makes it go red. |
| VERIFY-002 | objective-engine / friday-qa-engineer | A registry maps each §79 critical negative control to its red-path test ID. The meta-test fails if a listed test is not collected, or if an entry has neither a test nor an open ledger row. The row turns VERIFIED only when all 12 entries have tests. |

**Done when:** each row is VERIFIED with a passing test, or has a named defect filed as its next action.

## Slice 2: authority kernel (PRD Phase 1)

Needs an architecture review by `friday-tech-lead` (Opus; cross-service design). The owner declined SANDBOX as the default (2026-09-25).

1. **SEC-005 execution envelope.** A declared envelope object per PRD §43 (cwd, read/write roots, network, secrets, capabilities, process/memory/time/token/tool-call limits, expiry), attached to every worker launch in `executor_router` and `fabric_process`. The run backend stays HOST by default; the envelope is enforced by FRIDAY's own gates (policy, fsjail, netguard, the coding CLI's permission flags, timeouts and budgets), and SANDBOX remains an opt-in backend. Test that an out-of-envelope write, host and process is denied, per worker type.
2. **NET-001 network scope.** Generalise `trust.SecurityAuthorization`/`target_guard` (objective ID, expiry, revoke) into the envelope's network field, consumed by `netguard`. Test that an expired host is denied and an unknown host is denied.
3. **CAP-002 composition.** Before a worker starts, `PolicyEngine` computes risk over the envelope's whole capability set. Test that private-read plus arbitrary-send escalates even though each is allowed alone.
4. **STOP-001 out-of-band stop.** `launcher.stop_all` also revokes envelope and network leases. Test that a run whose model ignores the stop is still terminated and its leases are revoked.

## Slice 3: memory trust

1. **MEM-002.** A trust class on memory writes, reusing TRUST-001's runtime-typed provenance. Test that a web-origin candidate cannot land with user authority.
2. **MEM-003.** A poisoning lifecycle benchmark in `tests/test_memory_poisoning.py`: write hostile content, let it age, show it cannot drive a later action, then forget and repair it. It extends `memory_promotion`.

## Slice 4: Objective OS

1. **INTENT-001.** A golden journey from free text through `requirements.understand` and `plan_objective` to an executable objective. Also confirm the live loop calls `understand()` before planning.
2. **TRAJ-001.** A run-level trajectory check that extends the stuck detector in `friday/continuous.py`: progress deltas, amendment alignment, side-effect accumulation. Add a synthetic drift benchmark with a negative control.

## Slice 5: CortexOS core

1. **CTX-003.** A CriticalFactSet validator at the seam where FRIDAY writes a fresh-session handoff: executor continuity and `continuity.py`. Test that a mutated summary missing one fact is rejected.
2. **CTX-001.** Extend `memory_stack.aggregate` / `model_gateway.compile_context` so they also pick tool schemas and code references, with per-section telemetry.
3. **CTX-002.** An explicit L0/L1/L2 split in `capability_router`, plus a benchmark against the all-tools baseline (§82 target: at least 80% fewer tokens).
4. **Then (P1):** CTX-004 health, CTX-005 artifact offloading (`toolsets/research.py:546` returns raw markdown today), EVAL-002 Context Lab.

## Slice 6: resources and browser

- **RES-001.** A low-RAM stress harness that asserts observed worker and process shedding, plus GPU and network sampling in the resource governor.
- **BROW-001.** One objective that runs a connector step and a browser step, with a per-step path-priority function per §48.

## After Slice 6 (P1/P2, by PRD §86 phase)

- **Cognitive governor and Model Arena:** COG-002, COG-003, MODEL-003/004/005, EVAL-001 (part b), EVAL-003.
- **Learning:** LEARN-001, SKILL-002 (wire the dropped objective ID), FAIL-001, EPI-001.
- **Surfaces:**
  - CLI-001: a second real adapter.
  - REMOTE-001: Telegram pairing.
  - UX-001: Normal/Developer mode.
  - ONB-001: the first-run journey.
  - PKG-001: provisioning plus a clean VM run.
  - VOICE-001: latency, once real LiveKit credentials exist.
- **Proactive and world:** PRO-001, WORLD-001, EXP-001.
- **Last:** CODE-001 (Ponytail A/B on FRIDAY tasks), SCOUT-001, NEURAL-001, and MOBILE-001 (after the Artemis security review).

## Owner decisions needed

1. Approve this sequence and Slice 1 as the starting point.
2. Approve SANDBOX as the default for coding runs (Slice 2, SEC-005).
3. Provide real LiveKit credentials (VOICE-001) and a clean Windows VM or Windows Sandbox (PKG-001) when those slices arrive.
