# 06 — Verification

Independent challenge is split: `friday-final-reviewer` (Opus, read-only) reviews the code;
the orchestrator (Fable), who authored none of the slices except the one-line promotion wire,
runs the gate, Playwright and the live checks; S8a's golden journey exercises the chain on fakes.

## Evidence per slice (from the builders' reports, re-run where noted)
| Slice | Tests fail-before / pass-after | Suite counts |
|---|---|---|
| S1 task contract | 3 new, proven failing via `git stash` | 115 passed (task_contract, development, hermes_bridge, hermes_engine, executor) |
| S3 progress (room path) | 7 new | 86 passed (progress_digest, voice_pipeline + neighbours) |
| S3b progress (browser) | 2 new + 1 e2e | 61 unit + 1 Playwright passed |
| S2 quota routing | 12 new (pre-fix lines NOT captured — reviewer to judge teeth) | 77 passed |
| S4a fingerprints | 5 new | 50 passed |
| S4b handoff + subagent wire | 7 new | 47 passed |
| S6 Claude specialists | 2 new; lint A/ship ×4 | 27 passed |
| S5 Hermes team | 9 new (fake CLI) + LIVE kanban task `t_27a0e35d` on `friday-engineering` | 85 passed |
| S7 memory promotion | 6 new | 69 passed |
| S7w promotion wire | 1 new (re-run by Fable) | 12 passed with writeback + handoff |

## Live evidence
- Hermes 0.20.6 kanban: profiles `friday-research/engineering/qa/review` created (clone of
  `friday`), `kanban init`, task `t_27a0e35d` assigned to `friday-engineering`, its gateway
  started on demand, polled to completion, stopped; the `friday` gateway untouched (S5 report).

## Independent review
(written by `friday-final-reviewer`)

## Deterministic gate
Baseline (tree before S9, started 14:20): chunk 1 → 7 failed / 1026 passed — all seven in
`test_action_chain::test_the_irreversible_ones_still_need_a_yes[…]` and
`test_audit_planner::test_a_power_action_is_never_run_to_raise_the_pass_count`: the 2026-09-03
DANGEROUS default had turned power/kill CONFIRMs into AUTO; the suites run that evening did not
include these files. Fixed by `policy.IRREVERSIBLE_KEEP_CONFIRM` (shutdown, restart, sleep,
hibernate, lock, forced kill keep one question even in DANGEROUS); action_chain + audit_planner +
autonomy suites → 123 passed. Chunk 2 → 725 passed. Chunks 3–4 and the final full run on the
post-S9 tree: below.

## Review disposition (S9 by the objective engineer, S9b by Fable)
| Finding | Done |
|---|---|
| team path fabricated verification (high) | fixed: `verification=None`, the same `verify()` path as single-worker runs |
| blocking sleep in async execute (high) | fixed: `asyncio.to_thread` |
| all-cooled candidate still used, CAPPED delay 0 (high) | fixed: `wait_until` + route_reason; continuous delays to the earliest reset |
| digest starvation (high) | fixed: `last_digest_at` only when a digest fired |
| strategy_hint had no consumer (med) | fixed by Fable: the hint is injected into the requeued task's arguments (`continuous`), carried into `DevelopmentRun.strategy_hint` and rendered as a STRATEGY CHANGE constraint; `test_strategy_hint_reaches_the_worker_arguments` |
| cooldown file non-atomic + silent (med) | fixed: tmp + `os.replace`, corrupt file logged; 2 tests |
| unbounded spoken set (low) | bounded at 500 |
| handoff fields empty (low) | documented; `promote_handoff` is wired (`test_promotion_wire.py`) and exercised by the journey's candidates |
| Hermes bundle lacked the subagent line (S8a) | fixed: ROLE section names `roles.claude_agent_for(role)`; journey step 3 asserts it |
| S2/S4a weak fail-before evidence (note) | accepted as weak; behaviour is covered by the journey and by the follow-up tests |
Journey defect found by Fable: `store.update_objective_task` replaced `detail` JSON, erasing the
fingerprint history on the success write (and across restarts); it now merges. Restart step of
the journey corrected to stop the first executor (a process death takes its loop with it).
`tests/test_golden_engineering_journey.py` → 1 passed (steps 1–8 incl. restart recovery with
the fingerprint history intact). Follow-ups suite `tests/test_review_followups.py` → 5 passed.

