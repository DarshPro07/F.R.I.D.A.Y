# Owner directive 2026-10-08 (b): FRIDAY Multi-Model Intelligence & Autonomous Development Harness

Pasted by the owner, extracted verbatim from the session transcript. Recorded as PRD v7 amendment A16
(docs/PRD_V7.md section 110), mapped onto the requirements that already cover most of it.

---

# FRIDAY Multi-Model Intelligence & Autonomous Development Harness

Architecture proposal — Level 3 Evolution

I've researched the current Claude Max, Codex, Gemini CLI, OpenCode, OpenRouter, and multi-model routing options. I think we can build something more efficient than simply making Gemini the boss of several Claude Code subagents.

The architecture I recommend is:

> FRIDAY is the permanent supervisor. Models are interchangeable intelligence resources. Claude Code, Codex, Hermes, Gemini CLI and OpenCode are specialized execution engines. FRIDAY decides which combination of model, agent, skill, tool and environment can complete each task correctly at the lowest practical cost.

And yes, Gemini 2.5 Flash can be your initial, inexpensive conversational and interpretation model. It should not automatically become the coding agent, security authority, or the model responsible for every tiny decision.

## 1. What your existing subscriptions actually give FRIDAY

This matters because you already have substantial model access without needing to purchase every model through a separate API.

| Your account         | Verified capability                                                                   | Recommended FRIDAY role                                               |
| -------------------- | ------------------------------------------------------------------------------------- | --------------------------------------------------------------------- |
| Claude Max           | Claude Code through subscription sign-in; access governed by session and usage limits | Primary complex coding and architecture                               |
| ChatGPT Plus / Codex | Codex CLI using ChatGPT sign-in and included plan usage                               | Independent coding, debugging, testing and fallback                   |
| Google AI Pro        | Gemini CLI through Google sign-in, with an enhanced request allowance                 | Research, planning, everyday tasks, documentation and selected coding |
| OpenCode             | Multiple providers and free model variants, plus configurable agents                  | Free-first coding executor and model experiments                      |
| Local models         | On-device inference, limited by actual hardware                                       | Private classification, routing, retrieval and simple decisions       |

Anthropic explicitly supports Claude Code sign-in through Pro/Max, and OpenAI supports Codex with ChatGPT plans. Google documents 1,500 maximum model requests per user per day for Google AI Pro in Gemini CLI, although limits and model availability can change.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://support.claude.com\&sz=32)

Claude Help Center

+2



There is an important extra benefit: Claude Max now includes separate monthly Claude API credits, with published amounts of $100 on Max 5x and $200 on Max 20x, once claimed through an eligible Console organization. Those credits apply to the Claude API and Agent SDK, not Claude Code subscription usage. Verify the eligibility and current balance in your account before treating them as an available budget.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://platform.claude.com\&sz=32)

Claude Platform Docs



That gives you two distinct Anthropic execution paths:

Claude Code subscription executor

Subscription quota

Coding sessions authenticated through Claude Max.

Claude API executor

API credit balance

Direct agent/model calls through eligible API credits or paid API billing.

They must have separate accounting. An API-key environment variable can also make Claude Code use pay-as-you-go authentication instead of your subscription, so FRIDAY must explicitly verify which route it is using.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://support.claude.com\&sz=32)

Claude Help Center



This is the first design rule: never confuse subscription usage, free API usage, prepaid credits and billable API calls.

## 2. The architecture I recommend

I would call this the FRIDAY Intelligence Fabric, with the Self-Development Harness operating above its execution workers.

FRIDAY — Supervisor

Gemini 2.5 Flash initially for normal conversation and interpretation

Objective OS · Memory · Policy · Evidence · User preferences

Deterministic Task Controller

Classify · Create PRD when needed · Break into work · Assign budgets · Schedule jobs

Benchmark-Aware Intelligence Router

Quality · Task fit · Speed · Cost · Privacy · Quota · Health · Hardware

