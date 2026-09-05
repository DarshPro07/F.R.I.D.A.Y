# FRIDAY / JARVIS Master Product Requirements Document v3.1

Canonical source of truth (audit A-055). Converted losslessly from the
authored .docx on 2026-09-05; headings, paragraphs, lists and tables
preserved in document order. Office/PDF renders are release artifacts,
not tracked. Edit this file; regenerate renders from it.

FRIDAY / JARVIS
MASTER PRODUCT REQUIREMENTS DOCUMENT
From conversational assistant to persistent AI operating layer
| Product intent  FRIDAY is designed to reduce direct human-computer interaction by understanding outcomes, remembering context, assembling the right temporary team, operating approved digital tools, verifying results, recovering from failure, and improving its procedures over time. |
|---|

| Field | Value |
|---|---|
| Product | F.R.I.D.A.Y. / JARVIS |
| Primary repository | https://github.com/DarshPro07/F.R.I.D.A.Y |
| PRD version | 3.1 |
| Date | 4 September 2026 |
| Status | Master build specification / architecture baseline |
| Primary V1 platform | Windows desktop + browser + voice + remote channels |
| Product posture | Local-first, provider-abstracted, permissioned, evidence-driven |
| Hermes roles | Dual-role: MODEL_GATEWAY for inference routing + EXECUTION_ENGINE for serious build work; Claude Code and other coding agents remain bounded specialist workers |
| Audience | Founder / Product / Engineering / QA / Security / Agent-runtime implementation team |

This document is intentionally implementation-oriented. It defines product outcomes, architecture rules, functional and non-functional requirements, acceptance criteria, release gates, capability integration policy, safety boundaries, and a phased rollout. It should be treated as the source of truth for future implementation prompts and agentic coding work.

## Document Control
| Item | Definition |
|---|---|
| Owner | FRIDAY / JARVIS product owner |
| Purpose | Define what must be built before autonomous implementation proceeds |
| Requirement notation | MUST = release requirement; SHOULD = expected unless trade-off documented; MAY = optional |
| Priority | P0 = required for V1; P1 = production hardening; P2 = expansion |
| Baseline discipline | Current repository behavior is not assumed correct. Phase 0 re-establishes the live baseline on the exact commit being built. |
| Source basis | User requirements, current ecosystem research, agent-harness patterns, MCP guidance, voice-agent guidance, and AI-agent security guidance. |

### How to Use This PRD
- Product and engineering should use Sections 1–8 as the decision and delivery contract.
- Implementation agents must read the Non-Negotiable Architecture Rules before changing the orchestration model.
- Every P0 requirement must map to at least one test or Golden Objective before a release can be called production-ready.
- External repositories are integration candidates, not automatic dependencies. The Capability Register defines how each may be adopted.
- Self-improvement is subordinate to verification. FRIDAY may propose and test improvements, but it must not silently rewrite safety policy or promote unverified changes.

## Contents
1. Executive Summary
2. Background and Context
3. User Stories and Use Cases
4. Functional Requirements
5. Non-Functional Requirements
6. Design Considerations
7. Success Metrics and Evaluation
8. Timeline, Dependencies, Blockers and Risk
9. Target Technical Architecture and Contracts
10. Capability Ecosystem Strategy
11. Authorized Security / OSINT Capability Packs
12. Test, Release and Operational Readiness
13. Final V1 Acceptance Criteria
Appendix A. Security / OSINT Capability Register
Appendix B. External GitHub Project Integration Register
Appendix C. Glossary
Appendix D. Research References

## 1. Executive Summary
### 1.1 Problem Statement
The primary problem is not that modern AI systems cannot reason or generate code. The problem is that digital work remains fragmented across models, coding agents, browsers, terminals, files, SaaS products, desktop applications, memory systems, and automation tools. The user still acts as the orchestration layer: repeatedly explaining context, selecting tools, deciding which agent should work, repairing failed runs, carrying state between sessions, and manually validating whether a claimed result is real.
FRIDAY must replace that manual coordination for supported tasks. The user should express an objective in natural language; FRIDAY should determine whether it can act directly, whether a specialist is necessary, what context must be retrieved, what permissions are required, how the work will be verified, and what should be remembered afterward.
| North-star problem  Today the human operates the computer and coordinates the AI. The target state is that the human owns the objective and authority while FRIDAY operates the digital workflow. |
|---|

### 1.2 Proposed Solution
Build FRIDAY as a persistent, local-first AI operating and orchestration layer. FRIDAY remains the single managerial authority. Hermes has two explicitly separate roles: MODEL_GATEWAY, where FRIDAY sends a bounded inference request and Hermes brokers access to configured model providers without starting the full Hermes agent loop; and EXECUTION_ENGINE, where Hermes performs serious coding/build work. Claude Code and other agents are bounded specialist workers. Tools and external projects are loaded through a capability fabric. A deterministic policy layer controls authority. A shared memory service supplies scoped context. A verification layer decides whether work is actually complete.
| USER / REMOTE CHANNELS / VOICE / UI                  │                  ▼               FRIDAY      (intent, judgment, supervision)                  │      ┌───────────┼───────────┐      ▼           ▼           ▼   MEMORY       POLICY     OBJECTIVES      └───────────┼───────────┘                  ▼         CAPABILITY FABRIC      ┌──────┬────┼────┬──────────┐      ▼      ▼    ▼    ▼          ▼   HERMES  BROWSER PC  SKILLS  SPECIALISTS      └──────┴────┼────┴──────────┘                  ▼               VERIFIER                  │                  ▼               FRIDAY                  │                  ▼                 USER |
|---|

### 1.3 Product Vision
FRIDAY should feel less like an application the user opens and more like a trusted digital operator that is continuously available. It should work alone for simple tasks, construct a temporary team for complex tasks, remember relevant history across tools and agents, continue long-running work safely, accept interruption at any time, and show evidence rather than confidence when reporting completion.
The product is customer-oriented, not merely personal. V1 may optimize for one primary owner on one desktop, but the architecture MUST preserve profile boundaries, permission scopes, portable memory, capability isolation, and provider abstraction so the product can later support multiple customers and organizational deployments without a rewrite.
### 1.4 Product Principles
| Principle | Meaning |
|---|---|
| Outcome over prompts | The user states the desired result. FRIDAY owns task decomposition and tool selection. |
| One manager | FRIDAY is the orchestration authority. External frameworks do not become peer orchestrators by default. |
| Least sufficient mechanism | Use deterministic code or a simple tool when it is enough; do not spawn agents for trivial work. |
| Truth over confidence | Completion requires evidence. |
| Shared memory, scoped context | Workers share one logical memory service but receive only relevant, permitted slices. |
| Progressive capability loading | Capabilities are discovered and loaded on demand; tool schema overload is prohibited. |
| Interruptible autonomy | The user can pause, redirect, cancel or deny at any time. |
| Local-first privacy | Sensitive state and durable personal context stay local by default where practical. |
| Fail closed for privilege | Ambiguous high-risk authority is denied or escalated, not guessed. |
| Self-improvement through gates | Learn → propose → sandbox → test → verify → promote → monitor → rollback. |

### 1.5 Success Metrics
| Metric | V1 target | Why it matters |
|---|---|---|
| Autonomous Objective Success Rate | ≥ 90% on defined Golden Objective suite | Core product outcome |
| Objectives completed without manual execution | ≥ 85% | Measures reduction in direct computer operation |
| Capability routing accuracy | ≥ 95% | Prevents over-delegation and wrong tools |
| False completion rate | < 1% | Trust and honesty |
| Critical unauthorized actions | 0 | Safety |
| Crash/restart objective recovery | ≥ 99% | Persistent-operator requirement |
| Memory retrieval precision | ≥ 90% | Continuity without context pollution |
| User interruption success | ≥ 98% | Human control |
| Recoverable failure recovery | ≥ 90% | Real-world robustness |
| Unnecessary specialist/model invocations | < 10% of simple tasks | Latency and cost |

## 2. Background and Context
### 2.1 Why This Product Is Needed
The ecosystem already contains strong pieces: coding agents, browser automation systems, memory engines, social publishing systems, research tools, voice frameworks, security platforms, and MCP-based connectors. The product opportunity is to create a reliable control plane over these capabilities rather than repeatedly reimplementing each capability inside one monolith.
Hermes is particularly aligned with the desired direction because its public project describes persistent memory, skill creation, isolated subagents, scheduling, messaging gateways, multiple terminal backends and MCP integration [R1]. OpenViking demonstrates progressive skill loading, task-scoped tool visibility, sandboxing and memory/context composition [R2]. Browser Use provides CDP-based browser primitives and explicitly recommends using simpler fetch mechanisms when a browser is unnecessary [R3]. These patterns support a FRIDAY design in which capabilities are selectively activated rather than permanently loaded.
### 2.2 User Problems Solved
| Problem | Required product response |
|---|---|
| Fragmented execution | Work is split across chat, coding agents, terminals, browsers, SaaS products and desktop apps. |
| Repeated context transfer | The user re-explains architecture, preferences, project state and prior decisions. |
| Blind delegation | Agents invoke heavyweight workers for tasks that need only one deterministic action. |
| False completion | Agents may claim “done” without tests, visual validation or state evidence. |
| Brittle long-running work | A crash, quota reset or browser failure loses progress. |
| Tool overload | Large tool catalogs degrade context efficiency and routing quality. |
| Agreement bias | An assistant can optimize for pleasing answers rather than challenging weak assumptions. |
| Unsafe privilege | An agent operating signed-in applications can cause financial, security or reputational damage. |
| No learning closure | Failures repeat because lessons are not converted into reusable procedures/tests. |
| Remote discontinuity | The user cannot seamlessly continue an objective when away from the desktop. |

### 2.3 Business Objectives
- Create a differentiated AI operating layer rather than a commodity chat wrapper.
- Reduce time spent manually coordinating applications, agents and repetitive digital operations.
- Create a reusable capability platform that can serve development, business operations, research, media, ecommerce and authorized security workflows.
- Build durable product advantages in memory, orchestration, verification, safety and cross-application continuity.
- Keep model/provider choice replaceable so the product does not depend on one vendor.
- Enable future customer productization through profiles, permission presets, portable encrypted memory, health diagnostics and capability packaging.
### 2.4 Product Boundaries
| In scope for V1 | Out of scope / deferred |
|---|---|
| Windows desktop operation | Unrestricted cross-platform parity |
| Voice + text command surfaces | Emotion simulation as a product dependency |
| Browser operation with authorized sessions | CAPTCHA / human-verification bypass as a success criterion |
| Hermes + bounded coding workers | Stacking multiple competing top-level orchestrators |
| Shared scoped memory | Dumping all user history into every prompt |
| Scheduled and remote objectives | Unbounded always-on autonomous loops |
| Business/research workflows | Autonomous financial transactions |
| Authorized defensive security workspace | Scanning arbitrary third-party systems without authorization |
| Self-improvement through sandbox/test gates | Silent mutation of core safety policy |
| Single-owner optimized runtime with customer-ready boundaries | Public multi-tenant SaaS control plane in the first release |

