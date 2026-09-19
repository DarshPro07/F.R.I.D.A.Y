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
| FR-012 / GB-07/08 | Skill fingerprint + dependency graph + NEEDS_REVALIDATION | MISSING | **VERIFIED** | `b251844` `friday/skill_fingerprint.py`; 16 tests on a real ladder + temp tree; 4 plants red/restored; live probe: README sweep → `untouched`, unchanged declared file → `clean`, absent dependency reported at record time; tools `skill_declare_dependencies` / `skill_revalidation_sweep` on the vnext surface, gates 105+292 green |
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
| FR-100..106 | Proactive runtime: event bus, WAITING_* states | PARTIAL | **VERIFIED** (the gap; the rest EXISTING) | Mapping first: `docs/product/PROACTIVE_RUNTIME_MAPPING.md` — heartbeat (`_driver_tick`/`next_wake`), schedules (`schedules`/`automations`, OS-owned clock), WAITING_APPROVAL/PROVIDER/TIME, worker waits, notification intelligence (`objective_deliveries`) all EXISTING under other names; the ONE gap was an external-event wait. Built on the existing wait machinery, not beside it: `contracts.WAITING` (non-terminal, not CLAIMABLE) + `output["wait"]={kind,key,deadline_s}` → `RunStatus.WAITING_EVENT` (in `RUN_WAITING_STATUSES`, so driver/watchdog/invariant already treat it as legitimate) + `FailureKind.EVENT_REQUIRED`; `continuous.deliver_event(store, kind, key, payload)` is the one door back (exact-key match, payload redacted via `observability.redact`, `event.delivered`/`event.unmatched` in the trace); deadline expiry is honest (TRANSIENT, "did not arrive"); unknown kinds refused at park time; the resumed task re-runs WITH `delivered_event` (runtime passes it only to a function that declares it). First source: `files_wait` (parks on a missing path; the driver tick is the file watcher; read-back still the verification, an event claiming a file that is not on disk FAILS). Remote door `POST /api/event` (session gate 423 + A-042 nonce/timestamp; `file` kind refused from outside). 12 tests on a real Store incl. end-to-end park→appear→deliver→re-run→COMPLETED, 11 plants red/restored byte-identical; gates 158 + 394 + 115 + 59 green |
| FR-140..142 | Capability broker / metadata / states | EXISTING | VERIFIED | `capabilities.CAPABILITIES` + `fabric.Provider` |
| FR-150..152 | Data classes + LOCAL_ONLY physical enforcement | EXISTING | VERIFIED | `f81c2d4`; test_model_gateway |
| FR-160..166 | Authorized security pack | PARTIAL | NOT_AUDITED | fabric `risk=restricted` + `authorized_scope`; no PASSIVE/ACTIVE/BLOCKED modes |
| FR-170..182 | Self-audit / change-type / self-dev isolation | PARTIAL | NOT_AUDITED | `self_upgrade.KERNEL_PATHS` protects trust roots |
| ML-05 | RuntimeSelfModel from live capability state | PARTIAL | **VERIFIED** | `friday/self_model.py`; 15 tests, 7 plants red/restored (incl. both static claims restored → red, no-ledger-reads-healthy → red); snapshot = modalities (mss/display, cv2; "snapshot only, never a live feed") + `fabric.family_report()` + `provider_health.assess` (no ledger = UNPROBED) + durable operator switches; injected into BOTH prompt paths (`voice_brain._persona`, `agent_friday.build_instructions`); static "look at his screen or through the camera" and "You have 169 tools" removed; golden journey §87 (camera off → "cannot look at the camera (lens cap on)" → on → identical text, no prompt edit) passes; live probe on this host: verify venv says camera UNAVAILABLE (cv2 missing), live venv says AVAILABLE; tools `self_model_snapshot` / `self_model_switch` (DEVICE_SETTING); gates 268 + 176 + 107 green |
| ML-09/10 | ActionJournal / undo with before-state | PARTIAL | **VERIFIED** | `friday/action_journal.py` (before-state CAPTURED, never guessed: no hash → IRREVERSIBLE with reason; conflict check against the hash Friday left; reversal read back and only a match is REVERSED; before-content in a bounded backup store, never in the row; group rollback newest-first stops at the first conflict); wired into `files.write/create/edit/move` in `toolsets/files.py` (journal failure never fails a verified write); tools `files_actions` (READ_LOCAL_SAFE) / `files_undo` (FILE_WRITE, jail re-resolved for target AND move source before the journal is consulted); 19 tests on the REAL jail, 9 plants red/restored byte-identical; live probe through the MCP adapter: write → REVERSED with "read back 9 bytes sha == before" → disk shows the original; a user edit in between → CONFLICT, disk untouched; `action_journal.py` + its test joined `KERNEL_PATHS`; gates 283 + 210 green. copy/recycle reversal is the next extension (named in `reverse`), not half-built |
| ML-11 | Unforgeable human confirmation | EXISTING | VERIFIED | `confirmation.Book` binds nonce to exact action; R21 timeout≠consent |
| ML-18 | EchoGuard | UNVERIFIED | BLOCKED_EXTERNAL | needs real-hardware measurement; cannot verify from this session |
| ML-19/20 | AudioDeviceManager + probe | PARTIAL | NOT_AUDITED | |
| ML-15/16 | PTT + local wake word | PARTIAL | NOT_AUDITED | Hermes hotword skills exist |
| ML-23 | Transcript dedup | UNVERIFIED | NOT_AUDITED | |
| ML-28..30 | Plugin quarantine / crash isolation | PARTIAL | NOT_AUDITED | fabric UNAVAILABLE-never-breaks-boot is the crash-isolation half |
| GB-04 | Project brain (structured, provenance) | PARTIAL | NOT_AUDITED | `brain.py` ledger is git-tracked |
| GB-13 | Skill permission manifest enforced at runtime | MISSING | **VERIFIED** | `8252cd4` `friday/skill_permissions.py`; 36 tests, 9 plants red/restored; enforced at `CapabilityRuntime` (refuses before resolve), `ClaudeCodeExecutor.launch_for` (CLI allowlist narrowed), `HermesSupervisor.delegate` (prohibitions into contract); no manifest = read-only; gates 135+263+213 green |
| GB-16 | Skill behaviour testing (trigger / procedure / negative / competing) | MISSING | **VERIFIED** | `friday/skill_behavior.py` (SkillBehaviorEvaluator, native); 17 tests, 5 plants red/restored; deterministic detectors over the trace (no model grades), failed step never satisfies a later `after_step`, verdict INCOMPLETE unless all three strictness scenarios ran, trace constructors redact home/secret shapes (ECC #2730); tools `skill_behavior_scenarios` / `skill_behavior_grade` on the vnext surface; gates 305 + 223 green. Concept from ECC `skill-comply`, quarantined (see below) |
| GB-22 | Fresh Claude worker from task ledger | PARTIAL | NOT_AUDITED | `executors/claude_code.TaskBundle` is the work packet |
| GB-23 | Windows arg-length-safe prompt transport | EXISTING | TESTING | stdin transport at `cli.py:279`, decided for a different reason; arg-length property unpinned |
| GB-32 | Team packs as data | PARTIAL | NOT_AUDITED | `org.assemble(goal)` |
| GB-35 | Push broker | PARTIAL | **VERIFIED** | same mechanism as FR-100: `deliver_event` + `/api/event` is the door an external signal takes to a waiting objective; no second bus, no subscriptions table |

## Order of work (by what the tree lacks, not by document numbering)

1. ~~GB-23~~ VERIFIED `d7e365d`
2. ~~FR-012 / GB-07/08~~ VERIFIED `b251844`
3. ~~GB-13~~ VERIFIED `8252cd4`
3b. ~~GB-16~~ VERIFIED (skill behaviour evaluator; ECC quarantine)
4. ~~ML-05~~ VERIFIED (RuntimeSelfModel; static capability claims removed from both prompts)
5. ~~ML-09~~ VERIFIED (ActionJournal; `files_undo` verified by read-back, conflict-safe)
6. ~~FR-100/GB-35~~ VERIFIED (external-event wait on the existing wait machinery; `files_wait` + `/api/event`)

Everything Phase 3+ (browser profiles, comms, reservations, phone) waits on
these, because each of them consumes the skill/permission/self-model layer.

## External skill ecosystem check (`npx skills find`, 2026-09-19)

Searched before building GB-13 / ML-09 / project-brain, per the
audit-before-build rule. Nothing clears the find-skills quality bar
(1K+ installs, reputable source) for the load-bearing pieces:

| Query | Best hit | Verdict |
|-------|----------|---------|
| skill lint | Go linters; `ar9av/obsidian-wiki@wiki-lint` 3.4K (Obsidian vaults) | not applicable |
| skill permissions | Salesforce / Argent / NetSuite domain skills | not applicable |
| codebase memory | `reason-machines/mcp-skills@codebase-memory-mcp-intelligence` 446 | below bar; FR-007 is a fabric adapter pinned by commit, not a skill install |
| project knowledge wiki | `codesight-ai-context` 536, rest <100 | below bar |
| action undo journal | `open-gsd/gsd-core@gsd-undo` 281 (GSD-specific) | below bar |

Skill-authoring knowledge (`skill-creator`, `writing-skills`) is already
installed locally. The permission manifest in particular cannot be a
third-party skill: it must be enforced as an intersection with FRIDAY's own
`PolicyEngine`, which is code in this tree.

## ECC quarantine import (2026-09-19, FR-192 / FR-193)

`third_party/quarantine/ecc/` holds two skills from `affaan-m/ecc` (MIT),
pinned to commit `QUARANTINE.json:commit` with per-file sha256, state
`QUARANTINED`. They are outside every skill discovery root (Hermes
`HERMES_HOME/skills` + `skills.external_dirs`; Claude Code `.claude/skills`)
and `tests/test_skill_behavior.py::TestQuarantine` proves that plus the
hash pins plus "no production module imports from it". Not activated.

| Skill | Review finding | FRIDAY disposition |
|-------|----------------|--------------------|
| `skill-comply` | Concept is right (supportive/neutral/competing strictness; trace → spec-step grading). Two defects: `grader.py` classifies events with an LLM (haiku) so the verdict is a model's opinion; and a step that failed its temporal check still satisfies later `after_step` deps via the `classified` fallback. `runner.py` persists the operator home path into reports (upstream #2730). | **Clean reimplementation** `friday/skill_behavior.py`: deterministic detectors, failure-is-not-evidence, redacting trace constructors. Import stays reference-only. |
| `operator-approval-loop` | A prose approval protocol (ask → wait → act). FRIDAY already has the stronger, unforgeable form: `confirmation.Book` binds a nonce to the exact action and timeout ≠ consent (ML-11 VERIFIED). Adding prose on top would be a second approval system. | **REJECTED_WITH_REASON** — duplicate of ML-11; no code taken. |

Dropped from scope by the user (2026-09-19): the F:-drive portable brain
/ laptop-switching design that appeared in the PRD authoring conversation.
Not tracked here.