Claude Code

Complex coding · Architecture

Codex

Feature coding · Debugging

Hermes / OpenCode

Free-first and alternative models

Gemini CLI / API

Research · Planning · Fast reasoning

Local Models

Private, small decisions

Future Providers

Custom APIs · OpenRouter · Others

Isolated Agent Execution

Git worktrees · Sandboxes · Skills · MCP · Browser · Tests · Evidence

Independent Verifier & Evolution Memory

Prove success · Record performance · Learn routing · Promote verified work

### The key improvement over a normal multi-agent system

A regular multi-agent system often works like this:

One model decides the plan, another executes, another reviews, another summarizes, and the process repeats—even when a deterministic program could have done half the work.

FRIDAY should instead use two control layers:

Deterministic Supervisor: Manages worker lifecycles, permissions, money, memory, retries, scheduling, dependency graphs, and result verification without calling an LLM unnecessarily.

Reasoning Supervisor: Gemini 2.5 Flash initially interprets ambiguous requests, handles conversation, recognizes when planning is needed and helps make task-level decisions. It can be replaced later by a better benchmarked model without redesigning the system.

That separation is what makes the architecture durable.

## 3. Which models should do which work?

I would not hardcode “Claude is best at coding, Codex second, Gemini third” forever.

The best model depends on the work, its runtime, the repository, current model quality, tool compatibility and remaining allowance.

Here is the initial routing policy to benchmark, not a claim that these models have already won FRIDAY's own tests.

| Task                               | First candidate                           | Escalation or fallback                        |
| ---------------------------------- | ----------------------------------------- | --------------------------------------------- |
| Everyday conversation              | Gemini 2.5 Flash                          | Other fast connected model                    |
| Simple classification/routing      | Deterministic code or small local model   | Gemini Flash                                  |
| Research and requirements analysis | Gemini CLI / fast free model              | Strong reasoning model for difficult findings |
| PRD creation                       | Gemini for structured draft               | Claude/Codex review for complex designs       |
| Small code change                  | Free coding model or Codex                | Sonnet-class coding agent                     |
| Normal feature development         | Codex / Sonnet / tested free coding model | Strong coding model if tests fail             |
| Difficult debugging                | Sonnet / Codex                            | Opus-class advisor                            |
| Architecture and concurrency       | Strong model selected by benchmark        | Independent strong-model review               |
| Code review                        | Deterministic checks first                | Fresh model only when risk warrants it        |
| Security-sensitive changes         | Independent tests and protected verifier  | Strong specialist model                       |
| Test execution and merging         | Deterministic code                        | Model only for diagnosis or conflicts         |
| Large research/learning mission    | Cheap research worker                     | Strong model for synthesis or contradictions  |

Claude Code's official subagent configuration already supports explicit model assignment such as `haiku`, `sonnet`, `opus`, `fable`, and exact model IDs. It also supports per-agent turn limits, tools, worktree isolation, effort levels and memory scopes.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://code.claude.com\&sz=32)

Claude Code Docs



That is useful for specialized Claude workers.

But there is a bigger opportunity.

## 4. FRIDAY's agents should not all be Claude subagents

This is probably the most important architectural decision in the entire design.

Instead of:

```
Claude Code
  ├── Claude subagent A
  ├── Claude subagent B
  └── Claude subagent C
```

we should have:

```
FRIDAY
  ├── Claude Code Agent
  │     └── optional Claude-native subagents
  │
  ├── Codex Agent
  │     └── Codex-native execution
  │
  ├── Hermes Agent
  │     └── specialized Hermes workers
  │
  ├── OpenCode Agent
  │     └── free or paid model
  │
  ├── Gemini CLI Agent
  │     └── Google models
  │
  └── Local Agent
        └── small on-device model
```

FRIDAY, not Claude Code, owns the whole team.

This allows an actual multi-model workflow.

For example:

Example: Building a new Shopify automation capability

