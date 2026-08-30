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

- [ ] **4. Fabric skills auto-routing in the voice path** — adapters are
      reachable but Friday answers prompt/diagram/science requests from its own
      LLM instead of routing to them. Tune the persona/router so
      `capability_use` is preferred for skill-shaped requests.

## Gaps / reliability blockers

- [ ] **5. Test suite green** — 93 pre-existing failures (historical-skew,
      classified in A8_VERDICT.md; not fidelity defects) + 7 errors. Triage to
      zero or document each as intentionally-skipped with a reason.
- [ ] **6. LiveKit connection reliability** — the cloud link from this host
      dropped the worker mid-test today. Unattended operation needs this stable
      (retry/reconnect hardening, or a closer region).
- [ ] **7. One clean restart to load newest adapters** — the server caches its
      registry; `capability_reload` now avoids future restarts, but the server
      must be started once on current code.

## Order of attack

~~1 browser~~ ~~2 research~~ ~~3 autonomous-core~~ done → 4 (routing) →
5 (tests) → 6 (connection). Item 3 residuals: golden-harness voice-simulation skew and a full
voice-driven run (the narration concurrency bug is now fixed). 7 is a prerequisite for any live item and is done as part of it.
