# PRIMER B — Services: `toolsets` / `executors` / `fabric_adapters`

Source of truth: `git ls-files friday/toolsets friday/executors friday/fabric_adapters | sort` → **76 files**
(7 executors + 35 fabric_adapters + 34 toolsets). Read in full; big files paged with
`offset`/`limit`. No source edits. Generated 2026-09-23.

## Cross-cutting conventions (read once)

- **Toolsets** are capability-runtime implementations: `run: c.Run` first, return
  `c.ActionResult`, never a bare string. Every toolset has `_gate(run, tool_id, engine)`
  → `CANCELLED + APPROVAL_REQUIRED` when policy denies, plus `_scoped()` adding
  `execution_scope` (`local_machine` | `network` | `agent_runtime` | `user_device`).
  Writes are verified by read-back; success without observation is `PARTIAL`, not success.
- **Executors** hand development work to another agent (Claude CLI / Hermes) and own the
  `run_id ↔ session_id` mapping in SQLite, the permission profile, the question channel,
  and the `ActionResult` verdict.
- **Fabric adapters**: one module per external upstream. Module-level
  `DESCRIPTOR: fabric.Provider` + optional `start/stop/health/call`. Nothing imported by
  core at startup; failure to import = `UNAVAILABLE`, never a boot break (NON_NEGOTIABLE 15).
  Shared helpers: `_cli_adapter.make()` (CLI shape), `_skillpack` (markdown-only packs,
  lazy single-file reads, `MAX_CHARS=40_000`, empty-dir gitlink guard).

---

## 1. `friday/executors/` (7)

### `executors/__init__.py`
Role: package doc — executor ≠ model provider; ADA gives a TaskBundle, answers questions,
enforces policy, verifies. Exports `ClaudeCodeExecutor, TaskBundle`.

### `executors/brokers.py`
Role: the two brokers that must stay separate. Flows: `QuestionBroker` (decisions >
FACTs > ask-once > persist; `classify()` order: `must_ask` → `conflicting()` → evidence →
`low_risk` default; `Verdict`: ANSWER_FROM_PROJECT / ANSWER_LOW_RISK_DEFAULT / WAIT_USER /
CONFLICT) and `PermissionBroker` (pre-launch `--allowedTools/--disallowedTools`; `EXPLORE`
read-only vs `BUILD` explicit list; `OUT_OF_SCOPE` never granted; `denials_from()` readback).
Gotchas: broad `Bash(*)` beats narrow rules — allowlist must be explicit; PREFERENCE/PATTERN
is context-only, INFERENCE never decides; `must_ask` regex checked *before* memory.

### `executors/cli.py`
Role: Claude Code CLI v2.1.233 as probed, not as documented. Flows: `Launch.argv()`
(`-p --output-format stream-json`, prompt on **stdin** — trailing positional is swallowed as
tool name; `disableAllHooks:true`; never move `CLAUDE_CONFIG_DIR`), `parse()` stream-json →
`Event`, `Run.start/events/cancel` (tree-kill via job object / `taskkill /F /T`).
Gotchas: `AskUserQuestion` + `PermissionRequest` hook don't fire in `-p`; `manual` is the
CLI's name for `default`; `bypassPermissions` always refused; user hooks hang without bash/node.

### `executors/claude_code.py`
Role: ADA→Claude run owner (`TaskBundle` → prompt → `cli.Run` → `finish()` → promote).
Flows: `TaskBundle.prompt()` (goal/context/role/constraints/acceptance/reporting),
`execute()` (governor admit → `runs.open` → stream events → `finish()`),
`continue_run()` (attach/resume/restart), `promote/reject/rollback/cleanup` via worktrees.
Gotchas: verdict = `changed_files()` + denials, never Claude's summary; no acceptance →
max PARTIAL; isolated runs inspect the worktree dir, not launch dir; sandbox backend =
allowlist egress only (`CODING_HOSTS`); prompt-questions only logged (`WAITING_QUESTION`),
never answered back into closed stdin.

