# F.R.I.D.A.Y. / JARVIS — MASTER PRD v7.0
## The second brain: everything built, everything left, and the order to build it

Written 2026-09-20 from the tree at `47d112a` (+ uncommitted direct-action work),
the live processes, a Playwright probe of the Control Room, a fabric probe of
all 32 third-party providers, and every earlier ledger in `docs/`. Supersedes
v6.0 as the build authority; v6.0 stays the requirements dictionary (FR ids are
unchanged and referenced here).

This document is both the PRODUCT REQUIREMENTS and the AUTONOMOUS BUILD
CONTRACT. Section 11 is the execution loop; the agent runs it without asking.

---

## 0. Authority and rules

- **0.1 Audit before build.** Every requirement below carries what the tree
  already has. Nothing is rebuilt that exists; a second registry / router /
  memory / orchestrator beside a working one is drift (NON_NEGOTIABLE 1, 2, 15).
- **0.2 Evidence rule.** VERIFIED = a test run with its exit code, a read-back,
  a ledger row, a CI run id, or a live transcript. Code existing is not
  verification. "The model said so" is never verification.
- **0.3 Statuses.** NOT_AUDITED · EXISTING · PARTIAL · BROKEN · MISSING ·
  IMPLEMENTING · TESTING · VERIFIED · BLOCKED_EXTERNAL · REJECTED_WITH_REASON.
- **0.4 Ledgers this PRD is kept true by** (update after every requirement):
  `docs/implementation/FRIDAY_ACTION_TRUTH_MASTER_LEDGER.md` (current program),
  `docs/implementation/FRIDAY_DEFECT_REGISTER.md`, and this file's §5 table.
- **0.5 Both conversational paths or neither.** Every conversational fix lands
  on the LiveKit room agent (`agent_friday.py`) AND the browser brain
  (`friday/voice_brain.py` via `/api/ask`). The owner uses the browser path on
  a weak PC and LiveKit otherwise.
- **0.6 Machine reality.** 16 GB RAM routinely 95%+ used; C: often < 5 GB free;
  Modern Standby host (an 8 h soak cannot run here — proven, 3 attempts);
  no Docker; Node 24 present, npm not on PATH; `uv` present. Scratch on D:.

---

## 1. What "second brain" means for Friday (product definition)

Friday is Darsh's second brain: she **remembers** what he told her and what was
verified (never what a page said), **acts** on his behalf across files, browser,
desktop, Hermes and Claude Code, **tells the truth about what she did** (in both
directions), **continues** work across turns, restarts and days, and
**anticipates** (events, schedules, morning brief) without being asked twice.

The one architecture: Friday is the manager; Hermes is the execution engine and
model gateway; Claude Code is a worker; the Capability Fabric is the single
registry of external capabilities; the objective engine is the single loop for
durable work; the action-evidence ledger is the single record of what happened.

North-star metrics (measured, per E2E run, separated as PRODUCT / HARNESS /
INFRA per the harness classes):

| metric | today (M1 2026-09-20, UI probe B) | target |
|---|---|---|
| false success claims per run | 0 after 47d112a (M1: 1, the Hermes-written file refused) | 0 |
| fabricated failure claims per run | 0 after 47d112a (M1: 3) | 0 |
| exact-literal fidelity (file/content as spoken) | 100% on probe B (create/overwrite/undo/recycle) | 100% |
| objective admitted for a simple exact request | 0 after direct_action (M1: 1) | 0 |
| product step pass rate (master prompt) | M1 7/19 (pre-fix); probe B 5/7 (2 timeouts under my own suite load) | ≥ 95% on a quiet host |
| time-to-first-word, browser path | 7–32 s under CPU 100% (invalid); ~3–10 s quiet (earlier probes) | < 4 s quiet |

---

## 2. Repository truth at v7.0 (2026-09-20)

