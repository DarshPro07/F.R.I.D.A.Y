# F.R.I.D.A.Y. / JARVIS
# Master Product Requirements Document

**Document Type:** Product Requirements Document (PRD) + Implementation Requirements Authority  
**Product Name:** F.R.I.D.A.Y.  
**Alias:** JARVIS  
**Version:** 6.0  
**Date:** 2026-09-19  
**Primary Platform:** Windows-first personal AI operating system  
**Secondary Environments:** Linux/WSL for CI, testing, server-side workers, and selected integrations  
**Primary Orchestrator:** FRIDAY  
**Primary Execution Runtime:** Hermes Agent  
**Primary Engineering Specialist:** Claude Code  
**Status:** Master requirements baseline for audit, implementation, verification, and future expansion  
**Audience:** Product owner, autonomous coding agents, Claude Code, Hermes, QA, security, architecture, and future contributors

---

# 0. Document Authority and Usage

This document is the product and engineering requirements authority for the F.R.I.D.A.Y. / JARVIS project.

It defines the product vision, target user experience, architecture, autonomy model, shared memory, context routing, model/provider strategy, skills, MCPs, browser/desktop/voice/communications, business and creative capability packs, security/OSINT boundaries, testing strategy, rollout plan, release gates, and adoption policy for external repositories.

## 0.1 Audit-Before-Build Rule

Before implementing any requirement, the engineering agent SHALL audit the current repository and classify it as one of:

- `EXISTING`
- `PARTIAL`
- `BROKEN`
- `MISSING`
- `DUPLICATED`
- `UNVERIFIED`
- `NOT_APPLICABLE`

The implementation SHALL reuse working architecture rather than creating duplicate systems merely because this PRD uses different names.

## 0.2 Evidence Rule

A capability is not complete because code exists. `VERIFIED` requires objective evidence such as tests, state read-back, CI results, browser/desktop observation, API confirmation, database state, call/booking confirmation, or another authoritative verifier.

No evidence means not verified.

---

# 1. Executive Summary

## 1.1 Problem Statement

Modern AI assistants remain primarily chat systems connected to tools. Even capable coding agents and autonomous frameworks commonly suffer from fragmented memory, context loss, provider dependence, excessive token use, unnecessary delegation, tool overload, weak long-running objective handling, unreliable browser/desktop execution, false success claims, poor cross-agent continuity, weak self-learning, and excessive human supervision.

The central purpose of FRIDAY is to reduce routine human interaction with the computer as far as safely and realistically possible.

FRIDAY should behave like a capable digital operator living inside the user's computing environment rather than a chatbot that only answers questions. The user should be able to specify an outcome and allow FRIDAY to determine the correct combination of reasoning, memory, tools, workers, and verification.

## 1.2 Proposed Solution

Build FRIDAY as a persistent AI operating system composed of the following layers:

1. **FRIDAY Core** — personality, primary user interaction, intent, final authority.
2. **Objective Engine** — persistent goals, task graphs, budgets, blockers, retries, completion criteria.
3. **Context Engine** — relevant-context selection and context-budget enforcement.
4. **Shared Memory Fabric** — durable knowledge across FRIDAY, Hermes, Claude Code, and specialists.
5. **Retrieval Router** — SQL, filtering, lexical, semantic, hybrid, full-document, code search, connectors, web.
6. **Model Fitness Router** — model/provider selection from health, privacy, quality, latency, and cost evidence.
7. **Hermes Runtime** — provider gateway, execution engine, skills, MCP, scheduling, messaging, tools.
8. **Claude Code Worker** — high-end software engineering specialist.
9. **Specialist Team Runtime** — bounded researchers, reviewers, contrarians, judges, QA, business specialists.
10. **Capability Fabric** — browser, desktop, files, GitHub, spreadsheets, voice, communications, business apps, authorized security tools.
11. **Verification Layer** — outcome proof before completion claims.
12. **Skill Intelligence Layer** — procedural memory creation, testing, maintenance, versioning, rollback, publication.
13. **Self-Audit / Self-Development Layer** — safe continuous improvement.

## 1.3 Product North Star

> The user describes an outcome in normal language. FRIDAY safely and economically chooses the smallest correct set of context, intelligence, workers, and capabilities required to accomplish it, verifies the result, learns from it, and asks the user only when a meaningful decision or external authorization is genuinely necessary.

## 1.4 Success Metrics

| Metric | Target |
|---|---:|
| Supported routine objectives completed without human execution | >= 90% |
| False completion claims | < 1% |
| Unauthorized consequential actions | 0 |
| LOCAL_ONLY/private context sent to disallowed provider | 0 |
| Correct retrieval strategy | >= 95% |
| Correct capability routing | >= 95% |
| Memory retrieval precision | >= 90% |
| Supported objective recovery after restart/crash | >= 99% |
| Browser/desktop golden-journey success | >= 95% |
| User cancellation acknowledgement | < 300 ms target |
| Unnecessary specialist delegation | < 5% |
| Provider route labeled healthy without semantic proof | 0 |
| Duplicate external side effects | 0 |
| Unbounded autonomous loops | 0 |
| Known P0 security/privacy issues at release | 0 |

---

# 2. Background and Context

## 2.1 Why This Product Is Needed

A technically advanced user currently has to coordinate ChatGPT, Claude, Claude Code, Hermes, terminals, IDEs, browsers, GitHub, Gmail, calendars, documents, spreadsheets, ecommerce tools, social platforms, CRM, voice systems, MCP servers, and local desktop applications.

The user becomes the orchestrator.

FRIDAY exists to absorb that coordination burden.

## 2.2 Core User Problem

The project has historically suffered from a recurring failure pattern:

- features exist but are not connected;
- agents produce code without solving the end-to-end objective;
- automation stops after intermediate phases;
- memory is fragmented;
- performance degrades under real use;
- tools are added without strong routing logic;
- success is sometimes claimed without proof;
- the user has to repeatedly say “continue”.

This PRD therefore prioritizes truth, integration, continuity, verification, and measurable behavior over feature count.

## 2.3 Business Objectives

FRIDAY should eventually:

- reduce repetitive computer work;
- coordinate long-running knowledge and engineering tasks;
- perform safe business operations;
- improve decision quality through adversarial reasoning;
- maintain shared memory and project knowledge;
- operate approved desktop/browser workflows;
- support remote control;
- handle scheduled and conditional work;
- learn reusable procedures;
- improve itself through controlled self-development.

---

# 3. Core Product Principles

## 3.1 One Manager, Many Workers

FRIDAY is the manager. Hermes, Claude Code, specialist agents, skills, MCPs, plugins, and external frameworks are workers or capabilities.

No external framework shall silently become a competing top-level brain.

## 3.2 Outcome Before Tool

FRIDAY first determines the desired outcome, then selects the method.

## 3.3 Eliminate -> Automate -> Delegate

For every work step:

1. Can the step be removed?
2. Can deterministic software do it reliably?
3. Only then: does AI judgment add value?

Filtering, SQL aggregation, retry timers, hashing, validation, authorization, idempotency, and state transitions should remain deterministic when possible.

## 3.4 API Before Fragile UI When Appropriate

Preferred order:

1. deterministic native capability;
2. official API;
3. narrow CLI;
4. MCP;
5. browser DOM/accessibility automation;
6. vision/coordinates as fallback.

## 3.5 Read Before Act

Need information -> search/scrape/extract.  
Need interaction -> stateful browser.

## 3.6 Event-Driven Autonomy

Persistent does not mean constantly inferring.

Workers should sleep while idle and wake for schedules, events, objectives, external changes, or user commands.

## 3.7 Verify Before Claim

No meaningful external or UI action may be claimed as successful without observable evidence.

---

# 4. Product Roles

## 4.1 FRIDAY

FRIDAY owns:

- user-facing identity;
- intent;
- objective creation;
- context selection;
- memory policy;
- autonomy;
- permissions;
- capability routing;
- provider/model policy;
- specialist creation;
- final verification;
- final communication.

## 4.2 Hermes

Hermes has two product roles.

### Model Gateway

FRIDAY uses Hermes as a provider/model inference layer.

### Execution Engine

FRIDAY delegates substantial bounded work packages to Hermes.

Hermes may use terminal, browser, skills, memory, MCP, delegation, messaging, and schedules, while FRIDAY remains the authority.

## 4.3 Claude Code

Claude Code is the high-end engineering specialist for:

- repository-wide analysis;
- architecture;
- debugging;
- implementation;
- migrations;
- large refactors;
- test construction;
- code review.

## 4.4 Specialist Workers

Potential roles:

- Researcher;
- Contrarian;
- Judge;
- QA Engineer;
- Security Reviewer;
- Business Analyst;
- Creative Director;
- Data Analyst;
- DevOps Worker;
- Browser Specialist;
- Communication Specialist.

Specialists are created only when they improve quality, isolation, or parallelism.

---

# 5. User Personas and User Stories

## 5.1 Primary Persona — Owner / Operator

Needs minimal computer interaction, trustworthy autonomy, durable memory, remote operation, business support, engineering coordination, and intellectually honest recommendations.

## 5.2 Secondary Persona — Team Member

Needs scoped project access, reusable workflows, auditability, and isolation from unrelated private memory.

## 5.3 Core User Stories

### US-001 — General Outcome Execution

As a user, I want to state what I need rather than which tools to use so that FRIDAY coordinates the work end-to-end.

### US-002 — Intellectual Sparring

As a user, I want FRIDAY to challenge my assumptions and failure modes so that I receive truth rather than agreement.

### US-003 — Software Development

As a user, I want FRIDAY to coordinate Hermes and Claude Code through a persistent engineering objective so coding work does not stop after every phase.

### US-004 — Screen Understanding

As a user, I want FRIDAY to inspect my authorized screen state and explain what is happening.

### US-005 — Remote Operation

As a user away from the computer, I want to message FRIDAY and safely perform approved tasks remotely.

### US-006 — Business Operator

As a founder, I want FRIDAY to research, plan, create, and operate approved business workflows.

### US-007 — Proactive Operation

As a user, I want FRIDAY to resume work automatically when pending dependencies resolve.

### US-008 — Learning

As a user, I want successful procedures and demonstrations to become reusable procedural knowledge.

### US-009 — Personal Continuity

As a user, I want preferences, projects, decisions, and procedures to persist across sessions.

### US-010 — Safe Autonomy

As a user, I want FRIDAY to act freely within clear boundaries and ask only for genuinely consequential decisions.

---

# 6. Core Functional Requirements

## FR-001 — Persistent Objective Engine

Every meaningful task SHALL be represented as a durable objective with:

- objective ID;
- desired outcome;
- priority;
- status;
- task graph;
- current worker;
- context;
- budget;
- evidence;
- blocker;
- retries;
- approvals;
- verifier;
- next action.

### Acceptance Criteria

- WHEN FRIDAY restarts, THEN recoverable objectives SHALL restore logical state.
- WHEN the user pauses an objective, THEN no new work SHALL begin until resumed.
- WHEN one subtask is blocked but independent tasks remain, THEN FRIDAY SHALL continue them.
- WHEN a verifier has not passed, THEN an objective SHALL NOT become `COMPLETED`.

## FR-002 — Autonomous Agentic Loop

Large objectives SHALL follow:

**AUDIT -> UNDERSTAND -> PLAN -> EXECUTE -> OBSERVE -> TEST -> CRITIQUE -> FIX -> RETEST -> VERIFY -> CONTINUE**