### `executors/hermes.py`
Role: Hermes as mandatory engine (NON_NEGOTIABLE 2) through the same
`execute(bundle)→ActionResult` contract. Flows: `to_bridge_bundle()` field-for-field
(context→known_facts), `plan_delegation()` for model/effort/route, `supervisor().delegate()`
→ COMPLETE/PARTIAL/failed mapping. Gotcha: claude_code stays as explicit fallback only.

### `executors/runs.py`
Role: durable runs in `executor_runs` — transcript is accelerator, DB is truth. Flows:
`open/running/saw/close/cancelled`, `reconcile()` (LIVE+dead pid → INTERRUPTED, not FAILED),
`recover()` ladder attach→resume→restart→ask, `brief()` voice-readable. Gotchas: exit 0 ≠
SUCCEEDED (ActionResult decides); pid identity = pid+`pid_started_at` (name match caused
false-alive on recycled node pids); session lookup best-effort, resume failure falls to restart.

### `executors/worktrees.py`
Role: isolation boundary — worktree → verify → promote/reject → rollback. Flows: `create`
(`-b worktree-<name>` under `.claude/worktrees`), `commit` (headless runs leave uncommitted),
`promote` (merge `--no-ff`; conflict → REJECTED + `merge --abort`), `rollback` (`revert -m 1`,
never `reset --hard`), `unlock/cleanup/stale`. Gotchas: CLI leaves worktree LOCKED;
`verify()` checks git-common-dir + toplevel (empty subdir inside repo fails safe); promote
refuses unless checkout is on target branch; base_commit recorded, never assumed.

---

## 2. `friday/fabric_adapters/` (35)

### `fabric_adapters/__init__.py`
Role: contract doc — `DESCRIPTOR` + `start/stop/health/call`, lazy discovery via pkgutil.

### `fabric_adapters/_cli_adapter.py`
Role: single copy of CLI shape (`make(descriptor, bootstrap, commands)`). Flows: `start`=None,
`health`=version-check probe, `call`=one declared command, secrets as env never argv.
Gotcha: upstreams invisible in UI; reached via `capability_use(family, operation)`.

### `fabric_adapters/_skillpack.py`
Role: shared loader for markdown-only upstreams (SKILL mode). Flows: `cloned()` (non-empty
dir — gitlinks are empty dirs on fresh checkout), `health()` (READY only if entry exists
*and* ≥32 bytes), `read()` (40k cap), `catalogue()` (names only). Gotcha: eager load of
e.g. 348-file packs = token failure; `call()` reads one file.

### `fabric_adapters/agent_reach_transcribe.py`
Role: Agent-Reach narrowed to `transcribe` (yt-dlp+ffmpeg→Groq Whisper) + `doctor`.
Flows: absolute venv python path, `GROQ_API_KEY` from broker as env, ffmpeg via PATH/
`FFMPEG_DIR`/drive-root probe. Gotchas: `doctor` open (key not on descriptor or probe
closes too); READY without ffprobe is DEGRADED.

### `fabric_adapters/agenticseek_cli.py`
Role: agenticSeek local autonomous agent as isolated CLI worker. GPL-3.0 → CLI only
(`__post_init__` refuses ADAPTER). `version` open, `ask` gated `orchestration.local_agent`,
`paid` compute. Gotcha: owns its own browser + local model.

### `fabric_adapters/agents_team_pack.py`
Role: agents-team content pack (8 archetypes, 13 rules, 4 SKILL briefs), one file at a time.
Generator (`scaffold.py/lint.py`) never run (NON_NEGOTIABLE 1). MIT.

### `fabric_adapters/anythingllm_research.py`
Role: AnythingLLM RAG over REST (`ANYTHINGLLM_URL`, Bearer `anythingllm_api_key`). MIT root,
AGPL `open-computer/` subtree untouched. `workspaces/ask/documents` all open reads; never
spawns the instance. Gotcha: chat = slow LLM call, 60s timeout.