| item | value |
|---|---|
| root / branch / HEAD | `E:/friday-tony-stark-demo-main` · `main` · `47d112a` |
| uncommitted (Phase 0 of the action-truth program) | `friday/literals.py`, `friday/known_folders.py`, `friday/direct_action.py`, `tests/test_direct_action.py`, `tests/test_evidence_correlation.py`, `scripts/ui_probe.py`; edits to `agent_friday.py`, `friday/planner.py`, `friday/semantics.py`, `friday/voice_brain.py`, `friday/fsjail.py`, `friday/action_evidence.py`, `friday/honesty.py` |
| size | 138 modules in `friday/`, 40 tool modules, 35 toolsets, 200 capabilities, 32 fabric providers, 46 pinned upstream clones (+1 quarantined), 242 test modules, 72 scripts, 55 live DB tables |
| last green canonical suite | `d75e972`: 4,271 passed / 72 skipped / 0 failed |
| suite on this tree | run 1 (10:40): chunk0 17 failed — all one cause, test stubs lacking the new `act_directly_first` seam (fixed with `getattr`), chunk1 987 passed, chunk2 killed by a host reboot at 14:29; run 2 in flight (`D:/friday-test-tmp/p0_baseline2`) |
| live processes after reboot | MCP `server.py` :8000 pid 7296 (build `42c29fc+dirty`, i.e. pre-47d112a code until restarted), UI `run_ui.py --password` :8770 pid 6372, voice worker `agent_friday.py start` pid 11416, Hermes gateway (profile `friday-engineering`) pid 2604, `execution_bridge` pid 2556 |
| Hermes profile `friday` | default model `claude-opus-5` @ anthropic; routing tiers all `auto` |
| live DB | 3,354 runs, 246 messages, 400 memories, 52 objective runs, 1 automation, 0 reminders |

### 2.1 Live browser-path probe (Playwright, `scripts/ui_probe.py`, PIN via `--password` mode, host at CPU 100% from the suite)

| step | result | class | evidence |
|---|---|---|---|
| snapshot (model / families / camera) | reply arrived late (>150 s): "running on a Gemini model… families web, clock, memory, files, hermes, commerce, roles, media, desktop, selfcheck, diagnostic, work…"; it ran `selfcheck_run` (23/24 passed; 3.3 "a real Hermes job" failed under load) | INFO (timing BLOCKED_EXTERNAL) | ledger row `selfcheck_run` 15:00:42 |
| create exact file + read back | file written exactly, read back | PASS | ledger `files_create` + `files_read` verified; disk |
| overwrite + undo by action id + read back | "version two" → undo → "version one" | PASS | 3 verified rows |
| "did you open Notepad?" | "I have not actually done that, sir…" | PASS | honesty gate |
| skill families state | timed out (>160 s) | BLOCKED_EXTERNAL (load) | — |
| delegate to Hermes | refused honestly: "CRITICAL pressure: CPU 100%, RAM 97%; no new worker started" | PASS (governor) / BLOCKED_EXTERNAL (load) | reply |
| recycle + confirm | recycled | PASS | verified row |

**Real defect found by the probe (D-13, P1):** two overlapping `/api/ask`
requests interleaved on the same file — the create chain and the overwrite chain
both ran at 15:02:54 under ONE turn id, so the create's read-back reported the
overwrite's content ("version two"). The browser brain keeps `_CURRENT_TURN`
as a module global and has no per-target serialization. Fix in T1.

### 2.2 Third-party providers, probed through the fabric's own lifecycle (activate → health)

| state | providers | reason |
|---|---|---|
| READY (20) | adhd_mode, agent_reach_transcribe, agents_team_pack, claude_subagents, company_playbooks, diagram_design, dummy, dummy_backup, graphiti_memory*, gstack_process (37 workflows, 23 withheld), harness_templates, mausbot_skills, mem0_memory*, no_ai_slop, open_design (162 skills), openworker_cli, prompt_master, role_recipes, science_skills (159, 4 licence-blocked), scrapling_parse, security_skills (818 indexed, scope-gated) | *READY as skill packs; the Python libraries (graphiti-core, mem0ai) are NOT installed in the live venv — the memory adapters answer from the pinned clone, not a running store |
| DEGRADED (2) | codebase_memory (no warm daemon, ~14 s per call), graft (no graph built yet) | performance, not correctness |
| UNAVAILABLE – needs a running service (7) | anythingllm (:3001), maxun (:8080), medusa (:9000), open_notebook (:5055), openmontage/backlot (:4750), postiz (:5000), smartstore (:5000) | each is a server product; **no Docker on this machine**; every one reports UNAVAILABLE honestly and boot is unaffected (NON_NEGOTIABLE 15 holds) |
| UNAVAILABLE – not built (2) | agenticseek_cli, strix_pentest | Python packages not installed into their clones |

