# Open Notebook — Detailed Integration Brief

**Upstream:** https://github.com/lfnovo/open-notebook  
**License/boundary:** MIT  
**Integration mode:** `SIDECAR_MCP`  
**Role:** Research notebook/source workspace

## What it provides
Open Notebook uses Python/FastAPI, Next.js/React and SurrealDB. Its MCP integration exposes notebooks, sources, searches/chats and note workflows to AI clients.

## Friday/Hermes fit
Use as an active research workspace. Verified durable conclusions may later be admitted to GBrain; do not duplicate GBrain as universal truth.

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
- Another service/database.
- Overlap with AnythingLLM/GBrain.
- Research artifacts need tenant/project scope.

## Required gates
- Start service.
- MCP tool discovery.
- Create notebook.
- Ingest public source.
- Search/create note.
- Restart persistence.

## Promotion
`READY` requires: pinned → installed → upstream/focused tests → adapter/MCP tests where relevant → real Friday operation → controlled failure → restart/recovery → rollback instructions.

If runtime integration is unnecessary or duplicative, the correct outcome is `REFERENCE_ONLY`, not forced installation.
