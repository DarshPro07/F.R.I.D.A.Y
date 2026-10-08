# Owner directive 2026-10-08: FRIDAY Evolution Engine - Level 3 controlled self-evolution

Pasted by the owner, extracted verbatim from the session transcript. Recorded as PRD v7 amendment A15
(docs/PRD_V7.md section 109), mapped onto the requirements that already cover most of it.

---

# FRIDAY Evolution Engine — Level 3: Controlled Self-Evolution

Architecture direction selected

Bro, this is where FRIDAY moves beyond being an autonomous assistant and becomes a self-developing AI system.

The goal is simple:

> FRIDAY should be capable of identifying what it cannot do, teaching itself what it needs to know, creating the missing capability, testing it, integrating it, and improving its own performance over time—without requiring you to act as its developer every day.

And when an existing capability becomes outdated, FRIDAY should be able to diagnose, repair and upgrade it.

We are not building another AutoGPT-style framework. We're building this directly into the existing FRIDAY architecture, using Objective OS, CortexOS, Model Gateway, Hermes, Claude Code, Capability Forge, Skill Registry, Policy Engine and the execution infrastructure already developed.

## 1. The architecture I would choose

FRIDAY Core Intelligence

User goals · Memory · Objectives · Policies · Model selection

Evolution Controller

Detects gaps · Plans improvements · Schedules experiments · Tracks results

Research & Learn

Papers, GitHub, tools, skills

Agentic Development

Claude, Hermes, Codex, others

Evolution Arena

Tests, A/B evaluations, benchmarks

Evolution Memory

Successful and failed variants

Independent Promotion Authority

Security gates · Approval leases · Signed versions · Rollback

Upgraded FRIDAY

New skills · Tools · Agents · Workflows · Models · Verified code improvements

The main concept is inspired partly by Darwin Gödel Machine, which explores generating variations of an agent's own code, evaluating them empirically, and preserving successful candidates in an archive. The research reported substantial coding-benchmark improvements, but under experimental conditions with sandboxing and human oversight.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://arxiv.org\&sz=32)

arxiv.org



I want that evolutionary principle inside FRIDAY, but with measurable improvements, limited budgets, and enforced boundaries.

## 2. Two upgrade pipelines, not one

This is my first strong architectural decision.

Pipeline A — Capability Evolution

The frequent, lightweight path.

FRIDAY creates or upgrades local skills, MCP adapters, plugins, workflows, specialized workers, tool integrations, research knowledge and other replaceable capabilities.

Low-risk upgrades may activate automatically under a valid standing permission.

Pipeline B — Core Self-Development

The stricter path.

FRIDAY proposes changes to its own planning, context compiler, routing, model gateway, execution architecture, memory algorithms or other core implementation code.

It builds and benchmarks these in isolation. Promotion into the live executable requires independent validation and the appropriate owner approval.

The two paths must never have identical permissions.

A local skill update should not need to rebuild `FRIDAY.exe`. Conversely, changing the core executable must not be treated like editing an ordinary skill file.

Selected default: Level 3 allows automatic construction, testing and promotion of approved low-risk improvements. It does not grant autonomous authority to replace FRIDAY's security roots or silently install a new core executable.

## 3. How the autonomous evolution loop works

The central loop should be event-driven, not an expensive model constantly thinking in the background.

Observe a problem or opportunity

Capability missing · Task failed · Repeated work · Expensive execution

Create Evolution Proposal

Expected benefit, risk, cost, affected components and acceptance tests

Research and construct candidates

Reuse existing code first · Build only the missing parts

Sandboxed experiment

Functional tests · Negative controls · Benchmark comparison · Security

Independent decision gate

Promote · Reject · Repair · Request approval

Deploy, monitor and retain learning

Rollback on regression · Preserve last-known-good version

Every iteration must have a named objective, measurable acceptance criteria, an allocated cost budget, and a termination condition.

This is important because unlimited recursive improvement is not automatically useful. The original Darwin Gödel Machine research validated candidate modifications with coding benchmarks and preserved multiple candidates instead of continually overwriting one agent.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://arxiv.org\&sz=32)