Verdict: the fabric contract works (no adapter breaks boot; every failure names
its cause and the env var to fix it). Nine of the 46 clones deliver nothing
today; "Open Notebook is integrated" is FALSE as a user-facing capability until
a runtime exists. Decision for the owner in §10.

---

## 3. Architecture that exists (do not rebuild)

| layer | modules | status |
|---|---|---|
| Control plane | `server.py` (FastMCP, 200 caps), `agent_friday.py` (LiveKit), `friday/ui_server.py` + `ui/index.html` (Control Room), `start.py`, `Friday.exe` launcher | EXISTING |
| Conversation truth | `friday/action_evidence.py` (ledger, 8 executors, 6 claim classes, correlation by target/operation/tense, state snapshots w/ authority), `friday/honesty.py`, `llm_node` audit before the LiveKit tee, `voice_brain._honest_about_*` | VERIFIED (unit); live rows observed in probe B |
| Exactness | `friday/literals.py`, `friday/known_folders.py` (shell Known Folders), `friday/direct_action.py`, planner `target_pinned`/`_spoken_path` | TESTING (30+16 tests green; suite pending) |
| Objective engine | `friday/objectives.py`, `continuous.py`, `planner*.py`, `store.py` (CompletionRefused), budgets/leases, WAITING_* states, `/api/event` door | VERIFIED |
| Execution | `friday/hermes_bridge.py` (supervisor, WorkRun ledger, deliveries), `executors/` (hermes, claude_code, worktrees), `capability_runtime.py` (one execution layer, authority ∩ policy) | VERIFIED |
| Model routing | `execution_economics.py` (tiers, named-model resolution 5865aab), `model_gateway.py` (catalog, telemetry), `provider_health.py` (route-keyed), LOCAL_ONLY enforcement | VERIFIED |
| Memory | `store.memories` (400), `memory_stack.py` (tiers, budget-exact), `retrieval.py` + `retrieval_sources.py` (coverage semantics, provenance), `memory_promotion.py`, `brain.py` ledger, `memory_graph.py` (bounded) | VERIFIED; **no semantic source** (FR-103j PARTIAL) |
| Skills | `skill_ladder.py`, `skill_fingerprint.py`, `skill_permissions.py`, `skill_behavior.py`, `self_upgrade.py` (KERNEL_PATHS) | VERIFIED (GB-13/16, FR-012) |
| Safety | `policy.py`, `confirmation.py` (nonce-bound), `fsjail.py`, `sandbox.py`, `netguard.py`, `sensitive_domains.py`, `write_licence.py`, `access.py` (face/PIN gate) | VERIFIED; jail observability MISSING |
| Fabric | `fabric.py` + 32 adapters, `integration_matrix.py --check` (46 clones classified), `upstream_lock.py` | VERIFIED; 9 providers deliver nothing (§2.2) |
| Files | `toolsets/files.py` (create/write/edit/read/wait/delete/recycle/undo, action journal) | VERIFIED |
| Proactive | driver tick, schedules/automations (Task Scheduler), deliveries, `/api/event` | VERIFIED; **no connector feeds the event door** |
| Voice | LiveKit pipeline (Sarvam STT → Gemini → OpenAI TTS), barge-in, UI echo guard | PARTIAL (hardware rows unmeasured) |
| Self-model | `self_model.py` snapshot from runtime state | VERIFIED |

---

## 4. Personas and the ten stories (unchanged from v6, §5) — with today's truth

| story | today |
|---|---|
| US-001 general outcome execution | works for files/objectives; browser/desktop journeys not re-verified since 09-06 |
| US-002 intellectual sparring | works (Gemini path); no eval |
| US-003 software development via Hermes/Claude | works; Hermes refused under host load (correct) |
| US-004 screen understanding | vision tools exist; perception gate prevents invented looks |
| US-005 remote operation | Control Room + `/api/event` nonce; no remote client |
| US-006 business operator | packs REGISTERED; the store/social services are UNAVAILABLE (no runtime) |
| US-007 proactive | schedules exist; no email/calendar/webhook connector |
| US-008 learning | autolearn + skill ladder; learn-by-demonstration deferred |
| US-009 personal continuity | objective continuity + memories; no semantic recall; no morning brief wired to memory |
| US-010 safe autonomy | policy/confirmation/jail/evidence — the strongest layer; jail lacks observability |

