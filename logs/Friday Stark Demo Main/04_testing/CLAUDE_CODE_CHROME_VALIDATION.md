# Claude Code and Claude Chrome Validation

## Claude Code
Use for:
- code inspection;
- source edits;
- focused tests;
- exact diff review;
- process/config inspection;
- adapter/MCP validation.

Sequence:
git status → focused reproduction → smallest patch → focused tests → restart if needed → production health → real Friday journey.

## Claude Chrome / browser validation
Use only when real UI behavior must be proven:
1. connect to the correct Friday/LiveKit UI;
2. verify current tab/session;
3. use semantic/accessibility references;
4. send a user command through Friday;
5. observe one meaningful acknowledgement;
6. verify final user-visible result;
7. refresh/reconnect and prove state persistence;
8. never use real banking or credential pages as test fixtures.

A click return is not success. Success = verified resulting UI state.

## Production process check
Before/after live UI runs, verify the permanent single-logical-Friday-process rule and provider singleton rules.
