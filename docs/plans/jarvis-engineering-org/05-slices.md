# 05 — Slices

Thin end-to-end slices, each verified before the next. Builders append a report under
"## Reports" (≤ 250 words: file:line changes, exact test commands and counts before/after
with the failing assertion lines, what could not be done and why).

| # | Slice | Tracer bullet | Verification |
|---|---|---|---|
| S1 | Task contract to Hermes | one development run's bundle rendered with ACCEPTANCE CRITERIA | new tests fail-before/pass-after; test_development, test_hermes_bridge, test_executor* green |
| S3 | Progress on both paths + Work panel | a fake work run's tool events → spoken milestone + digest on room and page | test_progress_digest; test_voice_pipeline/test_ui_server; e2e/work-panel.spec.ts |
| S2 | Quota-aware routing | a fake 429 "limit resets at …" → cooldown → next candidate → spoken switch | test_quota_routing; test_execution_economics; test_executor_continuity |
| S4 | Fingerprints + handoffs | same failure twice → strategy change; third → BLOCKED; handoff JSON on the work run | test_failure_fingerprint, test_handoff; test_continuous (existing) |
| S6 | Claude specialists | 4 agents linted; role→agent map; worker prompt names the subagent | test_roles; `agents-team` lint grades |
| S5 | Hermes team | one kanban task executed by `friday-engineering` profile (live), fallback proven with a fake CLI | test_hermes_team; live check recorded |
| S7 | Memory promotion | a handoff candidate with evidence lands; a guess and a secret are refused | test_memory_promotion; test_memory_provenance |
| S8 | Golden journey + gate | the whole chain incl. injected failure and restart | test_golden_engineering_journey; 4-chunk gate; Playwright; Opus review |

## Reports
(appended by builders)

### S1

Fields added: `friday/executors/claude_code.py` `TaskBundle` (L93-107) gained `allowed_paths, verification, role, iteration_budget, known_facts, assumptions` (all default-empty). `friday/development.py` `DevelopmentRun.execute` (L191-235) takes `verifier`/`iteration_budget`, derives `acceptance`/`verification` from the passed `evaluation.Verifier` (or one `[derived from goal] ...` line when none given), `role` from `team.roles` titles, `known_facts=context_for()`, `assumptions=self._assumptions()`. `friday/executors/hermes.py` `to_bridge_bundle` (L47-59) forwards all six fields. `friday/hermes_bridge.py` `TaskBundle` (L231-260) gained the same fields + `REPORTING_CONTRACT` constant; `render()` (L324-360) now emits GOAL, ACCEPTANCE CRITERIA, KNOWN FACTS, ASSUMPTIONS, CONSTRAINTS, ALLOWED SCOPE, PROHIBITED ACTIONS (reused `disallowed`), ROLE / RESPONSIBILITY, VERIFICATION, REPORTING CONTRACT first, then the untouched USER OUTCOME/memory/code-refs/skill-hints/tool-scope/budget/policy sections — `with_memory()` and its token cap unchanged.

