# 05 — Slices

Vertical, one capability each, verified before the next. Builders append their
report under their slice heading (≤ 250 words: what changed with file:line, exact
test commands and counts, what could not be done).

| # | Slice | Tracer bullet | Verification |
|---|---|---|---|
| S1 | Team: packs added to the owner's Claude Code; `.claude/` team generated and linted | `scaffold.py` exit 0 with every agent ≥ B | lint grades table in `team.md`; `ls ~/.claude/agents \| wc -l` delta; plugin list |
| S2 | Hermes outcome → shared memory | one COMPLETE run through `fake_hermes_gateway` leaves an outcome both readers see | 3 new tests fail-before / pass-after; `tests/test_hermes_*.py` green |
| S3 | Portion budget enforced; header honest | 33k tokens on a 32k portion → exhausted + checkpoint; `/api/state` scoped | continuity + ui_server tests; Playwright envelope spec |
| S4 | Packs pinned and reachable by Friday | `roles/recipe` on one VoltAgent brief via `fabric.call_with_fallback` | new adapter tests; `upstream_lock.py --check`; `integration_matrix.py --check` |
| S5 | Master validation prompt v2 | the owner can run every phase on both paths | reviewed against v1 + this plan |
| S6 | Live validation | Playwright suite on the changed tree; Claude-in-Chrome pass on :8781 | `e2e-run.log`; Chrome console/network clean |
| S7 | Independent verification | Opus verifier in a fresh context attempts to disprove S1–S6 | `06-verification.md` |

## Reports
(appended by builders)

### S1 — Team installed and generated