### 2.5 Foundational Architecture Decision
| Freeze this decision before implementation  FRIDAY is the final manager. Hermes is the primary serious executor. Claude Code, OpenHands, Cline, CrewAI-style systems and other frameworks may be invoked only as bounded specialist runtimes. They MUST NOT each maintain a competing global plan, memory authority and retry loop for the same objective. |
|---|

## 3. User Stories and Use Cases
### 3.1 Primary Personas
| Persona | Job to be done | Critical needs |
|---|---|---|
| P1 — Power Operator / Owner | Runs many digital tasks and wants “tell once, supervise exceptions.” | Voice, PC control, browser, memory, remote access, scheduling. |
| P2 — Developer / Technical Founder | Needs coding, debugging, repository understanding, tests and deployments. | Hermes delegation, codebase memory, terminal, Git, E2E verification. |
| P3 — Founder / Business Operator | Needs research, planning, ecommerce, communications and analytics. | Sparring team, browser workflows, documents, social publishing, structured research. |
| P4 — Creative / Content Operator | Needs scripts, design, editing, publishing and asset management. | Media specialist packs, design skills, approval workflow, content provenance. |
| P5 — Security / Infrastructure Owner | Needs defensive discovery and authorized testing of owned assets. | Separate security workspace, target authorization, tool isolation, evidence/audit. |
| P6 — Future General Customer | Wants natural computer assistance without knowing models, tools or MCP. | Simple UX, permission presets, safe defaults, reliable outcomes. |

### 3.2 Core User Stories
- As a user, I want to state an outcome so that I do not have to specify every technical step.
- As a user, I want FRIDAY to complete simple actions itself so that it does not waste time spawning coding agents.
- As a developer, I want FRIDAY to delegate complex implementation to Hermes so that serious execution is handled by a specialized engine.
- As a user, I want FRIDAY, Hermes, Claude Code and specialists to share relevant memory so I do not repeat context.
- As a user, I want FRIDAY to challenge my assumptions so that weak decisions are surfaced before execution.
- As a user, I want to interrupt FRIDAY while it is speaking or acting so that I always retain control.
- As a user, I want FRIDAY to continue an objective after restart so that long-running work is resilient.
- As a user, I want to message FRIDAY remotely so that approved work can continue while I am away.
- As a user, I want scheduled objectives so that recurring work runs without repeated setup.
- As a founder, I want FRIDAY to research and construct a business workflow from a goal while asking only decisions that truly require me.
- As a user, I want FRIDAY to operate signed-in sites without treating authentication as permission for every action.
- As a user, I want FRIDAY to show evidence when it says work is complete.
- As a user, I want to correct or delete memory and see where important remembered facts came from.
- As a product owner, I want new capabilities added through a standard manifest so integrations do not create architecture debt.
- As a security owner, I want active recon tools usable only inside explicitly authorized target scopes.
- As a user, I want FRIDAY to learn repeatable procedures from successful work so future executions become faster and more reliable.
- As an engineer, I want FRIDAY self-development to happen in a sandbox branch with tests and rollback so a bad self-upgrade cannot break the main runtime.
### 3.3 Key Use Cases and Scenarios
| Use case | Example request | Expected scenario |
|---|---|---|
| UC-01 Simple desktop action | “Open YouTube and play my saved playlist.” | FRIDAY selects browser/desktop capability directly; no Hermes. It opens the authorized browser, navigates, verifies playback state, reports success. |
| UC-02 Complex coding objective | “Find why the app crashes after login, fix it and prove it works.” | FRIDAY inspects project context and codebase memory, creates acceptance criteria, delegates implementation to Hermes, runs tests, invokes an independent QA/browser verifier, repairs failures, stores evidence. |
| UC-03 Business creation | “Challenge my print-on-demand idea; if viable, build the launch plan and storefront draft.” | FRIDAY assembles researcher, contrarian, financial/product reviewer and judge. Only if viability threshold is met does it proceed to brand/store specialists and browser execution. |
| UC-04 Signed-in browser workflow | “Update the product description in my store.” | FRIDAY uses authorized browser profile, confirms target, edits reversible draft, verifies before save; external publish may require approval depending on policy. |
| UC-05 Deep research | “Research this market and tell me where the opportunity actually is.” | FRIDAY uses web/research adapters, source ranking, contradiction detection and an evidence checker. Facts, inferences and recommendations remain distinguishable. |
| UC-06 Remote continuation | User messages from phone: “Continue the deployment and tell me only if approval is needed.” | Remote identity is verified; objective checkpoint is restored; FRIDAY continues permitted steps and returns status/evidence to the originating channel. |
| UC-07 Recurring automation | “Every morning check whether our production health changed and alert me only if it matters.” | FRIDAY creates a persisted schedule with execution budget, source scope and conditional notification behavior. |
| UC-08 Email/social publish | “Prepare the launch post and send it after I approve.” | FRIDAY drafts, renders preview, waits for exact approval, publishes through an official/compliant connector, verifies returned post ID/URL. |
| UC-09 Creative media | “Make a 45-second product reel from these assets.” | FRIDAY invokes a media specialist runtime, generates a storyboard, composes/edit assets, produces preview, runs quality review, seeks approval before final publishing. |
| UC-10 Memory correction | “You remembered that wrong; our backend is Strapi v5.” | FRIDAY identifies source memory, writes corrected fact with provenance, supersedes conflicting memory and prevents the old fact from being treated as current. |
| UC-11 Self-improvement | Repeated browser failures occur on the same class of workflow. | FRIDAY detects pattern, proposes a reusable procedure/skill and regression test, implements it in sandbox, benchmarks, promotes only after passing gates. |
| UC-12 Authorized security assessment | “Audit my staging domain for exposed services.” | FRIDAY creates an authorization scope, activates the Security Pack, limits targets and actions, executes approved discovery, records audit evidence and produces defensive remediation. |
| UC-13 Interruption during action | User says “stop — don’t submit that.” | Voice/session interruption has priority; pending external action is canceled if not committed; state moves to PAUSED/WAITING_USER. |
| UC-14 Provider outage | Primary model or tool becomes unavailable mid-objective. | FRIDAY classifies the failure, checkpoints state, uses bounded failover when compatible, otherwise pauses with a concrete blocker rather than restarting blindly. |

