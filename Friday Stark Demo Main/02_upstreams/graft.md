# Graft — Detailed Integration Brief

**Upstream:** https://github.com/trailhq/Graft  
**License/boundary:** MIT  
**Integration mode:** `CORE_CODE_INTELLIGENCE`  
**Role:** Token-budgeted conceptual code context

## What it provides
Graft builds a local tree-sitter structural graph plus readable linked context nodes. Structural build/check/map/grep are described as local/no-key operations; optional deep summaries can use a configured model. Upstream reports large efficiency gains, which Friday must independently reproduce.

## Friday/Hermes fit
Use for repo orientation, concept maps and blast radius. Keep deterministic structural modes hot; gate deep summarization with H economics.

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
- graft init modifies agent config/hook files.
- Optional telemetry must be reviewed/disabled if desired.
- Deep builds may consume model quota.

## Required gates
- init --dry-run.
- Telemetry off.
- build/check/map/grep.
- Edit code and verify freshness.
- A/B real Friday code question.
- No automatic deep LLM call.

## Promotion
`READY` requires: pinned → installed → upstream/focused tests → adapter/MCP tests where relevant → real Friday operation → controlled failure → restart/recovery → rollback instructions.

If runtime integration is unnecessary or duplicative, the correct outcome is `REFERENCE_ONLY`, not forced installation.
