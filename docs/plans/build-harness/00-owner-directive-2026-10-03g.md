# Owner directive 2026-10-03 (g): OpenShell, Coucou and the Capability Marketplace

Pasted by the owner on 2026-10-03, extracted verbatim from the session transcript. Decisions: NVIDIA/OpenShell HIGH_PRIORITY_WRAP_AND_EVALUATE (the K2 tier of EXEC-SBX-001 and the Capability Forge quarantine runtime); Louis-CFM/coucou EXTRACT (UI patterns only); skills.rest through a Capability Marketplace that indexes all metadata and activates only what survives relevance, NVIDIA SkillSpector, sandboxed evaluation and a Skill Arena. The links are the owner's.

---

Yes — but I would change one part of your idea:

> **Ingest everything, activate almost nothing automatically.**

That gives you the benefit of `skills.rest` without turning FRIDAY into a bloated or unsafe junk drawer.

## 1. `NVIDIA/OpenShell`: very strong fit

This is one of the most relevant repos you've shared for FRIDAY.

OpenShell is specifically built as a **policy-enforced runtime for autonomous agents**. Its architecture separates a gateway/control plane from sandbox supervisors, and enforces filesystem, process, network, credential and inference boundaries inside each sandbox. On Linux it uses Landlock/seccomp and a policy proxy; it also has drivers for different compute backends. [GitHub](https://github.com/NVIDIA/OpenShell/blob/main/architecture/README.md?utm_source=chatgpt.com)

That maps almost directly onto our:

```text
ExecutionEnvelope
EXEC-SBX-001
ScopedCommandRunner
Secret Broker
Model Gateway
Network Scope
Worker Environment
```

So my classification is:

```yaml
NVIDIA/OpenShell:
  decision: HIGH_PRIORITY_EVALUATE_AND_WRAP
  role:
    - scoped command sandbox
    - worker runtime
    - filesystem/network/process enforcement
    - endpoint-bound credentials
    - inference routing
  does_not_replace:
    - FRIDAY Objective OS
    - FRIDAY Policy Engine
    - FRIDAY Model Router
    - FRIDAY UI
```

OpenShell already supports Windows through **WSL2 experimentally**, plus Linux/macOS environments, Docker/Podman/VM-style runtimes. [GitHub](https://github.com/NVIDIA/OpenShell/blob/main/README.md?utm_source=chatgpt.com)

So for your Windows laptop I would eventually use:

```text
EXEC-SBX-001

K1 = native AppContainer/LPAC
     lightweight Windows jobs

K2 = OpenShell through WSL2
     serious coding/build/testing workers

K3 = Windows Sandbox / VM
     highly untrusted work
```

Actually, OpenShell may save us from implementing a large amount of K2 sandbox infrastructure ourselves.

It also does endpoint-bound credential injection: the sandbox does not need raw secrets; OpenShell can inject credentials only for approved destinations. That's extremely relevant to your requirement that cloud models should never simply see API keys. [GitHub](https://github.com/NVIDIA/OpenShell/blob/main/architecture/gateway.md?utm_source=chatgpt.com)

### Very strong candidate

I would put OpenShell at:

**★★★★★ for FRIDAY architecture.**

---

# 2. `louis-cfm/coucou`: useful, but mostly UX

Coucou is a lightweight UI that monitors Claude Code, Cursor, Gemini CLI, Antigravity and other agent sessions, shows what agents are doing, lets the user approve permissions, drop files, and interact without switching windows. It supports macOS, Windows and Linux, though Windows packaging currently has an unsigned-installer/Defender issue. [GitHub](https://github.com/Louis-CFM/coucou/blob/main/README.md?utm_source=chatgpt.com)

That gives us useful ideas for:

```text
FRIDAY Activity Island
FRIDAY agent-status overlay
approvals
notifications
current worker
current task
model being used
drop file
stop/pause
```

I could imagine:

```text
                    top of screen

╭──────────────────────────────────────────────╮
│ FRIDAY  ● Building checkout flow             │
│ Sonnet · 43% · 18 tests passed               │
│ [Pause] [Details] [Take control]             │
╰──────────────────────────────────────────────╯
```

But I would **not embed Coucou as another application UI**.

Use:

```yaml
Coucou:
  decision: EXTRACT_UX_PATTERNS
  value:
    - agent status indicator
    - compact approval UX
    - provider/model switch UX
    - drag-and-drop context
    - notifications
  reject:
    - separate visible Coucou branding
    - duplicate chat system
    - duplicate model gateway
```

So OpenShell is **backend/security**, Coucou is **frontend inspiration**.

---

# 3. `skills.rest`: yes — create a **Skill Marketplace Harvester**

This should be part of Capability Forge.

But don't run:

```bash
install 500,000 skills
```

The site currently advertises a catalog/search helper across hundreds of thousands of skills. [skills.rest](https://skills.rest/skill/context-memory?utm_source=chatgpt.com)

And NVIDIA's SkillSpector reports that in its analyzed dataset of 31,132 skills, **26.1% contained vulnerabilities and 5.2% showed likely malicious intent**. [GitHub](https://github.com/nvidia/skillspector?utm_source=chatgpt.com)

So the right architecture is:

```text
                     skills.rest
                         │
                    Catalog Harvester
                         │
                         ▼
                     METADATA ONLY
                         │
                  category + source
                         │
                         ▼
                   Relevance Filter
                         │
                         ▼
                  Deduplication
                         │
                         ▼
              candidate source download
                         │
                         ▼
                     QUARANTINE
                         │
                    SkillSpector
                         │
           ┌─────────────┼──────────────┐
           ▼             ▼              ▼
        security     dependencies      permissions
           │             │              │
           └─────────────┼──────────────┘
                         ▼
                     Sandbox
                         │
                     Eval Arena
                         │
                ┌────────┴────────┐
                ▼                 ▼
             USEFUL            USELESS
                │                 │
             ACTIVE             REJECT
```

## Important distinction

I would happily **index all skills.rest metadata**.

I would not download/activate all source code at once.

Instead:

```text
ALL CATALOG ENTRIES
→ indexed

RELEVANT CANDIDATES
→ downloaded

SAFE + USEFUL CANDIDATES
→ activated
```

That is how you get the value of the huge catalog without destroying:

- disk;
- startup time;
- context;
- security;
- maintainability.

---

# 4. SkillSpector should become a permanent Capability Forge gate

This is exactly what it is designed for.

SkillSpector can scan:

- directories;
- individual `SKILL.md`;
- Git repositories;
- URLs;
- ZIP files.

It checks prompt injection, data exfiltration, privilege escalation, excessive agency, tool misuse, memory poisoning, dangerous code, MCP least privilege, MCP tool poisoning, dependency CVEs and many other categories. [GitHub](https://github.com/NVIDIA/skills/blob/main/docs/scanning-agent-skills.mdx?utm_source=chatgpt.com)

So every skill/repo/package entering FRIDAY goes through:

```text
SkillSpector static scan
        ↓
manual/LLM semantic review if necessary
        ↓
dependency/CVE scan
        ↓
sandbox
        ↓
live value evaluation
```

I would initially use **SkillSpector CLI**, not its MCP mode, because their current README mentions a known stdio MCP initialization issue. [GitHub](https://github.com/nvidia/skillspector?utm_source=chatgpt.com)

Later we can expose it through FRIDAY's own capability wrapper.

---

# 5. Automatic removal too — yes

This part of your idea is important.

Skills shouldn't only have an installation lifecycle.

They need a **garbage-collection lifecycle**:

```text
QUARANTINED
    ↓
TESTING
    ↓
ACTIVE
    ↓
DEGRADED
    ↓
REVALIDATE
    ↓
ACTIVE

or

DEPRECATED
    ↓
ARCHIVED
    ↓
REMOVED
```

Remove/archive when:

```text
security scan becomes bad
source changes unexpectedly
dependency becomes vulnerable
better duplicate exists
skill isn't used
skill increases tokens without improving results
repeated failures
app/API version changed
skill becomes obsolete
```

So FRIDAY automatically keeps its capability catalog clean.

---

# 6. Don't convert everything into MCP

Your wording:

> plugins → skill → MCP → whatever

should actually become a classifier.

```text
Input artifact
     ↓
Capability Classifier

SKILL?
→ install as skill

MCP?
→ register MCP

CLI?
→ CLI adapter

Python/TS library?
→ native adapter

local service?
→ service capability

plugin?
→ plugin adapter

GitHub application?
→ wrap useful subsystem

prompt/reference?
→ knowledge artifact

nothing useful?
→ REJECT
```

MCP is excellent for remotely callable tools.

But something like:

```text
testing instructions
```

doesn't need an MCP server.

And:

```text
pure Python package
```

often doesn't need an MCP wrapper either.

Use the **simplest reliable interface**.

---

# 7. First high-value `skills.rest` candidates

From the catalog I searched, these are worth sending through SkillSpector/evaluation first.

### Browser / E2E

`playwright-web-testing`

Covers Playwright browser automation and E2E validation. [skills.rest](https://skills.rest/skill/playwright-web-testing?utm_source=chatgpt.com)

But FRIDAY already has Playwright.

Likely outcome:

```text
REFERENCE / EXTRACT
```

rather than duplicate installation.

---

### MCP integration

`mcp-integration`

There are multiple copies/forks of basically the same skill in skills.rest. [skills.rest](https://skills.rest/skill/mcp-integration-ngtpte?utm_source=chatgpt.com)

This is precisely why the **dedupe stage is necessary**.

Choose one strongest/version-current source after security/value evaluation.

Don't install seven copies.

---

### Worktrees

`git-worktree-everyinc`

High adoption and covers isolated worktrees for parallel development. [skills.rest](https://skills.rest/skill/git-worktree-everyinc?utm_source=chatgpt.com)

But FRIDAY's harness already has worktree logic.

Likely:

```text
REFERENCE
or
EXTRACT_PATTERN
```

---

### Research

The `research` skill from Daniel Miessler's Personal AI Infrastructure is one of the more heavily adopted research skills surfaced by the catalog. [skills.rest](https://skills.rest/skill/research?utm_source=chatgpt.com)

Also worth comparing:

`research-agent`, which explicitly handles recursive source-verified research and credibility assessment. [skills.rest](https://skills.rest/skill/research-agent?utm_source=chatgpt.com)

FRIDAY should benchmark them against our Research OS.

---

### Security

`security-pvpha`

Secure coding/security review patterns. [skills.rest](https://skills.rest/skill/security-pvpha?utm_source=chatgpt.com)

And:

`skill-security`

specifically scans skill definitions for prompt injection and poisoned configuration. [skills.rest](https://skills.rest/skill/skill-security?utm_source=chatgpt.com)

But SkillSpector is stronger as the **mandatory gate**.

---

### Testing

`claude-code-testing-workflows`

General TDD/unit/integration/E2E guidance. [skills.rest](https://skills.rest/skill/claude-code-testing-workflows?utm_source=chatgpt.com)

And:

`test-plugin-skill`

specifically targets Claude plugin/MCP testing. [skills.rest](https://skills.rest/skill/test-plugin-skill?utm_source=chatgpt.com)

Potentially useful to Capability Forge for generated MCPs.

---

### Docker

There are many duplicate Docker skills.

For example `docker-alangeb` and others provide container/Compose lifecycle support. [skills.rest](https://skills.rest/skill/docker-alangeb?utm_source=chatgpt.com)

Again:

```text
dedupe
→ pick winner
```

not install all.

---

### Memory

`context-memory` and `memory-context-management` offer persistent context/knowledge systems. [skills.rest](https://skills.rest/skill/context-memory?utm_source=chatgpt.com)

But these likely overlap strongly with FRIDAY's canonical memory.

Likely:

```text
REFERENCE_ONLY
```

unless evaluation finds a concrete feature we're missing.

---

# 8. Add a **Skill Arena**

This is important.

Suppose skills.rest gives us:

```text
16 research skills
29 Git skills
41 browser skills
20 Docker skills
```

Don't let an LLM choose based on descriptions.

Run:

```text
Research Skill A
Research Skill B
Research Skill C
```

on the same tasks.

Measure:

```text
accuracy
completion rate
tokens
latency
tool calls
retries
security
maintenance
```

Then:

```text
winner → ACTIVE

others → ARCHIVED
```

FRIDAY could eventually automatically replace weak skills.

---

# 9. This becomes a complete **Capability Marketplace Fabric**

I would build:

```text
Capability Marketplace

Sources:
├── skills.rest
├── GitHub
├── NVIDIA Skills
├── MCP registries
├── Claude plugins
├── Codex skills
├── local skills
└── user-provided URLs

                   ↓

             Capability Forge

                   ↓

     Classify → Scan → Sandbox → Eval

                   ↓

             Capability Registry

                   ↓

                  FRIDAY
```

This is much better than having separate:

```text
Skill installer
MCP installer
Plugin installer
GitHub installer
```

One pipeline handles all of them.

---

# 10. Add `Capability Search`

Then when FRIDAY lacks something:

```text
User:
"Friday, convert this AutoCAD file."

FRIDAY:
capability search
    ↓
local catalog
    ↓
not found
    ↓
Capability Marketplace
    ↓
skills.rest / GitHub / MCP catalog
    ↓
candidate
    ↓
SkillSpector
    ↓
sandbox
    ↓
test
    ↓
activate
    ↓
perform original AutoCAD task
```

This connects directly to your earlier rule:

> If FRIDAY doesn't have a capability, it should be able to build/find one itself.

---

# 11. OpenShell can power the quarantine runtime

Here's where the pieces connect beautifully:

```text
External Skill
External GitHub repo
External MCP
       │
       ▼
Capability Forge
       │
       ▼
SkillSpector
       │
       ▼
NVIDIA OpenShell Sandbox
       │
       ├── restricted files
       ├── restricted network
       ├── no real credentials
       ├── restricted process
       └── audit
       │
       ▼
SkillEvaluator
```

OpenShell has policy-enforced filesystem/network/process boundaries and endpoint-bound credentials, which makes it an excellent candidate for the **dynamic behavior-testing stage** of Capability Forge. [GitHub](https://github.com/NVIDIA/OpenShell/blob/main/architecture/security-policy.md?utm_source=chatgpt.com)

That is much safer than evaluating random skills on the real Windows host.

---

# 12. My decisions on the two repositories

### `Louis-CFM/coucou`

```yaml
decision: EXTRACT

use:
  - agent activity UI
  - compact approvals
  - status overlay
  - drag/drop context
  - notifications

do_not_use_as:
  - FRIDAY brain
  - model gateway
  - separate user-facing product
```

### `NVIDIA/OpenShell`

```yaml
decision: HIGH_PRIORITY_WRAP_AND_EVALUATE

use:
  - EXEC-SBX-001 backend
  - coding worker sandbox
  - Capability Forge quarantine
  - network policy
  - filesystem isolation
  - process isolation
  - credential injection
  - sandboxed model routing

priority:
  VERY HIGH
```

OpenShell is significantly more important architecturally.

---

# 13. Give the builder this directive

```text
CAPABILITY MARKETPLACE DIRECTIVE

Build a unified Capability Marketplace behind FRIDAY.

Sources initially:

- skills.rest
- GitHub URLs
- NVIDIA skills
- existing local skills/MCP/plugins

DO NOT bulk-activate catalog content.

Index all discoverable metadata.

For relevant candidates:

1. download into quarantine;
2. pin source + commit/hash;
3. classify:
   SKILL / MCP / PLUGIN / CLI / SDK / SERVICE / REPO /
   MODEL / REFERENCE;
4. deduplicate against existing capabilities;
5. run SkillSpector static scan;
6. run semantic review where necessary;
7. inspect dependencies/licenses;
8. run dependency/CVE/secret checks;
9. execute dynamic evaluation inside OpenShell or another
   approved sandbox;
10. measure actual capability value;
11. register only passing candidates;
12. create/update local FRIDAY skill where useful;
13. store provenance and SBOM;
14. monitor upstream changes;
15. quarantine on unexpected change;
16. garbage-collect stale/duplicate/degraded capabilities.

Never install every skills.rest item into the active skill catalog.

The catalog may contain all metadata.
The active runtime should contain only the best verified capabilities.

Use NVIDIA SkillSpector as a mandatory pre-activation security gate.

Evaluate NVIDIA OpenShell as the preferred dynamic sandbox/backend
for untrusted imported capabilities and scoped development workers.

Evaluate Coucou only as a source of FRIDAY UI/notification/
agent-status patterns.

All accepted third-party functionality remains behind FRIDAY
Capability Fabric.

Normal user sees FRIDAY only.

Developer Mode preserves:
source
version
license
hash
scanner findings
SBOM
backend
health
permissions.
```

That is the architecture I would use.

The key shift is:

> **We don't “install all skills.” We let FRIDAY know about all of them, and install only the ones that survive relevance, security, duplication and value tests.**

That gives you effectively **hundreds of thousands of discoverable capabilities without carrying hundreds of thousands of active skills**.
