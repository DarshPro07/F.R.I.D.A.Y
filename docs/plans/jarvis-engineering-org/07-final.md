# 07 — Final (2026-09-04)

## Verdict: PARTIALLY_VERIFIED
Verified: every slice by unit tests (fail-before where practical), the golden engineering journey
on fakes end to end (steps 1–8 incl. restart recovery), the full Playwright suite (46 passed), an
independent Opus review whose four high findings are fixed and re-tested, and one LIVE Hermes
kanban task on a specialist profile. Not verified: the room-path digest with real audio, a real
provider cap from a live provider, the kanban gateway cap and idle sweep beyond unit tests.
Deterministic gate on the final tree: 3,526 passed, 2 owner-owned failures, 1 skipped (last section).

## Objective
Turn Friday → Hermes into a disciplined engineering organisation: progress the owner can hear,
quota-aware model routing, a real task contract, Hermes specialists on the native kanban, Claude
specialists with private memory, failure fingerprints, structured handoffs, controlled memory
promotion. Owner decisions: milestones + 3-min digest; auto-switch and say so; native profiles;
full plan now; one builder at a time, token-lean.

## Architecture before → after
Before: one Hermes profile, prompt-level roles, contract = goal + context, progress only on the
browser path and only on tool changes, capped providers retried blind, identical failures retried
until attempts ran out, memory written straight from turns.
After (ADR-001 unchanged shape): the same control plane with five additions inside existing seams —
contract fields + ten-section render; `progress_digest` on both voice paths + `/api/work`;
`provider_diagnostics.CAPPED` + `provider_cooldowns` + `execution_economics.candidates`;
`continuous.failure_fingerprint`/`_requeue_or_block`/`TASK_BLOCKED` with the hint fed to the next
attempt; `handoff` stored and spoken; `hermes_team` on native profiles/kanban beneath one objective
task; `memory_promotion` as the only door into canonical memory; four more Claude specialists.