## Deterministic gate (4 chunks, final tree)
Run 15:27–15:58 on the post-S9b tree (`gate-final/gate_summary.txt`): chunk 1 → 1086 passed
(15:28); chunk 2 → 691 passed, 36 deselected (7:03); chunk 3 → 939 passed, 1 skipped, 11 deselected
(3:59); chunk 4 → 810 passed, 2 failed, 6 deselected (3:15).
**Total 3,526 passed / 2 failed / 1 skipped.** Both failures are
`tests/test_upstream_lock.py::test_the_new_set_is_exactly_the_requested_minus_the_build_pack` and
`::test_no_upstream_was_staged_without_being_requested` — owner-owned: commit 99dd904 removed
`Friday Stark Demo Main/06_schemas/UPSTREAM_LOCK_TEMPLATE.json`, which both tests read. Not masked.
Every failure the baseline exposed (7 policy, 1 census) is gone.

## Live defect after the restart (16:08) and its fix (S10)
First digest in the room after the 16:02 restart: `did 0 tools, last: Hermes is reading the task - 0s in.,
on claude-opus-5 (default route), next: wrapping up;` repeated ~20 times in one utterance. Root causes
(STATICALLY_CONFIRMED, `hermes_bridge.py`): (1) `hermes_work_runs` had no owner, so a run whose process died
stayed WORKING forever and `active()` narrated it on every poll; (2) the progress ledger lived only in the
memory of the process that ran the delegation (server.py), so the room and control room — other processes —
read 0 tools / 0s for every run, live ones included; (3) `compose()` joined every run into one digest.
Fix: `owner` column (`pid:create_time`, psutil) written by `create()`; `WorkRunLog.sweep_orphans()` closes
dead-owner and ownerless runs as FAILED/`failure_kind=LOST` by direct SQL (last_event_at untouched so an old
ghost is not a fresh milestone; no on_terminal — nothing to hand off), called from `progress_digest.gather()`
and `HermesSupervisor.start()`; `progress_json` persisted on every event and read by `progress()` when the
in-memory ledger is empty; `MAX_DIGEST_RUNS = 3` + "and N more in flight".
Evidence: `tests/test_work_run_orphans.py` 4 failed before the change (assertion-level: 6 == 3, ghost present,
no owner column) → 4 passed after, plus a writer-side test added after the fix (5 passed); progress_digest, hermes_bridge, voice_pipeline, ui_server, hermes_engine,
golden journey, review follow-ups, handoff, promotion wire → 99 passed. Live: launcher restart after the fix
(see 00-status S10).

## S11 grounding + S12 launcher logs (17:26 owner request)
Defect (OBSERVED 17:12): asked "what did Hermes just finish?", Friday answered "still working" from chat
context 40 minutes after the ledger showed nothing active; at 16:31 she had echoed the pasted "Expected"
text without calling any tool (no Hermes session, no gateway child, no run). Fix: a deterministic branch in
`_try_command` (the sibling of the existing "what's running" branch) reads `WorkRunLog.recent()` and speaks
`progress_digest.outcome_line()` — status (LOST distinguished), task, handoff summary or result, age, model +
route reason; gate/test origins excluded; no record → "I have no finished Hermes job on record". Room path
(LiveKit agent) unchanged: it has its own LLM loop with hermes_status as a tool — noted, not fixed.
Evidence: `tests/test_voice_grounding.py` 6 failed before (the running-question test already passed through
the existing branch) → 7 passed; neighbours 115 passed.
Launcher (STATICALLY_CONFIRMED `build/Friday.cs` StartHidden): stdout/stderr were pipes nobody read — the
launcher exits after starting the three, so a child blocks on a full 4 KB pipe while it lives and loses
everything after. Now `cmd.exe /c ... >> logs/<name>.log 2>&1` with a `=== start` stamp; KillByCommandLine and
VoiceRunning unchanged (the python child keeps its command line). Rebuilt with csc (.NET 4.0.30319); strings
verified in the binary; live 17:55: `mcp_boot.log`, `ui_boot.log`, `agent_boot.log` all receive output.