### `fabric_adapters/claude_subagents.py`
Role: 158 VoltAgent subagent briefs, 10 categories. `agents/find_agent/agent_category`
names-only; `agent` allowlisted against frontmatter index (bare name resolves only if
unique; `../` refused). MIT.

### `fabric_adapters/codebase_memory.py`
Role: tree-sitter repo graph binary (`third_party/bin/cbm`, 7763 nodes/39.5k edges).
Flows: one-shot `cli --json <tool>` (stateless across restarts) + warm daemon in `start()`
(~14s cold → ~4.5s). Gotchas: `install` never run (rewrites 43 client configs); daemon binds
127.0.0.1:9749 regardless of config (B-002, loopback, lazy activation bounds it); payload =
last JSON line with `content` key (log-tolerant).

### `fabric_adapters/company_playbooks.py`
Role: auto-company 14 executive playbooks + ~36 business skills, one file at a time.
Agent loop never run. MIT-via-package.json (no LICENSE file — recorded honestly).
Gotcha: some playbooks in Chinese (model-readable).

### `fabric_adapters/diagram_skill.py`
Role: diagram-design single skill + `references/` on demand (39 types). Friday renders with
`workbench_write/preview`; no upstream code runs. MIT.

### `fabric_adapters/dummy.py` / `dummy_backup.py`
Role: deterministic test instruments. `BEHAVIOUR` (ok/start_fails/degraded/auth/unavailable/
call_raises), `STARTS` counter proves idempotent activate; backup always READY (`cheap` vs
`free` so router orders dummy first) to prove `call_with_fallback`. Stay in tree like a multimeter.

### `fabric_adapters/graft.py`
Role: conceptual code context (`build/check/ask/grep/map/callers/skeleton/blast`),
deterministic tree-sitter only. No daemon; graph files on disk. Gotchas: `init` never run
(rewrites `.claude/`, AGENTS.md); `build` appends `graft/` to `.gitignore` (declared);
`build --deep` not exposed (spend decision); telemetry forced off (`DO_NOT_TRACK=1`, opt-out noted).

### `fabric_adapters/graphiti_memory.py`
Role: Graphiti temporal-graph *feed* into the one shared memory, never a second store.
Needs `graphiti_core` + Neo4j/FalkorDB; else UNAVAILABLE (never crashes, never starts a DB).

### `fabric_adapters/gstack_process.py`
Role: gstack 61 review/QA/ship methodologies as text; `route()` ranks by upstream `triggers:`
frontmatter with rarity-weighted tokens. `DENIED` withholds browser/host-setup/iOS flows
(Friday owns browser policy). Gotcha: prose mentions `/scrape` etc. — inert here.

### `fabric_adapters/harness_templates.py`
Role: awesome-harness-engineering templates (AGENTS/PLAN/IMPLEMENT/HARNESS_CHECKLIST).
`checklist` structured for the self-build completion gate; reading list deliberately not an
operation. CC0-1.0.

### `fabric_adapters/mausbot_skills.py`
Role: OpenMausBot 2 skills only (`phone-harness`, `create-verification-skill`). Electron app
never run; `enterprise/` proprietary subtree unreachable by allowlist. Apache-2.0 root.

### `fabric_adapters/maxun_scraping.py`
Role: Maxun robots over REST (`MAXUN_API_URL`, `x-api-key` — never `Authorization`).
AGPL → SIDECAR only. `robots/runs/results` open; `run_robot` needs `scraping.run` +
confirmation. Never spawns postgres+minio+browser stack. Gotcha: bare-id validation (`/` refused).

### `fabric_adapters/medusa_commerce.py`
Role: Medusa v2 commerce over admin REST (`MEDUSA_BACKEND_URL`, Basic `secret:` — Bearer
refused upstream). MIT core, enterprise subtree untouched. Reads open; product/inventory
writes need `commerce.write` (never retried — a retried POST is two products); payments/
refunds not operations. Never spawns store.

