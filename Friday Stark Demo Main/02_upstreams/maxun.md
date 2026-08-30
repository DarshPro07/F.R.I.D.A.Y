# Maxun — Detailed Integration Brief

**Upstream:** https://github.com/getmaxun/maxun  
**License/boundary:** AGPL-3.0  
**Integration mode:** `ISOLATED_SIDECAR`  
**Role:** Persistent no-code scraping/crawling/AI extraction

## What it provides
Maxun is a self-hosted platform for scraping, crawling, search and AI data extraction with reusable robots/workflows. The upstream README labels the project early-stage and AGPL-3.0.

## Friday/Hermes fit
Use for persistent or reusable extraction robots, not ordinary one-page extraction. Run as an isolated service behind WebAutomationAdapter.

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
- AGPL distribution boundary.
- Heavy overlap with Scrapling/browser-use.
- Persistent browser/service resource use.

## Required gates
- Self-host health.
- Create disposable robot.
- Run twice and compare structured result.
- Failure/restart.
- No direct secret exposure.
- Friday invisible routing.

## Promotion
`READY` requires: pinned → installed → upstream/focused tests → adapter/MCP tests where relevant → real Friday operation → controlled failure → restart/recovery → rollback instructions.

If runtime integration is unnecessary or duplicative, the correct outcome is `REFERENCE_ONLY`, not forced installation.
