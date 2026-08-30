# CrewAI — Detailed Integration Brief

**Upstream:** https://github.com/crewAIInc/crewAI  
**License/boundary:** MIT  
**Integration mode:** `REFERENCE_OR_BOUNDED_FLOW`  
**Role:** Multi-agent Crews/Flows patterns

## What it provides
CrewAI provides Crews and Flows for multi-agent workflows. It has telemetry controls; deeper execution sharing is opt-in.

## Friday/Hermes fit
Use as reference or a bounded workflow capability only when it provides measurable value Hermes/Paperclip do not. Never grant parent ObjectiveRun authority.

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
- Duplicate orchestrator.
- Telemetry/context sharing.
- Duplicate worker/model calls.

## Required gates
- Telemetry disabled.
- One bounded flow.
- Friday parent objective remains authoritative.
- No duplicate workers.

## Promotion
`READY` requires: pinned → installed → upstream/focused tests → adapter/MCP tests where relevant → real Friday operation → controlled failure → restart/recovery → rollback instructions.

If runtime integration is unnecessary or duplicative, the correct outcome is `REFERENCE_ONLY`, not forced installation.
