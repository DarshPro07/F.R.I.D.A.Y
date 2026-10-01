# The owner's v10 program directive, 2026-10-01 (verbatim)

> Extracted byte-for-byte from session transcript 47979877 (one pasted message, 2026-10-01T16:41Z) by
> `extract_v10.py`; the pasted-content tags are removed and nothing else is changed. The message held only
> the pasted directive.
> Nothing below this note is the implementing agent's.

````text
# =====================================================================
# F.R.I.D.A.Y. AUTONOMOUS BUILD PROGRAM
# v10 — LEAN MULTI-AGENT / CODE-CONTROLLER EXECUTION
#
# PURPOSE:
#   Resume the existing FRIDAY PRD implementation from durable repo state,
#   finish the lean-harness upgrades, then continue the full PRD program
#   autonomously until all applicable requirements reach truthful terminal
#   states.
#
# THIS IS NOT A PLANNING-ONLY PROMPT.
# THIS IS AN EXECUTION PROGRAM.
# =====================================================================


# 0. START FROM DURABLE STATE, NOT CHAT MEMORY

Primary resume artifact:

    docs/plans/build-harness/02-lean-harness-v9.md

Expected previous state, which MUST be verified rather than assumed:

    branch:
        prd-v7/slice-7-adaptive-hardware

    expected previous HEAD:
        f12f027

    prior checkpoint branch:
        prd-v7/foundations-checkpoint

Known owner rulings:

    1. Cua waits until unresolved P0s are finished,
       unless a current P0 objectively requires it.

    2. Amendment A7 is confirmed:
       FRIDAY's own runtime should eventually use the same optimized
       worker/harness principles.

    3. No new framework/tool/skill installation merely because it is
       interesting.

    4. Technology Scout is effectively paused during P0 burn-down,
       except where a current blocker genuinely requires external research.

    5. Nothing is pushed to a remote repository without explicit owner
       authorization.

    6. New repositories/tools discovered during implementation go to the
       candidate backlog unless directly needed for an active P0/P1.

DO NOT rely on this prompt for repository truth.

VERIFY EVERYTHING ON DISK FIRST.


# =====================================================================
# 1. PRIME DIRECTIVE
# =====================================================================

Continue building FRIDAY autonomously.

Do not stop after:

    planning
    one requirement
    one slice
    one worker
    one commit
    one test pass
    one context window
    one model session
    one integration wave
    one checkpoint
    one release candidate

Continue until the full applicable PRD program has reached truthful
terminal states.

However:

    AUTONOMOUS != INFINITE

Every loop has:

    token budget
    turn budget
    time budget
    tool-call budget
    worker budget
    retry budget
    no-progress detection

If one requirement is blocked:

    persist the blocker
    continue independent work

Do not freeze the overall program.


# =====================================================================
# 2. TERMINAL STATES
# =====================================================================

A requirement may finish only as:

    VERIFIED
    BLOCKED_EXTERNAL
    DEFERRED_BY_PRD
    NOT_APPLICABLE_WITH_REASON
    REJECTED_WITH_EVIDENCE

These are NOT terminal:

    TODO
    PARTIAL
    IMPLEMENTED
    RESEARCHED
    CODE_WRITTEN
    PROBABLY_WORKS
    UNKNOWN
    IMPLEMENTED_UNVERIFIED


# =====================================================================
# 3. FIRST OPERATIONS IN THIS FRESH SESSION
# =====================================================================

Perform these in order:

1. Read:
       docs/plans/build-harness/02-lean-harness-v9.md

2. Verify:
       repo root
       branch
       HEAD
       git status
       worktrees
       untracked files
       user changes

3. Confirm that the expected state matches repository reality.

4. If the saved v9 plan is uncommitted:
       inspect it
       preserve it
       commit it first if clean and appropriate.

5. Reconstruct current:
       PRD requirement ledger
       P0 list
       dependency DAG
       recent evidence
       build-harness state

6. Do NOT reread the entire chat history.

7. Do NOT reload the complete PRD into active context.

8. Retrieve only the PRD sections needed for the current wave.


# =====================================================================
# 4. CRITICAL ARCHITECTURAL CHANGE
# =====================================================================

THE CONTROLLER ROLE MUST MOVE INTO CODE.

Do not use one large LLM session as the scheduler for the whole program.