## Playwright (final tree)
`node node_modules/@playwright/test/cli.js test --project=chromium --reporter=list` →
**46 passed (7.2m), exit 0** (`e2e-run-eng-org.log`; 45 existing + `work-panel.spec.ts`).

## Not verified in this pass
Progress digest and quota switch on the LiveKit room with real audio (needs the owner's
session); a real capped-provider event from a live provider (simulated through the fake
gateway); kanban gateway cap and idle sweep beyond unit tests.

## Independent review (friday-final-reviewer, 2026-09-04)

Ran `.venv-verify/Scripts/python.exe -m pytest tests/test_task_contract.py tests/test_quota_routing.py tests/test_failure_fingerprint.py tests/test_handoff.py tests/test_memory_promotion.py tests/test_progress_digest.py tests/test_hermes_team.py tests/test_roles.py -q --tb=line` -> 74 passed.

| file:line | sev | defect | fix |
|---|---|---|---|
| friday/development.py:304-307 | high | Q1: team path fabricates `Verification(evidence="all N profile tasks reported status=done")` - the worker's own kanban "done" IS the verification; `verify()`/`evaluation.Verifier` never runs | `verification=None` on the team path; verification only from `verify()` |
| friday/development.py:293 | high | `time.sleep(5)` poll loop (up to 1800s) inside `async def execute` blocks the voice event loop | `await asyncio.to_thread(self._execute_via_team, ...)` |
| friday/execution_economics.py:422-425 + friday/continuous.py:648 | high | Q2: every-candidate-cooled returns the capped first candidate anyway, and CAPPED requeues with `delay=0.0` -> capped provider hammered inside its own window until attempts exhaust | delay = seconds to earliest `until`; treat CAPPED like PROVIDER_DOWN backoff |
| friday/progress_digest.py:78-81 + agent_friday.py:2313 | high | Q5: caller stores `next_at` (a due time) into `last_digest_at` (a past time); each non-firing poll pushes it +cadence -> digest starves after the first hold | update `last_digest_at` only when `result.digest` fired |
| friday/continuous.py:~937 | med | Q3: `strategy_hint` persisted but has no consumer (grep: tests only) -> the identical task is retried blind; strategy change is bookkeeping. No loop past MAX_STRATEGY_CHANGES (BLOCKED at :946) | feed `detail["strategy_hint"]` into task arguments/bundle on requeue |
| friday/provider_cooldowns.py:30-36 | med | Q7: non-atomic `write_text` + bare `except` in `_load` -> a torn file silently reads `{}`, all cooldowns forgotten, capped provider retried | tmp file + `os.replace`; log on parse failure |
| agent_friday.py:2298,2307 | low | Q5: `state["spoken"]` unbounded per session; milestone text embeds a 200-char result slice, so a changed summary for the same run re-speaks | dedupe on `work_run_id`, cap the set |
| friday/handoff.py:from_work_run + hermes_bridge on_terminal | low | Q4: no invented fields (files/tests/memory stay empty, `_sensitive` guard applied) - but `memory_candidates` is always empty, so `promote_handoff` is a live no-op | feed candidates or note the gate is unexercised in production |
| tests/test_roles.py:179 | low | asserts on agent-file frontmatter text rather than behaviour | acceptable for config, but it proves file shape only |
| S2/S4a pre-fix claim | note | tests import `friday.provider_cooldowns`, `PD.CAPPED`, `failure_fingerprint`, `TASK_BLOCKED` - all new symbols, so they would fail pre-change by ImportError/AttributeError, not by asserting the missing behaviour | keep, but they are weak fail-before evidence |