## 4. Functional Requirements
All P0 requirements are release blockers unless explicitly waived in a signed product decision. Acceptance text below is normative and should be translated into unit, integration, E2E or Golden Objective tests.
### 4.1 Objective and Orchestration
FR-001 — Objective Ledger   [P0]
Every non-trivial request MUST be represented as a durable Objective with goal, desired outcome, constraints, risk, plan, current state, checkpoints, evidence, approvals, retry history and final result.
Acceptance: An objective can be inspected, persisted, resumed after restart, and traced from request to final evidence.
FR-002 — Task Complexity Classification   [P0]
FRIDAY MUST classify work as TRIVIAL, SIMPLE, STANDARD, COMPLEX, LONG_RUNNING or CRITICAL and route it accordingly.
Acceptance: A benchmark of labeled tasks reaches ≥95% routing agreement; trivial tasks do not invoke coding executors unless needed.
FR-003 — Dynamic Planning   [P0]
Plans MUST be constructed from the objective and capability state rather than forced through one fixed workflow. Plans MAY be sequential or parallel but must have explicit exit criteria.
Acceptance: Plan contains measurable steps and verification criteria before consequential execution.
FR-004 — Bounded Agentic Loop   [P0]
Complex work MUST follow understand → plan → execute → observe → test → critique → replan/verify, with retry, time, cost and progress budgets.
Acceptance: No execution path can loop indefinitely; stuck detection triggers replan or escalation.
FR-005 — Progress and Checkpointing   [P0]
Long-running objectives MUST checkpoint after meaningful milestones and before risky transitions.
Acceptance: Killing and restarting FRIDAY at a checkpoint restores the objective without repeating completed irreversible work.
FR-006 — Interruption and Cancellation   [P0]
User interruption MUST preempt speech and future tool dispatch. Cancellation state must propagate to active workers where technically possible.
Acceptance: Voice/text cancel halts new tool actions within defined latency and records whether any action was already committed.
FR-007 — Human Decision Minimization   [P1]
FRIDAY SHOULD ask questions only when missing information materially changes outcome, risk, cost or permission.
Acceptance: Golden Objective evaluation tracks clarification count; avoid questions resolvable from memory/tools.
FR-008 — Contrarian Decision Mode   [P1]
High-impact strategy decisions SHOULD support proposer, contrarian, failure analyst, evidence checker and judge roles.
Acceptance: Decision output preserves disagreements, evidence and uncertainty rather than fabricated consensus.
### 4.2 Delegation and Specialist Teams
FR-009 — Hermes Primary Execution Contract   [P0]
Serious implementation work MUST be delegated through a structured Hermes contract containing goal, constraints, working directory, memory slice, expected artifacts, test plan and reasoning depth.
Acceptance: Hermes runs can be reproduced from their stored contract and return structured status/evidence.
FR-010 — Secondary Coding Worker Contract   [P1]
Claude Code, OpenHands, Cline or other coding runtimes MAY be selected as secondary worker/reviewer based on task fit and availability.
Acceptance: All secondary workers use the same objective/evidence contract and cannot silently replace FRIDAY as manager.
FR-011 — Temporary Specialist Teams   [P1]
FRIDAY MUST be able to assemble temporary specialists for an objective and release them afterward.
Acceptance: Team roster is visible, each worker has bounded role/context/tools, and shared results flow through the objective ledger.
FR-012 — Independent Verification Worker   [P0]
The worker that implemented a consequential change SHOULD NOT be the sole authority certifying it.
Acceptance: Complex coding Golden Journeys include independent test/browser/review evidence.
FR-013 — Parallel Worker Budget   [P0]
Default active execution concurrency MUST remain bounded (target 0–2 workers) and scale only for demonstrably parallel work.
Acceptance: Resource governor blocks or queues additional workers under pressure.
FR-014 — Worker Failure Isolation   [P0]
A worker crash MUST not crash the FRIDAY control plane.
Acceptance: Simulated worker termination is recovered or reported while the objective remains durable.
### 4.3 Shared Memory and Context
FR-015 — Unified Logical Memory Service   [P0]
FRIDAY, Hermes, Claude and specialists MUST use one logical memory service rather than isolated long-term stores that diverge.
Acceptance: A fact committed by an authorized worker is retrievable by another within its permitted scope.
FR-016 — Memory Classes   [P0]
Memory MUST distinguish working, session, project, user/profile, semantic, episodic, procedural/skill, codebase and tool-state data.
Acceptance: Stored records expose type, owner/scope and lifecycle.
FR-017 — Scoped Retrieval   [P0]
Memory retrieval MUST be scoped by objective, project, identity and permission before prompt injection.
Acceptance: Irrelevant or forbidden project memories are excluded in cross-project tests.
FR-018 — Memory Provenance   [P0]
Durable memory MUST store source, timestamp, confidence and supersession/contradiction links.
Acceptance: UI/API can explain where a remembered fact came from and whether it is current.
FR-019 — Memory Correction and Forgetting   [P0]
Users MUST be able to correct, supersede, delete or export durable memory.
Acceptance: Correction stops old value from ranking as current; deletion respects retention/audit policy.
FR-020 — Context Compilation   [P0]
FRIDAY MUST compile a bounded context package instead of dumping full memory/history into each model call.
Acceptance: Token telemetry shows selected context budget; large-memory tests preserve task quality without full-store injection.
FR-021 — Codebase Memory   [P1]
Projects SHOULD maintain durable architecture, module, dependency, API, test and historical-fix knowledge to reduce repeated code exploration.
Acceptance: Repeated coding task uses stored codebase knowledge and reduces exploration calls versus cold baseline.
FR-022 — Learning Promotion   [P1]
Repeated successful procedures MAY be promoted into skills/procedural memory after validation.
Acceptance: Promoted skill contains trigger, steps, tools, guardrails, verification and version metadata.
### 4.4 Capability Fabric and MCP
FR-023 — Capability Registry   [P0]
Every executable or instructional capability MUST register name, type, version, source, license, execution mode, permissions, trust level, health, cost/latency profile and supported actions.
Acceptance: Registry is queryable and no unregistered capability can execute privileged work.
FR-024 — Capability Types   [P0]
The system MUST distinguish NATIVE, MCP, CLI, SDK, HTTP, SIDECAR, SKILL, REFERENCE and SPECIALIST_RUNTIME.
Acceptance: A SKILL cannot be presented as executable unless backed by an actual tool/runtime.
FR-025 — Progressive Discovery   [P0]
FRIDAY MUST reveal capability summaries first and load full tool schemas only when selected.
Acceptance: Tool context size stays bounded as total installed capability count grows.
FR-026 — Capability Health   [P0]
Capabilities MUST expose READY, DEGRADED, UNAVAILABLE, FAILED or DISABLED state with health details.
Acceptance: Unavailable tools are never represented to the user as successfully executed.
FR-027 — Dependency and Version Pinning   [P1]
Third-party capabilities SHOULD record compatible version/commit and license before production activation.
Acceptance: Upgrade review can identify exactly what changed and roll back.
FR-028 — MCP Gateway   [P0]
MCP integrations MUST pass through FRIDAY’s capability and policy layer rather than being globally trusted.
Acceptance: MCP tool visibility/authorization is scoped per objective and identity.
FR-029 — MCP Authorization Compatibility   [P1]
HTTP-based MCP integrations SHOULD follow current protocol authorization guidance, while STDIO credentials stay outside model context [R6].
Acceptance: Tokens are not passed through prompts/logs and protected resources validate intended audience where supported.
FR-030 — Capability Search   [P1]
When a needed capability is not loaded, FRIDAY SHOULD search the local registry/approved catalog before declaring inability.
Acceptance: A capability discovery test resolves known installed-but-unloaded tools without user naming them.
### 4.5 Browser, Desktop, Voice and Remote Operation
FR-031 — Browser Primitives   [P0]
Browser control MUST support open, inspect, navigate, click, type, scroll, select, upload, download, tabs, screenshots, wait and verify.
Acceptance: E2E suite proves each primitive and combined workflows.
FR-032 — Browser Mechanism Selection   [P1]
FRIDAY SHOULD use HTTP/fetch for simple public retrieval and full browser automation only when interaction/state requires it [R3].
Acceptance: Benchmark shows reduced browser startup for fetch-only research.
FR-033 — Authorized Browser Profiles   [P0]
FRIDAY MUST separate isolated research profiles from user-authorized signed-in profiles.
Acceptance: Session cookies from authorized profile are never automatically exposed to isolated workers.
FR-034 — Authentication Is Not Authorization   [P0]
Being signed in MUST NOT grant permission for purchases, publishing, destructive changes or security settings.
Acceptance: Policy tests block unapproved external-write actions despite valid session.
FR-035 — Human Verification Boundary   [P0]
FRIDAY MUST not treat bypassing CAPTCHA/anti-bot/human-verification controls as a success metric.
Acceptance: Encountering a true human verification step pauses for user completion or uses an approved API path.
FR-036 — Desktop Control   [P0]
Desktop operation MUST use observe → plan → policy → act → observe → verify and support keyboard, pointer, window/application controls.
Acceptance: No blind coordinate loop; every action sequence has an observation checkpoint.
FR-037 — Structured State First   [P1]
Accessibility tree, DOM, API or structured application state SHOULD be preferred over screenshot-only reasoning where available.
Acceptance: Screen-heavy benchmark records chosen perception channel and prioritizes structured data.
FR-038 — Voice Pipeline   [P0]
Voice MUST support streaming STT/TTS, VAD/turn detection, mute, interruption and execution status.
Acceptance: Voice Golden Journeys meet interruption and responsiveness targets.
FR-039 — Voice History Truncation   [P1]
When interrupted, conversation history SHOULD reflect only content the user actually heard, following the behavior supported by modern voice-agent frameworks such as LiveKit [R5].
Acceptance: Interrupted speech does not remain in history as if fully delivered.
FR-040 — Remote Channels   [P1]
FRIDAY SHOULD accept authenticated remote objectives through selected messaging/control channels and preserve conversation/objective continuity.
Acceptance: Remote request uses same identity, policy and objective ledger as local request.
### 4.6 Automation, Research and Business Workflows
FR-041 — Schedules   [P1]
Users MUST be able to create persisted one-time and recurring objectives with budgets, permissions, delivery channel and result history.
Acceptance: Scheduled task survives restart and records every execution.
FR-042 — Conditional Monitoring   [P1]
FRIDAY SHOULD support condition-based checks that notify only when the defined condition is met.
Acceptance: No-noise test suppresses notification when condition is false.
FR-043 — Research Pipeline   [P0]
Research MUST support search, retrieval, source comparison, contradiction detection, synthesis and source provenance.
Acceptance: Output can distinguish source fact, inference, uncertainty and recommendation.
FR-044 — Document and Knowledge Work   [P1]
FRIDAY SHOULD support project workspaces for ingesting, retrieving and producing documents grounded in user-selected sources.
Acceptance: Source-grounded answers preserve provenance and do not silently import unrelated context.
FR-045 — Business Workflow Orchestration   [P1]
FRIDAY SHOULD support research → challenge → plan → build → QA → approval → publish patterns for business workflows.
Acceptance: A business Golden Journey executes without the user naming individual tools.
FR-046 — Compliant Social Publishing   [P1]
Publishing adapters SHOULD prefer official OAuth/API flows and require preview/approval according to policy.
Acceptance: Post action records platform result identifier and approval evidence.
### 4.7 Self-Learning and Self-Development
FR-047 — Improvement Detection   [P1]
FRIDAY SHOULD detect recurring failure, high-cost or high-latency patterns and create an improvement candidate.
Acceptance: Candidate links to measured evidence rather than subjective “self-improvement.”
FR-048 — Sandboxed Self-Development   [P0]
FRIDAY MUST implement self-modification only in an isolated branch/workspace or equivalent sandbox.
Acceptance: Main runtime is unchanged until promotion gate passes.
FR-049 — Self-Development Test Gate   [P0]
Every self-change MUST run unit/integration/E2E tests relevant to the touched subsystem and a regression baseline.
Acceptance: Promotion is impossible while required tests fail.
FR-050 — Benchmark Before Promotion   [P1]
Performance/routing/memory improvements MUST compare against a pre-change baseline.
Acceptance: Promotion record includes before/after measurements and no unacceptable regression.
FR-051 — Rollback   [P0]
Every promoted self-change MUST have a deterministic rollback path.
Acceptance: A simulated post-promotion failure automatically or manually restores the prior known-good version.
### 4.8 Verification, Observability and Safety
FR-052 — Evidence Ledger   [P0]
Consequential steps MUST attach expected result, actual result, verification method, timestamp and pass/fail evidence.
Acceptance: Final objective result can enumerate evidence supporting completion.
FR-053 — Completion Gate   [P0]
FRIDAY MUST NOT mark an objective COMPLETED solely because a worker states it is done.
Acceptance: Completion state transition requires configured evidence conditions.
FR-054 — Observability   [P0]
The system MUST record objective state transitions, tool calls, workers, latency, retries, resource usage, errors and verification outcomes.
Acceptance: A single trace reconstructs what happened without reading raw model thoughts.
FR-055 — Cost/Token Accounting   [P1]
Model/tool usage SHOULD be attributed to objective and worker, with optional budgets.
Acceptance: Objective report includes calls/tokens/cost where provider data exists.
FR-056 — Resource Governor   [P0]
FRIDAY MUST monitor CPU, RAM, disk, browser processes, worker count and queue pressure, applying backpressure or shedding optional work.
Acceptance: Stress test keeps control plane responsive under resource pressure.
FR-057 — Secret Isolation   [P0]
Credentials MUST be resolved at execution time and excluded from ordinary prompts, durable memory and plaintext logs.
Acceptance: Secret scanning of logs/memory returns zero unredacted test secrets.
FR-058 — Policy Engine   [P0]
Risk and authorization MUST be enforced outside the LLM.
Acceptance: Adversarial prompt cannot override deterministic denial/approval requirements.
FR-059 — Risk Tiers   [P0]
Actions MUST be classified into read-only, reversible, external-write, destructive/security and forbidden tiers.
Acceptance: Representative tool actions map deterministically to a tier.
FR-060 — Exact-Action Approval   [P0]
Approval MUST bind to operation, target, parameters, objective and expiration.
Acceptance: Approval for one file/domain/action cannot authorize another.
FR-061 — Security Workspace   [P0]
Active recon/pentest tooling MUST exist in a separate AUTHORIZED_SECURITY capability namespace, disabled by default.
Acceptance: Security tools cannot execute from a normal objective without scope activation.
FR-062 — Security Target Contract   [P0]
Active security objectives MUST record target ownership/authorization, allowed scope, allowed actions, prohibited actions and expiration.
Acceptance: Out-of-scope host/action is blocked even when a tool requests it.
FR-063 — Prompt Injection Boundary   [P0]
External web/tool content MUST be treated as untrusted data and cannot grant itself higher authority.
Acceptance: Injected instructions requesting secrets or dangerous actions fail policy tests.
FR-064 — Supply-Chain Review   [P1]
Third-party agents/tools SHOULD receive source/version/license/trust review before privileged activation.
Acceptance: Capability registry records review status and blocks unreviewed high-risk activation.
FR-065 — Audit Log   [P0]
All privileged actions and authorization decisions MUST be written to a tamper-evident or append-oriented audit record.
Acceptance: Audit can answer who/what/when/target/decision/result for every P2+ risk action.
FR-066 — User Memory and Data Controls   [P1]
Users SHOULD be able to view, export and delete their durable personal data and inspect connector access.
Acceptance: Data-control workflow is accessible without developer tools.
FR-067 — Profile Boundary   [P1]
Future multi-profile/customer mode MUST isolate memory, credentials, capabilities and objective history by identity.
Acceptance: Cross-profile tests show zero unauthorized data/tool leakage.
FR-068 — Capability Marketplace Readiness   [P2]
The manifest model SHOULD support installing/disabling/updating capability packs without core-code edits.
Acceptance: A sample third-party pack can be added, permissioned and removed cleanly.
### 4.9 Hermes Model Gateway, Provider Routing and Token Governance
FR-069 — Hermes Dual-Role Interface   [P0]
FRIDAY MUST integrate Hermes through two explicit modes: MODEL_GATEWAY for inference-only access and EXECUTION_ENGINE for agentic coding/build execution.
Acceptance: MODEL_GATEWAY returns a model response without starting Hermes tools, skills, subagents or execution loop; EXECUTION_ENGINE still supports normal Hermes work.
FR-070 — Hermes Model Gateway   [P0]
In MODEL_GATEWAY mode FRIDAY MUST remain the visible brain/control plane and send only a bounded request envelope to Hermes, which selects/authenticates an eligible configured model provider and returns the inference result plus usage/route metadata.
Acceptance: Trace proves FRIDAY owns objective/state while Hermes gateway only brokers inference.
FR-071 — Provider Capability Discovery   [P0]
FRIDAY MUST query the Hermes provider layer for currently usable providers/models/entitlements rather than hard-coding model availability.
Acceptance: Provider inventory changes are reflected without FRIDAY code changes and unavailable routes are not advertised as ready.
FR-072 — Subscription and API Separation   [P0]
FRIDAY MUST NOT assume a ChatGPT or Claude subscription is interchangeable with direct API access. It MUST use only provider/authentication paths Hermes currently supports and report entitlement/auth failures truthfully.
Acceptance: Subscription-only configuration never masquerades as generic API credit; provider routes are separately identified.
FR-073 — Gateway Credential Isolation   [P0]
Provider credentials and refresh tokens SHOULD remain inside Hermes/provider credential stores or a secret broker. FRIDAY receives capability/health metadata and results, not raw provider secrets.
Acceptance: No provider credentials appear in FRIDAY prompts, memory, objective records or normal logs.
FR-074 — Privacy Truthfulness   [P0]
Routing through Hermes MAY centralize credential handling and reduce secret distribution, but FRIDAY MUST NOT claim that an upstream cloud-model call becomes private merely because Hermes brokers it.
Acceptance: UI/audit identifies local vs upstream provider boundary truthfully.
FR-075 — Model Routing Policy   [P0]
FRIDAY MUST choose model path based on task complexity, modality/tool needs, privacy, latency, cost, provider health and user policy. Fast defaults MAY handle routine interaction while higher-reasoning models are requested through Hermes when justified.
Acceptance: Routing benchmark shows task-appropriate tier selection and fallback without user naming providers.
FR-076 — Inference-Only Context Budget   [P0]
MODEL_GATEWAY calls MUST NOT automatically load Hermes full history, global memory, full repository, tool catalog, skills or subagent prompts. FRIDAY sends a compiled minimal context package.
Acceptance: Gateway trace demonstrates bounded context and zero tool/schema injection on inference-only requests.
FR-077 — Token Budget Profiles   [P0]
FRIDAY MUST define per-request token/reasoning budgets by task class and require explicit escalation when projected context exceeds the normal budget.
Acceptance: A simple request cannot silently expand into a very-large-context agent run.
FR-078 — Token Growth Guard   [P0]
The runtime MUST detect abnormal token growth across retries, repeated context, recursive summaries or agent handoffs and stop/replan before runaway consumption.
Acceptance: Synthetic recursive-context test triggers the guard before the objective budget is exhausted.
FR-079 — No Agent Loop for Pure Inference   [P0]
When FRIDAY requests only reasoning/completion from Hermes MODEL_GATEWAY, Hermes MUST not create subagents, execute shell/browser tools, inspect the repository or enter an autonomous loop unless FRIDAY explicitly promotes the request to EXECUTION_ENGINE mode.
Acceptance: Pure-inference benchmark records one bounded provider transaction path with no execution side effects.
FR-080 — Provider Usage Telemetry   [P0]
Every gateway call MUST record selected route/model, latency, input/output/cached/reasoning token usage when available, retries, failover and objective attribution without logging secrets.
Acceptance: Diagnostics identify the exact calls responsible for token/cost spikes.
FR-081 — Provider Failover Without Objective Reset   [P1]
If a gateway provider becomes unavailable or rate-limited, FRIDAY SHOULD checkpoint the request and perform bounded compatible failover rather than restarting the entire objective or duplicating completed context.
Acceptance: Fault injection preserves objective state and limits duplicate tokens during failover.

