# Build-harness backlog candidates

The owner's rule (master controller §22-25, v10 §27-28): during P0 burn-down nothing here is installed or
adopted because it looks useful. A candidate leaves this list only when a measured harness gap points at it and
a paired end-to-end benchmark (HARNESS-EVAL-001) shows it beats the current baseline. Sources:
`00-owner-research-2026-10-02.md`, `00-owner-directive-2026-10-01.md`, `00-owner-directive-2026-10-02b.md`.

| Candidate | Gap it might close | Status |
|---|---|---|
| pytest-testmon | iteration test selection (TEST-IMPACT-001); a change to a widely imported module selected ~all of tests/ for DOMAIN-001 | spike queued (BH-OPT-05 #2) |
| pytest-xdist | checkpoint wall time | only with a governor-chosen worker count, never `-n auto` |
| Serena | orientation (ORIENT-001/002) | bake-off after the capsule's A/B data |
| codebase-memory-mcp | orientation | bake-off |
| Aider-style bounded repo map | orientation (fallback) | bake-off |
| GitNexus | impact analysis / execution flows | benchmark only, isolated (its setup writes hooks/configs) |
| Repomix, code2prompt | static capsule generation | one of the two at most, if the capsule needs it |
| Ponytail | code minimalism / cost | A/B candidate (JetBrains measured ~10% lower cost on its workload) |
| RTK | output compression | rejected: FRIDAY's own measurement and JetBrains' paired benchmark; reopen only on a paired FRIDAY benchmark |
| Caveman | recoverable compression | candidate behind CortexOS, not stacked on other compressors |
| Context7 | version-specific library docs | on demand later, not per task |
| Cua | ComputerUseFabric backend | after P0 (owner ruling A5) |
| Windows Job Objects per worker | exact peak memory of a worker's whole process tree; admission order available -> commit -> lease -> recheck -> job object -> spawn -> track peak -> class p95 -> release (owner, 2026-10-02) | build only when a measured run shows psutil tree sampling under-counting a worker's children |
| Memory resource notifications (CreateMemoryResourceNotification) | event-driven wake instead of polling free memory | when polling cost or latency becomes a measured problem |
| Gas Town + Beads, Emdash, Claude Squad, DeepAgents, OpenHands SDK | orchestration / worktree UX | design references only - FRIDAY's kernel already covers them |
| SWE-agent ACI, Superpowers, wshobson/agents, ECC, Awesome Claude Code | worker tool design, review flow, skills | pattern sources; cherry-pick single skills only after scan + eval |
| HyperResearch, SkillSpector, SkillEvaluator, Laya, Jev, MiniCPM, Claude-Red, A2A, WebMCP, AgentGateway | product capabilities | product backlog after P0 = 0, separate scout wave |

Registry checks (`/find-skills`, 2026-09-30 and 2026-10-02): no published skill covers repo-map/code context,
test-impact selection, harness cost accounting or memory admission - those gaps need code, not instructions.
