# Full-Depth Upstream Layout

```text
third_party/
  upstream/
    openhands/
    maxun/
    browser-use/
    agent-reach/
    graft/
    agency-agents/
    codebase-memory-mcp/
    openmontage/
    open-notebook/
    no-ai-slop/
    i-have-adhd/
    strix/
    vane/
    agenticseek/
    scrapling/
    gstack/
    anythingllm/
    pipecat/
    postiz/
    crewai/
    cline/
  patches/
    <upstream>/
  licenses/
    <upstream>/
  UPSTREAM_LOCK.json
```

Rules:
- clone full upstream source;
- pin exact commit/tag;
- keep upstream tree as upstream-owned;
- Friday adapters live outside upstream;
- modifications to upstream become explicit commits/patches;
- one-upstream-at-a-time update process;
- never overwrite local changes while updating.
