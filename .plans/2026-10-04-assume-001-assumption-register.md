# Plan: ASSUME-001 assumption register

**Date:** 2026-10-04
**Status:** Done
**Services:** objective-engine (requirements.py, objectives.py, store.py), mcp-tools (toolsets/objectives.py wiring only)

## Objective
Assumptions Friday makes on the person's behalf carry origin, confidence,
consequence, how to validate them, and a state, are persisted against the
objective run, and a high-impact unvalidated one is validated before any work
that depends on it starts.

## Design
- `requirements.Assumption` (frozen): statement, origin, confidence, consequence,
  validation, state. `Question` stays the model-side input and gains optional
  confidence/consequence/validation; `Understanding.assumptions` stays a list of
  `Question` (existing callers and tests), `Understanding.register()` builds entries.
- `requirements.due_for_validation(register)`: UNVALIDATED and (consequence HIGH, or
  confidence LOW with consequence MEDIUM+), highest consequence first.
- `store.assumption_register` table (additive, keyed run_id + key) with save/list/set-state.
- `objectives.compile_objective(assumptions=...)`: persists the register; when any entry is
  due, the run opens WAITING_QUESTION, an `assumption.validation_due` event is written before
  any task row, and every task starts QUEUED. `resolve_assumption` records the outcome and
  releases the run once nothing is due or refuted (the snapshot then promotes QUEUED tasks).
- A held run follows the waiting-run convention: `next_wake` is None and `blocker` names
  what to confirm. The person releases it through `objective_resume(run_id, assumption=<key>,
  state=VALIDATED|WAIVED|REFUTED)`; `objective_status` lists each assumption with its key. No
  new MCP tool: `resolve_assumption` had no production caller, so a held run would have stranded.
- `ObjectiveContract.assumptions` stays strings (unchanged).

## Assumption (revisit if wrong)
"High-impact" means consequence HIGH, because neither the packet nor the repo defines a
threshold. A REFUTED entry that would have been due keeps the run held (it must be replanned,
not released).

## DOD
- [x] tests/test_assumption_register.py (10 tests) fail before (no Assumption, save_assumption or resolve_assumption), pass after
- [x] test_reachability, store, objective, golden, continuity and envelope suites stay green (826 passed, 1 skipped across 49 files)
