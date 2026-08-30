# AnythingLLM — Detailed Integration Brief

**Upstream:** https://github.com/Mintplex-Labs/anything-llm  
**License/boundary:** MIT  
**Integration mode:** `OPTIONAL_SIDECAR`  
**Role:** Local document/RAG workspace compatibility

## What it provides
AnythingLLM is local-first and supports document ingestion, agents, multi-user workspaces, vector databases and model routing. Its self-hosted terms emphasize local data sovereignty apart from optional telemetry/provider choices.

## Friday/Hermes fit
Use only for compatibility/workspace features not already better handled by Friday + GBrain + Open Notebook.

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
- Duplicate memory/vector stores.
- Second agent runtime.
- Large overlapping product surface.

## Required gates
- Local startup.
- Telemetry off.
- Document ingest/query.
- Workspace isolation.
- Friday routing only when justified.

## Promotion
`READY` requires: pinned → installed → upstream/focused tests → adapter/MCP tests where relevant → real Friday operation → controlled failure → restart/recovery → rollback instructions.

If runtime integration is unnecessary or duplicative, the correct outcome is `REFERENCE_ONLY`, not forced installation.
