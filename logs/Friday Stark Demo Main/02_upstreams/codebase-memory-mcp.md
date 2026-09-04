# codebase-memory-mcp — Detailed Integration Brief

**Upstream:** https://github.com/DeusData/codebase-memory-mcp  
**License/boundary:** MIT  
**Integration mode:** `CORE_MCP`  
**Role:** High-performance structural code graph

## What it provides
Indexes functions, classes, call chains, routes and cross-service links using tree-sitter across many languages, intentionally without a built-in LLM. It ships a static Windows/macOS/Linux binary. Its own security policy notes broad filesystem/config/background-process access.

## Friday/Hermes fit
Make it the exact structural-query backend in CodeIntelligenceRouter.

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
- Broad code filesystem access.
- May write agent config during install.
- Background process lifecycle must be singleton/observable.

## Required gates
- Binary/source integrity.
- Index Friday repo excluding secrets.
- trace_path/caller/route query.
- MCP handshake/cancel.
- Restart persistence.
- No protected directories indexed.

## Promotion
`READY` requires: pinned → installed → upstream/focused tests → adapter/MCP tests where relevant → real Friday operation → controlled failure → restart/recovery → rollback instructions.

If runtime integration is unnecessary or duplicative, the correct outcome is `REFERENCE_ONLY`, not forced installation.
