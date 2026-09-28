# Skill lifecycle

```
DISCOVERED ─► DRAFT ─► STAGED ─► TESTING ─► ACTIVE ◄──────────────┐
      │         ▲        (lint)     (eval)      │                  │
      └► QUARANTINED ◄──────────────────────────┤ unsafe content   │ evidence
              │ lint passes                     │                  │
              └► DRAFT                          ├► NEEDS_REVALIDATION ─► ACTIVE (fresh) | STALE | BROKEN
                                                ├► DEGRADED ─► rollback ─► ACTIVE
                                                ├► DEPRECATED (terminal; revival = DRAFT)
                                                └► DISABLED (the owner's switch)
```

The full table is `friday.skills.contracts.TRANSITIONS`; the registry
refuses anything not in it and records every move in `skill_history`
with who asked and why (the same shape as the forge's history table).

## Where a skill starts

| Origin | Trust | First state |
|---|---|---|
| shipped with FRIDAY | `built_in` | DRAFT |
| written or approved by the owner | `owner` | DRAFT |
| written by FRIDAY/Hermes from a trace | `agent` | QUARANTINED |
| installed from a tap or the hub | `community` | QUARANTINED |

Quarantine is left only through `stage` (lint with no FAIL). Nothing
goes from QUARANTINED to ACTIVE directly.

## The one door to ACTIVE

`SkillLifecycle.activate(name, report)` requires:

1. the report is for this skill and passed;
2. the registry recorded that exact report (its nonce) — produced by
   `SkillEvaluator.evaluate`, never constructed by a caller or a file;
3. the report's content hash equals the skill directory's hash *now*;
4. the classified risk is not a high-risk class when the trust is `agent`;
5. the current state may reach ACTIVE.

Then: transition, snapshot of exactly what was verified under
`STATE_ROOT/skills/.versions/<name>/<version>/`, a `skill_versions` row
marked verified, dependency fingerprints refreshed, quality rescored.

A `metadata.friday.state: ACTIVE` line in a SKILL.md is a lint FAIL
(`self_verification`), not a state.

## Revalidation after a code change

`SkillDependencyGraph.revalidate_after_change(paths)` marks every ACTIVE
skill whose declared `depends_on` matches a changed path
NEEDS_REVALIDATION — a state that stays *selectable* (the procedure is
probably still right) but is no longer *verified*. `lifecycle.revalidate`:

- fingerprints unchanged since verification → FRESH (ACTIVE, no test run);
- otherwise evaluate → pass: ACTIVE with new fingerprints; fail: BROKEN.

A README typo never touches a skill that did not declare the README.
`friday skills revalidate --since <commit>` reads `git diff --name-only`
plus the working tree and does the above for every affected skill.

## Versions and rollback

`update(name, bump=patch|minor|major)`: stamps the new version in the
frontmatter, re-reads tests/dependencies/triggers from the file, stages,
evaluates. Pass → ACTIVE at the new version with its own snapshot. Fail →
the last verified snapshot is restored and re-activated through the same
evidence check (its recorded evaluation is replayed); the failed content is
gone from disk, the refusal is in the history.

`rollback(name, to_version=None)` does the restore on request.
`degrade_if_needed(name)` marks DEGRADED below the quality floor (0.4)
and rolls back when a verified previous version exists.

## Quality

`SkillEvaluator.quality`: 0.5 × success rate (usage outcomes recorded by
FRIDAY), 0.2 × (1 − correction rate), 0.2 × freshness (verified within
30 days = 1, decaying to 0 at 180), 0.1 × token efficiency. Arithmetic
over recorded outcomes; no single model verdict decides it.

## Console

```
FRIDAY.exe -m friday.skills.cli sync           register / refresh what is on disk
FRIDAY.exe -m friday.skills.cli activate NAME  lint → stage → evaluate → ACTIVE
FRIDAY.exe -m friday.skills.cli revalidate     git change → affected skills → re-test
FRIDAY.exe -m friday.skills.cli rollback NAME
FRIDAY.exe -m friday.skills.cli doctor
FRIDAY.exe -m friday.skills.cli outcome NAME success|failure|corrected   feed quality
FRIDAY.exe -m friday.skills.cli diff NAME      what changed since the verified version
```

## Built-in skills across upgrades

A shipped skill is read-only where it ships. `adopt NAME` copies it into
the user tier and records which shipped content it came from
(`source: builtin:<hash>`). When a newer FRIDAY ships a changed version,
`builtins` shows `shipped version changed` and, if the owner edited the
copy, `edited locally`; `upgrade-builtin NAME` then:

- replaces an untouched copy with the new shipped content;
- merges the shipped procedure's new lines under the owner's edits
  otherwise, keeping the shipped frontmatter (its tests and version are
  what the new procedure was verified with);

and in both cases evaluates the result as an update — a failing shipped
test rolls the copy back to the last verified version, with the owner's
edits intact. Nothing is ever overwritten silently.
