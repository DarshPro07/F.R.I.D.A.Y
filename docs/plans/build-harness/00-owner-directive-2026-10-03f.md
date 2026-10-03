# Owner directive 2026-10-03 (f): OpenDots, unified product identity, the relevance gate

Pasted by the owner on 2026-10-03, extracted verbatim from the session transcript. It records the owner's verdict on CopilotKit/OpenDots (SELECTIVE ADOPTION - HIGH VALUE), asks that CopilotKit OpenMuse, OpenDots, OpenBot and AG-UI be evaluated together as one capability family, and formalises CAP-FORGE-013 (unified product identity) and CAP-FORGE-014 (relevance gate). The links are the owner's.

---

Yes — **OpenDots is useful**, and it fits your idea even better than simply adding another agent framework.

The important rule should now be permanent:

> **Third-party GitHub projects never become separate products inside FRIDAY. They enter through Capability Forge, and only the useful parts survive as FRIDAY capabilities. The user sees FRIDAY/JARVIS, not OpenDots, OpenMuse, Cua, n8n, etc.**

OpenDots is MIT-licensed and describes itself as an open-source template for **persistent AI coworkers** that work through web/mobile, text, calls and Slack, with background jobs and per-agent computers. [GitHub](https://github.com/CopilotKit/OpenDots?utm_source=chatgpt.com)

## What OpenDots gives FRIDAY

OpenDots has several pieces that map extremely well to what we've been designing:

| OpenDots capability | FRIDAY equivalent | Decision |
|---|---|---|
| Specialist “Dots” | FRIDAY AI Employees / Workers | **Use pattern** |
| Per-Dot browser/computer | Worker-specific execution environments | **High value** |
| Background work | Objective OS / proactive runtime | **Use/adapt** |
| Slack | Communication Fabric | **High value** |
| Calls / realtime speech | Phone/Voice Operator | **High value** |
| Spaces | Workspace / Project Brain | **Selective** |
| Pages | FRIDAY artifacts/documents | **Selective** |
| AG-UI | FRIDAY frontend transport | **Strong candidate** |
| Permissions | ExecutionEnvelope/Policy | **Compare/adapt** |
| Action records | Reality Ledger | **Compare/adapt** |
| Persistent browser profiles | User/workspace browser sessions | **High value** |
| Shell/files per worker | Scoped worker capability | **Use only through FRIDAY security** |

OpenDots specifically supports saved specialist roles, per-Dot memory/research permissions, searchable pages, conversations, Slack, calls, scheduled background work and per-Dot computers with browser profiles, files and shell. [GitHub](https://github.com/CopilotKit/OpenDots?utm_source=chatgpt.com)

That makes it especially interesting for your vision of:

```text
FRIDAY
   │
   ├── Research Team
   │     └── research worker
   │
   ├── Developer Team
   │     └── coding worker
   │
   ├── Marketing Team
   │     └── marketing worker
   │
   ├── Customer Support
   │     └── support worker
   │
   └── Personal Assistant
```

But the workers should be **FRIDAY workers**, not visibly called “Dots.”

---

# Most valuable OpenDots idea: each worker gets its own computer

This is particularly good.

OpenDots uses separate computer environments with persistent browser/files for individual specialists, and permissions for browser, workspace files and shell are stored per agent. It also says these capabilities start disabled and do not silently fall back to host shell/files if the computer service is unavailable. [GitHub](https://github.com/CopilotKit/OpenDots/blob/main/docs/COMPUTERS.md?utm_source=chatgpt.com)

That aligns beautifully with the security architecture we're building.

Instead of:

```text
ALL FRIDAY AGENTS
        ↓
same Windows desktop
same browser
same filesystem
same credentials
```

we should eventually have:

```text
                     FRIDAY
                        │
              Worker Environment Manager
                        │
      ┌─────────────────┼──────────────────┐
      ▼                 ▼                  ▼
 Developer          Researcher          Marketing
 Environment        Environment         Environment
      │                 │                  │
 browser A           browser B            browser C
 files A             files B              files C
 credentials A       credentials B        credentials C
```

This is a much better isolation model.

---

# But OpenDots should NOT replace FRIDAY's worker architecture

OpenDots currently combines its UI/runtime with CopilotKit Intelligence, model services and other configured infrastructure. [GitHub](https://github.com/CopilotKit/OpenDots/blob/main/docs/SETUP.md?utm_source=chatgpt.com)

We don't want:

```text
FRIDAY
→ OpenDots
→ its agents
→ its memory
→ its model
```

That would create another brain inside FRIDAY.

We want:

```text
OpenDots code/patterns
        ↓
Capability Forge
        ↓
extract useful components
        ↓
FRIDAY-native adapters
```

So:

### Keep FRIDAY's

- Objective OS
- CortexOS
- Memory
- Model Gateway
- Policy Engine
- Approval system
- Reality Ledger
- Capability Fabric
- Worker orchestration

OpenDots becomes an **implementation source**, not an authority layer.

---

# I especially like its background work system

OpenDots supports scheduled server-side work tied back to the original conversation, with pause/retry controls. [GitHub](https://github.com/CopilotKit/OpenDots?utm_source=chatgpt.com)

That's useful for:

```text
"Friday, monitor competitor prices every morning."

"Friday, prepare my CEO report every night."

"Friday, research this while I'm sleeping."

"Friday, keep following up with these leads."
```

But again:

```text
OpenDots scheduler
```

should not become FRIDAY's main scheduler.

We compare its implementation with:

```text
Objective OS
Proactive Runtime
Workflow Fabric
```

and extract anything better.

---

# Slack + calls are very interesting

OpenDots already connects specialist agents to Slack and supports realtime call/speech flows. [GitHub](https://github.com/CopilotKit/OpenDots?utm_source=chatgpt.com)

This maps directly to your future vision:

```text
FRIDAY
├── desktop
├── mobile
├── web
├── Telegram
├── WhatsApp
├── Slack
├── phone
└── voice
```

The same FRIDAY should appear through all channels.

Not:

```text
SlackBot
PhoneBot
TelegramBot
FridayBot
```

as separate intelligences.

Instead:

```text
                CHANNEL FABRIC

Telegram ─┐
Slack ────┤
Phone ────┤
Web ──────┤
Mobile ───┤
Voice ────┘
          ↓
        FRIDAY
          ↓
     Objective OS
```

OpenDots can accelerate the Slack/call portions.

---

# AG-UI is becoming increasingly interesting

CopilotKit's architecture uses AG-UI as an event protocol between frontend/runtime/agents. It carries lifecycle events, streamed text, tool calls and state updates, and CopilotKit supports multiple agent frameworks behind that interface. [GitHub](https://github.com/CopilotKit/CopilotKit/blob/main/dev-docs/architecture/ARCHITECTURE.md?utm_source=chatgpt.com)

This fits our requirement perfectly.

We could have:

```text
FRIDAY Core
    │
FRIDAY AG-UI Adapter
    │
    ├── Windows UI
    ├── Web UI
    ├── Mobile UI
    ├── OpenMuse-derived UI
    └── OpenDots-derived UI components
```

Thus UI can evolve without changing FRIDAY's brain.

---

# OpenDots + OpenMuse are complementary

I would classify them differently.

```text
OPENMUSE
    ↓
personal-assistant UI
mobile/web client
take-control experience
tasks/artifacts

OPENDOTS
    ↓
AI coworker / AI employee architecture
specialist workers
background jobs
Slack
calls
per-worker computers
```

Together they provide excellent reference material for the **surface layer** of FRIDAY.

But neither replaces:

```text
FRIDAY Core
```

---

# OpenDots + OpenBot is even more interesting

OpenDots itself references OpenBot for the worker computer layer. [GitHub](https://github.com/CopilotKit/OpenDots?utm_source=chatgpt.com)

OpenBot uses governed computer environments where actions go through a gateway that resolves the target, checks policy, records audit state and only then executes in the worker's isolated computer. [GitHub](https://github.com/CopilotKit/openbot/blob/main/docs/architecture.md?utm_source=chatgpt.com)

That is almost exactly our:

```text
ExecutionEnvelope
        ↓
PolicyEngine
        ↓
RealityLedger
        ↓
ComputerUseFabric
```

So I would tell Technology Scout:

```text
Evaluate together:

CopilotKit/OpenMuse
CopilotKit/OpenDots
CopilotKit/OpenBot
CopilotKit/AG-UI
```

as one **CopilotKit capability family**.

Don't independently bolt four frameworks onto FRIDAY.

---

# Your “invisible GitHub code” rule should become formal

I agree with the UX goal, with one caveat: internally we must retain provenance/license/attribution where required.

The user-facing architecture should be:

```text
                       FRIDAY
                          │
                   Capability Fabric
                          │
       ┌──────────────────┼──────────────────┐
       ▼                  ▼                  ▼
 Computer Use         Workflows        AI Employees
       │                  │                  │
       │                  │                  │
    Cua/etc.        Activepieces/etc.    OpenDots ideas
       │
    hidden backend
```

User asks:

> “Friday, research these competitors.”

They should **not** see:

> “Launching OpenDots Dot through CopilotKit using OpenBot…”

They see:

> “Researching the competitors now.”

Developer Mode can expose:

```text
Backend: OpenBot-derived worker
Provider: Claude
Capability: browser.research
Workspace: research-204
```

if needed.

---

# However, do not literally erase third-party provenance

There is an important distinction between:

### Good invisibility

```text
single FRIDAY UI
single FRIDAY brand
single command surface
third-party components hidden as implementation details
```

and:

### Bad invisibility

```text
remove license notices
remove attribution required by license
pretend all third-party code was originally ours
lose source/version/security provenance
```

Don't do the second.

OpenDots itself is MIT licensed, which is relatively permissive, but we should still retain its copyright/license notices where required. [GitHub](https://github.com/CopilotKit/OpenDots?utm_source=chatgpt.com)

FRIDAY's internal SBOM should know:

```yaml
capability:
  id: friday.worker.computer

implementation:
  source: github.com/CopilotKit/OpenDots
  commit: abc...
  license: MIT

ui_exposure:
  vendor_branding: false

provenance:
  retained: true
```

That's professional product architecture.

---

# Capability Forge needs a simple adoption classifier

For **every GitHub URL you give FRIDAY**, it should classify:

```text
ADOPT
EXTEND
WRAP
EXTRACT
REFERENCE
REJECT
```

### `ADOPT`

Code directly solves a missing capability cleanly.

### `EXTEND`

Mostly useful but requires FRIDAY security/behavior additions.

### `WRAP`

Good implementation; keep behind FRIDAY provider interface.

### `EXTRACT`

Only one subsystem/algorithm/design is valuable.

### `REFERENCE`

Useful ideas, but existing FRIDAY code already covers functionality.

### `REJECT`

No useful new capability or value < complexity/risk.

---

# Explicitly support rejection

This part of what you said is very important:

> “if nothing related reject the code”

Yes.

We need to prevent FRIDAY from becoming:

```text
500 GitHub repos
900 MCPs
1,000 skills
60 agents
```

with no idea what anything does.

Capability Forge should calculate something like:

```text
CapabilityValueScore

new_capability_value
+ quality_gain
+ cost_reduction
+ latency_gain
+ reliability_gain
+ user_value

-

duplicate_overlap
+ maintenance_cost
+ security_risk
+ runtime_cost
+ context_cost
+ integration_complexity
```

If result isn't worthwhile:

```text
REJECT
```

Example:

```yaml
repository: random-browser-agent

overlap_with_existing:
  Playwright: 85%
  Cua: 72%

new_capability:
  none

measured_improvement:
  none

decision:
  REJECT_DUPLICATE
```

No install.

---

# For OpenDots specifically

My adoption ledger entry would be:

```yaml
repository:
  CopilotKit/OpenDots

classification:
  agent_workspace
  specialist_workers
  mobile_web_ui
  background_work
  channels
  worker_computers

decision:
  SELECTIVE_ADOPTION

high_value:
  - specialist worker model
  - background job UX/patterns
  - Slack integration
  - realtime calls
  - per-worker computers
  - permission model ideas
  - AG-UI integration
  - Spaces / artifact concepts

evaluate:
  - page system
  - background scheduler
  - OpenBot computer layer

reject_as_replacements:
  - FRIDAY Objective OS
  - FRIDAY Memory
  - FRIDAY Model Gateway
  - FRIDAY Policy Engine
  - FRIDAY main UI identity

user_visible_brand:
  FRIDAY

backend_provenance:
  retained
```

---

# One particularly strong combined architecture

With the repos we've discussed:

```text
                           FRIDAY
                              │
                  ┌───────────┴───────────┐
                  │                       │
              UI Fabric             Capability Fabric
                  │                       │
          OpenMuse patterns               │
          OpenDots patterns               │
             AG-UI                        │
                                          │
                ┌─────────────────────────┼────────────────────────┐
                ▼                         ▼                        ▼
          Computer Use                 Workflow                Workers
                │                         │                        │
              Cua                 Activepieces/n8n             OpenDots/
          Playwright              Windmill                    OpenBot ideas
                │                         │                        │
                └─────────────────────────┼────────────────────────┘
                                          │
                                     Objective OS
                                          │
                                       CortexOS
                                          │
                                        Memory
                                          │
                                     Model Gateway
```

From the user's perspective:

```text
FRIDAY
```

That's it.

No frameworks.

---

# I would formalize one requirement now

### `CAP-FORGE-013 — Unified Product Identity`

> All externally sourced capabilities, skills, MCP servers, libraries, applications and agent frameworks SHALL execute behind FRIDAY's capability abstractions and user-facing identity. Third-party provenance, license, version, integrity and security information SHALL remain available internally and in Developer Mode, but normal users SHALL interact with FRIDAY rather than separate embedded products.

And another:

### `CAP-FORGE-014 — Relevance Gate`

> An external repository SHALL be rejected when it provides no material new capability, measurable improvement or required dependency relative to FRIDAY's existing capability set.

Acceptance test:

```text
give Capability Forge:
3 duplicate browser-agent repositories
1 genuinely new useful capability

Expected:

duplicates → REJECT
new useful one → quarantine/evaluate/integrate
```

That is how we can keep feeding FRIDAY interesting GitHub links for years **without turning the product into a junk drawer**.

For `CopilotKit/OpenDots`, my verdict is therefore **SELECTIVE ADOPTION — HIGH VALUE**, particularly for **AI employees, per-worker computers, background jobs, Slack/calls and AG-UI**, while FRIDAY remains the only visible product and the only authority/controller.