---

## 5. A–Z requirement ledger (v6 FR ids; status = evidence on 2026-09-20)

Legend: source = the ledger/commit that proves it. "→ Tn" = the tranche in §7.

### 5.1 Core (FR-001..015)
| id | requirement | status | source / gap | next |
|---|---|---|---|---|
| FR-001 | Persistent objective engine | VERIFIED | `CompletionRefused`, test_invariants | — |
| FR-002 | Autonomous loop, budgets, stuck detection | VERIFIED | leases 12 tests, GrowthGuard | — |
| FR-003 | Shared memory fabric (provenance/trust) | VERIFIED | provenance 19 tests | — |
| FR-004 | Expertise vs situational context | PARTIAL | `memory_promotion` exists; time-sensitive block unproven | → T3 |
| FR-005 | Context budget manager | VERIFIED | exact accounting c4fb14e | — |
| FR-006 | Retrieval router + coverage | VERIFIED | 32 tests | — |
| FR-007 | Codebase intelligence | PARTIAL | `codebase_memory` adapter DEGRADED (no daemon, 14 s/call) | → T6 |
| FR-008 | Resource/Memory/Skill/Capability/Plugin separation | PARTIAL | plugin lifecycle absent (ML-28..30) | → T6 |
| FR-009 | Skill lifecycle (lint/test/version/rollback/publish) | PARTIAL | ladder has capture/validate/deprecate; no lint/version; `skill_declare_permissions` untyped failure (D-11) | → T0 (typed prerequisite), T3 |
| FR-010 | Skill candidate detection | PARTIAL | autolearn/self_upgrade; no detector contract | → T3 |
| FR-011 | Skill lazy loading | VERIFIED | Hermes progressive disclosure; dedupe −23% tokens | — |
| FR-012 | Codebase skill maintenance (fingerprints, NEEDS_REVALIDATION) | VERIFIED | b251844, 16 tests | — |
| FR-013 | Skill publication | REJECTED_WITH_REASON | no publication target exists | — |
| FR-014 | Memory-to-skill bridge | PARTIAL | promotion path exists; no bridge from repeated memories to a skill candidate | → T3 |
| FR-015 | Learn by demonstration | REJECTED_WITH_REASON (deferred) | PRD "future" | — |

### 5.2 Routing (FR-020..024)
| FR-020..022 | classifier / E-A-D / width-depth | VERIFIED | execution_economics, 14+14 tests | — |
| FR-023/024 | specialist teams / isolation | PARTIAL | `hermes_team.py`, `roles.py` tested; no live multi-worker run recorded | → T6 |

### 5.3 Models (FR-030..035)
| FR-030 | Hermes model gateway | VERIFIED | named-model routing 5865aab (21 tests) | — |
| FR-031 | Hermes execution engine | VERIFIED | supervisor + WorkRun ledger; evidence rows on tool.complete (47d112a) | — |
| FR-032 | Claude Code worker | PARTIAL | executor exists; **no CLAUDE_CODE evidence writer**; plugins-off overlay measured (−2.4k tokens) not applied | → T0 (Phase 4), T2 |
| FR-033 | Model fitness router | PARTIAL | `RouteOutcomes` recorded, nothing learns from it; eval registry has no consumer | → T6 |
| FR-034 | Evidence-based provider health | VERIFIED | route-keyed | — |
| FR-035 | Local model route | VERIFIED | `_route_kind_for` endpoint proof | — |

### 5.4 Browser / web (FR-040..044)
| FR-040 | Web read router | EXISTING | `answer.plan` FAST/RESEARCH/DEEP | NOT_AUDITED live → T4 |
| FR-041 | Browser engine abstraction | PARTIAL | `/api/browser/*`, Playwright present; nodriver/browser-use clones REFERENCE | → T4 |
| FR-042 | Browser profiles (RESEARCH / AUTHORIZED_USER / TEMPORARY) | MISSING | — | → T4 |
| FR-043 | Browser action evidence | PARTIAL | BROWSER executor in ledger; no writer from `/api/browser/act` | → T4 |
| FR-044 | Human verification / CAPTCHA | MISSING | — | → T4 |

