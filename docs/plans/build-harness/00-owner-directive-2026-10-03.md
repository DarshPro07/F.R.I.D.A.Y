# Owner directive 2026-10-03 (verbatim)

Pasted by the owner on 2026-10-03 in reply to the controller's report of the 2026-10-02 overrun ($53.09 against
the $45 day cap) and its three open questions (SEC-005, CTX-003, MCP-001). Copied as received; the cited links
are the owner's.

---

Do **not raise the cap retroactively**. The cap already did its job by exposing a real accounting hole. Raising it after the overrun would weaken the control you just fixed.

Also, it is already **October 3 locally**, so the "after midnight" reset point has passed. Resume paid work **now only if the ledger shows a fresh October 3 budget window and correctly includes every paid call made since midnight**. Start with `CTX-002` and `APPROVAL-001`; run them in parallel only if the resource governor, write-set check, and cost budget all permit it.

The reason to be strict here is that agent-team costs can scale very quickly: Anthropic's long-running harness examples show large quality gains but also order-of-magnitude cost increases, and their 16-agent compiler experiment consumed about $20,000 across ~2,000 Claude Code sessions. Parallelism is useful, but cost governance has to be part of the scheduler rather than an afterthought. [Anthropic](https://www.anthropic.com/engineering/harness-design-long-running-apps?utm_source=chatgpt.com)

I'd make one more harness fix before allowing multiple paid agents: **a Spend Lease**, analogous to your memory lease.

```text
remaining_daily_budget
        ↓
estimate max invocation cost
        ↓
reserve spend lease
        ↓
recheck daily cap
        ↓
launch worker/verifier/advisor
        ↓
record authoritative actual cost
        ↓
release unused reservation
```

That prevents two parallel agents from both seeing "$6 left" and each starting a $5 job. Count **workers + verifiers + advisors + rescues + manual invocations** against the same daily ledger. I'd use a **$40 soft stop / $45 hard ceiling** until you prove accounting has no delayed/unlogged calls.

For the three decisions blocking the queue, here are my rulings.

**SEC-005: enforce the filesystem boundary; do not accept advisory-only limits.** You do not necessarily need to modify Hermes internals if FRIDAY can enforce it below Hermes. Each worker/session gets an actual runtime-scoped filesystem envelope. If Hermes cannot reliably enforce that itself, remove/bypass its unrestricted file tools and expose only FRIDAY's jail-backed file capability.

The path should be:

```text
Worker Session
   ↓
Execution Envelope
   ↓
FRIDAY File Capability
   ↓
canonicalize path
   ↓
resolve symlink/junction/reparse target
   ↓
allowed-root check
   ↓
READ / WRITE
```

On Windows, test ordinary paths, `..`, symlinks/junctions, UNC paths, case variations, alternate separators and redirected known folders. A prompt saying "stay in this directory" is not isolation. Anthropic's containment work makes the same broader point: capable agents need actual blast-radius controls, not merely behavioral instructions. [Anthropic](https://www.anthropic.com/engineering/how-we-contain-claude?utm_source=chatgpt.com)

**CTX-003: Objective Engine should hand off to the existing context/compiler layer, not directly to a model or worker.** Lock the boundary as:

```text
Objective Engine
      ↓
Objective Contract
      ↓
WorkPacket / ContextRequest
      ↓
CortexOS / Context Compiler
      │
      ├─ CriticalFactSet
      ├─ literals
      ├─ authority/policy
      ├─ L0/L1/L2 retrieval
      ├─ compaction
      └─ provenance
      ↓
Model Gateway / Worker
```

`CriticalFactSet` belongs to the Objective Contract side and is an input constraint to compaction. The Context Compiler may reduce representation, but it may not silently alter or omit critical facts, exact literals, authority state, acceptance criteria, active blockers or required provenance. That gives CTX-003 a clean ownership boundary and prevents Objective OS from becoming a prompt-builder itself.

**MCP-001: pin identity, but do not make brittle leaf-certificate pinning mandatory for every HTTPS MCP server.** For FRIDAY-managed local `stdio` servers, pin the executable/package/source version, command, hash, server metadata and tool-schema fingerprint. For remote MCP, pin the registered origin/endpoint, authenticated server relationship, advertised server identity, protocol compatibility and tool/description fingerprints. Modern MCP also carries server identity metadata, and current transports include Host/Origin protections against DNS-rebinding-style attacks. [Model Context Protocol](https://ts.sdk.modelcontextprotocol.io/v2/migration/support-2026-07-28?utm_source=chatgpt.com)

Use optional **SPKI/public-key pinning** only for deployments where you control the endpoint or specifically need that stronger binding. Do not globally pin leaf TLS certificates, because routine certificate rotation would turn legitimate updates into outages.

Servers configured outside FRIDAY should enter as:

```text
EXTERNAL_DISCOVERED
        ↓
EXTERNAL_UNVERIFIED
        ↓
fingerprint + scan + schema capture
        ↓
user/admin approval if required
        ↓
ACTIVE
```

They should **not be callable merely because Claude Code, another app, or a config file already knows about them**.

And if any of these changes unexpectedly:

```text
endpoint/origin
server identity
executable/hash
tool schema
tool description
permissions
auth scope
```

then:

```text
ACTIVE
→ SUSPENDED
→ REVALIDATE
```

That matches the current MCP direction toward explicit server identity and strict transport/session validation while still giving FRIDAY a stronger application-level trust boundary. [Model Context Protocol](https://ts.sdk.modelcontextprotocol.io/v2/migration/support-2026-07-28?utm_source=chatgpt.com)

So the next build wave I'd authorize is:

```text
October 3 budget ledger check
        ↓
fresh daily paid budget <= $45
        ↓
┌────────────────────┬────────────────────┐
│ CTX-002             │ APPROVAL-001       │
│ normal coding model │ normal coding model│
└────────────────────┴────────────────────┘
        only if RAM + write sets permit parallelism

then:

SEC-005
→ hard filesystem envelope

CTX-003
→ Objective → Context Compiler boundary

MCP-001
→ managed/external registration + identity/schema pinning

then:
next dependency-ready PRODUCT P0
```

Use Opus only if one of those normal workers genuinely fails or hits architectural ambiguity. Do not spend Opus money on the first pass.

And I would make the daily-cost rule non-negotiable from now on:

> **No chain, manual rescue, verifier, advisor, scout or subagent exists outside the daily budget ledger.**

That closes the exact path that produced the $53.09 overrun.