arxiv.org



## 4. Which FRIDAY components can modify themselves?

I would classify the codebase into four authority zones.

| Zone                          | Examples                                                                            | Level 3 behavior                                          |
| ----------------------------- | ----------------------------------------------------------------------------------- | --------------------------------------------------------- |
| Z1 — Local extensions         | Skills, tool adapters, prompt templates, low-risk workflows                         | Build, test and auto-promote within policy                |
| Z2 — Operational intelligence | Retrieval weights, tool routing, model preference, context strategies               | Experiment and auto-promote after benchmark gates         |
| Z3 — Core product code        | Objective OS, model gateway, planner, memory implementation, FRIDAY.exe             | Build and verify automatically; owner-approved deployment |
| Z4 — Protected trust roots    | Policy enforcer, secrets broker, trusted verifier, signing keys, approval authority | No autonomous promotion or self-granted permissions       |

The key is that the experimental builder must never control the final judge of its own experiment.

Recent research on reward hacking in coding agents found that agents can exploit test environments, including editing tests or hardcoding expected outcomes. This makes our existing negative-control and protected-verifier requirements especially important.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://arxiv.org\&sz=32)

arxiv.org



For software-update integrity, I would borrow from The Update Framework, which addresses signed metadata, compromised keys, rollback attacks and malicious updates.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://github.com\&sz=32)

GitHub



## 5. Specialized agents inside Evolution Engine

We should have logical roles, not permanently running model sessions.

| Agent role          | Responsibility                                | Preferred execution                                    |
| ------------------- | --------------------------------------------- | ------------------------------------------------------ |
| Evolution Scout     | Detect improvement opportunities              | Deterministic telemetry + tiny model                   |
| Research Specialist | Investigate papers, source code, skills, APIs | Cheap/normal model, stronger when needed               |
| Architect           | Resolve complicated design questions          | Strong model on demand                                 |
| Builder             | Implement modifications                       | Sonnet/Codex/Hermes or benchmark-selected coding model |
| Experiment Worker   | Run variants in isolation                     | Deterministic tests, sandbox                           |
| Verifier            | Independently validate candidate              | Protected harness + selective strong model             |
| Release Manager     | Promote signed, approved changes              | Deterministic code; not model discretion               |

On your 16 GB laptops, they should not all be in memory simultaneously. The same worker system supports sequential fresh agents when RAM is tight and independent parallel experiments when resources permit.

Anthropic's engineering work with parallel coding agents demonstrates both the potential and the considerable cost of multi-agent work; this is why I would optimize for verified improvement per rupee and minute, not maximum agent count.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://www.anthropic.com\&sz=32)

anthropic.com



## 6. Evolution Memory — the part I would add

I don't want FRIDAY forgetting that it already tried something unsuccessfully.

Each experiment should generate a durable record:

```
evolution_experiment:
  id: EVO-00042
  objective: "Reduce tool-routing latency"

  parent_version: "router-v4"
  candidate_version: "router-v5"

  hypothesis: "New retrieval strategy improves routing"

  scope:
    - tool_router

  baseline:
    task_success: 0.82
    latency_ms: 90

  candidate:
    task_success: 0.87
    latency_ms: 61

  evaluation:
    frozen_benchmark: true
    negative_controls: passed
    held_out_tests: passed
    security_review: passed

  decision: PROMOTE
  rollback_version: "router-v4"
```

Illustrative schema and measurements—not results measured on FRIDAY.

This record should connect to canonical memory, but there is a distinction:

Canonical memory remembers facts about the user, objectives and workspaces.

Evolution Archive remembers engineering experiments, performance, failed approaches, versions and evidence.

They should share a query interface without contaminating user memory with speculative engineering findings.

I would also keep failed candidates in the archive, so FRIDAY doesn't spend money rediscovering the same unsuccessful optimization.

## 7. The protected sandbox and upgrade system

For isolated development, the strongest candidate we've researched is NVIDIA OpenShell.