1. Gemini Flash — Understand

   Translate your request into measurable functionality and determine whether a PRD is needed.
2. Free research agent — Investigate

   Discover existing FRIDAY tools, documentation, suitable code, dependencies and integration approaches.
3. Codex — Implement

   Build the core feature in an isolated worktree.
4. Sonnet — Parallel implementation

   Build an independent frontend or integration component, only if the dependency graph allows it.
5. Deterministic test runner — Evaluate

   Run tests without occupying an expensive model conversation.
6. Opus — Only when necessary

   Resolve a difficult architecture problem, investigate repeated failures or review a high-risk change.
7. FRIDAY — Integrate and report

   Confirm real behavior, preserve evidence, promote the capability under policy and complete the original task.

This is much better than having Opus coordinate other Opus agents all day.

### One technical constraint

FRIDAY should launch Claude Code, Codex and Gemini CLI using their supported authenticated interfaces. OpenCode's own documentation specifically warns that using Claude Pro/Max authentication through certain third-party plugins is prohibited by Anthropic. We should not use unauthorized token sharing to make one subscription impersonate another provider.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://docs.opencode.ai\&sz=32)

OpenCode



Claude Code can contain Claude-native subagents. Codex can execute its own jobs. OpenCode can select models per run. FRIDAY supervises these separate runtimes and coordinates their artifacts.

## 5. I found genuinely useful free-model options

One source worth monitoring is OpenCode Zen.

Its current official model catalog lists free variants including:

MiMo-V2.6-Flash Free

Coding/fast-agent benchmark candidate

Free-listed

Nemotron 3 Ultra Free

Reasoning/research benchmark candidate

Free-listed

Jev 1.13 Free

Specialized lightweight decision-layer candidate

Free-listed

Other changing free models

Automatically discover, probe and benchmark

Dynamic

Listed in OpenCode's current catalog; production fitness, regional access, limits and continued free availability are not guaranteed.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://docs.opencode.ai\&sz=32)

OpenCode

I would also add OpenRouter as an optional free-model source.

OpenRouter currently offers `openrouter/free`, which filters for supported capabilities and selects among available free models. Its free-plan pricing page lists a 50-requests-per-day limit. But its automatic free route can change the underlying model, which makes it unsuitable as a reproducible benchmark target unless the exact returned model is recorded.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://openrouter.ai\&sz=32)

OpenRouter

+1



There is an important distinction:

Free is a price class, not a quality or privacy guarantee.

For non-confidential tasks, free models should be FRIDAY's first benchmark candidates. For private customer data, code secrets and credentials, the router must enforce the user's data policy before any provider is considered. Google's Gemini API pricing documentation, for example, distinguishes free-tier data use from paid-tier data use.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://ai.google.dev\&sz=32)

ai.google.dev



### What I would not do

I would not make the free model with the highest advertised benchmark score FRIDAY's permanent default.

Instead, run tests using the exact combination:

```
Provider + Model + Version + Executor + Configuration
```

The same model served by two different providers can behave differently because of tool support, configuration, inference settings, latency and availability.

OpenCode's own Zen documentation explains that provider/model combinations need their own testing rather than assuming every endpoint serves a model equivalently.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://docs.opencode.ai\&sz=32)

OpenCode



## 6. The benchmark-driven router: the system's most valuable intelligence

I propose a component named ModelArena, connected to the existing Model Gateway rather than replacing it.

For each task family, it maintains measured scores.

Example ModelArena results

Illustrative data

QualitySpeedRelative cost

0%25%50%75%100%Free coderGemini FlashCodexSonnetOpus

The chart depicts hypothetical coding-task scores, not measured FRIDAY results. The production dashboard would use real benchmark data for the selected task family.

A valid benchmark should test much more than whether the model produced convincing text.

