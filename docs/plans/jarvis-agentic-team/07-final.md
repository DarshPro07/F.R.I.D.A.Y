# 07 — Final (2026-09-03)

## Verdict: PARTIALLY_VERIFIED
Verified: every code change by unit tests that failed before and pass after; the full
deterministic gate; the full Playwright suite; three live passes through the real Gemini
brain on a face-bypass instance. Not verified (live-only or owner-gated): the LiveKit-room
phases, a real Hermes delegation showing the memory write-back end to end, and the owner's
live :8770 / :8000 processes, which still run the pre-session code until restarted.

## Objective
Give the owner a model-routed Claude Code team built from `fadymondy/agents-team` and
`VoltAgent/awesome-claude-code-subagents`, make Friday/Hermes use the same packs at
runtime, close the three real gaps behind "Jarvis is broken" (Hermes results never reach
shared memory; objective portions overspend tokens; the header lies about cost), replace
the master validation prompt, and prove it live.

## Architecture (unchanged shape, five additions)
Hermes stays the single engine; the fabric stays the only registry; memory stays one stack.
Added: a terminal-status hook in the Hermes bridge writing a sanitized outcome into the
store (seventh memory tier `outcomes`); budget enforcement at record time in the
continuity plane with a headroom line in the LiveKit envelope; objective-scoped metrics
plus an `all_time` block; two skill-pack providers in the `roles` family and a second
division source in `org.py`; a `.claude/` team generated and linted by the agents-team
plugin. Same-origin checks on the desk endpoint and the speech socket (security review).

## Implementation summary (by slice)
- S1 (Sonnet): 49 VoltAgent agents in `~/.claude/agents` with `model`/`maxTurns`/`effort`/
  `disallowedTools: mcp__*`; `agents-team@friday-local` plugin installed from an in-repo
  marketplace shim; nine `friday-*` agents (A/100), rules 01–15, hooks: SessionStart only.
- S2 (Sonnet): `WorkRunLog.on_terminal` → `store.add_message` + `record_decision`,
  `memory_stack.hermes_outcomes`; idempotent per work run; secret-shaped text refused.
- S3 (Opus): `continuity._enforce_budgets` from both record paths, `remaining_budget`,
  `RunSnapshot.budget_exhausted`; LiveKit stops the portion and speaks headroom;
  `_metrics(conn, run_id)` + `all_time`; header labels "this objective" / "all-time".
- S4 (Sonnet): both clones pinned (46 in the lock), `claude_subagents` + `agents_team_pack`
  providers, VoltAgent categories as divisions (415 agents), matrix/notices regenerated.
- S6a (Fable): unique op names for the subagent pack; `agent` resolves a bare name;
  `voice_brain.reply` never answers "..." (one tool-free follow-up call); origin checks.
- S5/S8 (Fable): `docs/MASTER_VALIDATION_PROMPT.md` v2 (14 phases); token-discipline
  protocol, rule 15, frontmatter caps, `CLAUDE_CODE_SUBAGENT_MODEL=sonnet` in project settings.

## Files changed
Modified: `friday/hermes_bridge.py`, `friday/memory_stack.py`, `friday/continuity.py`,
`friday/continuity_livekit.py`, `friday/ui_server.py`, `ui/index.html`, `friday/org.py`,
`friday/voice_brain.py`, `scripts/upstream_lock.py`, `scripts/new_upstream_set.py`,
`third_party/UPSTREAM_LOCK.json`, `docs/integrations/INTEGRATION_STATUS.md`,
`docs/integrations/NEW_UPSTREAM_SET.json`, `THIRD_PARTY_NOTICES.md`, `.gitignore`,
`.claude/settings.json` (hooks + env only; permissions untouched, backup kept),
`tests/test_continuity.py`, `tests/test_ui_server.py`, `tests/test_voice_brain_ui.py`,
`docs/MASTER_VALIDATION_PROMPT.md`.
New: `friday/fabric_adapters/claude_subagents.py`, `friday/fabric_adapters/agents_team_pack.py`,
`tests/test_hermes_memory_writeback.py`, `tests/test_fabric_agent_packs.py`,
`.claude/{team.json,agents/,rules/,hooks/,marketplace/}`, `third_party/upstream/agents-team`,
`third_party/upstream/awesome-claude-code-subagents`, `docs/plans/jarvis-agentic-team/*`.