Q1 second half (kanban/Hermes creating a Friday objective): none found - `hermes_team.submit` only links children under `objective_task_id` (hermes_team.py:201). Q6 swallowed exceptions turning failure into success: none found. Q7 `on_terminal`: none found (atomic `UPDATE ... WHERE memory_written=0`, claim released on write failure).

Verdict: do not ship until rows 1-4 are fixed.

## Independent review (S10-S12, friday-final-reviewer)

Ran `.venv-verify/Scripts/python.exe -m pytest tests/test_work_run_orphans.py tests/test_voice_grounding.py -q --tb=line -p no:cacheprovider` -> **12 passed in 5.34s**. No TODO/skip/stub in the four files.

| file:line | sev | defect | fix |
|---|---|---|---|
| progress_digest.py:132 | med | `MAX_DIGEST_RUNS` caps only the digest branch; `milestones` is uncapped. A crash-restart inside `terminal_window_s` re-adds every swept run (`gather` recent limit 12) and agent_friday.py:2304 speaks all 12 back-to-back. The "same run 20x" repeat IS fixed (per-line `state["spoken"]`); the burst is not. | cap+count milestones like lines |
| voice_brain.py:367 | med | `_run_work` falls back to `"; ".join(result.milestones)` - same uncapped join, spoken on demand | slice to `pd.MAX_DIGEST_RUNS` |
| hermes_bridge.py:782 | low | `Handoff.from_work_run(...)` sits outside the try; a raise breaks every terminal transition | move inside the guarded block |
| progress_digest.py:22 | low | second `TERMINAL` literal beside `hermes_bridge.TERMINAL:111` - values match today, drifts silently | import it |
| voice_brain.py:857 | low | `\bwhy that model\b` hijacks any ML-model question | require "hermes" in `low` |

Answers. Wrong sweep: no. `owner_alive` fails **open** (bare `except -> True`), pid-reuse is caught by `create_time` +/-2s, `ctime==0` returns alive, psutil is a hard dep (pyproject.toml:19, 7.2.2 installed). Only `owner=''` legacy rows sweep - one-time, correct. `started_at` is REAL (hermes_bridge.py:421), so the `progress()` fallback cannot TypeError. cmd quoting: sound - cmd.exe strips only the outermost quote pair, `Python()`/log/`--log` are each quoted, `Root` rides `WorkingDirectory`; `KillByCommandLine`/`VoiceRunning` filter on ProcessName "python" so the cmd.exe wrapper is skipped and the child's cmdline still carries the script name. No existing-caller regression (`progress=` keyword-only, `update()` allowlist extended).

Verdict: **SHIP** - rows 1-2 as an immediate follow-up.

### Disposition of the S10–S12 review (Fable, 18:08)
Rows 1–2 (uncapped milestones in `compose()` and in `_run_work`'s fallback): fixed in `compose()` — three
milestones plus "and N more finished", which `_run_work` inherits; `test_milestones_are_capped_like_the_digest`.
Row 3 (`Handoff.from_work_run` outside the guard): wrapped, failure logged, memory write proceeds with an
empty handoff. Row 4: `progress_digest` now imports `TERMINAL` from `hermes_bridge`. Row 5: the grounding
branch requires "hermes" in the utterance; `test_a_model_question_without_hermes_is_left_to_the_model`.
Suites after the follow-ups: progress_digest, orphans, grounding, voice_brain_ui, voice_pipeline,
hermes_bridge, handoff, promotion wire → 108 passed. Stack restarted through the launcher at 18:09.