Implement/finish a deterministic build kernel that owns:

    requirement DAG
    dependency readiness
    P0 ordering
    wave creation
    file-overlap conflict detection
    concurrency limit
    worktree lifecycle
    worker budgets
    timeout handling
    stall detection
    evidence intake
    integration state
    verifier scheduling
    checkpoint state

LLMs solve bounded engineering problems.

CODE controls the build program.


# =====================================================================
# 5. BUILD-HARNESS UPGRADE PROGRAM
# =====================================================================

Before returning to broad PRD work, complete the approved lean-harness
upgrade set described in:

    docs/plans/build-harness/02-lean-harness-v9.md

The intended decomposition is approximately:

WORKER A — KERNEL / SCHEDULER

    worktree lock
    base-SHA validation
    SHIP / SCOUT modes
    budget classes
    worker-read metrics
    wave scheduling improvements
    resource-aware concurrency

WORKER B — CODE CAPSULE / ORIENTATION

    reduce fresh-worker orientation tax

    provide compact machine-generated repository capsule containing only:

        relevant architecture
        symbols
        file ownership
        requirement context
        related tests
        recent relevant changes
        known failure patterns
        exact acceptance criteria

    measure whether capsule reduces:
        tokens
        repo search calls
        wall time

    do not add RepoMap/Serena unless an A/B benchmark later proves the
    external tool materially beats the in-house capsule.

WORKER C — DETERMINISTIC INTEGRATION

    red/green acceptance proof
    worktree result validation
    cherry-pick integration
    merge checks
    verifier scheduling
    rollback on integration failure

    reviewer policy:
        fresh verifier required for P0/security/consequential work

        ordinary work:
            deterministic acceptance + targeted tests may be sufficient

        review cost target:
            <= 25% of implementer cost unless consequence justifies more

After these workers finish:

    integrate
    run affected regression
    verify harness itself
    commit checkpoint

Then the CODE CONTROLLER should take over normal PRD execution.


# =====================================================================
# 6. MULTI-AGENT RULE
# =====================================================================

Use agents to reduce WALL CLOCK TIME.

Do not use agents merely to create more conversations.

Spawn a worker only when at least one condition is true:

    A. work is independent and can run in parallel
    B. isolated context improves performance
    C. a specialist is materially useful
    D. fresh verification is required
    E. current worker context is degraded

Do not spawn an agent because:

    "multi-agent sounds better"


# =====================================================================
# 7. DEFAULT CONCURRENCY
# =====================================================================

Concurrency must come from AdaptiveHardwareGovernor.

Do not hard-code:

    workers = 5

Reference machines include:

REFERENCE-A
    approximately 15.4 GB RAM
    integrated GPU
    low dedicated VRAM

REFERENCE-B
    approximately 16 GB RAM
    4 GB VRAM

Both must be supported.

The controller derives:

    max coding workers
    max browser workers
    max heavy-test jobs
    local-model residency
    background workload
    CPU/RAM limits

If memory pressure increases:

    stop dispatching new workers first

then:

    pause lowest-priority worker if necessary

Do not crash the host in pursuit of parallelism.


# =====================================================================
# 8. FRESH WORKERS
# =====================================================================

Each implementer should normally be a fresh headless coding worker.

Each worker receives:

    one requirement or one tightly coupled requirement set

    isolated Git worktree

    exact base SHA

    narrow work packet

    narrow Bash/tool allowlist

    its own FRIDAY state root

    no irrelevant MCP servers

    no irrelevant hooks

    bounded tokens

    bounded turns

    bounded dollars

    bounded wall time

    stall watchdog

The worker should NOT receive:

    entire conversation
    entire PRD
    all architecture docs
    every skill
    every MCP schema
    all historical failures


# =====================================================================
# 9. WORK PACKET FORMAT
# =====================================================================

Each worker gets something approximately like:

    REQUIREMENT:
        RES-003

    PURPOSE:
        Make resource pressure adaptive to machine baseline.

    BASE_SHA:
        ...

    WORKTREE:
        ...

    RELATED REQUIREMENTS:
        RES-002
        RES-004

    RELEVANT FILES:
        ...

    RELEVANT TESTS:
        ...

    CONSTRAINTS:
        ...

    CURRENT EVIDENCE:
        ...

    KNOWN FAILURES:
        ...

    ACCEPTANCE:
        ...

    BUDGET:
        ...

    REQUIRED OUTPUT:
        commit
        tests
        evidence
        concise result JSON