### 5.5 Desktop (FR-050..054)
| FR-050/051 | desktop control / screen understanding | EXISTING | desktop_plan/step (nonce), vision tools | NOT_AUDITED since 09-06 → T4 |
| FR-052 | Desktop evidence contract | PARTIAL | DESKTOP executor exists; writer missing | → T4 |
| FR-053 | Cancellation propagation | PARTIAL | 12 cancel sites; TTS/browser/subprocess propagation unproven | → T4 |
| FR-054 | Desktop golden journeys | PARTIAL | golden_*.py oracles exist; not rerun | → T4 |

### 5.6 Voice (FR-060..064)
| FR-060/061 | conversational voice, barge-in | VERIFIED (LiveKit live) | agent_friday.py; UI echo guard | — |
| FR-062 | Mute correctness | PARTIAL | voice-input mute gate is FUTURE in reachability | → T7 |
| FR-063 | Voice performance | PARTIAL | no TTFW measurement on a quiet host | → T7 |
| FR-064 | Voice runtime (hardware rows ML-15/16/18/19/20/23) | BLOCKED_EXTERNAL | needs owner hardware session | → T7 |

### 5.7 Communication / calendar / reservations / phone (FR-070..094)
| FR-070..074 | comms router, decisions, authority, calendar, scheduling adapter | PARTIAL | no `scheduling.py`; event door has no connector; `confirmation.Book` epoch/content-hash binding recommended before any send | → T5 |
| FR-080..082 | reservations | MISSING | — | → T5 (after comms) |
| FR-090..094 | phone operator | BLOCKED_EXTERNAL | no SIP credentials; LiveKit SIP adapter unbuilt | → T8 |

### 5.8 Proactive (FR-100..106)
| FR-100/104 | event bus, WAITING_* states | VERIFIED | 02fe012 | — |
| FR-101/102/103 | heartbeat, scheduled, conditional objectives | EXISTING | driver tick, schedules, `files_wait` | — |
| FR-105 | Morning brief | PARTIAL | `deck.py` briefs; not fed by memory/objectives/calendar | → T3 |
| FR-106 | Notification intelligence | EXISTING | `objective_deliveries` exactly-once | — |
| connectors | email / calendar / webhook / message feeding `/api/event` | MISSING | the single gap the mapping names | → T2 |

### 5.9 Business / creative / research packs (FR-110..131)
| FR-110 research | PARTIAL | web_deep_research works; open_notebook / anythingllm UNAVAILABLE (no service) | → T6 |
| FR-111 lead/CRM, FR-112 proposal | PARTIAL | company_playbooks / role_recipes READY; no CRM store | → T9 |
| FR-113 ecommerce | UNAVAILABLE | medusa / smartstore need a running store | → T9 (decision §10) |
| FR-114 social publishing | UNAVAILABLE | postiz needs a service; EXTERNAL_WRITE_CONFIRM exists | → T9 |
| FR-120..122 creative / website / media | PARTIAL | open_design READY (162 skills); openmontage UNAVAILABLE; bolt.diy/onlook/open-lovable REFERENCE | → T9 |
| FR-130/131 research notebook / deep research | PARTIAL | deep research native; notebook needs open_notebook runtime | → T6 |

### 5.10 Catalog, privacy, security (FR-140..166)
| FR-140..142 | capability catalog / metadata / states | VERIFIED | 200 caps + fabric | — |
| FR-150..152 | data classes / LOCAL_ONLY / local execution | VERIFIED | f81c2d4 | — |
| FR-160..166 | authorized security pack | PARTIAL | security_skills 818 indexed, scope-gated; strix not built; no PASSIVE/ACTIVE/BLOCKED modes | → T9 |

### 5.11 Learning & self-development (FR-170..193)
| FR-170..172 | learn from verified work / change-type / self-audit | PARTIAL | autolearn; self-audit = selfcheck (24 checks) | → T3 |
| FR-180..182 | self-dev loop / isolated changes / promotion gate | PARTIAL | worktrees executor, KERNEL_PATHS; promotion history FUTURE | → T6 |
| FR-190..193 | skill / MCP / plugin / external-framework rule | VERIFIED (rule) / PARTIAL (plugin lifecycle) | ECC quarantined 8fdf417; Provider import rules | → T6 |