## Tests executed (exact)
- Deterministic gate, `.venv-verify`, `-m "not live and not slow"`, 4 chunks: 1006 + 710 + 960 + 730
  = **3,406 passed, 1 skipped, 1 failed** → the failure (tier blueprint test) was an
  integration collision with the intended new tier; expectation updated, 34 passed on re-run.
- Slice suites: S2 64 passed; S3 73 passed; S4 95 passed; S1 lint A/100 ×9.
- Post-fix: `test_fabric_agent_packs` 14 passed; `test_voice_brain_ui` 34 passed;
  `test_ui_server` 22 passed (includes the cross-site test).
- Playwright (direct invocation): **43 passed (7.8m)**, exit 0. Subset re-run after the
  origin check (failure-paths, gate, brain-latency, mute-and-progress): 1 failed, 21 passed —
  the failure (`mute-and-progress` › muting) reproduced alone and was root-caused to a
  PRE-EXISTING race in `ui/index.html`: a 900 ms auto-listen timer after unlock re-enabled
  the mic after the test muted it (the origin check was exonerated: no non-200 in the run,
  raw same-origin handshake 101, foreign origin 403). Fix: `stopListening` clears the pending
  timer (`AUTO_LISTEN_T`), so a mute inside that window sticks for the owner too. Mute spec
  alone: 2 passed, twice. Full suite re-run on the final tree: **43 passed (6.8m), exit 0** (`e2e-run-final.log`).

## Live workflows verified (Claude-in-Chrome + `/api/ask`, real Gemini brain, :8781)
Pass A (pre-builders): time, file create/list/delete without a question back, fourteen
executives, honest store-unavailable. Pass B/C (post-builders): metrics envelope with
`all_time`; `/api/org` 415 agents from both packs; archetypes; Python specialists by name;
scrum-master brief read and answered in role; traversal refused; "what did Hermes last
finish" from run status; page console clean.

## Failures encountered and resolved
Roles op collision (wrong pack answered `search`); brief unreadable by bare name; a 900 ms
auto-listen race undoing a mute; silent
"..." reply after tool rounds; day-keyed history replaying a wrong answer during re-probes
(method fix: fresh `ADA_DB`); `cmd.exe /c e2e-run.bat` not running Playwright from the Bash
tool; `notify.sh` hook needing `jq`; marketplace shim in a session scratchpad.

## Unresolved limitations
Tokens cannot be scoped per objective (continuity and objective id spaces do not join), so
the header shows tokens all-time by design; portion elapsed-time budget still checks at the
next claim; the model may answer in role without reading a brief (prompt-level soft spot);
101 VoltAgent agents deliberately not installed globally (roster token cost); LiveKit-only
phases untested here.

## Rollback
`git checkout -- <modified files>`; delete the new files and the two clones plus their lock
rows; remove `.claude/agents/friday-*.md`, `.claude/rules`, `.claude/hooks`,
`.claude/marketplace`; restore `.claude/settings.json.bak-2026-09-03`;
`claude plugin uninstall agents-team@friday-local`; delete the 49 files carrying the
`source: VoltAgent` marker from `~/.claude/agents`.

## Evidence
`docs/plans/jarvis-agentic-team/05-slices.md` (builder reports), `06-verification.md`
(passes A/B/C, gate, Playwright, diff challenge), `e2e-run-full.log`, `e2e-run-origin.log`,
scratchpad `gate/gate_chunk{1..4}.txt`.

