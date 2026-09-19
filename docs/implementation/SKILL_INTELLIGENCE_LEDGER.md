# Skill Intelligence + Human Interface — Implementation Ledger

Covers three work packages that arrived together and overlap heavily:
PRD v6.0 (authority), the Gawkbot skill-intelligence build, and the
Mark-LIV human-interface build. One ledger, because building them as three
separate systems is the duplication every one of them forbids.

Worktree: `D:/friday-product-phase1` on `product/phase1-context`.
Track A (soak) is separate and untouched: `D:/friday-soak-fix`.

Statuses: NOT_AUDITED → EXISTING / PARTIAL / BROKEN / MISSING / UNVERIFIED
→ IMPLEMENTING → TESTING → VERIFIED / BLOCKED_EXTERNAL / REJECTED_WITH_REASON.
**VERIFIED requires evidence. Code existing is not verification.**

## Phase 0 — repository truth audit (done, `D:/friday-test-tmp/phase0_audit.py`)

39 requirement groups classified against the tree by grep/import, not by
module-name guesses:

    EXISTING 13   PARTIAL 20   MISSING 7   UNVERIFIED 2

The seven MISSING are the real build list. Everything else is mapping,
tests, or a narrow extension of code that already works.

| ID | Requirement | Was | Status | Evidence / next |
|----|-------------|-----|--------|-----------------|
| FR-001 | Persistent objective engine, no COMPLETED w/o verifier | EXISTING | VERIFIED | `store.CompletionRefused`; test_invariants |
| FR-002 | Autonomous loop with budgets/stuck detection | EXISTING | VERIFIED | `objective_budget.lease_for_child` 12 tests; GrowthGuard |
| FR-003 | Shared memory w/ provenance/scope/trust | EXISTING | VERIFIED | `brain.UNTRUSTED_PROVENANCE`; test_retrieval_provenance 19 |
| FR-004 | Expertise vs situational context | PARTIAL | NOT_AUDITED | check `memory_promotion` blocks time-sensitive promotion |
| FR-005 | Context budget manager | EXISTING | VERIFIED | `c4fb14e` exact accounting; test_context_budget 34 |
| FR-006 | Retrieval router + coverage | EXISTING | VERIFIED | `retrieval.Coverage`; test_retrieval_router 32 |
| FR-007 | Codebase intelligence (symbols/call chains/impact) | MISSING | NOT_AUDITED | adapter behind fabric; codebase-memory-mcp is P1 ADAPTER |
| FR-008 | Resource/Memory/Skill/Capability/Plugin separation | PARTIAL | NOT_AUDITED | Plugin lifecycle absent; fabric covers Capability |
| FR-009 | Skill lifecycle (lint/test/version/rollback/publish) | PARTIAL | NOT_AUDITED | `skill_ladder` has capture/validate/deprecate; no lint/version |
| FR-010 | Skill candidate detection | PARTIAL | NOT_AUDITED | `self_upgrade`/autolearn exist; no detector contract |
| FR-011 | Skill lazy loading | EXISTING | VERIFIED | Hermes progressive disclosure |
| FR-012 / GB-07/08 | Skill fingerprint + dependency graph + NEEDS_REVALIDATION | MISSING | NOT_AUDITED | the load-bearing gap of the Gawkbot package |
| FR-013 | Skill publication gates | MISSING | DEFERRED_WITH_REASON | no publication target exists yet; gates without a publisher are decoration |
| FR-015 | Learn by demonstration | MISSING | DEFERRED_WITH_REASON | PRD marks "future workflow" |
| FR-020..024 | Task classes / E-A-D / width-depth / specialists | EXISTING | VERIFIED | `execution_economics`; test_width_depth_routing 14; test_task_class_mapping |
| FR-030..035 | Gateway / exec engine / fitness / health / LOCAL_ONLY | EXISTING | VERIFIED | `fitness_by_model` aa82673 (12); route-keyed health; `_route_kind_for` endpoint-proven |
| FR-040..044 | Web read router / browser abstraction / profiles / CAPTCHA | PARTIAL | NOT_AUDITED | browser profiles RESEARCH/AUTHORIZED_USER/TEMPORARY absent |
| FR-043/052 / ML-08 | Action evidence contract | EXISTING | VERIFIED | `honesty.audit` wired both paths; `contracts.succeeded` requires Verification |
| FR-053 | Cancellation propagation | PARTIAL | NOT_AUDITED | 12 cancel sites; propagation through TTS/browser/subprocess unproven |
| FR-060..064 | Voice runtime (LiveKit) | EXISTING | VERIFIED | agent_friday.py live |
| FR-070..074 | Communication router / calendar | PARTIAL | NOT_AUDITED | no `scheduling.py` in this tree (memory note was stale) |
| FR-080..082 | Reservations | MISSING | NOT_AUDITED | Phase 4 |
| FR-090..094 | Phone operator | PARTIAL | BLOCKED_EXTERNAL | no SIP provider credentials; LiveKit SIP adapter unbuilt |
| FR-100..106 | Proactive runtime: event bus, WAITING_* states | PARTIAL | NOT_AUDITED | `automations` is schedule-driven; no event bus; Phase 6 |
| FR-140..142 | Capability broker / metadata / states | EXISTING | VERIFIED | `capabilities.CAPABILITIES` + `fabric.Provider` |
| FR-150..152 | Data classes + LOCAL_ONLY physical enforcement | EXISTING | VERIFIED | `f81c2d4`; test_model_gateway |
| FR-160..166 | Authorized security pack | PARTIAL | NOT_AUDITED | fabric `risk=restricted` + `authorized_scope`; no PASSIVE/ACTIVE/BLOCKED modes |
| FR-170..182 | Self-audit / change-type / self-dev isolation | PARTIAL | NOT_AUDITED | `self_upgrade.KERNEL_PATHS` protects trust roots |
| ML-05 | RuntimeSelfModel from live capability state | PARTIAL | NOT_AUDITED | data exists in `fabric.report()` + `provider_health`; no assembled self-description |
| ML-09/10 | ActionJournal / undo with before-state | PARTIAL | NOT_AUDITED | files recycle exists; no general journal |
| ML-11 | Unforgeable human confirmation | EXISTING | VERIFIED | `confirmation.Book` binds nonce to exact action; R21 timeout≠consent |
| ML-18 | EchoGuard | UNVERIFIED | BLOCKED_EXTERNAL | needs real-hardware measurement; cannot verify from this session |
| ML-19/20 | AudioDeviceManager + probe | PARTIAL | NOT_AUDITED | |
| ML-15/16 | PTT + local wake word | PARTIAL | NOT_AUDITED | Hermes hotword skills exist |
| ML-23 | Transcript dedup | UNVERIFIED | NOT_AUDITED | |
| ML-28..30 | Plugin quarantine / crash isolation | PARTIAL | NOT_AUDITED | fabric UNAVAILABLE-never-breaks-boot is the crash-isolation half |
| GB-04 | Project brain (structured, provenance) | PARTIAL | NOT_AUDITED | `brain.py` ledger is git-tracked |
| GB-13 | Skill permission manifest enforced at runtime | MISSING | NOT_AUDITED | policy categories exist for TOOLS not SKILLS |
| GB-22 | Fresh Claude worker from task ledger | PARTIAL | NOT_AUDITED | `executors/claude_code.TaskBundle` is the work packet |
| GB-23 | Windows arg-length-safe prompt transport | EXISTING | TESTING | stdin transport at `cli.py:279`, decided for a different reason; arg-length property unpinned |
| GB-32 | Team packs as data | PARTIAL | NOT_AUDITED | `org.assemble(goal)` |
| GB-35 | Push broker | PARTIAL | NOT_AUDITED | same gap as FR-100 |

## Order of work (by what the tree lacks, not by document numbering)

1. GB-23 — pin the existing stdin transport against a 200 KB packet (cheap, closes a P0 class)
2. FR-012 / GB-07/08 — skill fingerprint + dependency graph + NEEDS_REVALIDATION (the one genuine architecture gap in the Gawkbot package)
3. GB-13 — skill permission manifest, enforced as an intersection with worker/objective/user authority
4. ML-05 — RuntimeSelfModel assembled from `fabric.report()` + `provider_health` (data exists; assembly missing)
5. ML-09 — ActionJournal generalised from the files recycle path
6. FR-100/GB-35 — event bus + WAITING_* states (Phase 6; needed by both packages)

Everything Phase 3+ (browser profiles, comms, reservations, phone) waits on
these, because each of them consumes the skill/permission/self-model layer.
