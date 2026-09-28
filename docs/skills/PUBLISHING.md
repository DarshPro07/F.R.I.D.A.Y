# Publishing skills

"Publish" never means "make public". Three targets, one layout — Hermes's
tap layout, so a repository FRIDAY publishes to is directly usable with
`hermes skills tap add owner/repo`:

```
owner/repo
└── skills/
    └── <name>/
        ├── SKILL.md
        ├── references/
        ├── scripts/
        └── templates/
```

| Target | Where | Who may see it |
|---|---|---|
| `LOCAL` | `%LOCALAPPDATA%\FRIDAY\skills\user\<name>` | this machine |
| `PRIVATE_GIT` | `$FRIDAY_SKILLS_PRIVATE_DIR/<name>` (a checkout of the private tap's `skills/`) | the owner's team |
| `PUBLIC_GIT` | `$FRIDAY_SKILLS_PUBLIC_DIR/<name>` (a checkout of the public tap's `skills/`) | everyone |

The intended repositories: `DarshPro07/friday-skills-private` and
`DarshPro07/friday-skills`. Point the two variables at the `skills/`
directory of a local clone of each.

## The gate (`SkillPublisher.refusals`)

For every target: the skill is ACTIVE (evaluated, evidence on record).

For PRIVATE_GIT and PUBLIC_GIT additionally:

- lint in publication mode has no FAIL (no secrets, no profile paths,
  no e-mail addresses, no internal addresses);
- quality ≥ 0.5;
- the skill declares `publish: private` or `public` in `metadata.friday`.

For PUBLIC_GIT additionally:

- scope is not `project` or `repository` (codebase knowledge never goes
  public);
- `publish: public` is declared in the skill itself;
- a `license` is present;
- the skill was not installed from outside (`community` trust);
- **standing authorization** (`FRIDAY_SKILLS_PUBLISH_PUBLIC=1`) or an
  explicit approval for this skill. No approver, a failing approver, or a
  silent one is a refusal.

Every refusal is a history row on the skill with all the reasons.

## What a publish does

1. Copies the verified skill directory into the target (`export`).
2. For git targets: `git add`, `git commit -m "skill: <name> <version>"`
   in the checkout, authored `FRIDAY <friday@localhost>` (override with
   `FRIDAY_SKILLS_GIT_NAME/EMAIL`).
3. `git push` only when `FRIDAY_SKILLS_PUSH=1`: a local commit is
   reversible, a push is not.
4. Records the outcome in the skill's history.

```
FRIDAY.exe -m friday.skills.cli publish friday-windows-exe --target PRIVATE_GIT
```

## Round trip proven (PUB-01)

`tests/test_skills_golden.py::test_pub01_private_git_publish_and_fresh_clone_discovery`:
create → lint → real procedure test → ACTIVE → publish to a private tap
checkout (bare origin) with push → fresh clone → `files.discover` finds
it → registered under a second registry → its procedure test passes →
the router selects it.

## Hermes side

```
hermes skills tap add DarshPro07/friday-skills-private     # private: needs GITHUB_TOKEN
hermes skills install DarshPro07/friday-skills-private/<name>
```

Hermes runs its own security scan on install; FRIDAY's lint ran before
the publish. Both are deliberate.
