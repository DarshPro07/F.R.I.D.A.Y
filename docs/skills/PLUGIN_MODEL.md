# Three extension levels

FRIDAY chooses the smallest level a job needs, and the levels never blur.

| Level | What it is | Where it lives | Who verifies it |
|---|---|---|---|
| **SKILL** | instructions + existing tools; procedural memory | `skills/<name>/SKILL.md` (Hermes format), tiers in `docs/skills/ARCHITECTURE.md` | `friday.skills.evaluator` (lint, triggers, procedure tests) |
| **CAPABILITY** | an approved tool: native, API, CLI, MCP, fabric adapter | `friday/capabilities.py` records, `friday/fabric_adapters/` | the capability's own verification scope, the policy engine at call time |
| **PLUGIN** | installed executable Python | `friday/forge.py` + `forge_runtime.py`: spec → static gate → isolated verification → registry (`forged_skills`) | the forge's static and isolated gates; enabled only with scopes |

## The boundary that matters

A `SKILL.md` can *describe* running a script, calling an API or
installing a package. It cannot *become* one:

- the router only ever returns text to a model; nothing in the skill
  layer imports, executes or registers code from a skill directory;
- `scripts/` under a skill are files Hermes may run through its own
  sandboxed tool with its own approval; FRIDAY's evaluator runs only the
  commands declared in `metadata.friday.tests`, with a timeout, from the
  repository root, through `subprocess` without a shell;
- a skill that needs code FRIDAY does not have is a proposal for a
  capability or a forge plugin, which go through their own gates
  (`friday.capabilities`, `friday.forge`), never through `skill_manage`;
- `friday.self_upgrade.KERNEL_PATHS` (policy, trust, access, vault, the
  judge, `AGENTS.md`) cannot be modified by any self-development path,
  and a skill has no path at all.

Hermes draws the same line in its authoring guide: a skill when
instructions plus existing tools are enough, a custom tool when precise
API, auth, binary or streaming integration is needed.

## How a plugin is found and loaded (`friday.skills.plugins`)

A plugin is a normal installed distribution that advertises an entry
point in the `friday.plugins` group:

```toml
[project.entry-points."friday.plugins"]
weather = "friday_weather:PLUGIN"
```

`discover()` reads that metadata and imports nothing; `load()` imports
one plugin only when its name is in `FRIDAY_PLUGINS_ALLOW`, then validates
the shape (`name`, `version`, `capabilities`). A refused or malformed
plugin is reported with the reason, never half-loaded. No code path in
FRIDAY imports a `.py` found under a skill directory.

## Deciding the level (`ChangeType`)

`friday.skills.change_type.classify(symptom)` returns the one layer with
its reasons and the runners-up, deterministically, so a caller that
disagrees can say so.

When something should improve, the fix belongs to exactly one layer:

| Symptom | Layer |
|---|---|
| a fact keeps being re-asked | MEMORY (`friday.memory_promotion`) |
| a multi-step procedure keeps being rediscovered | SKILL (`friday.skills.candidates`) |
| a tool is missing or wrong | CODE / CAPABILITY (`friday.selfdev`, `friday.forge`) |
| a default or threshold is wrong | CONFIG |
| an action should need approval | POLICY (kernel path; owner only) |
| the check that should have caught it is missing | TEST |

The candidate detector produces skills only for verified, non-trivial,
repeatable work; the ladder's criteria are the same four it always had.
