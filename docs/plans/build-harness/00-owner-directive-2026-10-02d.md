# The owner's Capability Forge proposal, 2026-10-02 (verbatim)

> Extracted byte-for-byte from session transcript 47979877 (2026-10-02T07:39Z, 1 pasted parts) by
> `extract_master.py`; the pasted-content tags are removed and nothing else is changed.
> The owner's own words between the pastes: ''
> Nothing below this note is the implementing agent's.

## Message 1 - Capability Forge and the Repo Assimilator

````text
Yes — this should become a **core FRIDAY capability**.

The experience should be as simple as:

```text
You:
"Friday, add this capability:
https://github.com/trycua/cua"

Friday:
→ understands repo
→ decides what it provides
→ checks whether we already have it
→ scans it
→ tests it in isolation
→ builds the required adapter/skill/MCP/plugin
→ connects it to Capability Fabric
→ verifies a real task
→ remembers how to use it
→ monitors future updates

Friday:
"Integrated. I can now use it for desktop computer-use tasks."
```

You should **not** need to know whether the repository is Python, Rust, Node, MCP, CLI, SDK, a skill pack, a Docker service, or something else.

I would call this subsystem **Capability Forge**, with a front door called **Repo Assimilator**.

## The key idea

Do **not** make the workflow:

```text
GitHub URL
→ clone
→ pip install
→ pray
```

Make it:

```text
                           GitHub URL
                               │
                               ▼
                        REPO ASSIMILATOR
                               │
                    clone into quarantine
                               │
                               ▼
                         Repo Intelligence
                               │
                    ┌──────────┼─────────┐
                    ▼          ▼         ▼
                 code       docs      manifests
                    │
                    └──────────┼─────────┘
                               ▼
                     Capability Classifier
                               │
          ┌────────────────────┼─────────────────────┐
          ▼                    ▼                     ▼
        SKILL                 MCP                 CLI/SDK
          │                    │                     │
          ├──────── service / app / model / prompt ─┤
          │
          ▼
                    Capability Trust Pipeline
                               │
                          quarantine
                               │
                               ▼
                       Integration Planner
                               │
                               ▼
                       Adapter Generator
                               │
                               ▼
                           Sandbox
                               │
                        functional tests
                               │
                         security tests
                               │
                           real task
                               │
                               ▼
                      Capability Registry
                               │
                               ▼
                             FRIDAY
```

This is much more powerful than a “GitHub installer.”

## Step 1 — FRIDAY understands the repository automatically

When you paste a URL, first create a **Repo Dossier**.

FRIDAY should determine:

```yaml
repository:
  source:
  commit:
  license:
  languages:
  build_system:
  runtime:
  dependencies:

purpose:
  summary:
  major_capabilities:

integration_type:
  - skill
  - mcp
  - cli
  - python_library
  - node_library
  - executable
  - local_service
  - docker_service
  - model
  - prompt_corpus
  - reference_only

entry_points:
  ...

permissions_required:
  filesystem:
  network:
  browser:
  desktop:
  credentials:

platforms:
  windows:
  macos:
  linux:

existing_overlap:
  ...

recommended_adoption:
  USE_DIRECTLY
  WRAP
  EXTRACT_PATTERN
  BUILD_ADAPTER
  REFERENCE_ONLY
  REJECT
```

For this orientation layer, the tools we've already researched are ideal.