### 4.10 Objective State Model
| NEW -> UNDERSTANDING -> PLANNING -> READY -> EXECUTING -> VERIFYING -> COMPLETED                          +-- WAITING_APPROVAL                          +-- WAITING_EXTERNAL                          +-- RECOVERING -> EXECUTING                          +-- BLOCKED                          +-- CANCELLED / FAILED |
|---|

State transitions MUST be explicit events. A worker cannot directly set COMPLETED without the control plane evaluating completion criteria. WAITING_APPROVAL and WAITING_EXTERNAL must be distinguishable so remote/scheduled workflows do not confuse human approval with a third-party dependency.
### 4.11 Risk and Approval Model
| Level | Typical operations | Default handling |
|---|---|---|
| R0 — Read | Read file, inspect page, search docs | Automatic within scope |
| R1 — Reversible local | Open app, create draft/temp file | Automatic + audit |
| R2 — External write | Send message, publish, submit form | Approval or explicit pre-authorization |
| R3 — Destructive / security | Permanent delete, account/security change, privileged install, active security scan | Exact-action approval + policy validation |
| R4 — Forbidden | Actions prohibited by configured policy or outside authorized security scope | Block regardless of model request |

## 5. Non-Functional Requirements
### 5.1 Performance Requirements
| NFR | Target / requirement | Measurement |
|---|---|---|
| NFR-P01 Voice interruption | ≤300 ms median from detected user interruption to TTS stop | Voice telemetry |
| NFR-P02 First acknowledgement | ≤700 ms median for voice acknowledgement where pipeline is healthy | End-to-end traces |
| NFR-P03 First meaningful stream | ≤1.5 s median for normal cloud-assisted conversational responses | Session traces |
| NFR-P04 UI state propagation | <250 ms local target | Event-to-render metric |
| NFR-P05 Control Room interactive | <2 s target on supported machine | Cold-start benchmark |
| NFR-P06 Memory retrieval | P95 <500 ms for local scoped retrieval target | Memory benchmark |
| NFR-P07 Simple local action | Preferred <2 s when no cloud reasoning is required | Golden actions |
| NFR-P08 Idle CPU | Target <5% average for core runtime | 15-minute idle profile |
| NFR-P09 Worker concurrency | 0–2 default active execution workers | Runtime governor |
| NFR-P10 Token efficiency | No full capability catalog or full memory dump by default | Context telemetry |
| NFR-P11 Gateway isolation | Inference-only Hermes calls load no tools/subagents unless promoted | Gateway traces |
| NFR-P12 Token growth guard | Abnormal recursive/repeated context is stopped before budget exhaustion | Synthetic runaway test |
| NFR-P13 Provider attribution | 100% of model-gateway calls attributed to objective + provider/model | Usage telemetry |

| Performance reality  A universal 500 ms “Jarvis response” is not a valid engineering requirement for cloud STT → reasoning → tool → TTS workflows. Perceived responsiveness should be achieved through streaming, immediate acknowledgement, local routing for simple actions and fast interruption—not by sacrificing correctness. |
|---|

### 5.2 Reliability and Availability
- Control plane target availability during active desktop sessions: ≥99.5%.
- Optional provider failure MUST NOT crash the core runtime.
- Objective state, approvals and evidence MUST be durable before declaring a checkpoint.
- All retry loops MUST be bounded and categorized by failure type.
- Long-running jobs MUST tolerate provider rate limits, browser restarts and worker restarts without losing completed irreversible steps.
- An upgrade MUST be rollback-capable to the last known-good release.
### 5.3 Security and Privacy
FRIDAY combines local files, browser sessions, terminal execution, external models and connected applications; therefore model instructions are not a sufficient security boundary. OWASP’s current AI Agent Security guidance identifies excessive autonomy, high-impact action abuse, cascading failures, sensitive-data exposure, supply-chain risk and denial-of-wallet from unbounded loops, and recommends least privilege, separate tool sets and explicit authorization for sensitive operations [R4]. These principles are mandatory for FRIDAY.
| Control | Requirement |
|---|---|
| Least privilege | Grant only tools/resources needed for the active objective. |
| Secret handling | No credentials in prompts, memory or ordinary logs. |
| Sandboxing | Filesystem/command scope constrained to explicit workspaces for untrusted or self-development tasks. |
| Prompt injection | Untrusted web/tool content cannot change policy or approval state. |
| Connector consent | User can see and revoke connected-service authority. |
| Signed-in browser | Authentication state is isolated and does not equal write authorization. |
| Security tools | Active recon/pentest disabled outside authorized security workspace. |
| Supply chain | Privileged third-party integrations pinned/reviewed. |
| Data minimization | Only task-relevant personal/project context leaves local runtime. |
| Auditability | Privileged actions have durable decision/evidence records. |

