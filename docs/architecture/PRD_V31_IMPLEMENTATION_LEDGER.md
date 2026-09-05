# PRD v3.1 Implementation Ledger

Persistent ledger for the autonomous implementation of
`FRIDAY_JARVIS_Master_Product_Requirements_Document_v3.1.docx` (PRD v3.1,
4 Sep 2026). Regenerate evidence on the exact build; never copy counts
forward. Lifecycle per requirement:
`NOT_AUDITED -> EXISTING | PARTIAL | MISSING | BROKEN -> IMPLEMENTING -> TESTING -> VERIFIED`.

Started 2026-09-04 on commit `99dd904` (working tree dirty: the live agent
edits its own source; 79 paths modified/untracked at start, see
`data/baseline/summary.txt`).

## Phase 0 - baseline on the exact tree

Deterministic suite (`bash scripts/baseline_suite.sh data/baseline`,
`.venv-verify`, `-m "not live and not slow"`, 190 test files in 4 chunks):

| chunk | result |
|---|---|
| 0 | 1116 passed (342s) |
| 1 | 670 passed, 36 deselected (199s) |
| 2 | (pending at time of writing - see data/baseline/summary.txt) |
| 3 | (pending) |

Live stack at audit time: MCP core listening :8000 (pid 1396), UI :8770
(pid 22140). Hermes install located via `.env` (`HERMES_PYTHON`,
`HERMES_DIR` -> `D:/hermes/hermes-agent`, commit `5831d8365`); Friday's
Hermes profile `D:\hermes\profiles\friday` (model `claude-opus-5` /
`anthropic`, authenticated providers: anthropic, openai-codex, nvidia,
opencode-zen, opencode-free).

### Measured facts that shape the design

- Hermes has a first-class stateless inference path that starts NO agent
  loop: `agent.auxiliary_client.call_llm(provider=, model=, messages=,
  max_tokens=, route_info=, latency_info=)` (Hermes `agent/oneshot.py`
  wraps it). Probe from the friday profile: `PONG` in 3.99s, route
  `{'provider': 'anthropic', 'model': 'claude-opus-5'}`, usage
  `prompt_tokens=42 completion_tokens=5`. Import cost ~1.25s. This is the
  MODEL_GATEWAY substrate (FR-069/070/079). The default Hermes profile's
  main model (`opencode-free/deepseek-v4-flash-free`) returned HTTP 400
  "Model is unavailable" - an entitlement failure the gateway must report
  truthfully (FR-072), not mask.
- `hermes_cli.models.list_available_providers()` returns
  `{id,label,aliases,authenticated}` per provider - the provider discovery
  substrate (FR-071).
- Friday's own conversational brains call `google.genai` directly
  (`friday/voice_brain.py:_model`, `friday/planner_model.py:_model`,
  `friday/providers.py` for LiveKit). These are the existing "fast default"
  path; the PRD allows fast defaults for routine interaction (FR-075) and
  requires Hermes MODEL_GATEWAY for higher reasoning tiers.

## Requirement audit (P0 first)

Legend: EX=EXISTING, PA=PARTIAL, MI=MISSING, BR=BROKEN, IMPL=IMPLEMENTING,
TEST=TESTING, VER=VERIFIED. "Where" names the module that owns it.

### 4.9 Hermes Model Gateway (all P0 except FR-081)

| FR | State | Where / evidence | Gap |
|---|---|---|---|
| FR-069 dual-role interface | MI -> IMPL | `friday/hermes_bridge.py` is EXECUTION_ENGINE only (tui_gateway JSON-RPC, `prompt.submit`) | no inference-only mode |
| FR-070 model gateway envelope | MI -> IMPL | none | `ModelGatewayRequest/Result` |
| FR-071 provider discovery | MI -> IMPL | `execution_economics.known_models()` reads a cache file only | live query of Hermes provider layer |
| FR-072 subscription/API separation | MI -> IMPL | none | per-route entitlement state |
| FR-073 credential isolation | EX (by construction) | credentials resolved inside Hermes process; Friday never reads them | verify with secret scan test |
| FR-074 privacy truthfulness | MI -> IMPL | `friday/privacy.py` exists for local-only mode | route boundary label |
| FR-075 routing policy | PA | `execution_economics.choose_route` picks route+tier by task economics | integrate gateway tiers/fallback |
| FR-076 inference-only context budget | MI -> IMPL | `TaskBundle` compiles context for EXECUTION | compiled minimal package for gateway |
| FR-077 token budget profiles | PA | `TaskBundle.token_budget` label only; `voice_brain._thinking_budget` | per-class numeric budgets + escalation |
| FR-078 token growth guard | MI -> IMPL | none | growth detector across retries/handoffs |
| FR-079 no agent loop | IMPL | `call_llm` is a single provider transaction | test proves no tool/subagent side effects |
| FR-080 usage telemetry | MI -> IMPL | `WorkRunLog.usage()` for execution runs only | gateway_calls table |
| FR-081 failover w/o reset | PA | `provider_fallback.py`, `provider_cooldowns.py` exist | bounded gateway failover |

### 4.1 Objective and orchestration

| FR | State | Where | Gap |
|---|---|---|---|
| FR-001 objective ledger | PA | `friday/objectives.py`, `store.py` (runs, run_tasks, run_checkpoints, run_events, run_wakes) | no `risk_tier`, `approvals[]`, `evidence[]` columns as first-class; add objective schema view |
| FR-002 complexity classification | PA | `execution_economics.classify_task` (kind/consequence/blast radius) | map to TRIVIAL..CRITICAL classes; benchmark >=95% |
| FR-003 dynamic planning | EX | `objectives.compile`, `planner.py` | verify exit criteria present |
| FR-004 bounded loop | EX | `continuous.py` max_attempts, fingerprint block, watchdog | verify stuck detection test |
| FR-005 checkpointing | EX | `run_checkpoints`, `continuity.py`, `RunWatchdog` | restart/resume test |
| FR-006 interruption | EX | `HermesSupervisor.interrupt`, `arbiter.py`, LiveKit interruption | verify latency |
| FR-007/008 (P1) | PA | `roles.py`, `requirements.py` | contrarian mode roster |

### 4.2 Delegation

| FR-009 Hermes contract | EX | `TaskBundle` (goal, constraints, allowed_paths, verification, memory slice, budget) persisted in `WorkRunLog` | verify reproduce-from-record |
| FR-010 secondary worker | EX | `executors/claude_code.py`, `executor_router.py` (DEFAULT hermes, FALLBACK claude) | |
| FR-011 teams (P1) | PA | `hermes_team.py`, `roles.py` | roster visibility |
| FR-012 independent verifier | PA | `evaluation.py`, `promotion.py` | |
| FR-013 worker budget | PA | `runtime_control.py` single-flight; no numeric concurrency cap | resource governor |
| FR-014 failure isolation | EX | supervisor restarts, `FAKE_HERMES_DIE` tests | |