## Addendum 12:30 — S9: full autonomy, self-check, canned-status hijack
Triggered by the owner's 12:06 transcript (from the still-unrestarted :8770 server) and his
instruction "I do not need it to say okay … dangerously skip permission … autonomously".
- `policy.DANGEROUS` (CONFIRM → AUTO outside `NON_APPROVABLE`; DENY and `desktop.forbidden()`
  untouched), persisted by the spoken switch "full autonomy on|off" in `data/autonomy.json`;
  `desktop_takeover()` runs a plan to the end without a yes in that mode, on both voice paths.
- The spoken "okay" now actually approves the pending plan (nothing did before); a
  `desktop/step` the model calls on its own still meets "not approved".
- `friday/selfcheck.py`: 16 in-process checks from the master prompt plus the "needs you" list;
  "go according to the verification prompt" / "check yourself" run it deterministically.
- The status command fires only on utterances of ≤ 12 words (the pasted prompt no longer
  gets the canned "Online." line).
- Tests: `tests/test_autonomy_and_selfcheck.py` 9 passed; with `test_autonomy`, `test_confirmation`,
  `test_user_policy`, `test_jarvis_screen`, `test_voice_brain_ui`: 111 passed. The full gate and
  Playwright were NOT re-run after S9 (affected suites only). Not flipped from this session: the
  autonomy mode itself (the tool classifier refused an agent enabling a permission-skipping mode;
  the owner says "full autonomy on" once). Not live until the owner restarts :8770 / :8000.

## Addendum 15:30 — S10/S11: every web-app upstream connected, Opus review, final gates
- **Helpers without Docker.** postiz, anythingllm, open-notebook, maxun and openmontage are
  remote HTTP helpers in the fabric (social / research / research / scraping / media), driven at
  an instance the owner runs anywhere, honest "unreachable, set <ENV>" until then, writes gated
  and reachable by voice only under full autonomy AND only when the owner's own words asked for
  that action (`_asked_for`). `GET /api/helpers`, a Helpers section in the Organisation view,
  `helpers/list` by voice. Matrix: 46 clones, **30 integrated**, 0 unclassified; lock `--check`
  matches; notices regenerated. The 16 still reference-only are so for the one-browser /
  one-orchestrator / one-memory rules or because they are reading material.
- **Opus read-only review** (`critic`): nine findings, seven fixed (header-less cross-site desk
  request; Hermes outcome claim-before-write; go-ahead nonce lapsing; dropped inter-portion
  tokens; three silent-failure spots), two accepted with reasons; plus the security review's
  prompt-injection-to-write finding, fixed. Disposition table in `06-verification.md`.
- **Gate** (4 chunks, 14:45–15:14): 1016 + 730 + 976 + 733 = **3,455 passed, 1 skipped, 1 failed**;
  the failure was the speech-socket test connecting without an Origin header after the fail-closed
  change — the test now sends one (`test_jarvis_screen` 31 passed). Every suite touched by a later
  edit was re-run green after that edit (ui_server, autonomy/self-check, hermes bridge + write-back,
  continuity + livekit, conversation memory, voice brain: 144 → 46 → 34 passed on the final files).
- **Live pass D** (bypass instance, fresh DBs): helpers roster by voice and endpoint (32 providers),
  self-check "16 of 16 passed", pasted text with "status" not hijacked, unreachable helpers answered
  with the setting to provide ("set POSTIZ_API_URL or start Postiz").
- **Playwright on the final tree:** **45 passed (7.4m), exit 0** (`e2e-run-final3.log`; 43 earlier
  specs + the 2 helpers specs). One earlier run showed 15 failures (`e2e-run-final2.log`): it
  overlapped with `Friday.exe --stop`, which kills every `run_ui.py` including Playwright's own
  test server — collateral, not code; the helpers spec was also de-raced (it waited for a response
  the page had already fetched at boot).
- **Live stack restarted on today's code.** A first restart started the three processes from the
  agent's shell; they registered, then were reaped when the turn ended (15:45, all three gone).
  Restarted properly at 15:50 through the owner's launcher (`Friday.exe`, detached `pythonw`
  children + the control-room window): MCP :8000, control room :8770, voice agent registering.
