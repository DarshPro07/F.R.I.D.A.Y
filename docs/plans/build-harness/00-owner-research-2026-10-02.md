# The owner's harness research and memory-admission guidance, 2026-10-02 (verbatim)

> Extracted byte-for-byte from session transcript 47979877 (one message, 2026-10-02T05:44Z, 2 pasted parts)
> by `extract_research.py`; the pasted-content tags are removed and nothing else is changed.
> No other text was in the message.
> Nothing below this note is the implementing agent's.

## Message 1 - Harness research: the next bottleneck

````text
Yes. I researched specifically for the **next bottleneck in the build harness**, not for random “cool agent” repos.

The main conclusion is that we should **not install another giant agent framework right now**. Your PRD already has the right v9/A7 control philosophy: scheduling should be deterministic code, parallelism must be earned, workers should be fresh/bounded, and expensive model judgment should wake only for ambiguity/failure/verification. Pasted text It also already requires token attribution, harness minimalism, event-driven supervision, prompt-cache-aware prefixes, early cancellation and a measured token/wall-time improvement against baseline. Pasted text

So the new research should mostly improve **orientation, test selection, worker isolation and agent efficiency**, rather than add more layers.

## The strongest new findings

| Candidate | What I found | FRIDAY decision |
|---|---|---|
| **DeusData/codebase-memory-mcp** | Builds a local persistent code knowledge graph of functions, classes, calls, routes and cross-service links. Its own benchmark/preprint reports ~10× fewer tokens and ~2.1× fewer tool calls across 31 repos versus file-by-file exploration. It has Windows builds and no built-in LLM. [GitHub](https://github.com/DeusData/codebase-memory-mcp/blob/main/README.md?utm_source=chatgpt.com) | **HIGH-PRIORITY orientation benchmark.** This directly attacks the “fresh worker spends 10k–50k tokens figuring out the repo again” problem. Do not automatically install; benchmark against our Code Capsule. |
| **Serena** | LSP/symbol-level retrieval and editing rather than line-by-line `grep/read`. It can find symbols, references, type hierarchies and edit whole symbols. Its own Claude/Copilot configs specifically tell agents to avoid whole-file reads and use symbolic exploration. [GitHub](https://github.com/oraios/serena?utm_source=chatgpt.com) | **HIGH-PRIORITY candidate.** Potentially better than reinventing semantic repo navigation ourselves. Benchmark it for orientation + precise edits. |
| **GitNexus** | Builds a knowledge graph with dependency chains, functional clusters and execution flows; can enrich searches automatically and detect stale indexes after commits. [GitHub](https://github.com/nxpatterns/gitnexus/blob/main/gitnexus/README.md?utm_source=chatgpt.com) | **Benchmark only.** Very interesting for impact analysis and “what execution flow does this code belong to?”, but its setup writes skills/hooks/configs, so don't let it touch the real environment until isolated evaluation. |
| **Aider RepoMap** | Mature tree-sitter-based repository map with explicit token budgets. Aider warns that too much map context itself can confuse models. [GitHub](https://github.com/Aider-AI/aider/blob/main/aider/coders/base_coder.py?utm_source=chatgpt.com) | **Excellent minimal fallback/reference.** I would borrow the bounded repo-map idea even if we don't run Aider. |
| **Repomix** | Tree-sitter compression, token-count tree, per-file detail levels, secret scanning and selective repository packing. [GitHub](https://github.com/yamadashy/repomix?utm_source=chatgpt.com) | **Useful for static Code Capsules**, especially external worker handoffs. Not an always-loaded entire-repo dump. |
| **code2prompt** | Can create compact entity/function maps and scoped context packages, with token estimates and Git information. [GitHub](https://github.com/mufeedvh/code2prompt/?utm_source=chatgpt.com) | **Same arena as Repomix**, probably not both in production. Benchmark as Code Capsule generator. |
| **Gas Town + Beads** | Gas Town coordinates many coding agents with persistent work state, identities, handoffs and a merge queue; Beads provides a dependency-aware work graph that survives sessions. [GitHub](https://github.com/gastownhall/gastown?utm_source=chatgpt.com) | **Mine architecture, don't adopt wholesale.** Your Objective OS + v9 scheduler already overlap heavily. Particularly copy ideas around merge queues and durable work ownership. |
| **Emdash** | Cross-platform local-first UI for running multiple coding agents in isolated Git worktrees, with diff review and local/SSH execution. [GitHub](https://github.com/generalaction/emdash/blob/main/AGENTS.md?utm_source=chatgpt.com) | **Reference implementation for worker/worktree UX**, not core dependency. |
| **Claude Squad** | Very simple approach: tmux sessions + one Git worktree per agent. Supports Claude, Codex, OpenCode, Aider. [GitHub](https://github.com/smtg-ai/claude-squad/blob/main/README.md?utm_source=chatgpt.com) | **Reference only.** We already need more deterministic integration than it provides. |
| **DeepAgents** | Fresh ephemeral subagents, isolated context, one final handoff, optional task planning. Their docs explicitly call out context/token efficiency from keeping heavy work inside children. [GitHub](https://github.com/langchain-ai/docs/blob/main/src/oss/deepagents/overview.mdx?utm_source=chatgpt.com) | **Good orchestration pattern.** Our v9 design already matches it; no need to import the framework. |
| **SWE-agent** | Its Agent-Computer Interface work found that short file windows, concise search results and immediate lint feedback materially improved coding-agent performance. [GitHub](https://github.com/SWE-agent/SWE-agent/blob/main/docs/background/aci.md?utm_source=chatgpt.com) | **Important design input for our worker tools.** Especially relevant to the orientation/output problem. |
| **wshobson/agents** | Huge multi-harness marketplace, but importantly it installs granular plugins rather than loading everything. It has dedicated orchestration, context-management, TDD, operating-kit and agent-team modules. [GitHub](https://github.com/wshobson/agents/blob/main/docs/plugins.md?utm_source=chatgpt.com) | **Discovery catalog only during P0.** Later cherry-pick individual skills after SkillSpector/eval. Never install all 184+ skills/200+ agents. |
| **Superpowers** | Subagent-driven engineering methodology. Its June 2026 release collapsed two reviewers into one and reports about 2× speed / ~50% lower tokens in its own evals. [GitHub](https://github.com/obra/superpowers/releases?utm_source=chatgpt.com) | **Keep mining.** Especially single-reviewer and worktree patterns. Benchmark claims independently. |
| **Context7** | Pulls current, version-specific library/API documentation on demand instead of relying on stale model knowledge. [GitHub](https://github.com/upstash/context7/blob/master/docs/overview.mdx?utm_source=chatgpt.com) | **Useful later as an on-demand docs backend**, not something to invoke on every task. |
| **pytest-testmon** | Records test↔code dependencies and runs only tests affected by changes. [GitHub](https://github.com/tarpas/pytest-testmon?utm_source=chatgpt.com) | **Very high-value spike now.** Could cut your 4,700-test iteration time dramatically while retaining a full suite at checkpoints. |
| **pytest-xdist** | Parallelizes pytest across worker processes and supports custom worker counts/distribution. [pytest-xdist](https://pytest-xdist.readthedocs.io/en/stable/?utm_source=chatgpt.com) | **Use adaptively**, governed by RAM/CPU. Never blindly `-n auto` on the 15.4 GB laptop. |
| **OpenHands Agent SDK** | Separates reasoning loop, state, tools, workspaces, providers and extensibility. [GitHub](https://github.com/OpenHands/docs/blob/main/sdk/arch/sdk.mdx?utm_source=chatgpt.com) | **Architecture reference**, not adoption. Too much overlap with FRIDAY Core. |

There is one particularly important correction from the independent research: **do not make RTK a default yet**.

RTK genuinely compresses individual command output and its own project reports large reductions. [GitHub](https://github.com/rtk-ai/rtk?utm_source=chatgpt.com) But JetBrains ran a paired agent benchmark in July 2026 and found that, on their tested Claude Code workload, RTK actually increased median cost by **7.6% at low reasoning effort** and was effectively neutral at high effort. Their explanation is important: smaller individual outputs can still lead to more turns, and rereading a longer trajectory costs more overall. [The JetBrains Blog](https://blog.jetbrains.com/ai/2026/07/rtk-claude-code-token-savings/?utm_source=chatgpt.com)

That is exactly why your existing `ORCH-010` rule—*nothing becomes default unless removing it makes the benchmark worse*—is correct.

Ponytail currently has better independent evidence: the same JetBrains series measured roughly **10.3% lower cost and 11% lower time** on its tested workload, though they appropriately caution against treating that percentage as universal. [The JetBrains Blog](https://blog.jetbrains.com/ai/2026/07/ponytail-skill-claude-tested/?utm_source=chatgpt.com)

So:

```text
RTK
→ A/B candidate

Ponytail
→ stronger A/B candidate

neither
→ mandatory global hook yet
```

## The orientation problem is now the highest-leverage harness optimization

Your paused session identified the right issue: **fresh-worker orientation tax**.

Today a fresh agent often does:

```text
git status
tree
read README
read AGENTS
grep classes
read file
grep references
read tests
read callers
read config
...
```

Maybe 20 tool calls before it writes one useful line.

I would replace that with:

```text
                    WORKER START
                         │
                         ▼
                 ORIENTATION ROUTER
                         │
             ┌───────────┼───────────┐
             ▼           ▼           ▼
        Code Capsule   Symbol Graph  Git Context
             │           │           │
             └───────────┼───────────┘
                         ▼
                    WORK PACKET
```

The **Code Capsule** should contain only:

```text
requirement
current HEAD
affected subsystem
entry points
relevant symbols
likely files
direct callers/callees
relevant tests
recent commits touching those surfaces
known failures
architecture invariant IDs
```

Target:

```text
500–2,000 tokens
```

not 15k.

### Build an `OrientationArena`

Before committing to one repo intelligence system, run the *same* 20–30 real FRIDAY tasks through:

```text
current grep/read
vs
in-house Code Capsule
vs
Serena
vs
codebase-memory-mcp
vs
Aider RepoMap
vs
GitNexus
```

Measure:

```text
orientation input tokens
tool calls before first edit
time to first correct file
irrelevant files opened
correct impacted-file recall
correct impacted-test recall
task success
total task tokens
```

My expectation—not yet evidence—is that a hybrid wins:

```text
static Code Capsule
+
persistent graph/symbol backend
+
targeted raw reads
```

rather than one giant repo map.

This is also consistent with Anthropic's context-engineering guidance: use external organization/indexes and retrieve just-in-time rather than memorizing whole corpora. [Anthropic](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents?_bhlid=1ba3bf95247bd689b5d1b76ed18f996f167a8d63\&utm_source=chatgpt.com)

## Multi-agent: use fewer agents than the hype suggests

This is another important research result.

Anthropic's own production research system found multi-agent systems useful for broad, independently parallel research, but they also report that multi-agent systems can consume around **15× the tokens of normal chats**, and explicitly note that many coding tasks do not parallelize as cleanly as research. [Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system?utm_source=chatgpt.com)

Therefore FRIDAY's build harness should not become:

```text
planner
architect
implementer
tester
reviewer 1
reviewer 2
critic
judge
researcher
security agent
performance agent
...
```

for every ticket.

For normal implementation:

```text
Controller — deterministic code
     │
     ├── Implementer A
     ├── Implementer B  ← only independent work
     │
     └── maybe Implementer C
               │
               ▼
          Integrator
               │
               ▼
       Fresh verifier if warranted
```

For simple work:

```text
one implementer
→ targeted test
→ done
```

For difficult P0:

```text
implementer
→ verifier
→ strong advisor only if failed
```

That matches your A7 rules exactly. Pasted text

## I found another strong optimization: **test impact selection**

This could save more wall time than another agent framework.

You have ~4,700 tests.

During implementation:

```text
edit friday/foo.py
```

doesn't mean:

```text
run 4,700 tests
```

`pytest-testmon` tracks which code each test executes and can then run only tests affected by changed code. [Testmon](https://www.testmon.org/?utm_source=chatgpt.com)

I would architect:

```text
                  TEST SCHEDULER

change
  │
  ├── explicitly named requirement tests
  │
  ├── testmon affected tests
  │
  └── static dependency-map additions
          │
          ▼
    targeted iteration suite

slice integration
          ↓
    broader regression

checkpoint / merge
          ↓
      FULL SUITE
```

Then `pytest-xdist` can parallelize the checkpoint suite, but the AdaptiveHardwareGovernor chooses perhaps:

```text
2 workers
```

rather than `-n auto` if memory is already tight. pytest-xdist explicitly allows custom worker counts and scheduling modes. [pytest-xdist](https://pytest-xdist.readthedocs.io/en/latest/distribution.html?utm_source=chatgpt.com)

This deserves a spike before more agent-framework work.

## The new PRD additions I would make

I **would not create Amendment A8 with 30 more architecture systems**.

Your current PRD explicitly says Technology Scout should be periodic and a trending project should not be adopted without a measured need. Pasted text

A7 is already rich enough.

I would add only four small build-harness obligations to the **build plan/backlog**, not make them new P0 product requirements:

```text
ORIENT-001
Every fresh coding worker SHALL receive a bounded repository
orientation capsule before unrestricted exploration.

Acceptance:
median orientation tokens and time must beat baseline while
maintaining or improving correct file/test identification.


ORIENT-002
Repository-intelligence backends SHALL be interchangeable and
benchmarked.

Candidates:
in-house capsule
Serena
codebase-memory-mcp
Aider RepoMap
GitNexus


TEST-IMPACT-001
Iteration test selection SHALL use requirement-targeted tests plus
measured change-impact selection where reliable; every checkpoint
still runs the complete authoritative suite.


HARNESS-EVAL-001
Any claimed token/time optimization SHALL be evaluated using paired
end-to-end agent tasks, not the tool's own "tokens saved" counter.
```

That last one is extremely important because of what the independent RTK benchmark showed.

## Things I would **not** add now

Gas Town, Emdash, Claude Squad, DeepAgents, OpenHands and Wshobson's full marketplace are interesting, but they overlap heavily with what you're already building.

I would use them as **design mines**.

For example:

```text
Gas Town
→ merge queue + durable ownership ideas

Emdash
→ worktree UX + provider abstraction

Claude Squad
→ simple process/worktree lifecycle

DeepAgents
→ fresh one-shot subagent pattern

SWE-agent
→ agent-friendly file/search interfaces

wshobson/agents
→ granular plugin architecture

Superpowers
→ efficient review workflow
```

Then implement only what survives your benchmark.

This is more aligned with the PRD's own `REUSE → EXTEND → REPAIR → MIGRATE → REBUILD` rule than importing half the agent ecosystem. Pasted text

## My updated priority order for the build

I would **not change your current resume plan much**.

First, resume the v9 harness work you already persisted. The three-worker split described there—kernel/scheduler, Code Capsule, deterministic integration—is sensible.

Inside that work, I would change the Code Capsule worker into a small **orientation bake-off**:

```text
Phase 1
build in-house minimal capsule

Phase 2
benchmark it against:
Serena
codebase-memory-mcp
Aider repo map

Phase 3
only if still unclear:
GitNexus / Repomix / code2prompt

Phase 4
pick one primary + one fallback

Phase 5
remove the losers from active harness
```

Then add:

```text
pytest-testmon spike
→ test impact quality

pytest-xdist spike
→ checkpoint wall-time

Ponytail A/B
→ code/cost/time

RTK A/B
→ likely reject unless FRIDAY workload disproves JetBrains result
```

After that, **stop harness research and drive P0 toward zero**.

The technology scout already exists precisely so we do not need to predict and install every useful framework today. Pasted text

My strongest new recommendation from this research is therefore not “add more agents.” It is:

> **Make a fresh worker understand the FRIDAY repository correctly in under ~2k orientation tokens, run only the tests its change can affect during iteration, and spawn another worker only when parallelism is real.**

If we solve those three things, the build should become materially faster without turning your 16 GB laptop into a 20-agent token furnace.
````

## Message 2 - Memory admission after bf39d3d

````text
Good. **Do not force-start anything at 1.4 GB free.** The kernel refusing the worker is the correct behavior, and the `bf39d3d` fix closes a meaningful race in the policy: HIGH pressure should reduce concurrency, not weaken admission checks.

I would make three small improvements before calling the memory admission logic finished.

First, use **measured available-memory/commit headroom**, not a permanently hardcoded “3.2 GB worker” constant. Windows exposes both physical available memory and system commit usage/limit; Microsoft specifically treats available memory as the RAM immediately reusable by applications. [Microsoft Learn](https://learn.microsoft.com/en-us/windows/win32/memory/memory-performance-information?utm_source=chatgpt.com) A good admission formula would be closer to:

```text
worker_required =
    p95_peak_memory(worker_class)
    + cold_start_margin
    + safety_reserve

admit only if:

physical_available >= worker_required
AND
commit_available >= worker_required
AND
current_reserved_worker_memory + worker_required <= governor_budget
```

Your current measured values would still produce roughly:

```text
1.7 GB measured worker peak
+ 1.5 GB safety reserve
= 3.2 GB
```

but later a lighter worker might need only 1.1 GB, while a browser-heavy worker may need 4+ GB. The harness should learn those classes rather than treating every worker identically.

Second, add a **logical memory lease before process creation**. There is a tiny TOCTOU race today:

```text
watch sees 3.4 GB available
        ↓
decides worker may start
        ↓
Chrome / Windows / another worker grabs 800 MB
        ↓
worker actually launches
```

So do:

```text
CHECK
  ↓
CREATE MEMORY LEASE
  ↓
RECHECK
  ↓
SPAWN
  ↓
replace predicted lease
with measured worker usage
```

If the second check fails:

```text
release lease
→ remain WAITING_RESOURCES
```

This also prevents two workers from both observing the same 3.5 GB as “available” and both starting.

Third, make `1.7 GB` become a **worker-class measurement**, for example:

```text
IMPLEMENTER_LIGHT       p95 = ...
IMPLEMENTER_NORMAL      p95 = 1.7 GB
BROWSER_WORKER          p95 = ...
VERIFY_WORKER           p95 = ...
RESEARCH_WORKER         p95 = ...
FULL_SUITE_WORKER       p95 = ...
```

Then after each run:

```text
predicted peak
actual peak
error
```

feeds the governor. Eventually admission becomes much more accurate.

The other thing I'd preserve is exactly what you just discovered:

```text
LOW pressure
→ maybe several workers

MEDIUM
→ fewer workers

HIGH
→ at most one worker

BUT

every level
→ must still pass per-worker RAM admission
```

That's the right architecture. Pressure state chooses the **maximum concurrency ceiling**; it must never bypass the individual worker-fit check.

For Chrome, don't have FRIDAY arbitrarily kill the user's tabs. Chrome's own Memory Saver can deactivate inactive tabs and reload them when needed, and Google supports Moderate/Balanced/Maximum modes. [Google Help](https://support.google.com/chrome/answer/12929150?hl=en-GBso\&utm_source=chatgpt.com) If you want to free memory manually now, closing/suspending Chrome tabs is reasonable given the ~7 GB you measured, but that should remain a user decision rather than the build harness terminating foreground apps.

I would also add one negative control around `bf39d3d`:

```text
Plant:
pressure = HIGH
available = 2.9 GB
worker predicted requirement = 3.2 GB

Expected:
max_concurrency = 1
BUT
admitted_workers = 0
spawn_count = 0
```

and then:

```text
pressure = HIGH
available = 4.5 GB

Expected:
exactly one worker may start
```

That proves the bug cannot silently return.

For the detached P0 program, I like the current constraints:

```text
up to 6 waves
$15 cap
CORE-001 first
derive-a-check ruling
```

I would add just two safeguards:

```text
per-wave cost cap
+
minimum progress requirement
```

For example, a wave that consumes a significant part of the $15 without either closing a requirement, generating a failing reproduction, or producing a named blocker should terminate and replan instead of consuming the rest of the budget.

So the state should simply remain:

```text
P0_PROGRAM = WAITING_RESOURCES
reason = INSUFFICIENT_MEMORY
required ≈ 3.2 GB
available ≈ 1.4 GB
workers_started = 0
```

That's not a failed run. It's **correct resource-aware scheduling**.

And the next thing I'd build after the first worker actually runs is the memory-lease mechanism above—it is the main remaining hole I see in this part of the kernel.
````