No essay.


# =====================================================================
# 10. WORKER RESULT FORMAT
# =====================================================================

Workers should return compact structured output:

    STATUS
    REQUIREMENT_IDS
    COMMIT_SHA
    FILES_CHANGED
    TESTS
    ACCEPTANCE_EVIDENCE
    RISKS
    BLOCKERS
    TOKEN_USAGE
    WALL_TIME

Long debugging/research material belongs in an artifact file.

Do not send huge prose reports through the controller.


# =====================================================================
# 11. ORIENTATION TAX
# =====================================================================

Fresh workers waste tokens rediscovering the repository.

The code capsule must reduce that.

Measure:

    repository reads
    grep/search calls
    tokens before first edit
    wall time before first edit
    total tokens
    successful completion

Compare:

    worker without capsule
    vs
    worker with capsule

Promote the capsule only if it improves measured results.


# =====================================================================
# 12. TOOL / SHELL OUTPUT
# =====================================================================

Do not feed full command output into the model unless required.

For:

    pytest
    git log
    grep
    tree
    build logs

prefer:

    compact structured summary
    +
    raw artifact path

RTK was already measured and was NOT adopted as default because it lost
useful information on important commands.

Keep that decision unless a later benchmark changes it.

Use the in-house failure-only pytest view and raw-log artifact instead.

Do not retry the RTK decision without new evidence.


# =====================================================================
# 13. TEST STRATEGY
# =====================================================================

Workers:

    run targeted tests

Integrator:

    run affected/component regression

Checkpoint:

    run full suite

Release:

    run full suite + release gates

Do NOT run ~4,700+ tests after every small change.

Current last known full-suite checkpoint:

    4,753 passed
    0 failed

But verify the current repository state rather than assuming this still
applies to later commits.


# =====================================================================
# 14. TEST SELECTION
# =====================================================================

Use deterministic test-impact selection where possible.

Example:

    governor.py changed
        ↓
    select relevant resource-governor tests

Do not ask an LLM to choose tests if code can derive them reliably.


# =====================================================================
# 15. RED / GREEN PROOF
# =====================================================================

For important behavior, especially P0:

    demonstrate test failure with behavior disabled/broken where feasible

then:

    demonstrate pass with implementation enabled

This proves the test can actually detect the defect.


# =====================================================================
# 16. REVIEW POLICY
# =====================================================================

Do not create review swarms.

NORMAL P1/P2:

    implementer
    +
    deterministic tests

P0 / SECURITY / HIGH CONSEQUENCE:

    implementer
    +
    tests
    +
    one fresh independent verifier

Only create additional adjudicator/reviewer if:

    verifier disagrees
    architecture is highly consequential
    evidence is ambiguous

One good independent review beats five redundant agents.


# =====================================================================
# 17. MODEL CASCADE
# =====================================================================

Use least expensive capable model first.

Conceptually:

    simple deterministic
        ↓
    local/System-One
        ↓
    normal coding model
        ↓
    stronger coding model
        ↓
    frontier advisor

Do not start every task with maximum frontier reasoning.

Use frontier/Opus-class reasoning primarily for:

    architecture
    difficult P0
    repeated root-cause failure
    complex concurrency
    security-critical adjudication


# =====================================================================
# 18. NO-PROGRESS RULE
# =====================================================================

If one worker hits the same effective failure 3 times:

    root-cause reset

If two materially different root-cause strategies fail:

    escalate model/strategy

If still blocked:

    persist exact blocker
    return control
    continue another dependency-ready requirement

Never burn 100k tokens on one stubborn symptom.


# =====================================================================
# 19. TOKEN ACCOUNTING
# =====================================================================

Attribute tokens to:

    controller
    implementation
    orientation
    tests/log interpretation
    review
    research
    integration

Track for each worker:

    input tokens
    output tokens
    turns
    shell calls
    wall time
    dollar cost
    requirement result

Compute:

    AgentROI =
        verified useful work /
        (tokens + cost + wall time + retries)

The harness should learn which worker/model strategy performs best for
which requirement classes.


# =====================================================================
# 20. CONTROLLER TOKEN RULE
# =====================================================================

The controller should remain SHORT.

The controller should not:

    review full diffs unless required
    reread whole source tree
    read all test logs
    inspect all worker thoughts
    perform routine coding

It should consume:

    requirement metadata
    worker status
    commit SHA
    test summaries
    evidence
    blockers

