# Universal Capability Fabric Architecture

```text
USER
  |
FRIDAY / JARVIS
  |  objective + policy + conversation + permissions
  |
EXECUTION ECONOMICS / CAPABILITY ROUTER
  |
HERMES
  |  WorkRuns + MCP + Skills + tools + steer/stop
  |
  +-- CodingBackend
  |    +-- Hermes-native
  |    +-- OpenHands
  |    `-- Cline
  |
  +-- BrowserData
  |    +-- Scrapling
  |    +-- browser-use
  |    +-- Agent-Reach
  |    +-- Vane
  |    `-- Maxun
  |
  +-- CodeIntelligence
  |    +-- codebase-memory-mcp
  |    +-- Graft
  |    `-- existing GBrain
  |
  +-- ResearchWorkspace
  |    +-- Open Notebook
  |    `-- AnythingLLM optional
  |
  +-- Media -> OpenMontage
  +-- Social -> Postiz
  +-- Voice -> current LiveKit baseline / Pipecat experimental
  +-- Security -> Strix restricted
  `-- Skills/roles -> gstack / agency-agents / no-ai-slop / i-have-adhd

Reference/bounded-only unless benchmarks justify otherwise:
CrewAI, agenticSeek
```

## Capability contract
Every integrated capability declares:
- id/kind/provider;
- operations;
- permission/risk requirements;
- model requirement;
- cost/latency class;
- start/stop/health;
- secrets dependency;
- ObjectiveRun/WorkRun correlation;
- fallbacks;
- license mode;
- pinned commit/version.

## Selection rule
The router selects the minimum sufficient capability that satisfies:
authorization → quality → reliability → latency → token/model-call cost → hardware cost → historical success/rework.

No provider wins because it is fashionable or already installed.

## User invisibility
The user asks for outcomes. Friday chooses the backend. Repository names appear only on request/diagnostic surfaces.