The system SHALL NOT stop after one file, task, test, phase, or partial success.

Every autonomous loop SHALL include:

- stop condition;
- progress metric;
- retry budget;
- iteration limit;
- time budget;
- token/cost budget;
- concurrency cap;
- stuck-loop detection.

## FR-003 — Shared Memory Fabric

FRIDAY SHALL expose a canonical durable memory service to authorized FRIDAY, Hermes, Claude Code, specialists, plugins, and MCP clients.

Memory domains:

- user;
- project;
- session;
- objective;
- procedural;
- codebase;
- relationship;
- capability.

Memory SHALL carry provenance, scope, freshness, and trust metadata.

## FR-004 — Expertise vs Situational Context

Stable architecture/rules/preferences belong to expertise context. Live pages, current email, current calendar, and current UI state belong to situational context.

Time-sensitive situational facts SHALL not be silently promoted to permanent truth.

## FR-005 — Context Budget Manager

For every substantive objective, FRIDAY SHALL determine a context budget and select only relevant memory, tools, skills, and code.

It SHALL support checkpoint/compaction and handoff before context exhaustion.

## FR-006 — Retrieval Router

FRIDAY SHALL select among:

- exact filter;
- structured query;
- SQL aggregation;
- lexical search;
- semantic search;
- hybrid retrieval;
- full-document/section retrieval;
- code/structural search;
- live connector;
- web research.

Exact counts/totals SHALL NOT use arbitrary top-K retrieval.

Coverage SHALL be labeled as:

- `COMPLETE`;
- `FILTERED_COMPLETE`;
- `TOP_K`;
- `APPROXIMATE`;
- `UNKNOWN`.

## FR-007 — Codebase Intelligence

FRIDAY SHALL maintain durable structural knowledge of supported repositories.

Expected capabilities:

- symbol search;
- call chains;
- architecture;
- impact analysis;
- class/function graph;
- routes;
- project index;
- dead-code analysis where available;
- ADR support.

A structural backend such as codebase-memory-mcp MAY be used behind an adapter.

## FR-008 — Resource / Memory / Skill / Capability / Plugin Separation

- **Resource:** source material.
- **Memory:** durable facts, preferences, decisions, experiences.
- **Skill:** reusable procedure.
- **Capability:** controlled external or internal action.
- **Plugin:** executable extension.

Each SHALL have separate lifecycle and trust policies.

## FR-009 — Skill Intelligence System

FRIDAY SHALL support skill:

- discovery;
- creation;
- staging;
- linting;
- testing;
- review;
- activation;
- use;
- measurement;
- improvement;
- versioning;
- rollback;
- deprecation;
- publication.

Generated skills SHALL NOT become trusted active production instructions without validation.

## FR-010 — Skill Candidate Detection

Verified objectives MAY generate a skill proposal when the process is reusable.

Useful candidates include repeated workflows, difficult debugging paths, deployment procedures, research methods, and user-taught procedures.

Trivial one-offs SHALL NOT become skills.

## FR-011 — Skill Lazy Loading

Default context SHALL contain only skill metadata. Full skill instructions and references SHALL load on demand.

## FR-012 — Codebase Skill Maintenance

A codebase skill SHALL declare relevant dependencies. When affected code changes, mark it `NEEDS_REVALIDATION`, run targeted tests, and update/version only when necessary.

Unrelated repository changes SHALL NOT invalidate every skill.

## FR-013 — Skill Publication

Possible targets:

- local;
- private Git;
- private organization;
- public Git;
- compatible skill hub/tap.

Public publication SHALL require privacy, secret, license, security, and quality gates plus explicit policy/approval.

## FR-014 — Memory-to-Skill Bridge

Repeated memories MAY form a skill proposal when they represent a reusable procedure. Small facts remain memory.

## FR-015 — Learn by Demonstration

Future workflow:

**OBSERVE -> INFER -> ASK MISSING DECISIONS -> DRAFT SKILL -> SANDBOX REPLAY -> VERIFY -> PROMOTE**

Secrets SHALL be parameterized rather than recorded literally.

---

# 7. Task and Agent Routing

## FR-020 — Task Classifier

Use the existing project taxonomy where equivalent rather than creating parallel classes.

Conceptual classes include:

- deterministic;
- simple reasoning;
- reasoning + tool;
- specialist;
- engineering executor;
- multi-worker;
- critical/high-risk.

## FR-021 — Eliminate / Automate / Delegate

Before creating an AI worker, FRIDAY SHALL check whether the step can be eliminated or deterministically automated.

## FR-022 — Width / Depth Planner

Parallelize only genuinely independent work. Keep tightly coupled sequential debugging/edit-test-repair flows with one worker.

## FR-023 — Specialist Team Creation

For decisions needing adversarial reasoning, FRIDAY MAY create bounded specialists such as optimistic analyst, contrarian, evidence researcher, failure analyst, technical expert, economist, and judge.

## FR-024 — Specialist Isolation

Each specialist receives a specific question, minimal context, scoped capabilities, budget, expected output, and stop condition.

---

# 8. Model and Provider Requirements

## FR-030 — Hermes Model Gateway

FRIDAY SHALL be able to use Hermes as a provider gateway without making Hermes the user-facing primary agent.

A pinned provider SHALL resolve its own compatible model/default. Cross-provider model leakage SHALL be impossible.

## FR-031 — Hermes Execution Engine

FRIDAY SHALL also use Hermes as a serious execution worker for appropriate long-running tool-based tasks.

## FR-032 — Claude Code Worker

Claude Code SHALL remain a specialized engineering executor under FRIDAY's objective and verification model.

## FR-033 — Model Fitness Router

Selection SHALL consider:

- task class;
- historical evaluation score;
- provider/model health;
- privacy requirements;
- latency;
- cost;
- context/tool capability;
- availability.

Preferred strategy: cheapest healthy model that meets the required quality threshold.

## FR-034 — Evidence-Based Provider Health

Credentials/configuration do not equal health. A route becomes healthy only after a current semantic probe of the intended provider/model/transport succeeds.

Empty/truncated replies SHALL NOT count as success.

## FR-035 — Local Model Route

FRIDAY SHOULD support approved local inference for classification, tagging, summarization, and LOCAL_ONLY workflows.

If LOCAL_ONLY cannot be served locally, fail closed with `NO_ROUTE`.

---

# 9. Browser and Web Requirements

## FR-040 — Web Read Router

For information gathering, prefer search, scrape, crawl, structured extraction, and authoritative APIs.

## FR-041 — Browser Engine Abstraction

Expose stable operations such as navigate, observe, click, type, select, upload, download, tabs, screenshot, and verify.

Potential backends may include Playwright, Browser Use, Chrome/CDP, and specialized browser workers.

## FR-042 — Browser Profiles

Support:

- `RESEARCH` isolated profile;
- `AUTHORIZED_USER` signed-in profile;
- `TEMPORARY` ephemeral profile.

Research pages SHALL NOT automatically receive signed-in user state.

## FR-043 — Browser Action Evidence

No action claim without actual action and observed post-state.

**PLAN -> ACTION -> OBSERVE -> VERIFY -> CLAIM**

## FR-044 — Human Verification / CAPTCHA

FRIDAY SHALL NOT be designed to bypass protective access controls. If a site requires human/CAPTCHA verification, pause and request appropriate human completion before continuing.

---

# 10. Desktop Requirements

## FR-050 — Desktop Control

Preferred hierarchy:

1. native API;
2. accessibility/UI automation;
3. window/control metadata;
4. vision;
5. coordinates last.

## FR-051 — Screen Understanding

FRIDAY SHALL inspect user-authorized screen content and answer questions about visible state.

## FR-052 — Desktop Evidence Contract

No action claim without evidence.

## FR-053 — Cancellation

User cancellation SHALL propagate through model generation, Hermes, Claude Code worker, browser actions, subprocesses, TTS, specialists, and pending capability calls.

## FR-054 — Desktop Golden Journeys

Minimum: launch app, Start Menu, find/open file, browser, form, upload, download, save, terminal command, cancel, recover after UI drift.

---

# 11. Voice Requirements

## FR-060 — Conversational Voice

FRIDAY SHALL support streaming VAD/turn detection, STT, reasoning, and TTS.

## FR-061 — Barge-In

User speech SHALL interrupt FRIDAY output appropriately.

## FR-062 — Mute Correctness

Mute SHALL stop microphone audio from entering STT.

## FR-063 — Voice Performance

Targets on supported healthy infrastructure:

- acknowledgement < 700 ms median;
- first audio <= 800 ms p50;
- <= 1.5 s p95.

## FR-064 — Voice Runtime

LiveKit remains the preferred initial realtime architecture unless measured evidence justifies replacement. Pipecat may be evaluated as a reference/alternative.

---

# 12. Communication and Calendar

## FR-070 — Communication Router

Normalize inbound/outbound communication across Gmail, Outlook, WhatsApp, Telegram, Slack, SMS, phone, and future channels.

## FR-071 — Communication Decisions

Supported decisions:

- `IGNORE`;
- `NOTIFY`;
- `SUMMARIZE`;
- `DRAFT`;
- `RESPOND`;
- `FORWARD`;
- `SCHEDULE`;
- `ESCALATE`.

## FR-072 — Communication Authority

Sending/replying depends on sender trust, channel, business relationship, commitment risk, and standing user authorization.

## FR-073 — Calendar Interface

Generic operations: availability, create, update, cancel, find, verify.

## FR-074 — Scheduling Adapter

Provider-specific scheduling lives behind a generic adapter. Prefer official APIs when equivalent.

---

# 13. Reservations

## FR-080 — Generic Reservation Intent

Normalize type, date, time window, location, budget, participants, preferences, requirements, exclusions, payment limits, cancellation limits.

## FR-081 — Reservation Execution

Priority:

1. official API;
2. supported web flow;
3. authorized phone call;
4. user escalation.

## FR-082 — Reservation Verification

A reservation is complete only after authoritative confirmation.

---

# 14. Phone Operator

## FR-090 — Phone Identity

Default behavior identifies FRIDAY as the user's assistant, not as the user.

## FR-091 — Inbound Calls

**CALL -> IDENTITY -> CONTEXT -> INTENT -> POLICY -> ANSWER/SCHEDULE/ESCALATE -> SUMMARY -> FOLLOW-UP**

## FR-092 — Outbound Calls

FRIDAY MAY call to reschedule, confirm, book, request status, and coordinate routine matters within delegated authority.

## FR-093 — Call Outcomes

- `ANSWERED`;
- `NO_ANSWER`;
- `VOICEMAIL`;
- `BUSY`;
- `FAILED`;
- `DISCONNECTED`;
- `ESCALATED`;
- `BOOKED`;
- `FOLLOW_UP`.

## FR-094 — Telephony Runtime

LiveKit/SIP is the preferred first architecture because it extends the existing voice direction while keeping telephony providers behind adapters.

---

# 15. Proactive Runtime

## FR-100 — Event Bus

Support time, calendar, email, message, phone, file, GitHub, CI, browser, webhook, objective, provider-health, and system events.

## FR-101 — Heartbeat Runtime

Workers sleep while idle. Trigger -> bounded work -> persist -> sleep.

## FR-102 — Scheduled Objectives

Support one-time and recurring objectives.

## FR-103 — Conditional Objectives

