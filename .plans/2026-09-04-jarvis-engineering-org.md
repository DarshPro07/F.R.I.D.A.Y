# Plan: Jarvis engineering organisation

**Date:** 2026-09-04
**Status:** Done (PARTIALLY_VERIFIED — see docs/plans/jarvis-engineering-org/07-final.md)
**Services:** voice-brain, mcp-tools, fabric, objective-engine

## Objective
Make Friday narrate progress, route around capped providers, hand Hermes a real task
contract, run specialists as Hermes profiles on the native kanban and as Claude project
subagents with private memory, stop blind retries with failure fingerprints, and promote
only evidence-backed learning into canonical memory. Full design and evidence live in
`docs/plans/jarvis-engineering-org/` (00–07) and `docs/adr/ADR-001`, `ADR-002`.

## Features
### Feature 1: Task contract — Service: objective-engine — Agent: friday-objective-engineer
- Small win: a development run's bundle reaches Hermes with ACCEPTANCE CRITERIA.
- DOD: [ ] impl [ ] tests [ ] verified
### Feature 2: Progress digest + Work panel — Service: voice-brain — Agent: friday-voice-engineer
- Small win: a fake work run's events are spoken as milestones and a 3-min digest on both paths.
### Feature 3: Quota-aware routing — Service: objective-engine — Agent: friday-objective-engineer
- Small win: a fake 429 cools the provider and the next candidate takes the job, spoken.
### Feature 4: Fingerprints + handoffs — Service: objective-engine — Agent: friday-objective-engineer
- Small win: the same failure twice changes strategy; the third blocks with evidence.
### Feature 5: Claude specialists — Service: mcp-tools — Agent: friday-tools-engineer
### Feature 6: Hermes team (profiles + kanban) — Service: objective-engine — Agent: friday-objective-engineer
### Feature 7: Memory promotion — Service: objective-engine — Agent: friday-objective-engineer
### Feature 8: Golden journey + verification — Agent: friday-qa-engineer, friday-tech-lead

## Cross-service dependencies
S1 → S2 → S4 → S5 → S7 (same files); S3 and S6 run in parallel with them; S8 last.

## Technical notes
No second engine/board/registry/memory (ADR-001). Kanban beneath one objective task. Digest
composed from events only. All schema changes additive.