| Measurement         | What it establishes                                        |
| ------------------- | ---------------------------------------------------------- |
| Task success        | Did the requested real-world action work?                  |
| Acceptance tests    | Did the resulting code or workflow satisfy its contract?   |
| Regression rate     | Did it damage existing functionality?                      |
| First-pass success  | How often does it succeed without repair?                  |
| Total task cost     | What did implementation, retries and verification consume? |
| Wall time           | How long until the user receives a working result?         |
| Token usage         | How efficient was the model and its execution harness?     |
| Context stability   | Does performance deteriorate on long tasks?                |
| Tool reliability    | Does it select and use tools correctly?                    |
| Security compliance | Does it stay within its authority and privacy boundary?    |

Benchmarks should include task categories such as Python coding, TypeScript/Next.js, debugging, tests, research, multi-step browser work, UI editing and long-running objectives.

I would use public benchmarks for preliminary screening and FRIDAY-specific held-out tests for actual promotion.

### How should FRIDAY choose the model?

A candidate should first pass hard constraints:

\\[ \text{Eligible} = \text{Authorized} \land \text{Available} \land \text{Within Budget} \land \text{Privacy Compatible} \land \text{Capability Compatible} \\]

Only then rank eligible models by a score based on demonstrated task quality, completion time, cost and reliability.

The router should not pick a model with 60% success merely because it costs nothing when a better model can finish the task much more reliably.

For example, an inexpensive model that repeatedly produces broken code can cost more in retries than one strong model call.

Optimize cost per verified completed task, not cost per token.

## 7. The provider setup experience you want

I agree completely: the user should never have to edit Python files, environment variables or CLI configurations just to connect a new AI provider.

It should work inside FRIDAY's own settings.

## FRIDAY — AI Providers

Proposed settings UI

Claude Code

Subscription sign-in

Codex

ChatGPT sign-in

Gemini

Google sign-in or API

OpenCode

Free-model provider

Example: Add custom model provider

Provider name

API protocol

OpenAI-compatible

Anthropic Messages-compatible

Gemini-compatible

Custom adapter required

Provider endpoint

Design this provider connection This is a configuration mock-up, not a live provider connection. Real credentials should be entered only into FRIDAY's local secure credential UI, never into this chat.

### What happens when the user connects a provider?

FRIDAY should automatically identify the authentication method, validate the endpoint, securely store credentials, discover available models, check capabilities, record pricing/quotas, run a minimal health test and register working routes.

For example:

```
provider_id: custom_openrouter
display_name: OpenRouter

authentication:
  method: api_key
  secret_reference: vault://provider/openrouter

endpoint:
  protocol: openai_compatible
  base_url: https://openrouter.ai/api/v1

model_discovery:
  enabled: true

routing:
  benchmark_required: true
  auto_update_catalog: true
  paid_fallback: false

privacy:
  permitted_data_classes:
    - public
    - approved_non_sensitive

status: PENDING_VALIDATION
```

FRIDAY's database should contain the provider metadata and a reference to the credential, not the plaintext key. Use the operating system's credential protection, with Windows DPAPI/Credential Manager initially and macOS Keychain later.

API keys must never appear in model prompts, agent transcripts, shell logs or benchmark artifacts.

### Should we use LiteLLM?

LiteLLM is worth considering for the direct API layer. It supports numerous providers, budgets, rate limits, fallback rules, model routing and API abstraction.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://github.com\&sz=32)

GitHub

+2



But I would not replace FRIDAY's existing Model Gateway with it.

Use this division:

| Interface                  | Responsible component                        |
| -------------------------- | -------------------------------------------- |
| Claude Max subscription    | Claude Code adapter                          |
| ChatGPT Plus subscription  | Codex adapter                                |
| Google AI Pro subscription | Gemini CLI adapter                           |
| Standard provider API keys | FRIDAY API Gateway, optionally using LiteLLM |
| Local models               | Existing local inference adapter             |
| Model selection            | FRIDAY benchmark-aware router                |
| Spending authority         | FRIDAY Budget Governor                       |
| Secret protection          | FRIDAY Secret Broker                         |

