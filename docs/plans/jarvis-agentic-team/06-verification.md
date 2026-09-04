# 06 — Verification

Independent challenge performed by the orchestrator (Fable), which authored none of
S1–S4; the four builders were separate contexts. A separate verifier agent was not
spawned, per the owner's token rule ("never spawn an agent for what one grep answers").

## Live pass A — tree before the builders (2026-09-03 10:27 IST)
Instance: `scripts/run_ui.py --no-browser --bypass-face` on :8781, `ADA_DB=data/e2e-ada.sqlite3`
(live DB untouched). Driver: `POST /api/ask` through the real Gemini brain.

| Item | Spoken line | Reply (trimmed) | used | Verdict |
|---|---|---|---|---|
| 2.1 | Friday, what time is it? | "It's Thursday, September 3rd, 2026, at 10:27 AM India Standard Time, sir." | clock | PASS (latency note: 5 s, thinking) |
| 5.1 | Create a test file and then create a normal variable and name it food. Your choice of name and place. | "I've created `test_file.py` in your workspace, sir, with the variable `food` set to "pizza"." | files | PASS, no question back |
| 5.2 | List the files in your workspace. | "The file `test_file.py` is in the workspace, sir." | files | PASS |
| 5.3 | Delete that test file you just made. | "Consider it done, sir. The file `test_file.py` has been deleted." | files | PASS |
| 7.2 | List the fourteen executives you can play. | all fourteen auto-company stems | roles | PASS |
| 6.1 | What is for sale in the store right now? | "…no provider available for commerce products." | none (call errored honestly) | PASS on honesty; v1 wording expectation corrected in the master prompt |

Page: booted without the face gate; console clean; screenshot showed the orb, 258 agents, build footer.