OpenShell provides independently enforced filesystem, process, network and provider-access policies. It also mediates credentials through permitted endpoints rather than exposing them directly to the agent.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://docs.nvidia.com\&sz=32)

NVIDIA OpenShell

+1



However, its Windows WSL2/Docker platform is still marked experimental, so I would keep a provider-neutral sandbox interface and qualify it on both machines before treating it as the only execution backend.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://docs.nvidia.com\&sz=32)

NVIDIA OpenShell



Proposed internal contract:

```
class EvolutionSandbox:    def create(self, policy, workspace): ...    def run(self, command, timeout): ...    def collect_evidence(self): ...    def verify_boundaries(self): ...    def destroy(self): ...class EvolutionCandidate:    def build(self): ...    def test(self): ...    def benchmark(self): ...    def request_promotion(self): ...
```

The sandbox is not permitted to load the real user's confidential memory, production credentials or unrelated files simply because the agent wants them.

Imported GitHub repositories and skills must also pass the existing Capability Forge quarantine. NVIDIA SkillSpector supports static scanning for prompt injection, supply-chain issues, exfiltration patterns, tool poisoning and other risks. It belongs before dynamic evaluation, not instead of it.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://github.com\&sz=32)

GitHub



## 8. Automatic upgrade policies

I would use the following defaults.

| Change                                        | Automatic build?   | Automatic activation?                     |
| --------------------------------------------- | ------------------ | ----------------------------------------- |
| New local knowledge skill, no new permissions | Yes                | Yes, after verification                   |
| New external GitHub capability                | Yes, in quarantine | Only within approved capability authority |
| Small change to routing or prompt compilation | Yes                | Yes, after held-out evaluation            |
| New worker persona/function                   | Yes                | Yes, within workspace permissions         |
| Change to a customer-facing automation        | Yes                | Depends on its external side effects      |
| New FRIDAY.exe core release                   | Yes                | No; explicit promotion approval           |
| Change to security/secret/verifier root       | Proposal only      | No autonomous approval                    |

This preserves Level 3 autonomy while still making user authority meaningful.

For instance, FRIDAY could autonomously improve a local document-processing skill, but it couldn't reinterpret that permission as authority to install a new system-level driver or grant itself access to financial records.

## 9. Avoiding endless evolution and token waste

This is especially important given the cost and memory issues you've already encountered.

I'd have five independent budget governors:

Money

Daily, objective and experiment budgets

Memory

Predicted peak + reservation + safety headroom

Context

Fresh agents, code capsules, bounded tool output

Progress

No-progress detection and bounded repairs

Experiment capacity

Limited candidate variants and evaluation runs

I would make the default scheduling priority:

User's active task → unfinished user objective → reliability repair → approved capability improvement → speculative exploration.

So if FRIDAY is in the middle of editing a video, it must not consume all RAM and model budget evolving its own router.

And "learning while idle" should not mean an endless background token-consuming conversation. FRIDAY creates bounded learning missions, completes them, persists results and sleeps until there is another useful trigger.

## 10. FRIDAY's Evolution Control Room

Users should be able to see what FRIDAY is improving without managing agents manually.

## FRIDAY Evolution

Concept UI · Level 3

Controlled

Capabilities

# 24

Illustrative

Experiments

# 7

Illustrative

Promoted

# 3

Illustrative

Example activity

Document skill upgraded

Passed verification · Automatically promoted

Testing new routing strategy

Sandbox experiment · No live changes

Core update awaiting approval

Build ready · Live executable unchanged

Evolution autonomy

Level 3

Example layout only; it is not connected to a running FRIDAY instance.

Normal users see progress, capabilities, approvals and results. Developer Mode exposes experiment diffs, provider models, agent traces, security reports, budget logs and rollback history.

## 11. Concrete implementation structure

I would add an `evolution` package integrated with existing FRIDAY services. This is a proposed structure, not a claim that these modules already exist.

