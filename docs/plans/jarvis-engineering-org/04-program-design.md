# 04 — Program design

Each builder appends its actual design to `05-slices.md`. Contracts here are binding;
bodies are the builder's.

## Files
| Slice | Create | Modify |
|---|---|---|
| S1 contract | `tests/test_task_contract.py` | `friday/executors/claude_code.py` (TaskBundle fields), `friday/development.py` (fill acceptance/allowed_paths/verification/role/iteration_budget/known_facts), `friday/executors/hermes.py` (forward all), `friday/hermes_bridge.py` (TaskBundle.render sections; `_HERMES_MEMORY_TOKENS` untouched) |
| S3 progress | `friday/progress_digest.py`, `tests/test_progress_digest.py`, `e2e/work-panel.spec.ts` | `agent_friday.py` (digest task beside `drain_hermes_deliveries`, speaks through `deliver_message`, respects the pause rule), `friday/ui_server.py` (`/api/work`), `ui/index.html` (Work section + digest lines in the follow loop), `friday/voice_brain.py` ("what's running" → digest; tool text) |
| S2 quota | `friday/provider_cooldowns.py`, `tests/test_quota_routing.py` | `friday/provider_diagnostics.py` (CAPPED + reset parsing), `friday/execution_economics.py` (`candidates`, cooldown-aware `plan_delegation`, `route_reason`), `friday/hermes_bridge.py` (record `route_reason`, mark cooldown on capped errors), `friday/tools/hermes_control.py` (pass provider+reason), `friday/continuous.py` (CAPPED is not TRANSIENT: no blind retry on the same provider) |
| S4 loop | `friday/handoff.py`, `tests/test_failure_fingerprint.py`, `tests/test_handoff.py` | `friday/continuous.py` (fingerprint + strategy change + BLOCKED/WAITING_* terminal states), `friday/objectives.py` (FailureKind/terminal states if missing), `friday/store.py` (additive columns), `friday/hermes_bridge.py` (handoff on terminal, `render_completion` from handoff) |
| S6 specialists | `.claude/agents/friday-{debugger,performance-reviewer,final-reviewer,codebase-researcher}.md` | `friday/roles.py` (`CLAUDE_AGENT_FOR_ROLE`), `friday/executors/claude_code.py` (worker prompt names the subagent), `docs/plans/jarvis-agentic-team/team.md` roster |
| S5 team | `friday/hermes_team.py`, `tests/test_hermes_team.py` (fake `hermes` CLI) | `friday/hermes_bridge.py` (`delegate(profile=)` if supported; supervisor per profile else kanban), `friday/development.py` (route by `roles.compile_team` size), `friday/execution_economics.py` (route HERMES_MULTI → team) |
| S7 memory | `friday/memory_promotion.py`, `tests/test_memory_promotion.py` | `friday/handoff.py` → promotion feed, `friday/autolearn.py` (route candidates through promotion), `friday/memory_stack.py` (read provenance) |
| S8 journey | `tests/test_golden_engineering_journey.py`, `scripts/golden_engineering_org.py` | — |

## Types and contracts
See 03 §Interfaces. Terminal task states: SUCCEEDED, FAILED, PARTIAL, BLOCKED, WAITING_QUESTION, WAITING_PERMISSION, CANCELLED. Worker prompt sections in this order: GOAL, ACCEPTANCE CRITERIA, KNOWN FACTS, ASSUMPTIONS, CONSTRAINTS, ALLOWED SCOPE, PROHIBITED ACTIONS, ROLE / RESPONSIBILITY, VERIFICATION, REPORTING CONTRACT.

## Call flows
1. Digest: `agent_friday` loop every 20 s → `progress_digest.compose()` → speak new milestones immediately; speak the digest when `now - last_digest_at ≥ FRIDAY_DIGEST_SECONDS` (180) and something changed; on completion speak `handoff.summary + route_reason`.
2. Quota: bridge sees a capped error → `provider_cooldowns.mark` → task fails with kind CAPPED → `continuous` requeues immediately with `route_reason` → `plan_delegation` picks the next candidate → digest line "Claude is capped until 14:00, GPT-5.x took this job".
3. Contract: `DevelopmentRun.execute` → bundle → `HermesExecutor.execute` → bridge `TaskBundle(...).with_memory().render()` → Hermes.
4. Fingerprint: attempt fails → `failure_fingerprint` → compare → same & no evidence → `strategy_changes+1`, next role/plan → cap → BLOCKED with reason spoken once.
5. Team: `development` size ≥ medium and `compile_team` ≥ 2 roles → `hermes_team.submit_board` → poll → handoff → `evaluation.verify` → promotion.

## Error model
CAPPED is a distinct FailureKind: never retried on the same provider inside the window. Kanban/profile errors degrade to `delegate()` with a logged reason. Digest errors are logged, never spoken.

## Test design (each with its known failure mode)
- `test_acceptance_reaches_hermes_from_a_development_run` — fails today: empty acceptance.
- `test_worker_prompt_has_the_ten_sections_in_order` — fails today: no sections.
- `test_capped_provider_is_skipped_until_reset` — fails today: same provider retried.
- `test_switch_is_spoken_with_reason` — fails today: no route_reason.
- `test_same_fingerprint_without_new_evidence_changes_strategy` — fails today: blind retry.
- `test_strategy_budget_ends_in_blocked_not_a_loop` — fails today.
- `test_handoff_from_work_run_has_every_field` — fails today: no handoff.
- `test_digest_is_composed_only_from_events_and_deduplicates` — fails today: module missing.
- `test_room_path_speaks_milestones_and_a_timed_digest` — fails today: no narration on LiveKit.
- `test_profile_routing_falls_back_to_friday_when_missing` / `test_kanban_capability_detected`.
- `test_reviewer_agents_have_no_write_tools`, `test_role_to_agent_map_covers_every_role`.
- `test_promotion_rejects_unverified_and_secret_shaped_candidates`, `test_contradiction_supersedes_not_overwrites`.
- Golden journey: objective → team → injected failure → new hypothesis → repair → review → verify → promotion-eligible; restart mid-way recovers.

## Playwright journeys
Work section lists every running job with its latest line and model; digest lines appear in the transcript on change; `/api/work` shape. Existing 45 specs stay green.

## Least-confident decisions
1. Whether Hermes 0.20.6's gateway accepts a per-session profile or needs one gateway per profile (S5 checks `hermes gateway --help` and `session.create` params live).
2. Digest cadence on the room path vs the pause rule (never speak while the owner is mid-sentence).
3. How to name the failing verifier in a fingerprint when a Hermes run fails without a test name (fallback: normalized error class + task).

## Rollback
`git checkout -- <files>`; delete the new modules/tests/agents; drop the additive columns is unnecessary (unused); `hermes profile delete friday-*` for created profiles; kanban board left in Hermes's own DB is inert.
