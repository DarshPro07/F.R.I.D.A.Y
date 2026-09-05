---
description: "Right model for right job — Opus for hard reasoning, Sonnet for code, Haiku for lookups."
globs: "*"
alwaysApply: true
---

# Rule 13: Model Selection

**When this applies:** every team. Especially load-bearing for orchestrators that pick which specialist (and which model) handles a request.

Pick the cheapest model that can do the job well.

## Defaults

- **Opus** — planning, architecture, multi-step reasoning, complex code review, meetings (`/meet`), trade-off analysis.
- **Sonnet** — code writing, refactoring, test generation, design execution, ordinary debugging.
- **Haiku** — status checks, lookups, simple formatting, documentation reads, single-shot transformations, anything read-only and quick.

## Rules of thumb

- Never use Opus for raw implementation — overspending and slower.
- Never use Opus for data retrieval — Haiku does it for cents.
- Move *up* a tier when a Sonnet/Haiku run would loop or hallucinate. Move *down* when an Opus call is doing rote work.
- For background monitors / always-on agents, prefer Haiku unless they reason on every event.

## Cite the choice

When a generated agent picks a non-default model, the agent's body should say *why* in one line: "Uses Opus because architecture decisions require multi-step trade-offs." This is what the evaluator's `model_fit` dimension scores.
