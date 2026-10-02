# The owner's product-first override and v12 parallel product build directive, 2026-10-02 (verbatim)

> Extracted byte-for-byte from session transcript 47979877 (2026-10-02T12:25Z, 2 pasted parts) by
> `extract_master.py`; the pasted-content tags are removed and nothing else is changed.
> The owner's own words between the pastes: 'ok tell me when the exe built and run the task in sub agents optimised efficieny and running code in parrle building like many task doing in parrlel using opuss sonnet and other models when required ok /find-skills'
> Nothing below this note is the implementing agent's.

## Message 1 - Product-first override: park CORE-001, build the rest

````text
Yes bro. **Park CORE-001 and stop waiting on it. Build the rest of FRIDAY now.**

Your PRD already has plenty of independent product P0 work: durable objectives, intent compilation, context management, model routing, memory, capability safety, trust/lineage, security boundaries, action evidence, workspace isolation, MCP integrity, browser verification, resource control, evaluation, and the generic Cyber Lab boundary. Pasted text(20261002-053617) Pasted text(20261002-053617)

Anthropic’s long-running-agent work supports this approach: decompose a large product into independent chunks, persist clean handoffs, and let fresh sessions continue rather than allowing one stuck feature to monopolize the whole program. [Anthropic](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents?lid=1f0n56CPItf3Nm9NR\&utm_source=chatgpt.com)

I would give the builder this **product-first override** now:

```text
# FRIDAY PRODUCT-FIRST CONTINUATION DIRECTIVE

The owner has explicitly decided to stop waiting for CORE-001.

CORE-001 is NOT cancelled and is NOT VERIFIED.

Change its scheduler state to:

    PARKED_BY_OWNER

Reason:

    currently resource-blocked / escalation deferred while independent
    product requirements remain buildable.

Disable/cancel any detached waiter whose only purpose is to automatically
start CORE-001 when RAM reaches its threshold.

Do not let CORE-001 consume RAM, worker slots, model budget or controller
attention until the owner explicitly resumes it or all higher-value
independent product work is exhausted.


# PRIMARY MISSION

BUILD THE REST OF THE FRIDAY/JARVIS PRODUCT DEFINED BY PRD.md.

FRIDAY IS THE PRODUCT.

The build harness is already sufficiently operational.

From this point:

    PRODUCT work dominates.
    HARNESS work is reactive only.
    TECHNOLOGY research is on-demand only.


# EXECUTION POLICY

Read the current authoritative requirement ledger.

Ignore already VERIFIED requirements.

Ignore:

    CORE-001
    PARKED_BY_OWNER requirements
    BLOCKED_EXTERNAL requirements
    WAITING_USER requirements
    dependency-blocked requirements

Select the highest-priority DEPENDENCY-READY PRODUCT requirement.

Do not wait because another requirement is blocked.


# PRIORITY

1. Remaining runnable PRODUCT P0
2. Product P1 that unlocks multiple P0/P1 paths
3. Product capabilities required for golden journeys
4. Harness fixes only when a measured failure blocks real product work
5. Optional experiments/backlog later


# PRODUCT AREAS TO KEEP MOVING

The scheduler should use actual ledger state, not assume these are all unresolved,
but candidate P0 product families include:

    CORE-002
    INTENT-001

    OBJ-001
    OBJ-002
    OBJ-003

    TRAJ-001

    CTX-001
    CTX-002
    CTX-003

    COG-001

    MODEL-001
    MODEL-002

    NEURAL-002

    MEM-001
    MEM-002
    MEM-003

    CAP-001
    CAP-002

    TRUST-001
    TRUST-002

    SEC-001
    SEC-002
    SEC-003
    SEC-004
    SEC-005

    NET-001

    ACT-001

    WS-001

    MCP-001

    BROW-001
    BROW-002

    VERIFY-001
    VERIFY-002

    STOP-001

    RES-001

    CYBER-001
    CYBER-002

    EVAL-001

Use the current ledger to remove anything already VERIFIED or dependency-blocked.


# AGENTIC BUILD LOOP

For each ready product requirement:

    SELECT
      ↓
    ORIENT
      ↓
    IMPLEMENT
      ↓
    TARGETED TEST
      ↓
    NEGATIVE CONTROL IF CRITICAL
      ↓
    VERIFY REAL STATE
      ↓
    COMMIT
      ↓
    UPDATE LEDGER
      ↓
    NEXT PRODUCT REQUIREMENT

Do not return to CORE-001 automatically.


# AGENT USAGE

Use agents when they provide actual value.

SCOUT
    cheap, optional
    maps relevant files/symbols/tests.

IMPLEMENTER
    fresh bounded context
    builds one coherent requirement.

INVESTIGATOR
    only for unknown/root-cause problems.

VERIFIER
    independent and fresh for P0/security/authority claims.

INTEGRATOR
    only when semantic merge/conflict requires model judgment.

Do not create agent swarms.

Do not transfer full transcripts between workers.

Use compact artifacts.


# ONE-WORKER MACHINE

If only one heavy worker fits:

    product worker
    → exits
    → deterministic tests
    → next product worker

Do not use the only slot for speculative harness optimization.


# MULTI-WORKER MACHINE

If resources permit two or more workers:

    SLOT 1 = PRODUCT
    SLOT 2 = independent PRODUCT first,
             HARNESS/SCOUT only if useful

Never allocate every available slot to harness work while runnable product P0 exists.


# BLOCKED REQUIREMENT RULE

Whenever a requirement gets stuck:

    record evidence
    park it
    continue another independent requirement

Do not spend the whole program repeatedly repairing one requirement.

After repeated failures:

    escalate later with fresh context.


# HARNESS FREEZE

No proactive harness expansion.

Allowed harness work only when a measured product run exposes:

    program crash
    incorrect accounting
    unsafe resource admission
    bad rollback
    corrupt integration
    missing recovery
    severe token/wall-time regression

The fix must be minimal and immediately dogfooded on the product task that exposed it.


# RESEARCH FREEZE

Do not browse for new frameworks/repos during normal implementation.

Research only when a current product requirement needs an external fact or
cannot proceed from repository evidence.

Interesting repositories go to:

    BACKLOG_CANDIDATES.md

not into the current implementation program.


# CAPABILITY PRODUCT WORK

Do NOT postpone the actual FRIDAY capability systems behind harness work.

When their dependencies become ready, continue building:

    Capability Fabric
    Capability Forge / GitHub repo assimilation
    Skill lifecycle
    MCP integration
    Workflow Fabric
    Browser/Desktop/ComputerUse Fabric
    Research OS
    Memory / Project Brain
    Model Gateway
    local-model fabric
    CLI Gateway
    voice
    remote control
    onboarding
    installer
    self-development
    proactive runtime

according to PRD priority and dependency state.


# COST/TOKEN RULE

Use the cheapest capable route.

Do not leave an LLM alive while:

    tests run
    builds run
    downloads run
    CI runs
    timers wait

Persist the job and wake the controller on completion.

Keep worker context small.

No whole PRD.
No old transcript.
No giant logs.


# TEST RULE

During development:

    targeted tests

After requirement integration:

    affected regression

Checkpoint:

    full authoritative suite

A timeout is:

    TEST_TIMEOUT

not:

    whole-program crash.


# PRODUCT SUCCESS METRIC

Optimize:

    VERIFIED PRODUCT REQUIREMENTS CLOSED
    -------------------------------------
        TOKENS + COST + WALL TIME

Not:

    harness sophistication
    number of agents
    number of commits
    amount of research


# CONTINUATION

LOOP:

    refresh product ledger

    exclude:
        VERIFIED
        CORE-001
        blocked requirements

    determine dependency-ready PRODUCT work

    dispatch highest-value requirement

    implement

    test

    verify

    commit

    update evidence

    continue

UNTIL:

    no independent applicable PRODUCT requirement remains runnable.


# CORE-001

Do not silently forget it.

Maintain:

    CORE-001
    state = PARKED_BY_OWNER

Keep:

    escalation packet
    branch
    tests
    evidence
    blockers

Resume only when:

    owner explicitly requests it

OR

    other important product work is exhausted
    and resources support the escalation.


# FINAL DIRECTIVE

STOP WAITING FOR CORE-001.

STOP TRYING TO PERFECT THE HARNESS.

BUILD FRIDAY.

ONE BLOCKED REQUIREMENT MUST NEVER BLOCK THE PRODUCT.

CONTINUE THE REST OF PRD.md AUTONOMOUSLY,
REQUIREMENT BY REQUIREMENT,
UNTIL THE REMAINING PRODUCT IS BUILT AND VERIFIED.
```