The controller's job is scheduling and state transition.


# =====================================================================
# 21. CONTEXT POLICY
# =====================================================================

Context is finite.

Use just-in-time retrieval.

Persist large information outside model context.

If a worker context becomes noisy:

    checkpoint
    write handoff
    terminate
    spawn fresh worker

A context reset is NORMAL.

It is not failure.


# =====================================================================
# 22. SECURITY / AUTHORITY
# =====================================================================

Preserve existing security findings and fixes.

Previously fixed examples include:

    approval from objective A must not authorize objective B

    child envelope must not disable parent JIT checks

    failed-task tool output must not become trusted worker facts

Do not regress these.

Worker sandboxes / worktrees do not grant higher authority.


# =====================================================================
# 23. AUDIT ISOLATION
# =====================================================================

Automated tests must not write into live user audit databases.

Keep test state isolated.

Historical contamination must be documented, not silently deleted.


# =====================================================================
# 24. CURRENT PRODUCT PRIORITY
# =====================================================================

Burn down unresolved P0 before new product expansion.

Do not currently stop for:

    Cua
    new agent frameworks
    interesting GitHub repos
    new small models
    technology scouting

unless an active P0 proves that such a tool is required.

New discoveries go into:

    candidate backlog

not the active implementation path.


# =====================================================================
# 25. CUA RULING
# =====================================================================

Cua remains:

    CANDIDATE

Do not implement the Cua spike yet.

Exception:

    if a current P0 genuinely cannot be solved without the capability,
    document that dependency and evaluate it narrowly.


# =====================================================================
# 26. A7 — SAME HARNESS PRINCIPLES FOR FRIDAY RUNTIME
# =====================================================================

A7 is confirmed.

After the build harness itself proves effective, FRIDAY's own runtime
worker system should adopt the same principles where appropriate:

    sparse workers
    fresh sessions
    bounded budgets
    deterministic orchestration
    artifact handoffs
    resource-aware concurrency
    targeted context
    no-progress detection
    evidence-based integration

But do not let A7 distract from current P0 closure before its
dependency point is reached.


# =====================================================================
# 27. TECHNOLOGY SCOUT
# =====================================================================

During P0 burn-down:

    SCOUT MODE = OFF

except:

    implementation cannot proceed because required technical knowledge
    is genuinely missing.

If search discovers an interesting repository:

    add to candidate backlog

Do not install it automatically.


# =====================================================================
# 28. SKILL DISCOVERY
# =====================================================================

The previous /find-skills search failed because the shell safety
classifier produced transient failures.

Do not treat that as an unresolved product blocker.

The orientation problem currently has an in-house solution under test:

    code capsule

Only evaluate:

    RepoMap
    Serena
    other repo-map/code-context systems

later if the in-house capsule fails its benchmark.


# =====================================================================
# 29. SAFETY CLASSIFIER / TOOL INFRASTRUCTURE FAILURES
# =====================================================================

If command/tool safety infrastructure fails transiently:

    one bounded retry

If it fails again:

    persist state
    stop retrying that route
    use fresh session / compatible route later

Do not spend an entire context window retrying shell commands.


# =====================================================================
# 30. BUILD WAVES
# =====================================================================

After harness upgrades:

    READY_SET =
        unresolved requirements
        whose dependencies are terminal/verified
        and whose work does not conflict

Create wave based on:

    priority
    file overlap
    resource availability
    risk
    estimated cost

Example:

    Wave N

    Worker 1:
        requirement A

    Worker 2:
        requirement B

    Worker 3:
        requirement C

Only if A/B/C are truly independent.


# =====================================================================
# 31. INTEGRATION
# =====================================================================

For every successful worker:

    verify base SHA
    verify worker commit
    verify tests/evidence
    cherry-pick or equivalent
    detect conflict
    run affected tests
    update ledger

Do not merge output simply because worker says PASS.


# =====================================================================
# 32. CONFLICT HANDLING
# =====================================================================

If two workers touched overlapping architecture:

    stop blind auto-merge

Create bounded integration task with:

    both requirement contracts
    both diffs
    affected tests

Resolve explicitly.


# =====================================================================
# 33. CHECKPOINTING
# =====================================================================

At coherent milestones:

    clean working tree
    requirement ledger updated
    evidence persisted
    full suite when checkpoint policy requires
    create commit

Do not allow large piles of uncommitted work.


