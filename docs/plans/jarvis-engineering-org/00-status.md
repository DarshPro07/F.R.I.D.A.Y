# jarvis-engineering-org — status

**Feature:** turn Friday → Hermes into a disciplined engineering organisation: progress
you can hear, quota-aware model routing, a real task contract, Hermes specialist profiles
on the native kanban, Claude specialists with private memory, failure fingerprints,
structured handoffs, controlled memory promotion. Owner decisions (2026-09-04 11:30):
milestones + timed digest; auto-switch on quota and say so; native Hermes profiles if
supported (they are); full plan now.

**Started:** 2026-09-04 11:24 IST. **Verdict:** PARTIALLY_VERIFIED (`07-final.md`, 16:05).

| Gate | State |
|---|---|
| 0 Reality | done — `01-reality.md` |
| 1 Product | done — `02-product.md` |
| 2 Architecture | done — `03-architecture.md` (+ ADR-001, ADR-002) |
| 3 Program design | done — `04-program-design.md` |
| 4 Slices | done — `05-slices.md` |
| 5 Verification | done — `06-verification.md` (final gate 3,526 passed / 2 owner-owned failed / 1 skipped; Playwright 46) |

## Slices (sequential unless marked ∥; builders are the project `friday-*` agents)
| # | Slice | Builder | State |
|---|---|---|---|
| S1 | Task contract: acceptance/scope/verification reach Hermes; contract-driven worker prompt | friday-objective-engineer | done (6 fields, 10 sections, 3 tests fail-before, 115 green) |
| S3 ∥ | Progress: milestones + 3-min digest on the room path (`progress_digest.py`, `agent_friday.speak_progress_digests`) | friday-voice-engineer | done (7 tests, 86 green); browser half → S3b |
| S3b | `/api/work`, Work section, `work/status` by voice, Playwright spec | friday-voice-engineer (fresh) | done (61 unit + 1 e2e green; live-shape assertion → S8) |
| S2 | Quota-aware routing: capped provider → cooldown → next candidate; spoken switch | friday-objective-engineer | done (CAPPED kind, `provider_cooldowns`, `candidates()`, 12 tests, 77 green; fallback config shape to confirm in S5; pre-fix failing lines not captured → verifier) |
| S4a | Failure fingerprints + strategy change + BLOCKED + iteration budget | friday-objective-engineer | done (`failure_fingerprint`, `_requeue_or_block`, TASK_BLOCKED, `objective_tasks.detail`; 5 tests, 50 green; CONNECTIVITY diagnose-once change → verifier) |
| S4b | Structured handoff record on the work run; worker prompt names the Claude subagent | friday-objective-engineer | done (`handoff.py`, stored + spoken, ITERATION BUDGET rendered; 7 tests, 47 green; fields without source data stay empty) |
| S6 ∥ | Claude specialists: 4 missing agents, review-only tools, role→agent map, lint | friday-tools-engineer | done (4 agents A/ship, `roles.claude_agent_for`, 27 tests; executor wire done in S4b) |
| S5 | Hermes specialist profiles + kanban routing for durable multi-role work (+ one live kanban task) | friday-objective-engineer | done (`hermes_team.py`, `development._execute_via_team`, fake CLI; 9 tests, 85 green; LIVE: task t_27a0e35d ran on `friday-engineering`, gateway started/stopped, friday gateway untouched) |
| S7w | `on_terminal` → `promote_handoff` wire | Fable | done (`tests/test_promotion_wire.py`, 12 green with writeback + handoff suites) |
| S8a | Golden engineering journey integration test | friday-qa-engineer | written in an isolated worktree (which lacked the uncommitted slices); copied to tests/ by Fable; fails at step 4 (`strategy_changes` missing) → S9 |
| S8b | Independent Opus review of the whole build | friday-final-reviewer | done: verdict BLOCK — 4 high (team path fabricated verification; blocking sleep in async execute; all-cooled candidate still used + CAPPED delay 0; digest starves), 2 medium, 3 low → S9 |
| S9 | Review fixes + journey green + Hermes-bundle subagent line | friday-objective-engineer + Fable | highs 1–4 done by the builder; Fable: store `detail` now merges (the real journey defect), restart step corrected, journey 1 passed; 7 silent handlers now log; IRREVERSIBLE_KEEP_CONFIRM for the 7 gate failures; items 5–8 → S9b (Fable) |
| S9b | Review items 5–8 (hint feed-through, atomic cooldowns, bounded milestones, Hermes ROLE subagent line) | Fable | done (`tests/test_review_followups.py` 5 passed; journey 1 passed) |
| S8c | 4-chunk gate + Playwright on the final tree; restart via launcher | Fable | baseline: 3,506 passed / 10 failed (7 fixed by IRREVERSIBLE_KEEP_CONFIRM, 1 census fixed, 2 owner-owned lock-template failures); final gate 3,526 passed / 2 failed (owner-owned `test_upstream_lock`) / 1 skipped at 15:58; Playwright 46 passed; launcher restart 16:02 → :8000, :8770 + Chrome app up, agent registration below |
| S10 | Live defect 16:08 (first digest after the restart recited ~20 dead runs): work-run `owner` + `sweep_orphans` (LOST), persisted `progress_json` read cross-process, digest capped at 3 runs | Fable | done (`tests/test_work_run_orphans.py` 4 fail-before → pass + 1 writer-side test; 9 neighbouring suites 99 passed; live after the 16:2x restart) |
| S11 | Grounding: "what did Hermes finish / why that model" answered from the run ledger by `voice_brain._grounded_work_answer` + `progress_digest.outcome_line` (LOST said as lost; gate/test runs excluded; "no finished job on record") | Fable | done (`tests/test_voice_grounding.py` 6 fail-before → 7 pass; voice_brain_ui + digest + orphans + voice_pipeline + ui_server + hermes_engine 115 passed) |
| S12 | Launcher logs: `build/Friday.cs` StartHidden → cmd.exe redirection, appended `logs/mcp_boot.log`, `ui_boot.log`, `agent_boot.log` with `=== start` stamps; rebuilt with csc 4.0 → `build/Friday_new.exe` → `Friday.exe` (old exe kept as `build/Friday.exe.bak-2026-09-04`) | Fable | done; live 17:55: all three files receive the new processes' output |
| S13 | Live validation through the control room with Claude in Chrome | Fable | in progress (face gate is the owner's step) |
| S7 ∥ | Memory promotion: candidate → evidence → contradiction → canonical | friday-objective-engineer | done (`memory_promotion.py`, autolearn gated; 6 tests, 69 green; `promote_handoff` wire into `on_terminal` → after S5) |
| S8 | Golden journey + regression + Playwright; Opus review | friday-qa-engineer, friday-tech-lead | superseded by S8a / S8b / S8c |

## Next action
Owner: commit the day's work; live checks J1–J9 in `docs/HARD_PROMPTS.md`; a real provider cap and the room digest with real audio remain unverified.

## Constraints in force
Never touch `data/ada.sqlite3`; tests in `.venv-verify`; no commits by agents; Hermes is the
execution engine, Friday the manager, Claude specialists bounded beneath; no second objective
engine / task graph / registry / memory / permission system; every builder ≤ 25 turns, ≤ 250-word
report into `05-slices.md`; real-test rule for every fix.
