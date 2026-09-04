# OpenHands — Detailed Integration Brief

**Upstream:** https://github.com/OpenHands/OpenHands  
**License/boundary:** MIT core; enterprise/ separate PolyForm Free Trial  
**Integration mode:** `ADAPTER_BACKEND`  
**Role:** Sandboxed/software-development specialist backend

## What it provides
Core OpenHands plus its agent-server provides a programmatic software-agent runtime; the agent server exposes REST/WebSocket-style control with local conversation/event/workspace state. The core/agent-server are MIT, while enterprise/ is a separate license boundary.

## Friday/Hermes fit
Use OpenHands when a coding task benefits from its sandbox/workspace agent server. Hermes still creates/correlates the WorkRun and Friday still owns the parent objective.

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
- Do not vendor/use enterprise/ without a separate licensing decision.
- Do not let an OpenHands conversation become the authoritative Friday objective.
- Container/agent-server lifecycle and resource use need isolation.

## Required gates
- Start isolated agent server and health-check it.
- Execute one bounded code change in a disposable repo.
- Verify actual diff and targeted tests outside the agent's own self-report.
- Cancel an active run.
- Kill/restart backend and preserve Friday ObjectiveRun.
- A/B against Hermes-native and Cline.

## Promotion
`READY` requires: pinned → installed → upstream/focused tests → adapter/MCP tests where relevant → real Friday operation → controlled failure → restart/recovery → rollback instructions.

If runtime integration is unnecessary or duplicative, the correct outcome is `REFERENCE_ONLY`, not forced installation.