Examples: notify when CI is green; continue when email arrives; resume when file exists.

## FR-104 — Pending Dependency States

- `WAITING_EMAIL`;
- `WAITING_APPROVAL`;
- `WAITING_PROVIDER`;
- `WAITING_CI`;
- `WAITING_BOOKING`;
- `WAITING_FILE`;
- `WAITING_TIME`.

## FR-105 — Morning Brief

Optional brief may combine calendar, important messages, active objectives, deadlines, blockers, metrics, system health, and suggested priorities.

## FR-106 — Notification Intelligence

Notify only when user action, important state change, completion, meaningful failure, deadline, or high-priority communication justifies interruption.

---

# 16. Business Capability Packs

Business packs are lazy optional capabilities, not core prompt baggage.

## FR-110 — Research Pack

Market, competitor, product, customer, and business-model research.

## FR-111 — Lead/CRM Pack

**signal -> research -> score -> personalize -> policy -> outreach -> wait -> response -> qualification -> calendar**

## FR-112 — Proposal Pack

**meeting -> transcript -> requirements -> research -> proposal -> review -> send**

## FR-113 — Ecommerce Pack

Product research, catalog, descriptions, CRO, Shopify/commerce operations, analytics, and customer workflows.

Medusa and Smartstore may be adapters/reference platforms.

## FR-114 — Social Publishing Pack

Generic: create, preview, approve, schedule, publish, verify, analytics.

Postiz-style infrastructure may be evaluated as an adapter.

---

# 17. Creative Capability Pack

## FR-120 — Creative Team

Optional specialists: Creative Director, Copywriter, Image Generator, Motion Designer, Web Designer, Video Editor, QA Reviewer.

## FR-121 — Website Builder

**brief -> references -> IA -> design -> build -> browser -> screenshots -> compare -> repair -> performance -> release**

## FR-122 — Media Pipeline

Coordinate image/video generation, voice, editing, QA, and publishing.

OpenMontage and similar repositories remain optional adapters/specialists.

---

# 18. Research and Knowledge Workspace

## FR-130 — Research Notebook

Optional workspace for documents, sources, notes, semantic search, citations, and research collections.

Open Notebook is an optional candidate/reference.

## FR-131 — Deep Research Mode

Use independent perspectives only for decisions warranting the cost. Example roles: Practitioner, Academic, Skeptic, Economist, Historian. Produce contradiction map, synthesis, adversarial review, and primary-source verification.

---

# 19. Capability Broker

## FR-140 — Shared Capability Catalog

FRIDAY SHALL maintain searchable capability metadata instead of injecting every tool schema.

Conceptual interface:

- `search_capabilities(query, constraints)`
- `execute_capability(capability_id, input)`

OpenWork is a high-value reference for this design.

## FR-141 — Capability Metadata

Every capability SHALL expose ID, name, category, backend, version, license, authentication, health, permissions, risk, cost, latency, allowed data classes, operations, last verified.

## FR-142 — Capability States

`REGISTERED`, `INSTALLED`, `AUTHENTICATED`, `HEALTHY`, `AVAILABLE`, `DEGRADED`, `BROKEN`, `BLOCKED`.

Configured does not mean healthy.

---

# 20. Privacy and Local-First Requirements

## FR-150 — Data Classification

- `PUBLIC`;
- `INTERNAL`;
- `PERSONAL`;
- `CONFIDENTIAL`;
- `SECRET`;
- `LOCAL_ONLY`.

## FR-151 — Physical Privacy Enforcement

LOCAL_ONLY data SHALL be physically unroutable to public providers. If no approved local route exists, return `NO_ROUTE`.

## FR-152 — Local Execution

FRIDAY SHOULD support local inference/capabilities for low-cost and privacy-sensitive work. AgenticSeek is an architectural reference, not a mandatory dependency.

---

# 21. Authorized Security and OSINT Capability Pack

This pack SHALL be separate from ordinary FRIDAY capabilities.

## FR-160 — Authorization Modes

- `PASSIVE`: public-information research.
- `AUTHORIZED_ACTIVE`: explicit authorized target scope exists.
- `BLOCKED`: active testing unavailable.

## FR-161 — Active Scope Record

Active security tooling SHALL require target, target owner, authorization evidence, allowed techniques, scope window, expiry, and audit ID.

## FR-162 — Network / Infrastructure Tools

Candidates: Amass, Nmap, Subfinder, Naabu, RustScan, WhatWeb, Photon. Very high-volume scanners such as Masscan SHALL NOT be default capabilities.

## FR-163 — Public SOCMINT

Candidates: Sherlock, WhatsMyName, Blackbird, Maigret, Instaloader, approved public-data utilities.

Tools dependent on private account-recovery endpoints or questionable terms require legal/security review before adoption.

## FR-164 — GEOINT

Candidates: OpenStreetMap/Overpass, ExifTool, SunCalc, Geopy, Mapnik, public satellite catalog adapters.

Face recognition is limited to appropriate user-provided/authorized contexts.

## FR-165 — Corporate Intelligence

Potential: theHarvester, official registries, public company/domain research, approved enrichment services.

## FR-166 — Threat Intelligence

Potential: SpiderFoot, OpenCTI, TruffleHog, GitGuardian/ggshield, IVRE, approved feeds.

Repository secret scanning is strongly recommended.

---

# 22. Self-Learning and Self-Audit

## FR-170 — Learn from Verified Work

After verified work, FRIDAY evaluates what worked, what failed, what is reusable, and whether memory, skill, code, policy, config, test, or documentation should change.

## FR-171 — Change-Type Classifier

Supported change types:

- `MEMORY`;
- `SKILL`;
- `CODE`;
- `CONFIG`;
- `POLICY`;
- `TEST`;
- `DOCUMENTATION`.