### `fabric_adapters/mem0_memory.py`
Role: Mem0 as extract-only feed (`extract` → candidate facts → `friday.profile` admission).
Stores nothing (one-memory rule). UNAVAILABLE until `mem0ai` installed. Apache-2.0.

### `fabric_adapters/open_design.py`
Role: open-design 162 skills + 153 systems + 114 templates; `catalogue/route` names-only,
single-file reads; Friday builds via workbench/Hermes (runner product never run). Per-template
licence gate, fail-closed (35 carry own LICENSE). Apache-2.0 root.

### `fabric_adapters/open_notebook_research.py`
Role: Open Notebook over FastAPI (`OPEN_NOTEBOOK_URL`, optional Bearer password).
`notebooks/notebook/ask` open reads (`ask` = open-session + execute-turn in one call);
`add_source` needs `research.write`. Never spawns SurrealDB. MIT.

### `fabric_adapters/openmontage_media.py`
Role: OpenMontage Backlot board reads (`OPENMONTAGE_URL`, `/api/health|projects|state`).
AGPL → SIDECAR; no auth/write route upstream, so no permission. Never spawns render pipeline.

### `fabric_adapters/openworker_cli.py`
Role: OpenWorker optional coding worker (`version/plan/run`); Hermes stays mandatory.
`run` needs `coding.workspace_write`; `bypass-approvals` mode not exposed. MIT.

### `fabric_adapters/postiz_social.py`
Role: Postiz scheduling over `/public/v1` (`POSTIZ_API_URL`, raw-key `Authorization` —
no `Bearer` prefix). AGPL SIDECAR. Reads open; `schedule` needs `social.publish` + human
confirmation; writes never retried. Never spawns app.

### `fabric_adapters/presentation_skill.py` (`adhd_mode`)
Role: i-have-adhd opt-in output mode (action first). `guidance` + `catalogue` only.

### `fabric_adapters/prompt_skill.py` (`prompt_master`)
Role: prompt-master prompt-engineering skill (root SKILL.md + `references/` on demand,
catalogue-allowlisted). Writing family; activates only on explicit request. MIT.

### `fabric_adapters/role_recipes.py`
Role: agency-agents 318 role recipes across 13 divisions. `catalogue` names-only, `recipe`
one file allowlisted against catalogue; no bulk read (NON_NEGOTIABLE 7).

### `fabric_adapters/science_skills.py`
Role: 163 science procedures with *computed* per-skill licence gate (fail-closed; docx/pdf/
pptx/xlsx carry Anthropic no-extract terms). `catalogue/search` cheap, `skill` one file,
`blocked` diagnostic. Licence scan cached.

### `fabric_adapters/scrapling_parse.py`
Role: Scrapling parse-only (`parse/fields/similar/by_text`, ≤200 results). Fetchers
(requests/curl/Playwright) deliberately not installed — `html` required, fetch via
`web_fetch` so egress stays in netguard; no second browser (NON_NEGOTIABLE 11). Adaptive
fingerprints opt-in under `config.DATA_DIR`. Version-pinned health (DEGRADED on drift). BSD-3.

### `fabric_adapters/security_skills.py`
Role: 818 Anthropic security procedures, dual-gated. `catalogue/search` open (index.json,
knowing ≠ doing); `skill` needs `authorized_scope` arg + `risk=restricted` +
`security.authorized_scope` permission. Apache-2.0.

### `fabric_adapters/smartstore_commerce.py`
Role: Smartstore OData v4 fallback commerce (`SMARTSTORE_URL`, Basic `PublicKey:SecretKey`).
Numeric-id guard; `$filter/$top/...` forwarded, `$top` ≤100. Orders read-only (checkout
side-effects). AGPL SIDECAR; Medusa preferred.

