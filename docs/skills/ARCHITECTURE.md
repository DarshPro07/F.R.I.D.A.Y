# Skill Intelligence — architecture

FRIDAY treats **memory** as facts ("what is true, what the owner prefers"),
**skills** as procedures ("how we correctly do this class of task"),
**capabilities** as approved tools, and **plugins** as installed code. The
skill layer is the policy, lifecycle and evaluation intelligence around
Hermes's skill system; Hermes remains the physical store.

```
                        FRIDAY
                          │
                 friday.skills (this layer)
                          │
   ┌──────────────┬───────┼────────┬──────────────┐
   ▼              ▼       ▼        ▼              ▼
candidates     curator  lint    evaluator    dependencies
 (WHEN)        (clean)  (safe)  (WORKS?)     (WHICH changed)
   └──────────────┴───────┼────────┴──────────────┘
                          ▼
                      lifecycle  ──── registry (SQLite ledger)
                          │
              ┌───────────┼───────────┐
              ▼           ▼           ▼
           router     publisher   hermes_link
        (lazy load,  (local /    (skills.external_dirs
         tokens)      private /   in Hermes config)
                      public)
                          │
                    Hermes skills
        HERMES_HOME/skills  +  external_dirs  +  taps
```

## What Hermes already does (reused, not rebuilt)

Hermes 0.14 owns `HERMES_HOME/skills/<name>/SKILL.md` with `references/
templates/ scripts/ assets/`, the frontmatter (`name`, `description`,
`version`, `author`, `license`, `platforms`, `metadata.hermes.*`),
progressive disclosure (`skills_list` → `skill_view` → a reference file),
`skill_manage` (create / patch / edit / delete / write_file), its
`skills_guard` security scanner for hub installs, `skill_usage` counters
and active/stale/archived states, a background curator, and taps
(`hermes skills tap add owner/repo`, `hermes skills publish`).

FRIDAY writes skills in exactly that format. Everything FRIDAY adds sits
under `metadata.friday`, which Hermes ignores:

```yaml
metadata:
  friday:
    scope: repository            # global | user | organization | project | repository | session | temporary
    task: build-friday-exe       # the task two skills may compete for
    publish: private             # local | private | public
    depends_on: [friday/launcher.py, packaging/FRIDAY.spec, pkg:pyinstaller]
    tests: ["pytest tests/test_launcher.py"]
    triggers: [build the exe, rebuild friday.exe]
    never_for: [book a table]
```

## Modules (`friday/skills/`)

| Module | Answers | Notes |
|---|---|---|
| `contracts.py` | the vocabulary | states, transition table, scopes, tiers, risk classes, `SkillRecord`, `SkillProposal`, `EvaluationReport` |
| `files.py` | what is on disk | Hermes-format read/write, atomic writes, discovery (Hermes's skip rules), content hash, snapshot/restore, tier roots |
| `registry.py` | what FRIDAY knows | SQLite ledger via `friday.dbconn.ledger_connection`: skills, history, versions, usage, dependencies, evaluations |
| `lint.py` | is it well-formed and safe | deterministic; PASS/WARN/FAIL with codes; secret, privacy, injection, destructive-command scanners; risk classification from content |
| `candidates.py` | when a skill should exist | objective trace → NO_SKILL or a proposal; the ladder's criteria and evidence rule |
| `curator.py` | make it a procedure | incident noise out, imperative steps, dedupe, size, merge near-duplicates |
| `dependencies.py` | which change invalidates it | declared surface → fingerprints; git change → NEEDS_REVALIDATION; nothing else moves it |
| `evaluator.py` | does it still work, how well | lint, risk, trigger, non-trigger, procedure (subprocess), tokens → `EvaluationReport`; quality score |
| `lifecycle.py` | the state machine | the one door to ACTIVE is recorded evidence; versions, rollback, quarantine |
| `router.py` | which skill a task needs | metadata-level ranking, lazy body loading, token accounting, conflict resolution |
| `publisher.py` | where it may go | LOCAL / PRIVATE_GIT / PUBLIC_GIT in tap layout; public is policy-gated |
| `hermes_link.py` | Hermes sees FRIDAY's skills | `skills.external_dirs` planned by default, applied on request, backed up |
| `bridge.py` | memory ↔ skill | facts out of a skill into memory; repeated procedure memories into a proposal |
| `plugins.py` | LEVEL 3 discovery through the `friday.plugins` entry-point group; loading only for names in `FRIDAY_PLUGINS_ALLOW`, shape validated | `importlib.metadata` |
| `change_type.py` | which layer a symptom's fix belongs to: MEMORY, SKILL, CODE, CONFIG, POLICY, TEST, DOCUMENTATION | — |
| `cli.py` | the owner's console | doctor, sync, list, lint, activate, revalidate, rollback, publish, select, hints, propose, rescore, facts, outcome, usage, diff, builtins, adopt, upgrade-builtin, hermes-link |

The BUILT_IN tier ships `friday-operate` (how to start, stop, diagnose
and update the packaged FRIDAY and use this console), with its own
`scripts/check.py` as the procedure test. A built-in is read-only where it
ships; `adopt NAME` copies it into the user tier, where it can be edited,
and `upgrade-builtin NAME` brings that copy up to a newer shipped version
(replacing an untouched copy, merging the shipped procedure's new lines
under the owner's edits otherwise), evaluated like any update and rolled
back on failure. A skill name is unique across tiers: after adoption the
registry points at the editable copy and the shipped directory is its
upstream.

## Tiers (all in Hermes layout)

```
BUILT_IN         friday/skills/builtin/              shipped with FRIDAY, read-only
LOCAL_USER       %LOCALAPPDATA%\FRIDAY\skills\user\   the owner's private procedures
PROJECT          <repo>/skills/                      git-tracked; doubles as a tap root
SHARED_PRIVATE   $FRIDAY_SKILLS_PRIVATE_DIR          a checkout of the private tap's skills/
PUBLISHED_PUBLIC $FRIDAY_SKILLS_PUBLIC_DIR           a checkout of the public tap's skills/
```

`FRIDAY.exe -m friday.skills.cli hermes-link --apply` adds the existing
tiers to Hermes's `skills.external_dirs` so Hermes lists and loads them
like its own.

## Token discipline

Level 0 is one line per skill (`name: description [tags]`, ~20–70 tokens
each). The router ranks those; only the selected skills' `SKILL.md` is read
(Level 1), and a reference file only when the procedure asks for it
(Level 2). `Selection.index_tokens` and `loaded_tokens` report what
entered context. For the first real skill: 73 tokens of index, 1,358 for
the body when selected, 0 for an unrelated request.

## Where it plugs into FRIDAY

- `friday.skill_ladder.SkillLadder` records candidates with the same
  criteria (`candidates.py` calls it).
- `friday.memory_promotion` gates facts the bridge extracts.
- `friday.hermes_bridge.TaskBundle.skill_hints` carries
  `router.hints_for(query)` so Hermes loads at most those.
- `friday.self_upgrade.KERNEL_PATHS` remain untouchable: a skill can
  never modify policy, trust or the judge; a skill is text, never code.