### 5.12 Action-truth program (this PRD's T0) — see the master ledger for live status
| AT-01..03 | harness classes, evidence ledger, fail-closed audit | VERIFIED (unit) | 47d112a |
| AT-04 | literals + Known Folders + direct action + planner fidelity | TESTING | uncommitted; 264 targeted green; suite run 2 pending |
| AT-05 | correlation (target / operation / tense; 5 negative controls) | TESTING | `tests/test_evidence_correlation.py` 16 green |
| AT-07 | snapshot scope + authority_level, migration | TESTING | same |
| AT-08 | CLAUDE_CODE + OBJECTIVE_WORKER writers; Hermes claimed-write verification | MISSING |
| AT-09 | fail-closed proof across tts_node + transcription_node + UI store | PARTIAL |
| AT-10/11 | jail INFO observability; containment variant tests | MISSING |
| AT-12 | `orchestration_new_objective → objective_start` alias | MISSING |
| AT-13 | `skill_declare_permissions` typed PREREQUISITE_REQUIRED | MISSING |
| AT-14/15/16 | targeted E2E 3-12 on a restarted worker; M2; full regression + CI | NOT_AUDITED |

---

## 6. Open defects (register: `FRIDAY_DEFECT_REGISTER.md`)

| id | sev | defect | status |
|---|---|---|---|
| D-10 | P2 | hallucinated `orchestration_new_objective` | OPEN → AT-12 |
| D-11 | P2 | `skill_declare_permissions` untyped failure for uncaptured skill | OPEN → AT-13 |
| **D-13** | **P1** | UI brain: overlapping `/api/ask` requests share `_CURRENT_TURN` and interleave direct actions on the same file (probe B 15:02:54) | OPEN → T1 |
| D-14 | P2 | `ui_probe.py` attributed a late response to the next step | FIXED (match by request body) |
| D-15 | P3 | UI snapshot reply lists families ("include web, clock…") without a state snapshot row — `_STATE_RE` misses the "include" phrasing; the list comes from `_surface()` so it is true, but unaudited | OPEN → T1 |
| D-16 | P2 | MCP server still runs `42c29fc+dirty` after the reboot — the launcher restarted an old build; `restart_friday.py --check` must be part of every E2E preflight | OPEN → T0 Phase 12 |
| D-17 | P3 | selfcheck 3.3 "a real Hermes job" fails under host load and reports as a product failure | OPEN → classify as BLOCKED_EXTERNAL when the governor shed the worker |

---

## 7. The build program (tranches, in dependency order)

Each tranche: AUDIT existing → MAP → IMPLEMENT ONLY GAPS → tests + negative
controls (plants must turn red) → VERIFY on both paths → ledger → commit → next.
No tranche starts before the previous tranche's exit criteria are VERIFIED or
truthfully BLOCKED_EXTERNAL.

### T0 — Action truth (in progress; master prompt of 2026-09-20 is the contract)
Phases 0–14 as written; exit = §20 of that contract (no false success, no
fabricated failure, no wrong-executor denial, no stale-state certainty, literals
preserved, jail diagnosable, alias + typed prerequisite, M2 product-green, full
regression green, CI read for the exact commit).

### T1 — Browser-brain turn integrity (new, from probe B)
- One turn at a time per UI session: `/api/ask` takes a per-session lock; an
  overlapping ask is answered `409 {"busy": true, "since": …}` and the page shows
  "still on the last one" instead of sending a second model call. Per-turn
  context becomes a `contextvars.ContextVar`, not a module global.
- Direct-action chains serialize per target path (a lock keyed by the resolved
  path) so two chains can never interleave on one file.
- `_STATE_RE` widened to "families/skills … include/are/is …" with the
  `_surface()` listing recorded as an OBSERVED snapshot (`scope=ui_families`).
- Negative controls: two concurrent asks on the same file → second is 409 and
  the ledger shows one turn id per chain; a stubbed slow model → no interleave.
