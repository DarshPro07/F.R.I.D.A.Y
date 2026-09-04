# Third-party integration status

**Generated** by `scripts/integration_matrix.py`. Do not hand-edit -
run the script. `--check` fails when a clone has neither a descriptor
nor an explicit REFERENCE_ONLY demotion, because the unclassified
state is what let two thirds of the clones go unnoticed.

- clones: **46**
- integrated: **30**
- unclassified: **0**

| Upstream | Licence | Pin | Mode | Status | Detail |
|---|---|---|---|---|---|
| agency-agents | MIT | `3c9588880b7c` | SKILL | INTEGRATED | role_recipes (SKILL) |
| agent-reach | MIT | `06c202b03400` | CLI | INTEGRATED | agent_reach_transcribe (CLI) |
| agenticseek | GPL-3.0 | `ae57a2357745` | CLI | INTEGRATED | agenticseek_cli (CLI) |
| agentmemory | Apache-2.0 | `e04ba88819c3` | REFERENCE_ONLY | REFERENCE_ONLY | Apache-2.0 memory over MCP; NON_NEGOTIABLE 11 (no duplicate memories) - measure vs store.py before adopting |
| agents-team | MIT | `7f2f83927109` | SKILL | INTEGRATED | agents_team_pack (SKILL) |
| anthropic-cybersecurity-skills | Apache-2.0 | `1b3f6b228698` | SKILL | INTEGRATED | security_skills (SKILL) |
| anythingllm | MIT | `35c58d89907e` | SIDECAR | INTEGRATED | anythingllm_research (SIDECAR) |
| auto-company | NONE | `ebfab9b4bd5f` | SKILL | INTEGRATED | company_playbooks (SKILL) |
| awesome-claude-code-subagents | MIT | `009544a05267` | SKILL | INTEGRATED | claude_subagents (SKILL) |
| awesome-harness-engineering | CC0-1.0 | `6a146704c167` | SKILL | INTEGRATED | harness_templates (SKILL) |
| bolt.diy | MIT | `2e254ac19a69` | REFERENCE_ONLY | REFERENCE_ONLY | standalone app on WebContainer (commercial licence for production); Friday builds via Hermes instead |
| browser-use | MIT | `28670f720f63` | REFERENCE_ONLY | REFERENCE_ONLY | Friday has one browser policy (browser_capability + netguard); a second driver duplicates it. Read for its DOM-extraction patterns |
| cline | Apache-2.0 | `1d5d3b005575` | REFERENCE_ONLY | REFERENCE_ONLY | OPTIONAL_WORKER in executor_router.KNOWN (its own registry, its own lifecycle); a fabric descriptor too would be two registrations of one thing - test_upstream_lock forbids it |
| codebase-memory-mcp | MIT | `e678722746d4` | MCP | INTEGRATED | codebase_memory (MCP) |
| crewai | MIT | `fcdeb3d98d85` | REFERENCE_ONLY | REFERENCE_ONLY | orchestration framework; Friday owns orchestration. Its role patterns feed the roles family via role_recipes |
| diagram-design | MIT | `ac490fd1ac4b` | SKILL | INTEGRATED | diagram_design (SKILL) |
| firstmate | MIT | `0866a7702345` | REFERENCE_ONLY | REFERENCE_ONLY | an agent distro: shell/skill conventions, no single runnable entry point at the pin (bin/ is a set of hooks) |
| graft | MIT | `268e30d750b5` | SIDECAR | INTEGRATED | graft (SIDECAR) |
| graphiti | Apache-2.0 | `8b61fce9f003` | ADAPTER | INTEGRATED | graphiti_memory (ADAPTER) |
| gstack | MIT | `a3749bfa4b0f` | SKILL | INTEGRATED | gstack_process (SKILL) |
| i-have-adhd | MIT | `cbe69fb83c08` | SKILL | INTEGRATED | adhd_mode (SKILL) |
| maxun | AGPL-3.0 | `4fc597d9ca7e` | SIDECAR | INTEGRATED | maxun_scraping (SIDECAR) |
| medusa | MIT | `6a2fce501f3b` | SIDECAR | INTEGRATED | medusa_commerce (SIDECAR) |
| mem0 | Apache-2.0 | `19cb89aff472` | ADAPTER | INTEGRATED | mem0_memory (ADAPTER) |
| munder-difflin | MIT | `b91a49fc0896` | REFERENCE_ONLY | REFERENCE_ONLY | benchmark fixtures; read for shape, never run |
| no-ai-slop | MIT | `d30eddb9e045` | SKILL | INTEGRATED | no_ai_slop (SKILL) |
| nodriver | AGPL-3.0 | `a71cda374651` | REFERENCE_ONLY | REFERENCE_ONLY | AGPL browser driver; same one-browser rule as browser-use |
| onlook | Apache-2.0 | `423e2e924366` | REFERENCE_ONLY | REFERENCE_ONLY | visual code editor app; read for design-to-code patterns |
| open-design | Apache-2.0 | `df84ae5b9ebf` | SKILL | INTEGRATED | open_design (SKILL) |
| open-lovable | MIT | `69bd93bae7a9` | REFERENCE_ONLY | REFERENCE_ONLY | app-cloning web app; Friday's ui_browser.study_url covers the useful half natively |
| open-notebook | MIT | `a7de90d38aaf` | SIDECAR | INTEGRATED | open_notebook_research (SIDECAR) |
| openhands | MIT core; enterprise/ PolyForm Free Trial | `d104ffdc33e7` | REFERENCE_ONLY | REFERENCE_ONLY | control layer with its own agent loop and UI; Friday is the single orchestrator. The pinned clone is agent-canvas (TypeScript UI), not a callable worker |
| openmausbot | Apache-2.0 | `a3d2870528fb` | SKILL | INTEGRATED | mausbot_skills (SKILL) |
| openmontage | AGPL-3.0 | `cd9f3c1f0336` | SIDECAR | INTEGRATED | openmontage_media (SIDECAR) |
| openviking | AGPL-3.0 | `cd8580c6f8a5` | REFERENCE_ONLY | REFERENCE_ONLY | reference implementation; no operation Friday needs yet |
| openwork | SPLIT_MIT_PLUS_RESTRICTED_SUBTREE | `fda0babb6c76` | REFERENCE_ONLY | REFERENCE_ONLY | MIT core + EE subtree; its MCP model is what the fabric already is |
| openworker | MIT | `fb1bfc627201` | CLI | INTEGRATED | openworker_cli (CLI) |
| pipecat | BSD-2-Clause | `5ff3201996ba` | REFERENCE_ONLY | REFERENCE_ONLY | voice pipeline framework; LiveKit is Friday's voice path |
| postiz | AGPL-3.0 | `0f1647f7491a` | SIDECAR | INTEGRATED | postiz_social (SIDECAR) |
| prompt-master | MIT | `2bd92518e26b` | SKILL | INTEGRATED | prompt_master (SKILL) |
| scientific-agent-skills | MIT | `895b4be37ef0` | SKILL | INTEGRATED | science_skills (SKILL) |
| scrapling | BSD-3-Clause | `458e2a2ac909` | ADAPTER | INTEGRATED | scrapling_parse (ADAPTER) |
| smartstore | AGPL-3.0 | `3b7d986ecb6c` | SIDECAR | INTEGRATED | smartstore_commerce (SIDECAR) |
| strix | Apache-2.0 | `cbb0f57058a9` | CLI | INTEGRATED | strix_pentest (CLI) |
| ultron | MIT | `a65306f5a956` | REFERENCE_ONLY | REFERENCE_ONLY | read for its planner structure only |
| vane | MIT | `7dc5d088f726` | REFERENCE_ONLY | REFERENCE_ONLY | read for its scheduler patterns only |
