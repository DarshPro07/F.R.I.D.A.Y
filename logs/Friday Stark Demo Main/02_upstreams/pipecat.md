# Pipecat — Detailed Integration Brief

**Upstream:** https://github.com/pipecat-ai/pipecat  
**License/boundary:** BSD-2-Clause  
**Integration mode:** `VOICE_PROVIDER_EXPERIMENTAL`  
**Role:** Realtime voice/multimodal pipelines

## What it provides
Pipecat provides frame-based realtime voice/multimodal pipelines, many STT/TTS/LLM integrations, LiveKit and other transports, turn management, interruption, context summarization, MCP examples and evaluation/observability components.

## Friday/Hermes fit
Keep current LiveKit production path as baseline. Integrate Pipecat as optional VoicePipeline provider and benchmark.

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
- Large overlap with current voice stack.
- Many optional provider secrets/dependencies.
- Could create two competing conversation state machines.

## Required gates
- LiveKit transport.
- Barge-in.
- Mute.
- Latency.
- STT/TTS switch.
- MCP tool call.
- Compare with current Friday voice.

## Promotion
`READY` requires: pinned → installed → upstream/focused tests → adapter/MCP tests where relevant → real Friday operation → controlled failure → restart/recovery → rollback instructions.

If runtime integration is unnecessary or duplicative, the correct outcome is `REFERENCE_ONLY`, not forced installation.
