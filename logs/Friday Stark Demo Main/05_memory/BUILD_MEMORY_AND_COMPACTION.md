# Build Memory and Compaction Survival

The builder must not repeat Friday's old failure mode: losing the objective after compaction/restart/tool switching.

## Durable build artifacts

### BUILD_RUN_STATE.json
Store:
- current phase and slice;
- active upstream;
- last verified action;
- next exact action;
- changed files;
- tests run/result;
- live gates;
- blockers;
- active background processes;
- restart required?;
- rollback commit/tag.

### UPSTREAM_STATUS.json
For each of 21 upstreams:
- pinned commit/version/license;
- audit state;
- install state;
- adapter state;
- upstream tests;
- MCP tests;
- live journey;
- known limits;
- final status.

### DECISION_LEDGER.md
Record architectural decisions and rejected alternatives so a new Claude/Fable context does not relitigate them.

### BUG_LEDGER.md
Every real defect:
reproduction → root cause → patch → regression test → negative/mutation control → live proof.

## Before compaction/session switch
1. write the four durable records;
2. record `git status`;
3. record active process names/PIDs where relevant;
4. write one exact `next_action`;
5. checkpoint meaningful code;
6. do not create a 20k-token narrative recap.

## Resume
1. read BUILD_RUN_STATE;
2. inspect git status;
3. verify production/process health;
4. read only the active upstream brief;
5. continue from next_action;
6. do not repeat the full 21-repo audit.

## Retrieval order during builds
Current run state → relevant Skill → codebase-memory/Graft → GBrain → exact source → fresh web research only if needed.
