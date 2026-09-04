# Per-Upstream Adapter Implementation Prompt

You are implementing the active upstream into Friday.

Inputs:
- `02_upstreams/<slug>.md`
- pinned upstream source
- Capability Fabric contracts
- existing Hermes MCP/plugin/Skill/tool surfaces
- current BUILD_RUN_STATE

Do:
1. inspect the real pinned interface;
2. prefer official SDK/API/MCP/CLI over UI automation;
3. write the smallest typed adapter;
4. correlate execution with Hermes WorkRun + Friday ObjectiveRun;
5. expose health/cancel/timeout/start/stop;
6. declare permissions and secret dependencies;
7. lazy-load it;
8. verify external result rather than trusting returned success;
9. crash/restart it;
10. measure overlap against existing capability;
11. update state/status/bug/decision ledgers.

Never:
- hand the parent objective to the upstream;
- inject upstream-specific branching throughout Friday core;
- create another universal memory/router;
- expose credentials;
- mark READY from mocks.
