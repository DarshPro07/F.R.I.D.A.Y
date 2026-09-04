# Single-engine build — 2026-09-02

**Ask:** replace the split Claude/ChatGPT execution with one properly
connected engine, integrate the listed GitHub upstreams invisibly, fix the
Hermes↔Friday memory/depth gap, make Jarvis pick the Hermes model from the
requirement, give the browser path screen access and PC control, and report
latency causes. Validate live with Playwright.

**What is true about the starting point, measured before any change:**

- The fabric execution layer (FABRIC-GATE-01, PROC-01, CLI-01, SVC-01,
  LEARN-01, HEALTH-01) was already built and green (57/57) from the previous
  session — but **nothing was declared against it**. 22 of 41 clones were
  UNCLASSIFIED; the CLI mode had zero adapters.
- `executor_router.DEFAULT` was `"claude"` and the only builder was
  `ClaudeCodeExecutor`, so the development pipeline **never reached Hermes**,
  the "mandatory engine". The bridge existed; the pipeline bypassed it.
- The Hermes bridge sent a `TaskBundle` with no memory: a sub-agent started
  every task as a stranger to the owner. `routing.tiers` was unset, so every
  tier resolved to `""` — model selection was a no-op — and `reasoning_effort`
  was never sent even though the gateway accepts it per session.
- The browser brain (`voice_brain.py`) could see the screen (camera/screen
  description) but had **no way to act** on it; `desktop_plan/step/stop`
  were MCP-only.

## Done, with evidence

| # | Change | Where | Proof |
|---|--------|-------|-------|
| 1 | **Hermes is the engine.** `executor_router.DEFAULT="hermes"`, `FALLBACK="claude"`; `executors/hermes.py` is a shim over `HermesSupervisor.delegate` (same bridge, same WorkRun ledger) | `friday/executor_router.py`, `friday/executors/hermes.py` | `tests/test_hermes_engine.py` (12), `test_upstream_lock::test_hermes_is_the_engine_not_an_option` |
| 2 | **Shared memory reaches sub-agents.** `TaskBundle.with_memory()` compiles the goal-relevant slice of the six-tier memory (600 tokens) into the bundle; `delegate(share_memory=True)` by default | `friday/hermes_bridge.py` | `test_delegate_sends_effort_and_memory_to_the_gateway` (the `prompt.submit` text carries the memory line) |
| 3 | **Token-aware depth.** `plan_delegation()` → tier → `reasoning_effort` (`low/medium/high`) sent on `session.create`; `DEFAULT_TIERS` gives economy→haiku, standard→sonnet, deep→profile default (opus), overridable per tier in the profile | `friday/execution_economics.py`, `friday/tools/hermes_control.py` | live: rename→`haiku/low`, feature→`sonnet/medium`, architecture→`opus/high`; `test_plan_delegation_maps_tier_to_effort` |
| 4 | **Upstreams reachable, invisibly.** CLI adapters for `strix` (security, scope-gated), `openworker` (coding), `agenticseek` (GPL — isolated by CLI, `ADAPTER` still refused). 18 other clones given a stated REFERENCE_ONLY reason each | `friday/fabric_adapters/{strix_pentest,openworker_cli,agenticseek_cli,_cli_adapter}.py`, `scripts/integration_matrix.py`, `scripts/upstream_lock.py` | `integration_matrix.py --check`: **41 clones, all classified**; `upstream_lock.py --check`: lock matches clones; live `openworker version` → succeeded; `strix test` without grant → refused before spawn |
| 5 | **Screen access + PC control on the browser path.** `desktop` family (`plan/step/stop/point`) in the UI brain, same three-layer gate as MCP | `friday/voice_brain.py` | live: `plan("open the start menu")` → 1 step + nonce, nothing clicked; forbidden task refused before capture; `step` with forged nonce → `no_plan`; `stop` ungated |
| 6 | **Latency attribution.** `turn_timing.TurnTimer` on both paths: stage timing, host-load detection (CPU≥90/RAM≥92 names the machine), one spoken `latency_note` when ≥4s. UI logs `slow · <cause>`; LiveKit tool result carries `say_if_asked_why_slow` | `friday/turn_timing.py`, `voice_brain.reply`, `agent_friday.use_capability`, `ui/index.html` | `tests/test_turn_timing.py` (5); Playwright `brain-latency.spec.ts` |
| 7 | **Reachability green** (was failing at clean HEAD) | `friday/reachability.py` | `test_reachability` 26/26 |

## Not done, and why

- **cline as a fabric provider** — written, then removed: `test_upstream_lock`
  forbids an OPTIONAL_WORKER also being a fabric provider (two registries,
  two lifecycles). It stays declared in `executor_router.KNOWN` with no
  builder, as before. Honest status: discoverable, not runnable.
- **openhands / crewai / firstmate** as CLI workers — decided REFERENCE_ONLY
  with reasons: the openhands pin is a TypeScript Electron UI with no headless
  entry; crewai is an orchestrator (Friday owns orchestration); firstmate has
  no single runnable entry at the pin.
- **maxun / postiz / openmontage / anythingllm / bolt.diy / onlook /
  open-lovable / open-notebook** — web applications. `FABRIC-SVC-01` exists
  but no objective needs them yet; a sidecar nobody calls is a process to
  babysit. Each has a reason in the matrix and can be promoted when a real
  objective arrives.
- **medusa / smartstore** (from the list, not cloned): not pinned, not
  audited, so `Provider.__post_init__` would refuse a descriptor — which is
  the intended outcome until they go through `upstream_lock.py`.
- **"Remove ChatGPT entirely"**: there is no ChatGPT component in this repo.
  The two model providers are Google (voice LLM) and OpenAI (**TTS only**,
  `tts-1`/nova). Removing OpenAI TTS would silence Friday; it is not a
  reasoning component. Left as is.
- **Hermes `file`/`terminal` toolsets** are disabled in the friday profile
  because of the documented #73403-shaped wedge. Not flipped: the bridge's
  own note says a cheap probe cannot prove it fixed.

## Test status

Measured 2026-09-02, `.venv-verify`, `-m "not live and not slow"`, run in
four chunks because a single ~11m run gets reclaimed on this host:

```
chunk 1   1014 passed  (after fixing the one regression it found: "in depth" trigger)
chunk 2    788 passed  (after fixing 4 router tests that assumed Hermes absent)
chunk 3    876 passed, 1 skipped
chunk 4    676 passed, 2 failed  <- pre-existing: test_upstream_lock needs
                                    "Friday Stark Demo Main/06_schemas/UPSTREAM_LOCK_TEMPLATE.json",
                                    deleted in the owner's working tree (proven 2026-09-01)
total     3,354 passed, 1 skipped, 2 pre-existing failures, 0 new failures
```

Playwright (`e2e-run.bat`, chromium): **36 passed**, exit 0 — 31 prior +
4 mocked latency/logging tests + 1 LIVE screen-control test through the real
brain.

Gates: `scripts/verify_mcp.py` OK (every tool reachable);
`integration_matrix.py --check` 41/41 classified; `upstream_lock.py --check`
matched the clones when the template was temporarily restored, then the tree
was returned to the owner's state.

`test_reachability` is green — it failed at clean HEAD before this work.

## Live state

Python edits are not live until restart: `Friday.exe --stop`, then
`Friday.exe`. Not restarted here — the owner was mid-session.