- Exit: probe B rerun on a quiet host with all 7 steps PASS and one turn id per
  request in the ledger.

### T2 — Connectors into the event door (unblocks FR-070..106 and the morning brief)
- Email first (IMAP/Graph read-only → `/api/event kind=email`), then calendar
  (read-only), then webhook. Each connector: pinned upstream or stdlib, `risk`,
  `permissions`, health, and an evidence row per delivered event.
- `confirmation.Book` epoch/content-hash binding before any connector may SEND.
- Exit: an objective parked on WAITING_EVENT wakes from a real inbound email in
  a live test; no send capability exists yet.

### T3 — Second brain: memory, recall, brief, learning
- Semantic retrieval source (FR-103j) behind `RetrievalSource` — local
  embeddings only (LOCAL_ONLY respected); coverage stays honest (TOP_K).
- Memory→skill bridge (FR-014): repeated verified procedures become skill
  CANDIDATES via the ladder, never active skills.
- Morning brief (FR-105) assembled from objectives, deliveries, calendar events
  (T2), memories due, provider health — spoken on both paths, evidence-backed.
- Expertise vs situational (FR-004): time-sensitive facts never promoted.
- Skill lifecycle: lint + version on the ladder (FR-009).
- Exit: brief cites its sources; recall test corpus (oldest-row-behind-hundreds)
  passes for semantic + lexical + hybrid; plants red.

### T4 — Browser & desktop re-verification and evidence
- BROWSER/DESKTOP executors write evidence rows (`/api/browser/act`,
  `desktop_step`), with DOM/accessibility snapshot refs; Playwright
  trace-on-failure in the golden journeys.
- Browser profiles RESEARCH / AUTHORIZED_USER / TEMPORARY (FR-042); CAPTCHA
  hand-off to the owner (FR-044) — never solved by Friday.
- Cancellation propagation proven through TTS, browser and subprocess (FR-053).
- Exit: desktop golden journeys rerun green; a browser click claim is refused
  without its row (negative control).

### T5 — Communication & calendar actions (after T2 + T4)
- Router / decisions / authority (FR-070..072) with EXTERNAL_WRITE_CONFIRM on
  every send; calendar write; scheduling adapter; reservations (FR-080..082) as
  a generic intent over browser profiles.
- Exit: a send happens only after a nonce-bound confirmation; evidence row per
  message; undo/cancel where the provider allows.

### T6 — Execution depth: Claude Code evidence, fitness learning, plugin lifecycle, code intelligence
- CLAUDE_CODE evidence writer + plugins-off launch overlay (measured −2.4k
  tokens/call); `RouteOutcomes` consumed by the fitness router with an eval
  registry (FR-033); plugin QUARANTINED→PROMOTED lifecycle over the fabric
  (ML-28..30); `codebase_memory` warm daemon (FR-007); live multi-worker team
  run recorded (FR-023).
- Exit: a Claude-written file is claimable from its verified row; a route
  outcome changes a later route in a test.

### T7 — Voice hardware rows (owner session required)
ML-15/16 PTT + wake word, ML-18 EchoGuard, ML-19/20 devices, ML-23 dedup,
FR-062 mute gate, FR-063 TTFW measured on a quiet host. BLOCKED_EXTERNAL until
the owner is at the machine; the harness (`livekit_probe2.py`, `e2e_master.py`)
is ready.

### T8 — Phone operator (FR-090..094)
BLOCKED_EXTERNAL: SIP provider credentials. Design stays in v6.

### T9 — Business / creative / security packs (runtime decision first, §10)
Ecommerce (medusa/smartstore), social (postiz), research notebook
(open_notebook/anythingllm), media (openmontage), pentest (strix) all need a
service runtime that this machine does not have (no Docker). Two honest
options: (a) install Docker Desktop / WSL2 and run them as SIDECARs with health
probes; (b) keep them REFERENCE_ONLY and remove the user-facing promise. Until
decided, their capabilities stay UNAVAILABLE and Friday says so.

---

## 8. Skills and plugins policy (the owner's slash list, applied not ported)

