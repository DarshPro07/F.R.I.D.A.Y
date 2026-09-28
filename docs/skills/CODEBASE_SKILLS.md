# Codebase skills

A codebase skill teaches FRIDAY how *this* repository works: the exact
build and test commands, the boundaries, the traps. It lives with the
code (`<repo>/skills/<name>/`), is scoped `repository`, declares the files
it depends on, and is revalidated when those files change — not when the
repository changes somewhere else.

The first one is `skills/friday-windows-exe/` (how to build and validate
FRIDAY.exe), with `references/pitfalls.md` holding the lessons behind
each rule.

## What goes in

- architecture boundaries, entry points, module responsibilities
- the exact commands: build, test, validate, release
- safe editing patterns and the invariants that must hold
- common failure modes and how they were diagnosed
- dependency quirks (which venv, what a re-sync removes)

What does **not** go in: the repository itself, transcripts, dates and
run ids (the curator strips them), anything the owner's `AGENTS.md`
already says (a skill that duplicates AGENTS.md is a WARN candidate for
the curator's merge).

## Fingerprint and dependencies

```yaml
metadata:
  friday:
    scope: repository
    depends_on:
      - friday/launcher.py
      - packaging/FRIDAY.spec
      - scripts/build_windows.py
      - tests/test_launch*.py        # a glob: any launcher test
      - pkg:pyinstaller              # a package: its version is the fingerprint
    tests:
      - pytest tests/test_launcher.py tests/test_state_root.py
```

At activation every dependency is fingerprinted (file hash, glob hash,
package version) and stored in `skill_dependencies`; the registry row
also carries `codebase_commit` when the caller knows it.

## Co-evolution with the code

```
code changes (git diff / working tree)
        ↓
SkillDependencyGraph.affected_by(paths)      only skills whose surface matched
        ↓
ACTIVE → NEEDS_REVALIDATION                   still selectable, no longer verified
        ↓
lifecycle.revalidate(name)
   fingerprints unchanged  → FRESH  (ACTIVE again, no test)
   changed + tests pass    → ACTIVE with new fingerprints
   changed + tests fail    → BROKEN (fix the skill or the code; then `activate`)
```

`FRIDAY.exe -m friday.skills.cli revalidate --since HEAD~1` runs the whole
chain. An unrelated README edit changes nothing (SK-04).

When the *skill* changes (the procedure was wrong or improved),
`lifecycle.update(name, bump=…)` versions it and either activates the
new version or rolls back to the last verified one (SK-06).

## Proven on the real repository

`friday-windows-exe` went through the CLI against a scratch registry:
lint PASS, five trigger phrases select it, two `never_for` phrases do
not, its procedure test ran the real launcher and state-root suites
(42 passed), token cost 1,358, ACTIVE with a recorded evaluation;
`select "please rebuild friday.exe after the launcher change"` loads it
and an unrelated request loads nothing.
