# Friday readiness checklist

Honest state of Friday against "can it build/research/operate like Claude Code,
ChatGPT, Claude browser, Claude Cowork." Each item is PROVEN (live evidence
this cycle), BUILT (code + tests, not live-verified), or GAP.

Verdict in one line: **usable today for supervised work through its core path
(Hermes delegation, conversation, memory, news, objectives); not yet
product-grade for unattended operation.**

## Proven live (evidence gathered 2026-08-29)

- [x] **Voice pipeline end to end** — LiveKit ↔ Sarvam STT ↔ Gemini ↔ OpenAI TTS,
      greeting + turn-taking.
- [x] **Conversation + core tools (ChatGPT-like)** — memory write/recall, live
      `system_resource_usage` (real metrics), objectives/status (real projects).
- [x] **Delegated coding (Claude-Code-like, as manager)** — "ask Hermes to…"
      ran the full delegate → Hermes executes as a separate session → result
      delivered back into the conversation (PONG). Friday orchestrates a coding
      agent; it is not itself a from-scratch one.
- [x] **News/research brief** — real headlines + world-monitor follow-up (after
      the CORE_TOOLS fix).
- [x] **Capability fabric at runtime** — every implemented adapter reachable via
      `capability_use` on the live server; security scope-gate holds;
      `capability_reload` works.
- [x] **Browser automation (Claude-browser-like)** — verified live via MCP on
      2026-08-29: `browser_open`→`browser_inspect` read real page data
      (title "Example Domain", body text) then `browser_close`; and
      `browser_automate` ran the Gemini computer-use loop (opened browser, took
      a screenshot, model reasoned about the heading, status succeeded). Both
      the primitives and the agentic loop work.
- [x] **Deep multi-source research** — verified live via MCP on 2026-08-29:
      web_deep_research("What is the FastMCP framework?") returned status
      succeeded with 4 real cited sources (gofastmcp.com, fastmcp.wiki). Crawl
      + rank + citations work. (Caveat kept: web_search regex-scrapes
      DuckDuckGo and should move to a SearxNG-style backend for robustness.)

## Built, not yet live-verified (the priority queue)

_Items 1 (browser), 2 (deep research), and 3-core (autonomous engine on fresh DB) closed — see Proven above._

- [~] **4. Fabric skills auto-routing in the voice path** — wired, pending
      live tuning. The live agent carries `search_capabilities` /
      `use_capability` as function-tools over an active `capability_router`,
      and the session-side continuity plane is attached in the entrypoint
      (`agent._continuity = LiveKitContinuity(...)`), so each user turn is
      recorded as a durable objective before it is learned from. Closed this
      cycle: `search_capabilities` used to come back empty for a skill-shaped
      request ("make a diagram", "write a report") and the model answered from
      its own head; it now scans `_FAMILY_TRIGGERS` and surfaces the fabric
      family with `capability_use(...)` (test: `test_daily_driver_hardening`).
      What remains is behavioural and needs a live voice run: that the model
      *acts on* that hint rather than ignoring it. Persona, not wiring.

## Gaps / reliability blockers

- [x] **5. Test suite green** — the deterministic gate
      (`pytest -m "not live and not slow"`) is **green as of 2026-08-31:
      3,146 passed, 1 skipped, 0 failed, 0 errors** (run in two halves, ~11m
      total). Down from the prior `27 failed / 7 errors`. What was fixed this
      cycle: the 7 `ARTIFACTS_DIR` errors (`files_delete` implemented with the
      artifact-exemption + confirmation flow); Spotify transport wrongly gated
      ASK→cancelled (mapped `spotify.*` to `MEDIA_CONTROL`/`READ_LOCAL_SAFE`);
      the natural TTS rate reset to 1.0 (env-overridable via `TTS_SPEED`); a
      cross-chunk URL/markdown cleaner on the spoken stream; `mss`/`yaml` added
      to `.venv-verify`; continuity wired into the live loop (see item 4); and
      the standing dead-code triaged in `reachability.KNOWN`. The `@live` tests
      (LiveKit/browser/screen) stay excluded and need live credentials — that
      is item 6, not a unit-suite gap.
- [~] **6. LiveKit connection reliability** — hardened, pending live proof.
      The cloud link from this host dropped the worker mid-test once. The
      worker's reconnect ceiling is now raised: `worker_options()` sets
      `max_retry` from `ADA_MAX_RETRY` (default 64, up from the framework's
      datacenter-tuned 16), so a longer home-connection blip is ridden out
      with the framework's exponential backoff instead of exiting into a dead
      assistant (test: `test_daily_driver_hardening`). Everything below that
      — the room/worker reconnection itself — is handled inside livekit-agents;
      what is NOT yet proven is a live recovery on this host, and whether the
      original drop was retry-exhaustion or a region-latency issue (the "closer
      region" lever). That needs a live session to observe.
- [ ] **7. One clean restart to load newest adapters** — the server caches its
      registry; `capability_reload` now avoids future restarts, but the server
      must be started once on current code.

## Order of attack

~~1 browser~~ ~~2 research~~ ~~3 autonomous-core~~ ~~5 tests~~ done →
4 (routing, wired — live tuning left) → 6 (connection). Item 3 residuals:
golden-harness voice-simulation skew and a full voice-driven run (the
narration concurrency bug is now fixed). 7 is a prerequisite for any live
item and is done as part of it. The remaining open items (4-tuning, 6, 7)
all require a live voice/LiveKit session to close.