### `fabric_adapters/strix_pentest.py`
Role: Strix agentic pentest CLI (`version` open, `test --target --instruction` gated
`security.authorized_scope`; single-argv placeholders prevent injection). Expensive/model.
Apache-2.0; key via env never argv.

### `fabric_adapters/writing_skill.py` (`no_ai_slop`)
Role: no-ai-slop finalisation pass (`guidance/checklist`). Router treats as final step,
not per-sentence rewrite.

---

## 3. `friday/toolsets/` (34)

### `toolsets/__init__.py`
Role: one-line contract — each toolset returns ActionResult.

### `toolsets/adversarial.py`
Role: FR-008 panel (`decision_deliberate`: disagreements preserved, judge UNAVAILABLE →
failed) + FR-012 (`change_review` independent reviewer). Verification cites coverage.

### `toolsets/audio.py`
Role: Windows Core Audio *sessions* (not processes). `audio_sessions/volume/mute`,
`audio_master_volume` (stronger policy, never default reading of "turn it down").
`sessions` via pycaw; reversible via `friday.reversible`; ±2% `close_enough`. Gotchas:
import-safe off Windows (UNSUPPORTED, registry stays up — ubuntu CI lesson); COM
init per-thread in `finally`.

### `toolsets/automations.py`
Role: §14 DAG automations (manual/scheduled), OS owns schedule (`ADAAutomation*` tasks).
`TOOLS` allowlist only (no shell step, no fs writes, no money); `needs` deps, skip-on-fail,
retry ≤3, ≤20 steps; `{{placeholders}}` resolved from context; steps run in-process (no MCP
dependency at 08:00). Gotcha: create validates trigger+graph at save time.

### `toolsets/brain.py`
Role: shared-brain executor face (`recall/remember/entity/forget` via `SharedBrainAdapter`).
`recall` down → OBSERVED miss (never blocks objective); `remember` needs provenance,
`AdmissionRefused` → failed. Domain-prefix binds `brain_recall` here.

### `toolsets/brightness.py`
Role: WMI brightness (`WmiMonitorBrightness*`). Missing instance → `NOT_CONFIGURED`, never
keystroke fallback (unverifiable); floor 10% (never voice-to-dark); `revert_to_policy`
alternative restore. PowerShell, 20s timeout.

### `toolsets/connectors.py`
Role: connector plane executor face (`list/describe/connect/verify/smoke/status/repair`).
`done`→succeeded, `human_step`→cancelled (parks at USER_REQUIRED, no retry burn).

