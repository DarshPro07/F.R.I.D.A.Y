# FRIDAY / JARVIS PRD v3.1 - Compliance Matrix, Architecture Map, Readiness Verdict

Written 2026-09-05 against working tree on base commit `99dd904` (tree
uncommitted - the owner commits). Every row cites the evidence that exists
on disk; nothing here is inferred from code that was merely written. The
implementation ledger (`PRD_V31_IMPLEMENTATION_LEDGER.md`) holds the
per-requirement history, decisions and failure notes this matrix summarises.

Status vocabulary (the ledger's lifecycle): **VERIFIED** = real evidence
(tests, live probes, measurements) on this build; **PARTIAL** = implemented
and tested, with a named gap; **ENV-LIMITED** = cannot be evidenced on this
machine/session and says why; **NOT DONE** = no implementation.

Evidence runs referenced below (all on this tree):

| run | result |
|---|---|
| deterministic suite `data/post_schedules` (204 files) | 3,715 passed, 1 skipped, 1 failed -> fixed (silent-except ratchet) |
| deterministic suite `data/post_golden` (206 files) | 3,733 passed, 1 skipped, 1 failed -> fixed (ui_server stub expectation; FR-040) |
| deterministic suite `data/post_final` (206 files, this exact tree) | 3,737 passed, 1 skipped, 0 failed (chunk3 stalled 2.6 h behind a straggling Hermes-install `git fetch`/`maintenance` process + stale index.lock; finished green, region re-run 59/59 in 83 s) |
| E2E `e2e-run.log` (Playwright, chromium, real UI server) | 48 passed, EXIT=0 |
| Golden Objective suite `data/golden/fourth.json` | 162/162 (154 corpus + 8 golden failures); false completion 0; unauthorized 0; evidence coverage 1.0; 1 clarification (warranted), 0 unwarranted; p95 0.50 s; 0 model calls; production planner path |
| performance `data/perf/latest.json` | NFR-P06..P13 + 2 KPIs measured, all pass; P01..P05 NOT_MEASURED |
| Hermes MODEL_GATEWAY live | PONG round-trip through the gateway worker (ledger 2026-09-04) |
| Hermes EXECUTION_ENGINE live `data/hermes/engine_validation_first_run.json` | VERIFIED: bounded task -> exact file on disk, no stray changes, no commit |
| chaos `tests/test_chaos_restart.py` | 10/10 + 1/1 real-process kill/resume |
| live Chromium `tests/test_browser_primitives.py` | 19 passed incl. 2 live |
| live adversarial panel `tests/test_adversarial.py` | 14 passed incl. live DEFER@40 |

## 1. Compliance matrix

### 4.1 Objective and orchestration

| FR | P | Status | Where | Evidence |
|---|---|---|---|---|
| FR-001 Objective Ledger | P0 | VERIFIED | `friday/objectives.py`, `store.py` objective_runs (+risk_tier, approvals[], evidence[], blocker, source_channel) | test_objective_ledger 7; PRD 9.2 `objective_ledger()` shape |
| FR-002 Task Complexity Classification | P0 | VERIFIED | `friday/task_class.py` TRIVIAL..CRITICAL | agreement 1.0 on 64-item labelled set (>=95% required); test_task_class 6 |
| FR-003 Dynamic Planning | P0 | VERIFIED | `planner.py`, `planner_model.py`, `objectives.compile_objective` | test_planner 46, test_objective_planner; golden 162/162 on the production planning path |
| FR-004 Bounded Agentic Loop | P0 | VERIFIED | `continuous.py` max_attempts, fingerprint block, MAX_STRATEGY_CHANGES, watchdog | test_objective_engine, test_continuity*, NFR-P12 growth guard measured |
| FR-005 Progress and Checkpointing | P0 | VERIFIED | `continuous.py` leases/wakes, `RunWatchdog` | test_chaos_restart (real processes): 10/10 resume; orphan bug fixed |
| FR-006 Interruption and Cancellation | P0 | VERIFIED | `HermesSupervisor.interrupt`, `arbiter.py`, `objective_cancel`, LiveKit interruption | test_arbiter 11, test_voice_interruption 9, test_hermes_bridge |
| FR-007 Human Decision Minimization | P1 | VERIFIED | `golden.count_clarifications` (UNMAPPED handed back, WAITING_QUESTION, question events; permission requests excluded) scored per case; report carries `clarifications`, `clarification_rate`, `unwarranted_clarifications` | golden `data/golden/fourth.json`: 162 cases, 1 clarification (the one warranted case), 0 unwarranted; 3 answer-from-memory cases asked nothing |
| FR-008 Contrarian Decision Mode | P1 | VERIFIED | `friday/adversarial.py` five-role panel | test_adversarial 14 incl. live |

### 4.2 Delegation

| FR | P | Status | Where | Evidence |
|---|---|---|---|---|
| FR-009 Hermes Primary Execution Contract | P0 | VERIFIED | `hermes_bridge.TaskBundle`, `WorkRunLog` | test_hermes_bridge 21, test_hermes_engine 12; live engine validation |
| FR-010 Secondary Coding Worker | P1 | VERIFIED | `executors/claude_code.py`, `executor_router.py` | test_executor 44, test_executor_router 22 |
| FR-011 Temporary Specialist Teams | P1 | PARTIAL | `hermes_team.py`, `roles.py` | test_hermes_team 9, test_roles 27; no live multi-worker run recorded |
| FR-012 Independent Verification Worker | P0 | VERIFIED | `adversarial.py` + `promotion.decide(review=)`; gate check `independent_review` | test_adversarial, test_promotion 34, journeys updated |
| FR-013 Parallel Worker Budget | P0 | VERIFIED | `friday/governor.py` (cap 2, queue, shed) at both dispatch seams | test_governor 16; NFR-P09 measured 2 of 6 granted; live refusal at 96% RAM recorded |
| FR-014 Worker Failure Isolation | P0 | VERIFIED | supervisor restart, FAKE_HERMES_DIE, chaos suite | test_hermes_bridge, test_chaos_restart |

### 4.3 Memory

| FR | P | Status | Where | Evidence |
|---|---|---|---|---|
| FR-015..FR-020 unified memory, classes, scoped retrieval, provenance, correction/forgetting, context compilation | P0 | VERIFIED | `store.py` memory columns (memory_type, project_scope, source_ref, supersedes_id, contradicts_id, retention, importance), `memory_stack.py`, tools memory_provenance/memory_export | test_memory_contract 10, test_memory_provenance; NFR-P06 P95 89 ms; NFR-P10 no memory dump |
| FR-021 Codebase Memory | P1 | VERIFIED | `codegraph.py`, fabric code-intel adapters | test_codegraph + test_fabric_code_intel 65 |
| FR-022 Learning Promotion | P1 | VERIFIED | `autolearn.py`, `LiveKitContinuity` | test_autolearn 25 |

### 4.4 Capability fabric

| FR | P | Status | Where | Evidence |
|---|---|---|---|---|
| FR-023 Registry | P0 | VERIFIED | `fabric.py` (32 providers) + `capabilities.py` (190) + `manifest.py` | test_fabric 34, test_manifest 13 |
| FR-024 Types | P0 | VERIFIED | modes BUILTIN/ADAPTER/MCP/SKILL/SIDECAR/CLI/REFERENCE_ONLY (+SDK/HTTP/SPECIALIST mapping in manifest) | test_manifest |
| FR-025 Progressive Discovery | P0 | VERIFIED | `capability_router.py` CORE_TOOLS + groups | test_capability_router 29; NFR-P10 24.8 bytes/capability summary |
| FR-026 Health | P0 | VERIFIED | fabric STATES + FAILED after 3 raises | test_manifest |
| FR-027 Dependency and Version Pinning | P1 | VERIFIED | `Provider.__post_init__` commit pin; `scripts/upstream_lock.py` | test_upstream_lock 25/25 |
| FR-028 MCP Gateway | P0 | VERIFIED | `GuardedMCPServerHTTP` + `PolicyEngine`; 190 tools | test_objective_mcp, verify_mcp |
| FR-029 MCP Authorization Compatibility | P1 | PARTIAL | policy categories per tool; no per-client OAuth scopes | test_user_policy 13 |
| FR-030 Capability Search | P1 | VERIFIED | `Router.search` | routing KPI top-1 96.5% / top-3 100% |

### 4.5 Browser, desktop, voice, remote, schedules

| FR | P | Status | Where | Evidence |
|---|---|---|---|---|
| FR-031 Browser Primitives | P0 | VERIFIED | `friday/browser.py` 13 primitives, `browser_act` | test_browser_primitives 19 incl. live Chromium |
| FR-032 Browser Mechanism Selection | P1 | VERIFIED | `browser_capability.py`, profiles | test_browser_profiles 13 |
| FR-033 Authorized Browser Profiles | P0 | VERIFIED | profile allow-list, EXTERNAL_WRITE_CONFIRM | test_browser_primitives, test_browser_profiles |
| FR-034 Authentication Is Not Authorization | P0 | VERIFIED | signed-in state never widens policy; sensitive domains blocked before capture | test_browser_primitives; golden GO-browser-016 (chase.com BLOCKED) |
| FR-035 Human Verification Boundary | P0 | VERIFIED | structural HUMAN_VERIFICATION detection, no bypass | test_browser_primitives |
| FR-036 Desktop Control | P0 | VERIFIED | `windows.py`, `jarvis_screen`, `desktop_*` with before/after observations | test_windows 24, test_jarvis_screen 22 |
| FR-037 Structured State First | P1 | VERIFIED | DOM/AX before pixels in `browser.py` | test_browser_primitives |
| FR-038 Voice Pipeline | P0 | VERIFIED | `agent_friday.py` + `VoiceInputGate` mute gate attached | test_voice_interruption 9, test_voice_pipeline; NFR-P01..P03 ENV-LIMITED |
| FR-039 Voice History Truncation | P1 | VERIFIED | LiveKit `interrupted=True` transcript; UI `Store.truncate_message` + `/api/interrupted` | test_voice_interruption |
| FR-040 Remote Channels | P1 | VERIFIED | `/api/objective` behind session gate, `source_channel` | test_remote_channel 5 |
| FR-041 Schedules | P1 | VERIFIED | `toolsets/schedules.py`, schtasks | test_schedules 11 |
| FR-042 Conditional Monitoring | P1 | VERIFIED | schedule condition + no-noise delivery | test_schedules (condition false -> nothing delivered) |

### 4.6 Research, documents, business, social

| FR | P | Status | Where | Evidence |
|---|---|---|---|---|
| FR-043 Research Pipeline | P0 | VERIFIED | `toolsets/web.py` (search/fetch/deep_research), netguard | test_research 30 + fabric research 21; golden research 20/20 |
| FR-044 Document and Knowledge Work | P1 | VERIFIED | `toolsets/documents.py` | test_documents 20; golden docs/data 10/10 |
| FR-045 Business Workflow Orchestration | P1 | VERIFIED | `products.py`, `product_stages.py` | test_products 35, test_product_mcp 27; golden business 15/15 |
| FR-046 Compliant Social Publishing | P1 | PARTIAL | fabric social helpers, EXTERNAL_WRITE_CONFIRM | test_fabric_helpers_social 12; no live publish (needs accounts) |

### 4.7 Self-development

| FR | P | Status | Where | Evidence |
|---|---|---|---|---|
| FR-047 Improvement Detection | P1 | VERIFIED | `selfdev.py` OBSERVE/IDENTIFY | test_selfdev 15 |
| FR-048 Sandboxed Self-Development | P0 | VERIFIED | real git worktrees (`executors/worktrees.py`), fsjail | test_selfdev, test_worktrees 31 |
| FR-049 Test Gate | P0 | VERIFIED | `promotion.py` gates (changes, verified, independent_review, scope, reviewable, secrets, approved) | test_promotion 34 |
| FR-050 Benchmark Before Promotion | P1 | VERIFIED | `friday/selfdev_benchmark.py` measures the sandbox (memory P95, action p95, manifest bytes, routing) against the live tree; `toolsets/selfdev.py` runs it for perf-sensitive changes, records "no performance claim" otherwise | test_selfdev 18: regressing sandbox REJECTED with before/after; sandbox (not live tree) measured; docs-only change skipped; real probe ~60 s/side |
| FR-051 Rollback | P0 | VERIFIED | `selfdev_rollback`, `self_upgrade.py` | test_selfdev |

### 4.8 Verification, safety, observability

| FR | P | Status | Where | Evidence |
|---|---|---|---|---|
| FR-052 Evidence Ledger | P0 | VERIFIED | objective_runs.evidence[], `append_objective_evidence` | test_objective_ledger; golden evidence coverage 1.0 |
| FR-053 Completion Gate | P0 | VERIFIED | `continuous._completion_gate` -> PARTIAL on evidence gap | test_objective_ledger; golden false completion 0 |
| FR-054 Observability | P0 | VERIFIED | `observability.py` trace + 11-section diagnostics, redacted | test_observability 7; live diagnostics 11/11 |
| FR-055 Cost/Token Accounting | P1 | VERIFIED | `GatewayTelemetry` (gateway_calls), `WorkRunLog.usage`, `execution_economics` | NFR-P13 50/50 attributed; engine run usage recorded |
| FR-056 Resource Governor | P0 | VERIFIED | `governor.py` pressure NORMAL/ELEVATED/HIGH/CRITICAL | test_governor 16; live CRITICAL refusal recorded |
| FR-057 Secret Isolation | P0 | VERIFIED | `secret_broker.py`, `vault.py`, credentials inside Hermes process, redaction | test_secret_broker 9 (metadata-only ingest, encrypted store, shredded scratch); observability redaction test; no key in any log/JSON produced this cycle |
| FR-058 Policy Engine | P0 | VERIFIED | `policy.py` AUTO/ASK/CONFIRM/DENY, autonomy modes | test_trust 16, test_user_policy 13 |
| FR-059 Risk Tiers | P0 | VERIFIED | `trust.py` R0-R4 deterministic table | test_trust |
| FR-060 Exact-Action Approval | P0 | VERIFIED | `confirmation.py` nonce; in-objective APPROVED record bound to exact parameters (`_approval_on_record`) | test_golden_suite (forged approved_by grants nothing) |
| FR-061/062 Security Workspace + Target Contract | P0 | VERIFIED | `SecurityAuthorization` scope+expiry enforced in `fabric.call` | test_trust (real strix_pentest refusal) |
| FR-063 Prompt Injection Boundary | P0 | VERIFIED | `policy.provenance_verdict`, READ_MATERIAL runs | test_trust, test_privacy 23; e2e injection.spec |
| FR-064 Supply-Chain Review | P1 | VERIFIED | upstream lock, license audit, copyleft-isolation rule at import | test_upstream_lock 25, test_fabric |
| FR-065 Audit Log | P0 | VERIFIED | `trust.AuditLog` hash-chained WAL, `verify_chain()` | test_trust |
| FR-066 User Memory and Data Controls | P1 | VERIFIED | memory_export, forget, provenance | test_memory_provenance, test_user_policy |
| FR-067 Profile Boundary | P1 | VERIFIED | `profile.py` | test_profile 28 |
| FR-068 Capability Marketplace Readiness | P2 | NOT DONE | - | out of scope this cycle (P2) |

### 4.9 Hermes Model Gateway

| FR | P | Status | Where | Evidence |
|---|---|---|---|---|
| FR-069 Dual-Role Interface | P0 | VERIFIED | `model_gateway.py` (inference) vs `hermes_bridge.py` (execution) | test_model_gateway 21; live PONG; live engine run |
| FR-070 Model Gateway envelope | P0 | VERIFIED | `ModelGatewayRequest/Result` | test_model_gateway |
| FR-071 Provider Capability Discovery | P0 | VERIFIED | `ModelGateway.providers()` live query | test_model_gateway; diagnostics providers section |
| FR-072 Subscription and API Separation | P0 | VERIFIED | per-route entitlement in gateway | test_model_gateway |
| FR-073 Credential Isolation | P0 | VERIFIED | keys resolved inside the Hermes worker process; Friday's request/result envelopes carry none | test_model_gateway (envelope has no credential fields; NFR-P11 key scan); test_secret_broker 9 (`model_facing_surfaces_carry_no_value`, `store_on_disk_is_encrypted`); test_trust `injected_instructions_requesting_secrets_fail` |
| FR-074 Privacy Truthfulness | P0 | VERIFIED | route boundary label, `privacy.py` | test_privacy 23 |
| FR-075 Model Routing Policy | P0 | VERIFIED | task-class -> tier routing with fallback | test_model_gateway, test_execution_economics 11 |
| FR-076 Inference-Only Context Budget | P0 | VERIFIED | `compile_context` bounded package | NFR-P10: 6 of 40 turns kept |
| FR-077 Token Budget Profiles | P0 | VERIFIED | per-class numeric budgets + escalation | test_model_gateway |
| FR-078 Token Growth Guard | P0 | VERIFIED | `GrowthGuard` | NFR-P12 measured |
| FR-079 No Agent Loop | P0 | VERIFIED | single provider transaction, no tools | NFR-P11: request carries no tools/subagents/skills/memories |
| FR-080 Provider Usage Telemetry | P0 | VERIFIED | `GatewayTelemetry` | NFR-P13 100% attributed |
| FR-081 Failover Without Objective Reset | P1 | VERIFIED | bounded failover inside gateway call, objective untouched | test_provider_fallback 6, test_model_gateway |

### Non-functional (PRD 5)

| NFR | Status | Evidence |
|---|---|---|
| P01 interruption <= 300 ms | ENV-LIMITED | needs a live LiveKit room; path pinned by test_voice_interruption |
| P02 first acknowledgement <= 700 ms | ENV-LIMITED | live voice session |
| P03 first meaningful stream <= 1.5 s | ENV-LIMITED | live voice session |
| P04 UI propagation < 250 ms | ENV-LIMITED | needs browser event timing; e2e brain-latency.spec covers the path functionally |
| P05 Control Room cold start < 2 s | ENV-LIMITED | cold browser start |
| P06..P13 | VERIFIED | `data/perf/latest.json` (table in ledger) |

Counts: 55 P0 requirements -> 55 VERIFIED. 25 P1 -> 22 VERIFIED, 3 PARTIAL
(FR-011, FR-029, FR-046). 1 P2 NOT DONE (FR-068). 13 NFRs
-> 8 measured pass, 5 ENV-LIMITED.

## 2. Final gate checklist (the 20 items)

| # | item | state | evidence |
|---|---|---|---|
| 1 | Full PRD compliance matrix | done | this document |
| 2 | P0 implementation evidence | done | 55/55 rows above |
| 3 | Feasible P1 evidence | done | 22/25 verified, 3 partial with named gaps |
| 4 | Hermes Model Gateway validation | done | test_model_gateway 21 + live PONG |
| 5 | Hermes Execution Engine validation | done | live bounded task, outcome judged on disk |
| 6 | Shared-memory validation | done | test_memory_contract 10; P06 |
| 7 | Model-routing validation | done | test_model_gateway routing cases; task-class agreement 1.0 |
| 8 | Token-budget / governor validation | done | test_governor 16; P09/P12; live CRITICAL refusal |
| 9 | Capability Fabric validation | done | test_fabric 34, test_manifest 13, upstream lock 25/25, verify_mcp |
| 10 | Browser and desktop validation | done | live Chromium 19; test_windows 24; e2e 48 |
| 11 | Security-policy tests | done | test_trust 16, test_user_policy 13, test_privacy 23, golden security 5/5 |
| 12 | Crash/restart/resume tests | done | test_chaos_restart 10/10 + 1/1 |
| 13 | Deterministic suite | done | post_final 3,737 passed / 1 skipped / 0 failed on this exact tree (post_schedules 3,715/1 fixed; post_golden 3,733/1 fixed before it) |
| 14 | Integration suite | done | journey/vertical-slice/cross-capability tests inside the deterministic suite |
| 15 | E2E suite | done | 48 passed |
| 16 | Golden Objective suite | done | 162/162 incl. failures corpus 10 (production planner path) |
| 17 | Performance measurements | done where measurable | 8/13 NFR + 2 KPI; 5 ENV-LIMITED |
| 18 | Known limitations | done | section 4 |
| 19 | Architecture map | done | section 3 |
| 20 | Readiness verdict | done | section 5 |

## 3. Architecture map (as built)

```
 person (voice / Control Room / remote POST)
   |
   v
 FRIDAY control plane ------------------------------------------------------
 | agent_friday.py (LiveKit: Sarvam STT -> Gemini -> OpenAI TTS; mute gate;
 |                  continuity; interruption truncation)
 | ui_server.py    (Control Room, /api/objective remote door, session gate)
 | voice_brain.py  (conversation turn; router path = capability_router)
 |
 |  objective engine ------------------------------------------------------
 |  objectives.py  compile_objective -> objective_runs/tasks/events (SQLite)
 |  planner.py / planner_model.py   deterministic first, model when unresolved
 |  task_class.py  TRIVIAL..CRITICAL  -> budgets, routing tier
 |  continuous.py  ContinuousTaskExecutor: leases, wakes, retries, strategy
 |                 changes, permission boundary (WAITING_PERMISSION),
 |                 completion gate (evidence or PARTIAL), RunWatchdog
 |  schedules.py   persisted objectives on triggers; condition -> delivery
 |
 |  trust plane -------------------------------------------------------------
 |  policy.py      AUTO/ASK/CONFIRM/DENY per tool category, autonomy modes
 |  trust.py       R0-R4 tiers, SecurityAuthorization, hash-chained AuditLog
 |  confirmation.py nonce bound to exact action; capability_runtime honours
 |                 one APPROVED record per exact parameters
 |  fsjail / netguard / sandbox / sensitive_domains / privacy
 |
 |  capability fabric -------------------------------------------------------
 |  capabilities.py (190) + capability_router.py (progressive groups)
 |  fabric.py (32 providers, pinned, licensed) + fabric_adapters/*
 |  manifest.py  health, FAILED state, progressive summary
 |  tools/* (MCP faces) -> toolsets/* (run-first implementations)
 |  browser.py (Playwright primitives, profiles, human-verification boundary)
 |  windows.py / desktop (before/after observations)
 |
 |  memory ------------------------------------------------------------------
 |  store.py memories (types, project scope, provenance, supersedes/contradicts)
 |  memory_stack.py scoped aggregate under a token budget
 |
 |  verification -------------------------------------------------------------
 |  evaluation.py / honesty.py / adversarial.py (five-role panel)
 |  promotion.py gates incl. independent_review; selfdev.py on git worktrees
 |  golden.py (150 objectives + failures corpus); observability.py (trace,
 |  11-section diagnostics); governor.py (pressure, worker cap)
 ---------------------------------------------------------------------------
   |                                   |
   v                                   v
 HERMES_MODEL_GATEWAY                HERMES_EXECUTION_ENGINE
 model_gateway.py -> worker in the   hermes_bridge.py HermesSupervisor ->
 Hermes venv: one provider call,     tui_gateway JSON-RPC (session.create,
 no tools/subagents/skills/memory;   prompt.submit); TaskBundle contract;
 telemetry per call; growth guard    WorkRunLog; steer/interrupt/usage;
                                     governor lease per worker
                                       |
                                       v
                                     specialist workers (claude_code executor,
                                     worktrees) - adapters only
```

## 4. Known limitations (honest list)

1. Voice latency NFRs P01-P05 are not measured on this build; the code
   paths are pinned by deterministic tests and the framework records
   `interrupted=True`, but no live-room timing exists.
2. Deterministic planner routing: 10 confident misroutes remain on the
   144-phrase labelled set (`data/perf/latest.json` -> misses_sample).
   The conversation router meets the KPI (96.5% top-1); objective
   phrasing the planner cannot place goes to the model planner instead of
   being guessed. The 10 are the next defect list.
3. FR-050 benchmark gate runs a reduced probe (600 memories, 60 queries,
   6 actions; ~60 s per side) rather than the full perf profile, so a
   regression smaller than the 10% tolerance or outside those four
   metrics is not caught by promotion - the full profile remains a
   manual `scripts/perf_profile.py` run.
4. FR-046 social publishing and FR-011 specialist teams have no live run
   on record (accounts / second live worker needed).
5. FR-029: MCP clients are policy-gated per tool but there are no
   per-client OAuth scopes.
6. FR-068 (P2) marketplace readiness: not started.
7. The live Friday agent self-edits `agent_friday.py` and leaves git
   index locks; suite chunks importing that file during an edit show
   environmental failures (seen once, re-run clean). `.claude/worktrees/`
   (agent checkouts, 70 MB) is now git-ignored.
8. Host pressure: the resource governor refused a live delegation at 96%
   RAM during the gate run. Correct behaviour, but on this 16 GB machine
   the full suite, Playwright and a live delegation cannot run together.

## 5. Readiness verdict

**Production-ready for the P0 scope, with the limitations above stated.**
The final full deterministic suite on this exact tree is green
(`data/post_final`: 3,737 passed, 1 skipped, 0 failed - 0 unexplained
deterministic failures across four consecutive full gates, every failure
in the earlier three root-caused and fixed without weakening a test).

What backs the verdict: 55/55 P0 requirements with evidence on this
tree; 22/25 P1 verified; the Golden Objective suite at 162/162 on the
production planning path with zero false completions, zero unauthorized
actions and zero unwarranted questions; both Hermes roles validated
live; crash recovery on real processes; 48 E2E and 3,737 deterministic
tests green; performance targets met where measurable.

What it does not claim: sub-second voice latencies (unmeasured), a
perfect deterministic planner (10 known misroutes, none of them silent),
or the three P1 items marked PARTIAL. Those are listed, not hidden.