| use | where | why |
|---|---|---|
| brainstorming, writing-plans, spec | PRD/tranche design (this document's shape) | one design pass per tranche, no re-litigation mid-build |
| find-skills, webapp-testing, chrome-devtools, playwright | live checks (`ui_probe.py`), third-party verification | evidence artifacts, trace-on-failure |
| security-review, ultrareview, requesting/receiving-code-review, ponytail-audit | at each tranche gate, once | second perspective before commit |
| learning-from-mistakes, remember, self-improve | ledgers + skill patches after each tranche | lessons land in `friday-capability-wiring`, not in prose |
| gstack (37 workflows) | already a fabric provider (`gstack_process`) | via the fabric, never a parallel runner |
| autopilot / ultrawork / ralph / omc / team / sciomc / deepinit / trading-desk (oh-my-claudecode) | **NOT ported** | they multiply model calls and duplicate Friday's objective engine + Hermes delegation (NON_NEGOTIABLE: no duplicate orchestrators); measured cost = floor × calls |
| token floor facts | Hermes skills index 12.5k → 9.6k tokens (dedupe applied); Claude `-p` floor ≈ 31–34k regardless of plugins (`--strict-mcp-config`); plugins-off overlay −2.4k | spend goes to fewer calls, not smaller prompts |

---

## 9. Verification gates (every tranche)

1. Targeted tests for the change + negative-control plants (each guard proven red).
2. `pytest tests/ -m "not live and not slow"` whole, via `scripts/baseline_suite.py`
   with `--basetemp` on D:; read per-chunk exit codes, never a pipe's.
3. `scripts/restart_friday.py --check` — the running server hash matches the
   tree (D-16 says this was skipped once). Restart the voice worker before any
   live probe; record old/new PID and HEAD.
4. Live probe on a QUIET host (no suite running — probe B's timings were
   invalid under my own load): `scripts/ui_probe.py` (browser path) and
   `scripts/e2e_master.py` (LiveKit path); verdict classes PASS /
   PRODUCT_FAIL / HARNESS_FAIL / INVALID_TEST / BLOCKED_EXTERNAL / INFO.
5. `scripts/integration_matrix.py --check`, `scripts/upstream_lock.py --check`.
6. Push one commit per CI verdict; read the run with `ci_watch.py`; record run id.
7. Ledgers updated; commit message names the requirement ids.

---

## 10. Decisions needed from the owner (not blockers for T0–T3)

1. **Service runtime for the 7 UNAVAILABLE providers** (open-notebook,
   anythingllm, maxun, postiz, medusa, smartstore, openmontage): install Docker
   Desktop/WSL2 on D:, or downgrade them to REFERENCE_ONLY. Default if silent:
   REFERENCE_ONLY, promises removed from the spoken capability list.
2. **Voice hardware session** (T7): ~45 minutes at the machine with mic/speaker.
3. **SIP provider** for the phone operator (T8), or park it.
4. **Memory libraries**: install `mem0ai` / `graphiti-core` into the live venv
   (they are READY only as skill packs today), or keep native memory only.
   Default: native only — one memory (NON_NEGOTIABLE), no second store.

---

## 11. Execution contract (how the agent works this PRD)

Loop per requirement: DISCOVER → AUDIT → (RESEARCH only if it changes the
decision) → MINIMAL REPRODUCTION → MINIMUM CORRECT FIX → TARGETED TEST →
NEGATIVE CONTROL → BROADER REGRESSION → VERIFY (both paths) → LEDGER → NEXT.

Never: weaken a test, log file contents at INFO, write into `data/` from a
test, run pytest with `PYTEST_ADDOPTS=--basetemp` in the env, run an E2E while
the suite runs, kill a process the agent did not start, touch `data/ada.sqlite3`
directly, or port a second orchestrator.

Stop only for: whole tranche VERIFIED / BLOCKED_EXTERNAL, a consequential action
needing the owner, or a hard budget ceiling. The T0 contract's termination
banner (`FRIDAY_ACTION_TRUTH_PROGRAM_COMPLETE …`) closes T0; each later tranche
closes with `FRIDAY_T<n>_COMPLETE` and the same status fields.

Immediate queue (today): finish T0 Phase 0 (suite run 2 → commit
`feat(actions)`), T0 Phases 1–11, restart worker + MCP (D-16), targeted E2E on a
quiet host, M2, full regression, CI; then T1 (D-13) before anything else.
