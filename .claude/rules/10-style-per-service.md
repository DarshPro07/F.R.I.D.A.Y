---
description: "Match the style of the service you are touching. Don't drag your favorite conventions into someone else's codebase."
globs: "*"
alwaysApply: true
---

# Rule 10: Style Per Service

**When this applies:** any team with 2+ languages or 2+ services in `voice-brain, mcp-tools, fabric, objective-engine`. Skip for monorepo-of-one.

When working in a service, match its existing conventions:

- **Naming** (camelCase / snake_case / PascalCase / kebab-case) follows the language and the service's existing files. Don't introduce a new convention because it's what you'd write in your home language.
- **File layout** matches the service. If `bridge/` already groups by feature, keep grouping by feature. Don't split into "controllers/", "services/", "models/" because that's what your last project did.
- **Test layout** matches the service. Tests next to source, or tests in a `__tests__/` directory, or tests in a `_test.go` file — pick what's already there.
- **Error handling** matches the service: error returns vs exceptions vs Result types — use what the service uses.

## Why

Style consistency is most of what makes a codebase readable. A single file written in a foreign style sticks out, and the next person to touch it (you, in three months) has to context-switch.

## When you need a new convention

If the existing convention is genuinely wrong for your task (e.g. the service uses callbacks and you need a Promise), do the work in a new file *or* convert the surrounding area in a focused refactor PR. Don't half-convert.