## FR-172 — Self-Audit

Audit routing integrity, index truth, freshness, duplication/bloat, capability health, context placement, provider health, stale skills, policy drift.

Modes: lightweight daily, standard weekly, full after major upgrade.

---

# 23. Controlled Self-Development

## FR-180 — Self-Development Loop

**detect weakness -> classify -> isolate -> implement -> test -> adversarial review -> benchmark -> promote/rollback**

## FR-181 — Isolated Changes

Self-development code changes SHOULD occur in an isolated branch/worktree.

## FR-182 — Promotion Gate

Promotion requires relevant tests, security/invariant checks, evaluations/benchmarks where relevant, and evidence review.

Trust/policy/verifier roots require stronger authorization.

---

# 24. Skill / MCP / Plugin Strategy

## FR-190 — Skill

Use skills for procedural knowledge and reusable operating instructions.

## FR-191 — MCP

Use MCP for clean RPC-style integrations where tool exposure can be filtered. Avoid huge dangerous MCP surfaces where a narrow capability is simpler.

## FR-192 — Plugin

Use plugins for executable extensions with a defined interface. Untrusted skill folders SHALL NOT be arbitrary Python plugin paths.

## FR-193 — External Framework Rule

External agent frameworks remain adapters, specialist runtimes, capability packs, or references unless evidence justifies deeper adoption.

---

# 25. External Resource Adoption Matrix

| Resource | Role | Recommendation |
|---|---|---|
| Hermes Agent | model gateway + execution + skills + MCP | CORE |
| Claude Code | engineering specialist | CORE WORKER |
| OpenHands | software-engineering specialist/reference | EVALUATE |
| Maxun | browser/data automation | EVALUATE |
| Browser Use | browser backend | P1 ADAPTER |
| Agent Reach | external connectivity | EVALUATE |
| Graft | workflow/agent reference | EVALUATE |
| agency-agents | specialist-role patterns | REFERENCE |
| codebase-memory-mcp | structural code intelligence | P1 ADAPTER |
| OpenMontage | media/creative | P2 PACK |
| Open Notebook | research workspace | P2 ADAPTER |
| no-ai-slop | quality heuristics | REFERENCE |
| i-have-adhd | productivity UX patterns | OPTIONAL |
| Strix | authorized security testing | RESTRICTED PACK |
| Vane | evaluate after audit | EVALUATE |
| AgenticSeek | local-first architecture | P1 REFERENCE |
| Scrapling | web extraction | P1 CAPABILITY |
| gstack | coding workflow patterns | REFERENCE |
| AnythingLLM | RAG/knowledge patterns | REFERENCE |
| Pipecat | voice comparison | EVALUATE |
| Postiz | social publishing | P2 ADAPTER |
| CrewAI | multi-agent design reference | REFERENCE |
| Cline | coding-agent patterns | REFERENCE |
| OpenViking | context/resource/memory/skill architecture | HIGH-VALUE CANDIDATE |
| agentmemory | memory patterns | EVALUATE |
| diagram-design | design/diagram skill | CREATIVE PACK |
| scientific-agent-skills | domain skills | OPTIONAL |
| awesome-harness-engineering | harness/eval patterns | REFERENCE |
| anthropic-cybersecurity-skills | defensive/security skills | RESTRICTED PACK |
| munder-difflin | unknown until audited | EVALUATE |
| OpenWork | shared capability broker/reference | HIGH-VALUE REFERENCE |
| firstmate | evaluate | EVALUATE |
| auto-company | autonomous company patterns | BUSINESS RESEARCH |
| openworker | worker patterns | REFERENCE |
| OpenMausBot | durable specialist identities | HIGH-VALUE REFERENCE |
| Medusa | ecommerce adapter/platform | BUSINESS PACK |
| Smartstore | ecommerce reference/platform | BUSINESS PACK |
| book-to-skill | knowledge -> skill pipeline | P1 SKILL INTELLIGENCE |

---

# 26. Non-Functional Requirements

## NFR-001 — Performance

| Metric | Target |
|---|---:|
| Local UI action | < 200 ms target |
| User acknowledgement | < 500 ms target |
| Voice acknowledgement | < 700 ms median target |
| Cancellation acknowledgement | < 300 ms target |
| Local memory query P95 | < 500 ms target |
| Core idle CPU | < 5% target |
| Unnecessary heavy-agent routing | < 5% |

Avoid full memory graph rebuilds per delegation, newest-N-only complete retrieval, thread-per-request gateway designs, loading all skills/MCPs, re-indexing unchanged repos, and polling when event-driven behavior is possible.

## NFR-002 — Reliability

- persisted objectives;
- crash/restart recovery;
- deterministic state transitions;
- bounded retries;
- idempotent external effects;
- accurate health state;
- failure classification;
- no completion without verification.

## NFR-003 — Security

- least privilege;
- deterministic permission layer outside LLM;
- protected trust roots;
- secret redaction;
- replay protection;
- external content untrusted by default;
- capability-specific permissions;
- audit trail;
- secure data routing;
- explicit active-security authorization.

## NFR-004 — Privacy

Private information is disclosed only when relevant and authorized. Logs minimize sensitive payloads. Voice/call retention follows explicit policy.

## NFR-005 — Accessibility

Support keyboard navigation, text alternative to voice, visible focus, readable status, captions/transcripts where feasible, and state not conveyed by color alone.

## NFR-006 — Platform Support

Initial: Windows 10/11, Chromium, WSL/Linux for CI/compatibility. Future: macOS, Linux desktop, mobile companion.

## NFR-007 — Maintainability

Core interfaces are versioned, provider code lives in adapters, skills remain single-purpose, optional integrations fail without crashing core, and architectural invariants are testable.

---

# 27. UI / UX Requirements

## 27.1 One Assistant Experience

