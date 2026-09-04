# Postiz — Detailed Integration Brief

**Upstream:** https://github.com/gitroomhq/postiz-app  
**License/boundary:** AGPL-3.0  
**Integration mode:** `ISOLATED_SIDECAR`  
**Role:** Social scheduling/publishing

## What it provides
Postiz is a self-hosted social scheduling platform supporting multiple networks. Upstream states hosted auth uses official platform OAuth and the repo is AGPL-3.0. API capabilities can vary by version.

## Friday/Hermes fit
SocialPublishingAdapter sidecar. Existing ConnectorControlPlane owns auth UX; Friday permission kernel owns publish authority.

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
- OAuth/rate-limit changes.
- Public API incompleteness/version drift.

## Required gates
- Self-host health.
- Discover API/CLI for pinned version.
- Test account auth.
- Private/dry-run scheduling.
- Cancel/update if supported.
- Restart.

## Promotion
`READY` requires: pinned → installed → upstream/focused tests → adapter/MCP tests where relevant → real Friday operation → controlled failure → restart/recovery → rollback instructions.

If runtime integration is unnecessary or duplicative, the correct outcome is `REFERENCE_ONLY`, not forced installation.
