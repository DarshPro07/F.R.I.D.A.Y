# Slice 4, TRAJ-001: the run-level trajectory check

Date: 2026-09-27. Service: objective-engine only (`friday/continuous.py`, new `friday/trajectory.py`).

## Why

The budget (`objective_budget.check`) stops a run at a hard ceiling, and the task-level stuck detector
(`continuous.py`, `MAX_STRATEGY_CHANGES`) stops one task that keeps failing the same way. Neither sees a run
going wrong as a whole before a ceiling: several tasks failing in turn, one cause failing everything, policy
refusals piling up, spend running far ahead of progress, a side-effecting step being run again and again
(PRD §12).

## Design

- `trajectory.assess(events, tasks, *, spend)` is pure: it reads the run's own recorded events since the last
  start or resume (the window), the task rows, and the largest fraction of any budget spent, and returns
  ON_TRACK, NO_PROGRESS or DRIFTING with reasons. `trajectory.for_run(store, run_id)` gathers those.
- Signals, each a module constant so the thresholds are visible and tested:
  - no progress: 6 failed or retried attempts since the last success, across at least 2 tasks (one task alone
    is the task-level detector's);
  - same cause: one failure kind across 3 tasks since the last success;
  - policy near-misses: 3 refusals (ENVELOPE, APPROVAL_REQUIRED, BLOCKED) in the window;
  - burn: at least 75% of a budget spent with at most 25% of tasks done;
  - side effects: a write or external-action task started 3 times.
- The executor asks before dispatching ready tasks. A flagged run is PAUSED with the reasons as its blocker, a
  `trajectory.flagged` event, and one message to the person (continue, change the plan, or cancel). Resuming
  starts a new window, so the same history does not flag it again at once.
- Actions beyond pause (fresh worker, stronger model, lower concurrency) are later; this closes detection and
  the safe default action.

## Tests

- A synthetic long-horizon benchmark over generated traces: healthy runs (seeded, occasional failures that
  recover) never flag; each drift shape is flagged within a stated number of events of its onset.
- The executor parks a real run whose every call fails transiently across tasks, and a healthy run completes
  unflagged (negative control).