### 4.3 Memory

| FR-015 unified memory | PA | `store.memories`, `brain.py` (GBrain adapter), `memory_stack.aggregate` | worker->store write path test |
| FR-016 memory classes | PA | kinds FACT/PREFERENCE/PATTERN/INFERENCE + scope | add type taxonomy (working/session/project/user/semantic/episodic/procedural/codebase/tool_state) |
| FR-017 scoped retrieval | PA | `scope` column; `memories_by_scope` | project-scoped filter in `memory_stack` |
| FR-018 provenance | PA | source, confidence, superseded, observation_id, evidence_count | supersedes/contradicts links, source_ref |
| FR-019 correction/forgetting | EX | `remember(supersede)`, `forget`, contradictions table | export |
| FR-020 context compilation | EX | `memory_stack.aggregate(budget_tokens)`; `TaskBundle.with_memory(600)` | telemetry |

### 4.4 Capability fabric

| FR-023 registry | EX | `fabric.Provider` (license, mode, risk, permissions, cost, commit, health) + `capabilities.py` | manifest export view |
| FR-024 types | PA | modes BUILTIN/ADAPTER/MCP/SKILL/SIDECAR/REFERENCE_ONLY/CLI | add SDK/HTTP/SPECIALIST_RUNTIME mapping |
| FR-025 progressive discovery | EX | `capability_router.py` (CORE_TOOLS + groups) | |
| FR-026 health | EX | `fabric.STATES` READY/DEGRADED/AUTH_REQUIRED/UNAVAILABLE/DISABLED | FAILED state alias |
| FR-028 MCP gateway | EX | `GuardedMCPServerHTTP`, `PolicyEngine` | |

### 4.8 Verification and safety

| FR-052 evidence ledger | EX | `contracts.py` (Verification, ActionResult), `tool_results`, `artifacts` | |
| FR-053 completion gate | EX | `continuous._finish` counts task statuses; `honesty.audit` | |
| FR-054 observability | EX | `run_events`, `run_task_attempts`, `runtime_metrics` | |
| FR-056 resource governor | MI -> IMPL | psutil present; no governor | `friday/governor.py` |
| FR-057 secret isolation | EX | `secret_broker.py`, `vault.py`, Hermes-side creds | scan test |
| FR-058 policy engine | EX | `policy.py` PolicyEngine (AUTO/ASK/CONFIRM/DENY) | |
| FR-059 risk tiers | PA | categories, DESTRUCTIVE, NON_APPROVABLE | explicit R0-R4 mapping |
| FR-060 exact-action approval | EX | `confirmation.py` nonce bound to action | |
| FR-061/062 security workspace | PA | `security_skills`/`strix_pentest` risk=restricted + `security.authorized_scope` | SecurityAuthorization contract w/ scope+expiry |
| FR-063 prompt injection | PA | `policy.provenance_verdict` | test |
| FR-065 audit log | PA | `access_log.jsonl`, `user_policy.audit_trail`, run_events | append-only privileged-action audit |

### 4.7 Self-development

| FR-048 sandbox | EX | `executors/worktrees.py`, `sandbox.py`, `fsjail.py` | |
| FR-049 test gate | EX | `promotion.py` | |
| FR-051 rollback | EX | `self_upgrade.py` staged w/ rollback | |

## Architectural decisions

- AD-1 (2026-09-04): MODEL_GATEWAY is implemented as a **separate Hermes
  process invocation** (`<hermes python> -m` a tiny broker script under
  `friday/hermes_model_gateway_worker.py`) using Hermes's own
  `agent.auxiliary_client.call_llm`. Rationale: the Hermes venv is not
  Friday's venv (no cross-import); `call_llm` never builds an agent,
  toolset, or session; credentials stay in the Hermes process (FR-073).
  A persistent broker subprocess (JSON lines over stdio) amortises the
  ~1.3s import.
- AD-2: Friday's existing Gemini paths remain the "fast default" tier for
  routine voice interaction (FR-075 permits fast defaults). Reasoning
  tiers route through the gateway.

## Failed attempts

- Running Hermes with `HERMES_HOME=/d/hermes/profiles/friday` (MSYS path)
  fails `assert_named_profile_home_live`; must pass the native
  `D:\hermes\profiles\friday` form.

## Current requirement

The PRD gate closed on `3bcf6d4` (matrix + unconditional verdict). The
same day an external audit (`docs/architecture/AUDIT_2026-09-05_TRIAGE.md`)
found what the gate did not: the public repo carried the live SQLite DB,
runtime logs, a committed private key + pairing token, a 16 MB binary, and
`verify.yml` had **failed** on the pushed HEAD. Every P0 item is resolved
in the triage doc's resolution log (commits `d1751bb` .. this one); the
remaining P0 action is owner-side (GitHub push protection toggle).

Next: the audit's P1 "verify against reality" list - opt-in live provider
suite (A-008/A-024), browser prompt-injection pages (A-036), runtime
invariants (A-048), soak harness (A-051), provider transport model
(A-010/A-018/A-019) - and the standing matrix limitations (NFR-P01..P05
live room; FR-011/029/046; FR-068; 10 planner misroutes).

## Verified log

### 2026-09-05 - External audit P0 -> RESOLVED (repo hygiene, CI truth, four hardening items)

- Repo: 88 runtime/artifact/binary paths untracked, `.gitattributes`,
  companion pairing rotated, gitleaks full history 0 findings with the
  allowlisted test fakes (`d1751bb`).
- CI: the run on `3bcf6d4` was red (14 Windows-only test modules
  ImportError on ubuntu). Now `windows-latest` required + `ubuntu-latest`
  compat, `collect_ignore` with the reason, pytest-timeout per test.
- A-043 kernel guard covers trust roots + verifier + golden corpus +
  security tests + workflows (`7af2c96`, 76 tests).
- A-038 WAL/busy_timeout; `tests/test_store_durability.py` kills a real
  writer mid-transaction (`7af2c96`, 4 tests).
- A-022 objective budget enforced from recorded spend before every call
  (`8becc2d`, 6 tests + 184 regression).
- A-042 remote nonce/timestamp replay protection (`09898f5`, 9 tests).
- A-029/A-047 canonical Python runner with kill-tree chunk timeouts
  (`9231ba7`, proven exit 124 / 0 survivors).
- A-028 nine rules restored with path scopes.

Deliberately not done: history rewrite (rotated material; force-push on a
public `main` costs more than it removes - command recorded in the triage
doc for the owner).

### 2026-09-05 - Final full deterministic gate `data/post_final` -> 3,737 passed, 1 skipped, 0 failed

