# The owner's Agent Fabric v11 design note, 2026-10-02 (verbatim)

> Extracted byte-for-byte from session transcript 47979877 (2026-10-02T07:34Z, 1 pasted parts) by
> `extract_master.py`; the pasted-content tags are removed and nothing else is changed.
> The owner's own words between the pastes: ''
> Nothing below this note is the implementing agent's.

## Message 1 - Agent Fabric v11 - sparse specialist agents under a code controller

````text
Yes bro — **this is the problem I see now**.

The current harness is becoming safer, but it is still too much like:

```text
controller
   ↓
one giant worker
   ↓
worker researches
worker codes
worker tests
worker repairs itself
worker rereads its transcript
worker waits on huge test suite
```

That is not the optimized agentic build system we want.

The important distinction is:

> **One RAM slot does not mean one agent doing everything.**

On the current laptop, we may only be able to run **one heavy process concurrently**, but we can still use **multiple short-lived specialized agents sequentially**, each with fresh context. On the stronger machine, the same scheduler can run independent agents in parallel.

Anthropic's current guidance supports exactly this: use subagents when work is independent, needs isolated context, or forms a distinct workstream; don't delegate trivial single-file/sequential work because agent overhead can exceed the benefit. Their current models can also run independent tool calls in parallel inside one agent, so we should distinguish **parallel tools**, **sequential specialist agents**, and **parallel agents**. [Claude Platform Docs](https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/prompt-templates-and-variables?utm_source=chatgpt.com)

# I would change the harness to **Agent Fabric v11**

```text
                              PRD PROGRAM
                                   │
                          deterministic controller
                                   │
                              READY DAG
                                   │
              ┌────────────────────┼─────────────────────┐
              │                    │                     │
              ▼                    ▼                     ▼
         SCOUT AGENT         IMPLEMENTER AGENT       INVESTIGATOR
          cheap/tiny            normal coder          only if stuck
              │                    │                     │
              └──────────────┬─────┘                     │
                             ▼                           │
                     TARGETED TEST WORKER ◄──────────────┘
                             │
                       requirement passes?
                       /                \
                     yes                no
                     │                   │
                     ▼                   ▼
               VERIFIER AGENT      REPAIR AGENT
             only when needed      fresh context
                     │                   │
                     └─────────┬─────────┘
                               ▼
                          INTEGRATOR
                               │
                          commit/ledger
                               │
                               ▼
                            NEXT TASK
```

The important part is that these are **not all alive together**.

On this laptop:

```text
MAX_HEAVY_CONCURRENCY = 1
```

could result in:

```text
Scout exits
   ↓ RAM released

Implementer starts
   ↓ exits

Test worker starts
   ↓ exits

Verifier starts if required
   ↓ exits
```

That still gives us agent specialization and clean context without needing 12 GB free.

On your 16 GB / 4 GB VRAM machine, if profiling says it is safe:

```text
Implementer A ── CORE branch

Implementer B ── MCP branch

Scout C       ── next requirement
```

could run simultaneously.

Same architecture. Different concurrency.

---

## And DOMAIN-001 actually demonstrates where we need this

What happened was:

```text
DOMAIN implementer
      ↓
changes widely imported module
      ↓
integrator decides:
"almost everything might be affected"
      ↓
runs almost whole suite
in one 900-second process
      ↓
TIMEOUT
      ↓
uncaught timeout
      ↓
whole P0 program crashes
```

The integrator is doing too much.

The correct architecture is:

```text
DOMAIN implementer
       ↓
commit candidate
       ↓
Impact Analyzer
       ↓
affected tests = huge
       ↓
classify:
WIDE_IMPACT_CHANGE
       ↓
Test Scheduler
       │
       ├── chunk 1
       ├── chunk 2
       ├── chunk 3
       └── ...
       ↓
each chunk separately bounded
       ↓
aggregate result
```

If one chunk takes too long:

```text
TEST_TIMEOUT
```

not:

```text
PROGRAM_CRASH
```

And importantly, **the coding agent doesn't need to sit there consuming tokens while tests run**.