### 5.4 Accessibility Requirements
- Target WCAG 2.2 AA for applicable Control Room surfaces.
- Every core operation must be reachable without voice; keyboard/text control is required.
- Visible focus, semantic controls, adequate contrast and reduced-motion support are required.
- Status changes such as WAITING_APPROVAL, BLOCKED and FAILED must be understandable without relying on color alone.
- Voice transcripts/captions must be available for spoken interaction history.
- Diagrams and graphs require equivalent text summaries.
### 5.5 Browser and Device Support
| Tier | Support |
|---|---|
| V1 primary | Windows 10/11 desktop, current Chromium-based browser, microphone; webcam optional. |
| V1 remote | Authenticated messaging/control channel capable of objective creation, status, approval and interruption. |
| V1 development | Python/Node/toolchain versions pinned by repository; exact minimums established in Phase 0 truth audit. |
| Future | macOS, Linux and mobile companion after Windows reliability and security gates are met. |

### 5.6 Maintainability and Extensibility
- Core orchestration, memory, policy, capability adapters and UI must be independently testable modules.
- Capability integrations must use stable contracts rather than importing whole third-party applications into core.
- Integration-specific dependencies must remain optional unless explicitly promoted to core.
- Event, objective and evidence schemas must be versioned.
- Every external capability should document a retirement path so architectural scaffolding can be removed when no longer necessary.

## 6. Design Considerations
### 6.1 UI/UX Requirements
The interface should feel like a calm operating cockpit, not a generic AI SaaS dashboard. The user must be able to understand in a few seconds: what FRIDAY believes the objective is, what it is doing now, which worker/tool is active, what requires approval, whether the system is healthy, and what evidence supports completion.
| Primary surface | Purpose | Must show |
|---|---|---|
| Core | Glanceable operating state | Listening/thinking/acting state; current objective; next action; health; pending approval. |
| Objective | Execution trace | Plan, completed/current/next steps, retries, evidence, blockers, checkpoints. |
| Team | Temporary worker roster | FRIDAY, Hermes/specialists, status, purpose, resource/permission scope. |
| Memory | User/project continuity | Retrieved memories, provenance, confidence, contradictions, correction/delete controls. |
| Capabilities | What FRIDAY can actually do | Searchable capability registry, health, permissions, trust, installed/disabled state. |
| Automations | Scheduled/conditional work | Schedules, next run, budget, last result, pause/delete. |
| Activity / Audit | Trust and debugging | Consequential actions, approvals, failures, verification results. |
| System | Operational health | Provider state, queue, CPU/RAM, browser, memory store, gateway health. |

### 6.2 Design Principles
- Real state over decorative state: never show fake agents, fake metrics or pretend capabilities.
- Progressive disclosure: default views are simple; deep tool/model traces are available on demand.
- Action before decoration: motion should communicate listening, activity, progress, warning or completion.
- Approval clarity: show exactly what will happen, to what target, using what data, before an R2/R3 action.
- Recovery clarity: errors should state what failed, what has been preserved, what FRIDAY will try next and whether user action is needed.
- No generic “AI slop”: avoid gratuitous glassmorphism, purple gradients, meaningless neon and oversized assistant avatars.
- Voice-first but not voice-only: every command, approval and status remains accessible in text/UI.
- Operator trust: the user should be able to inspect objective, memory and evidence without exposing private model chain-of-thought.
### 6.3 Key Design Patterns
| Pattern | Example / rule |
|---|---|
| Objective timeline | ✓ Understand  ✓ Research  ● Implement  ○ Test  ○ Verify |
| Approval card | Action + target + parameters + reason + expiry + Approve/Deny/Edit |
| Capability health badge | READY / DEGRADED / UNAVAILABLE / FAILED / DISABLED |
| Evidence card | Expected → Actual → Verification → Pass/Fail |
| Memory provenance chip | Source + scope + last updated + confidence + superseded state |
| Worker roster | Only active/relevant workers; no permanent theatrical “agent army.” |
| Resource pressure banner | Explain when concurrency/model choice was reduced to preserve system health. |
| Remote-safe summary | Small status message that includes objective state, blocker/approval and key evidence without dumping logs. |

### 6.4 Voice Interaction Design
Turn-taking and interruption are product-critical. LiveKit documents multiple turn-detection strategies and interruption behavior in which agent speech can be paused when user speech is detected and conversation history can be truncated to what the user actually heard [R5]. FRIDAY should use those primitives or equivalent behavior rather than implementing a brittle custom voice loop.
- Acknowledge long work quickly (“I’m checking the repo now”) without pretending completion.
- During execution, voice updates should be sparse and meaningful; UI holds detailed trace.
- Mute must stop microphone input into STT, not merely hide UI state.
- When interrupted, stop speaking first; then process the new instruction; do not continue the prior sentence in parallel.

## 7. Success Metrics and Evaluation
### 7.1 KPI Framework
| KPI | Baseline | Target | Measurement |
|---|---|---|---|
| Autonomous Objective Success Rate | Unknown — establish Phase 0 | ≥90% | Golden Objective suite pass / attempted |
| Manual Execution Avoidance | Unknown | ≥85% | Objectives without user performing execution steps |
| Capability Routing Accuracy | Unknown | ≥95% | Labeled evaluation set |
| False Completion | Unknown | <1% | Completed states failing independent evidence audit |
| Critical Unauthorized Actions | 0 desired | 0 | Security test/audit |
| Restart Resume Success | Unknown | ≥99% | Chaos/restart suite |
| Memory Precision@K | Unknown | ≥90% | Curated memory benchmark |
| Memory Cross-Scope Leakage | Unknown | 0 | Isolation tests |
| Interruption Success | Unknown | ≥98% | Voice stress suite |
| Recoverable Failure Recovery | Unknown | ≥90% | Fault-injection suite |
| Simple Task Over-Delegation | Unknown | <10% | Task-routing telemetry |
| P95 Memory Retrieval | Unknown | <500 ms target | Local benchmark |
| UI State Propagation | Unknown | <250 ms target | Event telemetry |
| Idle CPU | Unknown | <5% target | Idle benchmark |
| Unbounded loops | Unknown | 0 | Runtime invariant |
| Secret leakage in logs/memory | Unknown | 0 | Secret-seed scanning |
| Evidence coverage for consequential completion | Unknown | 100% | Objective audit |
| Security pack out-of-scope execution | Unknown | 0 | Target-guard tests |

### 7.2 Golden Objective Evaluation Suite
The release benchmark should contain at least 150 stable, replayable objectives plus a rotating set of live/manual scenarios. Evaluation criteria are written before execution, not after seeing the result.
| Domain | Minimum cases | Examples |
|---|---|---|
| General computer operations | 20 | Open app, locate file, export, window control, interruption. |
| Browser | 25 | Research, signed-in reversible edit, upload/download, tab management, DOM changes. |
| Coding | 35 | Bug fix, feature, refactor, dependency change, migration, regression. |
| Research | 20 | Deep comparison, conflicting sources, freshness, citation provenance. |
| Business workflows | 15 | Market challenge, storefront draft, campaign plan, analytics review. |
| Documents / data | 10 | Source-grounded report, spreadsheet/document transformation, structured extraction. |
| Memory / continuity | 10 | Cross-session recall, correction, contradiction, project isolation, resume. |
| Recovery / chaos | 10 | Provider down, browser crash, tool timeout, worker crash, restart, rate limit. |
| Authorized security | 5 | Owned local/staging targets, scope denial, audit evidence. |

### 7.3 Measurement Discipline
- Never copy a test-count claim from an old audit into a release report. Re-run on the exact build.
- Every benchmark stores build/commit, configuration, model/provider, machine profile and date.
- Success scoring must include correctness, evidence, policy compliance, latency/cost and manual-intervention count.
- Where model nondeterminism exists, run enough repetitions to measure variance rather than selecting one successful demo.
- Keep a “golden failures” corpus: past bugs must become durable regression tests or skills.

## 8. Timeline, Dependencies, Blockers and Risk
### 8.1 Phased Rollout Plan
| Phase | Indicative effort | Focus | Exit condition |
|---|---|---|---|
| Phase 0 — Repository Truth Audit | 1–2 weeks | Map architecture/dependencies; run exact current tests; profile CPU/RAM/latency; identify duplicate orchestration/memory; verify every advertised integration. | Authoritative CURRENT_STATE.md + failing P0 backlog + reproducible runbook. |
| Phase 1 — Control Plane Consolidation | 2–3 weeks | Objective ledger, state machine, event bus, one FRIDAY authority, Hermes contract, failure isolation. | Complex coding objective can run and resume through one manager. |
| Phase 2 — Shared Memory and Codebase Context | 2 weeks | Unified memory API, provenance, project scope, codebase map, context compiler. | Cross-agent recall works without full-context dumping or leakage. |
| Phase 3 — Capability Fabric | 2 weeks | Manifest, progressive discovery, health, permissions, MCP/CLI/SDK adapters. | New capability can be added/disabled without changing core orchestration. |
| Phase 4 — Browser and Desktop Operator | 2–3 weeks | Authorized/isolated profiles, structured perception, desktop actions, approvals, verification. | Browser/PC Golden Journeys pass with interruption and no policy bypass. |
| Phase 5 — Voice and Remote Continuity | 1–2 weeks | Turn tuning, mute, interruption, messaging gateway, remote approval/status. | Voice/remote objectives share the same ledger and security model. |
| Phase 6 — Loop Engineering and Independent Verification | 2 weeks | Critic/judge/verifier roles, stuck detection, bounded retry/replan, evidence gates. | No consequential objective completes without configured evidence. |
| Phase 7 — Self-Development Pipeline | 2 weeks | Improvement detection, sandbox branches, benchmark, promotion/rollback. | FRIDAY can improve a bounded skill/procedure safely. |
| Phase 8 — Business / Creative Capability Packs | Ongoing | Research, social, media, ecommerce and domain skills through adapters. | Each pack has acceptance suite and permission profile. |
| Phase 9 — Authorized Security Pack | Ongoing after core safety | Security workspace, scope contract, passive/active split, target guard, audit reporting. | Out-of-scope active action is impossible through normal runtime. |
| Phase 10 — Customer Productization | After V1 reliability | Profiles, onboarding, portable encrypted memory, diagnostics, permission presets, packaging. | Second customer profile can run without data/capability leakage. |

| Planning note  The timeline is a sequencing estimate, not a promise. Strong coding agents can accelerate implementation, but they cannot safely remove the audit, verification, security and integration-hardening phases. “One giant autonomous prompt” is an execution technique—not a replacement for release gates. |
|---|