```
friday/
  evolution/
    controller.py
    opportunity_detector.py
    proposal.py
    experiment_registry.py
    candidate_archive.py

    research/
      source_registry.py
      evidence_collector.py

    builder/
      model_selector.py
      worker_dispatch.py
      code_capsule.py

    sandbox/
      interface.py
      openshell_adapter.py
      windows_adapter.py

    evaluation/
      benchmark_runner.py
      negative_controls.py
      cost_evaluator.py
      regression_gate.py

    promotion/
      candidate_validator.py
      capability_promoter.py
      core_release_promoter.py
      rollback.py

    memory/
      evolution_archive.py

tests/
  evolution/
```

No duplicated memory engine, no second policy engine, no second model router.

Its dependencies should point into the existing trusted systems.

| Existing FRIDAY system | Evolution Engine uses it for                       |
| ---------------------- | -------------------------------------------------- |
| Objective OS           | Durable work and restart/resume                    |
| Capability Forge       | Repository integration and capability construction |
| Skill Registry         | Local skill lifecycle                              |
| Model Gateway          | Task-dependent model choice                        |
| CortexOS               | Bounded relevant context                           |
| Canonical Memory       | User/workspace context                             |
| Policy Engine          | Authorization and immutable boundaries             |
| EXEC-SBX-001           | Scoped command execution                           |
| Reality Ledger         | Evidence of actual operations                      |
| Resource Governor      | RAM, CPU, parallelism                              |
| Release Gate           | Independent verification                           |

## 12. Implementation roadmap

I would begin with a thin, complete vertical slice rather than building all of these modules simultaneously.

1

Evolution Controller + Proposal Registry

Detect one real capability gap, produce a structured proposal and persist it. No LLM loops while idle.

Exit gate: A missing capability creates exactly one deduplicated proposal, survives restart and respects its budget.

2

Capability Builder + Sandboxed Evaluation

Create a small missing capability, test its actual effect and register it.

Exit gate: A new skill or adapter is created, evaluated, activated and used successfully to complete the original objective.

3

Evolution Arena + Variant Archive

Compare candidate routing, prompt or workflow improvements against frozen baselines. Reject regressions.

Exit gate: The system distinguishes a real improvement from a deliberately planted benchmark-cheating or regressing candidate.

4

Core Self-Development + Release Pipeline

Modify non-protected core code in a separate worktree, verify it, build a candidate executable and request promotion.

Exit gate: A verified new FRIDAY.exe can be promoted with approval, restarted, health-checked and rolled back if necessary.

5

Continuous Controlled Evolution

Automatically detect performance regressions, recurring tasks, obsolete skills and worthwhile optimizations, using historical experiments to choose what to improve next.

Exit gate: Over repeated real tasks, FRIDAY demonstrates maintained or improved success, bounded cost and safe rollback—not merely more generated code.

This builds on existing lessons from long-running coding agents: persistent progress artifacts, fresh contexts and incremental verified changes help overcome failures of monolithic long sessions.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://www.anthropic.com\&sz=32)

anthropic.com



## 13. The additional improvement I strongly recommend

I would add an Evolution Value Gate before even starting an expensive experiment.

FRIDAY asks, internally:

"Will this proposed change improve the user's ability to complete work, reduce manual effort, improve reliability, or reduce cost?"

If it cannot identify a measurable reason, it should not evolve that subsystem just because a new repository or model was released.

For example, a new GitHub agent framework that duplicates our Objective OS is not inherently valuable. A small technique that cuts context retrieval costs while maintaining quality could be far more valuable.

That keeps Capability Forge, Research OS and Evolution Engine aligned with your main goal: FRIDAY should become more helpful, more capable and more efficient—not simply larger.

## The one remaining product decision

I would now define two separate default permissions: Level 3 for extension evolution and experimental optimization, and owner-approved promotion for FRIDAY's core executable.

Do you want the Evolution Engine to start running small, low-risk improvement experiments automatically whenever FRIDAY is idle, or only after it detects a concrete capability gap, repeated failure or measurable performance issue?

My preference is trigger-based by default, with an optional user-enabled overnight research/experiment mode. That keeps continuous improvement possible without turning an idle laptop into an endless consumer of tokens and RAM.
