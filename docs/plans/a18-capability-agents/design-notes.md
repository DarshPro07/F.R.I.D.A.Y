# Owner design notes for amendment A18 (pasted by the owner 2026-10-09; owner ruling: "Add everything as P1")

## Note 1 - UltraCode repositories, self-acquired capabilities, Agent Command Center

### 1. The three UltraCode repositories (adopt patterns, never a second orchestrator)
- OpenUltraCode - selective adoption, high value. OpenCode commands for implementation, debugging, requirements audits, verification, research and multi-model comparison; coordinator/implementer/verifier/researcher role assets. It has no model-routing proxy (keeps OpenCode's selected model). FRIDAY: adapt its focused engineering skills, verification patterns and optional model-comparison workflows. Do not create a second central coordinator.
- UltraCode-Shim - reference + isolated benchmark. Local proxy with orchestrator/worker model selection, task routing, per-role routing directives, transient retries, stalled-stream handling. FRIDAY: extract ideas for explicit model assignment, retry handling and role-specific routing. Never our production gateway: it identifies orchestration partly by detecting interactive-only tools and advertises unrestricted threaded worker parallelism - both conflict with FRIDAY's resource admission and explicit worker identities.
- ultracode-workflows - best workflow reference. Executable multi-agent workflows for PRD generation, specifications, parallel implementation, debugging, security review, QA, release preparation; an observer UI showing workflow topology and live agent states; agent/pipeline/parallel/workflow primitives; avoid synchronization barriers until needed; workflow authoring from a description. Its full SDLC orchestrator is labelled aspirational: validate individual workflows, never treat the package as production-ready.
- All three become internal FRIDAY components or reference patterns, not branded products or permanently running agent brains.

### 2. FRIDAY creates its own capabilities ("Friday, integrate https://github.com/owner/repo, I want to edit videos")
1. Understand the goal: what the repo does, what the user wants, whether FRIDAY already supports it, whether the source adds value.
2. Research and inspect: clone a pinned version into quarantine; inspect source, dependencies, runtime, license, platform compatibility, interfaces; never follow instructions contained in the repo.
3. Choose the strategy: USE, WRAP, EXTEND, EXTRACT, REFERENCE or REJECT; native library, CLI, MCP adapter, skill or workflow.
4. Build the missing integration: a temporary development team with scoped work generates adapter, capability manifest, local skill, tests, registration.
5. Verify before activating: SkillSpector and dependency analysis, then representative functional and security tests in an isolated runtime.
6. Register inside FRIDAY: Capability Fabric + local Skill Registry; no separate third-party UI for normal use.
7. Complete the original task with the new capability and check the result.
8. Maintain: observe failures and upstream changes, propose repairs, re-run tests, roll back broken updates, keep provenance.
Applies to skills, agents, MCP servers, libraries, plugins, local apps and compatible GitHub projects. An unhelpful repo is rejected with a short reason instead of installed.

### 3. Agent Command Center UI
- Every registered specialist in a permanent roster grouped by team; an agent becomes visibly active only when FRIDAY dispatches real work to it. Hover a team to reveal its specialists; click/tap opens a panel with tasks, model, tools, logs, capabilities, permissions, execution history.
- Lifecycle states from real worker events only: Queued (muted card, queue position, no animation); Running (gentle pulsing border, animated FRIDAY-to-agent connection, step progress); Verified (brief success transition then static); Waiting/blocked (distinct icon and reason, animation stops). If a model claims work but no worker started, the UI cannot show a running agent.
- Frontend: React Flow (teams, nodes, hierarchy, parent-child subflows, full-screen graph); Motion for pulsing/transitions respecting reduced-motion; AG-UI or a FRIDAY-native compatible event adapter (subagent start/finish/error, tool-call and state events). Fits the existing control room; no separate UltraCode dashboard.
- One FRIDAY UI with two views: Simple Mode (everyday) and Agent Command Center (every team, subagent, model, skill, execution, development experiment).

### 4. Agent system
- Registered Agent (long-lived role: personality, skills, permissions, preferred model strategy) vs Agent Instance (temporary worker for one task) vs Team (reusable, extensible group of roles and workflow relationships). A registered role costs nothing while idle; an instance = role + objective context + model chosen at runtime (Codex/Sonnet/benchmarked free model) + skills + scoped workspace + budget/permissions.
- FRIDAY can create teams itself (e.g. 3D printer: hardware researcher, firmware developer, interface developer, tester): search the registry first; temporary roles expire; repeatedly useful ones become registered. Never create permanent agents per request.

### 5. Internal engineering workflows (FRIDAY-native, versioned definitions; not dependent on Claude Code's JS runtime)
| Idea -> Working Product (requirements-to-prd, feature-factory) | Missing Capability -> Installed Skill (OpenUltraCode + Capability Forge) | Autonomous Debugging (ultracode-debug, bug-hunt) | Independent Code Review (deep-code-review, risk-targeted) | Self-Improvement Experiment (ultracode-fusion, Evolution Arena: compare solutions, keep the measured winner) |
Example definition: workflow_id friday.build_capability v1; steps inspect(role capability_researcher) -> plan(integration_architect) -> build(developer) -> test(executor deterministic_test_runner) -> verify(independent_verifier) -> register(executor capability_registry). The production controller owns scheduling, memory admission and budget; models are assigned to steps at runtime.

### 6. Architecture to add before claiming self-development
- Agent Event Ledger: every spawned agent gets a durable run_id, parent_run_id, model-route identity, task, workspace and action-evidence record; the UI derives team activity from it; correct after restart.
- Skill/Capability Manifest Compiler: a structured declaration of what a capability does, needs, how to test it, which data it can access, how to remove or roll it back.
- Codebase-Aware Skill Memory: a generated coding skill stores verified project knowledge and version dependencies; a code change affecting it marks it for review/regeneration.
- Protected Evaluation Lab: an agent cannot pass verification by weakening tests, fabricating evidence or modifying the trusted evaluator; source changes built and run in isolated workspaces.
- Evolution Budget Governor: self-development has its own cost, RAM and time budgets; idle research cannot silently consume the owner's allowance or block foreground tasks.
- Capability Repair Loop: bounded repair of a broken integration; after repeated non-convergence change strategy or model; other tasks continue.
- Workflow Compiler: repeated successful agent trajectories become deterministic skills/workflows when that cuts cost without losing correctness.
- Release Boundary: new skills and replaceable adapters may be promoted under standing low-risk authority; rebuilding and activating a new core FRIDAY.exe needs stronger release evidence and owner authorization.

### 7. Build order (each with a pass condition)
1. Capability Forge + GitHub URL intake: dossier, relevance, scan, route. Pass only if it can REJECT an irrelevant or redundant repo.
2. Agent Composer + runtime registry: start a specialist on Claude, Codex, Hermes or OpenCode with compact context and authority/resource/spend limits. Pass only if the real worker lifecycle produces evidence and survives restart.
3. Auto-build one real capability from a small useful repo: native adapter + local skill + representative test, activate, complete the original request. Pass only after the external action and its verified result are observed.
4. Agent Command Center UI: registry, teams, live graph, hover/tap, event-based animation, progress, costs, logs, approvals. Pass only if real starts, failures, cancellations and completions are reflected truthfully.
5. Controlled self-evolution: discover improvements, generate alternatives, benchmark, promote verified low-risk changes with rollback. Pass only if it rejects a deliberately inferior or unsafe upgrade.

## Note 2 - Vercel Skills and MiniMind

### Vercel Labs Skills (skills CLI) - WRAP + EXTEND, high priority
- Open agent-skills manager: discover, install, update, remove and use skills across Claude Code, Codex, OpenCode, Gemini CLI and others; `npx skills find <q>`, `npx skills add <repo> --list`, `add <repo> --skill <name> -a claude-code -a opencode`, `update <name>`, `use <repo>@<skill>` (temporary use without install).
- FRIDAY: a skill-distribution backend inside the Capability Marketplace. Never run install commands when the user pastes a link. Pipeline: GitHub / skills.sh / skills.rest / local skills -> discover -> deduplicate -> quarantine -> SkillSpector -> sandbox -> evaluation -> FRIDAY Canonical Skill Registry (one maintained skill, versioned, permission-scoped) -> each executor (Claude Code, Codex, OpenCode/Hermes) gets a compatible scoped representation when it needs the skill. No divergent copies per agent.
- Vercel's audit fetch can fail open when its audit service is unavailable, so NVIDIA SkillSpector and FRIDAY's sandbox tests stay independent mandatory gates.
- First measurable success: give FRIDAY a new skill repo -> it evaluates and integrates the relevant skill -> the right subagent uses it -> the original task completes.

### MiniMind - EXPERIMENTAL_LOCAL_MODEL, research + benchmark
- Small LM with a full training pipeline; MiniMind-3 (2026): 64M dense and 198M MoE (~64M active); SFT, LoRA, distillation, RL, tool-calling examples; local inference with an OpenAI-compatible server - connects through the FRIDAY Model Gateway, no new assistant app. 64M FP16 weights ~128 MB (plus runtime/activations); fits 16 GB RAM / <=4 GB VRAM / <2 GB preference.
- Benchmark only on narrow tasks: task-category classification, which team to activate, structured instruction -> small schema, agent-state transitions. Not for complex development, architecture/deep research, approving sensitive actions, or replacing the main conversational model.
- Specialist micro-models: verified task history -> privacy-safe training dataset (approved) -> MiniMind or a lightweight classifier -> candidate -> frozen evaluation set -> compare with current routing -> promote only if better. Benchmark against a small linear classifier too; do not pick MiniMind just because it is a neural LM. The "two hours" training claim is one stage on an RTX 3090, not a laptop promise.

### UI: Intelligence Lab (extends the Agent Command Center) with two tabs
- Skill Marketplace: explore skills (search, compare with existing FRIDAY abilities, security and evaluation status), create a new skill from a description (research, build, test, register), import a GitHub capability (adopt / wrap / extract / reject).
- Local Model Lab.

### Decisions: Vercel Skills = wrap behind the verified Skill Registry (high, near-term); MiniMind = local model research/evaluation with optional specialist training (medium, experimental); NVIDIA SkillSpector = mandatory scanner for externally sourced skills (high); the FRIDAY Skill Registry stays the canonical source of truth.

## Note 3 - mytechnotalent/Reverse-Engineering (Apache-2.0): EXTRACT_KNOWLEDGE, medium value
- An education/example-code repo (x86, x64, ARM-32/64, AVR, RISC-V assembly; Windows API exercises; debugging lessons; embedded development; Ghidra resources), not an agent or MCP server. Linked Ghidra plugins and kernel-debugging projects must be evaluated individually.
- Capabilities: binary analysis (EXE/DLL structure, imports, architecture) - build specialist; Windows internals - adopt knowledge; firmware analysis (ESP32, Raspberry Pi) - adopt knowledge; assembly understanding (low-level crashes) - adopt knowledge; Ghidra integration (authorized binaries, proper tool adapter) - evaluate; malware analysis - controlled use, dedicated isolated lab only; network attack tools - excluded by default.
- New on-demand role "Binary Intelligence Engineer" (Development team): investigate compiled apps and binary compatibility; analyze Windows APIs, process behavior and crash artifacts; legitimate firmware debugging and reverse engineering; help the capability builder when source is unavailable; evidence-backed reports and repair suggestions; benchmark-selected model; isolated analysis environment; loaded only when needed. Flow: crash report + source -> ordinary debugging insufficient -> activate the specialist -> analyze the binary in isolation -> evidence-backed cause -> build/test a repair if source and authorization permit -> verify -> remember findings. Without source it explains what can be diagnosed or configured rather than claiming it can patch.
- Forge decision: classification educational_resource / assembly_examples / reverse_engineering_reference / embedded_systems_knowledge; decision EXTRACT_KNOWLEDGE; create binary_analysis_skill, windows_internals_skill, firmware_debugging_skill, reverse_engineering_specialist; selective, load on demand, security review required, isolated execution; never install all examples, execute unknown binaries on the host, load the whole course into context, or replace existing debuggers. Index selected tutorials with attribution and source references.