206 test files on the exact tree that carries every change in this
ledger (planner `_split_requests`, golden runner on the production
planner path, `selfdev_benchmark`, `hermes_bridge.health`, the FR-040
ui_server expectation). chunk0 1,176 / chunk1 720 / chunk2 1,019 (+1
skipped) / chunk3 822 passed; 0 failures; **0 unexplained deterministic
failures**.

One environmental anomaly, recorded rather than hidden: chunk3 took
2 h 41 m instead of its usual ~5 min, stalled at ~52% (the
`test_worktrees` region) from about 06:00 to 08:34. Cause found on the
host: a `git.exe fetch origin main --depth 1` + `git maintenance run
--auto --detach` pair belonging to the Hermes install (`cwd
D:\hermes\hermes-agent`, started 21:49 the previous evening) was still
alive, and a zero-byte `.git/index.lock` from 12:01 sat in this repo
(the live agent's known habit, AGENTS.md operational notes). The
worktree tests shell out to `git` with a 120 s timeout per call; once the
straggler exited the chunk finished in seconds with every test green.
Lock cleared (no git.exe of this repo running); the four files in that
region re-run fresh: 59 passed in 83 s. Not a test defect and not a
product defect - a host contention, the same class as the earlier
post_browser capture failures.
### 2026-09-05 - Full deterministic gate `data/post_golden` -> 3,733 passed, 1 skipped, 1 failed -> resolved

206 test files (two new: test_remote_channel, test_golden_suite). The one
failure: `test_ui_server.py::test_http_surface_is_live` expected the old
`/api/objective` capture stub (`{"captured": true}`); FR-040 replaced the
stub with the real objective door. Expectation updated to the stronger
contract (a run id, or a stated reason; `ok == bool(run_id)`); 29 passed
with test_remote_channel. 0 unexplained deterministic failures.

### 2026-09-05 - FR-050 benchmark before promotion -> VERIFIED

`friday/selfdev_benchmark.py`: the self-development loop had a benchmark
gate that could not fail - no `measure` was ever passed, so every
candidate reached BENCHMARKED as "skipped". Now a change to a
perf-sensitive file (planner, router, registry, memory, store, engine) is
measured INSIDE ITS SANDBOX by a subprocess with the sandbox first on
`sys.path` (memory P95, action p95, manifest bytes/capability, routing
top-1, planner accuracy) against the live tree's baseline, and rejected
past `BENCHMARK_TOLERANCE` (10%). A docs/tests-only change records "no
performance claim". A measurement that cannot run is a failed
measurement (every metric at its worst) - it rejects, it does not pass.
tests/test_selfdev.py 18 passed: regressing sandbox REJECTED with
before/after in the record; the sandbox path (not the live tree) is what
was measured; docs-only skipped; missing checkout fails closed. Real
probe measured on the live tree in 60 s.

### 2026-09-05 - FR-007 human decision minimization -> VERIFIED (and a golden-runner defect fixed)

Acceptance is "Golden Objective evaluation tracks clarification count;
avoid questions resolvable from memory/tools". `friday/golden.py` now
counts clarifications per case from durable state (UNMAPPED tasks handed
back, WAITING_QUESTION runs, question events - permission requests are
FR-060 and excluded) and scores `clarifications <= expect.clarifications`;
the report carries `clarifications`, `clarification_rate`,
`unwarranted_clarifications`. Four corpus cases (GO-memory-011..014):
three answers already in memory (0 questions allowed, 0 asked) and one
genuinely unplaceable request ("sort out the thing we discussed and send
it over": exactly 1 clause handed back, no guess).

The unplaceable case exposed a real defect: the golden runner planned
free-text objectives with `toolsets.objectives.plan_objective` - the
retired clause splitter, which `objective_start` no longer calls - and it
turned that sentence into `power_sleep` + `system_battery`. The runner
now takes the production path (`planner_model.plan_objective` ->
`validate` -> `task_specs`), so the suite measures the planner people
actually get.

That in turn exposed a production planner defect the splitter had
masked: `_SPLIT` cut "which windows are open right now" at the bare verb
"open" (a state, not a request) into windows_list + open_in_browser, run
PARTIAL (GO-general-005). Fixed by `planner._split_requests`: a
whitespace-only separator inside a question no longer splits;
punctuation, "then" and ", and <verb>" still do. Both defects are now
cases in `docs/golden/failures.jsonl` (GO-general-109, GO-memory-102;
corpus of 10).

Golden run `data/golden/fourth.json` on the production planning path:
162/162 (154 corpus + 8 failures at the time), success 1.0, false
completion 0, unauthorized 0, evidence coverage 1.0, 1 clarification (the
warranted case), 0 unwarranted, median 0.17 s, p95 0.50 s, 0 model
calls. Routing KPI after the split fix: router top-1 96.5%; planner 95
correct / 39 unresolved / 10 confident misroutes. Planner/routing
regression 327 passed.
### 2026-09-05 - Hermes EXECUTION_ENGINE independent validation -> VERIFIED (live), one bridge defect fixed

`scripts/verify_hermes_engine.py`: starts a gateway under the production
`HermesSupervisor` (fresh `HERMES_HOME`, so the machine's live Hermes
session's leases are not shared), delegates ONE bounded coding task via
`TaskBundle` into a scratch git repo (`allowed_paths`, `iteration_budget`
6, `token_budget` LOW, `share_memory=False`), waits, and judges the
outcome from disk - never from the agent's completion text.

Evidence `data/hermes/engine_validation_first_run.json`: gateway ready in
2.6 s; `delegate` 29.7 s; record `COMPLETE`; `answer.txt` == exactly
`friday-hermes-ok`; `git status` shows only `answer.txt`; commit count
unchanged (1) - the "do not commit" constraint held. Usage attributed:
model claude-opus-5, 3 calls, 116,647 tokens, 0 subagents.

Defect found by the probe: `HermesSupervisor.health()` reported
`alive: False` on a healthy, freshly started gateway because its one RPC
was `commands.catalog`, which cold-imports the whole `hermes_cli.commands`
registry (5 s idle, >15 s under load). Fixed in `friday/hermes_bridge.py`:
`session.list` (ms) decides liveness, the catalog is asked afterwards with
a 45 s budget and degrades to a warning. `tests/test_hermes_bridge.py`
+ `test_hermes_engine.py` + `test_hermes_health_layers.py`: 47 passed.

Second run of the probe (`engine_validation_governor_refusal.json`) was
refused by the resource governor: `CRITICAL pressure: RAM 96% used; no new
worker started` - the host had 1.0 GB free with the full suite, Playwright
and Chrome running. That is FR-056 doing its job at the delegation seam,
recorded as evidence rather than retried around.

### 2026-09-05 - E2E suite (Playwright, chromium, real UI server) -> 48 passed

`cmd.exe /c e2e-run.bat` -> `e2e-run.log`: 48 passed in 5.4 min, EXIT=0
(12 spec files; `scripts/run_ui.py --no-browser --bypass-face` as the web
server, `ADA_DB=data/e2e-ada.sqlite3`).

### 2026-09-05 - Performance measurements (PRD 5, NFR-P06..P13; KPIs) -> VERIFIED where measurable

`scripts/perf_profile.py` -> `data/perf/latest.json` with the 7.3
provenance block. On this build and machine:

| NFR | target | measured |
|---|---|---|
| P06 memory retrieval | P95 < 500 ms | P95 89 ms (2,000 memories, 200 scoped queries) |
| P07 simple local action | < 2 s | explicit graph p95 0.45 s; planner-routed p95 0.17 s |
| P08 idle CPU | < 5 % | live 8-process stack, 60 s: avg 0.06 %, max 0.58 % |
| P09 worker cap | 0-2 | 6 requested -> 2 granted, 4 queued |
| P10 token efficiency | no catalog/memory dump | manifest 24.8 bytes/capability; 6 of 40 history turns compiled |
| P11 gateway isolation | no tools/subagents | request carries no tools/subagent/skills/memories keys |
| P12 growth guard | stop before exhaustion | geometric growth stopped at call 2; identical-repeat loop at call 3 |
| P13 attribution | 100 % | 50/50 gateway calls carry objective + provider + model |
| KPI restart resume | >= 99 % | 10/10 + 1/1 real-process kill/resume (test_chaos_restart) |
| KPI routing accuracy | >= 95 % | router path top-1 96.5 %, top-3 100 % (144 labelled phrasings) |

Routing, stated honestly: the deterministic objective planner alone
scores 93 correct / 40 unresolved (handed to the model planner at run
time) / 11 confident misroutes on the same set. Eleven misroutes are the
open defect list (`planner_path_confident_misroutes`); the fixes this
cycle (no-target blind fallback -> unresolved; `operation_assumed`
overrule; six single-reading verbs added to the grammar; noun red-herring
overrule when the noun's shortlist fits at 0) took it from 25 to 11
without touching the router path, which `test_capability_routing` pins.
Verbs with more than one reading across objects (`put`, `bring`,
`restart`, `go`, `switch`) were tried and reverted - `test_semantics`
proves they break "put some music on" / "restart the song".

NOT measured (need a live voice room or a person; recorded as such in the
report, not estimated): P01 interruption, P02 first acknowledgement, P03
first stream, P04 UI propagation, P05 Control Room cold start.

Planner/routing regression after the changes: 263 passed, 1 skipped
(planner, registry order, capability routing, semantics, objective
planner/dispatch/control-plane/engine, selective, phase0, golden suite,
task class).
### 2026-09-05 - Full deterministic gate `data/post_schedules` -> 3,715 passed, 1 skipped, 1 failed -> resolved

204 test files. The one failure was the silent-exception ratchet
(`test_silent_excepts`: 88 > 81 + 5 grace): seven `except Exception:
pass` handlers, six of them mine (golden bench teardown x2, golden
memory read, manifest adapter probe, observability process iteration,
objectives store swap - pre-existing). Fixed by logging/recording instead
of swallowing; the ratchet is back under the baseline (5 passed). Not a
test weakening: the handlers now say what they caught.

### 2026-09-05 - Golden Objective evaluation suite (PRD 7.2, 7.3, 12.1; KPI table 1.5) -> VERIFIED

`friday/golden.py` (runner, scorer, report) + `scripts/golden_corpus.py`
(generator) -> `docs/golden/objectives.jsonl` (150 cases: general 20,
browser 25, coding 35, research 20, business 15, docs_data 10, memory
10, recovery 10, security 5) + `docs/golden/failures.jsonl` (8 golden
failures - every bug found this session as a replayable case with the
defect named). Acceptance is written in the case before any run:
status, capabilities that must/must not run, files/memory after, policy
refusals expected, latency + model-call budgets, interventions. Each
case runs through the REAL objective engine (`compile_objective` +
`ContinuousTaskExecutor` + production `objective_cli.build_dispatch`)
in its own bench: isolated SQLite store, file jail root, memory, audit
log, and a loopback web fixture allowed through `netguard.
evaluation_fixture` (one origin, in-process, for one case - not an env
switch). Scoring is deterministic on the 7.3 axes: correctness,
evidence, policy compliance, latency/cost, manual-intervention count;
false completion and unauthorized actions computed, not trusted. Report
carries the 7.3 provenance block (date, commit, dirty flag, python,
machine profile, configuration).

Result `data/golden/second.json` (commit 99dd904 dirty, Windows 16
cpu / 15.8 GB): 150/150 passed, success 1.00 (target >= 0.90), false
completion 0.0 (< 0.01), unauthorized 0, evidence coverage 1.00,
median 0.141 s, p95 0.406 s, 0 model calls; every category 100%.
Golden failures 8/8. First run was 138/150 and the twelve failures were
real: (a) `files_search` argument names in the corpus (corpus fix);
(b) ten planner-routed general cases misrouted - three planner defects
fixed in `friday/planner.py`: no-target fallback took the first eight
READ capabilities in registry order (now a capability whose own
examples fit decisively supplies its target and joins the shortlist);
a READ question with one LIST-shaped candidate was left unresolved
(informational operations are interchangeable for shape); a noun
("screen") pinned the wrong target although another capability's
examples fit >= 2x better (words overrule the noun above a threshold).
`files_roots` gained intent examples. Planner/routing regression 249
passed.

Found and fixed on the way, FR-060 in a durable objective: an ASK-tier
action inside a run used to FAIL the task (no retry, no way to say yes).
Now `continuous._park_for_approval` records the exact action
(operation/target/parameters, PENDING) on the run, parks it at
WAITING_PERMISSION with a blocker and one delivery; `continuous.
resume_after_approval(store, run_id, decided_by, operation, target)`
refuses a mismatched operation/target, writes APPROVED, marks the task
READY; `capability_runtime` answers the ASK for that one call ONLY when
an APPROVED record with identical parameters exists on a run (a forged
`approved_by` argument grants nothing) and audits it as APPROVED_ONCE.
`tests/test_golden_suite.py` 13 passed (corpus contract, idempotent
generator, scoring axes, false-completion detection, report gates,
provenance, real case through loopback web, and the four
permission-boundary tests).

### 2026-09-05 - Remote channels (FR-040) -> VERIFIED

`friday/ui_server.py` `/api/objective` (POST) is the remote door: the
face/PIN session gate is the identity (423 before any handler without a
valid session), `objectives.objective_start` compiles into the same
ledger with the same policy engine, the run is tagged
`source_channel=remote:<channel>` (`X-Friday-Channel`, sanitised) with
a `remote.accepted` event, and `/api/objective/status` reads the same
ledger back; the access log records every remote objective. Replaced
the previous read-only "captured" stub. `tests/test_remote_channel.py`
5 passed: unauthenticated refused before the ledger; authenticated
enters the same ledger and reads back; channel sanitised + logged;
empty -> 400; a remote ASK-tier write parks exactly like a local one
(no channel privilege).

### 2026-09-05 - Full deterministic gate `data/post_browser` -> 3,683 passed, 1 skipped, 4 failed -> all four resolved

200 test files, 4 chunks (469 s / 288 s / 293 s / 288 s). Triage:
- `tests/test_capture.py` x2 (`inspect.getsource(read_before_answering)`
  returned the wrong function): ENVIRONMENTAL. `agent_friday.py` was
  patched (FR-038 mute gate) 34 s after chunk0 started importing it, so
  the code object's line numbers no longer matched the file on disk. Both
  functions (`remember_the_project`, `note_the_requirements`) are still
  called from `read_before_answering` (L1375-1376). Re-run clean.
  Lesson: never edit source while a suite chunk is importing.
- `tests/test_journey_e.py`, `tests/test_vertical_slice.py` (gate check
  order): EXPECTATION UPDATE for the FR-012 `independent_review` check
  recorded between `verified` and `scope` (same change already made to
  `test_promotion`; these two end-to-end journeys were missed). Updated
  with the reason in a comment; the behaviour is the PRD's.
Re-run of the three files: 78 passed.

### 2026-09-05 - Observability (FR-054, PRD 12.3) -> VERIFIED

`friday/observability.py`: `trace(run_id)` rebuilds one timeline from
durable state only - objective events + tasks, `tool_results` (latency,
verification method/evidence), MODEL_GATEWAY call ledger (tokens,
retries, failovers), trust audit rows (tier, decision), executor/worker
runs - never model thoughts; `trace_text` for the voice. `diagnostics()`
is the 12.3 view: build identity, objective store (PRAGMA quick_check),
memory store, providers, Hermes/worker health, browser connection, voice
gateway, MCP/capability health, queue depth, resource pressure, recent
critical failures; every section best-effort with `probe_ms`, redacted by
default (`redact`: secret KEY names by whole word - `tokens_in` and
`token_budget` accounting survive - plus token-shaped VALUES).
Engine: `task.retry` event now recorded (`EVENT_TASK_RETRY`) with kind,
reason, attempt, delay, strategy hint - a retry is a fact in the ledger,
not an inference from a counter. Gateway ledger timestamps made UTC-aware
(merged trace was mis-ordering). Tools `objective_trace`,
`system_diagnostics` wired (policy / router `observability`+`governor` /
capabilities tail / semantics `trace`,`diagnostics` -> READ).

Evidence: `tests/test_observability.py` 7 passed - a REAL engine run with
a TRANSIENT failure + retry, a capability run, a gateway call and an R2
policy decision all appear in one time-ordered trace; unknown run is
said not invented; redaction by key and shape; diagnostics never raises
(a probe that throws reports `unavailable` with the reason) and reports
all 11 sections; recent failures surfaced with secrets redacted; tool
faces. Live `diagnostics()` on this machine: 11/11 sections ok in 6.9 s
(build 5.2 s = git on this tree).

### 2026-09-05 - Schedules + conditional monitoring (FR-041, FR-042) -> VERIFIED

`friday/toolsets/schedules.py` + `friday/tools/schedule_control.py`
(`schedules_create/list/run/history/delete`). A schedule is a persisted
OBJECTIVE (text or explicit task graph) with trigger once/daily/interval/
manual, budgets (retry/tokens/time -> written onto the objective row),
permissions (pre-approved tool ids: ASK-tier only AND risk tier <= R1 -
an unattended run may not pre-approve `files.delete` (R3) or
`browser.automate` (R2); CONFIRM tiers refused by the policy engine),
delivery channel (session | toast | none) and a condition evaluated by
CODE (`task_output` path/op/value, `task_status`, `any_failed`, `always`).
Each firing compiles and drives the objective through the real engine
(same ledger, evidence gate and policy as a spoken request; run tagged
`source_channel=schedule:<name>`), evaluates the condition, delivers or
suppresses, and writes a `schedule_runs` row. OS registration via
`schtasks` XML, queried back; `once` disarms itself after firing.
Engine change: schedule-sourced runs do not self-announce on completion -
the schedule's condition decides (FR-042 no-noise). Store: `schedules`,
`schedule_runs` tables; `touch_objective_run` accepts `approvals` and
`evidence`.

Evidence: `tests/test_schedules.py` 11 passed - condition false: the
objective RUNS (COMPLETED in the ledger) but nothing is queued for the
session and the firing is recorded `suppressed`; condition true: exactly
one delivery; `any_failed` fires only on failure; definition + budgets +
permissions survive a fresh Store on the same file and every firing is
recorded (FR-041 acceptance); once-schedule disarms; permission tiers;
bad definitions refused before storage; list/run/history/delete faces;
task XML well-formed for every trigger. Wiring gates 504 passed.

### 2026-09-05 - Crash / restart / resume chaos (FR-005, FR-014, KPI Restart Resume) -> VERIFIED (real processes)

Test: `tests/test_chaos_restart.py` (@slow, 2 tests, 43 s). A child
process runs the real objective engine (`compile_objective` ->
`ContinuousTaskExecutor.start`) against an on-disk SQLite store; the
parent `kill()`s it (TerminateProcess, no shutdown hook) while task
"slow" is mid-flight; a FRESH process with a new executor identity opens
the same database, runs `RunWatchdog.sweep_once()`, and the run
completes. Assertions on durable state only: run COMPLETED; the task that
had SUCCEEDED before the kill is not re-executed (side-effect file shows
`fast` done exactly once, never started by cp-2); the interrupted task is
re-dispatched and finished by cp-2; every task carries evidence (FR-052);
ledger has `watchdog.orphaned`, exactly one `run.completed`, and
`lease.acquired` by `cp-1` then `cp-2`. Second test repeats the
kill/resume cycle on 10 objectives: 10/10 resumed to COMPLETED (KPI
>= 99%; deterministic 100% or the failing cycle is named).

Bug found and fixed: `RunWatchdog._is_orphan` returned False for ANY run
with a `next_wake` set, so a run whose owner died after scheduling a wake
(the normal crash shape - the driver sets a wake between rounds) was never
reconciled by a fresh control plane; the orphan was only picked up if its
own driver loop happened to be running. Now a wake only shields a run
while it is in the future AND the lease is live; a due wake with a dead
lease is the crash case and is reconciled. Regression: objective /
continuity / watchdog / control-plane / dispatch / planner / mcp suites
172 passed.

### 2026-09-05 - Browser primitives + boundaries (FR-031/033/034/035/036/037) -> VERIFIED (live Chromium)

Implementation: `friday/browser.py` - all 13 PRD primitives (open,
inspect, navigate, click, type, scroll, select, upload, download, tabs,
screenshot, wait, verify) through observe -> plan (policy) -> act ->
observe -> verify over an injectable `Driver` (`PlaywrightDriver` real).
`PRIMITIVES` maps each to BROWSER_CONTROL (reads, AUTO) or
BROWSER_AUTOMATION (state changes, ASK). FR-034 `EXTERNAL_WRITE`
(purchase / publish / destructive / security settings) detected on the
element + URL -> `EXTERNAL_WRITE_CONFIRM`, refused without an exact-action
confirmation even in an AUTHORIZED session. FR-035 `HUMAN_VERIFICATION`
detected structurally (widget classes / challenge iframes via DOM
`markers`, interstitial titles, challenge phrases - not the bare word
"captcha") -> `Handoff`, never clicked through. FR-033 `choose_profile`:
workers ISOLATED unless the approval named the authorized profile.
FR-037 structured element table + forms + tabs first; screenshot is
evidence. FR-036: every step has before/after observations; a missing
target is refused ("re-observe rather than guessing"). Toolset
`toolsets/web.py::browser_act` over the shared Playwright session; MCP
`browser_act` (policy BROWSER_AUTOMATION; router browser; capability
user_device/requires_edge/requires_auth; semantics act->EXECUTE).
Playwright + greenlet + pyee copied into `.venv-verify`.

Evidence: `tests/test_browser_primitives.py` 19 passed: 17 rule-layer
tests + LIVE `test_live_every_primitive_against_real_chromium` (headless
Chromium over a loopback fixture: type/select/upload/download/tabs
new-switch-close/scroll/wait/verify/screenshot; "Buy now" refused as
purchase; captcha page handed off) + LIVE toolset-face test through the
policy gate (APPROVAL_REQUIRED partial for the purchase,
HUMAN_VERIFICATION partial on the captcha, unknown primitive failed).
Regression 444 passed wiring gates + browser_capability + jarvis_screen.
181 MCP tools.

### 2026-09-05 - Voice interruption + mute (FR-038, FR-039) -> VERIFIED

Audit: LiveKit path already had streaming STT/TTS, silero VAD, end-of-turn
model, `allow_interruptions=True`, interruption min_duration 0.5 s with
false-interruption resume; livekit-agents 1.5.1 stores the playback-
synchronized transcript with `interrupted=True` (FR-039 met on that
path by the framework; pinned by test). Two real gaps closed:
(1) FR-038 mute: `VoiceInputGate` existed but was never attached
(reachability KNOWN=FUTURE); now attached in the entrypoint
(`agent._input_gate`), KNOWN entry removed. (2) FR-039 browser-UI path
stored the FULL reply even when `shutUp()` cut it: `ui/index.html`
`speak(text, messageId)` tracks finished sentences (`HEARD`) and
`shutUp()` POSTs `/api/interrupted {message_id, heard}`;
`ui_server.api_interrupted` -> `voice_brain.mark_interrupted` ->
`Store.truncate_message` rewrites the row to the heard prefix +
`[interrupted]` (refuses a non-prefix rewrite); `reply()` returns
`message_id`. `_recent_turns` therefore hands the model only what was
heard.

Evidence: `tests/test_voice_interruption.py` 9 passed (store truncation +
prefix guard, model history reflects interruption, message_id on reply,
/api/interrupted through the UI server, page JS contract, LiveKit
behaviour pinned, session interruption config, mute gate detach/reattach
via room events, gate attached in entrypoint). Regression 127 passed
(voice_brain_ui, ui_server, reachability, runtime_metrics,
voice_pipeline).

### 2026-09-05 - Controlled self-development (FR-047..051) -> VERIFIED (real git)

Implementation: `friday/selfdev.py` - the PRD loop as a state machine
whose order is enforced by `_REQUIRES` (OBSERVED -> PROPOSED -> SANDBOXED
-> IMPLEMENTED -> TESTED -> REVIEWED -> REGRESSION_PASSED -> BENCHMARKED
-> PROMOTED -> MONITORED / ROLLED_BACK; REJECTED is terminal). FR-047:
`observe()` refuses a candidate without a measured number. FR-048:
`sandbox()` = `WorktreeManager.create()` (new; git worktree on its own
branch under .claude/worktrees); `implement()` rejects files outside the
proposal and a live checkout that moved. FR-049: `test()` + `regression()`
run in the sandbox via an injectable runner (real pytest by default);
there is no call path to `promote()` from any state but BENCHMARKED.
FR-012: `review()` = independent reviewer over the sandbox diff; DISPUTED
/ INCONCLUSIVE reject. FR-050: `benchmark()` before/after with 10%
tolerance. FR-051: `promote()` = --no-ff merge (needs `approved=True`),
`monitor()` health probe -> automatic `rollback()` = `git revert -m 1`
(history kept); manual rollback too. Kernel surfaces
(`self_upgrade.KERNEL_PATHS`) refused at propose, before any sandbox
exists. Every transition journaled + R3 audit row. Toolset
`friday/toolsets/selfdev.py` (applies a unified diff with `git apply`
inside the sandbox) + MCP `selfdev_run` / `selfdev_promote` /
`selfdev_rollback` / `selfdev_status` (policy FILE_WRITE /
COMMAND_EXECUTION x2 / READ_LOCAL_SAFE; router `selfdev`; registry tail;
semantics selfdev->DEVELOPMENT, promote->MUTATE, rollback->RECOVERY).
Fixed on the way: `WorktreeManager.changes()` lost the first character of
the first path (`git()` strips the leading porcelain space) - now slices
per line.

Evidence: `tests/test_selfdev.py` 15 passed on a throwaway git repo per
test: live checkout unchanged while sandbox holds the change (file +
HEAD + clean status asserted); kernel refused pre-sandbox; out-of-scope
file rejected; failing tests -> REJECTED and `promote` raises
GateRefused; no path around gates (4 skips refused); regression failure
after review rejects; DISPUTED review rejects; benchmark regression
rejects with before/after recorded; promote needs approval; merge lands
(VALUE=2), simulated post-promotion failure auto-rolls back (VALUE=1,
`git diff base` empty, log has merge + Revert, clean status); manual
rollback after healthy monitor; journal has all 10 states, audit chain
ok with sandboxed/promoted/rolled_back rows; rejected sandbox kept as
evidence, promoted one removed; toolset run->promote(refused without
approval)->promote->rollback end to end; non-applying patch rejected.
Regression: 416 passed wiring gates + worktrees + self_upgrade +
core01_red + reachability. 180 MCP tools.

### 2026-09-05 - Adversarial reasoning (FR-008, FR-012) -> VERIFIED (deterministic + live)

Implementation: `friday/adversarial.py` - `deliberate()` runs five fixed
roles (proposer, contrarian, failure analyst, evidence checker, judge),
each ONE bounded inference through the Hermes MODEL_GATEWAY
(`gateway_infer`, compile_context, no agent loop); structural tagged
parse (`parse_tagged` handles `TAG: item`, `TAG:` + one-per-line,
bullets, indented continuation); disagreements computed from the record
(objections + failure modes the judge's own words do not address), so an
ignoring judge cannot erase them; unavailable roles recorded, never
invented. `independent_review()` reviews diff + claim + verifier result
under a different attribution; findings override a self-contradicting
CONFIRMED; empty diff -> INCONCLUSIVE. `friday/promotion.py::decide`
takes `review=`: > CONSEQUENTIAL_FILES (3) files needs an independent
review, DISPUTED refuses (REVIEW_DISPUTED), missing/inconclusive refuses
(NOT_INDEPENDENTLY_REVIEWED), self-review does not count.
`DevelopmentRun.review()` reads the real git diff against base_commit;
`gate()` passes the evidence. MCP tools `decision_deliberate`,
`change_review` (policy WEB_SEARCH, router `adversarial`, registry tail,
semantics decision/change->DEVELOPMENT, deliberate->START, review->READ)
backed by `friday/toolsets/adversarial.py`.

Evidence: `tests/test_adversarial.py` 14 passed (13 deterministic + 1
live): live five-role panel via anthropic through the gateway in 33s.
Live decision sample (ledger SQLite vs Postgres): verdict DEFER,
confidence 40, consensus False, 8 standing disagreements, 2 unresolved,
4 uncertainties, E1 supported / E2 unsupported / 4 missing - exactly the
FR-008 acceptance (no fabricated consensus). Promotion: 4-file change
refused without review; DISPUTED refused with the finding in detail;
CONFIRMED accepted; implementer self-review rejected. Real-git
DevelopmentRun.review sends `-x = 1 / +x = 2` to the reviewer.
Regression 462 passed wiring/promotion/development/manifest;
`test_promotion::test_every_check_that_ran_is_recorded` updated for the
new `independent_review` check in the audit trail. 176 MCP tools.

### 2026-09-05 - Capability Fabric manifest (FR-023..028) -> VERIFIED

Implementation: `friday/manifest.py` - PRD 9.6 `Manifest` derived from
the fabric registry (typed NATIVE/MCP/CLI/SDK/HTTP/SIDECAR/SKILL/
REFERENCE/SPECIALIST_RUNTIME from integration_mode + adapter TRANSPORT
+ specialist set), Friday's own tools (NATIVE, with risk tier and
dangerous actions from the policy table, same id resolution as
`Capability.requires_approval`), and the coding executors
(SPECIALIST_RUNTIME); `summary()` (FR-025 names+counts, <40 bytes per
capability) / `describe(id)` / `export(path)` (FR-027). `friday/fabric.py`
gains the FAILED state: `FAILED_AFTER` (3) consecutive call raises mark a
READY provider FAILED with detail; next call re-activates via the health
probe. MCP tool `capability_manifest` (policy READ_LOCAL_SAFE, router
`capabilities`, registry tail, semantics `manifest`->READ) backed by
`friday/toolsets/manifest.py` for objective reach.

Evidence: `tests/test_manifest.py` 13 passed - every provider/tool/
executor present with all 9.6 fields; SKILL/REFERENCE never executable;
unregistered id and undeclared operation refused; three raises -> FAILED
-> recovers; one raise does not; UNAVAILABLE never reported executed
(no verification, no output); permission gate fires before activation
(FR-028); export carries pin + license + review for every upstream.
Regression 509 passed / 1 skipped across fabric, wiring gates,
core01_red, reachability, trust. 174 MCP tools registered.

### 2026-09-05 - Post-governor deterministic gate -> 0 unexplained failures

`bash scripts/baseline_suite.sh data/post_governor` (192 files):
chunk0 1 failed / 1117 passed; chunk1 682 passed; chunk2 958 passed /
2 skipped; chunk3 827 passed. The one failure,
`test_core01_red::test_red_a_reach_is_most_of_the_registry_not_a_handful`
(141 of 173 resolve; ceiling 30 unresolved), was caused by this cycle's
new MCP-only tools (model_* / system_pressure) having no `run`-first
implementation in `friday/toolsets/`. Fixed at the root: added
`friday/toolsets/model_gateway.py` (ActionResult-returning
implementations; the MCP tools now call them - one implementation, two
faces) and `friday/toolsets/system.py::system_pressure`. Reach now 28
unresolved; core01_red + reachability green.

### 2026-09-05 - Trust plane (FR-058/059/060/061/062/063/065) -> VERIFIED

Implementation: `friday/trust.py` - R0..R4 `CATEGORY_TIER` complete
over `policy.DEFAULT_POLICY` (asserted), `Verdict.tier`;
`SecurityAuthorization` (PRD 11.2 fields, refuses unbounded scope /
missing basis / missing approval / missing expiry), `target_guard`
(host CIDR/subdomain scope, allowed/prohibited action, intensity
ceiling, expiry) enforced inside `fabric.call` for the security
namespace; `AuditLog` sha256-chained append-only SQLite
(`data/audit.sqlite3`, WAL, one connection; ~8ms->sub-ms per row),
secret redaction, `verify_chain()`; `PolicyEngine.decide` audits every
R1+/non-AUTO verdict. `tests/conftest.py` gives each test its own log.

Evidence: `tests/test_trust.py` 16 passed - adversarial prompt cannot
change a verdict; WEB_PAGE provenance denied destructive tools; secrets
DENY under FULL autonomy; approval binds to run+action+target+args and
is single-use / expiring; real `strix_pentest` call refused for
out-of-scope host and for no contract, both audited DENY; chain detects
edit and delete. Regression: 600 tests green across policy, fabric,
confirmation, approval channel, wiring gates, reachability.

### 2026-09-05 - Shared scoped memory (FR-015..020) -> VERIFIED

Implementation:
- `friday/store.py` - `memories` gains PRD 9.5 columns (memory_type,
  project_scope, source_ref, supersedes_id, contradicts_id,
  retention_policy, last_retrieved_at, importance); `MEMORY_TYPES` (9
  classes) + `MEMORY_RETENTION` lifecycle; `remember()` scopes
  supersession per project and links `supersedes_id`; `recall_scoped`
  (global-or-this-project only, stamps last_retrieved_at),
  `memory_provenance` (source, ref, links, open contradictions, current),
  `export_memories`, `expire_memories` (working/session lifecycle).
- `friday/memory_stack.py` - `preferences()`/`aggregate()` take
  `project_scope`; other projects are excluded before the prompt.
- `friday/toolsets/memory.py` + `friday/tools/memory_control.py` -
  `memory_remember` accepts memory_type/project/source_ref; new
  `memory_provenance`, `memory_export` (wired: policy MEMORY_READ, router
  memory_extra, capabilities, semantics `provenance`->READ).

Evidence: `tests/test_memory_contract.py` 10 passed (class lifecycle,
project isolation at store AND compiler seam, correction supersession
with links, contradiction surfacing, retrieval stamping, cross-worker
readback, toolset round-trip, bounded compiler telemetry). Regression:
phase1d, memory_promotion, hermes_memory_writeback, profile,
memory_provenance, conversation_memory, shared_brain + wiring gates:
397 passed, 1 skipped.

### 2026-09-05 - Objective ledger + task class + evidence + completion gate (FR-001/002/052/053) -> VERIFIED

Implementation:
- `friday/task_class.py` - deterministic TRIVIAL/SIMPLE/STANDARD/COMPLEX/
  LONG_RUNNING/CRITICAL classifier (whole-word matching; the substring
  version mis-flagged "release date"/"payment module"/"scheduler"/
  "production health" as CRITICAL), `route_for` (class -> route level),
  `risk_tier_for` (class -> R0..R4 floor).
- `friday/store.py` - `objective_runs` gains PRD 9.2 columns (task_class,
  risk_tier, owner_id, project_scope, memory_scope, constraints,
  required_capabilities, retry_budget, cost_budget_tokens, time_budget_s,
  approvals, evidence, blocker, source_channel) via the additive
  migration; `append_objective_evidence` / `append_objective_approval`
  (append-only, seq+timestamp); `objective_ledger()` assembles the PRD
  Objective shape.
- `friday/objectives.py::compile_objective` classifies at admission and
  persists class/risk/budgets/required capabilities; the `run.created`
  event carries the classification.
- `friday/continuous.py` - `_ledger_evidence` at every task success
  (direct + reconciled worker + composite) and failure;
  `_completion_gate` (FR-053): COMPLETED requires a passing evidence entry
  per succeeded task, else PARTIAL with `evidence_gap:<task ids>` and a
  `completion.gate_refused` event.
- `friday/toolsets/model_gateway.py` - run-first implementations of
  model_providers/model_infer/model_usage/system_pressure so the objective
  engine can bind them (CORE-01 reach); MCP tools delegate to them.

Evidence:
- `tests/test_task_class.py` 6 passed: 64-item labelled benchmark
  (criteria written before execution) at **100% agreement** (>=95%
  required); TRIVIAL/SIMPLE never route to an executor.
- `tests/test_objective_ledger.py` 7 passed: schema persisted + survives
  reopen, append-only evidence/approvals, evidence written through the
  REAL executor for success/failure/composite, completion gate refuses a
  task that was only "said" done (run ends PARTIAL, t2 never dispatched).
- Regression: objective restart/continuity/watchdog/rc11/auth-resume/mcp/
  fingerprint suites 85 passed; core01_red + gateway + governor + fabric
  runtime + reachability 92 passed.

Fixed on the way:
- Post-governor suite chunk0 failure `test_core01_red::test_red_a_reach`:
  the 4 new MCP-only capabilities pushed unresolved to 32 (>30). Root
  cause: capabilities without a run-first toolset implementation are
  unreachable by the objective engine. Fixed properly (implementations
  added), not by raising the ceiling.

### 2026-09-05 - Post-governor deterministic suite

`bash scripts/baseline_suite.sh data/post_governor` (192 files): chunk0
1117 passed / 1 failed (core01_red, fixed above), chunk1 682 passed;
chunks 2-3 see `data/post_governor/summary.txt`. Re-run scheduled after
the memory work as `data/post_memory`.

### 2026-09-04 - Hermes MODEL_GATEWAY (FR-069..081) -> VERIFIED (deterministic) / VERIFIED (live)

Implementation:
- `friday/hermes_model_gateway_worker.py` - runs in the Hermes venv under
  the friday profile; `hello` / `providers` / `infer` / `shutdown` over JSON
  lines. Uses `agent.auxiliary_client.call_llm` (single stateless provider
  transaction). Strips its own directory from `sys.path` because
  `friday/platform/` shadows the stdlib `platform` module inside Hermes.
- `friday/model_gateway.py` - `ModelGatewayRequest/Result` (PRD §9.4),
  `TokenBudget` per task class (FR-077), `GrowthGuard` (FR-078: ceiling,
  geometric growth, identical-context repeats), bounded failover
  (FR-081: `max_failover`, unhealthy-route memory, cooldown marks for
  quota/rate-limit), `GatewayTelemetry` (FR-080: `data/gateway_calls.sqlite3`,
  never the prompt), provider discovery with `route_kind` (FR-071/072),
  `boundary` label (FR-074), `compile_context` (FR-076).
- MCP tools `model_providers`, `model_infer`, `model_usage`
  (`friday/tools/model_gateway_control.py`), wired through all invariants
  (policy, router group `model_gateway`, capabilities tail, semantics
  `model`->DEVELOPMENT + `infer`->READ).

Evidence:
- `tests/test_model_gateway.py`: 21 passed (20 deterministic against
  `tests/fake_model_gateway_worker.py` + 1 `@live`). Live: real Hermes,
  anthropic route, `PONG`, usage reported, `boundary=upstream_cloud`.
- `scripts/probe_model_gateway.py` output: providers in 2.67s
  (anthropic api, openai-codex subscription, opencode-free free_tier ...),
  default route claude-opus-5 5.4s, haiku 0.95s, opencode-free ->
  `MODEL_UNAVAILABLE` reported truthfully (FR-072).
- Wiring gates: 360 passed (reachability, capability_reach/router/routing,
  approval_channel, semantics, phase0, planner, selective,
  registry_order_contract). 172 MCP tools register.

Fixed on the way:
- `tests/test_upstream_lock.py` 2 baseline failures: the build pack
  directory `Friday Stark Demo Main/` was deleted in 99dd904 but
  `scripts/upstream_lock.py` and `scripts/new_upstream_set.py` still read
  its lock template. Restored the template (from f6bcae5) and the brief
  list to `docs/integrations/build_pack/` and repointed both scripts.
  25 passed.
- The gateway's failover test wrote fake routes into the LIVE
  `data/provider_cooldowns.json`; cleaned the file and isolated the test
  fixture (`COOLDOWNS_FILE` monkeypatched to tmp).
