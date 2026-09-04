# Failure Recovery and Rollback

Lifecycle:
`NOT_INSTALLED → INSTALLED → STARTING → HEALTHY → DEGRADED → FAILED → RECOVERING → HEALTHY/DISABLED`.

On failure:
1. identify exact provider/component;
2. preserve parent ObjectiveRun;
3. do not blindly repeat a mutating action;
4. reconnect first;
5. restart only affected provider if safe;
6. inspect side effects before retry;
7. route to equivalent fallback only if semantics remain valid;
8. record rework cost and defect.

Every slice gets:
- pre-slice git checkpoint;
- pinned upstream SHA;
- config backup;
- uninstall/disable command;
- process cleanup command;
- data location;
- migration/rollback note.

Optional-provider failure must not force a whole-Friday rollback unless shared core was damaged.
