# 01 — Reality (2026-09-04, checked against the code, not the pasted plan)

Labels: REPRODUCED / STATICALLY_CONFIRMED / OBSERVED / INFERRED / SUSPECTED / UNKNOWN.

## Control plane (keep)
- STATICALLY_CONFIRMED: `friday/objectives.py`, `continuous.py` (1323 lines: leases, watchdogs,
  `FailureClassifier`, `PROVIDER_DOWN` diagnostics via `provider_diagnostics.diagnose`, bounded
  `max_attempts`, provider backoff), `dag.py`, `planner*.py`, `development.py`,
  `roles.py` ("a role is not a process"; `size_of`, `compile_team`, `Team`), `org.py`
  (proposal only), `executor_router.py` (Hermes default, Claude fallback), `hermes_bridge.py`,
  `memory_stack.py` (7 tiers incl. `outcomes` since 2026-09-03), `store.py`, `evaluation.py`
  (`Verifier`, `verify`, `graded`, `Record`), `promotion.py` (code-promotion gate: debris,
  secrets, scope, base), `autolearn.py` (`AutoLearner` from turns), `honesty.py` (`ClaimAudit`)
  all exist. Nothing here is replaced.

## The owner's complaints, traced
1. **"He says I'm doing it and goes quiet."** STATICALLY_CONFIRMED: the bridge keeps live
   progress per work run (`HermesSupervisor._progress`, `progress()` L1420, fed by
   `tool.start/progress/complete`, `message.complete` events); the control-room page follows
   the `seq` and speaks each new line (`ui/index.html` ~L1152, `/api/hermes/progress`).
   The LiveKit room only drains completions (`drain_hermes_deliveries` L2206, called at L2343);
   there is NO progress narration and NO timed digest on either path; objective portions
   narrate milestones only through `continuity.reserve_narration`. Completion text is
   `render_completion(record)` — outcome, no route reasoning.
2. **"Acceptance criteria."** STATICALLY_CONFIRMED (the plan's claim is true):
   `development.py` L210-215 builds `TaskBundle(goal, workspace, project, context,
   constraints)` — no `acceptance`; `executors/hermes.py` L56 forwards
   `acceptance=tuple(bundle.acceptance)` faithfully, so Hermes gets an empty contract from
   development runs. `claude_code.py` L84-127 documents acceptance as what `finish()` checks.
3. **"It burned the 5-hour / weekly limits."** STATICALLY_CONFIRMED: no quota logic anywhere
   (`grep -i 'rate.?limit|quota|429|resets'` over bridge/health/economics/router: nothing
   except `provider_diagnostics` treating 429 as TRANSIENT-retry). `execution_economics`
   resolves ONE model per tier (`resolve_model`), from the profile's `routing.tiers` and the
   provider cache; `delegate()` records requested vs effective model/provider (H0) but a
   capped provider is simply retried after `PROVIDER_BACKOFF_SECONDS`.
4. **"Sub-agents that share memory."** STATICALLY_CONFIRMED: Hermes bundles carry Friday's
   memory (`TaskBundle.with_memory`), outcomes write back (`on_terminal`); `roles.py` roles
   are prompts inside one executor context; `org.assemble` never spawns anything; one Hermes
   profile (`friday`) at `D:\hermes\profiles\friday`.
5. **"Retries the same failure."** STATICALLY_CONFIRMED: `continuous.py` has attempts and
   provider backoff, no fingerprint / no-progress detection (`grep fingerprint|no.?progress`: none).

## Installed runtimes (OBSERVED)
- Claude Code 2.1.260: project subagents with `memory: project`, `maxTurns`, `effort`,
  `disallowedTools`, worktree isolation; 9 `friday-*` agents exist (all `memory: project`),
  no `.claude/agent-memory/` yet (created on first use). Missing vs the plan: debugger,
  performance-reviewer, final-reviewer, codebase-researcher.
- Hermes 0.20.6 at `D:\hermes\hermes-agent` (`venv/Scripts/hermes.exe`): subcommands
  `profile {list,use,create,delete,describe,show,alias,rename,export,import,install,update,info}`,
  `kanban {init,boards,create,swarm,list,show,assign,set-model,reclaim,reassign,diagnostics,link,…}`
  ("durable SQLite-backed task board shared across Hermes profiles; tasks claimed atomically,
  can depend on other tasks, executed by a named profile"), `fallback`, `moa`, `worktree`,
  `pause`, `peer`. Profiles on disk: only `friday`. `profile_home()` in the bridge resolves
  `<hermes root>/profiles/<name>`; the supervisor runs ONE gateway under the `friday` profile.
- Executors: `claude_code.py` drives the CLI as a subprocess (`TaskBundle` with
  `acceptance`, `constraints`, `isolate`=worktree); `executors/worktrees.py`, `runs.py`,
  `brokers.py` exist.

## Tests that exist for these modules (OBSERVED)
test_development, test_roles, test_evaluation, test_promotion, test_autolearn,
test_executor(+continuity/router/sandbox), test_hermes_engine/bridge/delivery,
test_planner(+model, objective, audit), test_continuity(+livekit, fresh_db). No test names
mention fingerprint, handoff, digest, quota, profile routing or kanban.

## Hermes topology (OBSERVED 12:36, resolves most of the unknowns below)
- `hermes profile list`: `default` (deepseek-v4-flash-free, gateway running) and `friday`
  (claude-opus-5, gateway running) — a gateway runs PER PROFILE; `profile create <name>
  --clone-from friday` copies config.yaml/.env/SOUL.md/skills (`--clone-all` copies all state).
- Kanban lifecycle is complete in the CLI: `init` (idempotent kanban.db), `boards`, `create
  --assignee PROFILE --parent --workspace --goal --goal-max-turns --model --provider
  --max-runtime --max-retries --idempotency-key --json`, `link` (dependency), `claim`,
  `comment`, `attach`, `complete`, `block`, `schedule`, `reassign`, `set-model`, `show --json`,
  `list --json --assignee --status {todo,ready,running,review,blocked,done,…}`, `swarm --worker
  PROFILE:TITLE[:SKILL] --verifier --synthesizer` (parallel workers → verifier → synthesizer).
  Dispatch is done by each profile's gateway (`hermes pause` stops "cron/kanban dispatch"), so a
  specialist profile needs its own gateway running to pick up its tasks.
- `hermes fallback list` on the friday profile: "No fallback providers configured" — the chain
  is empty; `fallback add` is an interactive picker. Friday-side switching (S2) does not depend on it.

## Unknown until built
UNKNOWN: whether `hermes profile create` copies the `friday` config/keys or needs `--from`;
whether one gateway can serve several profiles or each profile needs its own gateway process
and port; how `kanban swarm` starts workers on this Windows host. S5 verifies live before wiring.