The user should see FRIDAY, not implementation complexity.

Example:

```text
FRIDAY

State: Acting
Objective: Fix provider routing and verify live routes
Current: Running targeted provider health tests
Evidence: 4/6 routes verified
Blocked: 2 external provider/account issues
Next: Update route registry and run regression
```

## 27.2 Main Views

- Core;
- Objectives;
- Memory;
- Skills;
- Capabilities;
- Team;
- Communications;
- Calls;
- Automations;
- Activity;
- System.

## 27.3 Agent Team View

Show specialists only when real workers exist. No fake swarm UI.

---

# 28. Error Handling and Edge Cases

FRIDAY SHALL explicitly handle:

- internet unavailable;
- provider unavailable;
- quota exhaustion;
- empty model output;
- unsupported provider/model;
- browser crash;
- page change;
- CAPTCHA/human verification;
- expired login;
- microphone unavailable;
- voice provider failure;
- user interruption;
- conflicting memory;
- poisoned external content;
- stale skills;
- malformed/unavailable MCP;
- worker crash;
- machine restart;
- duplicate webhook;
- duplicate scheduler execution;
- disk/memory pressure;
- SQLite contention;
- partial external action;
- authorization timeout;
- wrong repository/worktree;
- corrupted checkpoint;
- tool reports success but resulting state did not change.

---

# 29. Verification and Testing Strategy

## 29.1 Test Layers

**UNIT -> INTEGRATION -> ADVERSARIAL -> GOLDEN JOURNEY -> E2E -> SOAK where applicable**

## 29.2 Negative-Control Rule

Every release/safety guard SHALL include a test proving the guard can fail.

Examples:

- fake provider health -> red;
- LOCAL_ONLY public route -> red;
- approval timeout -> execution blocked;
- no tool call -> action success impossible;
- duplicate idempotency key -> one external effect;
- protected-file write -> blocked.

## 29.3 Golden Journeys

1. Store preference -> new session -> retrieve preference.
2. Open Start Menu -> real action -> observed evidence.
3. Web research -> extraction before browser when possible.
4. Browser form -> action -> authoritative read-back.
5. Code task -> edit -> tests -> verifier.
6. Provider failure -> fallback/no-route.
7. Budget exhaustion -> pause.
8. Context handoff -> new session -> resume.
9. Calendar scheduling.
10. Incoming message -> correct communication policy.
11. Reservation -> confirmation -> calendar.
12. Inbound phone -> schedule/message.
13. Outbound phone -> structured result.
14. Remote command -> nonce/replay protection.
15. Prompt injection -> blocked.
16. Learned skill -> fresh objective -> reuse.
17. Code change -> affected skill revalidated.
18. Crash -> objective restored.
19. User STOP -> cancellation propagation.
20. Long soak -> terminal verdict.

---

# 30. Success Metrics and Measurement

## 30.1 Objective Metrics

- autonomous completion rate;
- verifier success rate;
- human intervention rate;
- retries;
- cost/objective;
- duration/objective.

## 30.2 Intelligence Metrics

- retrieval-route accuracy;
- model-route accuracy;
- capability-route accuracy;
- memory precision;
- false-confidence rate.

## 30.3 Capability Metrics

Per capability: success rate, latency, cost, retries, failure rate, last verified.

## 30.4 Trust Metrics

Target zero for policy bypasses, secret leaks, LOCAL_ONLY egress, duplicate external side effects, false UI action claims, and false health claims.

---

# 31. Timeline and Phased Rollout

## Phase 0 — Repository Truth Audit

- map repo;
- inventory modules;
- classify every requirement;
- find duplicates;
- identify false claims/broken paths;
- run current test evidence.

Exit gate: architecture mapped and evidence-backed.

## Phase 1 — Context + Routing Intelligence

- Context Budget Manager;
- provenance;
- Retrieval Router;
- task classification mapping;
- Width/Depth Planner;
- model fitness infrastructure.

Exit gate: retrieval benchmark, no top-K exact-count bugs, no cross-provider leakage, LOCAL_ONLY enforced.

## Phase 2 — Memory + Skills + Learning

- canonical shared memory;
- codebase memory;
- skill lifecycle;
- learning;
- self-audit;
- session handoff.

Exit gate: durable cross-session recall, lazy skills, skill revalidation, memory poisoning blocked.

## Phase 3 — Browser + Desktop

- web read router;
- browser abstraction;
- signed-in profile isolation;
- action evidence;
- desktop control;
- cancellation.

Exit gate: golden journeys >= target and false action claims = 0.

## Phase 4 — Communication + Calendar + Reservations

- Communication Router;
- authority policy;
- calendar;
- scheduling;
- reservations.

## Phase 5 — Phone Operator

- LiveKit/SIP;
- inbound/outbound calls;
- assistant identity;
- call policy;
- structured outcomes.

## Phase 6 — Proactive Runtime

- Event Bus;
- heartbeats;
- scheduled objectives;
- conditional objectives;
- pending dependencies;
- morning brief;
- notification intelligence.

## Phase 7 — Business + Creative Capability Packs

Build optional lazy capability packs.

## Phase 8 — Authorized Security / Intelligence Pack

Build behind authorization/scope controls.

## Phase 9 — Controlled Self-Development

**detect -> isolate -> implement -> test -> adversarial review -> benchmark -> promote -> monitor -> rollback**

---

# 32. Dependencies and Blockers

Core dependencies:

- Hermes Agent;
- Claude Code;
- persistent database;
- shared memory layer;
- browser runtime;
- Windows desktop APIs;
- LiveKit;
- provider credentials;
- Git;
- CI;
- secure secret handling;
- MCP support.

Optional/pluggable dependencies:

