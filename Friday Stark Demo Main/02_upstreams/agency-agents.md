# agency-agents — Detailed Integration Brief

**Upstream:** https://github.com/msitarzewski/agency-agents  
**License/boundary:** MIT  
**Integration mode:** `SKILL_PACK`  
**Role:** Specialist role recipe library

## What it provides
The repo contains 230+ specialist agents/roles intended to be copied into Claude agent directories. Friday should treat them as lazy role recipes, not persistent workers.

## Friday/Hermes fit
Build RoleRecipeRegistry: cheap metadata discovery, full role body loaded only when an objective genuinely needs that perspective.

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
- Prompt/context explosion.
- Conflicting role personalities.
- Hundreds of agents would cause needless model use if instantiated.

## Required gates
- Relevant role selection.
- No role loading on trivial task.
- Two/three role decomposition remains disjoint.
- Role result is advisory; Friday remains manager.

## Promotion
`READY` requires: pinned → installed → upstream/focused tests → adapter/MCP tests where relevant → real Friday operation → controlled failure → restart/recovery → rollback instructions.

If runtime integration is unnecessary or duplicative, the correct outcome is `REFERENCE_ONLY`, not forced installation.