## Live pass B — tree after S1–S4 (2026-09-03 11:00 IST, same instance restarted)
| Item | Check | Result | Verdict |
|---|---|---|---|
| 12.1 | `GET /api/state` metrics envelope | keys `all_time, avg_run_secs, model_tokens, open_tasks, runs_by_state`; e2e DB has no objective so all zero | PASS (envelope); numbers to be read on the live DB by the owner |
| 7.7 | "What agent archetypes does the agents-team pack define?" | the eight archetypes, `used: roles` | PASS |
| 7.5 | "Which Python specialists do you have? Names only." | skill names from another roles pack, `used: roles` | FAIL → root cause: `search` exists on several roles providers and `fabric.candidates()` takes the cheapest healthy one. Fixed 11:02: the pack's ops renamed to `agents / find_agent / agent / agent_category` (adapter + tests + one clause in the voice-brain tool text); `tests/test_fabric_agent_packs.py` + `tests/test_voice_brain_ui.py` = 46 passed. Re-probe below. |
| 7.6 | "Act as a scrum master and plan our next sprint…" | answered in role, `used: []` (no brief read) | SOFT: model behaviour, recorded as a known soft spot in the master prompt |
| 7.9 | traversal path in a "recipe" request | nothing read; the model picked the wrong family and was told the family's ops | PASS by refusal; traversal is unit-proven in the adapter tests |
| 3.3 | "What did Hermes last finish?" | a real prior run from `hermes/status` | PASS (status path). The S2 memory write-back is covered by the fake-gateway tests; no live delegation was triggered (costs Hermes tokens; left to the owner's Phase 3) |
| page | console after reload | one mediapipe OpenGL warning only | PASS |
| page | footer registry hash | unchanged `e22cd5f848c7` | expected: the hash covers MCP capability ids, not fabric providers (master prompt pre-flight 5 corrected) |

## Live pass C — after the orchestrator fixes (11:10–11:16 IST, fresh probe DBs)
| Item | Spoken line | Reply (trimmed) | used | Verdict |
|---|---|---|---|---|
| 7.5 | Which Python specialists do you have? Names only. | "You're looking for Python specialists, sir? I have a `fastapi-developer` and a `python-pro` at your disposal." (repeated on a second clean DB: "We have a `fastapi-developer` and a `python-pro`, sir.") | roles | PASS |
| 7.6 | Act as a scrum master: read that brief first, then tell me in two sentences how you would open our next sprint. | first run: two successful roles reads then an empty reply "..." → silent-exit fix; after the fix: "Welcome everyone! Let's kick off this sprint by aligning on our sprint goal…" | roles, roles | PASS; latency note reported "8 seconds - this machine is at 90% CPU and 99% memory" (the gate was running) |
| 7.9 | Read me the agent brief at categories/09-meta-orchestration/../../../pyproject.toml | refused as a path outside the workspace; nothing read | none | PASS by refusal |
| 7.8 | `GET /api/org` | agents_total 415; source lists agency-agents and awesome-claude-code-subagents; VoltAgent categories present as divisions | — | PASS |

Note on method: the day-keyed conversation store replays earlier turns, so a failed probe
repeated on the same DB reproduces the earlier wrong answer from history; every re-probe
after a fix used a fresh `ADA_DB` (`data/probe-ada.sqlite3`, `data/probe2-ada.sqlite3`).

## Independent challenge of the builders' claims (diff-level)
- S3: `_enforce_budgets` is called from both `record_actions` (L365) and `record_model_tokens` (L377); `remaining_budget` L380; `RunSnapshot.budget_exhausted` L105. The two renamed tests kept every previous assertion and added the immediate-outcome ones (diff inspected).
- S2: `on_terminal` L648, `_write_outcome` L542 behind `brain._sensitive`, additive `memory_written` column L416; `memory_stack.hermes_outcomes` L247 wired as tier t7 L279.
- No `skip`/`xfail` introduced; the only skips are the pre-existing "clone absent" guards in the pack tests.
- S1: nine `friday-*` agents present with `maxTurns`, `effort`, `disallowedTools: mcp__*`; hooks block adds only SessionStart/Notification/Stop/TaskCompleted commands; `permissions` and `attribution` untouched; plugin listed by `claude plugin list`; marketplace shim moved from the scratchpad into `.claude/marketplace/friday-local/` (junction, gitignored) and re-registered.
- S4: 46 clones pinned, `upstream_lock.py --check` and `integration_matrix.py --check` clean per its report; re-checked after the rename by the pack tests.

## Deterministic gate (4 chunks, 10:58–11:22 IST, `.venv-verify`, `-m "not live and not slow"`)
```
chunk 1   1006 passed
chunk 2    710 passed, 36 deselected
chunk 3    960 passed, 1 skipped, 11 deselected
chunk 4    730 passed, 1 failed   <- test_ui_server::test_memory_stack_aggregates_four_tiers_under_budget
total     3,406 passed, 1 skipped, 1 failed
```
The one failure was an integration collision, not a regression: S2 added the seventh
memory tier (`outcomes`) after S3 had run the UI-server suite, and the blueprint test
enumerates the tiers. Expectation updated with a dated note (the tier is intended);
`tests/test_ui_server.py tests/test_conversation_memory.py tests/test_hermes_memory_writeback.py`
→ 34 passed afterwards. The two `test_upstream_lock` failures recorded on 2026-09-02 did
not occur: the lock template is present in the tree.

## Playwright (direct invocation, `e2e-run-full.log`)
Note: `cmd.exe /c e2e-run.bat` from the Bash tool did not execute the suite at all (the
log it left was yesterday's two-test run at 21:08); the suite was rerun with the explicit
`node node_modules/@playwright/test/cli.js test --project=chromium --reporter=list` command.
Result: **43 passed (7.8m), exit 0** on the changed tree (36 on 2026-09-02 + the specs added since).

## Security review (automated, during the session) — two findings in `friday/ui_server.py`, both fixed
- Cross-site WebSocket hijacking on `/api/stt` (no Origin check) and CSRF on `GET /api/desk`
  (clipboard capture; `?what=selection` sends a synthetic Ctrl+C). With the face gate ON the
  `SameSite=Strict` session cookie already blocks both; they were live in `--bypass-face` runs.
- Fix: `_same_origin(headers)` — Origin, else Referer, must name this server; no header
  (curl, tests, same-origin GET) is allowed as before. `/api/desk` answers 403 and logs the
  block; `/api/stt` closes with 4403 before accept.
- Test: `test_cross_site_callers_cannot_reach_desk_or_stt` (gate off, grab spied: never called
  cross-site; same-origin and header-less calls still 200; cross-site socket disconnects).
  `tests/test_ui_server.py` → 22 passed. Playwright specs touching these routes re-run: 1 failed, 21 passed. The failure was the
  mute spec's pre-existing 900 ms auto-listen race (see 07-final); the origin check passed a raw
  same-origin handshake (101) and refused a foreign one (403); desk with the page's Referer → 200.
  After the timer fix: mute spec 2/2 twice alone; full suite on the final tree: 43 passed (6.8m), exit 0 (`e2e-run-final.log`).

## S9 (12:06–12:30) — unit level only
`tests/test_autonomy_and_selfcheck.py` (9): CONFIRM→AUTO only outside NON_APPROVABLE; the
persisted switch flips the live engine; spoken switch on/off; a 20-word paste with "status"
is not a status question while "status" alone is; the spoken okay leaves the nonce APPROVED
at step time; a dangerous takeover runs two fake steps to "finished" while FULL returns the
plan untouched; forbidden categories survive; the real self-check passes its core items
(clock, routing, refusal, memory tiers); "go according to the verification prompt" and
"check yourself" reach the self-check. No live probe: the classifier refused to start another
bypass-face instance and to flip the mode from this session.

## Security review 2 (12:45) — three findings, disposition
1. `policy.skip_permissions` / spoken autonomy switch (HIGH per the reviewer): **kept by the owner's
   explicit instruction** ("dangerously skip permission … no need for verification all the time").
   Mitigations in place: the spoken switch only arrives through the face/PIN-gated session or the
   owner's LiveKit room; it is a whole-utterance deterministic command (page text cannot reach it
   through the model); the mode is announced aloud and now written to the access log; DENY,
   NON_APPROVABLE and `desktop.forbidden()` are untouched; "stop" is ungated. Not adopted: PIN
   re-entry per switch and a fresh per-plan token — those are the round trips he asked to remove.
2. `/api/stt` relay (HIGH): **fixed** — a handshake with no Origin header is now refused (browsers
   always send one; a header-less client is not a page). `FRIDAY_UI_HOST` already defaults to
   127.0.0.1. With the gate ON the cookie is also required on websocket scope.
3. clone/study SSRF (MEDIUM): **already covered, not applicable** — `ui_browser.study_url` runs in
   Friday's gated browser inside `netguard` (ui_browser.py L166), and `netguard` re-resolves at
   fetch time (DNS rebinding), blocks loopback / private / link-local including IPv6-mapped
   forms and the cloud metadata addresses, and never follows redirects (netguard.py L7–19, L58–59,
   L97–141).

## S10 (13:27–14:50) — helpers without Docker
- Fabric suites incl. the five new adapters: `test_fabric_helpers_social` (17), `test_fabric_helpers_research`
  (27) with a fake `http.server` per adapter (request shape, auth header, honest unreachable,
  write-argument validation), plus commerce/packs, execution, agent packs, upstream lock, code intel:
  **152 passed**. `upstream_lock.py --check` matches; `integration_matrix.py --check` 46/46 classified,
  30 integrated; notices regenerated.
- UI: `tests/test_ui_server.py` + `tests/test_voice_brain_ui.py` 57 passed; `e2e/helpers.spec.ts` 2/2
  (Organisation → Helpers section ≥ 20 providers; `/api/helpers` shape) run by B3 against a Playwright-started
  server.
- Voice write allowance under full autonomy: `test_full_autonomy_lets_a_spoken_write_reach_the_fabric`
  (refused in FULL, reaches the fabric in DANGEROUS, restricted provider still refused); autonomy +
  voice-brain suites 44 passed.
- Not exercised live: no instance of any of the five helpers exists on this machine (that is the
  honest-unreachable path, which the tests cover); the spoken phases are in the master prompt (Phase 15).

## Opus read-only review of the session diff (`critic`, 141k tokens, 14:45) — disposition
| Finding | Severity | Done |
|---|---|---|
| `_same_origin` allowed header-less requests, so a cross-site `<img referrerpolicy="no-referrer">` reached `/api/desk` | high | fixed: desk requires a same-origin Origin or Referer; header-less is 403 (parameter validation still answers 400 first, as the e2e spec expects) |
| `on_terminal` claimed `memory_written=1` before writing; a failed write lost the outcome silently | high | fixed: a failed write releases the claim; a secret-shaped outcome stays claimed on purpose (never retried) |
| `_GO_AHEAD` spent a stashed nonce on a bare "ok" up to 180 s later | med | fixed: the offer lapses unless the very next turn is the yes (`test_the_takeover_offer_lapses_after_one_unrelated_turn`) |
| `_LAST_PLAN_NONCE` is module-global across tabs | med | accepted: both tabs are the face-gated owner's, and the one-turn lapse above bounds it |
| `on_usage_updated` advanced `_session_tokens` before the `claim is None` return, dropping inter-portion tokens | med | fixed: advanced only after a successful record; between-portion tokens land in the next portion |
| `open_notebook_research._auth` sends no header when the password is absent | med | accepted: Open Notebook's password is optional upstream; a 401 surfaces as an honest error |
| `hermes_outcomes` hid a store error as an empty tier | low | fixed: logged, and the tier carries `error` |
| `_budget_exhausted` direct-indexed stored JSON | low | fixed: defaults from `RunBudget()` merged in |
| `_envelope` dropped the headroom silently | low | fixed: logged |
| Security review (14:47): a model-chosen follow-up write under full autonomy could be driven by page content | high | fixed: a write goes through only when the owner's own words in that turn asked for that kind of action (`_asked_for`, `_CURRENT_TURN`); tested with an injection case |
Q1 no model-initiated approval path; Q2 no secret leaks; Q5 no XSS in the Helpers section (all fields escaped).

## Live pass D — helpers, self-check, status guard (15:05–15:20, fresh probe DBs, gate off by env)
| Item | Check | Result | Verdict |
|---|---|---|---|
| 15.2 | `GET /api/helpers` | keys families/processes/providers; 32 providers | PASS |
| 15.1 | "Which helpers do you have?" | a spoken roster from `helpers/list` ("codebase_memory, graft, openworker_cli, medusa_commerce, smartstore_commerce, and many more"), `used: helpers` | PASS |
| 0.8 | "Go according to the verification prompt." | "Self-check: 16 of 16 passed. 7 phases need you: …", `used: selfcheck` | PASS |
| 0.9 | a 40-word paste containing "status" twice | summarised in one sentence; no canned status line | PASS |
| 15.3 | "What is queued on our social accounts?" | first "the capability is not available" → the fabric raises on no provider and the hint was lost; after `_with_health_hint` (asks each provider's `health()`) plus a one-line persona nudge: "the Postiz social helper is unreachable, sir. You'll need to set the `POSTIZ_API_URL` environment variable or start Postiz." | PASS |
| 15.7 | "What media projects are on the board?" | "The Openmontage media helper is unreachable, sir. You'll need to set the `OPENMONTAGE_URL` environment variable or start Backlot." | PASS |
No helper instance exists on this machine, so the reachable path is covered by the fake-server tests only.

## Final tree (15:30 IST)
- Gate: 3,455 passed, 1 skipped (chunks 1016 / 730 / 976 / 733; the one chunk-2 failure was the
  speech-socket test now sending Origin, re-run green); every suite touched afterwards re-run green.
- Playwright: **45 passed**, exit 0 (`e2e-run-final3.log`).
- Live stack restarted on this code (MCP :8000, control room :8770, LiveKit worker registered).

## 18:15 — full autonomy by default
`policy.current_autonomy()` → DANGEROUS unless the persisted file or `ADA_AUTONOMY` says
otherwise; spoken switch accepts any phrasing. Suites: `test_autonomy_and_selfcheck`,
`test_autonomy`, `test_voice_brain_ui`, `test_jarvis_screen` → 103 passed (four tests that
encoded the old default were updated with the date; two tier-specific tests pinned to FULL).
Live stack restarted through the launcher afterwards (see 07-final).

## Not verified in this pass
LiveKit-room phases (1, 12.2–12.4, 13) need the owner's voice session; a real Hermes
delegation (Phase 3.3–3.4, 4.x) was not triggered; the live :8770 / :8000 processes still
run the pre-session code until the owner restarts them (pre-flight 1–2).