### 8.2 Dependencies
| Dependency | Criticality | Requirement |
|---|---|---|
| FRIDAY control plane | Critical | Objective/state/policy/capability orchestration. |
| Hermes | Critical for planned architecture | Dual role: inference/model gateway plus primary serious execution engine; each role has a separate contract and can fail independently. |
| LLM providers | Critical but abstracted | Reasoning/execution; health/failover required. |
| Memory store | Critical | Durable user/project/objective state. |
| LiveKit or equivalent voice runtime | Critical for voice | Turn detection/interruption/streaming. |
| Browser automation / CDP layer | Critical | Web interaction and verification. |
| Windows control APIs | Critical for desktop V1 | Local app/keyboard/pointer/window interaction. |
| MCP/adapter gateway | High | Standardized external capability access. |
| Git + test frameworks | Critical for self-development | Sandboxed changes, verification and rollback. |
| Messaging connectors | P1 | Remote continuity. |
| External capability packs | Optional | Must not become core runtime dependencies by accident. |

### 8.3 Key Blockers
| Blocker | Failure mode | Mitigation |
|---|---|---|
| Unknown current-state truth | Repository may contain built but unproven paths. | Phase 0 exact-build audit before major architecture work. |
| Architecture duplication | Multiple agents/frameworks may own planning/memory/tool loops. | One-manager rule and integration contracts. |
| Unlimited scope | “Do anything” cannot be a testable release criterion. | Capability families + Golden Objectives. |
| Memory pollution | Shared memory can become shared noise or sensitive-data leakage. | Scope/provenance/context compiler. |
| Tool overload | Too many schemas degrade reasoning and token efficiency. | Progressive discovery. |
| Self-improvement too early | Broken system can amplify itself. | Verification and rollback before self-development. |
| Signed-in browser risk | Sessions can expose high-impact authority. | Authentication ≠ authorization; exact-action approvals. |
| Third-party supply chain | Agent/tool dependencies change rapidly. | Pinning, review, health, isolation and retirement plan. |
| Always-on resource cost | Persistent assistants can leak RAM/CPU/API quota. | Resource governor, idle suspension, budgets. |
| Evaluation theater | Demo success can mask low reliability. | Repeatable benchmark + chaos tests + evidence ledger. |

### 8.4 Risk Assessment
| Risk | Severity | Primary controls |
|---|---|---|
| Incorrect privileged action | Critical | Exact-action policy + preview + verify + audit |
| Credential leakage | Critical | Secret isolation + redaction + scoped connector tokens |
| Prompt injection | Critical | Untrusted-data boundary + deterministic policy |
| Unauthorized security activity | Critical | Separate workspace + target contract + default disabled |
| Runaway self-modification | Critical | Sandbox/test/promotion/rollback |
| False completion | High | Evidence gate + independent verifier |
| Memory poisoning/conflict | High | Provenance + correction + confidence + scoped retrieval |
| Cascading multi-agent error | High | Least privilege + bounded roles + FRIDAY final authority |
| Supply-chain compromise | High | Version pin + review + isolation |
| Browser UI drift | High | Semantic/DOM-first perception + recovery tests |
| Provider outage/rate limit | High | Classification + checkpoint + bounded failover |
| Resource exhaustion | High | Governor + queue + worker limits |
| Excessive API spend | Medium | Objective budgets + routing + stop conditions |
| Runaway model-gateway context/token growth | High | Inference-only mode + compiled context + per-class budgets + growth guard |
| Voice false interruption | Medium | Tuned turn detection + recoverable continuation |
| User trust erosion | High | Transparent state/evidence; no fake metrics/capabilities |

## 9. Target Technical Architecture and Contracts
### 9.1 Runtime Layers
| L0  SURFACES     Voice \| Control Room \| CLI \| Remote Channels  L1  FRIDAY CONTROL PLANE     Intent \| Objective Ledger \| Planner \| Supervisor \| Interruption  L2  TRUST PLANE     Identity \| Policy \| Approval \| Audit \| Secret Broker  L3  CONTEXT PLANE     Memory \| Codebase Knowledge \| Context Compiler \| Skills  L4  CAPABILITY PLANE     Registry \| Health \| MCP \| CLI \| SDK \| HTTP \| Specialist Runtimes  L5  EXECUTION PLANE     Hermes \| Browser \| Desktop \| Documents \| Media \| Business \| Security  L6  TRUTH PLANE     Tests \| Verifier \| Evidence Ledger \| Metrics \| Release Gates |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|

### 9.2 Objective Schema
| Objective {   id, owner_id, created_at,   intent, goal, desired_outcome,   constraints[], risk_tier,   project_scope, memory_scope,   required_capabilities[],   plan_steps[], workers[],   approvals[], checkpoints[],   evidence[], retry_budget,   cost_budget, time_budget,   current_state, blocker,   final_result } |
|---|

### 9.3 Worker Contract
| WorkerRequest {   objective_id,   role,   goal,   constraints,   workspace,   context_package,   allowed_capabilities,   prohibited_actions,   expected_artifacts,   verification_plan,   reasoning_depth,   cancellation_token }  WorkerResult {   status,   artifacts,   actions,   test_results,   evidence,   errors,   unresolved_questions,   suggested_next_step } |
|---|

### 9.4 Hermes Dual-Role Provider Contract
Hermes is integrated through one logical gateway with two operational modes. FRIDAY chooses the mode; Hermes does not autonomously escalate from inference brokerage to execution.
| FRIDAY   \|   +--> Hermes MODEL_GATEWAY   \|      - inference only   \|      - provider/model discovery   \|      - credential resolution   \|      - bounded compiled context   \|      - usage + latency metadata   \|      - NO tools/subagents/execution loop by default   \|   +--> Hermes EXECUTION_ENGINE          - coding/building          - tools/skills/subagents as explicitly allowed          - workspace + test + evidence contract          - checkpoint/recovery |
|---|---|---|---|---|---|---|---|---|

| ModelGatewayRequest {   objective_id, task_class, context_package,   required_capabilities, preferred_quality_tier,   privacy_policy, max_input_tokens, max_output_tokens,   reasoning_budget, latency_budget,   provider_allowlist, provider_denylist, allow_failover }  ModelGatewayResult {   status, provider, model, response,   input_tokens, output_tokens, cached_tokens, reasoning_tokens,   latency_ms, failover_count, entitlement_state, warnings } |
|---|

| Important entitlement rule  Hermes can broker only provider routes that are actually configured and supported. FRIDAY must not equate a consumer subscription with generic API entitlement. OpenAI documents ChatGPT and API billing as separate products [R20]; Hermes documents its supported provider/authentication paths [R18][R19]. |
|---|

| Privacy rule  Using Hermes as a model gateway can centralize credential handling and reduce the number of components that receive provider secrets. It does not make upstream cloud inference local or invisible to the upstream provider. Privacy claims must describe the actual route. |
|---|

### 9.5 Memory Record Contract
| MemoryRecord {   id, type, owner_scope, project_scope,   subject, content,   source, source_ref,   created_at, updated_at,   confidence, importance,   supersedes[], contradicts[],   retention_policy,   last_retrieved_at } |
|---|

### 9.6 Capability Manifest
| CapabilityManifest {   id, name, version,   type: NATIVE\|MCP\|CLI\|SDK\|HTTP\|SIDECAR\|SKILL\|REFERENCE\|SPECIALIST_RUNTIME,   source, license,   trust_level, review_status,   permissions[], dangerous_actions[],   health_check, dependencies[],   cost_profile, latency_profile,   supported_platforms[],   default_state } |
|---|---|---|---|---|---|---|---|---|

### 9.7 Event Model
The control plane should be event-driven enough that UI, remote channels, audit, objective persistence and workers do not depend on direct synchronous coupling. At minimum, emit typed events for objective transitions, worker lifecycle, tool dispatch/result, approval request/decision, evidence attachment, failure classification, checkpoint, capability health and resource pressure.

## 10. Capability Ecosystem Strategy
### 10.1 Integration Classes
| Class | Definition | Examples |
|---|---|---|
| CORE | Permanent FRIDAY responsibilities; extremely small surface. | Objective ledger, policy, memory compiler, capability router, verifier. |
| EXECUTION ADAPTER | Bounded tool/runtime invoked for a task. | Browser Use, Maxun, Postiz, TruffleHog. |
| SPECIALIST RUNTIME | Larger agent/system given a bounded sub-objective. | Hermes, OpenHands, OpenMontage, Strix. |
| SKILL / PROCEDURE | Instructions/knowledge, not autonomous authority. | gstack skills, scientific-agent-skills, diagram-design, no-ai-slop. |
| REFERENCE ARCHITECTURE | Study/adapt patterns; do not run in production by default. | CrewAI, Auto-Company, OpenWorker, AnythingLLM depending on need. |
| CAPABILITY PACK | Policy-scoped collection activated for a domain. | Business, Media, Research, Authorized Security. |

### 10.2 Selection Rules
- Prefer adapting a small stable interface over embedding an entire external application.
- If two projects solve the same problem, choose one primary adapter and keep the other as a benchmark/reference unless there is a measured reason for both.
- Capabilities with broad autonomy must be wrapped by FRIDAY policy and objective contracts before receiving privileged access.
- Instruction libraries are skills, not “agents,” unless they actually execute independently.
- Licensing must be reviewed before commercial redistribution, especially copyleft/AGPL projects.
- Every integration must define health checks, failure behavior, version pin and uninstall path.
### 10.3 Research-Informed Architecture Patterns
| Reference | Product implication |
|---|---|
| Hermes Agent | Persistent memory, skill learning, subagents, scheduling, messaging gateways and multiple execution backends support using Hermes as a powerful bounded executor rather than rebuilding those primitives [R1]. Its provider system also motivates the separate MODEL_GATEWAY role [R18][R19]. |
| OpenViking | Progressive skill loading and runtime-dependent tool visibility support a context-efficient capability fabric [R2]. |
| Browser Use | CDP primitives plus explicit advice not to use a browser for simple public fetches supports mechanism-aware routing [R3]. |
| Graft | Durable codebase explanations target repeated repo rediscovery and motivate project codebase memory [R7]. |
| OpenWork | A small “search capabilities / execute capability” model across MCP/skills/connectors is a useful reference for capability distribution [R8]. |
| AgentMemory | One memory server shared across different coding agents validates the feasibility of cross-agent memory as a service [R9]. |
| Awesome Harness Engineering | Frames reliability around context, tools, planning, permissions, memory, verification, sandboxes and human-in-loop rather than model novelty [R10]. |
| Maxun | Recorder/AI extraction robots are candidates for structured web data workflows [R11]. |
| Postiz | Official OAuth and API-based social scheduling offers a compliant publishing adapter pattern [R12]. |
| OpenCTI / Strix | Useful specialized security systems, but their capabilities require strict authorized-security boundaries [R13][R14]. |

## 11. Authorized Security / OSINT Capability Packs
| Default state: DISABLED  Security/OSINT capability packs are not part of the ordinary “open browser / edit file / send email” permission pool. Passive public research and active scanning are distinct. Any active network or application assessment must be limited to systems the user owns or is explicitly authorized to test. |
|---|

