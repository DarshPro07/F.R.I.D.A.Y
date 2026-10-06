# Plan: OBJ-004 narrowed batch retry must not reuse the first attempt's idempotency key

**Date:** 2026-10-06
**Status:** Done
**Services:** objective-engine

## Objective
OBJ-004: a three-item batch with one failed item retries only that item, and a
restart duplicates nothing. The in-process half already held at base. The
defect is the live path: `ContinuousTaskExecutor._dispatch` sends
`O.idempotency_key(objective, task)` on every attempt, and the production port
(`agent_friday.objective_port`) records a `succeeded` call - including a batch
that reports one failed item - as `call.returned` under that key and replays it.
The retry narrowed to the failed item is a different request, but it gets the
first attempt's partial result back, so the failed item is never retried.

## Features
### Feature 1: key carries the pending item set
- **Service:** objective-engine
- **Agent:** friday-objective-engineer
- **Small win:** a narrowed retry gets a new key; a crash re-run of the same
  subset keeps its key; tasks without `items` keep `run:task` unchanged.
- **Change:** `objectives.idempotency_key(run, task, items=None)` plus
  `objectives.idempotency_key_task(key, run)` as the one parser of the format;
  `continuous._dispatch` passes `arguments.get("items")`;
  `agent_friday.objective_port._ledger_ids` uses the parser.
- **DOD:**
  - [x] `tests/test_batch_restart.py` narrowed-retry test red at base (calls == [[alpha,bravo,charlie]]), green after
  - [x] `test_durable_side_effects.py` (key unchanged without items) still green
  - [x] behaviour-contract, continuity, chaos-restart suites green (108 passed); 19 objective/journey files 404 passed, 1 skipped

## Technical notes
Assumed (no owner available): the key suffix is a digest of the sorted
`str(item)` list, joined after `#`. Task ids are `[A-Za-z0-9_-]+`, so `#`
cannot occur in one. Revisit if a task id grammar change allows `#`.
