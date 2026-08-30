# OpenMontage — Detailed Integration Brief

**Upstream:** https://github.com/calesthio/OpenMontage  
**License/boundary:** AGPL-3.0  
**Integration mode:** `ISOLATED_SIDECAR_SKILLS`  
**Role:** Agent-first video production

## What it provides
OpenMontage is instruction-driven: YAML pipeline manifests, stage-director Skills, tool registry, checkpoints and self-review. It includes reference-video analysis and full production pathways. The agent is the orchestration intelligence; Python provides tools/persistence.

## Friday/Hermes fit
Do not make it a second parent orchestrator. Preserve its full skill/pipeline knowledge behind MediaProductionAdapter and let Hermes drive it. Keep AGPL implementation isolated.

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
- AGPL boundary.
- Many optional provider/API dependencies.
- Conflicting orchestration if its top-level control flow is copied blindly.

## Required gates
- Pipeline discovery/preflight.
- Reference-video plan without generation.
- Checkpoint resume.
- Asset verification.
- Render self-review on safe fixture.
- Failure/restart.

## Promotion
`READY` requires: pinned → installed → upstream/focused tests → adapter/MCP tests where relevant → real Friday operation → controlled failure → restart/recovery → rollback instructions.

If runtime integration is unnecessary or duplicative, the correct outcome is `REFERENCE_ONLY`, not forced installation.
