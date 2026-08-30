# i-have-adhd — Detailed Integration Brief

**Upstream:** https://github.com/ayghri/i-have-adhd  
**License/boundary:** MIT  
**Integration mode:** `OPTIONAL_PRESENTATION_SKILL`  
**Role:** Action-first output mode

## What it provides
Skill focuses on leading with next action, numbered steps, state restatement and tangent suppression. It is an output/presentation behavior rather than an execution engine.

## Friday/Hermes fit
Expose as optional user presentation mode; adapt any upstream rule that conflicts with Friday's product/system constraints.

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
- Can overcompress deep analysis.
- Specific time-estimate behavior may conflict with runtime rules.

## Required gates
- Enable/disable.
- State persists only at selected user scope.
- Deep answer remains complete.
- No leakage to other users/modes.

## Promotion
`READY` requires: pinned → installed → upstream/focused tests → adapter/MCP tests where relevant → real Friday operation → controlled failure → restart/recovery → rollback instructions.

If runtime integration is unnecessary or duplicative, the correct outcome is `REFERENCE_ONLY`, not forced installation.