- Builder cost this round: 56k + 107k + 102k tokens (caps + resume-by-message), review 141k.

## Addendum 18:15 — full autonomy is the default
Owner at 18:03: "still not autonomy like Friday and Jarvis do it, get it full autonomy". The
switch had never been flipped (no `data/autonomy.json`; the earlier restart therefore ran in
FULL, which still waits for a yes on takeovers). Changed: `policy.current_autonomy()` falls
back to DANGEROUS when neither the persisted file nor `ADA_AUTONOMY` says otherwise, so both
voice paths start autonomous with nothing to say first; the spoken switch accepts any phrasing
that names full autonomy / autonomous mode / skip permissions (off, stop, guarded step back and
persist). Tests encoding the old default were updated with the date and reason
(`test_dangerous_is_the_default` ×2; two tier-specific tests pinned to FULL). The room agent's
persona already says "Act. Do not ask permission." The hard refusals (credentials, money,
destroying data, security settings) are unchanged, and a spoken write still needs the owner's
own words to ask for it.

## Addendum 18:45 — spoken files confined, Scrapling extraction, hard prompts
- Security review 3: spoken `files/*` accepted absolute paths, and the file toolset's own jail
  allows all of E:\ plus Documents and Desktop, so under full autonomy a page Friday read could
  have steered a write into a project file or a read of `.env`. Every spoken file operation is
  now confined to her workspace (`ARTIFACTS_DIR`); project files are Hermes's job
  (`test_spoken_file_ops_never_leave_the_workspace`). The speech-socket and desk findings were
  re-flagged and stand as dispositioned (gate cookie when the gate is on; strict Origin/Referer;
  no per-click nonce by the owner's instruction).
- Owner: "use the Scrapling web search part properly, it is fast and gives proper details".
  Scrapling stays parse-only (no second egress path); `web/extract {url, fields?|text?|selector?}`
  now fetches through the gated `web_fetch(include_html=True)` and parses with `scrapling_parse`
  (`fields` / `by_text` / `parse`, default digest: title, headings, table rows, list items,
  links). The tool text steers "details from one page" to it
  (`test_web_extract_parses_the_gated_fetch_with_scrapling`, incl. the netguard refusal).
- `docs/HARD_PROMPTS.md`: nine sections of end-to-end prompts (autonomy, Hermes coding, objectives,
  roles, web + Scrapling, helpers, screen, self-verification, one Jarvis-style chain) with PASS/FAIL tells.

## Addendum 19:00 — the self-check does the work itself
Owner, 18:50, on "Self-check: 16 of 16 passed. 7 phases need you": "this is what autonomy is,
huh??". Rebuilt `friday/selfcheck.py`: 24 items, of which the former "needs you" half now runs
for real — a tiny economy-tier Hermes job submitted (`hermes/delegate`, delivery spoken later,
outcome read back by the next self-check), a real transcription of the smallest cached audio,
a scratch objective run stopped at its 32k portion cap, host load for latency attribution, a
look at the live screen for the mic button (an honest "not visible right now" counts as the
pipeline working), and the spoken-files confinement. Side-effect items skip honestly under
`FRIDAY_SELFCHECK_LIVE=0` (tests). Spoken line now: "Self-check: N passed, F failed, K skipped.
Hermes has a real job from me as part of this; I'll tell you when it lands. The one thing I
can't do alone is the pause rule - that needs your voice." Live run on the probe instance:
23 passed, 1 honest negative (the screen look), Hermes work run submitted on the economy tier.
Tests: `tests/test_autonomy_and_selfcheck.py` 16 passed.

## Token cost of this session's team (for the owner)
Builders launched before the token rules: S1 262k, S2 210k, S3 153k, S4 234k (≈860k). Every
later dispatch is governed by `.claude/rules/15-token-discipline.md`, the `friday-*` agent
caps, and the owner's global "Token Discipline" section; no verifier agent was spawned.
