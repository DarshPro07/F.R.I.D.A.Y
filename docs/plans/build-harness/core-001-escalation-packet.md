CORE-001 ESCALATION (owner's packet, 2026-10-02 - docs/plans/build-harness/00-owner-directive-2026-10-02b.md, message 2)

STATUS: PARKED_AFTER_3_ATTEMPTS. DO NOT REDO COMPLETED INVESTIGATION.

UNRESOLVED ACCEPTANCE FAILURES:

1. The derived check executes in the environment that friday/execution.py:38 says is NOT a sandbox.
   Requirement: the derived check must execute through the required jail (FRIDAY's execution policy, the
   delegation workspace jail, no network).

2. The audit.sqlite3 root cause has not been investigated.
   Requirement: determine the root cause, reproduce it, fix it if it is a product defect, and add regression
   evidence.

3. A delegation with no acceptance text is still relayed as COMPLETE.
   Requirement: the absence of an acceptance contract must not produce COMPLETE (a missing contract is
   INVALID_CONTRACT / NEEDS_ACCEPTANCE_CRITERIA, never COMPLETE).

4. Without objective_run_id the verdict never reaches the objective store.
   Requirement: verdict persistence must be correlated to the objective run; without that correlation the
   objective-level completion cannot be committed.

Completion requires: acceptance contract exists AND evaluation produced a verdict AND the verdict is correlated to
objective_run_id AND the objective store persisted it.

FILES: only the files and symbols directly related to these four paths.

RULE: do not read any prior conversational transcript. Read only this packet, the current code, the relevant
tests and the evidence artifacts.

SUCCESS: all four conditions independently proven by tests (each test fails without its fix).
