---
description: "Enforces plan-first development. Never start implementation before saving a plan to .plans/."
paths:
  - "friday/**"
  - "server.py"
  - "agent_friday.py"
  - "ui/**"
  - "scripts/**"
alwaysApply: false
---
# Rule 1: Plan-First Development

**When this applies:** every team. Skip only for single-file bug fixes, typos, and trivial config changes.

**Never start implementation before saving a plan.**

Before writing any code for a task that involves 2+ files or crosses service boundaries:

1. Create a plan file in `.plans/` at the project root.
2. Filename: `YYYY-MM-DD-{kebab-case-title}.md`.
3. Present the plan to the user for approval before starting.

## Plan template

```markdown
# Plan: {Title}

**Date:** {YYYY-MM-DD}
**Status:** Draft | Approved | In Progress | Done
**Services:** voice-brain, mcp-tools, fabric, objective-engine

## Objective
{What are we building and why?}

## Features
### Feature 1: {Name}
- **Service:** {one of voice-brain, mcp-tools, fabric, objective-engine}
- **Agent:** {agent name}
- **Small win:** {minimum deliverable that proves value}
- **DOD:**
  - [ ] Implementation complete
  - [ ] Tests pass
  - [ ] Verified working

## Cross-service dependencies
{Which features must complete before others can start?}

## Technical notes
{Architecture decisions, risks, trade-offs}
```

## When to skip

Single-file bug fixes, typo corrections, and config changes do not need a plan. Use judgment — if it's truly trivial, just do it.
