# agenticSeek — Detailed Integration Brief

**Upstream:** https://github.com/Fosowl/agenticSeek  
**License/boundary:** GPL-3.0  
**Integration mode:** `REFERENCE_OR_ISOLATED_ADAPTER`  
**Role:** Local assistant patterns/components

## What it provides
AgenticSeek is a fully local voice-enabled autonomous assistant with web browsing, coding and agent selection.

## Friday/Hermes fit
Its top-level job overlaps Friday/Hermes. Study and selectively adapt components/patterns only when they provide measurable non-duplicate value.

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
- GPL boundary.
- Competing parent agent.
- Local-model/hardware assumptions.

## Required gates
- No parent authority.
- Component-only benchmark.
- License boundary.
- Disabled by default unless advantage proven.

## Promotion
`READY` requires: pinned → installed → upstream/focused tests → adapter/MCP tests where relevant → real Friday operation → controlled failure → restart/recovery → rollback instructions.

If runtime integration is unnecessary or duplicative, the correct outcome is `REFERENCE_ONLY`, not forced installation.
