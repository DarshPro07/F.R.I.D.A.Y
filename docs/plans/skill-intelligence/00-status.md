# Skill Intelligence — plan and status

Owner direction (2026-09-15): build the FRIDAY intelligence layer *around*
Hermes's existing skill system; do not build a second skill store.

    Hermes skills            = the physical procedural store
    FRIDAY Skill Intelligence = policy + lifecycle + evaluation on top

## Reality (audited 2026-09-15, read-only)

**Hermes 0.14.0** (`%LOCALAPPDATA%\hermes\hermes-agent`, home
`%LOCALAPPDATA%\hermes`, 25 bundled skill categories under `skills\`):

- Store: `HERMES_HOME/skills/<category>/<name>/SKILL.md` plus
  `references/ templates/ scripts/ assets/`; frontmatter `name`,
  `description`, `version`, `author`, `license`, `platforms`,
  `metadata.hermes.{tags,related_skills,requires_*,fallback_for_*,config}`,
  `required_environment_variables`. Progressive disclosure:
  `skills_list` (index) → `skill_view(name)` → `skill_view(name, path)`.
- `skills.external_dirs` in `config.yaml` adds directories Hermes scans
  (currently `[]`); local skills win on name clash; missing dirs ignored.
- `skill_manage` (create/patch/edit/delete/write_file/remove_file) with
  validation: name `^[a-z0-9][a-z0-9._-]*$` ≤ 64, description ≤ 1024,
  SKILL.md ≤ 100k chars, files ≤ 1 MiB, subdirs restricted.
- `tools/skills_guard.py`: security scanner (exfiltration, injection,
  destructive commands, supply chain), verdict safe/caution/dangerous;
  `guard_agent_created: false` on this machine.
- `tools/skill_usage.py`: view/use/patch counters, states
  active/stale/archived, pinned, agent-created marker; `agent/curator.py`:
  background review (archive/consolidate/patch), never deletes.
- Taps: any GitHub repo laid out `skills/<name>/SKILL.md`;
  `hermes skills publish … --to github --repo owner/repo`,
  `hermes skills tap add owner/repo`; hub installs are scanned.
- What Hermes does **not** have: versioned snapshots with rollback,
  dependency tracking against a codebase, revalidation on code change,
  trigger/procedure tests, a quality score from *Friday's* outcomes, a
  private→public publication policy.

**Friday already has** (reuse, do not duplicate):

- `friday/skill_ladder.py` — `SkillLadder`: candidate capture refused
  without a promotion criterion + evidence; CANDIDATE → VALIDATED /
  REJECTED / DEPRECATED, versioned rows in `skill_candidates`.
- `friday/memory_promotion.py` — handoff `skill_candidates` → procedure
  candidate files under `data/skills_candidates/`.
- `friday/handoff.py` — `skill_candidates`, `memory_candidates`.
- `friday/forge.py` + `forge_runtime.py` — executable capabilities
  (LEVEL 3) with static gate, isolated verification, registry + history.
- `friday/capabilities.py` — LEVEL 2 capability records with risk/scope.
- `friday/fabric_adapters/_skillpack.py` — LEVEL 1 read-on-demand
  markdown skill packs (integration_mode SKILL).
- `friday/self_upgrade.py`, `selfdev.py`, `promotion.py` — the code
  change pipeline and `KERNEL_PATHS` (policy, trust, access, vault, the
  judge itself, AGENTS.md) that no self-modification may touch.
- `friday/hermes_bridge.py` — `TaskBundle.skill_hints` ("load at most
  these; do not enumerate all skills").
- `friday/dbconn.py` — per-call `ledger_connection`; every ledger is
  enumerated by `tests/test_dbconn.py` and must not leak a handle.

## Decision: `friday/skills/` package

| Module | Owns | Reuses |
|---|---|---|
| `contracts.py` | states, scopes, tiers, risk classes, extension levels, `SkillRecord` | Hermes name/size rules |
| `files.py` | SKILL.md read/write in Hermes format, tier directories, content hash | pyyaml frontmatter, `skill_manage` limits |
| `registry.py` | SQLite ledger: skills, versions (snapshots), history, dependencies, usage | `dbconn.ledger_connection`, forge history pattern |
| `lint.py` | deterministic linter + secret/privacy scan → PASS/WARN/FAIL | patterns aligned with `skills_guard` |
| `candidates.py` | `SkillCandidateDetector`: objective trace → NO_SKILL or proposal + draft | `SkillLadder` criteria + ledger |
| `curator.py` | strip incident noise, parameterise machine paths, dedupe/merge, structure | — |
| `dependencies.py` | `SkillDependencyGraph`: declared deps → fingerprints; git change → NEEDS_REVALIDATION | git |
| `evaluator.py` | trigger / non-trigger / procedure / security / token tests → report; quality score | pytest via subprocess |
| `router.py` | metadata-level selection, lazy load with token accounting, conflict detection | `skill_hints` seam |
| `lifecycle.py` | guarded transitions, semver, rollback, quarantine; verification is external evidence | — |
| `publisher.py` | LOCAL / PRIVATE_GIT / PUBLIC_GIT in tap layout; public gated by policy | Hermes taps |
| `hermes_link.py` | `skills.external_dirs` linkage (diff by default, apply on request, backup) | Hermes config |
| `bridge.py` | memory ↔ skill: facts to memory, repeated procedures to proposals | `memory_promotion` |
| `cli.py` | `friday skills doctor / list / lint / test / revalidate / publish / rollback` | launcher shim |

Physical tiers (all Hermes-compatible `…/<name>/SKILL.md`):

    BUILT_IN      <package>/skills_builtin/         shipped with FRIDAY, read-only
    USER          %LOCALAPPDATA%\FRIDAY\skills\user\
    PROJECT       <repo>/skills/                    git-tracked with the code; doubles as a tap root
    ORGANIZATION  FRIDAY_SKILLS_PRIVATE_DIR         a checkout of the private tap
    PUBLISHED     FRIDAY_SKILLS_PUBLIC_DIR          a checkout of the public tap (staging only)

Security invariants carried into tests: a skill cannot mark itself
verified (the evaluator's report is the only evidence the lifecycle
accepts); risk comes from classification, never from the skill's own
claims; PROJECT/REPOSITORY scope can never be published publicly;
public publication needs standing authorization in policy; kernel paths
are never touched; approval timeout is denial.

## Golden journeys (tests/test_skills_*.py)

SK-01 proposal from a verified complex trace · SK-02 approve → lint →
test → ACTIVE · SK-03 relevant code change → NEEDS_REVALIDATION ·
SK-04 unrelated README change → stays FRESH · SK-05 fake secret →
QUARANTINE, publication blocked · SK-06 bad new version → previous stays
ACTIVE · SK-07 conflict → deterministic or explicit · SK-08 project skill →
public publish refused · SK-09 irrelevant → full content never loaded ·
SK-10 malicious instruction → policy authoritative · PUB-01 create →
private git publish → fresh clone → discovered → procedure test passes.

First real skill: `skills/friday-windows-exe` (how to build and validate
FRIDAY.exe) with `depends_on` over the launcher, spec, build and validate
scripts and `tests: [tests/test_launcher.py]` — the co-evolution journeys
run against real git changes to those files.

## Status

| Piece | Status |
|---|---|
| Audit of Hermes and Friday seams | done (above) |
| Package, registry, files, lint | done — `friday/skills/{contracts,files,registry,lint}.py` |
| Candidate detector, curator | done — `candidates.py`, `curator.py` |
| Dependency graph, evaluator, lifecycle | done — `dependencies.py`, `evaluator.py`, `lifecycle.py` |
| Router, publisher, hermes link, bridge, cli | done — `router.py`, `publisher.py`, `hermes_link.py`, `bridge.py`, `cli.py` |
| Golden journeys SK-01…SK-10, PUB-01 | done — `tests/test_skills_golden.py`, 17 passed; units `tests/test_skills_units.py`, 24 passed; `SkillRegistry` in the handle-leak ledger test |
| First codebase skill + co-evolution proof | done — `skills/friday-windows-exe/` activated through the CLI with a real pytest procedure test (42 passed); SK-03/SK-04 prove revalidation on a declared dependency and indifference to a README |
| Docs (`docs/skills/*.md`), ledger | docs done (ARCHITECTURE, LIFECYCLE, SECURITY, PUBLISHING, CODEBASE_SKILLS, PLUGIN_MODEL); ledger in `docs/product/EXE_AND_SKILL_SYSTEM_LEDGER.md` |

Also done after the first pass: the console gained `propose` (memory
statements → detector → curator → quarantined DRAFT in the user tier),
`rescore` (quality floor → DEGRADED → rollback), `hints`
(`router.hints_for`, the `TaskBundle.skill_hints` seam) and `facts`
(a skill's `## Facts` → memory promotion); three dead helpers were
removed; `friday/reachability.py` (a kernel path — owner to approve)
gained one `DECLARED["friday/skills"]` entry so the ratchet knows the
console is a production path the reference walk cannot follow. The whole
console was exercised from the packaged `FRIDAY.exe -m friday.skills.cli`
on the final build (sync, activate with a real procedure test through the
shim, hints, propose, rescore, doctor).

Second pass (2026-09-16): a BUILT_IN tier with `friday-operate` (its own
`scripts/check.py` as the procedure test), `adopt` / `builtins` /
`upgrade-builtin` (replace an untouched copy, merge the shipped procedure
under the owner's edits, evaluate, roll back on failure), usage outcomes
and statistics (`outcome`, `usage`, `rescore`), `diff` against the
verified snapshot, the codebase fingerprint stamped at activation
(`codebase_id`, `codebase_commit`), the plugin entry-point model
(`friday/skills/plugins.py`), the `ChangeType` classifier, and a read-only
`/api/skills` endpoint in the control room. Tests:
`tests/test_skills_extensions.py`, `tests/test_launcher_doctor.py`,
`tests/test_ui_server_skills.py`.

Not done, on purpose: no MCP tool exposes the skill layer to the voice
brain yet (the `friday/tools` registry and capability records are a
separate owner); `friday.hermes_bridge` does not yet call
`router.hints_for()` for `skill_hints` (one-line seam, left for the
objective-engine owner); the LLM-assisted curator pass is a hook, not a
requirement.
