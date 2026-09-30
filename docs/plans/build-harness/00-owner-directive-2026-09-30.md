# The owner's build-harness directive, 2026-09-30 (verbatim)

> Extracted byte-for-byte from the session transcript (two pasted messages, then the owner's own words).
> Nothing below this note is the implementing agent's.

## Message 1

````text
Yes bro — I misunderstood the bottleneck. You mean **the actual FRIDAY build is too slow and too token-hungry**, and the harness is not exploiting parallel agents well enough.

I agree. We should change the **builder architecture**, not shrink the PRD.

I researched ECC, Superpowers, Awesome Claude Code, RTK, Planning-with-Files, and Anthropic’s own long-running-agent work. The useful pattern is clear: **use agents aggressively only where work is independent, keep the expensive controller out of the mechanical loop, keep context on disk, compress shell output, and use fresh short-lived workers.** Anthropic’s long-running harness work reached the same broad conclusion: incremental agents with persistent artifacts work better than asking one context to build everything. :chatgpt-content-reference{index="0"}

# What is wrong with our current build

Right now it sounds too much like:

```text
OPUS / MAIN AGENT
     │
     ├── read PRD
     ├── inspect code
     ├── design
     ├── edit
     ├── run tests
     ├── inspect 5,000 lines of test output
     ├── debug
     ├── review itself
     ├── run full suite
     └── next requirement
```

That is expensive and mostly serial.

We should instead build:

# **FRIDAY Build Harness v2**

```text
                         BUILD SUPERVISOR
                      cheap/mid-tier model
                               │
                      Requirement DAG
                               │
             ┌─────────────────┼─────────────────┐
             ▼                 ▼                 ▼
          WORKER A          WORKER B          WORKER C
        requirement X     requirement Y     requirement Z
        own worktree       own worktree       own worktree
             │                 │                 │
             ▼                 ▼                 ▼
       targeted tests     targeted tests     targeted tests
             │                 │                 │
             └─────────────────┼─────────────────┘
                               ▼
                        INTEGRATION AGENT
                               │
                        component tests
                               │
                               ▼
                       FRESH VERIFIER
                               │
                               ▼
                          checkpoint
```

Not 20 agents.

Usually **2–4 useful agents**.

---

# 1. The main controller should NOT be Opus

This is probably one of the biggest savings.

The main controller mostly does:

```text
read ledger
choose next requirements
spawn workers
watch status
merge evidence
manage dependencies
```

That is not the hardest intelligence in the project.

Use:

```text
CONTROLLER
→ mid-tier / cheap model

IMPLEMENTER
→ normal coding model

INVESTIGATOR
→ normal model

VERIFIER
→ fresh normal/strong model

OPUS
→ only difficult architecture / stuck P0 / critical review
```

Superpowers explicitly documents a cheaper orchestration mode where the expensive top-level session delegates the complete subagent-driven plan to a cheaper orchestrator layer rather than paying frontier-model rates for every coordination message. :chatgpt-content-reference{index="1"}

That idea fits us extremely well.

---

# 2. Parallelize by dependency graph, not randomly

Suppose remaining work is:

```text
RES-003
MODEL-009
MCP-001
CORE-001
CTX-003
ACT-001
```

If:

```text
MODEL-009 depends on RES-003
```

don't parallelize those.

But perhaps:

```text
RES-003     independent
MCP-001     independent
CTX-003     independent
```

Then:

```text
Agent A → RES-003
Agent B → MCP-001
Agent C → CTX-003
```

This is where agents genuinely save wall-clock time.

The scheduler should calculate:

```text
READY = requirements whose dependencies are VERIFIED
```

then assign independent items.

---

# 3. Every worker gets its own Git worktree

This is important.

```text
.worktrees/
├── res-003/
├── mcp-001/
└── ctx-003/
```

Workers do not edit the same checkout.

ECC explicitly includes Git worktree parallelization/cascade workflows as part of its performance approach. :chatgpt-content-reference{index="2"}

Worker finishes:

```text
code
tests
commit
evidence
```

Then integration agent merges.

If merge conflict:

```text
Integration Agent
```

handles it with both requirement contracts.

This is much cleaner than three agents fighting in one directory.

---

# 4. Dynamic concurrency

Don't hardcode:

```text
agents = 5
```

Use the Adaptive Hardware Governor we already designed.

On your weaker reference machine:

```text
15.4 GB RAM
integrated GPU
```

perhaps:

```text
2 normal coding workers

OR

1 heavy test/browser worker
+
1 lightweight worker
```

On your 16GB/4GB VRAM laptop:

maybe:

```text
2–3 workers
```

But FRIDAY determines this from measured headroom.

Conceptually:

```text
if GREEN:
    concurrency += 1

if YELLOW:
    keep current

if ORANGE:
    stop new workers

if RED:
    pause lowest priority worker
```

So parallelism helps rather than crashing the laptop.

---

# 5. Add **agent cost accounting**

Every worker should report:

```yaml
worker:
requirement:
model:
input_tokens:
output_tokens:
tool_calls:
wall_time:
tests_run:
requirements_closed:
```

Then calculate:

# `AgentROI`

```text
verified requirements closed
-----------------------------
tokens + wall time + retries
```

If a certain agent strategy burns:

```text
100k tokens
```

to close one simple requirement while another closes the same type with:

```text
15k
```

routing learns from that.

---

# 6. ECC: **YES — but absolutely do NOT load the whole thing**

Current official ECC is huge: its current plugin metadata advertises **68 agents, 292 skills and 94 legacy command shims**. :chatgpt-content-reference{index="3"}

If we dump all of that into FRIDAY:

```text
FRIDAY PRD
+
ECC 292 skills
+
our skills
+
MCP
+
agents
```

we have recreated the exact token problem we're trying to solve.

So:

# `ECC = pattern/capability source`

not:

# `ECC = FRIDAY build operating system`

I would mine/adopt specifically:

- worktree parallelization;
- context-efficient subagent orchestration;
- verification loops;
- continuous-learning patterns;
- session persistence;
- research-first debugging;
- model tiering;
- useful security hooks;
- selected language/test skills.

ECC itself emphasizes context optimization, model selection, memory persistence, verification loops, parallelization and iterative retrieval. :chatgpt-content-reference{index="4"}

### If we install ECC at all

Use:

```text
minimal profile
```

first.

Never:

```text
ECC full
+
manual ECC install
+
old everything-claude-code
+
our hooks
```

The official migration docs specifically warn that running both old and new ECC versions duplicates skills, commands and hook executions. :chatgpt-content-reference{index="5"}

That could literally make token/performance problems worse.

---

# 7. Awesome Claude Code: **discovery registry, not runtime**

Yes, use these lists.

But their role should be:

```text
Technology Scout
       ↓
Awesome Claude Code
       ↓
candidate discovery
       ↓
SkillSpector
       ↓
benchmark
       ↓
adopt/reject
```

One current Awesome Claude Code index tracks agent harnesses, routing/proxies, memory/context, hooks, MCPs, skills and other Claude Code tooling. :chatgpt-content-reference{index="6"}

Another production-tested collection contains hooks for things like preventing main-branch commits, post-edit linting and pre-commit quality gates. :chatgpt-content-reference{index="7"}

Useful.

But:

```text
awesome list
≠
install everything
```

---

# 8. **RTK should be tested immediately**

This might produce one of the fastest token reductions during the build.

`rtk-ai/rtk` is a Rust CLI proxy that filters the output of common commands before it reaches the coding model.

It supports things like:

```text
ls
tree
cat
rg
git
pytest
npm test
cargo test
docker
```

and its project reports large reductions in command-output tokens; their example session estimates roughly ~118k tokens of raw CLI output reduced to ~24k. Those are project-provided figures, so we should benchmark on FRIDAY rather than assume them. :chatgpt-content-reference{index="8"}

This is particularly relevant because you have **4,700+ tests**.

Instead of the agent seeing:

```text
4,717 passed...
thousands of lines...
```

it gets:

```text
4717 passed
0 failed
duration: ...
```

And when failures exist:

```text
3 failed

FAIL:
test_x
test_y
test_z
```

Then retrieve full traceback only for those.

### Architecture

```text
shell command
     ↓
ShellOutputRouter
     ├─ raw artifact
     └─ RTK compressed view
             ↓
           agent
```

Raw output remains available.

That's important.

---

# 9. Do not stack RTK + Caveman blindly

We already have Caveman.

So:

```text
pytest
 ↓
RTK
 ↓
Caveman
 ↓
model
```

could overcompress.

Use:

# `OutputCompressionRouter`

```text
CLI structured command?
→ RTK

large miscellaneous payload?
→ Caveman

semantic document?
→ CortexOS summary

critical exact evidence?
→ raw
```

One compressor per stage unless explicitly benchmarked.

---

# 10. Superpowers: use the orchestration ideas

Current Superpowers releases specifically focused on reducing subagent-review overhead; its release notes report roughly 50% lower token usage and around 2× speed in its own evaluations after collapsing redundant review agents. :chatgpt-content-reference{index="9"}

This supports something I already wanted us to do:

### OLD

```text
implementer
→ spec reviewer
→ code reviewer
→ second reviewer
→ controller
```

### NEW

```text
implementer
→ one fresh task reviewer
```

For normal P1 work.

For P0:

```text
implementer
→ deterministic tests
→ independent verifier
```

Only security-critical architecture may need more.

This avoids reviewer swarms.

---

# 11. Planning-with-Files: **don't install it as another planning system**

The pattern is excellent:

```text
task_plan.md
findings.md
progress.md
```

It treats filesystem as durable memory and context as temporary working memory. :chatgpt-content-reference{index="10"}

But FRIDAY already has:

```text
Objective OS
Requirement Ledger
Project Brain
Failure Genome
```

Installing another independent planning system creates duplication.

So adopt only the principle:

```text
persistent state on disk
+
fresh context
```

Our implementation ledger already does this better for FRIDAY.

---

# 12. Agent types we actually need during the build

Not 68.

Start with **six logical roles**, instantiated only when needed.

### `BuildController`

Cheap.

Does:

```text
DAG
scheduling
budgets
merge scheduling
```

### `Implementer`

Does:

```text
one requirement/slice
```

### `Investigator`

Only on unknown/failure.

Does:

```text
repo search
root cause
focused web research
```

### `TestWorker`

Runs:

```text
targeted/component tests
```

Usually deterministic; doesn't need much model reasoning.

### `Verifier`

Fresh context.

Checks:

```text
requirement
diff
tests
evidence
```

### `Integrator`

Merges independent worktrees.

That's enough initially.

Other specialists are created only when a requirement warrants them.

---

# 13. Don't let agents talk to each other in long conversations

This is another huge token leak.

Bad:

```text
Agent A → Controller: 3000 tokens
Controller → Agent B: 2500
Agent B → Controller: 4000
Controller → Reviewer: 3500
```

Good:

```text
Agent A
→ commit SHA
→ evidence.json
→ short status
```

Agent B reads artifact only if required.

Use:

```yaml
status: PASS
requirement: RES-003
commit: abc123
tests:
  passed: 18
evidence:
  - artifact://...
notes:
  - baseline-aware pressure implemented
```

Maybe 100–300 tokens.

---

# 14. No general agent chat room

Worker communication:

```text
artifact references
structured results
events
```

not conversational debates.

If two agents disagree:

```text
Controller
→ create adjudication task
→ one strong model
```

Don't have five agents argue.

---

# 15. Use a **Cascade Model Strategy**

For each task:

```text
cheap implementer
        ↓
PASS?
├─ yes → verify
└─ no
    ↓
normal stronger implementer
    ↓
still stuck?
    ↓
Opus advisor
```

Not:

```text
Opus immediately
```

ECC specifically promotes model selection and tiering as a token optimization strategy. :chatgpt-content-reference{index="11"}

---

# 16. Hard budgets for agents

Every agent gets:

```yaml
budget:
  turns: 12
  tokens: 20000
  shell_calls: 30
  web_searches: 0
  full_test_runs: 0
```

Example investigator:

```yaml
web_searches: 3
tokens: 12000
```

Hard P0 debugger:

```yaml
tokens: 40000
advisor_calls: 2
```

When budget approaches limit:

```text
return evidence + blocker
```

not continue indefinitely.

---

# 17. Agent spawn rule

Spawn only if at least one is true:

```text
A. work can run independently in parallel

B. task needs isolated context

C. specialist knowledge materially improves it

D. fresh verification is valuable

E. current context is polluted
```

Do not spawn because:

```text
"multi-agent sounds powerful"
```

---

# 18. Make agent creation itself cheap

Agent description should be tiny:

```yaml
role: implementer
requirement: RES-003
worktree: ...
budget: normal
```

Then it dynamically retrieves:

```text
requirement
files
tests
```

Do not give every agent a 10k system prompt.

One lean agent harness we found intentionally keeps startup context under roughly 500 tokens and loads full skills/agents only on invocation; its rationale is exactly that excessive harness instructions themselves can reduce performance. :chatgpt-content-reference{index="12"}

---

# 19. Full suite scheduling needs a gate

Right now:

```text
4717 tests
```

Run full suite only on:

```text
checkpoint
integration merge
P0 cluster completion
before push
release
```

Workers run:

```text
targeted tests
```

Integrator runs:

```text
affected component suite
```

Checkpoint agent runs:

```text
full suite
```

That should dramatically cut build time.

---

# 20. Build cache

Add:

# `TestResultCache`

Key roughly:

```text
test
+
code dependency fingerprint
+
environment fingerprint
```

If unrelated files changed:

don't rerun expensive unaffected tests.

Of course final release still runs everything.

---

# 21. Research agents should be rare during implementation

You already did months of architecture research.

During build:

```text
unknown implementation fact?
→ focused investigator

new cool repo?
→ backlog
```

Not:

```text
every slice
→ web research
```

We should currently have:

```text
Technology Scout = PAUSED
```

until P0 burn-down reaches zero, except when a current blocker needs external research.

---

# 22. Add these as **Build Harness requirements**, not product-core clutter

I'd add:

### `BUILD-001 — DAG Parallel Execution`

Independent implementation requirements SHALL be executable concurrently through isolated workers.

### `BUILD-002 — Sparse Agent Policy`

Workers SHALL be spawned only for parallelism, specialization, context isolation or independent verification.

### `BUILD-003 — Per-Agent Budget`

Every worker SHALL have bounded tokens, turns, tool calls, research calls and wall time.

### `BUILD-004 — Artifact Handoffs`

Worker-to-worker communication SHALL prefer structured artifacts and references over conversational transcript transfer.

### `BUILD-005 — Model Cascade`

Build tasks SHALL begin with the least costly model predicted to meet the requirement and escalate only on measured need.

### `BUILD-006 — Isolated Worktrees`

Concurrent code workers SHALL operate in independent Git worktrees/branches.

### `BUILD-007 — Output Compression`

CLI output SHALL be filtered/compressed before entering LLM context where exact raw text is not required, while preserving recoverable raw artifacts.

### `BUILD-008 — Test Impact Scheduling`

Targeted tests SHALL be preferred during iteration; full regression SHALL be reserved for integration/checkpoint/release boundaries.

### `BUILD-009 — Agent ROI`

The harness SHALL measure verified work relative to tokens, wall time, retries and compute per worker strategy.

### `BUILD-010 — Harness Minimalism`

New agents/hooks/skills/process steps SHALL not become defaults unless evaluation shows positive value.

That last one matters a lot.

---

# What to take from ECC / Awesome Claude Code

My shortlist now:

| Project/idea | Use |
|---|---|
| **ECC** | Select orchestration, worktree, memory, verification, model-tiering patterns |
| ECC full 292 skills | **No** |
| **RTK** | **High priority A/B test for shell-output compression** |
| **Superpowers** | Subagent-driven development + cheaper orchestration/review pattern |
| Planning-with-Files | Principle only; Objective OS already covers it |
| Awesome Claude Code | Candidate discovery registry |
| selected tested hooks | cherry-pick |
| giant agent collections | no bulk install |
| Caveman | non-CLI/general recoverable compression |
| Ponytail | code minimalism |
| SkillSpector | skill supply-chain scan |
| SkillEvaluator | actual skill ROI |

---

# This is how I want the build to run

```text
                        BUILD CONTROLLER
                         mid-tier model
                              │
                 read requirement DAG only
                              │
            ┌─────────────────┼─────────────────┐
            ▼                 ▼                 ▼
       IMPLEMENTER 1      IMPLEMENTER 2      INVESTIGATOR
        normal model       normal model         on demand
       worktree A          worktree B
            │                 │
            ▼                 ▼
       targeted tests     targeted tests
            │                 │
            └────────┬────────┘
                     ▼
                 INTEGRATOR
                     │
              affected tests
                     │
                     ▼
             FRESH VERIFIER
                     │
               ┌─────┴─────┐
               ▼           ▼
             PASS         FAIL
               │           │
               │       repair worker
               ▼
            CHECKPOINT
               │
       full suite only here
               │
               ▼
          NEXT DAG WAVE
```

And underneath:

```text
RTK
Caveman
artifact store
worktrees
test cache
resource governor
token budgets
```

---

# What I would tell the current builder **right now**

Paste this:

> **BUILD-HARNESS OPTIMIZATION DIRECTIVE:** The current implementation program is too serial and token-expensive. Keep `PRD.md` unchanged as product authority, but change execution immediately to dependency-DAG waves with sparse parallel workers. Use one lightweight/mid-tier BuildController that schedules work but does not perform routine coding. Spawn independent Implementers into separate Git worktrees, up to the concurrency permitted by the AdaptiveHardwareGovernor. Each worker receives only its requirement packet, relevant files/tests, constraints and a strict token/tool/time budget. Workers communicate through commits, structured evidence and artifacts, not long conversational reports. Use a fresh verifier only after meaningful requirement completion; do not create multiple reviewers unless consequence requires it. Frontier/Opus-class reasoning is reserved for architecture, unresolved P0 root causes and critical adjudication, not coordination or test-log reading. Run targeted tests in workers, affected regression after integration, and the full suite only at coherent checkpoints/push/release. Add an A/B spike for `rtk-ai/rtk` as the default structured shell-output compressor for git/search/test commands; preserve raw outputs as artifacts and do not stack RTK and Caveman unless benchmarked. Evaluate ECC and Superpowers as pattern sources: selectively adopt worktree parallelization, model cascading, context-efficient subagent orchestration, persistent handoffs and verification loops; do not bulk-load ECC's full agent/skill catalog. Pause Technology Scout/new-framework research during P0 burn-down unless a current blocker specifically requires external research. Measure each worker's verified requirements closed, tokens, wall time, retries and tool calls. Continue autonomously toward unresolved P0 = 0.**

That is the change I would make **before adding more product features**.

The key distinction is:

> **Agents should reduce wall time through independent parallel work, not multiply token usage through conversation.**

One manager, **few temporary workers**, isolated worktrees, tiny work packets, structured handoffs, compressed tool output, selective frontier reasoning. That's the build system we need.
````

## Message 2

````text
Yes bro. **117.9k tokens in ~1 hour for one build stretch is exactly the signal that our builder harness needs another redesign.** The goal is not “use fewer tokens and accept worse work.” The goal is:

> **same or better verified output, fewer model turns, less repeated context, less duplicated work, and more useful parallelism.**

I researched current agent-harness work, coding-agent orchestration repos, and the latest approaches from OpenAI and Anthropic. The clearest conclusion is that we should **stop making an LLM manage every part of the build**.

The next version should be a **mostly deterministic build scheduler with temporary coding agents**, not a giant master agent supervising a swarm through conversation.

---

# 1. The biggest change: remove the LLM from the scheduling loop

Our current mental model was still:

```text
MAIN MODEL
   ↓
reads ledger
   ↓
decides what to do
   ↓
spawns agent
   ↓
reads report
   ↓
decides next
```

Every one of those arrows costs tokens.

Instead:

# `FridayBuildKernel`

```text
                    PRD REQUIREMENT GRAPH
                              │
                              ▼
                  DETERMINISTIC SCHEDULER
                              │
             dependency / priority / locks
                              │
                  ┌───────────┼───────────┐
                  ▼           ▼           ▼
                TASK A      TASK B      TASK C
                  │           │           │
               worker      worker      worker
                  │           │           │
                  └───────────┼───────────┘
                              ▼
                     deterministic merge
                              │
                              ▼
                       verification gate
```

The scheduler does **not require Claude** to decide:

```text
what is unblocked?
which worker is idle?
which dependency finished?
which worktree belongs to whom?
```

Code can answer all of that.

This is directly supported by OpenAI's new Symphony design: tasks start only when blockers are resolved, isolated workspaces are created per issue, concurrency is bounded, failed runs use backoff/retry, and workflow policy lives in the repo rather than inside an endlessly growing agent conversation. :chatgpt-content-reference{index="0"}

This alone should remove a lot of useless supervisor tokens.

---

# 2. Use agents for intelligence, not bookkeeping

Agent:

```text
figure out concurrency race
design context algorithm
implement RES-003
review security-sensitive diff
debug failing integration
```

Code:

```text
choose next unblocked requirement
create worktree
launch process
measure tokens
watch heartbeat
kill timeout
collect test JSON
merge clean branch
calculate retry count
```

That distinction matters enormously.

---

# 3. Anthropic's 16-agent experiment gives us a warning

Anthropic recently built a compiler using **16 parallel Claude agents**, nearly 2,000 sessions and about **2 billion input tokens**. It worked, but it cost almost $20,000. More importantly, they found that parallelism stopped helping when all agents encountered the same sequential bottleneck; 16 workers simply attacked the same bug and overwrote one another. :chatgpt-content-reference{index="1"}

That's exactly what we **must not do**.

So FRIDAY Build should have:

```text
parallelism = f(
    independent_ready_tasks,
    changed_file_overlap,
    machine_headroom,
    model_budget
)
```

Not:

```text
parallelism = 10
```

For your machines, I would initially cap at:

```text
normal:
2 implementers

occasionally:
3 lightweight workers

hard sequential P0:
1 strong worker
```

Then benchmark.

---

# 4. Introduce a **Parallelism Value Test**

Before launching another agent:

```text
Does it work on an independent requirement?
        │
        ├── NO → don't spawn
        │
        └── YES
             ↓
Will files overlap heavily?
        │
        ├── YES → probably sequential
        │
        └── NO
             ↓
Is hardware GREEN?
        │
        ├── NO → queue
        │
        └── YES → spawn
```

Agents should parallelize **work**, not duplicate thinking.

---

# 5. OpenAI Symphony: HIGH VALUE, but mine the architecture

I think `openai/symphony` is now one of the most relevant repos for our builder.

It gives us:

- issue/task-driven execution;
- dependency-aware parallelism;
- isolated per-task workspaces;
- retry/backoff;
- stall detection;
- bounded concurrency;
- workflow policy stored in repo;
- continuation without resending the original prompt;
- observability. :chatgpt-content-reference{index="2"}

Classify:

```text
OpenAI Symphony
CLASS:
BUILD_ORCHESTRATOR_REFERENCE

PRIORITY:
VERY HIGH

ADOPT:
architecture/spec ideas

DO NOT:
immediately replace our Objective OS
```

Most important ideas to copy:

```text
ReadyQueue
WorkspaceManager
WorkerLease
RetryQueue
StallDetector
Reconciler
```

---

# 6. Beads: VERY interesting for our requirement DAG

`gastownhall/beads` is explicitly a dependency-aware graph for agents.

Its basic flow is beautifully small:

```text
create task
   ↓
dependency graph
   ↓
bd ready
   ↓
agent claims
   ↓
agent closes
   ↓
new blocked work becomes ready
```

It is also git/Dolt-backed and designed to survive agent restarts. :chatgpt-content-reference{index="3"}

This is almost exactly what our:

```text
data/prd_requirements.json
```

is slowly becoming.

I see two options.

### Option A — keep ours

Build:

```text
RequirementGraph
ReadyQueue
ClaimManager
```

ourselves.

### Option B — Beads adapter

```text
PRD requirements
    ↓
Beads
    ↓
bd ready
    ↓
BuildKernel
```

I would **benchmark before deciding**.

Do not use both permanently.

---

# 7. Gas City: research, don't install yet

`gastownhall/gascity` is essentially a composable orchestration SDK extracted from Gas Town, with:

- runtime providers;
- routing;
- health patrol;
- work tracking;
- declarative orchestration;
- supervisor reconciliation. :chatgpt-content-reference{index="4"}

Very relevant conceptually.

But this is exactly the kind of framework that could cause us to build:

```text
FRIDAY orchestrator
+
Gas City orchestrator
+
Hermes orchestrator
+
Claude orchestration
```

No.

Classify:

```text
REFERENCE / BAKE-OFF
```

Mine:

```text
health patrol
desired-state reconciliation
work routing
worker lifecycle
```

---

# 8. Firstmate: one idea is excellent

`firstmate` has a particularly useful design:

> one supervisor, isolated worktree workers, and a **zero-token event-driven watcher** supervising the fleet. :chatgpt-content-reference{index="5"}

The phrase **zero-token watcher** is exactly where we need to go.

Instead of an LLM constantly asking:

```text
Agent A finished?
Agent B finished?
Agent C alive?
```

use:

```text
process events
filesystem
git
JSON
heartbeats
```

Only wake intelligence when:

```text
worker failed
merge conflict
requirement ambiguous
verification failed
```

This could save a huge amount.

So add:

# `ZeroTokenSupervisor`

```text
process started
process exited
test result written
commit created
heartbeat stale
resource threshold hit
```

all deterministic.

---

# 9. Ralph is useful — but only for the fresh-context philosophy

There are several Ralph variants now.

One particularly clean one uses:

```text
stable prompt
+
mutable disk plan
+
fresh Claude process each iteration
```

and explicitly avoids hooks, agents, MCP servers and skills. :chatgpt-content-reference{index="6"}

Another Ralph implementation starts a fresh Claude process every iteration and stores state under `.ralph/`, so parent-session context doesn't grow. :chatgpt-content-reference{index="7"}

This validates our direction.

But I **wouldn't make Ralph the whole builder**, because we need dependency-parallel execution.

Use:

```text
Ralph pattern
=
fresh context per implementation unit
```

inside our scheduler.

---

# 10. Superpowers: strong evidence that fewer reviewers can be better

Superpowers 6 redesigned its subagent review flow from **two reviewers to one**, and its own evaluations reported approximately:

- 2× faster;
- nearly 50% fewer tokens;
- similar quality. :chatgpt-content-reference{index="8"}

That's extremely relevant to your problem.

Our old pattern:

```text
implementer
↓
spec reviewer
↓
quality reviewer
↓
security reviewer
↓
manager
```

can become:

### Normal P1

```text
implementer
↓
tests
↓
one fresh verifier
```

### P0/security

```text
implementer
↓
negative controls
↓
strong verifier
```

### extreme case only

additional specialist.

No reviewer bureaucracy.

---

# 11. RTK: highest priority immediate experiment

This may be our **quickest token win**.

`rtk-ai/rtk` compresses shell output for:

- git;
- tree;
- grep;
- pytest;
- npm;
- cargo;
- Docker;
- many other common commands.

It claims 60–90% reductions on supported command output, with less than 10 ms overhead. Those are upstream claims, so we should measure FRIDAY ourselves. :chatgpt-content-reference{index="9"}

Given your run:

> **117.9k tokens**

a lot of that is probably not intelligent reasoning.

It's likely:

```text
file reads
git diff
test output
grep
shell logs
repeated repository orientation
```

We need telemetry proving it.

So before anything else:

# Instrument token sources

```text
117,900
│
├── system instructions ?
├── repeated context ?
├── source files ?
├── test logs ?
├── shell ?
├── subagent prompts ?
├── reviewer duplication ?
└── actual reasoning ?
```

Then attack the top offenders.

---

# 12. Add `TokenAttribution`

For every model call:

```yaml
input_tokens:
  system: 1800
  requirement: 700
  source_code: 4800
  tools: 1200
  previous_history: 8200
  tool_results: 6100

output_tokens:
  reasoning: ...
  answer: ...
```

And per objective:

```text
TokenWasteReport
```

Examples:

```text
Repeated static prefix: 21%
Raw pytest output: 18%
Re-reading unchanged code: 14%
Reviewer duplication: 11%
Useful reasoning: 25%
...
```

Without this we're optimizing blind.

---

# 13. OpenAI's current harness engineering confirms three huge optimizations

OpenAI describes three ideas now used in the Codex/Work harness:

### Deferred discovery

Don't load all:

```text
MCPs
skills
plugins
tools
```

up front.

Find them when needed.

### Cap tool output

Their harness caps individual tool/MCP outputs at **10,000 tokens by default** unless the model requests more.

### Preserve prompt cache prefixes

Keep stable instructions/tool definitions deterministic and append new context at the end so prefix caching remains effective. :chatgpt-content-reference{index="10"}

These three should become explicit Build Harness requirements.

---

# 14. We need **prompt-cache-aware compilation**

Currently our context compiler probably generates slightly different instructions each task.

That destroys provider caching.

Instead:

```text
STABLE PREFIX
──────────────
build kernel rules
security invariants
worker role
tool definitions

DYNAMIC SUFFIX
──────────────
requirement
files
evidence
```

Never include:

```text
timestamp
random IDs
machine status
changing budget text
```

near the beginning unless needed.

OpenAI specifically says deterministic tool order and append-only history help maintain high prompt-cache hit rates. :chatgpt-content-reference{index="11"}

This is a major hidden optimization.

---

# 15. AirBnB's `agent-harness-optimizer`: very interesting future system

Airbnb just published `agent-harness-optimizer`.

This is probably one of the most interesting discoveries from today's research.

It automatically optimizes:

```text
system prompt
+
tool middleware
```

based on benchmark outcomes.

It supports several optimization strategies and explicitly evaluates:

```text
pass rate
reliability
cost
```

rather than just blindly shortening prompts. :chatgpt-content-reference{index="12"}

This is basically:

# **Harness learns to optimize itself**

But don't run it constantly.

Use offline:

```text
every major checkpoint
        ↓
sample 50 representative FRIDAY build tasks
        ↓
baseline current harness
        ↓
optimizer proposes mutations
        ↓
eval holdout
        ↓
better?
 ├─ yes → candidate
 └─ no → discard
```

This fits our self-development philosophy beautifully.

Classify:

```text
HIGH-VALUE EXPERIMENT
```

not production dependency yet—it is very new.

---

# 16. Strands Harness Optimizer: similar idea, watch

`strands-labs/harness-optimizer` also optimizes tunable context/harness components based on recorded rollouts. :chatgpt-content-reference{index="13"}

Again:

```text
HarnessOptimizationLab
├── Airbnb AHO
├── Strands optimizer
└── FRIDAY-native optimizer
```

Run bake-off.

Don't install both into production.

---

# 17. Oh-My-ClaudeCode: mine patterns, not whole framework

`oh-my-claudecode` currently has:

- automatic parallelization;
- staged team workflows;
- persistent execution;
- model routing;
- verification;
- learning.

Its project claims 30–50% token savings from model routing, but that is project-provided and must be benchmarked. :chatgpt-content-reference{index="14"}

Useful ideas:

```text
model cascade
team phases
HUD
persistent continuation
```

But our architecture already covers most of it.

So:

```text
REFERENCE ONLY
```

not another orchestrator.

---

# 18. gstack: cherry-pick specific engineering roles

`gstack` is more workflow/skill-oriented than infrastructure.

It provides opinionated:

```text
planning
engineering review
QA
release
investigation
benchmarking
deployment
```

skills. :chatgpt-content-reference{index="15"}

Useful to mine:

```text
/review
/qa
/investigate
/benchmark
/ship
```

But again:

don't globally load 30 gstack skills.

Use Skill Trust + Skill ROI.

---

# 19. Gas Town: excellent research, terrible default for our laptop

Gas Town claims to coordinate **20–30 agents** with persistent work state through Beads. :chatgpt-content-reference{index="16"}

That's useful to understand future scaling.

But your machines:

```text
16 GB RAM
```

and your problem:

```text
117.9k tokens/hour
```

mean the last thing we should do is launch 20 agents.

So classify:

```text
SCALING REFERENCE
NOT DEFAULT
```

---

# 20. Auto-Company: useful for long-lived agency pattern

`Auto-Company` wakes up a group, reads shared consensus, forms a 3–5-person squad, works, updates state, then sleeps. :chatgpt-content-reference{index="17"}

Useful concepts:

```text
wake
form temporary squad
execute
persist
sleep
```

That's relevant to FRIDAY's universal product later.

For current coding build:

not core.

---

# 21. New idea: **Agent Wave Scheduler**

I think this is the architecture we should implement now.

Requirements graph:

```text
               P0-A
              /    \
           P0-B    P0-C
             │      │
           P1-D   P0-E
              \    /
               P1-F
```

Scheduler calculates:

```text
Wave 1:
P0-A

Wave 2:
P0-B
P0-C

Wave 3:
P0-E
P1-D
```

Only independent nodes run together.

This is essentially what Symphony demonstrates with blocker-aware parallelism. :chatgpt-content-reference{index="18"}

---

# 22. Add **File Conflict Prediction**

Before parallel workers launch, inspect likely file surfaces.

Task A:

```text
friday/resource_governor.py
```

Task B:

```text
friday/resource_governor.py
```

Don't parallelize.

Task C:

```text
friday/mcp_firewall.py
```

Fine.

So:

```text
dependency independence
+
file-surface independence
=
parallelizable
```

This avoids merge churn.

---

# 23. Add **Speculative Agent Spawn**

Interesting optimization:

When a hard P0 begins, maybe spawn:

```text
Agent A:
implement likely fix

Agent B:
independently diagnose root cause
```

But only when:

```text
ExpectedValue(parallel investigation)
>
extra token cost
```

If Agent A succeeds immediately:

cancel Agent B.

This is analogous to speculative execution.

Use rarely.

---

# 24. Add **Early Cancel**

Very important.

Suppose 3 workers run.

Worker A proves architecture assumption wrong.

Workers B/C depend on that assumption.

Immediately:

```text
cancel B
cancel C
```

Don't let them burn another 30k tokens.

Scheduler needs dependency invalidation events.

---

# 25. Add **Token Stop-Loss**

Your example:

```text
117.9k tokens
```

should never happen silently again.

Per task:

```text
soft_limit = 15k
hard_limit = 30k
```

At soft:

```text
Agent must report:
progress
blocker
why more budget is useful
```

Scheduler either:

```text
continue
escalate model
fresh session
park
```

At hard:

terminate unless explicit high-value extension.

---

# 26. Add **Wall-Time Stop-Loss**

Similarly:

Normal requirement:

```text
10–20 min target
```

Complex P0:

```text
30–45 min
```

If one worker is still circling at 60 minutes:

that's almost always:

```text
bad decomposition
bad context
bad model
bad test loop
```

not:

> “give it another hour.”

---

# 27. Add **Test Sampling During Iteration**

Anthropic's compiler harness specifically used a fast mode running only 1–10% of tests during iteration, while full CI handled complete validation. :chatgpt-content-reference{index="19"}

We can do similar intelligently:

```text
edit
↓
directly affected tests

worker checkpoint
↓
dependency-neighborhood tests

wave integration
↓
larger regression

checkpoint
↓
4,700 suite
```

This is probably a major wall-time saving.

---

# 28. Add **Failure-Focused Output**

Anthropic also recommends test harnesses output just a few useful lines and write full details to a file, because thousands of bytes of test logs pollute agent context. :chatgpt-content-reference{index="20"}

Our test runner should produce:

```text
4717 tests
3 failed

FAIL:
CTX-003/test_compaction_literal
MCP-001/test_schema_change
RES-003/test_hysteresis

details:
artifact://test-run/9281
```

That's all the model initially sees.

---

# 29. Add **Re-read Avoidance**

Before reading a file:

```text
have we already read this exact hash in this worker?
```

If yes:

don't re-read.

Return:

```text
UNCHANGED SINCE READ
```

This is easy and potentially huge.

Same for:

```text
git diff
test artifact
research
```

---

# 30. Add **Code Structural Digest**

Instead of every fresh agent doing:

```text
tree
read 15 files
grep everywhere
```

generate once:

```text
RepoDigest
```

containing:

```text
modules
symbols
ownership
important interfaces
tests
dependency graph
recent commits
```

Each worker gets only relevant section.

Your codebase-memory ideas already point this way.

---

# 31. Add **Worker Warm Start**

Fresh context doesn't mean fresh ignorance.

Give worker:

```text
REQUIREMENT
CURRENT HEAD
RELEVANT ARCHITECTURE
3–7 RELEVANT FILES
KNOWN TEST COMMAND
PREVIOUS FAILURE, IF ANY
```

No need to spend first 5k tokens rediscovering repo basics.

---

# 32. But don't overprompt workers

Anthropic's newer harness-design work explicitly warns that as models improve, complex scaffolding can become unnecessary and should be simplified methodically rather than accumulated forever. :chatgpt-content-reference{index="21"}

This is important for us.

We have a risk of:

```text
FRIDAY prompt
+ PRD
+ ECC
+ Superpowers
+ gstack
+ PromptOS
+ skills
```

becoming another context-rot source.

So build:

# `Harness Minimalism Gate`

Every default instruction/hook/agent must answer:

```text
Does removing me reduce benchmark quality?
```

If no:

remove it.

---

# 33. My research ranking

| Technology | Use in FRIDAY Build |
|---|---|
| **OpenAI Symphony** | **A-tier — architecture reference for scheduler/workspaces/retry** |
| **RTK** | **A-tier — benchmark immediately** |
| **Superpowers** | **A-tier — review simplification + subagent patterns** |
| **Beads** | **A-tier — evaluate as requirement DAG/task state backend** |
| **Airbnb Agent Harness Optimizer** | **A-tier research — automatic harness optimization** |
| **Ralph** | **A-tier pattern — fresh iteration/context** |
| **Firstmate** | **B+ — zero-token watcher/worktree supervision ideas** |
| **Gas City** | **B+ reference — future orchestration SDK** |
| **ECC** | **B+ pattern source, don't bulk install** |
| **gstack** | **B — selective review/QA skills** |
| **Oh-My-ClaudeCode** | **B — routing/team pattern source** |
| **Gas Town** | **B scaling reference, too heavy now** |
| **Auto-Company** | **B universal FRIDAY research, not build core** |
| **Strands Harness Optimizer** | **B experimental competitor to Airbnb AHO** |

---

# 34. The build architecture I'd lock now

```text
                         FRIDAY BUILD KERNEL
                         DETERMINISTIC CODE
                                │
                 ┌──────────────┼───────────────┐
                 │              │               │
             Task Graph     Resource Gov.   Token Gov.
                 │              │               │
                 └──────────────┼───────────────┘
                                ▼
                           READY QUEUE
                                │
                        Wave Scheduler
                                │
              ┌─────────────────┼─────────────────┐
              ▼                 ▼                 ▼
         WORKTREE A        WORKTREE B        WORKTREE C
         Worker A          Worker B          Worker C
            │                 │                 │
       compact packet      compact packet     compact packet
            │                 │                 │
            ▼                 ▼                 ▼
        targeted           targeted          targeted
          tests               tests              tests
            │                 │                 │
            └──────────┬──────┴──────┬─────────┘
                       ▼             ▼
                  evidence       commits
                       │
                       ▼
                    Integrator
                       │
                 affected tests
                       │
                       ▼
                    Verifier
                       │
                ┌──────┴──────┐
                ▼             ▼
              PASS           FAIL
                │             │
                │        repair worker
                ▼
             CHECKPOINT
                │
          full regression
                │
                ▼
             NEXT WAVE
```

And the agents themselves are **ephemeral**.

---

# 35. What happens to 117.9k tokens?

We need a hard objective.

I would create:

# `BUILD-EFF-001`

> For representative P0/P1 implementation slices, reduce median total model tokens by at least 60% from the current harness baseline while maintaining or improving requirement verification rate.

And:

# `BUILD-EFF-002`

> Reduce median wall time by at least 40% on parallelizable slices without increasing regression failures.

Not guarantees.

These become benchmarks.

Your current observation:

```text
1h 3m
117.9k tokens
```

becomes one baseline data point.

We need maybe:

```text
same/representative slice
old harness vs new harness
```

not vague feelings.

---

# 36. A realistic target architecture could produce

Instead of:

```text
one session
117.9k
63 min
```

a parallelizable 3-task wave might become roughly:

```text
Controller:
~0–2k model tokens
mostly deterministic

Worker A:
12–20k

Worker B:
10–18k

Worker C:
8–15k

Verifier:
5–10k

TOTAL:
maybe 35–65k

WALL TIME:
bounded by slowest worker,
not sum of all three
```

Those are **design targets**, not promises.

If quality drops, we don't keep them.

---

# 37. One more new idea: **Harness A/B continuously**

Every 20 completed requirements:

```text
current harness
vs
candidate optimized harness
```

Use completed historical tasks as replay/evaluation cases.

Evaluate:

```text
success
tokens
time
retries
regressions
```

Airbnb's optimizer makes this idea particularly concrete: automatically mutate prompt/middleware, evaluate against holdout cases, and keep only improvements. :chatgpt-content-reference{index="22"}

Eventually FRIDAY's builder optimizes **its own building process**.

That's recursive improvement we can actually measure.

---

# What I would do next

Before more PRD implementation, I'd spend **one focused slice** building the Build Harness optimization itself:

```text
BH-OPT-01

1. token attribution
2. deterministic ReadyQueue scheduler
3. worktree workers
4. dynamic concurrency = 2 initially
5. RTK A/B
6. targeted test scheduler
7. hard token/time budgets
8. zero-token process watcher
9. artifact-only worker reports
10. fresh-context workers
```

Then take **3 unresolved P0 requirements** and run:

```text
OLD HARNESS
vs
NEW HARNESS
```

Compare:

```text
verification result
total tokens
wall time
number of model calls
number of file reads
test runtime
regressions
```

Only then roll it out.

That is the important part: **we are not optimizing for fewer tokens. We're optimizing for more verified engineering per token and per minute.**
````

## The owner's own words

````text
DO IT SAME FOR FRIDAY TOO
````
