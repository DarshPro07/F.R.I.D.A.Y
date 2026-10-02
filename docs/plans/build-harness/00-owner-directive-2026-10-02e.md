# The owner's Workflow Fabric proposal, 2026-10-02 (verbatim)

> Extracted byte-for-byte from session transcript 47979877 (2026-10-02T07:58Z) by `add_a9.py`; the pasted-content tags
> are removed and nothing else is changed. Other text in the message: ''.
> Nothing below this note is the implementing agent's.

````text
Yes. **FRIDAY should absolutely have a workflow capability**, but I would not hard-wire FRIDAY to n8n.

The better design is:

```text
                    FRIDAY
                       │
                Workflow Fabric
                       │
        ┌──────────────┼──────────────┐
        ▼              ▼              ▼
   Activepieces       n8n          Windmill
   app workflows   visual flows   code workflows
        │              │              │
        └──────────────┼──────────────┘
                       ▼
                  Real-world apps
```

You say:

> “Friday, whenever a Shopify order above ₹5,000 comes in, add it to Sheets, notify WhatsApp, and create a follow-up task if it isn't shipped in 24 hours.”

FRIDAY should decide:

```text
Does this need a durable reusable workflow?
        ↓ yes

Which workflow backend is best?
        ↓

create it
connect accounts through OAuth
test it
publish it
monitor it
repair it when it breaks
```

You shouldn't have to open n8n yourself.

## My current preference: **Activepieces first**

For FRIDAY specifically, **Activepieces may fit better than n8n as the default workflow backend**.

Why?

