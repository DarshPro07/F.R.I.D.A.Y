---
description: "A task is not done until tests pass, the feature works end-to-end, the build succeeds, and the code is formatted."
globs: "*"
alwaysApply: true
---

# Rule 3: Definition of Done

**When this applies:** every team.

A task is NOT done until ALL of the following are met:

## 1. Tests pass

Write and run tests appropriate to the touched service. The team's CLAUDE.md should map service → test command. Default expectation: every change has at least one new or modified test that would have failed before the change.

## 2. Feature works end-to-end

For cross-service features, verify the full chain manually or with an integration test:
- Database query returns expected data.
- Backend endpoint serves it correctly.
- Frontend displays it (verify in browser or component test).

## 3. Build succeeds

Run the build/typecheck/lint for every service the change touched. CI must be green; a failing CI is a failing task.

## 4. Code formatted per service standards

- Lint passes for every touched file.
- No `any` / `unknown` / `interface{}` etc. without explicit justification in a comment.
- No `console.log` / `print` / `println` debugging artifacts.
- No hardcoded secrets, URLs, or credentials.

## 5. Acceptance criteria checked off

If the task came with acceptance criteria (e.g. from a GitHub issue), every criterion is checked. Add a "verified by" line per criterion when verification is non-obvious.