### 11.1 Security Workspace Architecture
| NORMAL OBJECTIVE       │       ▼ GENERAL POLICY ───────────────► Normal capabilities       │       └── requests security capability                  │                  ▼           SECURITY GATE        Authorization contract?           │             │          NO            YES           │             │         BLOCK      Scope + target check                         │                         ▼              AUTHORIZED_SECURITY PACK                         │                   Evidence + audit |
|---|

### 11.2 Security Objective Contract
| SecurityAuthorization {   owner_identity,   target_scope[],   ownership_or_permission_basis,   allowed_actions[],   prohibited_actions[],   max_scan_intensity,   time_window,   data_retention,   expires_at,   approval_id } |
|---|

### 11.3 Passive vs Active
| Mode | Examples | Default |
|---|---|---|
| Passive public intelligence | Public DNS/search, technology identification, public threat intel, public repository secret scanning when authorized by repository access | May run under normal research policy depending on target and data sensitivity. |
| Low-impact active discovery | Port/service discovery on owned hosts, controlled subdomain validation | Requires authorized-security scope. |
| High-volume active discovery | Masscan-style wide scanning | Strong restriction; bounded owned ranges only, explicit approval. |
| Exploit validation / pentest | Security testing frameworks that produce PoCs or attempt exploitation | Explicit authorized scope, strongest policy, separate sandbox and audit. |
| Identity/SOCMINT | Username/email/phone/profile correlation | Public/business research only; privacy/harassment safeguards; targeted private-person dossiers are restricted. |

## 12. Test, Release and Operational Readiness
### 12.1 Test Pyramid
| Layer | Purpose | Examples |
|---|---|---|
| Unit | Deterministic logic | State transitions, policy, routing, context selection, manifest validation. |
| Integration | Subsystem contracts | Memory↔worker, MCP↔policy, browser↔verifier, remote↔objective. |
| E2E | Real user flows | Voice command, signed-in browser edit, coding + Playwright validation. |
| Golden Objectives | Outcome reliability | Cross-application tasks with predefined acceptance/evidence. |
| Chaos / fault injection | Recovery | Kill worker, drop network, rate limit provider, crash browser, restart core. |
| Security / adversarial | Boundary integrity | Prompt injection, approval confusion, out-of-scope security target, secret leakage. |
| Performance | Resource/latency | Idle resource use, memory retrieval P95, tool routing, concurrent workers. |

### 12.2 Release Gates
- ☐ 0 unexplained deterministic test failures.
- ☐ All P0 policy/security tests pass.
- ☐ Golden Objective release suite reaches the defined threshold.
- ☐ No R2/R3 action can bypass its approval/pre-authorization requirement.
- ☐ No out-of-scope active security tool execution is possible.
- ☐ Memory persistence, correction and cross-project isolation tests pass.
- ☐ Crash/restart continuation passes for long-running objectives.
- ☐ Provider and browser failure recovery tests pass.
- ☐ Capability registry matches what is actually installed and executable.
- ☐ No seeded secrets appear in prompts/logs/durable memory snapshots.
- ☐ Current build identity/version is visible in diagnostics and evidence.
- ☐ Clean-install/start/stop/upgrade/rollback paths are tested.
- ☐ Performance profile shows no uncontrolled worker/process/memory growth.
- ☐ Independent verifier approves consequential coding/browser Golden Journeys.
### 12.3 Operational Diagnostics
A single diagnostic view/command should report build identity, objective store health, memory store health, provider status, Hermes/worker health, browser connection, voice gateway, MCP/capability health, queue depth, resource pressure and most recent critical failures. Diagnostic output must be safe to share: secrets and sensitive user content are redacted by default.

## 13. Final V1 Acceptance Criteria
FRIDAY V1 is considered product-ready only when a supported user objective can move through the complete control loop below while preserving safety, continuity, truth and performance.
| INTENT   ↓ CONTEXT + MEMORY   ↓ REASON / CHALLENGE   ↓ PLAN   ↓ RISK + AUTHORITY   ↓ CAPABILITY / TEAM SELECTION   ↓ EXECUTION   ↓ OBSERVATION   ↓ TEST / CRITIQUE   ↓ RECOVER OR VERIFY   ↓ EVIDENCE   ↓ MEMORY UPDATE   ↓ RESULT |
|---|

- FRIDAY is the single final orchestration authority.
- Hermes is the default serious execution engine but remains replaceable through a worker contract.
- Hermes MODEL_GATEWAY can broker configured provider/model access without starting the full Hermes agent runtime.
- FRIDAY, not Hermes, decides whether a request is inference-only or full execution.
- Consumer subscription access is never misrepresented as generic API entitlement.
- Inference-only calls use bounded compiled context and pass token-growth protection.
- Simple tasks execute without unnecessary coding-agent delegation.
- Workers share one scoped logical memory service.
- Memory provenance, correction and project isolation work.
- Capability availability is truthful and progressively loaded.
- Browser and desktop operation are interruptible and evidence-driven.
- Signed-in state never automatically authorizes consequential actions.
- Every autonomous loop is bounded and has stuck detection.
- Provider/tool crashes do not destroy the objective or core runtime.
- Long-running objectives can resume after restart.
- Consequential completion requires evidence, not worker self-report.
- Critical permissions are deterministic and external to model reasoning.
- Authorized security tooling cannot escape its declared target/action scope.
- Self-development happens only through sandbox → test → verify → promote → rollback gates.
- The release suite has zero unexplained P0 failures and meets Golden Objective target metrics.
| Product statement  FRIDAY is a persistent AI operating layer whose purpose is to reduce the need for humans to manually operate their digital environment. FRIDAY manages. Hermes and specialists execute. Memory provides continuity. Capability Fabric provides reach. Policy defines authority. Verification defines truth. Learning improves future execution. The human remains the ultimate owner. |
|---|

## Appendix A. Security / OSINT Capability Register
The following register captures the tools supplied in the requirements. Inclusion here does not mean automatic installation or permission. Each integration still requires a capability manifest, license/source review and validation on the exact version selected.
| Tool | Domain | Intended role | Integration | Risk / default |
|---|---|---|---|---|
| OWASP Amass | Network | Attack-surface/subdomain mapping | CLI adapter | High; passive mode preferred, active requires security scope |
| Nmap | Network | Network/service discovery | CLI adapter | High; authorized targets only |
| Subfinder | Network | Passive subdomain discovery | CLI adapter | Medium; public/authorized use |
| Masscan | Network | High-speed port scanning | CLI adapter | Critical; disabled by default, tightly bounded ranges |
| Naabu | Network | Fast port discovery | CLI adapter | High; authorized targets only |
| DNSDumpster wrapper | Network | DNS/domain mapping | HTTP/SDK adapter | Medium; rate/source policy |
| Photon | Network | OSINT crawler | CLI adapter | Medium; public content, bounded crawl |
| RustScan | Network | Fast port discovery with Nmap handoff | CLI adapter | High; authorized targets only |
| Cloudflare-origin discovery utilities | Network | Origin infrastructure discovery | Restricted reference/adapter | Critical; do not optimize for bypassing protection |
| WhatWeb | Network | Web technology fingerprinting | CLI adapter | Medium; target authorization as needed |
| Sherlock | SOCMINT | Username discovery | CLI adapter | Medium; public data, anti-harassment controls |
| Osintgram | SOCMINT | Public Instagram OSINT | CLI adapter | High privacy sensitivity |
| WhatsMyName | SOCMINT | Username account checks | Dataset/CLI | Medium |
| TweetHarvest | SOCMINT | Public X timeline/keyword archival | CLI adapter | Medium; platform compliance |
| Toutatis | SOCMINT | Public Instagram account metadata | CLI adapter | High privacy sensitivity |
| SocialScan | SOCMINT | Email/username availability checks | CLI adapter | High identity sensitivity |
| Blackbird | SOCMINT | Username search | CLI adapter | Medium |
| Maigret | SOCMINT | Username correlation/dossier generation | CLI adapter | High; restrict targeted-person profiling |
| Instaloader | SOCMINT | Public Instagram media/metadata download | CLI adapter | Medium; platform/content rules |
| ScrapeGhost | SOCMINT | AI-assisted structured scraping | SDK/CLI candidate | Medium; source compliance |
| DeepFace | GEOINT/Identity | Local face recognition/attributes | Local SDK | Critical biometric data; tightly permissioned |
| Overpass Turbo / Overpass API | GEOINT | OpenStreetMap queries | HTTP adapter | Low/Medium |
| ExifTool / pyexiftool | GEOINT | Image/file metadata extraction | CLI/SDK | Medium; user-owned/authorized files |
| SunCalc | GEOINT | Sun/shadow calculations | Library | Low |
| Creepy | GEOINT | Location aggregation from public posts | Specialist adapter | High privacy sensitivity |
| Mapnik | GEOINT | Map rendering | Library/sidecar | Low |
| OpenStreetMap core/data | GEOINT | Map/geographic data | HTTP/data source | Low/Medium |
| Satellight | GEOINT | Satellite catalog lookup | Evaluate/adapter | Medium; verify project/version |
| Bellingcat Auto-Archiver | GEOINT/Research | Source/media archival | Workflow adapter | Medium; storage/provenance controls |
| Geopy | GEOINT | Geocoding | Python SDK | Low/Medium; provider terms |
| GHunt | Corporate/Identity | Google account public OSINT | CLI adapter | High identity sensitivity |
| Holehe | Corporate/Identity | Email account-existence checks | CLI adapter | High privacy sensitivity |
| theHarvester | Corporate/Identity | Public emails/domains/hosts aggregation | CLI adapter | High; authorized/public business research |
| PhoneInfoga | Corporate/Identity | Phone-number OSINT | CLI adapter | High privacy sensitivity |
| Ignorant | Corporate/Identity | Phone-linked account checks | CLI adapter | High privacy sensitivity |
| Infoga | Corporate/Identity | Email intelligence | Evaluate/CLI | High; verify maintenance and legality |
| LinkedInt | Corporate/Identity | Employee/name enumeration | CLI/reference | High; platform/compliance constraints |
| CrossLinked | Corporate/Identity | Search-engine-based employee/email enumeration | CLI adapter | High; business research only |
| EmailHarvester | Corporate/Identity | Email discovery | CLI adapter | High; business/authorized use |
| TruecallerJS | Corporate/Identity | Caller identity lookup | SDK/CLI | Critical privacy/compliance review |
| SpiderFoot | Threat Intel | Modular OSINT automation | Specialist runtime | High; scoped modules |
| Recon-ng | Threat Intel | Modular recon framework | Specialist runtime | High; authorized scope |
| Maltego Transform SDK | Threat Intel | Graph transform integrations | SDK/reference | Medium/High based on transform |
| TruffleHog | Threat Intel/DevSecOps | Secret discovery | CLI adapter | Medium on owned repos; evidence redaction |
| GitGuardian ggshield | Threat Intel/DevSecOps | Secret prevention/scanning | CLI adapter | Medium; CI/local use |
| Katana Framework | Threat Intel | Web reconnaissance framework | Evaluate carefully | High; exact project/version required |
| Snaked | Threat Intel | OSINT pipeline aggregation | Evaluate carefully | Medium/High; exact project/version required |
| IVRE | Threat Intel | Network recon data platform | Specialist runtime | High/Critical; authorized scope only |
| FinalRecon | Threat Intel | Web reconnaissance | CLI adapter | High; authorized target policy |
| OpenCTI | Threat Intel | Threat-intelligence knowledge platform | Specialist service | Medium; useful for CTI knowledge, source/provenance controls |