Activepieces currently has built-in MCP support, can expose connected app actions through a single MCP server, is self-hostable, supports OAuth, and its current site lists **760+ applications**. Its core is MIT licensed. [Activepieces](https://www.activepieces.com/mcp/github?utm_source=chatgpt.com)

That means FRIDAY could connect once:

```text
FRIDAY
   ↓ MCP
Activepieces
   ↓
Google
Slack
GitHub
Shopify
CRM
Sheets
email
etc.
```

Instead of FRIDAY carrying:

```text
Google MCP
Slack MCP
GitHub MCP
Shopify MCP
...
```

for hundreds of integrations.

That's potentially a **big simplification of our Capability Fabric**.

Activepieces also allows an MCP client to build/manage flows and expose flows back as callable MCP tools. [GitHub](https://github.com/activepieces/activepieces/blob/main/docs/mcp/overview.mdx?utm_source=chatgpt.com)

So FRIDAY could do:

```text
"Friday, automate invoice follow-ups."

        ↓

FRIDAY designs workflow

        ↓

Activepieces creates it

        ↓

workflow appears as capability:

finance.invoice_followup
```

That's excellent.

---

## n8n is still very useful

I would definitely support it.

n8n can self-host locally/on-prem/private cloud, and its current product supports workflows plus AI agents and MCP-related integration. [GitHub](https://github.com/n8n-io/n8n-docs/blob/main/docs/deploy/host-n8n/README.md?utm_source=chatgpt.com)

For an individual or company using FRIDAY internally:

```text
FRIDAY
→ local n8n
→ workflow
```

is perfectly reasonable.

But there is a product consideration.

n8n uses its **Sustainable Use License**, not a permissive MIT/Apache license. n8n's own licensing guidance says that if you embed n8n into a product or host/manage workflows and credentials for customers, commercial/Embed or Enterprise licensing may be required. [n8n Help Center](https://support.n8n.io/article/can-i-use-your-license-for-my-use-case?utm_source=chatgpt.com)

Since you're ultimately thinking about distributing FRIDAY to many users/companies, I would therefore make:

```text
n8n
=
OPTIONAL WORKFLOW PROVIDER
```

rather than:

```text
n8n
=
FRIDAY CORE
```

That keeps our product architecture clean.

---

# Windmill is interesting for another reason

Windmill is much more **code-first**.

A Python, TypeScript, Go or Bash script can become:

```text
API
scheduled job
trigger
workflow step
tool
```

and workflows can use schedules, webhooks, Kafka, Postgres CDC, emails, WebSockets, etc. [Windmill](https://www.windmill.dev/use-cases/scripts?utm_source=chatgpt.com)

It also supports AI-agent steps where models can call Windmill scripts as tools. [Windmill](https://www.windmill.dev/docs/core_concepts/ai_agents?utm_source=chatgpt.com)

That makes Windmill attractive for:

```text
DevOps
data jobs
backend scripts
internal tools
code-heavy automations
scheduled Python jobs
AI coding workers
```

Example:

> “Friday, every night benchmark my models, store results, and alert me if performance drops.”

I'd probably choose:

```text
Windmill
```

over n8n for that.

But Windmill has AGPL/commercial licensing considerations for wrapping/reselling its broader system, so again I'd use it behind an adapter, not make FRIDAY depend on it. [GitHub](https://github.com/windmill-labs/windmill/blob/main/LICENSE?utm_source=chatgpt.com)

---

# And don't forget our own Durable Execution Kernel

There are actually two completely different types of workflow.

### Type 1 — FRIDAY itself

Example:

```text
research problem
→ build capability
→ wait for user
→ restart PC
→ resume
→ test
→ finish objective
```

That belongs in:

```text
FRIDAY DurableExecutionKernel / Objective OS
```

Not n8n.

### Type 2 — business/user automation

Example:

```text
new lead
→ enrich
→ send email
→ wait two days
→ follow up
```

That's where:

```text
Activepieces / n8n / Windmill
```

shine.

Never make FRIDAY's own brain dependent on n8n being alive.

---

# So I'd add a new subsystem: **Workflow Fabric**

```text
                          FRIDAY
                             │
                      WorkflowRouter
                             │
            ┌────────────────┼─────────────────┐
            ▼                ▼                 ▼
        EPHEMERAL        REUSABLE          DURABLE
          TASK           AUTOMATION         OBJECTIVE
            │                │                 │
        direct tools      Workflow          Objective OS
                           Fabric
                             │
            ┌────────────────┼────────────────┐
            ▼                ▼                ▼
      Activepieces          n8n           Windmill
```

And later:

```text
Kestra
Node-RED
Temporal
other workflow engines
```

can plug into the same interface.

---

# FRIDAY should choose automatically

Example:

### Simple one-off task

> “Send this file to Swati.”

```text
direct capability
```

Don't create a workflow.

### Repetitive business automation

> “Every lead from Meta should go to CRM and get a WhatsApp follow-up.”

```text
Activepieces / n8n
```

### Code/data pipeline

> “Every night scrape metrics, run Python analysis and regenerate reports.”

```text
Windmill
```

### Long-running FRIDAY objective

> “Build my Iron Man helmet over the next three months.”

```text
Objective OS
```

### IoT later

> “When my workshop sensor detects X, tell FRIDAY.”

Node-RED is particularly interesting for hardware/IoT and is Apache-2.0 licensed. [GitHub](https://github.com/node-red/node-red.github.io/blob/main/about/license/index.md?utm_source=chatgpt.com)

That may matter later for your physical/JARVIS projects.

---

# The coolest part: Friday can BUILD workflows itself

Instead of giving the user a workflow editor:

```text
User:
"When an order gets cancelled, check why and tell my support team."

FRIDAY:

1. understand trigger
2. understand required accounts
3. search available connectors
4. generate workflow
5. create test event
6. run workflow
7. inspect result
8. repair errors
9. publish
10. register capability
```

Then:

```text
Workflow:
cancelled-order-response-v1

Status:
ACTIVE

Provider:
Activepieces

Capability:
commerce.cancelled_order_handler
```

The workflow itself becomes another FRIDAY capability.

---

# Even better: workflows can become **compiled procedures**

Suppose FRIDAY repeatedly performs:

```text
open Gmail
find invoice
download attachment
upload Drive
notify Slack
```

after maybe 5–10 repeated successful executions, Pattern Learner says:

```text
"This is deterministic and repeated."
```

FRIDAY proposes:

```text
convert agent trajectory
        ↓
deterministic workflow
```

So:

```text
BEFORE

LLM
→ Gmail
→ reason
→ Drive
→ reason
→ Slack

expensive
```

becomes:

```text
AFTER

Activepieces workflow

cheap
fast
deterministic
```

FRIDAY only handles exceptions.

This can reduce model cost tremendously.

Call it:

# `TrajectoryToWorkflowCompiler`

That's a new subsystem I strongly recommend.

---

# Example

FRIDAY notices:

```text
Every day you:

1. download Meta ad report
2. put results into Sheet
3. compare yesterday
4. notify team
```

After learning:

> “Boss, this process has repeated seven times. I can convert it into an automated workflow so it runs without model tokens. Create it?”

If your standing policy says:

```text
auto-create low-risk workflows
```

then:

```text
build
test
activate
```

automatically.

That is exactly the sort of self-improvement we want.

---

# Workflow levels

I'd define:

```text
L0 — Direct Action
one tool call

L1 — Procedure
deterministic series of FRIDAY capabilities

L2 — Managed Workflow
Activepieces/n8n/Windmill

L3 — Agentic Workflow
deterministic skeleton + model decision nodes

L4 — Objective
Objective OS / multi-hour-day mission
```

FRIDAY promotes tasks to the cheapest adequate level.

---

# Add a `Workflow ROI Engine`

Before turning something into a workflow:

```text
frequency
×
model tokens currently consumed
×
time
×
error reduction
```

vs:

```text
workflow creation
maintenance
integration complexity
```

Only automate when worthwhile.

Otherwise we'd end up with 10,000 useless workflows.

---

# Also add Workflow Health

Each deployed workflow:

```yaml
workflow:
  id:
  provider:
  version:

health:
  last_run:
  success_rate:
  latency:
  failures:

dependencies:
  apps:
  credentials:
  connector_versions:

owner:
  workspace:

fallback:
  friday_agent:
```

If:

```text
workflow fails
```

then:

```text
FRIDAY
→ diagnose
→ repair

OR

execute manually through agent
```

so the user's work doesn't stop.

---

# Workflow provider health too

Same idea as ModelGateway:

```text
Activepieces:
READY

n8n:
DEGRADED

Windmill:
READY
```

WorkflowRouter selects accordingly.

---

# Security matters

FRIDAY should never give an automation engine all credentials.

Use:

```text
Workflow
   ↓
Secret Broker
   ↓
scoped credential/OAuth connection
```

The workflow gets only what it needs.

Example:

```text
marketing workflow
```

doesn't get:

```text
bank credentials
```

And workflow changes should go through the same policy/JIT authority system.

---

# What I would use now

For **FRIDAY's first workflow backend**, my current preference is:

### **1. Activepieces — first**

Because:

- self-hostable;
- core MIT licensed;
- MCP-native;
- hundreds of connected applications;
- OAuth;
- flows + agents;
- matches our FRIDAY Capability Fabric extremely well. [GitHub](https://github.com/activepieces/activepieces/blob/main/docs/overview/welcome.mdx?utm_source=chatgpt.com)

### **2. n8n — supported adapter**

Because:

- extremely mature workflow UI/ecosystem;
- lots of integrations;
- self-hostable;
- useful for existing n8n users. [n8n](https://n8n.io/vs/activepieces/?utm_source=chatgpt.com)

But don't make it core because of product/embedding licensing considerations.

### **3. Windmill — developer workflow backend**

Use for:

```text
code
infra
data
Python
DevOps
```

rather than ordinary business automations.

---

# I would add these requirements

**WF-001 — Workflow Fabric**  
FRIDAY SHALL support interchangeable workflow execution providers rather than depending on a single workflow product.

**WF-002 — Natural-Language Workflow Creation**  
A user SHALL be able to describe a recurring process and have FRIDAY create, test and activate the workflow without manually configuring workflow nodes.

**WF-003 — Provider Selection**  
FRIDAY SHALL select workflow backend according to connectors, durability, task type, privacy, resources and user policy.

**WF-004 — Workflow-to-Capability Registration**  
A verified workflow SHALL become callable through the Capability Fabric.

**WF-005 — Trajectory-to-Workflow Compilation**  
FRIDAY SHALL detect repeated successful agent trajectories and may propose or automatically compile appropriate deterministic workflows.

**WF-006 — Workflow Health**  
FRIDAY SHALL monitor workflow executions and detect degraded or broken workflows.

**WF-007 — Self-Repair**  
FRIDAY SHALL be able to diagnose and repair workflows, with rollback and verification.

**WF-008 — Agent Fallback**  
When a workflow is broken, FRIDAY SHOULD be able to complete eligible urgent work using normal capabilities while repairing the workflow.

**WF-009 — Scoped Credentials**  
Workflow engines SHALL access only objective/workflow-specific credential scopes.

**WF-010 — Workflow ROI**  
FRIDAY SHALL not convert every task to a workflow; promotion should be based on recurrence, reliability and expected savings.

And I think this would be a **major upgrade** because it gives FRIDAY two modes:

```text
INTELLIGENCE
for novel work

+

AUTOMATION
for repeated work
```

The more FRIDAY learns your routine, the **less it should need an LLM to perform routine work**. That's how we get a system that becomes cheaper and faster over time rather than consuming more and more model tokens.
````