That keeps subscription-based CLI execution separate from ordinary API inference.

## 8. The autonomous development experience

This should be the main user experience:

> “Friday, build an application that controls my 3D printer, creates printable parts, and lets me control it through voice.”

FRIDAY should be capable of carrying that through the entire engineering lifecycle.

### Phase A — Understand and specify

Gemini Flash (or the current preferred interpretation model) determines the actual user goal, examines existing capabilities and creates an appropriate plan.

It should not produce a 100-page PRD for a calculator.

Adaptive specification depth

| Small task                    | Compact acceptance contract                                                 |
| ----------------------------- | --------------------------------------------------------------------------- |
| Normal feature                | Short implementation plan, contracts, tests                                 |
| Complex application           | Full PRD, architecture, dependency DAG, risk assessment                     |
| Research/physical engineering | Evidence-backed technical specification, validation and staged safety gates |

FRIDAY asks you only for essential information it cannot reasonably infer or test.

### Phase B — Choose the team

It identifies which work is actually independent.

```
Project Goal
    │
    ▼
FRIDAY Specification Engine
    │
    ▼
Dependency DAG
    │
    ├── Firmware development ─── Codex
    │
    ├── Voice interface ──────── Sonnet
    │
    ├── Control UI ────────────── Free coder
    │
    └── Hardware research ─────── Gemini / research model
```

These assignments are illustrative. ModelArena makes the actual selections.

### Phase C — Execute efficiently

Each worker receives only its relevant files, constraints and acceptance tests.

Its execution environment is isolated. The worker produces changes and evidence, not a huge transcript for FRIDAY to reread.

The harness performs deterministic testing and integration wherever possible.

If one worker fails, FRIDAY can reassign that work to a different model without losing the progress made by the other workers.

### Phase D — Build, validate and deliver

FRIDAY tests the actual product, checks that the requested behavior exists, produces appropriate deliverables and gives you a concise result.

A new capability is not considered finished just because the code was written.

The completion contract is:

Requested behavior verified + required outputs available + evidence recorded + no unresolved release-blocking defect.

## 9. How the agents should be limited

I would assign hard controls to every subagent.

Example subagent execution profiles

EconomyBalancedDeep

Token budget

10,000–25,000

Maximum turns

10–20

Concurrent workers

1–2 if RAM permits

Model preference

Codex / Sonnet / best measured

Proposed starting profiles, not measured limits. Real budgets depend on task, provider, hardware and user spending policy.

Each agent needs limits on tokens, tool calls, time, filesystem access, network access, estimated maximum spend and retry attempts.

FRIDAY's Resource Governor determines whether concurrency is genuinely possible. On your 16 GB machines, starting five heavy agents simultaneously could be substantially worse than two well-chosen agents.

And every paid request—including advisors, verifiers and rescues—must pass the same atomic budget reservation mechanism. This is especially important given the earlier FRIDAY build-cost overcounting and daily-cap issues.

## 10. The part I would add beyond what you asked: Dynamic Agent Composition

I don't want FRIDAY permanently maintaining 50 model-specific coding employees.

Instead, maintain reusable functional roles and assemble them only when necessary.

For instance, the role Python Backend Developer can be instantiated using Codex today, Sonnet tomorrow, and a free coding model next week.

```
Worker Role
    +
Selected Model/Executor
    +
Task-Specific Skills
    +
Allowed Tools
    +
Workspace and Policy
    =
Temporary Specialist Agent
```

The identity and knowledge of the specialist live in FRIDAY's canonical memory and capability registry, not permanently inside a particular provider conversation.

This means new models can improve FRIDAY automatically without forcing you to redesign all its agents.

It also prevents a common architectural problem: treating model selection, employee identity, task memory and tool permissions as one inseparable thing.

## 11. Continuous model discovery and evolution

