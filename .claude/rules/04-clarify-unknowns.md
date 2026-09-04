---
description: "When you don't have the information to make a decision, ask. Do not guess or invent."
globs: "*"
alwaysApply: true
---

# Rule 4: Clarify Unknowns

**When this applies:** every team.

If a task requires information you don't have — business rules, user preferences, system constraints, terminology meaning — **stop and ask**. Use the AskUserQuestion tool when available.

## What counts as an unknown worth asking about

- A name, route, or identifier you cannot find in the codebase.
- A business rule with no documented source (e.g. "what counts as a duplicate?").
- An architectural choice the user hasn't expressed a preference on.
- A constraint that could change the design materially (e.g. "is this multi-tenant?").

## What does *not* count

- Implementation detail you can read in the code with one or two greps.
- Convention the codebase already shows (style, naming, file layout).
- Standard library behavior you can verify with the docs.

## Anti-pattern: invent and proceed

Inventing a rule and proceeding wastes effort if your guess was wrong, and produces code that contradicts the user's intent. The cost of a clarifying question is small; the cost of rebuilding on a wrong assumption is large.

If you must proceed without an answer (e.g. user is unavailable), state your assumption explicitly in the plan or commit message: "Assumed X because Y; revisit if wrong."
