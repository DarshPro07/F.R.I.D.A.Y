# MCP Verification Plan

Every MCP integration must pass all applicable layers.

## M0 Discovery
- executable/version resolves;
- expected server only;
- no surprise global config edits;
- tools/list exposes intended tools only.

## M1 Protocol
- initialize handshake;
- tools/list;
- one harmless tools/call;
- invalid argument returns structured error;
- timeout/cancel works.

## M2 Friday/Hermes registration
- capability registry sees provider;
- H router can select it;
- WorkRun records capability/provider;
- correct origin/session/objective correlation.

## M3 Real operation
Use a disposable real task. Verify the resulting file/page/data/state independently.

## M4 Crash
Kill MCP/server during an operation.
Expected:
- exact layer diagnosed;
- ObjectiveRun survives;
- reconnect/restart only affected provider;
- mutation is not blindly repeated.

## M5 Restart
Restart Friday + Hermes + MCP. Existing configured integration returns without manual terminal repair.

## M6 Secret isolation
A fake-secret marker must appear nowhere in:
- prompt/transcript;
- WorkRun model context;
- logs;
- GBrain;
- Skills;
- UI reports.

## M7 Process singleton
No stale duplicate provider process after restart.

## M8 Token/tool economy
Tool schema is lazy-loaded; capability does not inject huge instructions on unrelated turns.