It exits.

A deterministic test worker executes the chunks.

That is a major optimization.

---

# There should be three kinds of parallelism

We've been treating “agents” as if they only mean concurrent processes. That's too narrow.

### Layer A — parallel tool calls

Within one worker:

```text
read file A
read file B
read file C
```

in parallel when independent.

Anthropic explicitly recommends simultaneous independent tool calls and warns that this can bottleneck the machine if used too aggressively, so ResourceGovernor still controls it. [Claude Platform Docs](https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/prompt-templates-and-variables?utm_source=chatgpt.com)

### Layer B — sequential fresh specialist agents

Perfect for your current constrained laptop:

```text
Scout
→ Implementer
→ Test worker
→ Verifier
```

Only one heavy slot.

But every stage gets clean context.

### Layer C — true parallel agents

When RAM permits:

```text
CORE-001
MCP-001
ACT-001
```

run independently in isolated worktrees.

ECC explicitly recommends parallel agents for genuinely independent operations, and its external orchestration guidance says separate worktree processes are appropriate when workers require independent Git state/build execution. [GitHub](https://github.com/affaan-m/ECC/blob/main/AGENTS.md?utm_source=chatgpt.com)

That is where we'll use parallel agents.

---

# We should borrow Superpowers' newest SDD design

Superpowers' newer implementation is useful because they've already experienced some of the same problems we're seeing.

Their current subagent-driven workflow is broadly:

```text
implementer
→ tests
→ review package
→ one task reviewer
→ fix if necessary
```

and their v6 redesign removed the previous duplicate reviewers. They report roughly **2× faster execution and nearly 50% fewer tokens in their own evaluations**, while appropriately noting that those exact numbers will vary by workload. [GitHub](https://github.com/obra/superpowers/releases?utm_source=chatgpt.com)

That means our normal task should NOT be:

```text
implementer
reviewer
security reviewer
quality reviewer
architecture reviewer
controller reviewer
```

Use:

```text
IMPLEMENTER

+ VERIFIER
only where justified
```

P0/security/authority work gets verification.

A simple test fix doesn't need a five-person committee.

---

# ECC should also be used differently

ECC has many useful specialized roles:

```text
planner
architect
TDD guide
reviewer
security reviewer
build-error resolver
E2E runner
loop operator
harness optimizer
RAG reviewer
```

and now explicitly includes roles for harness optimization and autonomous loops. [GitHub](https://github.com/affaan-m/ECC/blob/main/AGENTS.md?utm_source=chatgpt.com)

But copying its rule:

> always use planner + reviewer + TDD agent

would increase our token problem.

So FRIDAY's BuildController should use **ECC roles as a specialist registry**.

Example:

```text
normal test bug
→ Implementer

pytest infrastructure broken
→ BuildErrorResolver-style worker

hard harness cost problem
→ HarnessOptimizer-style worker

RAG retrieval regression
→ RAG reviewer

security boundary
→ Security specialist

architecture uncertainty
→ Architect
```

No global loading.

---

# Add an actual **Agent Spawn Score**

Right now a model may simply “feel like” spawning an agent.

Don't leave that entirely to the model.

Before spawning, estimate:

```text
ParallelismBenefit
+ ContextIsolationBenefit
+ SpecialistBenefit
+ VerificationBenefit
-
OrientationCost
- TokenCost
- RAMCost
- IntegrationCost
```

Then:

```text
positive enough
→ SPAWN

otherwise
→ DIRECT
```

Call it:

```text
AgentUtilityScore
```

Examples:

```text
"rename variable in one file"

AgentUtility < 0
→ direct


"investigate unrelated MCP failure while CORE worker runs"

AgentUtility > 0
→ agent


"independent review of authority boundary"

VerificationBenefit high
→ fresh verifier
```

That's much more intelligent than:

```text
USE AGENTS ALWAYS
```

or:

```text
NEVER USE AGENTS
```

---

# Add **Scout → Builder handoff**, but cheap

DOMAIN-001 already showed a `$0.20` scout can be useful.

Excellent.

Formalize:

```text
SCOUT
```

only when orientation uncertainty is high.

Scout output should be tiny:

```yaml
requirement: DOMAIN-001

likely_files:
  - x.py
  - y.py

symbols:
  - Foo.route
  - Bar.validate

tests:
  - test_domain.py
  - test_router.py

risk:
  wide_import_surface

notes:
  derived check currently at ...

tokens:
  ...
```

Then Scout dies.

Builder doesn't read Scout transcript.

Just the capsule.

This is exactly how we reduce fresh-worker orientation cost.

---

# Add **Agent Context Firewall**

Very important.

An agent should not inherit:

```text
controller's entire history
previous 9 failed repairs
all research
old test output
the whole PRD
```

It gets:

```text
role instructions
+
work packet
+
relevant source
+
relevant failure evidence
```

That's it.

Anthropic's current skill guidance explicitly treats context as a shared scarce resource and loads full skill bodies only when relevant rather than preloading them all. [Claude Platform Docs](https://docs.claude.com/es/docs/agents-and-tools/agent-skills/best-practices?utm_source=chatgpt.com)

Apply the same idea to agents.

---

# Repair agents must always be fresh after repeated failure

You've already measured:

```text
$1.34
$1.44
$1.77
$2.11
```

because the retry keeps rereading its growing history.

So I would tighten the policy beyond what we already planned:

```text
ATTEMPT 1
normal worker

ATTEMPT 2
same worker ONLY if:
failure is localized
AND context is still useful

otherwise fresh immediately

ATTEMPT >= 3
MANDATORY fresh worker

with:
Failure Capsule
not transcript
```

Failure Capsule:

```text
what failed
expected
observed
root causes already ruled out
reproduction
relevant files
current diff
tests
```

No conversation history.

---

# The fresh agent can even use a different model

This is another optimization.

```text
Attempt 1
normal coding model

Attempt 2
same tier / different approach

Attempt 3
fresh strong model

Attempt 4
frontier advisor + normal implementer
```

Don't make Opus edit every file.

For a hard problem:

```text
OPUS ADVISOR
      ↓
5-minute diagnosis / architecture packet
      ↓
normal coding worker implements it
```

That's cheaper.

---

# Use the Agent SDK's own accounting

If Claude Agent SDK is part of the harness, its Task result already exposes:

```text
usage
total_cost_usd
duration_ms
```

for subagent execution. [Claude Platform Docs](https://docs.claude.com/it/api/agent-sdk/python?utm_source=chatgpt.com)

That means the harness shouldn't try to reconstruct all worker spend from accumulated transcript logs when direct invocation-level accounting exists.

Use the provider/runtime's authoritative values where available:

```text
agent_invocation_id
model
input tokens
output tokens
cost
duration
```

Then the append-only stream stores that result.

This also helps fix the current double-counting bug.

---

# I would add a **Merge Queue**

When we eventually have 2–3 workers concurrently:

```text
Worker A done
Worker B done
Worker C done
```

don't let each merge directly.

Use:

```text
                 MERGE QUEUE

A ───────┐
B ───────┼──→ Integrator → affected tests → main candidate
C ───────┘
```

Each candidate includes:

```text
base SHA
commit SHA
requirements
affected surfaces
tests
```

If base became stale:

```text
REBASE_REQUIRED
```

not random cherry-pick.

Superpowers' newer worktree design similarly emphasizes isolated workspaces and explicit lifecycle/cleanup rather than agents writing over the same checkout. [GitHub](https://github.com/obra/superpowers/blob/main/docs/superpowers/plans/2026-04-06-worktree-rototill.md?utm_source=chatgpt.com)

---

# A big change to test execution

Tests are **jobs**, not coding-agent work.

Create:

```text
TestScheduler
```

It can run without LLM tokens.

For wide changes:

```text
affected tests = 3,800
```

schedule:

```text
chunk 1  0–499
chunk 2  500–999
...
```

On this laptop:

```text
parallel chunks = 1
```

On stronger host:

maybe:

```text
parallel chunks = 2
```

ResourceGovernor decides.

If `pytest-testmon` eventually proves reliable, use it as an additional impact signal. Its purpose is specifically to select tests affected by code changes. [GitHub](https://github.com/tarpas/pytest-testmon?utm_source=chatgpt.com)

Never make the LLM wait 900 seconds holding context while tests execute.

---

# Add **Asynchronous Agent Jobs**

This is probably the missing conceptual feature.

When BuildController dispatches:

```text
WORKER-123
```

it doesn't sit there watching.

It gets:

```text
JOB_STARTED
```

and releases itself.

Worker later sends:

```text
JOB_COMPLETE
```

or:

```text
JOB_FAILED
```

or:

```text
WAITING_RESOURCES
```

Then controller wakes.

This means the controller itself can be a tiny durable service rather than a giant Claude conversation.

That is much closer to FRIDAY's final architecture anyway.

---

# BuildController should eventually stop being an LLM

This is crucial.

Do not use Claude for:

```text
is CORE ready?
is MCP blocked?
which worker finished?
what is free RAM?
which worktree belongs to DOMAIN?
what is the spend?
```

That's code.

So the long-term build system:

```text
                    PYTHON BUILD CONTROLLER
                              │
                      Requirement DAG
                              │
                   Resource / Cost Gov.
                              │
       ┌──────────────────────┼──────────────────────┐
       ▼                      ▼                      ▼
   Agent Job              Test Job              Merge Job
       │
       ▼
    Model
```

The model is a worker.

Not the operating system.

---

# I would make this **BH-I: Agent Fabric**

After BH-G and BH-H, before resuming a big P0 campaign, create one bounded harness task:

```text
BH-I — SPARSE AGENT FABRIC
```

Not a giant framework.

It should implement:

```text
AgentJob
AgentRole
AgentBudget
AgentUtilityScore
FailureCapsule
ScoutCapsule
fresh-session repair
async job state
MergeQueue
TestJob separation
provider-native usage accounting
```

Most importantly:

```text
max_concurrent_agents
```

comes from ResourceGovernor.

On current machine:

```text
1
```

On stronger machine:

```text
maybe 2–3
```

No manual configuration required.

---

# Then the actual program looks like this

```text
                    DETERMINISTIC CONTROLLER
                             │
                     dependency DAG
                             │
                    ready requirements
                             │
                 AgentUtility calculation
                             │
                ┌────────────┴────────────┐
                │                         │
             DIRECT                   DELEGATE
                │                         │
                │                   agent queue
                │                         │
                │              ResourceGovernor
                │                         │
                │                  available slots
                │                         │
                │            ┌────────────┴──────────┐
                │            ▼                       ▼
                │         AGENT A                 AGENT B
                │          fresh                   fresh
                │            │                       │
                └────────────┼───────────────────────┘
                             ▼
                         TestScheduler
                             │
                          chunks
                             │
                             ▼
                         MergeQueue
                             │
                             ▼
                  Verifier when warranted
                             │
                             ▼
                        requirement
                         VERIFIED
```

THAT is what I mean by a properly optimized agentic build.

---

## I would slightly change the order you just reported

Let BH-G finish. Don't touch its tracked files while its integrator is operating.

Then do:

```text
BH-H
cost/run accounting
fresh retries
escalation
program lock

↓

BH-I
Sparse Agent Fabric

↓

BH-OPT-05a
memory lease/admission v2

↓

CORE-001
fresh escalated agent through new fabric

↓

P0 PROGRAM
```

I would move BH-I ahead of the orientation bake-off and testmon research because **there's no point optimizing worker orientation if the harness still treats every worker as a monolithic session**.

After P0 is running reliably:

```text
orientation arena
testmon
Ponytail A/B
```

can proceed according to the saved plan.

And then **freeze the harness**.

The current goal should be:

> agents become short-lived compute units controlled by deterministic software, not long-lived personalities talking to each other.

That gives you the “use agents properly” part **without doubling or tripling token burn**.
````