VoltAgent global install: 49 copied, 8 skipped (collision: frontend-developer, code-reviewer, compliance-auditor, debugger, penetration-tester, ai-engineer, prompt-engineer, technical-writer). Per-model: sonnet 30, haiku 10, opus 9. Description-token estimate: 236 files/49,311 chars (~12,327 tok) before to 285 files/57,586 chars (~14,396 tok) after, delta ~2,068 tok (owner's <=2,500 cap held). Every copied file got disallowedTools: mcp__*, maxTurns, effort, and a VoltAgent@009544a source comment.

Plugin: bare `claude plugin install <path>` and `marketplace add <path>` both failed (pinned clone ships plugin.json but no marketplace.json; a marketplace source must start with "./"). Fixed via a scratchpad marketplace shim + directory junction, no pinned-clone edits. Final: `claude plugin install agents-team@friday-local -y` -> "Successfully installed plugin: agents-team@friday-local (scope: user)", cached independently at ~/.claude/plugins/cache/friday-local/agents-team.

Team: .claude/team.json -> scaffold.py --min-grade B. First run: friday-orch and friday-security-engineer scored A/94 verdict reject (two anti-pattern rules tripped by wording). Reworded team.json, re-ran: exit 0, all 9 agents A/100/ship. Applied token-discipline pass (maxTurns/effort/disallowedTools/cacheTtl, 5-line Budget block, rules-14/15 reference, tech-lead lost Agent tool) and re-linted: unchanged A/100/ship.

Hooks wired in .claude/settings.json: session-init.sh (SessionStart), notify.sh (Notification/Stop/TaskCompleted). Staged unwired in .claude/hooks/: teammate-idle-gate.sh, post-commit-check.sh. settings.json backed up to .claude/settings.json.bak-2026-09-03 (gitignored); diff confirms permissions/attribution byte-identical, only hooks key added.

Not done: ~101 remaining VoltAgent agents left uninstalled (token cap; reachable via roles/search or install-agents.sh); friday-local marketplace registration left in place (removal command documented in team.md).

### S2 — Hermes memory write-back

Choke point: `WorkRunLog.update()` (friday/hermes_bridge.py:626) — every
status write (delegate/event-handler/interrupt/cancel) already funnels
through it, so hooking there covers every terminal site without hunting
each caller. Added: `TERMINAL` tuple (L111); `_MEMORY_COLUMN`/
`memory_written` additive column (L416, migrated like `_ORIGIN_COLUMN`);
`_write_outcome()` (L542); `WorkRunLog.on_terminal()` (L648) — idempotent
via atomic `UPDATE ... WHERE memory_written=0` (mirrors `claim_delivery`),
never raises, logs and returns on any failure.

Writes: `render_completion(record)[:600]` (existing formatter, reused)
through `brain._sensitive()`; if flagged, logged and refused, nothing
written. Else two existing-API writes: `store.add_message(voice_brain
.conversation_id(), "assistant", summary)` (voice path — `_recent_turns()`
/`_memory_context()` see it) and `store.record_decision("hermes", summary,
source=f"hermes:{work_run_id}")` (durable row). No existing tier reads
`project_decisions`, so per 04-program-design.md's fallback I added one:
`memory_stack.hermes_outcomes()` (L247) — recency-based like `episodes()`,
not keyword-scored — reading `store.decisions("hermes")`, wired into
`aggregate()` unconditionally so it survives `include_episodes=False`, at
high priority so it isn't the first thing budget-dropped.

Skipped `log_result()`'s vault page: `vault.VAULT` resolves once from env
at import, so it isn't test-isolable, and neither the 5 required tests nor
the stated GOAL need it.

Tests: `tests/test_hermes_memory_writeback.py`, 6 tests (added one
fake-gateway e2e per 05-slices.md's tracer bullet). Before: 5 failed
(`assert False` / `AttributeError: 'WorkRunLog' object has no attribute
'on_terminal'`), 1 (secret-shape) vacuously passed. After:
`pytest tests/test_hermes_bridge.py tests/test_hermes_delivery.py
tests/test_conversation_memory.py tests/test_memory_provenance.py
tests/test_hermes_memory_writeback.py -q -m "not live and not slow"` →
64 passed, 11 deselected.

### S3 — Portion budget + honest metrics

**Changed.** `friday/continuity.py`: `RunSnapshot.budget_exhausted: str = ''` (L105),
derived in `status()` (L166); `claim_run` clears the marker per portion (L242);
`_enforce_budgets(conn, claim, now)` (L396) called at the end of `record_actions`
(L365) and `record_model_tokens` (L377); `remaining_budget(claim)` (L380).
`friday/continuity_livekit.py`: `on_usage_updated` stops the portion (L219-229);
`_envelope` is now an instance method carrying the headroom line (L378-388).
`friday/ui_server.py`: `_metrics(conn, run_id=None)` + `all_time` (L390-422),
`build_state` passes the objective run id (L464, L477). `ui/index.html` L788, L1186.
Tests: `tests/test_continuity.py` (3 new; 2 superseded next-claim tests renamed to
`..._when_the_spend_lands`, assertions kept and strengthened),
`tests/test_ui_server.py` (1 new).

**Seam.** Enforcement lives in one private method called by both record paths, so
every caller (LiveKit usage events, `runtime_control._record_action` L302) is covered
by one guard. Total budget crossed -> existing `_finish_budget` immediately; portion
crossed -> one `budget_exhausted` event (`"portion:model_tokens 33000/32000"`) plus a
marker in the existing `run_controls.counters` JSON (no schema change), cleared at the
next claim. LiveKit reuses the `on_close` pattern: `checkpoint(..., WakeCondition.immediate(...))`
then `_deactivate`; a run finished by its total budget gets `_deactivate` only (terminal
runs must not be handed a wake).

**Steering: wired, not hooked.** `_envelope(claim)` is the real seam — it is the
`user_input` of `session.generate_reply` in `pump_once` (L255). It now ends with
"Remaining budget: N model tokens and N tool actions in this portion; ...", wrapped in
try/except so sizing can never break a wake.

**Token scoping join.** `open_tasks` is scoped by `objective_tasks.run_id = objective_runs.run_id`
(verified on the live DB read-only: 340/340 tasks join). `run_portions.run_id` is the
CONTINUITY id space and never overlaps `objective_runs` (0/50 join) — there is no join
to scope tokens by, so scoped `model_tokens` is 0 in production today and the header
now labels the real number "tokens all-time" from `all_time.model_tokens`, with
"tasks open (this objective)" beside it. `build_state` envelope keys unchanged; no e2e
selector touches those strings (`grep` of `e2e/*.ts`: only `budget_tokens`, unrelated).

**Tests.** `.venv-verify/Scripts/python.exe -m pytest tests/test_continuity.py
tests/test_continuity_livekit.py tests/test_continuity_fresh_db.py
tests/test_objective_continuity.py tests/test_ui_server.py -q -m "not live and not slow"
-p no:cacheprovider`. Before (new tests only): 4 failed —
`AttributeError: 'RunSnapshot' object has no attribute 'budget_exhausted'`;
`assert 'working' == 'partial'`;
`AttributeError: 'ContinuityManager' object has no attribute 'remaining_budget'`;
`TypeError: _metrics() takes 1 positional argument but 2 were given`.
After: **73 passed**.

**Not done.** Portion `max_elapsed_seconds` is still only checked at the next claim
(record-time wall-clock enforcement would flip runs on a live clock; out of scope).
`runtime_control._record_action` discards the returned snapshot, so an action-budget
stop still relies on the existing `_speech_done` action check plus a `StaleClaim` on the
next tool call. Playwright not run (S6 owns it).

### S4 — Packs pinned as fabric roles

**Files.** `friday/fabric_adapters/claude_subagents.py` (new, 136 lines): family
`roles`, mode `SKILL`, upstream `awesome-claude-code-subagents`; operations
`catalogue`/`search`/`recipe`/`category`, all declared `open_operations`.
`friday/fabric_adapters/agents_team_pack.py` (new, 95 lines): family `roles`,
mode `SKILL`, upstream `agents-team`; operations
`archetypes`/`archetype`/`rules`/`rule`/`skill`, all open. `friday/org.py`:
`VOLT_UPSTREAM` (L36), `_load_agency_divisions` (L57, the old
`_load_divisions` body unchanged), `_load_voltagent_divisions` (L90, new —
category dirs as divisions, frontmatter name/description, numeric prefix
stripped for the label), `_load_divisions` (L126, now
`_load_agency_divisions() + _load_voltagent_divisions()` — agency-agents
always first), `_sources`/`state()["source"]` (L248-267, lists both upstream
paths when both contributed, else one, else `"unavailable"`).
`scripts/upstream_lock.py` REVISED dict (L302, L313): both upstreams, mode
SKILL + why. `scripts/new_upstream_set.py`: REQUESTED (+2 URLs, L113-114),
`EXPECTED_NEW` 23→25 (L128), DECISIONS (+2 entries, L383, L392) — needed
because `docs/integrations/NEW_UPSTREAM_SET.json` is derived strictly from
`REQUESTED`. `tests/test_fabric_agent_packs.py` (new, 13 tests).

**Pins.** `agents-team` (fadymondy, MIT) @
`7f2f83927109dfac878dc78a53f27925f083aaeb`. `awesome-claude-code-subagents`
(VoltAgent, MIT) @ `009544a05267426b3896c77230177967f99f6360`. Both LICENSE
files read directly at the clone root — plain MIT grant text, no NOTICE, no
carve-out.

**Regeneration.** `.venv/Scripts/python.exe scripts/new_upstream_set.py
--audit` → `wrote ... NEW_UPSTREAM_SET.json (25/25 cloned)` (was 23/23).
`scripts/upstream_lock.py` → `wrote ... UPSTREAM_LOCK.json (46 cloned, 46
total)` (same 2 pre-existing LICENSE WARNINGs: strix, anythingllm — unrelated
to this slice); `--check` → `matches the clones`. `scripts/integration_matrix.py`
→ `wrote ... INTEGRATION_STATUS.md (46 clones, 25 integrated, 0
unclassified)`; `--check` → `integration matrix: 46 clones, all classified.`
`scripts/third_party_notices.py` → `wrote ... THIRD_PARTY_NOTICES.md (46
upstreams)`. Both new upstreams show `INTEGRATED` / `SKILL` in both generated
docs.

**Tests.** `.venv-verify/Scripts/python.exe -m pytest
tests/test_fabric_agent_packs.py tests/test_fabric_commerce_and_packs.py
tests/test_orgplane.py tests/test_fabric_code_intel.py
tests/test_upstream_lock.py -q -m "not live and not slow" -p no:cacheprovider`.
Before (the new test file did not exist yet; the other four alone): 81
passed, **1** failed — `test_no_upstream_was_staged_without_being_requested`:
`staged but never requested: ['agents-team', 'awesome-claude-code-subagents']`
— 2 deselected. This is **not** the two deleted-TEMPLATE failures the brief
anticipated: `Friday Stark Demo Main/06_schemas/UPSTREAM_LOCK_TEMPLATE.json`
is present and untouched in this checkout, and every test that reads it
passed at baseline. The one real baseline failure was this slice's own two
clones being unrequested, so fixing it — adding both to `REQUESTED` — is the
"add them the same way" instruction, not the forbidden fix. After: **95
passed**, 0 failed, 2 deselected (13 new + the 1 prior failure now passing).

**Not done.** No REFERENCE_ONLY→SKILL reversal was invented for
`awesome-claude-code-subagents`; it read as SKILL on the first pass (no code,
no generator), so its REVISED entry documents the audit rather than a
change of mind — `agents-team`'s entry is the genuine reversal (plugin vs.
its markdown content). VoltAgent category labels are plain
`re.sub(r"^\d+-","",id).title()` (e.g. "Data Ai" for `05-data-ai`), matching
the existing agency-agents convention (`key.title()`) rather than a
hand-tuned name table.


### S6a — Orchestrator fixes found by the live pass (Fable, 11:00–11:15)

1. **Roles op collision.** `roles/search` on the new VoltAgent pack was never reached:
   `fabric.candidates()` filters by `operation in p.operations` and then ranks by cost /
   health / fabric_memory, so another roles provider that also declares `search` answered
   "which Python specialists" with its own skill names. Fix: the pack's ops are now unique
   in the family — `agents`, `find_agent`, `agent`, `agent_category`
   (`friday/fabric_adapters/claude_subagents.py` L84–L122, tests renamed in
   `tests/test_fabric_agent_packs.py`, one clause added to the `use_capability` tool text in
   `friday/voice_brain.py`). Live re-probe: "fastapi-developer and python-pro", `used: roles`.
2. **Brief unreadable by name.** `find_agent` returns names but `agent` demanded the exact
   catalogue path, so the model could never read what it found. `agent` now resolves a
   bare name or stem to its unique catalogue path; traversal and unknown names still fail
   with "not in this pack's catalogue" (`test_agent_resolves_a_bare_name_to_its_catalogue_path`).
3. **Silent exit.** After two successful roles reads the reply was "..." — the tool loop
   stops after `_MAX_TOOL_ROUNDS` and speaks whatever text is left, which can be none.
   `voice_brain.reply` now makes one tool-free follow-up call ("Answer now … from what you
   already found") before ever falling back to a sentence that admits the loss
   (`test_reply_speaks_even_when_the_tool_loop_ends_without_words`, fake client,
   asserts `_MAX_TOOL_ROUNDS + 2` calls). `tests/test_voice_brain_ui.py`: 34 passed.
4. **Master prompt corrections** from evidence: pre-flight 5 (the registry hash covers MCP
   capability ids, not fabric providers; prove the fabric via `/api/org`), 6.1 wording,
   7.5/7.6/7.9 op names and a known soft spot (the model may answer in role without reading).
5. **Auto-listen race.** `ui/index.html`: the unlock path arms a 900 ms `startListening()`
   timer; muting inside that window was undone when it fired (surfaced by running the mute
   spec alone, fast). `stopListening` now clears `AUTO_LISTEN_T`. Two-line change, no new test:
   the existing `mute-and-progress.spec.ts` is the regression test and now passes in both the
   fast and the slow timing.

### S9 — Full autonomy, self-check, and the canned-status hijack (Fable, 12:06–12:30)

Owner, 12:06–12:07: a pasted block of the master prompt and a file path each got the canned
"Online. GBrain unavailable…" line; "go according to the verification prompt" got a
clarifying question; then "I do not need it to say okay … dangerously skip permission …
autonomously working".

1. **Status hijack.** `_try_command`'s status regex matched the word "status" anywhere;
   it now fires only on utterances of ≤ 12 words (`test_a_pasted_page_mentioning_status_is_not_a_status_question`).
2. **The spoken okay never approved anything.** `desktop_step` consumes a nonce that must be
   APPROVED, and only `confirmation.book.approve()` sets that; nothing in the voice path
   called it. `reply()`'s go-ahead branch now approves the last plan's nonce before stepping —
   there, on the owner's words, not inside `_run_desktop`, so a `desktop/step` the model calls
   on its own still meets "not approved" (`test_the_spoken_okay_approves_the_pending_plan`).
3. **`policy.DANGEROUS`** ("dangerously skip permissions"): CONFIRM → AUTO for every category
   outside `NON_APPROVABLE`; DENY and `desktop.forbidden()` (credentials, money, destroying
   data, security settings) untouched. Persisted in `data/autonomy.json` by the spoken switch
   ("full autonomy on|off", "skip permissions on|off"); `policy.current_autonomy()` reads file →
   env → FULL; `PolicyEngine.set_autonomy()` re-resolves the live engine
   (`test_dangerous_answers_confirm_but_never_the_non_approvable`, `test_set_autonomy_persists…`,
   `test_forbidden_categories_survive_dangerous_mode`).
4. **Takeover without a yes.** `desktop_plan` approves its own nonce in dangerous mode and
   returns `autorun`; new `desktop_takeover()` runs the steps to the end, one capture per step,
   stopping on finished/stopped/refused/cannot_see/focus_moved. Used by the MCP `desktop_plan`
   tool and by `voice_brain._run_desktop("plan")` when the mode is on; persona and tool text
   say "never ask for a yes" in that mode (`test_dangerous_takeover_runs_the_plan_without_a_yes`).
5. **Self-check.** `friday/selfcheck.py`: the automatable half of the master prompt as 16
   in-process checks (clock, files round trip, executives, find_agent, archetypes, traversal,
   commerce honesty, bundle-carries-memory-not-transcript, economy/deep routing, hermes/status,
   metrics envelope, organisation, outcomes tier, credential refusal, autonomy mode) plus the
   "needs you" list. Reachable as `selfcheck/run` and deterministically from "go according to
   the verification prompt" / "check yourself" (`test_selfcheck_runs_the_automatable_half`,
   `test_go_according_to_the_prompt_runs_the_selfcheck`).
6. The mode switch itself was NOT flipped from this session (the tool classifier refused a
   permission-skipping change made by an agent); the owner turns it on with one sentence.

### S10 — Every web-app upstream connected as a helper, without Docker (13:27–14:50)

Owner: "connect all the github codes like a helper … build everything … without docker …
autonomously". Docker is absent here, so each helper is a remote HTTP SIDECAR the way the
commerce helpers already are (`owns_process=False`, `<NAME>_URL` env, a secret alias, open
reads, gated writes, honest "unreachable, set <ENV>"). Three builders on disjoint files, then
the orchestrator regenerated the lock, matrix and notices.

| Helper | Adapter (family) | Open reads | Write (gated) | Env / secret |
|---|---|---|---|---|
| postiz (AGPL) | `postiz_social` (social) | integrations, queue, status | schedule → `social.publish` | `POSTIZ_API_URL` / `postiz_api_key` |
| anythingllm (MIT + AGPL subtree) | `anythingllm_research` (research) | workspaces, ask, documents | — | `ANYTHINGLLM_URL` / `anythingllm_api_key` |
| open-notebook (MIT) | `open_notebook_research` (research) | notebooks, notebook, ask | add_source → `research.write` | `OPEN_NOTEBOOK_URL` / `open_notebook_password` |
| maxun (AGPL) | `maxun_scraping` (scraping) | robots, runs, results | run_robot → `scraping.run` | `MAXUN_API_URL` / `maxun_api_key` |
| openmontage (AGPL) | `openmontage_media` (media) | projects, project | — (Backlot has no write route) | `OPENMONTAGE_URL` |

- B1 (`python-pro`, 56k tokens): postiz + anythingllm, 17 fake-server tests.
- B2 (`python-pro`, 107k): open-notebook + maxun + openmontage (its Backlot FastAPI server at
  the pin: `backlot/server.py`, port 4750), 27 tests.
- B3 (`fullstack-developer`, 102k): `GET /api/helpers` (providers, families, processes; 10 s
  cache), a Helpers section in the Organisation view, `helpers/list` by voice, `e2e/helpers.spec.ts`
  (2 passed), +1 UI-server test.
- Orchestrator: lock decisions rewritten for the five (built, not deferred); lock regenerated and
  `--check` matches; matrix 46 clones / **30 integrated** (was 25) / 0 unclassified; notices for 46;
  fabric suites 152 passed. Voice path: under full autonomy a spoken write (schedule a post, run
  a robot, add a source) now reaches the fabric instead of "needs the boss's go-ahead"; restricted
  providers stay refused (`test_full_autonomy_lets_a_spoken_write_reach_the_fabric`).
- The remaining 16 reference-only clones stay so on purpose: they would duplicate Friday's one
  browser (browser-use, nodriver), one orchestrator (crewai, openhands, openwork, cline-as-fabric),
  one memory (agentmemory) or are reading material (awesome-harness-engineering, munder-difflin,
  ultron, vane, firstmate, pipecat) or app builders Friday replaces with Hermes (bolt.diy, onlook,
  open-lovable).
