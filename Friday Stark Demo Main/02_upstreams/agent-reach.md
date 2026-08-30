# Agent-Reach — Detailed Integration Brief

**Upstream:** https://github.com/Panniantong/agent-reach  
**License/boundary:** MIT  
**Integration mode:** `ADAPTER_MCP`  
**Role:** Platform-specific internet retrieval

## What it provides
Agent-Reach 1.5.0 describes search/read across 10+ platforms and exposes optional browser/cookies/MCP extras. It includes yt-dlp as a dependency and targets sources such as social/video/RSS-style surfaces.

## Friday/Hermes fit
Use for platform-specific retrieval that general search or static scraping handles poorly.

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
- Platform-specific breakage.
- Cookie/ToS handling.
- yt-dlp and anti-bot changes.
- Do not use cookies from protected contexts without permission.

## Required gates
- MCP discovery.
- Public platform read.
- YouTube transcript/public metadata task.
- No-cookie safe mode.
- Graceful degradation.

## Promotion
`READY` requires: pinned → installed → upstream/focused tests → adapter/MCP tests where relevant → real Friday operation → controlled failure → restart/recovery → rollback instructions.

If runtime integration is unnecessary or duplicative, the correct outcome is `REFERENCE_ONLY`, not forced installation.
