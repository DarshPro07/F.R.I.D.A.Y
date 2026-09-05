# ADR-001 — Friday owns the objective; Hermes owns durable execution; Claude specialists own bounded assignments

**Date:** 2026-09-04 · **Status:** accepted

## Decision
Friday (`objectives.py`, `continuous.py`, `dag.py`, `development.py`, `roles.py`) is the single
control plane and the top-level project state. Hermes (profiles, kanban, goal mode, `delegate`)
is the durable execution engine beneath an objective task. Claude Code project subagents are
bounded engineering specialists beneath the execution layer. No lower layer may create a Friday
objective, run its own global scheduler, keep its own canonical user memory, or grant itself
permissions.

## Consequences
- Hermes kanban is an execution mechanism for one objective task; it never recurses into a new
  objective. Friday polls it and verifies its output independently (`evaluation.verify`).
- Specialist memories (Hermes profile homes, Claude `memory: project` dirs) are private;
  only evidence-backed candidates cross into Friday's canonical memory through
  `memory_promotion`.
- Anything that duplicates an existing registry, board, memory or permission system is a bug,
  not a feature.