FRIDAY should maintain a background Model Scout, but it must be bounded and event-driven.

When a new model becomes available, Scout collects the actual model ID, provider, license/usage conditions, capabilities, price, context limit and version information.

Then it runs compatibility probes and a small representative benchmark. Only promising models progress to the full ModelArena.

The result could be:

```
New model discovered
    ↓
Provider connection verified
    ↓
Compatibility probe
    ↓
Short benchmark
    ↓
Promising?
    ├── No → Archive findings
    └── Yes
         ↓
      Held-out evaluation
         ↓
      Champion/Challenger comparison
         ↓
      Promote for selected task classes only
```

For example, a free model might win at writing unit tests but lose at architecture. FRIDAY promotes it only for unit-test creation.

If a model disappears, changes pricing, becomes unreliable, or loses functionality, FRIDAY marks the route degraded and selects an approved alternative.

It must never silently switch confidential data to a free public endpoint simply because the previous private route stopped working.

And use measured regression evidence, not popularity rankings, to drive the promotion. OpenRouter itself describes its free-model rankings as usage-based rather than quality benchmarks.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://openrouter.ai\&sz=32)

OpenRouter



## 12. What I would build next in the actual FRIDAY codebase

I would keep this compatible with the current PRD rather than invent an entirely new infrastructure stack.

| Priority | Component                             | Acceptance gate                                                                                |
| -------- | ------------------------------------- | ---------------------------------------------------------------------------------------------- |
| P0       | Unified Provider Registry             | Connect CLI, API and local routes; discover/health-check correctly                             |
| P0       | Agent Runtime Adapters                | Claude Code, Codex, Gemini CLI, OpenCode/Hermes run with bounded tasks and structured receipts |
| P0       | Shared Budget + Resource Admission    | No paid call or worker launch bypasses reservation                                             |
| P0       | Agent WorkPacket + Evidence Contract  | Fresh contexts; verifiable results; durable retries                                            |
| P0       | Benchmark Data Model                  | Results tracked by provider/model/version/runtime/task class                                   |
| P1       | ModelArena                            | Reproducible evaluations and champion/challenger promotions                                    |
| P1       | Adaptive Model Router                 | Best eligible route selected from evidence, not static rankings                                |
| P1       | Provider UI + Secure Vault            | Add custom endpoint/key without editing source                                                 |
| P1       | Dynamic Agent Composer                | Create task-specific subagents with correct skills/tools/limits                                |
| P1       | Evolution Scout                       | Discover, evaluate and propose model upgrades                                                  |
| Later    | Specialized model tuning/distillation | Train small local models only when enough verified data supports it                            |

These are proposed implementation priorities; they should be reconciled with the existing FRIDAY requirement DAG and the status of components already built. I would not recreate anything already verified.

## 13. My final architecture recommendation

I would lock these defaults:

FRIDAY remains the only supervisor.

Gemini 2.5 Flash starts as the everyday conversational/interpretation model.

Codex and Sonnet compete for normal coding.

Opus is reserved for difficult tasks or high-value review.

Free models are tested first where privacy and reliability permit.

Local models handle small private decisions within the hardware budget.

Benchmarks continuously determine routing improvements.

Skills, agents and providers are loaded only when needed.

I would also make one subtle but important change to the design: use a small deterministic router before Gemini, so FRIDAY does not need to pay even a free-model latency cost for every trivial decision. Gemini remains the conversational intelligence, not a bottleneck in every action.

### One decision I want from you

For the self-development harness, should the default be Free First (try the best benchmarked free model, escalate when it cannot meet the task's requirements) or Quality First Within Subscription (prefer your authenticated Codex/Claude coding subscriptions when they have demonstrated better quality, and save free models for suitable work)?

I lean toward Quality First Within Subscription for coding, Free First for routine work, both subject to your fixed spending caps. That should minimize total cost and wasted time rather than simply minimizing the cost of the first model call.
