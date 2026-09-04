# Scrapling — Detailed Integration Brief

**Upstream:** https://github.com/D4Vinci/Scrapling  
**License/boundary:** BSD-3-Clause  
**Integration mode:** `CORE_WEB_ADAPTER`  
**Role:** Adaptive deterministic scraping/crawling

## What it provides
Scrapling supports selectors, fetchers, spiders, proxy rotation, CLI and MCP. It can relocate elements when websites change and scale from requests to crawls. Upstream explicitly cautions users to comply with scraping/privacy law and website policies.

## Friday/Hermes fit
Default web extraction backend before an LLM browser agent when deterministic parsing is sufficient.

## Implementation rules
1. Clone/pin the complete upstream.
2. Preserve its license and upstream tests.
3. Audit install scripts, dependencies, network calls, telemetry, secret handling, file access and background processes.
4. Prefer official SDK/API/MCP/CLI over browser-driving the upstream's UI.
5. Put Friday-specific behavior in a typed adapter or Skill wrapper.
6. Correlate every mutating action to the parent ObjectiveRun and Hermes WorkRun.
7. Expose deterministic health, cancel/timeout, start/stop and rollback.
8. Keep the provider dormant when unused where practical.
9. Verify actual external side effects/results; do not trust a returned success envelope alone.
10. Record token/latency/resource evidence if another installed capability overlaps.

## Principal risks
- Website policies/legal obligations.
- Anti-bot/stealth capabilities need responsible gating.
- Optional browser dependencies.

## Required gates
- Static extraction.
- Changed-selector relocation.
- Crawler pause/resume.
- MCP tools.
- Policy/robots constraints.
- No unnecessary model call.

## Promotion
`READY` requires: pinned → installed → upstream/focused tests → adapter/MCP tests where relevant → real Friday operation → controlled failure → restart/recovery → rollback instructions.

If runtime integration is unnecessary or duplicative, the correct outcome is `REFERENCE_ONLY`, not forced installation.