Tests: `tests/test_task_contract.py` (new, 3 tests). Confirmed pre-fix failure via `git stash` (tracked changes only, test file untracked) + rerun: `assert () == ('shrink() works',)`, `TypeError: unexpected keyword argument 'assumptions'`, `AssertionError: 'REPORTING CONTRACT' in ...` — 3 failed. After `git stash pop`: `tests/test_task_contract.py tests/test_development.py tests/test_hermes_bridge.py tests/test_hermes_engine.py tests/test_executor.py` → 115 passed (0 before, since the file didn't exist; 112 in the other 4 files, unaffected).

Not done: iteration_budget has no dedicated render section (plan lists 10 sections, none named for it) — carried as a field only, for a future slice (S4 loop) to consume.

Claude-Session: https://claude.ai/code/session_01EMo4XB3dQiFjM9vYQLQGfq

### S5

New `friday/hermes_team.py` (232 lines): `PROFILES`, `_hermes()` CLI wrapper (never raises), `profiles()`, `ensure_profile()`, `plan_team()` (roles.compile_team → () for TRIVIAL/SMALL or <2 roles), `submit()`/`poll()` over `kanban create/link/show --json`, cycle guard (`CYCLE_MARKER`), `gateway_for()` with `MAX_LIVE_GATEWAYS=2` LRU stop + idle sweep. `friday/development.py` L38 import, `DevelopmentRun.execute` (~L191) now calls `_execute_via_team` (new, ~55 lines) when `plan_team` returns ≥2 profiles, else unchanged `delegate()`-style path; falls back on any kanban error.

Tests: `tests/test_hermes_team.py` (new, 9 tests, fake CLI via `tests/fake_hermes_cli.py` + `HERMES_EXE_PREFIX`) all pass. Full run `test_hermes_team.py test_development.py test_roles.py test_hermes_bridge.py`: 85 passed (0 before hermes_team.py existed).

Live check (real Hermes, `D:\hermes`): `profile create friday-research/-qa/-review --clone-from friday` → created; `friday-engineering` already existed from a prior attempt. `kanban init` → ok, listed profiles. `kanban create --assignee friday-engineering --max-runtime 300 --max-retries 1` (CLI rejects `--max-retries 0`, spec assumption wrong) `--idempotency-key S5-selfcheck-1 --json` → id `t_27a0e35d`, status `ready`. Started `friday-engineering` gateway (`HERMES_HOME=D:\hermes\profiles\friday-engineering`). Polled `kanban show --json` every 20s: running → **done** in ~2 polls (~40-100s), outcome `completed`, summary "Wrote the current date (2026-09-04)…verified file reads back exactly one line", `changed_files` matches. Verified via `cat data/artifacts/kanban_selfcheck.txt` → `2026-09-04`. `hermes gateway stop` (profile-scoped) → stopped; `friday` gateway never touched.

Not done: `--max-retries 0` is invalid on this Hermes build (used 1); `profile list` gave empty stdout in one capture (parsed via `kanban init`'s profile listing instead, not exercised live); idle-gateway sweep and LRU-cap untested against a real second/third live gateway (unit-tested only, RAM risk on this host).

### S6

Built 4 project agents in `.claude/agents/`: `friday-codebase-researcher.md`
(read-only, maxTurns 15), `friday-debugger.md` (Edit scoped to `tests/`/scratch
only, maxTurns 20), `friday-performance-reviewer.md` (read-only, maxTurns 15),
`friday-final-reviewer.md` (read-only, opus, maxTurns 15, states it never
self-approves). Each has the 5-line Budget block, reads `AGENTS.md`+memory
first, the "never build a second objective engine/registry/memory/Hermes
bridge" line, and the 13-field report contract.

`friday/roles.py:159-183` adds `CLAUDE_AGENT_FOR_ROLE` (11 role ids →
`.claude/agents` names) and `claude_agent_for(role)` with a safe default
(`friday-tools-engineer`). Mapping: architect→tech-lead, security→security-
engineer, tests→qa-engineer, reviewer→final-reviewer, voice→voice-engineer,
data→fabric-engineer; minimal/implementer/tooling/prompt/ux (no dedicated
domain agent) → tools-engineer as the generic default.

`tests/test_roles.py` +34 lines: `test_role_to_agent_map_covers_every_role`
(every CATALOGUE role resolves to an existing agent file) and
`test_reviewer_agents_have_no_write_tools` (parses YAML frontmatter of the 4
genuinely review-only agents — excludes `friday-security-engineer`, which
intentionally edits to apply fixes).

Lint: all 4 new agents scored via
`third_party/upstream/agents-team/plugins/agents-team/lib/eval/lint.py` —
researcher 100/A/ship, debugger 100/A/ship, performance-reviewer 100/A/ship,
final-reviewer 98/A/ship (one warning: opus-on-readonly, accepted — role
needs multi-step verification reasoning). Roster table in
`docs/plans/jarvis-agentic-team/team.md` appended with all 4.

Test: `.venv-verify/Scripts/python.exe -m pytest tests/test_roles.py -q
--tb=line -p no:cacheprovider` → 27 passed (was 24 before this slice).

### S3

Done: `friday/progress_digest.py` (new, pure `compose(runs, objectives=None, *, now, last_digest_at, cadence=180) -> Digest(milestones, digest, next_at)`, dedupes by `(work_run_id, seq)` per call, L1-95; `gather(sup)` merges `WorkRunLog.active()` + recently-terminal `recent()` rows with `HermesSupervisor.progress()` for callers). `agent_friday.py` `speak_progress_digests()` (L2213-2246) + wired into the existing delivery loop (L2360-2378): gates on `session.user_state != "speaking"` and no `current_speech`, speaks milestones once via `deliver_message`, then the cadence digest; first poll seeds `last_digest_at=now` so a fresh room never opens with a status dump.

Tests: `tests/test_progress_digest.py` (6 cases: dedupe, cadence hold/fire, milestone-vs-digest, garbage input, gather merge + terminal-window inclusion) and `tests/test_voice_pipeline.py::test_room_path_speaks_a_milestone_and_a_timed_digest` (fake run → no digest before cadence, digest with model+reason after). Ran: `tests/test_progress_digest.py tests/test_ui_server.py tests/test_voice_brain_ui.py tests/test_voice_pipeline.py` → 86 passed.

Not done (ran out of turns): (3) `friday/ui_server.py GET /api/work` + `ui/index.html` Work section + digest lines appended to the transcript; (4) `friday/voice_brain.py` "what's running" — no `work/status` op added to `_surface`/`_run_capability`, no `_try_command` guard, no tool-text clause; (5) `e2e/work-panel.spec.ts` — file does not exist, Playwright not run. These are the remaining S3 deliverables per 04-program-design.md; hand back for a follow-up pass.

### S3b

Done: `friday/ui_server.py` — `_probe_work()`/`api_work` (L1077-1101ish, `_WORK_CACHE`/`_WORK_TTL=5.0` mirroring `_HELPERS_CACHE`), builds `{runs:[{id,model,status,latest,route_reason}], objectives:[], digest}` from `progress_digest.gather`+`compose`; route registered at `/api/work`. `ui/index.html` — `pollWork()` beside `pollDeck`/`pollGates` (5s interval, wired into `startAll`/`timers`/`setView("room")`), Work section in `renderRoom()` (`#work-list`), digest text appended to the transcript via `convAdd` only when it changes (`_WORK_LAST_DIGEST`). `friday/voice_brain.py` — `out["work"]={"status"}` in `_surface`, `_run_work()` (reuses `pd.gather`/`pd.compose`, "nothing running right now" fallback), `_run_capability` branch, `_try_command` regex for "what's running / how's the work going / status of the work" (≤12 words), tool-text clause before "Families --".

Tests: appended `test_api_work_returns_runs_objectives_and_digest_shape` to `tests/test_ui_server.py` and 3 cases (`work` family present, `work/status` "nothing running", trigger phrase → `work.status`) to `tests/test_voice_brain_ui.py`. New `e2e/work-panel.spec.ts` (mocks `/api/work`, asserts `#work-list` shows id + latest line).

Ran: `pytest tests/test_ui_server.py tests/test_voice_brain_ui.py` → 61 passed. Playwright `e2e/work-panel.spec.ts` → 1 passed.

Not done: the e2e spec does not additionally assert the real `/api/work` shape via `page.request.get` against the live server (only the mocked route is checked) — coordinator's follow-up ask; add if wanted.

### S2

Done: `friday/provider_diagnostics.py` — `CAPPED` kind, `_CAP_MARKERS`
(rate limit/quota/usage limit/limit reached/weekly/daily/5-hour/too many
requests) checked before the generic 429/5xx TRANSIENT branch, `reset_at`
parsed via `_cap_reset_at()` (L~63-116, explicit "resets at"/"retry after"
first, else 5-hour→+1h/daily→next 00:00/weekly→+24h/unknown→+30min),
`worth_retrying` now includes CAPPED. New `friday/provider_cooldowns.py`
(JSON at `data/provider_cooldowns.json`, `mark/active/clear`, one
`threading.Lock`). `friday/execution_economics.py`: `candidates(tier)` (tier
model under profile default provider → `fallback_providers` config entries
→ bare profile default), `plan_delegation` now skips cooled candidates,
returns `provider`/`switched_from`, reason `"<provider> capped until
HH:MM → <model>"` or `"waiting for <provider> until HH:MM"` when all cooled.
`friday/tools/hermes_control.py` passes `provider=plan["provider"]`.
`friday/hermes_bridge.py`: `_QUOTA_COLUMNS` (`failure_kind`, additive
migration), `_capped_update()` diagnoses a gateway error text, marks the
cooldown for the run's effective provider/model, sets `failure_kind`+
`route_reason`; `progress()` exposes `route_reason`/`switched_from`
(parsed from the reason text — no new column for it, ponytail). `friday/
objectives.py`: `FAILURE_CAPPED`, added to `FAILURE_KINDS`/`RETRYABLE_KINDS`.
`friday/continuous.py`: `FailureClassifier.classify` returns CAPPED for a
capped diagnosis; retry delay stays 0.0 (existing `PROVIDER_DOWN`-only
backoff branch), so requeue is immediate without new continuous.py logic.

Tests: `tests/test_quota_routing.py` (12 cases) +
`tests/fake_hermes_gateway.py` `FAKE_HERMES_CAPPED=1`. Ran:
`tests/test_quota_routing.py tests/test_execution_economics.py
tests/test_model_selection.py tests/test_executor_continuity.py
tests/test_hermes_bridge.py` → 77 passed.

Not done: no failing-lines-before-fix capture (edited straight through
given the turn budget); `fallback_providers:` config *format* was inferred
(dict with `provider`/`model` keys, or bare provider string), not confirmed
against a real profile config.yaml on this host — revisit if the actual
Hermes profile uses a different shape.

### S4a

`friday/continuous.py`: `failure_fingerprint(kind, error, verifier, task_id)`
(L~110-142, sha1 of kind+error-class+normalized message with numbers/paths/
hex-ids stripped) called from a new `_requeue_or_block` (L~908-978, shared by
both the exception path L648 and the refusal path L686) that persists
`last_fingerprint`, `fingerprint_history`, `hypothesis`, `strategy_changes`,
`strategy_hint` on the task's new `detail` JSON column; same fingerprint +
same hypothesis → `strategy_changes+=1`, hint cycles replan→different_role→
reduce; past `MAX_STRATEGY_CHANGES=3` → `TASK_BLOCKED` + `EVENT_TASK_BLOCKED`.
`_fail_task` gained `detail=` kwarg (L~854). `_max_attempts_for` (L~868)
caps attempts by `task.arguments["iteration_budget"]` when set. `_finish`
(L~1088) counts `blocked`, sets run PARTIAL/FAILED with
`summary["outcome"]="blocked:<fingerprint>"` when only BLOCKED remain.
`friday/objectives.py`: added `TASK_BLOCKED`/`TaskStatus.BLOCKED` to
`TASK_STATUSES`/`TASK_TERMINAL`, `EVENT_TASK_BLOCKED`. `friday/store.py`:
additive `objective_tasks.detail` column (`_ADDED_COLUMNS`), `detail` now an
allowed `update_objective_task` field (JSON-encoded), decoded back in both
`objective_task` and `objective_tasks`.

Tests: `tests/test_failure_fingerprint.py`, 5 tests, all pass. Full run:
`50 passed` across test_failure_fingerprint/test_executor_continuity/
test_objective_continuity/test_quota_routing.

Not done: no failing-lines-before-fix capture kept in this report (edited
straight through under turn budget, verified interactively instead). Found
mid-task that `objective_tasks` has no persisted `max_attempts` column
(pre-existing gap, not touched — tests set `executor.max_attempts` directly
instead of via the compiled task). Refusal-path CONNECTIVITY diagnose is now
called once instead of twice (behavior change, not covered by a dedicated
test). `speak()` narration for BLOCKED/outcome text explicitly skipped per
orchestrator instruction.

### S4b

New `friday/handoff.py`: `Handoff` dataclass (17 fields) with
`to_json`/`from_json` and `from_work_run(record, progress)`, built only from
what the work-run row + `HermesSupervisor._progress[id]` actually contain —
`files_read`/`files_changed`/`tests_run` stay `()` since progress only keeps
the last tool line, not a path history (documented `ponytail:` comment,
upgrade path noted). `summary` reuses `render_completion` + `brain._sensitive`
(same guard `_write_outcome` uses); a secret-shaped result yields `summary=""`
instead of raising.

`hermes_bridge.py` (L450-453 `_HANDOFF_COLUMN` additive migration,
L613-730 `WorkRunLog.update`/`on_terminal` now thread an optional `progress`
dict and store `handoff` JSON in the same claiming `UPDATE ... WHERE
memory_written=0` that guards idempotency, L547-579 `render_completion`
appends `(route_reason)` and `Next: <next_action>` when present, L1292
`_handle_event` passes `progress=prog`, L1516-1520 `progress()` exposes
`"handoff"`). `TaskBundle.render` appends `ITERATION BUDGET: N attempts` to
CONSTRAINTS when `iteration_budget > 0`.

`executors/claude_code.py` `TaskBundle.prompt()`: role set → "Use the
`<claude_agent_for(role)>` subagent for this work."; `iteration_budget > 0`
→ same ITERATION BUDGET constraint line.

Tests: `tests/test_handoff.py` (5 tests) + 2 appended to
`test_task_contract.py`. Full run across the 5 named files: 47 passed.

Not done: no history/list of files touched across multiple tool calls per
run (only the single last-tool line exists today — a real trail needs a new
list on `_progress`, not built here). `decisions`/`assumptions`/
`failed_attempts`/`residual_risks`/`blockers`/`memory_candidates`/
`skill_candidates` are always empty — no source data exists on the work-run
row or progress dict for them yet; left honest rather than invented, per the
task's own ground rule. `continuous.py`/`objectives.py` untouched (not
owned).

### S7
Built `friday/memory_promotion.py`: `Candidate`/`Decision` dataclasses and `promote(candidate, *, store=None)`. Rejects secret-shaped text via `brain._sensitive` (memory_promotion.py:86), empty evidence, confidence < 0.6, and `kind="outcome"` as one-off task state (`ONE_OFF_KINDS`, line 27). Dedupe/contradiction runs on `store.recall()` by a heuristic `_subject()` key (line 66): exact-value duplicates rejected, stronger-confidence contradictions supersede via `store.remember(..., supersede=True)` + `store.add_contradiction(resolution="new_wins")`, weaker ones are rejected but both rows and the contradiction stay (`store.contradictions()`), never a silent overwrite. `procedure` kind routes to `data/skills_candidates/*.md` instead of memory. `decision` kind uses `store.record_decision()`.

`promote_handoff(handoff, *, store=None)` (memory_promotion.py:186) feeds `handoff.memory_candidates`/`skill_candidates` through `promote()`.

`friday/autolearn.py` `AutoLearner._learn` now calls `self._gate(stored, user_text)` (autolearn.py:~157-179): builds a `Candidate` per stored profile fact and calls `promote()` as a second check over what `profile_learn_from_turn` already wrote — accepted behaviour (`self.learned`, `_refresh()`) is untouched; rejections are `logger.info`'d. No second write path (ADR-001): the real write stays in `friday/profile.py`, which this repo does not own.

Tests: `tests/test_memory_promotion.py`, 6 tests, all new, all failed before the module existed (ImportError). Full run: `.venv-verify/Scripts/python.exe -m pytest tests/test_memory_promotion.py tests/test_memory_provenance.py tests/test_autolearn.py tests/test_conversation_memory.py -q --tb=line -p no:cacheprovider` → 69 passed.

Not done: `memory_stack.py` was not touched (read-only per the STATICALLY_CONFIRMED note; no reading-provenance change was requested). No hook was added at the point `Handoff` objects are actually constructed in production (`hermes_bridge.py`, owned by another builder) — `promote_handoff()` exists and is tested but is not yet called from a live completion path; that wiring is a one-line follow-up for whoever owns that call site.

### S9

1. DONE — `friday/development.py:262-311` `_execute_via_team` no longer fabricates `Verification`; returns `status="partial", verification=None` (ActionResult forbids "succeeded" without Verification, so "partial" is the correct terminal state, not "succeeded"). Caller still runs `verify()`. Test: `tests/test_development.py::test_team_done_on_the_kanban_is_not_verification`.
2. DONE — `development.py:191,244-245` `execute()` runs `_execute_via_team` via `await asyncio.to_thread(...)`; the internal `time.sleep(5)` poll now runs off-thread. Test: `test_team_poll_does_not_block_the_event_loop`.
3. DONE — `friday/execution_economics.py:372-443` `plan_delegation`: all-cooled returns `model="", provider=""`, `wait_until=<earliest>`, no capped candidate chosen. `friday/continuous.py:627-655` CAPPED now diagnoses `reset_at` and delays to it (never 0 inside the window). Tests: `tests/test_quota_routing.py::test_plan_delegation_all_cooled_returns_no_capped_candidate`, `::test_capped_requeue_delay_reaches_the_reset`.
4. DONE — `agent_friday.py:~2313` only writes `state["last_digest_at"] = result.next_at` when `result.digest` fired. Test: `tests/test_progress_digest.py::test_caller_must_not_store_next_at_when_no_digest_fired`.
5, 6, 7, 8: NOT DONE — ran out of turns before reaching these.
9. IN PROGRESS, not passing. Fixed 3 real test defects on the way (stale API expectations, not source bugs): `.status.lower() in ("ok","succeeded","")` → real vocabulary is COMPLETE/PARTIAL/FAILED; `TaskStatus.COMPLETED` doesn't exist → `TaskStatus.SUCCEEDED`; `Decision.action` doesn't exist → `Decision.target`. Current blocker (step 8, restart recovery): after `store.close()`, executor #1's background `_driver_loop` task (started in `ContinuousTaskExecutor.__init__`, continuous.py:332-335) is still alive and polls `store.objective_runs()` on the closed connection (continuous.py:391-403), throwing `sqlite3.ProgrammingError`; `executor2.start()` then returns `resumed=False`. Root cause not yet isolated: either the first executor's driver loop needs `.stop()` before `store.close()` in the test, or `start()`/driver-loop lifecycle needs to not outlive one `start()` call. Not a fix I made — leaving for the orchestrator per instruction.

Suite: `.venv-verify/Scripts/python.exe -m pytest tests/test_golden_engineering_journey.py tests/test_development.py tests/test_quota_routing.py tests/test_failure_fingerprint.py tests/test_progress_digest.py tests/test_executor_continuity.py tests/test_hermes_team.py tests/test_task_contract.py -q --tb=line -p no:cacheprovider` → 99 passed, 1 failed (golden journey, step 8).
