# Capability Forge adoption ledger

One entry per external repository the owner or FRIDAY proposes. Each gets one decision - ADOPT, EXTEND, WRAP,
EXTRACT, REFERENCE or REJECT (CAP-FORGE-014) - and nothing enters FRIDAY except through the Capability Forge,
behind FRIDAY's identity with its provenance kept (CAP-FORGE-013). Until the Forge itself exists (A8 is P1, behind
the P0 burn-down) entries here are decisions and evaluation plans only: nothing is cloned, installed or pinned.

Rule (owner, 2026-10-03f): third-party projects never become separate products inside FRIDAY. The person sees
FRIDAY; Developer Mode may show the backend. Licence notices, attribution, source, commit, version, integrity and
security provenance are never erased.

## CopilotKit/OpenDots - SELECTIVE ADOPTION, high value (owner verdict 2026-10-03)

Source of the verdict: `docs/plans/build-harness/00-owner-directive-2026-10-03f.md`. Licence: MIT (the owner's
research; checked through the fabric's licence gate when a part is adopted). Status: decided, not evaluated.

| Part | FRIDAY equivalent | Owner's call |
|---|---|---|
| Specialist workers ("Dots") | FRIDAY workers / AI employees | use the pattern |
| Per-worker computer (browser profile, files, shell) | worker-specific execution environments | high value; shell and files only through FRIDAY security (ExecutionEnvelope, EXEC-SBX-001) |
| Background work (scheduled, tied to the conversation, pause/retry) | Objective OS, proactive runtime, Workflow Fabric | compare and extract; not FRIDAY's main scheduler |
| Slack | Communication / channel fabric | high value |
| Calls, realtime speech | phone / voice operator | high value |
| Spaces, pages | workspace / project brain, artifacts | selective |
| AG-UI | FRIDAY frontend transport | strong candidate |
| Permissions, action records | ExecutionEnvelope / policy, Reality Ledger | compare and adapt |

Rejected as replacements: FRIDAY's Objective OS, memory, model gateway, policy engine and main UI identity -
OpenDots ships with its own runtime, model services and memory, and FRIDAY must not grow a second brain.

## CopilotKit family - evaluate together (Technology Scout, P1)

Evaluate as one capability family rather than four separate frameworks: CopilotKit/OpenMuse (personal-assistant
UI, mobile/web client, take-control experience), CopilotKit/OpenDots (AI coworkers, background work, Slack, calls,
per-worker computers), CopilotKit/OpenBot (governed worker computers: a gateway resolves the target, checks policy
and records audit before acting - close to FRIDAY's ExecutionEnvelope -> PolicyEngine -> Reality Ledger ->
ComputerUseFabric) and AG-UI (the event protocol between frontend, runtime and agents). Output: one adoption entry
per part with its CAP-FORGE-014 decision and value score. Ranked P1 behind the P0 burn-down; no clone or install
before then.