# =====================================================================
# 34. PUSH POLICY
# =====================================================================

Do not push.

Remote push requires owner instruction.

Local commits/branches/worktrees are allowed according to project rules.


# =====================================================================
# 35. FULL PRD CONTINUATION
# =====================================================================

When the ten harness upgrades are verified:

DO NOT STOP.

Transition automatically to:

    FULL PRD PROGRAM

Use the new code controller to continue requirement waves.

Loop:

    read ledger
    calculate READY_SET
    schedule bounded wave
    execute workers
    integrate
    verify
    update ledger
    checkpoint
    repeat


# =====================================================================
# 36. RESEARCH POLICY
# =====================================================================

Research only when it answers a concrete implementation uncertainty.

Before searching:

    check existing research artifacts

If fresh evidence exists:

    reuse it

Do not browse for novelty during active P0 execution.


# =====================================================================
# 37. PRODUCT EXPANSION AFTER P0
# =====================================================================

Once:

    unresolved P0 = 0

then evaluate candidate backlog in a separate SCOUT wave.

Possible candidates already discussed include:

    Cua
    HyperResearch
    SkillSpector
    SkillEvaluator
    Laya
    Jev
    MiniCPM
    Caveman
    Claude-Red
    A2A
    WebMCP
    AgentGateway
    external repo-map/context tools
    ECC patterns
    Awesome Claude Code candidates

No candidate is adopted merely because it is interesting.

Each must beat current architecture on a measured need.


# =====================================================================
# 38. MEMORY / LEARNING
# =====================================================================

After verified work:

    important durable project truth
        → Project Brain

    reusable operating procedure
        → skill candidate

    recurring defect
        → Failure Genome

    benchmark outcome
        → routing/evaluation dataset

Do not dump raw worker conversations into permanent memory.


# =====================================================================
# 39. COMPLETION CONDITIONS
# =====================================================================

The full autonomous program may stop only when:

    every applicable PRD requirement is terminal

and:

    unresolved P0 = 0
    unresolved release-blocking P1 = 0

and:

    final deterministic suite run
    integration suite
    adversarial/security gate
    browser/computer-use gates where applicable
    install gate
    objective recovery gate
    context/eval gates
    CI status known
    soak status known

and:

    final evidence report exists

and:

    no unexplained critical defect remains.


# =====================================================================
# 40. IF CONTEXT WINDOW ENDS
# =====================================================================

Do NOT interpret context exhaustion as program completion.

Before ending a session:

    persist controller state
    persist ledger
    persist active wave
    persist worker results
    persist exact HEAD
    persist next action

Then the next fresh session resumes automatically from those artifacts.


# =====================================================================
# 41. FINAL PROGRAM OUTPUT
# =====================================================================

Only after the entire applicable PRD program is terminal print:

FRIDAY_PRD_PROGRAM_COMPLETE

PRD_STATUS=<PASS|BLOCKED|PARTIAL|FAIL>
HEAD=<sha>
TOTAL_REQUIREMENTS=<n>
VERIFIED=<n>
BLOCKED_EXTERNAL=<n>
DEFERRED=<n>
NOT_APPLICABLE=<n>
UNRESOLVED_P0=<n>
UNRESOLVED_P1=<n>

BUILD_HARNESS=<PASS|FAIL>
FULL_TEST_SUITE=<PASS|FAIL|BLOCKED>
SECURITY_GATE=<PASS|FAIL|BLOCKED>
BROWSER_GATE=<PASS|FAIL|BLOCKED>
INSTALL_GATE=<PASS|FAIL|BLOCKED>
CI=<PASS|FAIL|BLOCKED|NOT_RUN>
SOAK=<PASS|FAIL|INCONCLUSIVE|NOT_RUN>

FINAL_REPORT=<path>
VERDICT=<path>

Then stop all temporary workers and exit.


# =====================================================================
# 42. BEGIN
# =====================================================================

Resume from:

    docs/plans/build-harness/02-lean-harness-v9.md

Verify current branch/HEAD.

Commit the saved plan if still pending.

Complete the approved harness upgrade workers.

Integrate and verify them.

Hand control to the deterministic build kernel.

Then continue the PRD program autonomously.

Do not restart completed work.

Do not repeatedly reread old conversation context.

Do not stop after the harness is built.

Do not stop after the next wave.

Continue until the full applicable FRIDAY PRD is truthfully terminal.
````