## Appendix B. External GitHub Project Integration Register
| Project | Potential role | Disposition | Priority | Product note |
|---|---|---|---|---|
| OpenHands | Coding specialist runtime | REFERENCE / optional specialist | P1 | Do not make a second global orchestrator. |
| Maxun | Web extraction/crawl/search | EXECUTION ADAPTER | P1 | Useful for structured extraction robots; isolate browser credentials. |
| browser-use | Browser CDP automation | EXECUTION ADAPTER / reference | P0/P1 | Strong candidate for browser primitives; still behind FRIDAY policy. |
| agent-reach | Public web/social retrieval | RESEARCH ADAPTER | P1 | Use for discovery/retrieval, not privileged account control. |
| Graft | Durable codebase knowledge | CODEBASE MEMORY | P1 | High value for reducing repeated repo rediscovery. |
| agency-agents | Reusable specialist role definitions | SKILL / REFERENCE | P1 | Adapt roles, do not spawn theatrical permanent agents. |
| codebase-memory-mcp | Codebase memory | MCP ADAPTER | P1 | Evaluate alongside Graft; avoid duplicate competing stores. |
| OpenMontage | Agentic video production | SPECIALIST RUNTIME | P2 | Media pack; isolate provider/API dependencies and render pipeline. |
| open-notebook | Research/document workspace | REFERENCE / optional service | P1 | Useful source-grounded knowledge UX patterns. |
| no-ai-slop | Quality/style guidance | SKILL | P1 | Use as quality heuristics, not subjective release proof. |
| i-have-adhd | Productivity/ADHD workflow repo | REFERENCE / optional skill | P2 | Evaluate exact content before customer use; never infer health status. |
| Strix | Autonomous security testing | AUTHORIZED SECURITY SPECIALIST | P2 | Owned/authorized targets only; strongest sandbox/approval. |
| Vane | Privacy-focused answer/search engine | RESEARCH REFERENCE / adapter | P2 | Could inform private search and source selection. |
| agenticSeek | Autonomous agent/search stack | REFERENCE | P2 | Study capabilities; avoid duplicate orchestration. |
| Scrapling | Web scraping/extraction | EXECUTION ADAPTER | P1 | Do not make anti-bot bypass a product KPI. |
| gstack | Engineering/product/QA skills | SKILL LIBRARY | P1 | Valuable structured review/QA workflows. |
| AnythingLLM | Local document/agent application | REFERENCE / optional service | P2 | Study multi-user/local document pipeline; avoid overlapping product shell. |
| Pipecat | Real-time voice pipelines | VOICE REFERENCE | P1 | Benchmark against LiveKit; do not replace working voice stack without evidence. |
| Postiz | Social scheduling/publishing | EXECUTION ADAPTER | P1 | Prefer official OAuth/API workflow; review AGPL implications. |
| CrewAI | Multi-agent orchestration framework | REFERENCE | P2 | Do not adopt as peer orchestrator; borrow patterns only if needed. |
| Cline | Coding agent / parallel worktrees | SPECIALIST / REFERENCE | P1 | Useful secondary coding worker and parallel-work pattern. |
| OpenViking | Memory/context/skills runtime | MEMORY/CAPABILITY REFERENCE | P1 | Strong progressive-loading and scoped-tool patterns. |
| agentmemory | Shared persistent memory server | MEMORY ADAPTER CANDIDATE | P1 | Directly aligned with cross-agent memory; evaluate against existing store. |
| diagram-design | Editorial diagram skill | SKILL | P2 | Use for polished diagrams when a diagram is superior to prose. |
| scientific-agent-skills | Scientific domain skill library | SKILL PACK | P2 | Progressively load domain procedures. |
| awesome-harness-engineering | Agent harness patterns | REFERENCE | P0/P1 | Use as reliability checklist for context/tools/permissions/verification. |
| anthropic-cybersecurity-skills | Cybersecurity skill library | AUTHORIZED SECURITY SKILL PACK | P2 | Skills do not grant active authority; security scope still required. |
| munder-difflin | 24/7 coordinated coding-agent office | REFERENCE | P2 | Study shared worker/continuity patterns; avoid nested manager loops. |
| OpenWork | Shared MCP/skills/capabilities workspace | CAPABILITY DISTRIBUTION REFERENCE | P1 | Useful search-capability/execute-capability abstraction. |
| firstmate | Agent/coworker project | EVALUATE | P2 | Audit exact capabilities/license before decision. |
| auto-company | 24/7 autonomous multi-agent company | REFERENCE ONLY | P2 | Useful for squad/memory/daemon ideas; autonomy requires stronger FRIDAY guardrails. |
| openworker | Desktop AI coworker | REFERENCE | P2 | Study desktop finished-work UX and local model/provider abstraction. |
| OpenMausBot | Desktop agent harness with approvals/computer lifecycle | REFERENCE | P2 | Useful approval/computer-control harness patterns; platform fit differs. |
| Medusa | Headless commerce platform | COMMERCE ADAPTER | P2 | Use only if FRIDAY needs direct commerce backend integration. |
| Smartstore | Commerce platform | COMMERCE ADAPTER / reference | P2 | Platform-specific; optional capability pack. |
| book-to-skill | Knowledge-to-agent-skill workflow | SKILL TOOLING | P2 | Potential method for converting trusted material into procedures after review. |

## Appendix C. Glossary
| Term | Definition |
|---|---|
| Capability | An executable tool, service, skill or specialist runtime registered in the Capability Fabric. |
| Capability Fabric | Registry, discovery, health, permission and invocation layer for FRIDAY-accessible capabilities. |
| Context Package | The bounded set of task-relevant instructions, memory, project information and permissions supplied to a worker. |
| Evidence | Independent observable result used to determine whether an action/objective succeeded. |
| Golden Objective | A stable end-to-end benchmark scenario with predefined acceptance criteria. |
| Hermes | Primary serious execution engine in the target architecture. |
| Objective | Durable representation of a user outcome, plan, state, authority, evidence and result. |
| Policy Engine | Deterministic authority layer that decides whether and under what conditions an action may run. |
| Skill | Procedural knowledge/instructions; not equivalent to a tool unless backed by executable capability. |
| Specialist Runtime | An agent/framework used for a bounded sub-objective under FRIDAY control. |
| Stuck Detection | Logic that detects repeated actions/errors or no measurable progress and triggers replan/escalation. |
| Verifier | Independent mechanism/worker that checks actual state against acceptance criteria. |
| Worker | Any bounded executor such as Hermes, Claude Code, a specialist agent, browser executor or domain runtime. |

## Appendix D. Research References
R1. Hermes Agent — Nous Research. Persistent memory, skills, subagents, schedules, messaging gateways, MCP and execution backends. https://github.com/NousResearch/hermes-agent
R2. OpenViking — Agent Capabilities. Context construction, progressive skills, tool visibility, sandboxing, cron/subagents. https://github.com/volcengine/OpenViking/blob/main/bot/docs/en/concepts/02-agent-capabilities.md
R3. Browser Use — Browser Actor / Skill. CDP browser primitives and browser-vs-fetch guidance. https://github.com/browser-use/browser-use/blob/main/browser_use/actor/README.md
R4. OWASP AI Agent Security Cheat Sheet. Least privilege, excessive autonomy, cascading failures, tool authorization, unbounded loops. https://cheatsheetseries.owasp.org/cheatsheets/AI_Agent_Security_Cheat_Sheet.html
R5. LiveKit Agents — Turns. Turn detection, VAD and interruption behavior. https://docs.livekit.io/agents/logic/turns/
R6. Model Context Protocol — Authorization / 2026 specification. Authorization and current protocol evolution; HTTP auth and security principles. https://modelcontextprotocol.io/specification/draft/basic/authorization
R7. Graft. Durable codebase understanding to reduce repeated agent rediscovery. https://github.com/trailhq/Graft
R8. OpenWork. Reusable capabilities/skills/MCP connections across compatible agents. https://github.com/different-ai/openwork
R9. AgentMemory. Shared persistent memory for multiple coding agents via MCP/HTTP. https://github.com/rohitg00/agentmemory
R10. Awesome Harness Engineering. Patterns for context, tools, planning, permissions, memory, verification and sandboxes. https://github.com/ai-boost/awesome-harness-engineering
R11. Maxun. Web extraction, scraping, crawling and search automation. https://github.com/getmaxun/maxun
R12. Postiz. Social scheduling; official OAuth/API compliance posture. https://github.com/gitroomhq/postiz-app
R13. OpenCTI. STIX-based cyber threat-intelligence knowledge platform. https://github.com/OpenCTI-Platform/opencti
R14. Strix. Automated application security testing with explicit authorized-use warning. https://github.com/usestrix/strix
R15. OpenMontage. Agentic video production workflows and tools. https://github.com/calesthio/OpenMontage
R16. Cline. Coding agent with CLI/IDE and parallel worktree task patterns. https://github.com/cline/cline
R17. Auto-Company. Autonomous multi-agent squad/consensus-memory reference with explicit guardrail warning. https://github.com/maxmiksa/auto-company
R18. Hermes Agent — AI Providers. Hermes inference-provider selection including OpenAI Codex OAuth, Anthropic, OpenRouter, Nous and other providers. https://github.com/NousResearch/hermes-agent/blob/main/website/docs/integrations/providers.md
R19. Hermes Agent — Provider Routing / Fallbacks. Provider routing, fallback behavior and auxiliary model/provider selection. https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/fallback-providers.md
R20. OpenAI Help — ChatGPT and API Billing. OpenAI states that ChatGPT subscriptions and API platform usage are billed and managed separately. https://help.openai.com/en/articles/9039756
Research accessed 4 September 2026. External projects evolve rapidly; implementation must re-check current versions, licenses, security posture and APIs at integration time.
