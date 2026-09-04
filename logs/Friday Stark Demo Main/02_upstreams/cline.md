# Cline — Detailed Integration Brief

**Upstream:** https://github.com/cline/cline  
**License/boundary:** Apache-2.0  
**Integration mode:** `ADAPTER_BACKEND`  
**Role:** Coding SDK/headless CLI

## What it provides
Cline SDK is a TypeScript programmable agent engine with file editing, shell, web/API/custom tools and SQLite session persistence; its CLI also supports headless JSON-style automation.

## Friday/Hermes fit
Use as a specialist CodingBackendAdapter under Hermes when benchmarked better than Hermes-native for a task class.

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
- Second session/orchestration state.
- Provider layer duplication.
- Node/runtime footprint.

## Required gates
- SDK startup.
- Bounded edit.
- Headless run.
- Cancel.
- Session restart.
- A/B vs Hermes-native/OpenHands.

## Promotion
`READY` requires: pinned → installed → upstream/focused tests → adapter/MCP tests where relevant → real Friday operation → controlled failure → restart/recovery → rollback instructions.

If runtime integration is unnecessary or duplicative, the correct outcome is `REFERENCE_ONLY`, not forced installation.
