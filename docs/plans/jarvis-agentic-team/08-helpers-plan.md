# 08 — "Connect all the GitHub code as helpers" (plan, 2026-09-03 13:30)

Owner: "connect all the github codes like a helper helping them … build this from end to
end … no care of time, use sub agents". His token rule (global CLAUDE.md) requires an
estimate and a yes before a job over three agents or 200K tokens. This is that estimate.

## Where the 46 clones stand (from `docs/integrations/INTEGRATION_STATUS.md`)
- **25 integrated** and reachable through the fabric today: code intelligence (codebase_memory,
  graft), scraping (scrapling), memory backends (graphiti, mem0), roles (agency-agents 258,
  auto-company 14, VoltAgent 158, agents-team archetypes), skills (gstack, no-ai-slop, adhd,
  science, security, diagram-design, open-design, prompt-master, harness templates, mausbot),
  CLI workers (strix, openworker, agenticseek), commerce (medusa, smartstore).
- **21 reference-only**, each with a recorded reason. They fall into three kinds:

| Kind | Repos | Why they are not "helpers" today |
|---|---|---|
| Would duplicate a thing Friday must have only one of | browser-use, nodriver (second browser); crewai, openhands, openwork, cline-as-fabric (second orchestrator/registry); anythingllm, agentmemory (second memory) | NON_NEGOTIABLES: one browser policy, one control layer, one memory |
| Reading material or fixtures, not software | awesome-harness-engineering (reading list), munder-difflin (benchmark), ultron (planner shape), vane (scheduler shape), firstmate (conventions), pipecat (framework) | nothing to call |
| Whole web applications | postiz (social scheduling, AGPL), open-notebook (research notebooks, MIT), maxun (scraping robots, AGPL, five services + own browser), openmontage (video montage, AGPL), bolt.diy / onlook / open-lovable (app builders; Friday builds through Hermes) | need a running service; `friday/fabric_service.py` (FABRIC-SVC-01) can host them as on-demand, idle-reaped sidecars |

## What "connect them as helpers" can honestly mean
Only the third kind gains anything from wiring. A sidecar helper = one adapter in
`friday/fabric_adapters/` (DESCRIPTOR + `fabric_service.Service` spec + 2–4 endpoints),
started on demand by the fabric, health-probed, reaped after 10 idle minutes, with the
write operations at CONFIRM tier (auto-approved only when the owner's full autonomy is on),
plus tests against a fake HTTP server and one live check. Each needs Docker on this
machine and RAM headroom while it runs.

## Measured 13:32: Docker is NOT installed on this machine (`docker --version` fails; Node 24 is).
postiz needs Postgres + Redis + its Node app, open-notebook needs SurrealDB + its app, maxun five
services: without Docker none of them can be booted here. The adapters, endpoint specs and
fake-service tests do not need Docker; only the live boot does. Installing Docker Desktop is a
system change the owner makes himself.

## Options (agent count → token estimate, wall-clock)
- **A. Two helpers that add a real capability, plus a quality pass (recommended)** — postiz
  ("schedule this post", "what is queued") and open-notebook ("start a research notebook on X",
  "ask my notebook"). 2 Sonnet builders + 1 Sonnet simplify pass on this session's diff + gate
  and Playwright re-runs by me. ≈ 550K tokens, ≈ 1.5 h (image builds dominate).
- **B. All four web-app helpers** — A + maxun (scraping robots; AGPL, five services) + openmontage
  (video montage; AGPL). 4 builders. ≈ 1.1M tokens, ≈ 3 h, ~5 GB disk, and the machine is
  already at 90%+ RAM when they run together.
- **C. No new sidecars** — quality pass on this session's diff, reachability/silent-except audit,
  gate + Playwright, docs. ≈ 150K tokens, ≈ 40 min.

Not in any option (would make Friday worse, per its own governance): a second browser driver,
a second orchestrator, a second memory. Their useful ideas are already folded in (DOM extraction
patterns, role recipes, scheduler shapes) and stay readable as reference clones.

## Decision (owner, 13:27): full scope, without Docker, autonomously, no further check-ins
Helpers are wired the way the commerce helpers already are: remote HTTP SIDECARs
(`owns_process=False`) that Friday drives at an instance the owner runs anywhere, honest
"unreachable, set <ENV>" until then. Team (disjoint files): B1 `python-pro` → postiz_social +
anythingllm_research; B2 `python-pro` → open_notebook_research + maxun_scraping (+ openmontage
if it has an API at the pin); B3 `fullstack-developer` → `/api/helpers`, a Helpers panel in the
Organisation view, `helpers/list` by voice, a Playwright spec. Then an Opus read-only review of
the whole session diff, lock/matrix/notices regeneration, gate + Playwright, a live pass, and the
restart of :8770/:8000. The `friday-*` project agents exist on disk but are not loaded into
this session (Claude Code reads `.claude/agents` at start-up), so the VoltAgent agents with the
same caps carry the work.

## What the owner still has to do regardless
Restart :8770/:8000 for today's code; say "full autonomy on" once; give postiz its social
OAuth credentials if option A/B is chosen (credentials never pass through an agent).
