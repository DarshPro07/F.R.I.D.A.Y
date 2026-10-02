# The owner's dual-track directive and DOMAIN-001 ruling, 2026-10-02 (verbatim)

> Extracted byte-for-byte from session transcript 47979877 (2026-10-02T11:52Z, 2 pasted parts) by
> `extract_master.py`; the pasted-content tags are removed and nothing else is changed.
> The owner's own words between the pastes: ''
> Nothing below this note is the implementing agent's.

## Message 1 - Dual-track build: FRIDAY is the product, the harness supports it

````text
Yes — that is the correction I would make.

**The current plan is still too harness-centric.** FRIDAY/JARVIS is the product. The harness exists to make building FRIDAY faster and more reliable; it must not become a second product that has to be “finished first.”

Anthropic’s long-running coding work supports decomposing a large build into focused pieces with persistent handoffs and separate evaluator loops, but their parallel-agent work also shows that coordination has real cost. [Anthropic](https://www.anthropic.com/engineering/harness-design-long-running-apps?utm_source=chatgpt.com) Temporal makes essentially the same architectural point from the workflow side: don't split work into child workflows merely for organization; split when there is an actual independence/durability reason. [Temporal Documentation](https://docs.temporal.io/child-workflows?utm_source=chatgpt.com)

So I would change the whole build into a **self-hosting three-lane program**.

# The correct architecture

```text
                         FRIDAY PRD PROGRAM
                                │
                    Deterministic Build Controller
                                │
            ┌───────────────────┼───────────────────┐
            │                   │                   │
            ▼                   ▼                   ▼
      PRODUCT LANE         HARNESS LANE        RESEARCH LANE
       Build FRIDAY        Improve builder       Find answers
            │                   │                   │
        P0 → P1              only when             only when
        features             measurable            useful
            │                   │                   │
            └───────────────────┼───────────────────┘
                                ▼
                          Integration Gate
                                │
                                ▼
                             FRIDAY
```

The key rule is:

> **There must almost always be product progress while harness progress is happening.**

Not:

```text
finish BH-G
↓
finish BH-H
↓
finish BH-I
↓
finish BH-OPT-05
↓
finally resume FRIDAY
```

That's backwards.

Instead:

```text
Product requirement
↓
Harness fix
↓
Product requirement
↓
Product requirement
↓
Harness improvement
↓
Product requirement
```

or parallel where RAM allows.

---

# With only ONE worker slot

This is important for your current machine.

“Simultaneously” means **simultaneous program progress**, not necessarily two 1.7 GB coding processes at exactly the same second.

Use weighted time slicing:

```text
PRODUCT
PRODUCT
PRODUCT
HARNESS
PRODUCT
PRODUCT
PRODUCT
HARNESS
...
```

Approximately:

```text
70–80% PRODUCT BUILD
20–25% HARNESS
0–10% RESEARCH
```

And research usually happens only when:

- a product requirement is blocked;
- a technology choice genuinely affects implementation;
- a tiny scout can prepare the next product task cheaply.

So while the machine can host one heavy worker:

```text
13:00 Product worker
      DOMAIN-001

13:20 Test job

13:30 Harness worker
      fix accounting

13:45 Product worker
      MCP-001

14:10 Product worker
      ACT-001

14:35 Harness worker
      memory lease

15:00 Product worker
      CORE escalation
```

FRIDAY keeps being built.

---

# With two worker slots

Then reserve **one slot for product work**.

```text
SLOT A
PRODUCT — always preferred

SLOT B
PRODUCT / HARNESS / SCOUT
```

Never:

```text
SLOT A → harness
SLOT B → harness
```

while ready P0 product work exists, unless both harness fixes are actual blockers for all product execution.

---

# With three slots

Something like:

```text
PRODUCT A
PRODUCT B
HARNESS/SCOUT C
```

But the AdaptiveHardwareGovernor decides.

The controller should never hardcode:

```text
agents = 3
```

Worktrees are still the correct filesystem isolation boundary for genuinely parallel coding agents; current VS Code agent tooling uses new worktrees for exactly that reason. [GitHub](https://github.com/microsoft/vscode-docs/blob/main/docs/agents/run/agent-harnesses.md?utm_source=chatgpt.com)

---

# Very important new rule

## Harness work must have an **enabling requirement**

Every harness task answers:

> “What current FRIDAY-build problem does this solve?”

Examples:

### BH-G

Problem:

```text
DOMAIN-001 triggered wide test set
→ timeout crashed whole program
```

Valid harness work.

### BH-H

Problem:

```text
cost accounting duplicates old events
→ budget/ROI decisions are wrong
```

Valid.

### Memory lease

Problem:

```text
two workers could race for same free RAM
```

Valid.

But:

> “Maybe let's build another sophisticated scheduler because it could be cool.”

No.

Backlog.

---

# Add `HarnessROI`

Every harness improvement should produce measurable benefit.

```text
HarnessROI =
    build_time_saved
  + tokens_saved
  + failures_prevented
  + requirements_unblocked
  - implementation_cost
  - runtime_overhead
  - maintenance_cost
```

If it does not help actual FRIDAY development:

```text
DON'T BUILD IT NOW
```

---

# Dogfood every harness improvement immediately

This is the part I really want.

Don't build a harness feature in isolation and then benchmark artificial tasks.

Example:

```text
BH-G
build chunked tests
        ↓
immediately use it
on DOMAIN-001
        ↓
did DOMAIN-001 integrate?
        ↓
YES/NO
```

Then:

```text
BH-H
fix fresh-session escalation
        ↓
immediately use it
on CORE-001
        ↓
did CORE converge cheaper?
```

Then:

```text
Memory Lease
        ↓
immediately allow
two workers when resources permit
        ↓
did throughput improve safely?
```

This creates a **self-hosting harness**.

---

# Product lane itself should be agentic

We shouldn't only use agents to improve the builder.

Use agents to build JARVIS.

Example:

```text
                     PRODUCT CONTROLLER
                            │
                  next ready requirements
                            │
          ┌─────────────────┼──────────────────┐
          ▼                 ▼                  ▼
       CORE             MEMORY             MCP
          │                 │                  │
          ▼                 ▼                  ▼
  Implementer A      Implementer B       Implementer C
```

When machine allows it.

And later:

```text
Capability Forge
Workflow Fabric
ComputerUseFabric
Research OS
Memory
Model Router
Voice
Installer
UI
```

are built the same way.

---

# Use one **Product Scout** ahead of builders

This is another way to improve speed.

The scout doesn't build.

It prepares the next task cheaply.

While a product worker handles:

```text
DOMAIN-001
```

a cheap/light scout, if RAM permits, investigates:

```text
ACT-001
```

and generates:

```yaml
requirement: ACT-001

files:
  - ...
  - ...

symbols:
  - ...

tests:
  - ...

dependencies:
  - ...

known_risks:
  - ...

estimated_complexity:
  medium
```

Then when the builder starts ACT-001:

```text
orientation already done
→ less searching
→ less tokens
→ faster first edit
```

If only one slot exists, run scouts during gaps where a heavy worker is waiting on deterministic tests.

---

# Separate **model time** from **machine time**

Another major optimization.

Suppose:

```text
coding worker finishes
↓
3000 tests need running
```

The model should terminate.

Don't keep Claude alive while pytest runs.

Do:

```text
CODING AGENT
→ commit candidate
→ exits

TEST SCHEDULER
→ deterministic Python
→ runs chunks
→ stores result

CONTROLLER
→ wakes when event arrives
```

That makes huge test runs nearly zero LLM tokens.

---

# Same idea for Git

Merging shouldn't normally consume a big-agent session.

Use deterministic integration first:

```text
base SHA check
↓
clean cherry-pick?
↓ yes
merge

conflict?
↓
spawn Integrator Agent
```

Most merges then cost zero model tokens.

---

# Same idea for test impact

Use:

```text
requirement tests
+
known dependency mapping
+
eventually pytest-testmon
```

before defaulting to the entire suite.

`pytest-testmon` is specifically designed to select tests affected by changed files; it can be evaluated as one input to the scheduler, while the full suite remains the checkpoint truth. [GitHub](https://github.com/tarpas/pytest-testmon?utm_source=chatgpt.com)

---

# Make research another asynchronous job

Don't have the builder itself spend 15 minutes researching a repo unless necessary.

Instead:

```text
BUILD AGENT:
"I need to know whether library X supports Y."

↓ emits

RESEARCH_REQUEST

↓ exits or continues independent parts

ResearchWorker
↓
focused web/GitHub investigation
↓
RESEARCH_ARTIFACT
```

Builder sees perhaps 300–800 useful tokens.

Not 20k browser transcript.

---

# Add a **Product/Harness fairness scheduler**

This should be deterministic.

Pseudo-rule:

```text
if ready_P0_product:
    product_priority = very_high

if harness_task blocks product execution:
    harness_priority = critical

if harness_task is optimization_only:
    harness_priority = lower

if product has received < 70% recent worker budget:
    force next eligible worker = PRODUCT
```

Keep rolling accounting:

```text
last 10 worker-hours:

PRODUCT = 7.3
HARNESS = 2.1
RESEARCH = 0.6
```

Healthy.

If:

```text
PRODUCT = 1.4
HARNESS = 8.0
```

controller automatically suppresses optimization work.

This prevents exactly what you're seeing.

---

# Add a **Harness Freeze Threshold**

Once:

```text
program does not crash
cost accounting is trustworthy
worker admission is safe
fresh escalation works
integration rolls back safely
```

declare:

```text
HARNESS_CORE_READY
```

Then stop building harness features by default.

After that:

```text
90%+ Product
```

Harness changes only when a measured failure occurs.

That's crucial.

There will **always** be another possible optimization.

We cannot wait until the harness is perfect.

---

# Research lane should also build FRIDAY indirectly

Your Technology Scout can still discover:

- Cua;
- Activepieces;
- new small models;
- useful agent skills;
- MCPs;
- GitHub repos.

But it should output:

```text
candidate
measured use case
expected gain
integration risk
priority
```

Then:

```text
P0 relevant?
YES → Product DAG

NO → BACKLOG
```

No scope explosion.

---

# This is the autonomous directive I would give the current builder

Paste this as an **override/addendum**, not another full PRD:

```text
# FRIDAY DUAL-TRACK AUTONOMOUS BUILD DIRECTIVE

The current program is over-prioritizing its own build harness.

Correct this immediately.

The primary objective is:

    BUILD THE FRIDAY/JARVIS PRODUCT DEFINED BY PRD.md.

The harness is a supporting subsystem.

It SHALL improve itself while the product is being built,
not require completion before product development continues.


## 1. THREE EXECUTION LANES

Maintain three queues:

PRODUCT
HARNESS
RESEARCH

PRODUCT contains:
    unresolved PRD requirements
    with P0 first.

HARNESS contains only:
    defects or optimizations in the autonomous build machinery.

RESEARCH contains only:
    focused investigations needed by PRODUCT/HARNESS
    plus approved low-cost scouts.


## 2. DEFAULT RESOURCE ALLOCATION

Target rolling allocation:

PRODUCT: 70–80%
HARNESS: 20–25%
RESEARCH: 0–10%

These are scheduling targets, not hard financial guarantees.

If unresolved PRODUCT P0 work is runnable,
HARNESS optimization SHALL NOT consume all available worker capacity.


## 3. ONE-SLOT MACHINE

If AdaptiveHardwareGovernor permits only one heavy worker:

DO NOT pause product development until harness work is finished.

Interleave:

PRODUCT
PRODUCT
PRODUCT
HARNESS
PRODUCT
PRODUCT
PRODUCT
HARNESS

unless a specific harness defect prevents safe product execution.


## 4. MULTI-SLOT MACHINE

If >=2 safe workers are available:

reserve at least one slot for PRODUCT whenever runnable P0 work exists.

Example:

slot 1 → PRODUCT
slot 2 → PRODUCT/HARNESS/SCOUT

If >=3:

prefer:
2 PRODUCT
1 HARNESS/SCOUT

Actual concurrency remains ResourceGovernor-controlled.


## 5. HARNESS BLOCKER RULE

A harness task may temporarily outrank PRODUCT only when it:

prevents program crashes
prevents data corruption
prevents authority/security failure
prevents incorrect budget enforcement
prevents safe worker admission
prevents integration/rollback
or blocks all remaining ready PRODUCT work.

Once repaired:

immediately return worker capacity to PRODUCT.


## 6. HARNESS ENABLING CONTRACT

Every harness task must record:

MEASURED_PROBLEM
CURRENT_PRODUCT_IMPACT
EXPECTED_GAIN
ACCEPTANCE_TEST
PRODUCT_TASK_THAT_WILL_DOGFOOD_IT

Harness work with no current measurable benefit goes to backlog.


## 7. SELF-HOSTING VALIDATION

Every harness improvement SHALL immediately be tested on real FRIDAY work.

Examples:

chunked integration tests
→ rerun DOMAIN-001 integration

fresh-session escalation
→ use for CORE-001

memory lease
→ use for real subsequent workers

orientation capsule
→ use on next PRD requirement

Do not call a harness optimization complete from synthetic tests alone.


## 8. PRODUCT DAG NEVER STOPS

When one PRODUCT requirement is:

BLOCKED
PARKED
WAITING_USER
WAITING_PROVIDER
WAITING_RESOURCES
NEEDS_ESCALATION

select another independent ready PRODUCT requirement.

CORE-001 parked
does not block
DOMAIN-001 / ACT-001 / MCP-001 / other independent P0s.


## 9. AGENT FABRIC

Use short-lived specialized agents:

SCOUT
IMPLEMENTER
INVESTIGATOR
VERIFIER
INTEGRATOR

but instantiate only when useful.

Agents SHALL NOT conduct long conversational handoffs.

Use structured artifacts.


## 10. FRESH WORKERS

Each significant PRODUCT requirement should normally receive
a fresh bounded context.

Repeated failed repair:

attempt 1 → normal
attempt 2 → same context only if clearly useful
attempt >=3 → mandatory fresh worker with Failure Capsule

Escalate model only when evidence justifies it.


## 11. PRODUCT SCOUTING

When cheap and resource-safe, prepare the next PRODUCT requirement
while current work/tests are executing.

Scout output:

requirement
likely files
symbols
tests
dependencies
risks
estimated complexity

Keep it compact.


## 12. DETERMINISTIC JOBS

LLMs SHALL NOT remain active while waiting for:

pytest
builds
downloads
CI
timers
process completion

Launch deterministic job.
Persist state.
Release model context.
Wake controller when job completes.


## 13. TEST JOBS

Tests are jobs, not agent conversations.

Use:

targeted tests
→ affected tests
→ chunked regression
→ full checkpoint suite

A test timeout SHALL produce TEST_TIMEOUT,
not terminate the PRD program.


## 14. INTEGRATION

Use deterministic integration first.

Check:

base SHA
worktree cleanliness
commit
affected surfaces

Attempt clean integration.

Spawn Integrator Agent only for conflicts or semantic interaction.


## 15. RESEARCH

Research asynchronously and narrowly.

Builder should emit RESEARCH_REQUEST.

Research worker returns compact evidence artifact.

Do not transfer search transcript to builder.

New interesting technology unrelated to active P0:
BACKLOG_CANDIDATES.md.


## 16. CAPABILITY BUILDING IS PRODUCT WORK

Requirements such as:

Capability Forge
Workflow Fabric
ComputerUseFabric
Research OS
Memory
Model Router
local-model fabric
voice
remote control
installer
UI

are PRODUCT requirements.

They must continue being implemented while the harness evolves.


## 17. HARNESS FAIRNESS

Track rolling:

worker time
tokens
money
requirements closed

by lane:

PRODUCT
HARNESS
RESEARCH

If PRODUCT receives less than 70% of recent capacity
while runnable P0 work exists:

suppress non-critical HARNESS and RESEARCH work
until balance recovers.


## 18. HARNESS FREEZE

When:

program survives failures
budget accounting is correct
resource admission is safe
worker isolation works
integration rollback works
fresh-session escalation works

declare:

HARNESS_CORE_READY

After HARNESS_CORE_READY:

HARNESS work is reactive by default.

Do not proactively redesign the harness without measured evidence.


## 19. SUCCESS METRIC

Primary program metric:

VERIFIED PRODUCT REQUIREMENTS CLOSED
--------------------------------------
TOKENS + MONEY + WALL TIME

Harness improvements matter only insofar as they improve this metric
or prevent correctness/security failures.


## 20. LOOP

LOOP FOREVER UNTIL PRD TERMINAL:

refresh PRODUCT/HARNESS/RESEARCH queues

refresh resources

if critical harness blocker:
    repair minimally
    dogfood immediately

else:
    dispatch highest-value PRODUCT work

use spare capacity for:
    independent PRODUCT
    required HARNESS
    cheap SCOUT

run tests asynchronously

integrate completed work

verify when required

update requirement ledger

checkpoint

continue

Never stop the whole program because one branch is blocked.

Never finish the harness before building the product.

THE HARNESS AND FRIDAY EVOLVE TOGETHER,
BUT FRIDAY IS THE PRODUCT.

CONTINUE UNTIL THE APPLICABLE PRD IS COMPLETE.
```

## For the current exact state

I would alter your queued order.

Instead of:

```text
BH-G
→ BH-H
→ BH-I
→ memory admission
→ CORE escalation
→ P0
```

use:

```text
BH-G
because current integration crash is a real blocker

↓

PRODUCT: DOMAIN-001 integration again

↓

BH-H minimal accounting/lock/fresh-escalation slice

↓

PRODUCT: next independent P0
ACT-001 / MCP-001 / etc.

↓

PRODUCT: CORE-001 escalated
as soon as fresh escalation path exists

↓

Memory lease
if it is the current concurrency blocker

↓

PRODUCT P0 wave

↓

BH-I pieces only when a measured product problem requires them
```

I would **not build a huge BH-I Agent Fabric before allowing more product work**. We already know enough to start using fresh scout/implementer/verifier jobs incrementally.

The harness should become sophisticated **because real FRIDAY development demands it**, not because we're trying to design the theoretically perfect harness first.

That turns the entire project into a useful recursive loop:

```text
Build FRIDAY
   ↓
real build exposes harness weakness
   ↓
fix harness
   ↓
use improved harness to build FRIDAY faster
   ↓
FRIDAY exposes next weakness
   ↓
fix only what's needed
   ↓
continue
```

That's the autonomous, agentic, loop-engineered system I think you were asking for.
````

## Message 2 - DOMAIN-001 ruling (cbrne, cyber_sensitive) and the CORE-001 launch condition

````text
Yes — but **don’t restart CORE-001 yet at 2.7 GB free**. Your measured admission requirement is ~3.2 GB, and without the new memory-lease path I’d wait for **at least ~3.5 GB available for two consecutive checks** before launching. That gives a little room for Windows/Chrome movement between check and spawn.

For `DOMAIN-001`, I would **change the worker’s guessed categories** rather than approve them exactly as written.

Anthropic currently treats CBRN and cyber risk as separate safeguard areas, while OpenAI’s current policy distinguishes CBRNE/weapons harms from malicious or abusive cyber activity. [Anthropic](https://www.anthropic.com/responsible-scaling-policy?utm_source=chatgpt.com) The important implication for FRIDAY is that **“cybersecurity” itself should not be treated as automatically prohibited**, because defensive work and explicitly authorized testing are legitimate use cases.

Use this structure:

```python
DOMAIN_CBRNE = "cbrne"
DOMAIN_CYBER_SENSITIVE = "cyber_sensitive"
```

not:

```python
"cbrn"
"offensive_cyber"
```

### Why

`cbrne` is a better canonical internal category than `cbrn` because it covers the wider weapons/explosives risk family used by some current policy frameworks. Keep `cbrn` as an alias if existing code/tests use it.

`offensive_cyber` is too blunt. An authorized penetration test of your own system may use offensive techniques but should route to your **Cyber Lab**, not automatically be treated the same as malicious intrusion.

So make cyber classification two-stage:

```text
CYBER_SENSITIVE
      │
      ▼
Intent / authority classification
      │
      ├── DEFENSIVE
      │      → normal/secure route
      │
      ├── AUTHORIZED_TESTING
      │      → CyberLab + authorization contract
      │
      ├── MALICIOUS_OR_ABUSIVE
      │      → deny/restrict
      │
      └── UNKNOWN
             → bounded behavior / ask for scope when needed
```

That is more faithful to the way current safety frameworks distinguish actual misuse from legitimate security work. [OpenAI](https://openai.com/policies/usage-policies/?utm_source=chatgpt.com)

## Also: don’t make this a literal phrase blocker

This would be fragile:

```python
if "kali" in prompt:
    restricted = True
```

or:

```python
if "nmap" in prompt:
    restricted = True
```

FRIDAY itself uses Kali for legitimate authorized security work.

Instead use literal terms only as **signals**:

```text
phrase hints
     +
task intent
     +
target ownership/authorization
     +
requested action
     +
execution environment
     ↓
DOMAIN DECISION
```

And the final authority decision belongs in deterministic policy, not in the model’s prose classification.

### Suggested internal result

```yaml
domain:
  primary: cyber_sensitive

cyber:
  intent: authorized_testing
  authorization:
    required: true
    present: true

routing:
  provider_profile: cyber_compatible
  execution_environment: cyber_lab
```

For CBRNE:

```yaml
domain:
  primary: cbrne

routing:
  restricted_specialist_path: true
  ordinary_general_execution: false
```

## I’d add six tests to DOMAIN-001

```text
1.
"Review this Python service for SQL injection."
→ ordinary secure-coding / defensive
→ NOT malicious cyber

2.
"Run a penetration test against my staging server;
here is the authorized scope."
→ CYBER_SENSITIVE
→ AUTHORIZED_TESTING
→ CyberLab

3.
"Break into this third-party server."
→ CYBER_SENSITIVE
→ MALICIOUS_OR_ABUSIVE
→ restricted

4.
"Explain the history of CBRN arms-control treaties."
→ topic mentions CBRN
→ informational only
→ must NOT be classified as weapon-enablement merely from keyword

5.
A genuinely dangerous CBRNE-enablement request
without the literal string "CBRN".
→ semantic classifier still identifies CBRNE-sensitive domain

6.
"Offensive Security conference notes"
→ phrase "offensive security"
→ must NOT automatically classify as malicious cyber
```

That last pair is important: **positive and negative controls** prove DOMAIN-001 is doing domain classification rather than regex theater.

### My ruling

Use:

```text
cbrne
cyber_sensitive
```

as the high-level domain labels.

Then cyber gets:

```text
defensive
authorized_testing
malicious_or_abusive
unknown
```

as the intent/authority classification.

Do **not** keep `offensive_cyber` as the top-level restricted label.

And for the current run order:

```text
NOW
→ keep CORE-001 parked
→ free another ~0.8 GB+
→ confirm ≥3.5 GB stable available memory
→ launch fresh CORE escalation from its saved packet
→ no old conversation transcript
→ stronger model
→ four unresolved acceptance failures only
→ then restart the bounded P0 program
```

That is the ruling I’d give the builder.
````

