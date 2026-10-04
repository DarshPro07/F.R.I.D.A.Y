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

## NVIDIA/OpenShell - HIGH PRIORITY: WRAP AND EVALUATE (owner verdict 2026-10-03g)

Source: `docs/plans/build-harness/00-owner-directive-2026-10-03g.md`. A policy-enforced runtime for autonomous
agents: a gateway (control plane) and sandbox supervisors that enforce filesystem, process, network, credential and
inference boundaries; endpoint-bound credential injection; Windows support through WSL2 (experimental).

- Use: EXEC-SBX-001 tier K2 (coding, build and test workers through WSL2), the Capability Marketplace's quarantine and
  dynamic-evaluation runtime (CAP-MKT-003), network, filesystem and process isolation, credential injection,
  sandboxed model routing.
- Does not replace: FRIDAY's objective engine, policy engine, model router or UI.
- Status: P0 through EXEC-SBX-001 - evaluated by its next run through the capability fabric (licence read, commit
  pinned, adapter, UNAVAILABLE when absent).

## Louis-CFM/coucou - EXTRACT (UI patterns only, owner verdict 2026-10-03g)

A small UI that watches agent sessions (Claude Code, Cursor, Gemini CLI and others), surfaces permission requests,
accepts dropped files and shows notifications. Extract for FRIDAY's activity indicator: agent status, compact
approvals, provider/model switching, drag-and-drop context, notifications. Not used as: FRIDAY's brain, model
gateway, a second chat system, or a separately branded product. P1 (UI fabric).

## skills.rest and NVIDIA SkillSpector - the Capability Marketplace (owner directive 2026-10-03g, amendment A11)

- skills.rest: index all catalogue metadata; download only relevant, deduplicated candidates into quarantine; activate
  only winners (CAP-MKT-001, CAP-MKT-004). Never install the catalogue.
- NVIDIA SkillSpector: mandatory pre-activation gate, CLI first (the owner notes a known stdio MCP initialisation
  issue), later behind a FRIDAY capability wrapper (CAP-MKT-002). The owner cites its finding that 26.1% of 31,132
  analysed skills contained vulnerabilities and 5.2% showed likely malicious intent.
- First candidates the owner named for scanning and evaluation, each with its likely outcome: playwright-web-testing
  (REFERENCE/EXTRACT - FRIDAY has Playwright), mcp-integration (several forks: deduplicate, keep one), git-worktree-
  everyinc (REFERENCE/EXTRACT - the harness has worktrees), research and research-agent (benchmark against FRIDAY's
  research), security-pvpha and skill-security (SkillSpector stays the gate), claude-code-testing-workflows and
  test-plugin-skill (useful to the Forge for generated MCP servers), the Docker skills (deduplicate, pick one),
  context-memory and memory-context-management (likely REFERENCE_ONLY against FRIDAY's memory).
- P1 behind the P0 burn-down; nothing downloaded before then.

## muellerberndt/mini-agi - EXTRACT_PATTERN, medium (owner verdict 2026-10-04b)

A small 2023-era autonomous loop (GPT-3.5/4): one action per turn from a tiny command set, summarised short-term
memory, an optional self-critic. Adopted as designs only: the one-action loop (COG-005, a SIMPLE-task executor tier
below the Objective OS), the progress critic called only on stagnation (COG-006), bounded recent history, and a strict
action schema executed by deterministic code. Not installed as a runtime, memory, shell executor or security layer:
its raw execute_shell / execute_python conflicts with SEC-005 and EXEC-SBX-001.
