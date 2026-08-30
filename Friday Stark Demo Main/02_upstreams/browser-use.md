# browser-use — Detailed Integration Brief

**Upstream:** https://github.com/browser-use/browser-use  
**License/boundary:** MIT  
**Integration mode:** `ADAPTER`  
**Role:** Interactive browser agent

## What it provides
Browser Use lets an AI agent navigate, click, type, fill forms and extract content. It supports MCP integration and persistent browser profiles; upstream notes Chrome parallelism can consume substantial memory.

## Friday/Hermes fit
Use when semantic browser interaction and reasoning are needed. Prefer deterministic extraction first. Friday policy remains above browser-use.

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
- Profile/cookie privacy.
- Duplicate browser workers.
- Large Chrome resource footprint.
- Upstream cloud/stealth options must not silently replace local policy.

## Required gates
- Safe local form.
- Semantic refs not coordinate-only.
- Reload/tab recovery.
- Profile isolation.
- User interruption.
- No banking capture.

## Promotion
`READY` requires: pinned → installed → upstream/focused tests → adapter/MCP tests where relevant → real Friday operation → controlled failure → restart/recovery → rollback instructions.

If runtime integration is unnecessary or duplicative, the correct outcome is `REFERENCE_ONLY`, not forced installation.
