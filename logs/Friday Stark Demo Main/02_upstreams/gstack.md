# gstack — Detailed Integration Brief

**Upstream:** https://github.com/garrytan/gstack  
**License/boundary:** MIT  
**Integration mode:** `HIGH_VALUE_SKILL_PACK`  
**Role:** Engineering/CEO/QA/design/ship procedures

## What it provides
gstack supplies Skills such as CEO/engineering/design reviews, QA, browse, ship, debug, retro and release workflows. It also has per-domain browser skill notes that compound across sessions.

## Friday/Hermes fit
Import as versioned Friday Skills. Reconcile Claude-specific instructions—especially browser overrides—with Friday's existing browser policy instead of blindly copying host rules.

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
- Upstream agent-host assumptions.
- Context bloat if all Skills loaded.
- Auto-upgrade without governance.

## Required gates
- Skill metadata discovery.
- CEO/engineering review.
- Local UI QA.
- Domain skill save/reuse.
- No global browser override.

## Promotion
`READY` requires: pinned → installed → upstream/focused tests → adapter/MCP tests where relevant → real Friday operation → controlled failure → restart/recovery → rollback instructions.

If runtime integration is unnecessary or duplicative, the correct outcome is `REFERENCE_ONLY`, not forced installation.
