# Strix — Detailed Integration Brief

**Upstream:** https://github.com/usestrix/strix  
**License/boundary:** Apache-2.0  
**Integration mode:** `RESTRICTED_SECURITY_ADAPTER`  
**Role:** Autonomous application security testing

## What it provides
Strix uses autonomous pentesting agents, dynamic execution and proof-of-concept validation, with Docker and an LLM provider in normal upstream setup.

## Friday/Hermes fit
Only behind Friday SecurityAuditAdapter for owned/authorized targets. Security policy outranks Strix capabilities.

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
- Offensive capability.
- Docker/privilege footprint.
- Potential misuse against third parties.

## Required gates
- Intentionally vulnerable local fixture.
- Scope deny on unauthorized target.
- Finding reproduction.
- Safe remediation.
- Container cleanup.

## Promotion
`READY` requires: pinned → installed → upstream/focused tests → adapter/MCP tests where relevant → real Friday operation → controlled failure → restart/recovery → rollback instructions.

If runtime integration is unnecessary or duplicative, the correct outcome is `REFERENCE_ONLY`, not forced installation.