## Files changed (today)
friday/: executors/claude_code.py, executors/hermes.py, development.py, hermes_bridge.py,
progress_digest.py (new), provider_diagnostics.py, provider_cooldowns.py (new),
execution_economics.py, tools/hermes_control.py, objectives.py, continuous.py, store.py,
handoff.py (new), hermes_team.py (new), memory_promotion.py (new), autolearn.py, roles.py,
(S10: hermes_bridge.py, progress_digest.py, tests/test_work_run_orphans.py; S11: voice_brain.py,
progress_digest.py, tests/test_voice_grounding.py; S12: build/Friday.cs, Friday.exe),
voice_brain.py, ui_server.py, policy.py (IRREVERSIBLE_KEEP_CONFIRM); agent_friday.py;
ui/index.html; .claude/agents/friday-{codebase-researcher,debugger,performance-reviewer,
final-reviewer}.md; .claude/rules (11 → 3 files); tests: test_task_contract, test_progress_digest,
test_quota_routing, test_failure_fingerprint, test_handoff, test_hermes_team (+fake_hermes_cli),
test_memory_promotion, test_promotion_wire, test_review_followups, test_golden_engineering_journey,
test_roles (+2), test_ui_server (+1), test_voice_brain_ui (+1), test_voice_pipeline (+1),
test_development, test_autonomy_and_selfcheck; e2e/work-panel.spec.ts; docs/plans/jarvis-engineering-org/*,
docs/adr/ADR-001, ADR-002, docs/MASTER_VALIDATION_PROMPT.md (Phase 17), docs/HARD_PROMPTS.md (J).

## Hermes profiles introduced
friday-research, friday-engineering, friday-qa, friday-review (cloned from `friday`; private
HERMES_HOME each; gateways started on demand, at most two, idle-stopped). Live: task `t_27a0e35d`
executed on friday-engineering.

## Memory ownership matrix
| Layer | Owner | Written by | Read by |
|---|---|---|---|
| Friday canonical (store, GBrain) | Friday | `memory_promotion.promote` only (autolearn and handoffs go through it) | every turn, every bundle (bounded block) |
| Hermes profile private | each profile | Hermes itself | that profile |
| Claude specialist private | each `.claude/agents` file (`memory: project`) | Claude Code | that agent |
| Task memory | objective engine | continuous/store (task detail merges) | next attempt, next process |
| Skill candidates | `data/skills_candidates/` | promotion of `procedure` kinds | owner / future skills |

## Routing algorithm
`roles.size_of`/`compile_team` → trivial/small: one Hermes delegate on `friday`; ≥ 2 roles: kanban
tasks per profile with dependencies, `--model/--provider` from `plan_delegation` (tier → candidates
minus cooled providers); Claude specialists named in the ROLE section for the worker; Friday's
`verify()` after every path; worker "done" is never verification.

## Loop engineering / fingerprints
fingerprint = sha1(kind, error class, message minus numbers/paths/ids, verifier, task). Same
fingerprint without new evidence → strategy change (replan → different_role → reduce), the hint
injected into the next attempt's arguments and rendered as a STRATEGY CHANGE constraint; after
MAX_STRATEGY_CHANGES → TASK_BLOCKED with the evidence; iteration budget caps attempts. Terminal
states now include BLOCKED. Task detail merges so the history survives success and restarts.

## Tests executed (exact, `.venv-verify`)
Per-slice suites in `06-verification.md` (115 / 86 / 61+1 e2e / 77 / 50 / 47 / 27 / 85 / 69 / 12);
review follow-ups 5; golden journey 1; census + voice + UI 66; action-chain/audit/autonomy 123;
Playwright 46. Baseline gate 3,506 passed / 10 failed → 8 fixed today; final gate 3,526 passed /
2 failed / 1 skipped, the 2 owner-owned (`test_upstream_lock`: the lock template directory was
removed by commit 99dd904).

## Failures found and repaired
Team path counted a worker's kanban "done" as verification; blocking sleep in an async execute;
capped candidate still chosen when all were cooled and requeued with zero delay; digest starvation;
strategy hint never consumed; cooldown file torn-read silent; unbounded spoken set; Hermes bundle
lacked the subagent line; task `detail` replaced instead of merged (erased fingerprints); the
DANGEROUS default had removed the yes from machine power/kill actions (kept via
IRREVERSIBLE_KEEP_CONFIRM); seven silent exception handlers now log. After the restart the first room
digest recited ~20 dead runs: no run owner (dead-process runs never closed), the progress ledger was
in-memory in one process, no cap on the digest — fixed as S10 (`owner` + `sweep_orphans`, persisted
`progress_json`, three-run cap; `tests/test_work_run_orphans.py`).
Then (17:12) "what did Hermes finish" was answered from chat context — fixed as S11 (deterministic ledger
answer, `tests/test_voice_grounding.py`); and the launcher lost every server/control-room log line — fixed
as S12 (`build/Friday.cs` file redirection, rebuilt `Friday.exe`).

## Remaining limitations
Handoff fields beyond summary/status are empty until the bridge records tool paths; the S2/S4a
"fail-before" evidence is import-level only; `hermes profile list` JSON shape unconfirmed live;
`--max-retries 0` invalid on this Hermes build; Hermes `fallback` chain empty (owner's picker);
Claude Agent Teams untouched (optional, off).

## Claude Agent Teams / Hermes Kanban support
Agent Teams: not enabled (capability-gated, off by default, ADR-001). Kanban: native in Hermes
0.20.6, used beneath one objective task, proven live once.

## Token cost of the build (for the owner)
Builders (Sonnet, project agents, capped): S1 148k, S3 275k, S3b 146k, S2 285k, S4a 119k, S4b 144k,
S6 62k, S5 190k, S7 148k, S8a 90k, S8b review 63k, S9 336k ≈ 2.0M; orchestrator overhead on top.
Every first turn now starts ~16k tokens lighter (rules 4.6k → 1.5k, roster 14.4k → 1.3k).

## Rollback
`git checkout -- <files>`; delete the new modules/tests/agents; `hermes profile delete friday-*`;
the kanban board in Hermes's own DB is inert; restore `.claude/rules-archive/*` if the compact rules
are unwanted; move `~/.claude/agents-archive/*` back to restore the roster.

## Deterministic gate (final tree)
4 chunks, 15:27–15:58: **3,526 passed / 2 failed / 1 skipped** (53 deselected by marker). The two
failures are the owner-owned `test_upstream_lock` pair (lock template removed by commit 99dd904);
every failure the baseline exposed is fixed. Playwright 46 passed. Detail in `06-verification.md`.
