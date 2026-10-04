# Owner directive 2026-10-04 (b): MiniAGI - extract the pattern, not the runtime

Pasted by the owner, extracted verbatim from the session transcript. The owner proposed the ids COG-002 and COG-003; those ids already name other requirements (reasoning effort, advisor/executor route), so the two requirements are recorded as COG-005 (lightweight autonomous loop) and COG-006 (progress critic).

---

Yes — **MiniAGI has a few ideas worth taking, but I would not install it as another agent framework inside FRIDAY.**

The repo is old and deliberately minimal: it was last actively updated around 2023, targets GPT-3.5/GPT-4, and its core loop is only a few hundred lines. It gives an autonomous model a tiny command set (`execute_python`, `execute_shell`, web search, ingest/process data, talk to user, done), summarized short-term memory, optional self-criticism, and a strict one-action-per-turn loop. [GitHub](https://github.com/muellerberndt/mini-agi/blob/main/README.md?utm_source=chatgpt.com)

For FRIDAY, I would classify it:

```yaml
repository: muellerberndt/mini-agi

decision: EXTRACT_PATTERN

priority: MEDIUM

do_not_install_as:
  - main agent runtime
  - Objective OS
  - memory system
  - shell executor
  - security layer
```

### What is genuinely useful

The strongest idea is its **minimal autonomous loop**:

```text
Objective
   ↓
think
   ↓
ONE action
   ↓
observe
   ↓
update memory
   ↓
repeat
   ↓
done
```

MiniAGI deliberately asks for exactly one command/action at a time instead of generating giant multi-step action batches. [GitHub](https://github.com/muellerberndt/mini-agi/blob/main/miniagi.py?utm_source=chatgpt.com)

That would be useful as a new FRIDAY execution mode:

## `MiniLoop`

For simple tasks such as:

> “rename these files”

> “find the build error”

> “convert this CSV”

> “check whether this URL works”

we don't need:

```text
planner
→ 3 agents
→ Objective DAG
→ reviewer
→ orchestration
```

We could do:

```text
FRIDAY
  ↓
task complexity classifier
  ↓
SIMPLE
  ↓
MiniLoop
  ↓
action → observe → action → observe
```

That could save a lot of tokens.

---

## Its memory idea is useful, but needs upgrading

MiniAGI keeps:

```text
summary of history
+
recent actions
+
critic feedback
```

and summarizes observations when they become too large. [GitHub](https://github.com/muellerberndt/mini-agi/blob/main/miniagi.py?utm_source=chatgpt.com)

That directly relates to the context-rot/lost-in-the-middle problem we've been working on.

But I would **not copy its recursive summarization literally**.

Repeated summarization can gradually lose exact facts.

FRIDAY should use:

```text
CriticalFactSet          NEVER silently compressed away

RecentActions            bounded

ObjectiveState           structured

Failures/Blockers        structured

HistoricalSummary        compressible
```

So use MiniAGI's principle:

> compress old operational history

but combine it with our:

```text
CTX-003
CriticalFactSet
Context Compiler
provenance
```

architecture.

---

## The critic is interesting too

MiniAGI has an optional critic that examines whether the agent is actually making progress, repeating itself, trying impossible actions, or talking instead of acting. [GitHub](https://github.com/muellerberndt/mini-agi/blob/main/README.md?utm_source=chatgpt.com)

That's useful, but don't call the critic every turn.

The repo itself warns that enabling it adds API requests. [GitHub](https://github.com/muellerberndt/mini-agi/blob/main/README.md?utm_source=chatgpt.com)

FRIDAY should use:

```text
ProgressCritic
```

only when:

```text
same failure repeated

objective hasn't advanced

worker keeps reasoning without action

same command repeats

action/result mismatch

token burn exceeds progress
```

Then:

```text
normal loop
normal loop
normal loop
     ↓
progress detector says STALLED
     ↓
critic
     ↓
course correction
```

not:

```text
action
critic
action
critic
action
critic
```

That would waste tokens.

---

# One idea I particularly like: progress-over-thinking

MiniAGI's critic explicitly asks whether the agent is making **real-world progress** rather than just talking to itself. [GitHub](https://github.com/muellerberndt/mini-agi/blob/main/miniagi.py?utm_source=chatgpt.com)

That matches a major FRIDAY problem we already found:

> claims it opened something but executed nothing.

We can formalize:

```text
ActionProgressScore

state changed?
tool executed?
artifact produced?
test improved?
requirement moved?
```

If after N turns:

```text
tokens_used > threshold
AND
real_progress == 0
```

then:

```text
STALL_DETECTED
→ critic / strategy reset
```

This could improve both the **product agent** and the **build harness**.

---

# Where MiniAGI is not suitable

### 1. Shell execution

MiniAGI allows raw:

```text
execute_shell
execute_python
```

directly from the agent. [GitHub](https://github.com/muellerberndt/mini-agi/blob/main/miniagi.py?utm_source=chatgpt.com)

That directly conflicts with what we've just fixed in:

```text
SEC-005
EXEC-SBX-001
```

FRIDAY must instead do:

```text
MiniLoop
   ↓
Capability request
   ↓
ExecutionEnvelope
   ↓
PolicyEngine
   ↓
Sandbox
```

Never:

```text
LLM
→ subprocess
```

---

### 2. Security model

Its README itself warns that the agent may suggest harmful commands and may share data with third-party providers depending on configuration. [GitHub](https://github.com/muellerberndt/mini-agi/blob/main/README.md?utm_source=chatgpt.com)

So its execution layer is nowhere near sufficient for FRIDAY.

---

### 3. Memory

Its memory is essentially:

```text
LLM summary
+
last actions
```

FRIDAY now has a far stronger architecture:

```text
canonical memory
provenance
critical facts
structured objective state
workspace scopes
retrieval
context compiler
```

So don't replace any of that.

---

### 4. Model architecture

MiniAGI is tightly tied to the older OpenAI/ThinkGPT model style. [GitHub](https://github.com/muellerberndt/mini-agi/blob/main/miniagi.py?utm_source=chatgpt.com)

FRIDAY needs:

```text
Claude
OpenAI
Gemini
local models
free providers
future providers
```

through ModelGateway.

---

# Best use: a lightweight executor tier

I would add this architecture:

```text
                        FRIDAY

                           │
                   Task Complexity
                           │

             ┌─────────────┼──────────────┐
             ▼             ▼              ▼

          SIMPLE         NORMAL          COMPLEX

             │             │              │

         MiniLoop      Objective Run    Agent Team

             │             │              │

      local/cheap       Sonnet/etc.       Opus/
         model                            specialists
```

Examples:

### Simple

> “check if this file exists and rename it.”

```text
MiniLoop
```

### Normal

> “Add a login feature.”

```text
Objective OS
+
normal coding worker
```

### Complex

> “Research, design and build a new FRIDAY sandbox runtime.”

```text
Objective OS
+
specialists
+
strong model
```

This directly supports your goal:

> **less token waste, but full capability when necessary.**

---

# Add a bounded MiniLoop

Something like:

```yaml
mini_loop:
  max_steps: 8
  max_tokens: 6000
  max_cost: 0.10
  critic_after_no_progress_steps: 3
  max_repeated_action: 1
```

If it succeeds:

```text
DONE
```

If complexity grows:

```text
ESCALATE_TO_OBJECTIVE
```

So a cheap task doesn't accidentally evolve into a 70k-token autonomous session.

---

# Another useful pattern: strict action schema

MiniAGI enforces a structured next action format instead of free-form output. [GitHub](https://github.com/muellerberndt/mini-agi/blob/main/miniagi.py?utm_source=chatgpt.com)

For FRIDAY we'd modernize this to:

```json
{
  "intent": "ACT",
  "capability": "files.rename",
  "arguments": {},
  "expected_postcondition": "...",
  "confidence": 0.94
}
```

Then deterministic code handles execution.

That's much better than the model producing arbitrary text like:

> “I think I should probably rename the file now.”

---

# Capability Forge verdict

If tomorrow you give FRIDAY:

```text
https://github.com/muellerberndt/mini-agi
```

Capability Forge should ideally decide:

```yaml
classification:
  autonomous_agent_framework

overlap:
  objective_os: HIGH
  memory: HIGH
  command_execution: HIGH
  model_gateway: HIGH

novel_useful_patterns:
  - minimal one-action loop
  - progress critic
  - bounded recent-history summary
  - strict action protocol

decision:
  EXTRACT_PATTERN

install_runtime:
  false

new_friday_capabilities:
  - MiniLoopExecutor
  - ProgressCritic
```

That's exactly the behavior we want from Capability Forge.

---

## I would add only two requirements

### `COG-002 — Lightweight Autonomous Loop`

FRIDAY SHALL provide a low-overhead execution loop for simple bounded objectives that do not justify full Objective OS planning or multi-agent orchestration.

### `COG-003 — Progress Critic`

FRIDAY SHALL detect repeated actions, reasoning-without-action, objective stagnation and poor progress-per-token, and invoke corrective reasoning only when stagnation is detected.

Acceptance example:

```text
simple task
→ MiniLoop
→ 3 actions
→ done

versus

complex task detected
→ MiniLoop refuses/escalates
→ Objective OS
```

So **yes, use MiniAGI — but as an architectural idea mine, not another embedded agent brain**. Its simplicity is actually the valuable part: it can teach FRIDAY when *not* to invoke the heavyweight machinery.