And I would make **one more change to the scheduler**: `PARKED_BY_OWNER` should be a first-class non-terminal state. That way CORE remains visible in the final report but does not continuously re-enter the ready queue.

The PRD explicitly says capabilities should be controlled action interfaces, and it already defines FRIDAY as capable of browser, desktop, communications, calendars, creative apps, mobile, simulation and future devices—not merely a build harness. Pasted text(20261002-053617) The requirement matrix also contains the product systems we should now attack rather than waiting on one stuck control-plane requirement. Pasted text(20261002-053617)

So from here I would aim for:

```text
CORE-001        PARKED

REST OF P0      ↓↓↓

P0 remaining
20
↓
15
↓
10
↓
5
↓
0 except parked/externally blocked

then P1 product capabilities
```

**That is the right move now: build JARVIS, not wait for JARVIS's harness to become perfect.**
````

## Message 2 - v12 parallel product build directive (EXE_READY gate)

````text
Yes — this is the build mode I’d lock now.

The **goal is no longer “finish harness work.”** The goal is:

> **Build the rest of FRIDAY in parallel, use the harness to accelerate it, and notify you only when the Windows EXE is genuinely built and verified.**

The current PRD already defines FRIDAY as a universal local-first cognitive operating system and requires evidence rather than trusting a model saying “done.” Pasted text Anthropic’s recent harness work also supports keeping scaffolding as simple as possible and increasing complexity only where measured results justify it. [Anthropic](https://www.anthropic.com/engineering/harness-design-long-running-apps?utm_source=chatgpt.com) Superpowers’ current work similarly found that redundant reviewers and over-detailed planning waste substantial time/tokens; their newer lean planning reduced plan generation dramatically, and their single-reviewer redesign cut overhead in their own evaluations. [GitHub](https://github.com/obra/superpowers/releases?utm_source=chatgpt.com)

Use this **as the new execution directive**:

```text
# =============================================================================
# FRIDAY AUTONOMOUS PARALLEL PRODUCT BUILD DIRECTIVE — v12
# PRODUCT-FIRST / MULTI-AGENT / MODEL-ROUTED / TOKEN-EFFICIENT
# =============================================================================

PRIMARY OBJECTIVE:

BUILD THE COMPLETE APPLICABLE FRIDAY/JARVIS PRODUCT DEFINED BY PRD.md
AND PRODUCE A VERIFIED WINDOWS FRIDAY.exe.

DO NOT STOP THE PROGRAM BECAUSE ONE REQUIREMENT FAILS.

DO NOT WAIT FOR CORE-001.

DO NOT FINISH THE HARNESS BEFORE THE PRODUCT.

THE HARNESS EXISTS TO MAKE FRIDAY BUILD FASTER, CHEAPER AND MORE RELIABLE.


# =============================================================================
# 1. FINAL OUTCOME
# =============================================================================

The program continues until:

- all feasible product P0 requirements are VERIFIED;
- remaining blocked requirements are explicitly classified;
- required product P1 work for the executable is complete;
- FRIDAY.exe is built;
- FRIDAY.exe is validated from the built artifact;
- core golden journeys run through the EXE;
- state survives restart/rebuild;
- runtime dependencies are correctly packaged/provisioned;
- installer/bootstrap behavior is tested;
- final full regression passes on the exact release candidate;
- release evidence is persisted.

ONLY THEN emit:

    EXE_READY

and notify the owner:

    "FRIDAY.exe is built and verified."

Never notify merely because PyInstaller/Nuitka/etc. produced an .exe.


# =============================================================================
# 2. EXE STATES
# =============================================================================

Use these exact states:

EXE_NOT_BUILT
EXE_BUILDING
EXE_BUILT_UNVERIFIED
EXE_VALIDATING
EXE_BLOCKED_EXTERNAL
EXE_READY

EXE_READY requires real validation.

If compilation succeeds but runtime fails:

    EXE_BUILT_UNVERIFIED

not:

    EXE_READY


# =============================================================================
# 3. PRODUCT-FIRST SCHEDULING
# =============================================================================

Maintain queues:

PRODUCT
HARNESS
RESEARCH
TEST
INTEGRATION
PACKAGING

Default rolling effort:

PRODUCT      70–80%
HARNESS      10–20%
RESEARCH      0–10%
PACKAGING     increases as release nears

HARNESS may outrank PRODUCT only when a measured harness defect:

- crashes the program;
- corrupts state;
- miscounts money/tokens;
- prevents integration;
- prevents safe worker execution;
- breaks rollback;
- makes product evidence unreliable.

After the minimal harness repair:

RETURN TO PRODUCT.


# =============================================================================
# 4. PRODUCT DAG
# =============================================================================

Read the requirement ledger.

Build a dependency DAG.

Every iteration:

1. exclude VERIFIED;
2. exclude PARKED_BY_OWNER;
3. exclude BLOCKED_EXTERNAL;
4. exclude WAITING_USER;
5. exclude dependency-blocked work;
6. find all dependency-ready product requirements;
7. score them;
8. dispatch the highest-value independent set.

CORE-001 remains parked until explicitly resumed or needed as a hard dependency.


# =============================================================================
# 5. PARALLEL BUILD
# =============================================================================

Parallelize independent work.

Example:

Worker A:
    MEM-xxx

Worker B:
    ACT-xxx

Worker C:
    MCP-xxx

Worker D:
    browser / packaging / eval

ONLY when:

- write sets are sufficiently independent;
- dependency graph permits it;
- memory permits it;
- integration cost is reasonable.

Every concurrent coding worker gets:

    its own Git worktree
    its own branch
    its own requirement packet
    its own budget
    its own logs
    its own evidence


# =============================================================================
# 6. RESOURCE-AWARE CONCURRENCY
# =============================================================================

DO NOT hard-code worker count.

AdaptiveHardwareGovernor controls:

max heavy workers
max light workers
max browser workers
test concurrency
local model residency

On memory-constrained machine:

    heavy_concurrency = 1

Use fresh sequential subagents:

Scout
→ Implementer
→ Test Job
→ Verifier

On stronger machine:

run independent workers concurrently.

Never start a worker just because a slot count says one exists.
Memory admission must still pass.


# =============================================================================
# 7. SUBAGENT ROLES
# =============================================================================

Use few specialized roles.

CONTROLLER
    deterministic/minimal intelligence
    scheduling only

SCOUT
    cheap
    maps files/symbols/tests
    returns compact capsule

IMPLEMENTER
    builds one coherent requirement

INVESTIGATOR
    only for hard unknown/root cause

VERIFIER
    fresh context
    P0/security/high-consequence only

INTEGRATOR
    only when deterministic merge cannot resolve semantics

PACKAGER
    builds/validates executable

TEST JOB
    deterministic process
    not an LLM conversation

Do not create agent theater.


# =============================================================================
# 8. MODEL ROUTING
# =============================================================================

Use the cheapest model capable of the task.

DETERMINISTIC CODE:
    no LLM

MICRO / CHEAP MODEL:
    classification
    repo orientation
    summarizing logs
    extracting failures
    formatting artifacts
    simple triage

SONNET-CLASS MODEL:
    normal coding
    feature implementation
    refactors
    tests
    straightforward debugging
    documentation tied to code

OPUS-CLASS MODEL:
    hard architecture
    unresolved P0
    repeated root-cause failures
    subtle concurrency/state bugs
    high-consequence security review
    difficult cross-system integration

OTHER CONNECTED MODELS:
    use when benchmarks or task fit make them cheaper/faster/better.

Do not use Opus merely to:

- grep files;
- run tests;
- wait;
- summarize logs;
- update JSON;
- perform routine code edits;
- count requirements.


# =============================================================================
# 9. ADVISOR → EXECUTOR PATTERN
# =============================================================================

For very difficult work:

Opus Advisor
    ↓
compact diagnosis / architecture decision
    ↓
Sonnet Implementer
    ↓
tests
    ↓
fresh verifier if required

Do NOT pay Opus rates for the entire mechanical implementation when a cheaper
worker can implement a clear diagnosis.


# =============================================================================
# 10. FRESH CONTEXT
# =============================================================================

Every major worker starts fresh.

Worker receives only:

REQUIREMENT
WHY
RELEVANT FILES
RELEVANT SYMBOLS
DEPENDENCIES
KNOWN FAILURES
ACCEPTANCE
TESTS
CONSTRAINTS
BUDGET

Never send:

entire PRD
entire transcript
all previous repair conversations
all MCP schemas
all skills
giant test output


# =============================================================================
# 11. SCOUT CAPSULE
# =============================================================================

A Scout returns maximum-value minimum-context:

requirement
likely files
important symbols
callers/callees
relevant tests
recent relevant commits
known risks
expected write surface

Builder consumes the capsule, not Scout conversation.


# =============================================================================
# 12. TOKEN EFFICIENCY
# =============================================================================

Optimize:

verified useful work / token

Rules:

- no repeated repo orientation;
- no repeated reading of resolved findings;
- no duplicate reviewer;
- no unnecessary planning essay;
- no giant command output in context;
- no model waiting for tests;
- no whole-history retries;
- no entire PRD per task;
- no all-skills preload.

Persist state to files/artifacts.

Context is temporary working memory.


# =============================================================================
# 13. PLANNING
# =============================================================================

Simple task:

understand
→ edit
→ test
→ verify

Do NOT spend thousands of tokens writing a plan.

Complex task:

write a lean decision plan containing:

- exact files;
- signatures/interfaces;
- acceptance;
- tests;
- dependencies;
- risk.

A plan records decisions, not code transcripts.


# =============================================================================
# 14. FAILURE / RETRY
# =============================================================================

Attempt 1:
    normal worker

Attempt 2:
    same context only if previous context is clearly valuable

Attempt >=3:
    mandatory fresh session

Create Failure Capsule:

expected
observed
reproduction
files
tests
attempts
ruled-out causes
remaining hypotheses

If normal model failed repeatedly:

escalate to stronger model.

If strong model still fails:

park requirement
continue independent work.


# =============================================================================
# 15. TESTS ARE JOBS
# =============================================================================

LLM worker finishes code and exits.

TestScheduler runs independently.

Do not keep an expensive model alive while pytest runs.

During development:

targeted tests

Then:

affected tests

Then:

chunked component regression

Checkpoint/release:

full suite

Timeout:

    TEST_TIMEOUT

not:

    PROGRAM_CRASH


# =============================================================================
# 16. PARALLEL TESTING
# =============================================================================

If resources allow:

parallelize independent test chunks.

If resources do not allow:

run sequentially.

ResourceGovernor chooses.

Do not blindly use all CPU cores.


# =============================================================================
# 17. INTEGRATION
# =============================================================================

Worker finishes:

commit SHA
base SHA
requirements
changed files
tests
evidence

MergeQueue receives it.

Attempt deterministic integration first.

If clean:
    merge/cherry-pick
    run affected tests

If semantic conflict:
    spawn Integrator Agent

If integration fails:
    reset candidate safely
    park candidate
    continue product program


# =============================================================================
# 18. REVIEW POLICY
# =============================================================================

Normal requirement:

Implementer
→ tests
→ done

P0/security/high consequence:

Implementer
→ tests
→ one fresh Verifier

Do not automatically create:

spec reviewer
code reviewer
second reviewer
critic
judge

One high-quality reviewer is preferred unless evidence shows another is needed.


# =============================================================================
# 19. HARNESS SELF-UPGRADE
# =============================================================================

The harness is allowed to improve itself WHILE building FRIDAY.

But every harness upgrade requires:

MEASURED_PROBLEM
PRODUCT_IMPACT
MINIMAL_FIX
ACCEPTANCE_TEST
REAL_PRODUCT_DOGFOOD

Example:

test timeout crashes DOMAIN-001
→ fix chunked test scheduler
→ rerun DOMAIN-001
→ keep fix if improvement confirmed

No speculative harness architecture.


# =============================================================================
# 20. HARNESS A/B
# =============================================================================

Any claimed optimization:

Ponytail
RTK
Serena
codebase-memory-mcp
RepoMap
new planner
new reviewer
new compressor
new agent strategy

must beat baseline on paired real FRIDAY tasks.

Measure:

task success
tokens
cost
wall time
tool calls
orientation time
first-correct-edit time
retries

Do not trust marketing or self-reported savings.


# =============================================================================
# 21. CAPABILITY FORGE
# =============================================================================

Continue actual product capability work.

FRIDAY must eventually accept:

    GitHub repository URL

and autonomously:

inspect
classify
quarantine
security-review
choose integration pattern
wrap/adapt
test
register
generate local skill where useful
monitor version
rollback on regression

This is PRODUCT work.


# =============================================================================
# 22. WORKFLOW FABRIC
# =============================================================================

Continue workflow capability work.

Support provider abstraction such as:

Activepieces
n8n
Windmill
future workflow engines

FRIDAY chooses backend according to task.

Do not make FRIDAY Core depend on one external workflow engine.


# =============================================================================
# 23. COMPUTER USE
# =============================================================================

Continue:

structured browser
Playwright
desktop automation
Cua candidate
vision fallback
application adapters

Do not wait for optional Cua adoption to continue browser/desktop product work.


# =============================================================================
# 24. MEMORY / CONTEXT / MODEL FABRIC
# =============================================================================

Continue building:

canonical memory
trust/provenance
context compiler
context rot protections
model registry
local models
model residency
semantic probes
provider fallback
privacy routes

in parallel where dependencies permit.


# =============================================================================
# 25. PACKAGING LANE
# =============================================================================

Do not wait until the final day to test packaging.

Maintain a PACKAGING lane.

At meaningful checkpoints:

build FRIDAY.exe candidate

run:

    FRIDAY.exe --version
    FRIDAY.exe --doctor
    FRIDAY.exe --status

launch normal runtime

verify:

- bundled imports;
- MCP server startup;
- browser capability startup;
- model gateway initialization;
- state path;
- logs;
- clean shutdown;
- restart;
- upgrade/state preservation.

Packaging bugs become normal product bugs.


# =============================================================================
# 26. EXE ACCEPTANCE
# =============================================================================

EXE_READY requires:

1. executable exists;
2. launches without source checkout;
3. does not require developer Python/venv;
4. required runtime dependencies provision correctly;
5. --doctor passes or names only explicitly accepted external blockers;
6. state is outside replaceable application bundle;
7. restart preserves state;
8. golden product journeys pass from executable;
9. browser/runtime smoke test passes if locally available;
10. full candidate test/release gate passes;
11. no known P0 packaging blocker;
12. exact commit/build identity recorded.


# =============================================================================
# 27. EXE NOTIFICATION
# =============================================================================

When and ONLY when EXE_READY becomes true:

write:

    data/release/EXE_READY.json

containing:

commit
build id
artifact path
tests
release gate
doctor result
known external blockers
timestamp

Then emit a user-facing notification:

    "FRIDAY.exe is built and verified."

Include:

artifact location
commit
validation summary
known remaining non-release blockers

Do not notify earlier.


# =============================================================================
# 28. PROGRESS REPORTS
# =============================================================================

Reports stay compact.

WAVE:
PRODUCT REQUIREMENTS:
AGENTS USED:
MODELS:
COMMITS:
TESTS:
TOKENS:
COST:
WALL TIME:
PEAK RAM:
VERIFIED:
PARKED:
P0 REMAINING:
EXE STATE:

No essays unless requested.


# =============================================================================
# 29. BLOCKERS
# =============================================================================

If something needs:

user login
OTP
hardware key
approval
unavailable service
RAM
provider
external API

park only that branch.

Continue all independent work.

Never stop the product program because one branch needs the user.


# =============================================================================
# 30. LOOP
# =============================================================================

LOOP:

refresh requirement DAG

refresh machine resources

refresh budgets

find ready independent PRODUCT tasks

dispatch maximum useful safe parallelism

run scouts cheaply

run implementers on appropriate model

release worker context

run tests independently

integrate completed branches

verify high-risk work

update ledger

build EXE checkpoint when appropriate

repair measured harness problems minimally

continue

UNTIL:

all applicable requirements terminal
AND
release candidate verified
AND
EXE_READY true.


# =============================================================================
# 31. FINAL PRINCIPLE
# =============================================================================

USE MANY AGENTS ONLY WHEN THEY PRODUCE REAL PARALLELISM.

USE FRESH AGENTS TO PREVENT CONTEXT ROT.

USE OPUS FOR THE HARD PARTS.

USE SONNET-CLASS MODELS FOR MOST BUILD WORK.

USE CHEAP MODELS OR CODE FOR MECHANICAL WORK.

RUN TESTS WITHOUT MODEL TOKENS.

KEEP PRODUCT WORK MOVING.

LET THE HARNESS IMPROVE FROM REAL FAILURES.

BUILD FRIDAY AND THE HARNESS TOGETHER.

THE PRODUCT HAS PRIORITY.

DO NOT STOP UNTIL THE APPLICABLE PRD IS BUILT,
THE RELEASE CANDIDATE IS VERIFIED,
AND FRIDAY.exe IS READY.
```

### One change I particularly want

Do **not** interpret “many subagents” as “run 10 Opus processes.”

The efficient shape is more like:

```text
                    Controller
                       │
          ┌────────────┼────────────┐
          ▼            ▼            ▼
       Sonnet       Sonnet       Sonnet
       Feature A    Feature B    Feature C
          │            │            │
          ▼            ▼            ▼
       tests         tests        tests
          │            │            │
          └───────┬────┴────┬───────┘
                  ▼         ▼
              integration  Opus
                           only if
                           hard issue
```

If only one heavy worker fits the laptop:

```text
Sonnet A
→ exits

Sonnet B
→ exits

Opus hard-debug
→ exits
```

That still uses **multiple specialized subagents**, just not simultaneously enough to crash the machine.

And based on current external evidence, we should be particularly careful about excessive subagent overhead. Superpowers' own live evaluations found that cheap agents sometimes took **2–3× more turns**, repeated agent spin-ups re-derived the same context, and controller coordination itself became a major cost center; their optimized design therefore uses compact task briefs, explicit model assignment and fewer reviewers. [GitHub](https://github.com/obra/superpowers/blob/main/docs/superpowers/specs/2026-06-09-sdd-task-scoped-review-dispatch-design.md?utm_source=chatgpt.com)

So the guiding equation should be:

> **maximum verified parallel progress — not maximum agent count.**

For your current project, that means **Sonnet-class workers for most implementation, Opus-class workers for the truly hard P0/architecture/debugging jobs, deterministic test/integration jobs whenever possible, and resource-aware parallel worktrees for independent FRIDAY features**.
````

