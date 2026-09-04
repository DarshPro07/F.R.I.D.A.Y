# Vane — Detailed Integration Brief

**Upstream:** https://github.com/ItzCrazyKns/Vane  
**License/boundary:** MIT  
**Integration mode:** `SEARCH_ADAPTER`  
**Role:** Private local answering/metasearch

## What it provides
Vane combines SearxNG-backed web search with discussion/academic modes and supports local Ollama plus multiple cloud providers.

## Friday/Hermes fit
Use retrieval/metasearch capability; avoid duplicating answer-generation model calls when Friday/Hermes can synthesize.

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
- Overlap with native search.
- SearxNG service footprint.
- Double-synthesis token waste.

## Required gates
- Search health.
- Source/citation preservation.
- Speed/balanced/quality comparison.
- Disable extra synthesis where possible.
- Offline/degraded behavior.

## Promotion
`READY` requires: pinned → installed → upstream/focused tests → adapter/MCP tests where relevant → real Friday operation → controlled failure → restart/recovery → rollback instructions.

If runtime integration is unnecessary or duplicative, the correct outcome is `REFERENCE_ONLY`, not forced installation.
