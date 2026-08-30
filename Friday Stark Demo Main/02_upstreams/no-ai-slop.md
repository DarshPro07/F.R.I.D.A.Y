# no-ai-slop — Detailed Integration Brief

**Upstream:** https://github.com/petergyang/no-ai-slop  
**License/boundary:** MIT  
**Integration mode:** `SKILL`  
**Role:** Writing quality gate

## What it provides
The repository provides a writing Skill plus an eval checklist to detect/remove common AI-writing patterns while aiming for minimum effective editing.

## Friday/Hermes fit
Import as versioned Friday Skill for writing/content tasks only.

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
- Can flatten intentional voice if universal.
- Unnecessary tokens on engineering-only tasks.

## Required gates
- Slop fixture.
- Minimal-edit fixture.
- Eval checks.
- No invocation on code-only task.

## Promotion
`READY` requires: pinned → installed → upstream/focused tests → adapter/MCP tests where relevant → real Friday operation → controlled failure → restart/recovery → rollback instructions.

If runtime integration is unnecessary or duplicative, the correct outcome is `REFERENCE_ONLY`, not forced installation.
