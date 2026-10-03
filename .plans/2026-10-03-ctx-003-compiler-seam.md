# Plan: CTX-003 - the objective engine reaches workers only through the context compiler

**Date:** 2026-10-03
**Status:** In Progress (owner ruling 2026-10-03 is the approval; see `.build/packet.md`)
**Services:** objective-engine, mcp-tools

## Objective
Critical facts are an input constraint on compaction. The compiler
(`model_gateway.compile_working_set`) may shorten representation but never
drops, alters or omits a critical fact; a package that cannot keep them fails.

## Features
### 1. Fact-preserving compiler
- **Service:** objective-engine (`friday/model_gateway.py`)
- `compile_working_set(facts=...)`: every fact line travels in a P0 section,
  over-budget raises `ContextOverBudget`, the result is validated with
  `compaction.validate`, telemetry reports `facts_kept` and what was dropped.
### 2. Contract seam
- `compaction.ContextRequest` is what the engine emits (data only, no prompt).
- `model_gateway.compile_request(store, request)` turns it into facts + working set.
### 3. Callers go through it
- `development.for_request` is the only production entry to `for_goal`.
- `hermes_delegate` (objective path) and `model_infer` take their binding /
  recorded split from the compiler instead of re-deriving it.

## Assumptions (owner unavailable)
- "Active blocker" is the existing BLOCKED-task fact (`ALREADY FAILED:`); no
  tenth fact kind is added (PRD §19.8 names nine).
- Quote markers (`> `) are representation; validation reads the content with
  them removed, so a multi-line literal is not refused for being quoted.
- No capability yet calls `development.for_request`; registering one needs a
  policy-category decision and is left as a stated gap.

## Technical notes
Tests: `tests/test_working_set.py`, `tests/test_compaction.py`,
`tests/test_envelope_hermes.py`.