`codebase-memory-mcp` can create a persistent graph of functions, classes, call chains, HTTP routes and cross-service relationships instead of making the agent read every file. [GitHub](https://github.com/DeusData/codebase-memory-mcp/blob/main/README.md?utm_source=chatgpt.com)

Serena can inspect and edit at symbol level through MCP rather than relying only on primitive whole-file reading. [GitHub](https://github.com/oraios/serena?utm_source=chatgpt.com)

Repomix can ingest a **remote GitHub repository** and even generate Agent Skills-format reference material from it; it also supports tree-sitter compression and secret filtering. [GitHub](https://github.com/yamadashy/repomix?utm_source=chatgpt.com)

So Repo Assimilator doesn't need to burn 80k tokens understanding each project.

---

# Step 2 — classify instead of treating all GitHub repos alike

This is essential.

If you give:

```text
https://github.com/NVIDIA/SkillSpector
```

that's effectively a:

```text
CLI
+ scanner
+ optional MCP
```

If you give:

```text
https://github.com/trycua/cua
```

that's:

```text
computer-use capability
+ SDK
+ driver
+ MCP
+ benchmark system
```

If you give:

```text
some-repo/SKILL.md
```

that's:

```text
skill
```

If you give:

```text
system_prompts_leaks
```

that's:

```text
RESEARCH_ONLY
```

It should not become executable production code merely because it is a GitHub repository.

This classifier prevents a lot of unnecessary integration.

---

# Step 3 — check whether FRIDAY already has the capability

Before writing code:

```text
Requested capability
        ↓
Capability Registry
        ↓
Already supported?
```

Perhaps you paste another browser automation repository.

FRIDAY might determine:

```text
Existing:
Playwright
Cua Driver

New repo:
85% duplicate capability

Decision:
REFERENCE_ONLY
```

Or:

```text
New capability:
Bluetooth-device control

Existing:
none

Decision:
BUILD_ADAPTER
```

This avoids endless framework bloat.

---

# Step 4 — quarantine first

Absolutely no repository should immediately modify FRIDAY.

Downloaded repository goes here conceptually:

```text
FRIDAY/
  capabilities/
    quarantine/
      github.com-owner-repo/
```

It initially has:

```text
network = restricted
secrets = none
FRIDAY memory = none
user files = none
system authority = none
```

Then inspect it.

NVIDIA's SkillSpector is directly useful because it can accept Git repositories/URLs/ZIPs/directories and scan for prompt injection, exfiltration, privilege escalation, dangerous code, memory poisoning, MCP poisoning and other classes of risk. [GitHub](https://github.com/NVIDIA/SkillSpector/blob/main/README.md?utm_source=chatgpt.com)

But we already established:

```text
SkillSpector PASS
≠
trusted
```

It still needs sandbox execution and behavioral testing.

---

# Step 5 — automatic integration planning

Now create:

## `IntegrationRecipe`

Example for Cua:

```yaml
capability_id: computer_use.cua

adoption:
  mode: WRAP

backend:
  type: desktop_driver

install:
  isolated_environment: true

interfaces:
  - ComputerUseProvider

permissions:
  windows:
    - accessibility
    - bounded_input

tests:
  - enumerate_windows
  - inspect_accessibility_tree
  - background_click
  - post_state_verification

fallback:
  - playwright
  - vision

expose_to_friday:
  capability:
    computer.use.desktop
```

Example for an MCP:

```yaml
mode: MCP

server:
  command: ...

expose:
  selected_tools:
    - search
    - fetch

blocked_tools:
  - shell
  - delete
```

Example for a Python SDK:

```text
SDK
↓
thin FRIDAY adapter
↓
CapabilityRuntime
```

The model shouldn't choose different architecture every time.

Most integration patterns should be deterministic templates.

---

# Step 6 — the repo may become a **skill**, **capability**, or both

Important distinction.

Suppose the repository is:

```text
DaVinci Resolve MCP
```

FRIDAY needs:

### Capability

Executable integration:

```text
DaVinciAdapter
→ Resolve
```

### Skill

Knowledge:

```text
how to edit efficiently
which operations are available
known limitations
workflow guidance
```

Therefore Repo Assimilator may generate:

```text
capability/
    adapter

skill/
    SKILL.md

tests/
    ...

manifest/
    capability.json
```

Skills explain **how/when**.

Capabilities actually **do**.

---

# Step 7 — automatically generate a capability manifest

Every assimilated repository should have something like:

```yaml
id: cua-driver
version: 0.8.x

origin:
  repo: https://github.com/trycua/cua
  commit: abc123

integration:
  mode: wrapped

capabilities:
  - desktop.inspect
  - desktop.click
  - desktop.type

platform:
  - windows

permissions:
  filesystem: none
  network: none
  desktop: bounded

trust:
  status: VERIFIED_LOCAL
  scanned: true
  sandboxed: true

tests:
  health: PASS
  functional: PASS

update:
  policy: notify

rollback:
  previous_version: ...
```

FRIDAY's Capability Registry reads this.

No model has to remember how the integration works.

---

# Step 8 — test the **actual capability**, not just installation

Very important.

Do not say:

```text
pip install succeeded
→ integrated
```

Instead:

```text
repository says:
"controls desktop"

FRIDAY acceptance test:
open Calculator
→ read display
→ press 4
→ press 2
→ independently observe 42
```

Only then:

```text
DESKTOP_CAPABILITY = READY
```

Similarly:

```text
GitHub repo claims:
"YouTube transcript"

test:
take known public video
→ retrieve transcript
→ validate content
```

That is actual assimilation.

---

# Step 9 — then give it to FRIDAY as a normal capability

The user never needs to see:

```text
Cua
Maxun
Scrapling
browser-use
some-mcp-project
```

during normal usage.

FRIDAY sees:

```text
desktop.control
web.scrape
browser.automate
video.understand
```

So:

```text
User:
"Edit this application."

FRIDAY:
Capability Search
        ↓
desktop.control
        ↓
backend:
Cua Driver
```

The user experience remains **FRIDAY**.

But internally we should preserve source/provenance/license information rather than concealing where third-party components came from.

---

# Step 10 — automatic local skill generation

This connects directly to your earlier Gawkbot idea.

Once integration succeeds, FRIDAY creates:

```text
Local Skill:
cua-desktop-control
```

Initially:

```text
DRAFT
```

Then:

```text
real usage
→ successful
→ revalidate
→ ACTIVE
```

Skill contains things such as:

```text
best actions
known bugs
which apps work
fallback paths
performance characteristics
version-specific information
```

Over time:

```text
Chrome update breaks technique
↓
FRIDAY observes failure
↓
skill becomes DEGRADED
↓
researches/fixes integration
↓
retests
↓
updates local skill
```

That's exactly the living skill system you wanted.

---

# Step 11 — automatically create an MCP when appropriate

Suppose repo is just a library:

```text
cool-video-library
```

but FRIDAY needs to expose:

```text
video.trim()
video.subtitle()
```

Capability Forge could generate:

```text
Library
↓
minimal adapter
↓
FRIDAY Capability
```

or where MCP is clearly useful:

```text
Library
↓
small MCP wrapper
↓
Capability Broker
```

But **do not convert everything to MCP**.

If a native Python call is simpler:

```text
use native Python.
```

If CLI is best:

```text
use CLI.
```

If browser operation is best:

```text
use browser.
```

The adapter planner picks the simplest reliable path.

---

# Step 12 — update automatically

Once imported:

```text
Technology Scout
    ↓
upstream changed?
```

Compare:

```text
current commit
latest stable commit
```

Then:

```text
new update
↓
read changelog/diff
↓
security scan
↓
sandbox
↓
run existing capability tests
↓
benchmark
```

If better and compatible:

```text
LOW-RISK UPDATE
→ auto-upgrade if user policy allows
```

If breaking/high-risk:

```text
"Boss, Cua has a major update.
It improves X but changes Y.
Should I promote it?"
```

If user previously says:

```text
always auto-update safe integrations
```

remember policy.

---

# Step 13 — rollback automatically

Every integration needs:

```text
previous known-good version
```

Then:

```text
update
↓
failure rate increases
↓
automatic rollback
```

and:

```text
mark new version BAD
```

This prevents Technology Scout from constantly breaking FRIDAY.

---

# Step 14 — learn from repository patterns even when we don't install it

This is another important path.

Maybe you give:

```text
some-agent-framework
```

FRIDAY determines:

```text
90% overlaps existing architecture

BUT

it has an excellent retry algorithm
```

Decision:

```text
EXTRACT_PATTERN
```

Then it researches that algorithm and implements an equivalent FRIDAY-native improvement if useful.

It doesn't need the whole project.

This is how I'd treat things like ECC in many cases.

---

# The command should be stupidly simple

Eventually:

```text
Friday, integrate:
https://github.com/owner/repository
```

Optional:

```text
Friday, integrate this for video editing:
https://github.com/...
```

Everything else:

```text
research
classification
security
dependency setup
adapter
skill
testing
registration
documentation
update policy
```

is autonomous.

---

# And FRIDAY should return a compact result

Something like:

```text
Integrated ✓

Capability:
Background desktop control

Source:
trycua/cua @ abc123

Integration:
Wrapped Cua Driver behind ComputerUseFabric

New abilities:
• inspect Windows applications
• interact with bounded windows
• background input
• accessibility state

Security:
Sandbox PASS
Capability scan PASS
No secret access

Verification:
5/5 journeys passed

Skill:
cua-desktop-control v1 ACTIVE

Fallback:
Playwright / vision

Update:
Monitor upstream; ask before major upgrade
```

Not 5,000 tokens of install logs.

---

# One more powerful feature: `Capability Forge can recursively resolve dependencies`

Imagine a repo requires:

```text
FFmpeg
Node
some MCP
Chrome
```

FRIDAY builds:

```text
Dependency DAG
```

Then:

```text
already installed?
compatible?
trusted?
```

and provisions the missing pieces.

But every dependency also passes trust/provenance rules.

No:

```text
curl random-script | bash
```

straight into your main system.

---

# I would add these requirements to FRIDAY

1. **CAP-FORGE-001 — GitHub URL Assimilation**  
   A user SHALL be able to provide a GitHub repository URL and ask FRIDAY to evaluate and integrate its useful capabilities without manually editing FRIDAY source/configuration.

2. **CAP-FORGE-002 — Automatic Repository Classification**  
   FRIDAY SHALL classify repositories as skill, MCP, CLI, library, application, service, model, research corpus, capability pack or reference-only before integration.

3. **CAP-FORGE-003 — Quarantined Analysis**  
   New repositories SHALL be analyzed outside the trusted runtime before receiving FRIDAY authority, secrets, user data or standing network permissions.

4. **CAP-FORGE-004 — Capability Integration Planning**  
   FRIDAY SHALL derive the minimum viable integration pattern: native call, CLI wrapper, MCP, service adapter, browser path or reference-only.

5. **CAP-FORGE-005 — Automatic Adapter Generation**  
   Where appropriate, FRIDAY SHALL generate and test the smallest necessary adapter without requiring the user to write code.

6. **CAP-FORGE-006 — Skill Generation**  
   Successfully integrated capabilities SHALL be eligible to produce local skills describing correct use, limitations and verified procedures.

7. **CAP-FORGE-007 — End-to-End Verification**  
   Integration is not complete until at least one real representative capability journey passes independently observed verification.

8. **CAP-FORGE-008 — Capability Registry Admission**  
   Only verified integrations may become normal FRIDAY capabilities.

9. **CAP-FORGE-009 — Upstream Monitoring**  
   Integrated repository versions and source commits SHALL be tracked for updates and security/reliability changes.

10. **CAP-FORGE-010 — Rollback**  
   Updates SHALL retain a last-known-good integration and support automatic rollback.

11. **CAP-FORGE-011 — Redundancy Detection**  
   FRIDAY SHALL avoid importing repositories whose useful capabilities are already sufficiently covered unless measurements show an improvement.

12. **CAP-FORGE-012 — Pattern Extraction**  
   FRIDAY MAY adopt useful architecture/patterns from a repository without installing the repository when that is the simpler or safer solution.

This should become one of **FRIDAY's signature features**.

Because then your workflow stops being:

> “I found another cool GitHub repository. Now how do I connect it?”

and becomes:

> **“Friday, learn this.”**

And FRIDAY itself figures out whether that means **install it, wrap it, turn it into a skill, expose an MCP, extract one idea, or reject it**. [GitHub](https://github.com/NVIDIA/SkillSpector/blob/main/README.md?utm_source=chatgpt.com)
````

