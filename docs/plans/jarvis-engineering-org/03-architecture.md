# 03 — Architecture

## Governing rule (ADR-001)
Friday owns the objective. Hermes owns durable execution. Claude specialists own bounded
engineering assignments. No lower layer becomes another Friday: no second objective engine,
task graph, capability registry, canonical memory, permission system or Hermes supervisor.

## Current architecture (relevant seams)
```
owner ──voice──► Friday (voice_brain / agent_friday)
                   │ capability_use / hermes_delegate
                   ▼
            objectives.py + continuous.py + dag.py   (durable objective engine)
                   │ development capability
                   ▼
            development.DevelopmentRun ──TaskBundle──► executors.hermes ──► hermes_bridge.HermesSupervisor
                                                                              │ session.create {model, provider, reasoning_effort}
                                                                              ▼
                                                                    Hermes gateway (profile `friday`, one HERMES_HOME)
                   ▲ on_terminal → memory_stack.outcomes; delivery broker → spoken completion
```

## Proposed fit, slice by slice (extend seams, add no layer)
| Need | Lands in | Why there |
|---|---|---|
| Task contract | `claude_code.TaskBundle` (+`allowed_paths`, `verification`, `role`, `iteration_budget`, `known_facts`), `development.DevelopmentRun.execute` (fills them), `hermes_bridge.TaskBundle.render` (sections GOAL / ACCEPTANCE / KNOWN FACTS / CONSTRAINTS / ALLOWED SCOPE / PROHIBITED / ROLE / VERIFICATION / REPORTING) | the bundle already exists on both executors; only the fields and the rendering are missing |
| Progress | new `friday/progress_digest.py` (pure: from `bridge.progress()`, continuity snapshots and work-run rows → milestone/digest strings, dedup by `seq`/hash, cadence); `agent_friday` periodic task next to the delivery drain; `ui_server` `/api/work`; `ui/index.html` Work section; `voice_brain` "what's running" → same module | the bridge already tracks progress; nothing speaks it on the room path and nothing summarises |
| Quota routing | `provider_diagnostics` (CAPPED kind + reset parsing), new `friday/provider_cooldowns.py` (store-backed windows), `execution_economics.candidates(tier)` (ordered from profile `routing.tiers` + `hermes fallback` config + provider cache) and `plan_delegation` skipping cooled providers, `hermes_bridge.delegate` recording `route_reason` incl. the switch, spoken via progress digest | detection already exists as TRANSIENT; only the memory of the cap and the alternative are missing |
| Fingerprints | `continuous.py` attempt path (after `FailureClassifier.classify`): `failure_fingerprint(kind, error, verifier, task)` persisted on the task attempt; repeat without new evidence → strategy change (re-plan / debugger role / reduce / BLOCKED) with `MAX_STRATEGY_CHANGES` | the loop, attempts and classifier exist; add one guard, no new loop |
| Handoffs | new `friday/handoff.py` dataclass + `from_work_run(record, events)`; stored as JSON on `hermes_work_runs.handoff`; `render_completion` reads it | the parent needs evidence, not transcripts; the work-run row is the natural home |
| Hermes team | new `friday/hermes_team.py`: `profiles()` (native `hermes profile list`), `ensure_profile(name)` (`profile create`, config cloned from `friday`), `route_to_profile(team)` (roles.compile_team → profile names), kanban ops via the CLI (`kanban create/assign/show/swarm`); `HermesSupervisor` gains `profile=` on `delegate` where the gateway supports it, else kanban | native primitives; Friday's objective stays top-level; kanban is beneath one objective task |
| Claude specialists | `.claude/agents/friday-{debugger,performance-reviewer,final-reviewer,codebase-researcher}.md` (`memory: project`, review-only tools), `roles.py` `CLAUDE_AGENT_FOR_ROLE` map, `claude_code` worker prompt names the subagent to use | project subagents are discovered from the worktree's `.claude/agents` |
| Memory promotion | new `friday/memory_promotion.py`: `Candidate` → evidence check → contradiction check (`memory_graph` / `store` supersession) → dedupe → `store.remember`/`brain` with provenance/owner/scope/confidence; fed by `handoff.memory_candidates` and `autolearn` | the stores and contradiction model exist; only the gate between private and canonical is missing |