### `toolsets/desktop.py`
Role: mouse/keyboard takeover — most dangerous capability, 3 construction layers:
CONFIRM `DESKTOP_CONTROL` (FULL can't self-approve) + one-shot nonces bound to exact action +
code `forbidden()` (money/credentials/destruction/sys-settings/outward-without-text, no off
switch). Fresh capture every step; `PLAN_TTL` 180s; ≤8 steps; click ≥0.65 confidence;
`desktop_stop` AUTO global flag. Gotcha: prompt refusals aren't refusals — code is.

### `toolsets/documents.py`
Role: read PDF/DOCX/XLSX/PPTX/ZIP that `files_read` calls binary (pypdf, 40k chars, 200 rows,
page/sheet select; scanned-PDF flag). Same fsjail as files. Deliberately no `run` action,
no model summarise inside tool, no audio/video (no ffmpeg). Encrypted PDF without blank
password → `DocumentError`.

### `toolsets/executor.py` (`ada_ask`)
Role: the missing middle — coding agent's question channel. Resolves live dev run from
store (not process-local), classifies via `broker_for`, SUCCEEDED with authority-tagged
answer (persisted as USER vs IMPLEMENTATION decision) or PARTIAL + `WAITING_QUESTION` in DB
(survives restart). No fifth "make something up" outcome.

### `toolsets/files.py`
Role: Phase 1C jailed fs (fsjail first, write-then-read-back verify). Reads AUTO; writes/
recycle ASK; permanent `files_delete` separate (nonce-bound; Friday-owned `ARTIFACTS_DIR`
exempt, else confirmation). Search: 6s/`MAX_HITS`/20k-examined caps, prune
`.venv/node_modules/.git/...` via `os.walk` (rglob can't prune — 300s MCP-ceiling lesson);
reparse-point aware. Gotcha: `status --porcelain` sliced per-line (leading-space bug).

### `toolsets/hardware.py`
Role: Batch 2A physical inventory (battery/disks/displays/network via psutil). Read-only.
Replaces 91 unverifiable pyautogui keystrokes (volume key vs pycaw read-back lesson).
Skips virtual filesystems + empty optical drive.

### `toolsets/identity.py`
Role: which-account resolution (12 answers to "open YouTube"). Named → remembered FACT
preference → last-used → ask-once-and-remember. Preference = personalisation, never
authorisation. Scope `user_device`.

### `toolsets/manifest.py`
Role: capability manifest runtime (`capability_manifest`, read-only, nothing activated).

### `toolsets/media.py`
Role: Phase 1F Spotify without API creds: `WM_APPCOMMAND` to Spotify's own window (not global
keys — browser steals them), verified by title change (idle `spotify free/premium`, ad titles
excluded). No-change → PARTIAL. `play` = search URI + targeted transport.

### `toolsets/memory.py`
Role: Phase 1D durable facts (kind/source/confidence; `spoken_form` hedges INFERENCE so DB
distinction survives speech; supersede never delete). `remember/recall/search/forget/
provenance/export`, project decisions/context/resume, `session_recap` idempotent, scope
`agent_runtime`.

### `toolsets/model_gateway.py`
Role: Hermes MODEL_GATEWAY runtime (`providers` with ledger-evidenced health vs credential
state; `infer` one bounded inference attributed to objective; `usage/pressure/diagnostics/
trace`). Failures are ActionResults, never bare strings.

### `toolsets/music.py`
Role: account-free playback via YouTube+yt-dlp+ffplay (Spotify desktop automation measured
unreliable 0/5). Stream via `android` client first (web 403s), download fallback; evidence =
`ffplay pid alive + exact title`. Moods = query phrasing, no fake audio analysis.

### `toolsets/objectives.py`
Role: Phase 3 durable compiled task graphs. Deterministic `plan_objective` (regex clauses →
capability port; unmapped → FAILED/CAPABILITY_MISSING task, PARTIAL run — never hallucinated);
`start/status/list/pause/resume/cancel/history`. Store-level, lease-free; watchdog observes
at task boundaries. Hard to start, trivial to stop.

### `toolsets/power.py`
Role: lock/sleep/hibernate/shutdown/restart on `LOCAL_MACHINE`. Accepted = `INITIATED`
(ExitWindowsEx/Lock semantics), settled later by boot-identity reconcile — not SUCCEEDED.
No force on ordinary path (own confirmation), no hibernate→sleep substitution, availability
from `SYSTEM_POWER_CAPABILITIES` (not legacy `IsPwrSuspendAllowed`). 30s grace + cancellable
callback; `power_cancel`.

### `toolsets/processes.py`
Role: Batch 2D pid+create_time+image identity (recycled-pid lesson ×3). `close` (WM_CLOSE,
may prompt/decline) vs `terminate` (unconditional, own yes); both wait-then-look. Triple
critical guard (names + pid {0,4} + own chain); `own_chain` never killed. Token re-check
before action.

### `toolsets/products.py`
Role: catalogue pipeline capabilities (transport extraction from MCP adapter; domain in
`products.py`). Store is authority (restart-safe `run_id` continuity + `_active_run_id`
"those" resolution); failed rows = results not transport errors; catalogue+export jailed;
mutating calls refuse to guess (`safe_to_mutate`); FAILED vs `needs_attention` literal.

### `toolsets/profile.py`
Role: user-model runtime over `friday/profile.py` (transport extraction; domain untouched).
`get/explain/learn_from_turn/open_conflicts/resolve_conflict`; writes verified by read-back.

### `toolsets/reminders.py`
Role: Phase 1G OS-scheduled reminders (`ADAReminder*` + SQLite row marked by fire script).
`parse_when` natural language; creation verified by `schtasks /Query` (not returncode);
fired history answerable. Survives exit/sleep/reboot; asyncio timer would not.

### `toolsets/research.py`
Role: FAST (`web_answer` one grounded call ~3s) / RESEARCH (`crawl_all` parallel →
trafilatura markdown, Chromium only when <400 chars) / DEEP (`web_deep_research`
search→crawl→rank→budgeted corpus 3k/18k chars, cited, fingerprinted, deduped).
`ADA_CRAWL_ENGINE` trafilatura default; crawl4ai opt-in (41 pkgs + TLS bump — documented no).

### `toolsets/schedules.py`
Role: FR-041/042 persisted schedules + conditional monitoring on top of automations-style
tasks + objective engine (same ledger/policy). Triggers once/daily/interval/manual; budgets;
ASK-only pre-approvals (never CONFIRM); delivery session/toast/none; conditions
(`always/task_output/task_status/any_failed`) evaluated in code — false → `suppressed` row,
no delivery (no-noise rule). Async `fire()` entry.

### `toolsets/screen.py`
Role: pointing only (fractions not pixels — model sees downscaled frame; tip-on-control
arrow in Friday orange). Below 0.45 confidence draws nothing ("not visible" first-class).
Never moves mouse (that's `desktop`).

### `toolsets/selfdev.py`
Role: FR-047..051 controlled self-dev (`run` OBSERVE→…→BENCHMARKED, never promotes;
`promote` needs human exact-action yes; `rollback/status`). Patch = unified diff applied in
sandbox; thread-locked candidates.

### `toolsets/system.py`
Role: Phase 1A machine state/apps/volume/clipboard. `apps_open` succeeds only on observed
process (Mark-L Popen-truth lesson); provenance-refused web-lifted objectives BLOCKED
(not asked); clipboard with retry; volume via endpoint read-back. `APPROVAL_PREFIX` home.

### `toolsets/vision.py`
Role: Phase 1E camera/screen on demand (open-per-call, 5-frame warmup, 30s staleness cap,
artifact PNG+sha256 per capture). Numeric confidence → status (≥0.75 assert, <0.45 ask for
better view, else hedged PARTIAL — honesty as state machine). Gemini `ADA_VISION_MODEL`,
30s deadline. `locate_in_frame` feeds desktop/screen.

### `toolsets/web.py`
Role: Phase 1B search/fetch/news + Playwright (brave→tavily→duckduckgo recorded in evidence;
stub returns eliminated → NOT_CONFIGURED). Browser not kept running; computer-use loop donor
minus auto-ack of safety confirmations; egress via netguard/sensitive_domains; world/finance
monitors. Gotcha: separate `BROWSER_SCOPE=local_machine` vs `network`.

### `toolsets/windows.py`
Role: Batch 2B window objects (not keystrokes): list/focus/min/max/restore/arrange by
verified state (`isMaximized` read-back). No close (destructive). `pygetwindow` optional —
off Windows answers UNSUPPORTED (registry-survives lesson). 40-window voice cap.

### `toolsets/workbench.py`
Role: generate→validate→serve→iterate sites. Real files under `data/workbench/<name>`
(`SAFE_NAME`), `validate()` before announce, loopback-only lazy server opened in *his*
Chrome (no embedded UI, no `file://` — argv-injection + relative-path reasons).

### `toolsets/youtube.py`
Role: YouTube as data: API (1-unit lists, full stats) preferred, Atom feed fallback (no key/
quota, documented endpoint — current runner since key 403s). `search.list` 100 units → resolve
channel ONCE and remember (`youtube.channel.<slug>`), uploads playlist for recents. Handle/
name resolved via page then remembered (prevents fan-channel drift).