- Browser Use;
- codebase-memory-mcp;
- OpenViking;
- OpenWork;
- Open Notebook;
- Pipecat;
- Postiz;
- Medusa;
- Smartstore;
- authorized security/OSINT tooling;
- additional specialist frameworks.

Docker SHALL NOT be a mandatory product requirement unless a specific capability clearly requires it and no simpler deployment path exists.

---

# 33. Risk Assessment

| Risk | Severity | Mitigation |
|---|---|---|
| Prompt injection | Critical | untrusted-content boundary + deterministic policy |
| Unauthorized browser/desktop action | Critical | capability permissions + read-back verification |
| Secret leakage | Critical | secret broker + redaction + data classes |
| LOCAL_ONLY public egress | Critical | physical route constraint |
| Runaway autonomy | Critical | time/token/cost/iteration caps |
| Memory poisoning | High | provenance + trust + promotion gates |
| Model/provider drift | High | live probes + eval registry |
| Over-delegation | High | Width/Depth Planner |
| Tool/MCP bloat | High | capability broker + filtering |
| Browser UI changes | Medium | semantic targeting + re-observation |
| Duplicate architectures | High | audit-before-build |
| External API changes | Medium | adapters + health checks |
| Self-modification weakens safety | Critical | protected trust roots + isolated worktree |
| Security-tool misuse | Critical | explicit authorized scope |
| Public skill leaks private data | Critical | privacy/secret/license gates |

---

# 34. Production Acceptance Criteria

FRIDAY SHALL NOT be described as production-ready until:

```text
[ ] repository architecture audited
[ ] every P0 requirement classified
[ ] no unexplained deterministic failures
[ ] exact release commit CI green
[ ] soak reaches a defensible terminal PASS
[ ] persistent objective recovery verified
[ ] completion requires verifier
[ ] budget limits physically enforced
[ ] provider/model routing verified
[ ] provider health evidence-based
[ ] LOCAL_ONLY isolation verified
[ ] prompt-injection suite passes
[ ] protected trust roots enforced
[ ] shared memory verified
[ ] skill creation/reuse verified
[ ] codebase skill revalidation verified
[ ] browser golden journeys pass
[ ] desktop action evidence verified
[ ] cancellation verified
[ ] communication policy verified
[ ] scheduler idempotency verified
[ ] secret scan clean
[ ] active security tooling requires explicit scope
[ ] self-development cannot weaken policy
```

---

# 35. Open Questions

1. Which canonical shared-memory backend should become the long-term source of truth?
2. Should OpenViking become the shared context/memory layer or remain optional?
3. Should codebase-memory-mcp be enabled for every code repository or only complex ones?
4. Which messaging channels ship first?
5. Which SIP/telephony provider should be primary for India/international use?
6. Which STT/TTS stack meets the real hardware latency/quality target?
7. Which tasks should receive autonomy level A4/A5 by default?
8. Which signed-in browser operations may run without per-action confirmation?
9. What call/voice transcript retention policy applies?
10. Which business capability pack ships first?
11. Which skills are private versus publishable?
12. What permanent multi-laptop/shared-state architecture is preferred?
13. Which self-development areas remain permanently human-controlled?

---

# 36. Research-Informed Architecture Notes

## Hermes Agent

High-value because it already supports persistent memory, skills with progressive disclosure, MCP, provider routing, scheduling, messaging, and delegation. FRIDAY should orchestrate those capabilities rather than duplicating them.

## OpenViking

High-value candidate because it explicitly models resources, memories, and skills and supports durable semantic memory namespaces and reusable execution trajectories.

## OpenWork

High-value reference for a compact capability-broker model where multiple agents can share skills, plugins, MCP connections, and connected services via search/execute semantics.

## codebase-memory-mcp

High-value candidate for persistent structural code intelligence. It builds and queries a code knowledge graph and leaves model reasoning to the MCP client.

## Browser Use

Useful candidate as one browser backend, not as the FRIDAY control plane.

## OpenHands / Cline / CrewAI / Other Agent Frameworks

Useful as references or specialists. They should not replace FRIDAY's primary control plane by default.

---

# 37. External Research Sources

Current upstream/primary sources consulted for this PRD revision include:

- NousResearch Hermes Agent: https://github.com/NousResearch/hermes-agent
- Hermes MCP guidance: https://github.com/NousResearch/hermes-agent/blob/main/website/docs/guides/use-mcp-with-hermes.md
- Hermes Skills: https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/skills.md
- Hermes Persistent Memory: https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/memory.md
- OpenViking: https://github.com/volcengine/OpenViking
- OpenViking Skills: https://github.com/volcengine/OpenViking/blob/main/docs/en/api/04-skills.md
- OpenWork: https://github.com/different-ai/openwork
- Codebase Memory MCP: https://github.com/DeusData/codebase-memory-mcp
- Anthropic documentation: https://docs.anthropic.com/
- LiveKit documentation: https://docs.livekit.io/

These are architectural inputs, not mandatory dependencies.

---

# 38. Final Product Definition

FRIDAY is not defined by the number of tools, repositories, MCP servers, models, or agents attached to it.

FRIDAY is successful when it reliably performs:

```text
USER INTENT
    ↓
UNDERSTAND
    ↓
CONTEXT
    ↓
MEMORY
    ↓
CHALLENGE ASSUMPTIONS
    ↓
CHOOSE METHOD
    ↓
CHOOSE MODEL / WORKER / CAPABILITY
    ↓
EXECUTE
    ↓
OBSERVE
    ↓
VERIFY
    ↓
LEARN
    ↓
PERSIST
    ↓
REPORT ONLY WHAT MATTERS
```

The permanent architectural rule is:

> **One manager. Many capabilities. Minimal relevant context. Explicit authority. Evidence-backed outcomes. Durable learning.**

That is the target definition of F.R.I.D.A.Y.
