# 01 — Reality (2026-09-03)

Labels: REPRODUCED / STATICALLY_CONFIRMED / OBSERVED / INFERRED / SUSPECTED / UNKNOWN.

## The transcript the owner pasted (control room, Deepgram path, 2026-09-02)
- STATICALLY_CONFIRMED: every failure in it was fixed the same evening in
  `friday/voice_brain.py`: `clock/now` op plus the persona line "never say you
  cannot"; `files/write` picks a scratch name when none is given (no bounce-back);
  `_GO_AHEAD` + `_LAST_PLAN_NONCE` make "okay / yes / go" after a desktop plan
  the approval; `hermes/delegate` is in the surface so "add a comment to
  answer.py" is not a camera question; `_thinking_budget()` sizes thinking to
  the question.
- REPRODUCED: 245 passed in `.venv-verify` across 17 test files
  (test_voice_brain_ui, test_model_selection, test_hermes_engine,
  test_turn_timing, test_daily_driver_hardening, test_conversation_memory,
  test_fabric_execution, test_fabric_cli_adapters,
  test_fabric_commerce_and_packs, test_fabric_completion, test_breaker,
  test_ui_server, test_execution_economics, test_orgplane, test_routing_memory,
  test_jarvis_screen, test_silent_excepts). Run 10:01 IST, 156 s.
- OBSERVED: the live processes are newer than the sources. `server.py`
  PID 20852 started 2026-09-02 21:10:58 (:8000); `run_ui.py` PID 20984 at
  21:11:14 (:8770); `agent_friday.py start` PID 27968 at 2026-09-03 09:42:40.
  Newest source mtime is `hermes_bridge.py` 21:10:19. The fixed brain is what
  is running on both paths.

## Single engine, model selection, memory to sub-agents
- STATICALLY_CONFIRMED: `executor_router.DEFAULT = "hermes"`, `FALLBACK = "claude"`;
  `friday/executors/hermes.py` is a shim over `HermesSupervisor.delegate`.
- STATICALLY_CONFIRMED: there is no ChatGPT reasoning component. OpenAI is TTS
  only (`providers.DEFAULT_TTS = "openai"`); the voice LLM is Gemini. Removing
  OpenAI would silence Friday. Decision recorded 2026-09-02 in
  `docs/audit/2026-09-02-SINGLE-ENGINE-BUILD.md`; kept.
- STATICALLY_CONFIRMED: `execution_economics.REQUIREMENT_TIERS` maps spoken
  requirements ("cheapest", "think hard", "quick") to economy / standard / deep;
  `plan_delegation()` yields the model (haiku / sonnet / profile-default opus)
  and the Hermes `reasoning_effort` (low / medium / high), validated against
  `D:\hermes\profiles\friday\provider_models_cache.json`.
- STATICALLY_CONFIRMED: memory → Hermes exists (`TaskBundle.with_memory()`, 600
  tokens of the six-tier stack; episodes excluded so the transcript never leaks).
- STATICALLY_CONFIRMED: memory ← Hermes does NOT exist. `hermes_bridge.py` only
  reads `memory_stack`; a finished run is spoken by the delivery broker
  (`render_completion`) and then forgotten. `memory_stack.log_result()` has zero
  production callers (only `tests/test_ui_server.py:165`). Gap → S2.

## Token-aware depth in the objective engine
- STATICALLY_CONFIRMED: the header "340 tasks open · 1.71M tokens" is an
  all-time aggregate. `ui_server._metrics` sums `model_tokens` over every
  `run_portions` row ever written and counts every non-terminal
  `objective_tasks` row. 205 of those tasks belong to one run
  (`RUN-6a8cfa07e634`); the header objective is `RUN-7883f20e81a6`
  (2026-08-29, FAILED). Not a live runaway; a misleading label.
- STATICALLY_CONFIRMED + OBSERVED (read-only query of the live DB):
  `PortionBudget.max_model_tokens = 32000` is never enforced. Tokens are
  recorded after the fact from LiveKit usage events
  (`continuity_livekit.on_usage_updated` → `record_model_tokens`); the only
  budget check is `_budget_exhausted` at the next portion claim. Largest single
  portion: 229,027 tokens. Two runs ended `budget_exhausted:model_tokens` at
  354,917 and 444,058 against a 250,000 total. Gap → S3.
- STATICALLY_CONFIRMED: Hermes-side depth is already bounded by an
  information-value stop condition (`hermes_bridge.py` near line 270, measured
  35 → 5 calls on the same goal).

## Third-party packs
- OBSERVED: `fadymondy/agents-team` (MIT) is a Claude Code plugin: skills
  `/team-gen`, `/meet`, `/evaluate-agent`, `/evaluate-agent-behavior`; eight
  agent archetypes (orchestrator and tech-leader on opus; domain-engineer,
  designer, qa-engineer, security-engineer, devops-engineer on sonnet; monitor
  on haiku, background); 13 rule templates; 4 hooks; `lib/gen/scaffold.py`
  renders a `team.json` into `.claude/`; `lib/eval/lint.py` grades agents.
  Its `agents/` directory is empty by design.
- OBSERVED: `VoltAgent/awesome-claude-code-subagents` (MIT): 158 agents in 10
  categories; frontmatter `name / description / tools / model` (106 sonnet,
  19 haiku, 25 inherit). Ships a plugin marketplace (`.claude-plugin/marketplace.json`).
- OBSERVED: both cloned with full history into `third_party/upstream/` (S4 pins
  them). The owner already has 236 user-level agents in `~/.claude/agents`
  (18 with `model:`); project `.claude/agents` is empty; VoltAgent names that
  collide with existing user files: `debugger`, `code-reviewer`.
- STATICALLY_CONFIRMED: the fabric already integrates 41 clones. Roles family:
  `role_recipes` (agency-agents, 258 briefs) and `company_playbooks`
  (auto-company, 14 executives). The skill-pack pattern is
  `fabric_adapters/_skillpack.py` (catalogue = read allowlist, one file per
  read, never bulk). `org.py` loads divisions from agency-agents only.
- SUSPECTED: `docs/integrations/INTEGRATION_STATUS.md` is stale against the
  medusa / smartstore adapters added 2026-09-02 (regenerate with
  `scripts/integration_matrix.py`).

## Environment
- OBSERVED: no `tmux`, no `omc` binary, so `/omc-teams` and `/setup` cannot run.
- OBSERVED: `claude` and `codex` CLIs exist; Playwright browsers live in
  `D:\playwright-browsers`, node in `D:\software` (see `e2e-run.bat`).
- Known pre-existing failures, not to be masked: 2 in `test_upstream_lock`
  (the owner deleted `Friday Stark Demo Main/06_schemas/UPSTREAM_LOCK_TEMPLATE.json`).
- UNKNOWN: LiveKit-room behaviour for the spoken phases (needs the owner's voice session).