## Data flow (new pieces)
1. Digest: bridge events → `_progress[work_run_id]` (seq, tool, last line) → `progress_digest.compose(runs, objectives, now)` → {milestones: [...], digest: str|None} → spoken on room (deliver_message) and page (follow loop) → also `/api/work`.
2. Quota: gateway error / session.usage → `provider_diagnostics.diagnose` → CAPPED(provider, reset_at) → `provider_cooldowns.mark` → next `plan_delegation` skips it → `delegate(model, provider, route_reason="claude capped until 14:00 → gpt")` → the digest speaks the switch; the cooldown expires by clock.
3. Contract: DevelopmentRun → TaskBundle(all fields) → HermesExecutor → bridge TaskBundle.render (sections) → Hermes; `evaluation.verify` checks the same acceptance afterwards.
4. Fingerprint: attempt fails → fingerprint → compare with previous attempt's → same & no new evidence → `strategy_changes += 1` → re-plan with a different role / smaller task → after `MAX_STRATEGY_CHANGES` → BLOCKED with the evidence.
5. Team: objective task (size ≥ medium, ≥ 2 roles) → `hermes_team.route` → kanban board task(s) with dependencies assigned to profiles → `kanban swarm` executes → Friday polls `kanban show` → handoff → verifier. Size small → `delegate()` on `friday` as today.

## Interfaces (signatures; bodies in code)
- `TaskBundle(goal, workspace, project, context, acceptance, constraints, allowed_paths=(), verification=(), role="", iteration_budget=0, known_facts=())`
- `progress_digest.compose(runs: list[dict], objectives: list[dict], *, now, last_digest_at) -> Digest(milestones, digest, next_at)`
- `provider_diagnostics.Diagnosis.kind` gains `CAPPED`; `.reset_at: datetime|None`
- `provider_cooldowns.mark(provider, model, until, reason)`, `.active(now) -> {provider: until}`, `.clear()`
- `execution_economics.candidates(tier) -> list[(provider, model)]`; `plan_delegation(...)` adds `switched_from`, `route_reason`
- `continuous.failure_fingerprint(kind, error, verifier="", task_id="") -> str`; task attempt detail gains `fingerprint`, `hypothesis`, `strategy_changes`
- `handoff.Handoff` (task_id, agent, role, status, summary, files_read, files_changed, tests_run, verification, decisions, assumptions, failed_attempts, residual_risks, blockers, memory_candidates, skill_candidates, next_action) + `from_work_run(record, events) -> Handoff`
- `hermes_team.profiles()`, `ensure_profile(name)`, `plan_team(goal) -> list[str]`, `submit_board(objective_task, team) -> board_ref`, `poll(board_ref) -> status`
- `memory_promotion.promote(candidate: Candidate) -> Decision(accepted, reason, superseded)`

## Data (additive only)
`hermes_work_runs`: `route_reason TEXT`, `handoff TEXT` (JSON), `profile TEXT` via `_ADDED_COLUMNS`-style migration; new table `provider_cooldowns(provider, model, until, reason, at)`; objective task attempt detail JSON gains `fingerprint`/`hypothesis`; kanban lives in Hermes's own SQLite (not ours).

## State
Objective task: … → FAILED(attempt) → [fingerprint same & no evidence] → STRATEGY_CHANGE → … → BLOCKED | SUCCEEDED | PARTIAL | WAITING_QUESTION | WAITING_PERMISSION | CANCELLED. Provider: READY → CAPPED(until) → READY.

## Failure paths
Digest composer never raises (a failed compose = silence, logged). A cooled provider list that is empty falls through to the profile default with a spoken warning. Profile creation failure → route on `friday` with role prompts and say so. Kanban unavailable → `delegate()` as today. Handoff parse failure → the raw completion still speaks.

## Security
Profiles isolate memory and keys per specialist (never one writable HERMES_HOME shared by concurrent workers). Specialists never receive the owner's whole personal memory: `with_memory()` stays the bounded, episode-free block. Promotion never writes secret-shaped text (`brain._sensitive`). Claude reviewers are read-only (`tools` without Edit/Write).

## Observability
`run_events` kinds: `digest`, `route.switch`, `fingerprint.repeat`, `strategy.change`, `handoff`; access log lines for profile creation and kanban submissions.

## Compatibility
All bundle fields default to empty; renderers emit sections only when present; existing tests for TaskBundle/delegate unchanged in meaning. Kanban and profiles are opt-in by task size; the `friday` profile keeps working alone.

## Alternatives considered
A Friday-side kanban (rejected: duplicate board); an LLM "progress narrator" (rejected: hallucinated progress; the digest is composed from events only); switching models mid-session (rejected: Hermes sessions are per task — switch at the next task, which is what the retry already does).

## Risks
Profile/gateway topology unknown until S5's live check; kanban `swarm` behaviour on Windows; digest cadence vs the pause rule on the room path (never interrupt the owner mid-sentence: the digest waits for a quiet turn).
