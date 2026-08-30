# Third-party capability integration — status matrix

Canonical root: `E:\friday-tony-stark-demo-main`. Branch: `feature/capability-fabric-v1`.
Baseline before this work: `c91902a`, bundled and verified to
`D:\friday-baseline-protection\2026-08-29\pre-capability-fabric-v1\`.

Pin and license data in this document is **generated**, not typed:
`scripts/upstream_lock.py` reads each clone under `third_party/upstream/` and
writes `third_party/UPSTREAM_LOCK.json`. Run it with `--check` to fail when the
lock has drifted from the clones. Do not hand-edit the lock.

## The architecture already existed

The single most important finding of this pass: Friday already implements the
capability fabric this work was scoped to build. `friday/fabric.py` provides
the descriptor, registry, lazy activation, health, routing and fallback;
`friday/fabric_adapters/` is the adapter package, discovered with pkgutil so an
adapter that fails to import is an `UNAVAILABLE` provider rather than a broken
Friday. Two invariants are enforced in `Provider.__post_init__` rather than in
review:

- **copyleft implies isolation** — an AGPL/GPL provider that declared `ADAPTER`
  or `BUILTIN` raises `FabricError` at import time. Maxun, OpenMontage, Postiz
  and agenticSeek *cannot* be linked into Friday's process by a later mistake.
- **an unpinned upstream is an unaudited upstream** — a provider naming an
  upstream without a commit raises.

So the correct work is writing adapters into that contract. Building a second
fabric would have been the worst available outcome.

## Status

`IMPLEMENTED` means a descriptor is registered, the adapter is tested, and the
pin matches the lock. It does **not** mean the upstream is installed on this
machine — an absent upstream is a health state, not a build failure.

| # | Upstream | License (verified from clone) | Pinned | Mode | Status |
|---|----------|-------------------------------|--------|------|--------|
| 1 | codebase-memory-mcp | MIT | `e678722746d4` | MCP | **IMPLEMENTED** |
| 2 | i-have-adhd | MIT | `cbe69fb83c08` | SKILL | **IMPLEMENTED** |
| 3 | no-ai-slop | MIT | `d30eddb9e045` | SKILL | **IMPLEMENTED** |
| 4 | agency-agents | MIT | `3c9588880b7c` | SKILL | **IMPLEMENTED** |
| 5 | graft | MIT | `268e30d750b5` | SIDECAR | **IMPLEMENTED** (this pass) |
| 6 | gstack | MIT | `a3749bfa4b0f` | SKILL | **IMPLEMENTED** (this pass) |
| 7 | scrapling | BSD-3-Clause | `458e2a2ac909` | ADAPTER (parse-only) | **IMPLEMENTED** (this pass) |
| 8 | browser-use | MIT | `28670f720f63` | **REFERENCE_ONLY** (revised) | DECIDED — not installed |
| 9 | agent-reach | MIT | `06c202b03400` | **REFERENCE_ONLY** (revised) | DECIDED — not installed |
| 10 | vane | MIT | `7dc5d088f726` | **REFERENCE_ONLY** (revised) | DECIDED — not installed |
| 11 | maxun | **AGPL-3.0** | `4fc597d9ca7e` | SIDECAR (isolated) | DEFERRED — 5 services incl. a browser |
| 12 | openhands | MIT | `d104ffdc33e7` | **REFERENCE_ONLY** (revised) | DECIDED — is a control layer, not a worker |
| 13 | cline | Apache-2.0 | `1d5d3b005575` | OPTIONAL_WORKER | **DECLARED** in executor_router (no builder) |
| 14 | open-notebook | MIT | `a7de90d38aaf` | **REFERENCE_ONLY** (revised) | DECIDED — not installed |
| 15 | anythingllm | MIT + **AGPL subdir** | `35c58d89907e` | **REFERENCE_ONLY** (revised) | DECIDED — no capability remains |
| 16 | pipecat | BSD-2-Clause | `5ff3201996ba` | **REFERENCE_ONLY** (revised) | DECIDED — LiveKit is the voice path |
| 17 | openmontage | **AGPL-3.0** | `cd9f3c1f0336` | SIDECAR (isolated) | DEFERRED — heavy media app, no pipeline yet |
| 18 | postiz | **AGPL-3.0** | `0f1647f7491a` | SIDECAR (isolated) | DEFERRED — no social objective yet |
| 19 | strix | Apache-2.0 | `cbb0f57058a9` | **REFERENCE_ONLY** (revised) | DECIDED — 2nd agent brain; gap noted |
| 20 | crewai | MIT | `fcdeb3d98d85` | **REFERENCE_ONLY** (revised) | DECIDED — Friday owns orchestration |
| 21 | agenticseek | **GPL-3.0** | `ae57a2357745` | **REFERENCE_ONLY** (revised) | DECIDED — local-worker pattern only |
| 22 | openviking | **AGPL-3.0** | `cd8580c6f8a5` | SIDECAR | DECIDED — L0/L1/L2 pattern; GBrain canonical |
| 23 | agentmemory | Apache-2.0 | `e04ba88819c3` | MCP (deferred) | DECIDED — measure vs store.py before adopting |
| 24 | diagram-design | MIT | `ac490fd1ac4b` | SKILL | **IMPLEMENTED** (this pass) |
| 25 | scientific-agent-skills | MIT (4 skills licence-blocked) | `895b4be37ef0` | SKILL | **IMPLEMENTED** — 159 offered, 4 blocked |
| 26 | awesome-harness-engineering | CC0-1.0 | `6a146704c167` | REFERENCE_ONLY | **USED** — docs/architecture/HARNESS_AUDIT.md |
| 27 | anthropic-cybersecurity-skills | Apache-2.0 (all 25 subtrees) | `1b3f6b228698` | SKILL (restricted) | **IMPLEMENTED** — scope-gated |
| 28 | munder-difflin | MIT | `b91a49fc0896` | REFERENCE_ONLY | DECIDED — agency patterns; Friday above workers |
| 29 | openwork | MIT core + **EE subtree** | `fda0babb6c76` | REFERENCE_ONLY | DECIDED — fabric already covers its MCP model |
| 30 | prompt-master | MIT | `2bd92518e26b` | SKILL | **IMPLEMENTED** — Friday (writing) + Claude Code skill |
| 31 | open-design | Apache-2.0 (35 templates own MIT) | `df84ae5b9ebf` | SKILL | **IMPLEMENTED** — Friday presentation (Claude-Design-style) |
| 32 | ultron-by-sagar-builds | MIT | `a65306f5a956` | **REFERENCE_ONLY** (design) | DECIDED — Next.js/Three.js/MediaPipe orb UI; orb ported as CSS, app not vendored |

**30 / 30 researched** (prompt-master added after the original 29 on request; a
Claude Code skill for generating paste-ready prompts, wired as a Friday
`writing` provider and installed at `~/.claude/skills/prompt-master/`).

**29 / 29 researched.** Rows 1–21 came from the build pack. Rows 22–29 were
cloned, pinned and license-audited in this pass; the set difference is computed
by `scripts/new_upstream_set.py`, not recalled, and asserted to equal 8 by
`tests/test_upstream_lock.py`. Full per-repo detail — default branch, tag,
manifests, subtree licenses, proposed mode and reason — is in
`docs/integrations/NEW_UPSTREAM_SET.json`.

None of rows 22–29 are integrated. They have no adapter, no descriptor, and
nothing in Friday depends on them.

## License findings in the new eight

Three vendoring blockers, all found by reading the clones:

**scientific-agent-skills is MIT at the root and proprietary in four skills.**
`skills/docx`, `skills/pdf`, `skills/pptx` and `skills/xlsx` each carry
Anthropic terms that explicitly forbid extracting the materials from the
Services, retaining copies outside them, reproducing them, and creating
derivative works. Those four cannot be vendored into Friday under any mode.
A skill adapter must allowlist the MIT skills rather than glob the directory.

**openwork's root LICENSE is a split grant, not a single license.** Everything
outside `/ee` is MIT; `/ee` is the OpenWork Enterprise Edition License, which
is source-available and gates production use above five users on a paid
subscription. The classifier reports the root as
`SPLIT_MIT_PLUS_RESTRICTED_SUBTREE` deliberately: calling the whole repository
"enterprise" would be as wrong as ignoring the carve-out.

**openviking is AGPL-3.0**, so `fabric.Provider` will refuse any importing mode
for it. Proposed as SIDECAR; its value is the L0/L1/L2 hierarchical context
model as a pattern for MemoryFabric, with GBrain remaining canonical.

One correction to an assumption carried into this pass: **munder-difflin's
bundled illustrations are not non-commercial.** The subtree carries an MIT
LICENSE plus a `NOTICE.md` requesting attribution to the author. The practical
decision is unchanged — the artwork is irrelevant to Friday and none of it is
imported — but the constraint is attribution, not a commercial-use ban.

`anthropic-cybersecurity-skills` was the cleanest of the eight: Apache-2.0 at
the root and in all 25 inspected skill subtrees, with no nested surprise.

## License findings

Verified by reading `LICENSE` out of each clone, not from a policy document.
Declared and verified agree for all 21 except where noted.

**AnythingLLM carries an AGPL-3.0 component inside an MIT repository.**
`00_governance/LICENSE_AND_DISTRIBUTION_POLICY.md` lists AnythingLLM under
"MIT" with no caveat. The clone contains `open-computer/LICENSE`, which is
AGPL-3.0. `scripts/upstream_lock.py` now flags this automatically as a
`license_warning` in the lock. If AnythingLLM is ever integrated, that
subdirectory is subject to the copyleft rule regardless of the root license.

**OpenHands has no `enterprise/` directory at the pinned commit.** The policy
warns that OpenHands core is MIT while `enterprise/` is PolyForm Free Trial.
At `d104ffdc33e7` that directory does not exist, so the concern does not apply
to this pin. It would apply again on any future bump — which is what the
`--check` mode is for.

**Copyleft set:** Maxun, OpenMontage, Postiz (AGPL-3.0) and agenticSeek
(GPL-3.0). The fabric refuses to register any of these in an importing mode.
The remaining nested licenses found are vendored tree-sitter grammars and CI
config, filtered as noise by the generator.

## What was implemented this pass

`friday/fabric_adapters/graft.py` — Graft as the second `code_intelligence`
provider, alongside `codebase_memory`. It answers orientation questions
(`map`, `ask`, `blast`, `skeleton`, `callers`, `grep`) where CBM answers exact
structural ones, and declares `codebase_memory` as its fallback.

Two upstream behaviours are refused rather than inherited:

- `graft init` is **not** an operation. It rewrites `.claude/settings.json`,
  `AGENTS.md` and `.mcp.json`. Friday does not edit the operator's agent
  configuration as a side effect of answering a code question. Every query
  command works without it. A test asserts `init` is unreachable.
- `graft build --deep` is **not** reachable. It runs an LLM pass over every
  file under a provider key; spend is execution-economics' decision. A test
  asserts `--deep` cannot be smuggled through the free `build`.

Telemetry is opt-out upstream and is forced off (`DO_NOT_TRACK=1`) on every
subprocess. One upstream side effect is declared rather than hidden: `graft
build` appends `graft/` to the repository's `.gitignore` and has no flag to
suppress that.

## Next, in the build pack's own order

**Slice 1 is complete** (codebase-memory-mcp, Graft, gstack). Next is Slice 2:
Scrapling, browser-use, Agent-Reach, Vane, and Maxun as an isolated sidecar.

gstack is registered as `gstack_process` in the `roles` family, offering 37
workflows and withholding 23 with a stated reason each. Its browser workflows
are withheld deliberately: Friday has one browser policy, and gstack documents
overriding the host's. Routing uses the upstream's own `triggers:` frontmatter,
weighted by token rarity across the catalogue, so a plain request reaches the
right workflow without slash-command syntax.

The eight uncloned repositories need the same treatment rows 1–21 already had
— clone, pin, read the license from the clone, write a brief — before any of
them can be given a descriptor. `Provider.__post_init__` will refuse them
until they are pinned, which is the intended outcome.
