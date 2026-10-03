# F.R.I.D.A.Y. / JARVIS
## MASTER PRODUCT REQUIREMENTS DOCUMENT — v7.0
### Universal Cognitive Operating System + Intent-to-Reality Engine

> **Transcription note (added 2026-09-25; not part of the owner's text).**
> Transcribed from the owner's pasted v7.0 text into Markdown. Sections 0–94
> keep the owner's wording; only list and table formatting changed.
> Appendix A of the original — the prior v6.0 baseline dated 2026-09-19,
> "retained verbatim for traceability" — is not reproduced here; the original
> states that v7 controls where the two conflict. The repository's existing
> requirement ledger (`scripts/prd_ledger.py`) tracks a third document, PRD
> v1.0 Universal Product Baseline (`C:\Users\admin\Downloads\PRD.md`, sha256
> `1777465992…`). The v7 requirement IDs in §91 are the ones the v7 audit
> classifies (`docs/plans/prd-v7-truth-audit/`).
>
> Amendment A1 (§95 and Appendix C) was added on 2026-09-26 at the owner's
> instruction; see the note at §95.
>
> Amendment A2 (§96 and Appendix D) was added on 2026-09-27 at the owner's
> instruction; see the note at §96.
>
> Amendment A3 (§97 and Appendix E) was added on 2026-09-27 at the owner's
> instruction; see the note at §97.
>
> Amendment A4 (§98 and Appendix F) was added on 2026-09-28 at the owner's
> instruction; see the note at §98.
>
> Amendment A5 (§99 and Appendix G) was added on 2026-09-29 at the owner's
> instruction; see the note at §99.
>
> Amendment A6 (§100 and Appendix H) was recorded on 2026-09-30 under the
> owner's standing instruction to keep new architecture in the PRD while the
> P0s are driven down; see the note at §100.
>
> Amendment A7 (§101) was added on 2026-09-30 at the owner's instruction ("DO
> IT SAME FOR FRIDAY TOO"); see the note at §101.

| | |
|---|---|
| Canonical filename | PRD.md |
| Version | 7.0 |
| Date | 2026-09-25 |
| Product | F.R.I.D.A.Y. |
| Alias | JARVIS |
| Canonical creator/product identity | Darsh Yadav |
| Document type | Master PRD + Target Architecture + Implementation/Evaluation Authority |
| Primary current platform | Windows 10/11 |
| Target platforms | Windows first; macOS next; Linux/WSL for development/runtime compatibility; Android through adapters; future web/mobile remote surfaces |
| Primary orchestrator | FRIDAY |
| Primary execution substrate | Hermes plus FRIDAY-native capabilities |
| Primary engineering workers | Claude Code, Codex and future benchmarked CLIs through a FRIDAY-owned CLI Gateway |
| First real-world pilot | Crunchy Fashion, without making ecommerce the product definition |
| Product posture | local-first, model-agnostic, capability-driven, evidence-driven, privacy-aware, self-improving under controlled authority |
| Status | authoritative target-state PRD; implementation MUST audit current repository before building |
| Amendments | A1, 2026-09-26: domain partitioning, the domain partition of the general build, and the updated implementation controller (§95, Appendix C); A2, 2026-09-27: durable execution, behavioural contracts and interoperability (§96, Appendix D); A3, 2026-09-27: the local-model hardware rule and the prompt research corpus (§97, Appendix E); A4, 2026-09-28: adaptive hardware and the two reference machines (§98, Appendix F); A5, 2026-09-29: live browser readiness, test isolation and the P0 drive (§99, Appendix G); A6, 2026-09-30: the computer-use fabric and the Cua evaluation (§100, Appendix H); A7, 2026-09-30: FRIDAY runs its workers the way its build should (§101) |

---

## 0. Document authority, classification and usage

This v7 document is the authoritative future-state product definition. It supersedes earlier architectural descriptions where they conflict, while preserving their verified implementation evidence and lessons.

A new term in this PRD does not justify duplicating an existing working subsystem. Before implementation, engineering SHALL classify existing repository state as:

`EXISTING_VERIFIED` · `EXISTING_UNVERIFIED` · `PARTIAL` · `BROKEN` · `MISSING` · `DUPLICATED` · `SUPERSEDED` · `DEFERRED_BY_PRD` · `NOT_APPLICABLE`

Preferred engineering sequence: **REUSE -> EXTEND -> REPAIR -> MIGRATE -> REBUILD**

### 0.1 Product classification

The user's generic checklist does not contain a category for a persistent local AI operating system. FRIDAY is classified as a hybrid product:

- **Primary:** Desktop application with embedded persistent local cognitive/agent runtime.
- **Secondary:** Local service/API substrate, CLI orchestration platform, browser/desktop automation system.
- **Future interaction surfaces:** Telegram, secure web remote UI, Android companion/control, macOS application, telephony and selected physical-system adapters.

### 0.2 Checks applied

This PRD applies desktop, installation, persistence, memory, security, auth, permissions, privacy, accessibility, state, scalability of agents/workspaces, browser/desktop automation, background services, updates, recovery, local/cloud models, self-development, remote control, voice, mobile and evaluation requirements.

### 0.3 Checks explicitly deferred

The following are not silently skipped; they are deferred:

- public SDK packaging/tree-shaking/peer-dependency policy;
- full multi-tenant cloud SaaS deployment;
- public plugin/skill marketplace;
- full native iOS automation;
- unrestricted physical robotics operation;
- commercial billing/subscription implementation;
- training a frontier-scale foundation model from scratch.

### 0.4 Evidence rule

A requirement is not complete because code exists. VERIFIED means the requested real behavior was demonstrated with authoritative evidence.

Examples of valid evidence include: deterministic tests; execution-based evaluators; post-state readback; DB state; file hash/readback; browser state; API confirmation; CI on the exact commit; real benchmark output; simulation/measurement; protected negative control.

A model saying "done" is not evidence.

## 1. Executive vision

FRIDAY is no longer defined as an ecommerce assistant, coding assistant, voice assistant or a wrapper around Hermes/Claude/Gemini/OpenAI.

FRIDAY is a universal local-first cognitive operating system that converts human intent into verified real outcomes by dynamically acquiring missing knowledge, selecting intelligence, assembling temporary expertise, operating permitted digital and eventually physical systems, learning from evidence, and continuously improving under deterministic authority constraints.

The user should be able to say "Friday, I have an idea. Build it." or "Friday, handle this." and receive a working product, executed workflow, verified research result, design artifact, code change, browser action, simulation, report, communication or other real output — not only an explanation of how it could be done.

## 2. Problem statement

Current AI products still force the human to behave as the orchestrator. The user chooses the model, gathers context, decides which tool to open, moves information between systems, watches the agent, restarts broken loops, checks whether success is real, and repeats the same knowledge in future sessions.

The problem becomes worse as AI agents gain more autonomy because capability introduces new failure modes:

- **Context rot / lost in the middle** — relevant information exists but attention degrades with context size/noise.
- **Context pollution** — logs, stale plans, tool results and duplicated memory crowd out critical facts.
- **Compaction corruption** — a summary can silently omit information needed later.
- **Premature termination** — the agent concludes a long task is done or impossible without sufficient evidence.
- **Trajectory drift** — many individually plausible steps gradually move away from the user's original goal.
- **Tool hallucination** — the model invents tools/arguments or uses the wrong schema.
- **Tool/MCP bloat** — huge tool catalogs consume context and reduce selection quality.
- **Multi-agent explosion** — unnecessary workers multiply token usage and coordination errors.
- **Frontier-model overuse** — expensive models perform tasks deterministic code or local models could do.
- **Reasoning overspend** — easy tasks receive deep reasoning while consequential tasks may receive insufficient verification.
- **Prompt/data injection** — external content attempts to manipulate agent actions.
- **Memory poisoning** — malicious content persists and influences actions later.
- **Authority confusion** — strings/metadata claim trust or permission they do not actually possess.
- **Capability composition risk** — individually safe tools combine into dangerous authority.
- **Evaluator gaming** — an autonomous builder optimizes the test instead of the true outcome.
- **False action claims** — the model equates an attempted command/click with completed state change.
- **Provider/model drift** — the same named route changes behavior over time.
- **Skill staleness** — reusable procedures rot as code and websites change.
- **Self-improvement regressions** — autonomous modifications make the system less reliable or safe.
- **Continual-learning forgetting** — new adaptation damages old capabilities.
- **Resource starvation** — browsers, CLIs, local models and workers overwhelm normal hardware.
- **Idea-to-reality gap** — models produce plausible plans rather than finished outcomes.

FRIDAY's product architecture exists to systematically reduce these failure modes.

## 3. Product north star

```text
USER INTENT
  -> UNDERSTAND DESIRED REALITY
  -> CHECK MEMORY / WORLD STATE / ARTIFACTS
  -> IDENTIFY ASSUMPTIONS / CONSTRAINTS / UNCERTAINTY
  -> RESEARCH / EXPERIMENT ONLY WHERE NEEDED
  -> COMPILE DURABLE OBJECTIVE
  -> BUILD MINIMAL HIGH-SIGNAL WORKING CONTEXT
  -> SELECT LEAST-WASTEFUL CAPABLE INTELLIGENCE
  -> SELECT / BUILD CAPABILITIES
  -> EXECUTE UNDER EXPLICIT AUTHORITY
  -> OBSERVE REAL STATE
  -> VERIFY INDEPENDENTLY
  -> DELIVER REAL RESULT / ARTIFACT
  -> LEARN VERIFIED LESSONS
  -> IMPROVE NEXT EXECUTION
```

The user should not be required to understand MCP, Mem0, OpenViking, provider routing, vector databases, agent topology, local inference or CLI transport to get value from FRIDAY.

## 4. Goals and measurable objectives

- **G-01 Intent to reality** — Convert a vague or direct instruction into real deliverables or state changes.
- **G-02 Universal adaptation** — Adapt to software, research, engineering, business, creative, personal and future physical work without a hard-coded profession tree.
- **G-03 Minimum waste** — Optimize Verified Useful Work per 100K Tokens instead of maximizing model usage.
- **G-04 Truthful operation** — No important success/failure/state claim without matching evidence.
- **G-05 Durable continuity** — Objective state survives fresh model sessions, compaction, crash and supported reboot.
- **G-06 Local-first privacy** — Selected private data remains physically unroutable to disallowed cloud providers.
- **G-07 Controlled autonomy** — Routine low-risk reversible work proceeds with minimal interruption; consequential actions follow user policy.
- **G-08 Learning from evidence** — Verified traces produce memory, skills, tests, routing data and failure knowledge.
- **G-09 Self-extension** — Missing capabilities may be researched, built/adapted, tested and registered.
- **G-10 Model independence** — Models are replaceable cognitive resources. FRIDAY remains the product.
- **G-11 Comprehensibility** — Greater autonomy cannot make FRIDAY less auditable or controllable.
- **G-12 Low-end support** — Useful baseline on 8 GB RAM, 4-core CPU, no dedicated GPU.

## 5. Non-goals and hard boundaries

"Universal" does not mean guaranteed success on literally anything. It means broad legitimate task acquisition within technical, physical, account, legal, hardware and authority constraints.

FRIDAY shall not:

- fabricate real-world success;
- allow a model/skill/plugin to mint authority;
- allow external text to become system policy;
- expose secrets in ordinary model prompts;
- silently send LOCAL_ONLY data to cloud providers;
- treat installed software as healthy without probes;
- run infinite self-improvement or retry loops;
- train production neural weights directly from one conversation;
- let self-development modify protected trust roots without protected approval;
- expose a generic "unethical mode" that bypasses the authority kernel.

Authorized cybersecurity research exists as a separate scoped laboratory, not as removal of the product's security boundary.

## 6. Core product principles

- One manager; many replaceable workers.
- Outcome before tool.
- Eliminate -> Automate -> Delegate.
- Strongest model does the most valuable tokens, not the most tokens.
- Context is working memory, not storage.
- Conversation history is archival evidence, not the brain.
- Durable state lives outside disposable model sessions.
- Skills are procedural memory, not permissions.
- Capabilities are closed-world controlled actions.
- Authority is deterministic and outside model context.
- External content is untrusted by default.
- Important actions carry proof.
- A click is not a completed outcome.
- A summary is not automatically faithful.
- One successful trace is not automatically a production skill.
- One correction is not automatically a neural-weight update.
- Test cheap hypotheses instead of debating indefinitely.
- More agents are not automatically better.
- More context is not automatically better.
- More reasoning tokens are not automatically better.
- Self-improvement must be reversible and benchmarked.
- Critical guards must prove they can fail.
- High-consequence errors deserve stronger checking.
- Protected controls cannot be weakened by the model they constrain.

## 7. Users and personas

FRIDAY is not restricted to one profession. Primary personas include:

- **Universal power user** — wants one assistant to operate software, browser, files, research and projects.
- **Builder / developer** — needs repository understanding, coding CLIs, debugging, tests and autonomous engineering loops.
- **Founder / operator** — needs research, communications, SaaS/browser operations, analytics, workflows and proactive monitoring.
- **Engineer / researcher** — needs literature, equations, experiments, constraints, simulation, CAD/electronics and epistemic traceability.
- **Creator** — needs research, scripts, image/video/audio production, software control, publishing and analytics.
- **Agency / multi-company operator** — needs strict workspace isolation, reusable generic skills and private client data.
- **Student / learner** — needs learning missions, projects, explanations, coding/build help and policy appropriate to account/jurisdiction where required.

## 8. Macro architecture

```text
                              USER
                               │
                        voice / text / remote
                               ▼
                         ┌───────────┐
                         │  FRIDAY   │
                         │   CORE    │
                         └─────┬─────┘
                 ┌─────────────┼─────────────┐
                 ▼             ▼             ▼
          INTENT-TO-REALITY  OBJECTIVE OS   POLICY/AUTHORITY
                 └─────────────┼─────────────┘
                               ▼
                          WORLD MODEL
                               ▼
                           CORTEX OS
                               ▼
                       COGNITIVE GOVERNOR
              ┌────────────────┼────────────────┐
              ▼                ▼                ▼
        DETERMINISTIC      LOCAL EXPERTS     FRONTIER AI
              └────────────────┼────────────────┘
                               ▼
                        CLI / WORKERS
                               ▼
                      CAPABILITY FABRIC
                               ▼
                          REAL WORLD
                               ▼
                      REALITY / EVIDENCE
              ┌────────────────┼────────────────┐
              ▼                ▼                ▼
          VERIFIER      EPISTEMIC LEDGER    BLACK BOX
              ▼
            LEARN
   memory / skills / evals / neural candidates
```

## 9. FRIDAY core

FRIDAY is the single product-level manager and primary user relationship.

FRIDAY owns: product identity/personality; user intent; objective creation; user interruption handling; context requests; memory policy; routing policy; specialist creation; approval/authority request; final verification decision; final communication.

Hermes, Claude Code, Codex, local models, Gawkbot-like patterns, OpenHands, CrewAI and other frameworks may serve as substrates/adapters/specialists but do not replace FRIDAY as control plane.

## 10. Intent-to-Reality Engine

The Intent-to-Reality Engine closes the gap between "discuss an idea" and "produce a real outcome."

### 10.1 Inputs
voice instruction; text instruction; image/screen/reference; current project context; previous user decisions; observed state.

### 10.2 Outputs
Depending on objective, real outputs may include: software/project; code changes; design/CAD artifact; research pack; report; website; edited media; browser state change; communication; scheduled task; simulation; experiment; future physical-device action through a permitted controller.

### 10.3 Intent Compiler
The compiler extracts: desired outcome; explicit constraints; exact literals; implicit assumptions; success criteria; uncertainty; material decisions; privacy/risk; required evidence.

It researches externally resolvable factual uncertainty rather than asking the user unnecessarily.

It asks only when ambiguity materially changes the user's desired result, authority, safety or irreversible consequence.

## 11. Objective OS

Every meaningful task is durable state, not merely a conversation turn.

Minimum objective fields:

```yaml
objective_id:
workspace_id:
user_id:
goal:
goal_version:
success_criteria:
hard_constraints:
preferences:
exact_literals:
assumptions:
open_questions:
dependencies:
task_graph:
current_phase:
worker_leases:
capability_scope:
network_scope:
privacy_class:
budget:
  money:
  input_tokens:
  output_tokens:
  wall_time:
  tool_calls:
  workers:
  compute:
risk:
consequence:
evidence:
checkpoints:
experiments:
blockers:
retries:
progress:
verifier:
next_action:
terminal_status:
```

Objective lifecycle: `NEW -> ACTIVE -> WAITING/VERIFYING -> COMPLETED_VERIFIED / FAILED_VERIFIED / CANCELLED / BLOCKED_EXTERNAL / DEFERRED`.

COMPLETED without verification is invalid.

### 11.1 Objective versioning

When the user changes material intent: create amendment; increment version; perform impact analysis; invalidate only dependent work/evidence; preserve unaffected verified work.

This handles the real behavior of users who interrupt and change direction mid-execution.

## 12. Trajectory governor

Long-running systems fail over trajectories, not only single actions.

The governor continuously checks: objective alignment; scope creep; authority drift; no-progress/repeated-error loops; token/cost/time drift; accumulating side effects; user amendments; high-consequence unverified assumptions; repeated policy near-misses; worker conflict.

Actions include: continue; trim/fold context; force fresh worker; choose stronger model; lower concurrency; pause branch; replan; ask user; cancel objective.

## 13. World model

FRIDAY needs a structured representation of current reality beyond memories and chat.

Possible entities: people; workspaces; teams; projects; applications; accounts; websites; devices; repositories; files; physical components; orders/customers; experiments; requirements; capabilities.

World state carries: source/provenance; timestamp; confidence; relationships; current measurement; expected transitions; invalidation conditions.

Speculation cannot silently become authoritative world state.

## 14. Digital twin / physical-system boundary

For future wearable engineering, robotics, electronics and physical projects:

```text
human intent -> high-level reasoning -> formal constraints + world state
  -> simulation / digital twin -> feasibility + safety verification
  -> deterministic low-level controller -> physical device
  -> sensor measurement -> world-model update
```

An LLM shall not directly become an unrestricted low-level safety controller.

## 15. Assumption register

Complex objectives explicitly track assumptions: statement; origin; confidence; consequence if wrong; validation plan; current status.

FRIDAY should test high-impact assumptions early instead of building large plans on hidden guesses.

## 16. Constraint engine

Whenever a task is naturally formal, FRIDAY should translate free-form intent into deterministic constraints rather than asking an LLM to reason everything in prose.

Potential backends: arithmetic/calculation; symbolic algebra; optimization; SAT/SMT; scheduling; graph algorithms; geometry; database/SQL; simulation.

The model interprets the problem; deterministic systems compute and verify feasible solutions.

## 17. Counterfactual engine

For meaningful decisions compare: plan A; plan B; do nothing; defer; run experiment first.

Each branch distinguishes observation, sourced fact, simulation and speculation.

## 18. Experiment / scientific loop

When inexpensive evidence can settle uncertainty, FRIDAY should test instead of generating endless debate.

Experiment object: hypothesis; competing hypothesis; variables; measurement; information-gain objective; budget; stop criteria; result; interpretation; next experiment.

Research-oriented objectives may use: `OBSERVATION -> HYPOTHESIS -> EXPERIMENT -> MEASUREMENT -> ANALYSIS -> WORLD-MODEL UPDATE -> NEXT EXPERIMENT`.

## 19. CortexOS — context virtualization

### 19.1 Principle
A large context window is not permission to dump the entire FRIDAY brain into the model. FRIDAY knowledge may grow to millions of tokens while a model receives only the relevant working set.

### 19.2 Working-set compiler
Input: current objective/task; worker role; model context profile; budget; privacy; world state; pinned constraints.

Output sections: system/kernel constraints; objective; exact user constraints; current task; current state; relevant memory; relevant project knowledge; relevant skill; selected code/resources; selected tool schemas; recent evidence; output contract.

### 19.3 L0/L1/L2 progressive disclosure
For large objects: L0 = tiny pointer/abstract; L1 = concise operational overview; L2 = full source. Scan broad L0 metadata, shortlist L1, load L2 only where needed.

### 19.4 Context priority
- P0_CONTEXT — objective, policy, exact literals, current correction, critical constraints.
- P1_CONTEXT — highly relevant architecture/state.
- P2_CONTEXT — on-demand supporting info.
- P3_CONTEXT — pointer/reference only.
- DROP — resolved/duplicate/noise from active context.

### 19.5 Context health
Track: utilization; relevance density; duplicate ratio; stale ratio; contradiction score; raw-output ratio; failed-attempt density; struggle score; retrieval confidence.

States: HEALTHY; NOISY; DEGRADED; ROTTING. Responses: prune; summarize; fold; handoff to fresh session.

### 19.6 Context folding
Temporary branches may ingest large information, write artifacts, produce a concise verified branch result and then fold out of the parent context.

### 19.7 Artifact offloading
Full logs, web pages, reports, screenshots, traces and large analyses live in artifacts. The active model receives structured result + reference.

### 19.8 Compaction fidelity
Before replacing history with summary, identify CriticalFactSet: objective; exact literals; hard constraints; decisions; unresolved questions; evidence pointers; failed approaches worth avoiding; next action; authority/privacy.

Post-compaction validation must prove the summary retained them. Lossy compaction is rejected.

### 19.9 Fresh-session handoff
Long-running worker identity persists through objective state and artifacts, not a forever-growing model conversation. Fresh worker loads a compact work packet and continues.

### 19.10 Semantic cache
Cache suitable non-side-effect results only when: equivalence is high; scope matches; freshness valid; user/project/private boundaries match. Side-effecting actions are never replayed merely from a cached answer.

### 19.11 Context Lab
Benchmark: lost-in-middle positions; semantic needles; multi-hop facts; aggregation; contradiction/supersession; noise scaling; tool-schema bloat; skill bloat; long trajectories; compaction fidelity; fresh handoff; repository-scale code reasoning; exact literal retention; poisoned context.

Compare strategies against a full-history baseline.

## 20. Cognitive governor

The governor determines how much cognition the task deserves.

Inputs: complexity; uncertainty; consequence if wrong; privacy; model availability/health; user preference; money/token budget; latency; hardware; past performance.

Routes:

- **Level 0 — Deterministic.** No LLM where code/solver suffices.
- **Level 1 — Lightweight local.** Classification, extraction, routing, small structured tasks.
- **Level 2 — Cheap/local general.** Simple drafting/reasoning.
- **Level 3 — Normal executor.** Typical agentic work.
- **Level 4 — Frontier advisor.** Strong model answers hard strategic questions while cheaper worker performs long execution.
- **Level 5 — Frontier direct/deep.** Use only when evaluations justify it.
- **Level 6 — Bounded team.** Independent specialists only where parallelism, specialization, isolation or verification justify overhead.

## 21. Reasoning budget manager

Conceptual effort modes: NO_THINK; SHORT; NORMAL; DEEP; EXPERT.

Reasoning allocation depends on difficulty, uncertainty and consequence. A typo fix should not consume maximum reasoning. A seemingly simple but destructive migration deserves stronger verification because consequence is high.

## 22. Advisor / executor pattern

Use a frontier advisor for: architectural choice; repeated failure; high-consequence migration; strategic review; difficult ambiguity.

Use a lower-cost executor for: searching; mechanical edits; repetitive tool loops; standard test/fix cycles.

The advisor is not consulted on every turn.

## 23. Ponytail / minimal-build policy

Ponytail or a FRIDAY-native equivalent may be activated for coding workers after A/B validation on the real repository.

Minimal implementation ladder: does this need to exist? is it already in the codebase? does stdlib solve it? does native platform/browser behavior solve it? does an installed dependency solve it? can the safe diff be smaller? only then create new abstraction/dependency.

Never simplify away: validation; security; accessibility; data-loss protection; required error handling; observability essential for truth.

Ponytail's advertised savings are not accepted as FRIDAY's own savings until measured on FRIDAY tasks.

## 24. Neural Fabric — FRIDAY's own learnable brain

FRIDAY's own neural architecture begins with small learnable policy/value components, not training a frontier foundation model from scratch.

Candidate networks:

- **24.1 Model Router Network** — predict suitable provider/model.
- **24.2 Context Policy Network** — predict context budget, retrieval, folding and compaction strategy.
- **24.3 Value/Utility Network** — estimate expected outcome quality, latency, cost and user acceptance for candidate plans.
- **24.4 Risk/Consequence Predictor** — estimate impact of being wrong. Deterministic policy remains final authority.
- **24.5 User Preference Model** — learn usage patterns while explicit facts remain in memory.
- **24.6 Skill Router** — select relevant procedures.
- **24.7 Memory/Retrieval Router** — select exact/SQL/lexical/semantic/graph/full-document/web strategies.
- **24.8 Anomaly Detector** — detect unusual system/project/business state.
- **24.9 Difficulty/Compute Estimator** — choose reasoning budget.
- **24.10 Confidence/Abstention Calibrator** — decide when a cheap/local prediction must escalate.

### 24.11 Training lifecycle
`verified trajectory -> sanitized feature/example -> offline dataset -> candidate model -> holdout eval -> adversarial/regression eval -> promotion or rejection`.

A single interaction never directly mutates production weights.

## 25. Continual learning / distillation lab

Potential future techniques: LoRA/adapters; local specialist fine-tuning; teacher-to-small-model distillation; skill-to-model distillation; harness distillation.

Requirements: private data governance; train/test separation; old-capability regression; forgetting measurement; promotion gate; rollback.

Scaffold/skills/config improvement comes before neural training where it solves the problem more cheaply.

## 26. Recursive reasoning controller

Recursive processing is optional. Use it for very large/decomposable contexts or independent subproblems. Do not recurse by default on tasks that fit cleanly.

Limits: max depth; calls; tokens; wall time; duplicate-subproblem detection; worker limit; evidence requirement.

## 27. Model Arena

FRIDAY maintains personalized model profiles using real/synthetic safe evals.

Metrics: success; code quality; tool use; browser/computer success; context robustness; latency; cost; cache economics; privacy; availability; failure patterns.

New models enter through open-set profiling rather than rewriting product logic.

## 28. Model routing

Default objective: choose the cheapest/lowest-latency healthy route meeting required quality, privacy and consequence threshold.

Maintain a simple baseline router. A learned router is deployed only after beating the baseline on FRIDAY-specific evaluation.

User preference can be: preferred with fallback; preferred without fallback; strict-only.

## 29. Local expert pool

Potential roles: Laya-style decision classifier; Needle-style tool/structured extraction model; embedding model; small private general model; vision model; STT; anomaly detector; small coding model where feasible.

On 8 GB hardware, heavier experts should load on demand and unload. Stronger systems may keep high-use experts warm if benchmarked beneficial.

Local does not automatically mean trusted; model/runtime supply chain still receives security review.

## 30. Free model scout

Research legitimate official free/trial/experimental/open routes. Can benchmark and register candidates.

Cannot: use leaked credentials; evade quotas; create fake identities; scrape unauthorized keys; bypass required payment/identity controls.

## 31. Canonical memory

Durable truth belongs to FRIDAY's governed memory system.

Memory scopes: user; workspace; project; objective; relationship; capability; episode.

Worker-private permanent memory is disallowed by default; workers can maintain bounded procedural state during active work.

Memory records carry: provenance; trust; scope; confidence; freshness; supersession; privacy class.

## 32. Memory security lifecycle

Memory lifecycle: `CANDIDATE -> VALIDATED/TRUSTED -> RETRIEVABLE -> SUPERSEDED/DEPRECATED/FORGOTTEN`.

Untrusted webpage/tool content cannot silently become a standing user instruction.

Security benchmark follows a Write -> Execute -> Forget/Repair pattern and tests delayed downstream effects.

## 33. Project brain

Human-inspectable structured project state:

```text
project/ architecture/ decisions/ requirements/ facts/ assumptions/ research/
experiments/ incidents/ failures/ procedures/ objectives/ handoffs/ entities/
artifacts/ evaluations/
```

Indexes may be derived/rebuilt.

## 34. Research artifact store

Each substantial research artifact records: question; sources; date; facts; synthesis; conflicts; confidence; freshness; expiry/invalidation; decisions influenced.

Search existing research before repeating expensive research.

## 35. Epistemic ledger

Important claims store:

```yaml
claim:
status: observed | measured | sourced | inferred | simulated | speculative
sources:
evidence:
confidence:
freshness:
assumptions:
contradictions:
scope:
```

This allows FRIDAY to know not only what it believes, but why.

## 36. Failure genome

Meaningful failures become structured reusable knowledge: symptom; environment/version; task class; failed approach; wrong assumption; root cause; successful repair; prevention; regression test; skill candidate; applicability conditions.

Before repeating a known failure pattern, retrieve this structure rather than searching old chat.

## 37. Skill intelligence

Lifecycle: `DISCOVER -> DRAFT/QUARANTINE -> LINT -> TEST -> NEGATIVE_TEST -> REVIEW -> ACTIVE -> MEASURE -> REVALIDATE -> UPDATE/ROLLBACK -> DEPRECATE -> OPTIONAL_PUBLISH`.

Skill record includes: trigger; non-trigger; procedure; input/output; capabilities; risk/privacy; dependencies; examples; tests; version.

### 37.1 Trace-to-skill distillation
Prefer evidence from multiple verified trajectories where possible. Extract both success patterns and failure avoidance.

### 37.2 Dependency fingerprint
App/code changes invalidate only related skills.

### 37.3 Meta-skill evolution
Experimental. A system that improves the skill-learning process itself remains sandboxed until independently validated.

## 38. Shadow mode / learn by observation

With explicit permission: observe user workflow; predict next action privately; compare prediction with actual behavior; capture decision points; build draft skill; replay safely; propose takeover once performance reaches threshold.

This is the preferred path for learning complex personal workflows without forcing the user to document every step.

## 39. Capability fabric

Capabilities are controlled action interfaces, not prompts.

Categories may include: files; code; terminal; browser; desktop; communications; calendars; social; data; creative apps; mobile; telephony; simulation; authorized security; future physical devices.

Metadata: ID; version; backend; source/provenance; health; auth; permissions; risk; data classes; latency; cost; supported platform; tests; last verified.

## 40. Closed-world capability resolver

A model cannot execute an invented tool.

`proposal -> registry existence -> schema validation -> composition/policy check -> execution`.

Unknown tool or invented argument is rejected before action.

## 41. Typed trust and data lineage

Authority is produced by trusted runtime code, never by text saying "trusted=true".

Trust classes may include: direct user instruction; protected product policy; authenticated internal state; authenticated connector data; public web; arbitrary external content; model hypothesis.

Track lineage through transformations so external data does not become more trusted simply by being summarized.

## 42. Capability composition analyzer

Evaluate the power of combined tools. For example: private-data-read + arbitrary-network-send may be high risk even if each individual capability is useful.

Worker creation computes an effective authority/risk profile for the complete set.

## 43. Execution envelopes

Each worker/CLI receives deterministic scope outside model context:

```yaml
cwd:
read_roots:
write_roots:
network:
secrets:
allowed_capabilities:
blocked_capabilities:
process_limit:
memory_limit:
time_limit:
token_limit:
tool_call_limit:
expiry:
```

A worker cannot expand its own envelope.

## 44. Network capability tokens

Network access can be scoped to: none; public-web read; selected hosts; provider endpoints; authenticated business services; explicit authorized security targets.

Tokens are objective-bound, auditable and revocable.

## 45. CLI gateway

Unified adapter for: Claude Code; Codex; Hermes execution; Gemini CLI; OpenCode; future CLIs.

Gateway handles: work packet; cwd; envelope; context; model/provider; prompt transport; interactive questions; budget; progress; cancellation; artifacts; handoff; termination.

Large prompts should use stdin/files/structured transport rather than fragile command-line arguments.

## 46. CLI work packets and output contract

Input example:

```yaml
objective:
requirement_ids:
current_task:
why:
constraints:
architecture:
files:
skills:
capabilities:
previous_failures:
tests:
budget:
required_artifacts:
completion_evidence:
```

Output default:

```yaml
status:
result:
files_changed:
tests:
evidence:
artifacts:
blockers:
remaining_risk:
```

Full narratives go to artifacts, not the manager context.

## 47. Browser and computer use

Normal users should not be forced to configure merchant APIs just to automate familiar websites.

Execution options: native/official integration; authenticated browser; OAuth connector; API where useful and approved; human takeover.

Chrome support: approved existing user profile; dedicated FRIDAY profile; temporary isolated profile; CDP-compatible session control where reliable.

OTP/passkeys/security keys and genuine human-verification challenges require appropriate human participation.

## 48. Hybrid action-path router

Per step choose: deterministic native API; official scripting interface; structured tool/MCP; DOM/accessibility; semantic vision; coordinates last.

The hierarchy may adapt based on measured reliability. A tool path and a GUI path can coexist within one objective.

## 49. Action truth / proof-carrying actions

Every meaningful completed action returns result + proof. Examples: file -> path/hash/readback; browser publish -> URL/state/screenshot; email -> message ID/sent state; code fix -> reproduction + tests + commit/diff; experiment -> measurement artifact; future device -> sensor/telemetry evidence.

Action lifecycle: `PLAN -> PRE-STATE -> ACTION -> POST-STATE -> VERIFY -> CLAIM`.

## 50. Reality ledger

Normalize action evidence from: FRIDAY direct; MCP; Hermes; Claude Code; Codex; browser; desktop; scheduler; mobile; phone; simulation/physical adapters.

Correlation keys include workspace, objective, turn, action, target, operation, timestamp and executor.

Unrelated recent evidence cannot justify a new claim.

## 51. Black box recorder

Record observable trajectory data sufficient to reconstruct important outcomes: objective and amendments; policy version; selected models; capability calls; approvals; side effects; evidence; environment fingerprint; terminal result.

This does not require storing private hidden reasoning.

## 52. Independent verifier and evaluator-gaming defense

For P0/P1/high-consequence work:

- builder cannot self-approve final truth;
- verifier reads evidence or recomputes state;
- protected tests remain outside builder write scope where feasible;
- test harness must reject known mutants/incorrect implementations;
- the harness cannot create the evidence it claims to test.

## 53. Out-of-band emergency stop

STOP must be deterministic and independent of the model. It shall: cancel new actions; revoke capability/network leases; stop owned workers/processes; stop browser/desktop action queue; stop model/TTS where requested; persist recoverable state.

The model does not get to refuse STOP.

## 54. Checkpoints, rollback and undo

Before consequential reversible multi-step work, capture checkpoint where feasible.

Action journal includes: before; after; inverse/recovery action; evidence; objective.

Undo must verify that state has not diverged in a way that makes reversal dangerous.

## 55. Resource governor

Manage: RAM; CPU; GPU; local model residency; browser processes; worker concurrency; disk; network.

Every owned process receives owner, objective, lease, heartbeat, deadline and cleanup policy.

## 56. Proactive event runtime

Persistent does not mean constantly invoking LLMs.

Prefer: filesystem watchers; webhooks; schedules; provider events; email/calendar events; CI events; objective state transitions.

`event -> bounded wake -> inspect -> act/notify -> persist -> sleep`.

## 57. Notification intelligence and opportunities inbox

Communication modes: detailed; balanced; important only.

FRIDAY may proactively detect repeated work, errors, anomalies, stale dependencies, useful model/tool upgrades and other opportunities. Nonurgent suggestions go to an Opportunities Inbox rather than interrupting constantly.

## 58. Capability builder

Missing capability flow: `detect -> search existing -> research -> provenance/security/license review -> adopt/adapt/build -> quarantine -> implement -> test -> negative test -> integration -> register -> execute original objective`.

Major construction asks the user once unless standing policy allows it. Built capabilities remain private until explicitly shared/published.

## 59. Technology scout

Periodic/event-driven, not continuous crawling.

Research: models; CLIs; context methods; memory; skills; MCPs; browser/computer use; security; relevant domain tools.

A trending project is not adopted unless it addresses a measured need.

## 60. Controlled self-development

`weakness -> reproduce -> isolated worktree -> implement -> test -> adversarial review -> benchmark -> promote/rollback`.

Protected kernel includes: Policy Engine; Secret Broker; permission evaluator; verification trust root; updater/signature trust; audit integrity; emergency stop.

Protected-kernel changes require stronger explicit approval.

## 61. Authorized cybersecurity research lab

Purpose: defensive research; CTFs; owned/authorized systems; code review; threat modeling; sandboxed malware analysis; vulnerability reproduction in isolated environments; detection engineering.

A less-restrictive local security specialist may operate inside the lab but does not receive unrestricted FRIDAY authority.

Mandatory: target; ownership/authorization; allowed methods; time window; network allowlist; isolated credentials/files; audit ID; scope expiry.

There is no global "unethical mode" that disables product policy.

## 62. Voice

Voice is an interface, not the reasoning architecture.

Requirements: multilingual; low latency; VAD/STT/TTS; barge-in; mute physically stops audio to STT; optional PTT/wake word; echo guard; device health; fast acknowledgement while deep task delegates.

FRIDAY has a canonical assistant voice. Customer-facing communication may use different configured voices where allowed.

## 63. Remote control

Priority: Telegram; secure web interface; mobile application later.

Remote channel supports: talk; create objective; status; approve/deny; stop; inspect result.

Requires pairing, identity, replay protection, revocation and audit. Host remains execution location unless another deployment is explicitly configured.

## 64. Mobile

Android-first adapter architecture; Artemis is a candidate subject to current security review.

Granular permissions: app interaction; notifications; files; settings; messages; calls; camera; device controls.

Mobile adapter is not the product brain.

## 65. Telephony

Provider-agnostic adapter. LiveKit/SIP is a logical first candidate because it aligns with the existing voice direction.

Support: inbound; outbound; scheduling/status calls; routine support under policy; escalation; structured outcomes.

Recording/disclosure/identity follow applicable user policy and law/platform rules.

## 66. Multi-workspace and FRIDAY-to-FRIDAY federation

Default hard isolation: memory; secrets; browser profiles; artifacts; research; workers; private skills; policies.

Federation is opt-in and messages carry sender identity, workspace, objective, authority, data class, requested operation, expiry and evidence. Receiving FRIDAY independently validates its own policy.

## 67. User / workspace / project brain

Support layered state: private user brain; optional workspace/company brain; project brain; objective state.

No automatic sharing merely because two people belong to one company.

## 68. Memory UI

User can: search; inspect; visualize relationships; see provenance; correct; supersede; delete/forget; inspect freshness; inspect linked research/skills.

Secrets are represented by protected references, not casually displayed.

## 69. UI / UX modes

**Normal Mode:** FRIDAY conversation; current objective; progress; important decisions; approvals; artifacts; memory/team summaries.

**Developer Mode:** raw logs; models/routes; tokens/cost; MCP/tool schemas; skills; capabilities; memory provenance; evidence; policy decisions; process tree; test/eval/CI status; database health.

User may show/hide workers. Security authority is unaffected by UI mode.

## 70. Onboarding

Goal: useful within roughly 5–10 minutes, then learn progressively.

Learn: identity/name; profession/work; projects; goals; apps/sites; model accounts; budget; privacy; autonomy; languages; communication preference; important folders; recurring workflows.

Discover: OS/hardware; Chrome; installed applications; Git/CLIs; local model runtimes; audio devices.

Use official provider connection/login flows where supported rather than requiring normal users to extract tokens.

## 71. Installation, background runtime and state

Target one user-facing installer/bootstrapper. Internet may be required during initial installation.

Provision as needed: FRIDAY; Hermes; runtimes; browser dependencies; local DB; selected local models; MCP infrastructure; CLI adapters; updater/background service.

Persistent state lives outside replaceable application bundle directories.

FRIDAY can run in the background, but idle state should not continuously infer or poll when event-driven mechanisms exist.

## 72. Hardware baseline

Compatibility target: 8 GB RAM; 4 CPU cores; no dedicated GPU. This is not the ideal target machine but must remain useful.

Heavy capabilities lazy-load, unload or use cloud/provider paths when policy allows. Measure idle CPU/RAM/network and background process count.

## 73. Update / self-repair

Detect: broken dependency; dead worker; expired provider/login; corrupt index; browser runtime failure; model route failure.

Low-risk repair may be automatic. Repeated systemic failure must surface, not be hidden indefinitely. All updates have rollback.

## 74. Codebase intelligence

Maintain durable structural repository knowledge: symbols; calls; dependencies; routes; tests; architecture; diffs; decisions.

Do not reread an entire large repo every fresh worker session. A structural backend such as codebase-memory-mcp may sit behind an adapter if evaluation proves value.

## 75. Learning / expertise missions

Example: "While I sleep, learn aerodynamics deeply for this project."

Create bounded Expertise Mission: goal; project relevance; source quality; time/token/money/storage budget; concept map; equations/frameworks; unresolved questions; project implications; candidate skills; citations/evidence; freshness.

Do not claim accredited degrees/credentials.

## 76. Intellectual sparring

For high-value decisions FRIDAY may create: evidence researcher; optimist; contrarian; failure analyst; domain expert; judge.

Do not run a debate panel for trivial tasks.

## 77. External repository / framework adoption

Classify candidates: CORE; ADAPTER; CAPABILITY; MCP; PLUGIN; SKILL_SOURCE; SPECIALIST; REFERENCE; QUARANTINE; REJECT.

User-facing UX can hide unnecessary implementation branding, but internal provenance/version/license/security information remains traceable.

## 78. Current candidate registry

Important candidates/references already discussed: Hermes Agent; Claude Code; Codex; OpenViking; Mem0; OpenWork; codebase-memory-mcp; Browser Use; Playwright/CDP; Gawkbot; Mark LIV; Agent Reach; Scrapling; Maxun; Postiz; Open Notebook; Artemis; Pipecat; Ponytail; OpenHands/Cline/CrewAI as specialists/references; authorized security/OSINT tool packs.

No candidate is automatically trusted or mandatory.

## 79. Testing strategy

Test layers: `UNIT -> COMPONENT -> INTEGRATION -> ADVERSARIAL -> GOLDEN JOURNEY -> E2E -> SOAK`.

Critical negative controls: planted dummy secret; cross-workspace read; fake action evidence; stale capability health; protected-kernel write; lossy compaction; prompt injection; unauthorized network host; expired authority; duplicated side effect; invented capability; evaluator mutant.

A guard that cannot be made red is not strong evidence.

## 80. Golden user journeys

- **Idea to product** — vague idea -> requirements -> build -> test -> runnable artifact.
- **Clean install** — new Windows VM -> bootstrap -> onboarding -> first useful task.
- **Coding feature** — compact context -> proper model/CLI -> minimal diff -> targeted tests -> verifier.
- **Missing capability** — detect -> research/build -> test/register -> execute original request.
- **Browser operation** — authenticated Chrome -> action -> post-state evidence.
- **Mid-task user change** — objective amendment -> selective replan.
- **Memory continuity** — new session/restart -> correct scoped recall.
- **Context-rot resistance** — very long task completes through folding/handoffs without losing constraints.
- **Model outage** — fallback under policy or strict truthful blocker.
- **Shadow learning** — observe -> predict -> skill -> safe takeover proposal.
- **Expertise mission** — overnight research -> structured project-relevant pack.
- **Experiment** — uncertainty -> bounded experiment -> measurement -> updated belief.
- **Proactive anomaly** — event -> analysis -> alert/fix -> verify.
- **Remote Telegram** — paired remote objective -> progress -> verified outcome.
- **Cyber lab** — explicit owned target/scope -> isolated authorized research -> scope expiry.
- **Emergency stop** — user STOP terminates model-resistant worker.
- **Compaction fidelity** — long session -> compaction -> exact literals/constraints survive.
- **Model Arena** — new candidate enters, is benchmarked, and only promoted if it wins.

## 81. Success metrics

### 81.1 User value
supported work completed without manual computer operation; hours saved; user corrections per objective; artifacts/outcomes delivered; proactive errors prevented; workflow cycle-time reduction.

### 81.2 Cognitive efficiency
verified useful work / 100K tokens; cost/objective; model calls/objective; tool calls/objective; unnecessary delegation rate; frontier-token share; context usefulness ratio; skill/tool schema token reduction.

### 81.3 Reliability/trust
false success claims; false failure claims; duplicate side effects; cross-workspace leaks; unauthorized actions; protected-kernel violations; LOCAL_ONLY egress; restart recovery; cancellation success.

### 81.4 Learning
skill reuse; stale-skill detection; failure-genome reuse; router improvement; neural candidate promotions/rejections; backward benchmark retention.

## 82. Targets for CortexOS

Targets are goals to validate, not guarantees from external papers.

- pinned critical-constraint retention >= 99% through approved compaction;
- middle-position critical-fact task success >= 95% on FRIDAY benchmark;
- tool-schema context reduction >= 80% vs all-tools baseline for large catalogs;
- skill context reduction >= 80% vs all-skills baseline for large catalogs;
- significant long-task token reduction without statistically meaningful success regression;
- premature unsupported termination reduced vs full-history baseline;
- exact literal preservation across handoff/compaction.

## 83. Targets for cognitive efficiency

- deterministic tasks use zero frontier tokens where feasible;
- no model cascade calls every provider by default;
- frontier advisor called only for qualifying high-value decisions;
- sparse worker teams;
- targeted tests during iteration, full gate at appropriate milestones;
- verified useful work/100K tokens improves across releases;
- adaptive reasoning policy meets quality threshold at lower cost than always-deep baseline.

## 84. Targets for Neural Fabric

No learned component is promoted without: sufficiently large verified dataset; holdout evaluation; privacy/scope sanitation; deterministic baseline; calibrated abstention; regression test; measurable improvement; rollback.

If a learned router does not outperform the simpler policy, do not deploy it.

## 85. Risk register

| Risk | Severity | Primary mitigation |
|---|---|---|
| Prompt injection | Critical | typed trust + structured extraction + capability scoping |
| Memory poisoning | Critical | provenance + trust lifecycle + selective repair |
| Secret leakage | Critical | Secret Broker + prompt/log exclusion tests |
| LOCAL_ONLY egress | Critical | physical routing constraint |
| Cross-workspace leak | Critical | mandatory scope on all private state |
| Self-modification weakens controls | Critical | protected kernel + isolated promotion |
| Network escape | Critical | execution envelopes + network tokens |
| False completion | High | proof-carrying action + independent verifier |
| Evaluator gaming | High | protected tests + mutation/negative controls |
| Context rot | High | CortexOS + Context Lab |
| Compaction loss | High | fidelity gate |
| Tool hallucination | High | closed-world resolver |
| Tool/MCP bloat | High | capability search/lazy schemas |
| Model overuse | High | Cognitive Governor |
| Agent swarm cost | High | sparse team policy |
| Catastrophic forgetting | High | continual-learning regression gate |
| Skill staleness | High | dependency fingerprints/revalidation |
| GUI drift | High | hybrid routing + re-observation |
| Endless retries | High | struggle/no-progress detector |
| Resource exhaustion | High | Resource Governor/process leases |
| Unauthorized cyber action | Critical | Cyber Lab scope/network enforcement |
| Physical-world mismatch | Critical for physical | digital twin + deterministic controller |

## 86. Implementation roadmap

Dependency-driven, not merely calendar-driven.

- **Phase 0 — Repository truth:** current HEAD/worktrees/import origin; architecture inventory; requirement classification; test baseline; preserve verified work.
- **Phase 1 — Protected kernel + truth:** Reality Ledger; claim correlation; Policy/Authority; Secret Broker; Emergency Stop; execution envelopes.
- **Phase 2 — Objective OS:** durable objective; versioning; budgets; trajectory governor; checkpoints.
- **Phase 3 — CortexOS:** working-set compiler; layered retrieval; artifacts; folding/handoff; compaction fidelity; Context Lab.
- **Phase 4 — Cognitive Governor / Model Arena:** registry/health; simple router baseline; reasoning budget; advisor/executor; local expert pool; provider benchmarks.
- **Phase 5 — Memory / Project Brain / Epistemic systems:** scoped canonical memory; provenance/trust; research artifacts; Epistemic Ledger; Failure Genome; memory security tests/UI.
- **Phase 6 — Skills / Shadow Mode:** lifecycle; trace distillation; dependency graph; teach/observe/replay.
- **Phase 7 — Capability Fabric / CLI Gateway:** closed-world resolver; capability search; composition analysis; Claude/Codex/Hermes adapters; cancellation.
- **Phase 8 — Browser/Desktop:** Chrome profiles; browser abstraction; hybrid action router; execution-based evidence; computer-use evals.
- **Phase 9 — Intent-to-Reality:** Intent Compiler; assumptions; constraints; counterfactual/experiment engine; idea-to-product journey.
- **Phase 10 — Universal onboarding/UI/install:** installer; normal/developer mode; autonomy controls; workspace/persona setup.
- **Phase 11 — Real pilot:** use Crunchy Fashion as a stress test; keep vertical behavior in optional workspace/capability packs.
- **Phase 12 — Proactive/remote:** Event Bus; Opportunities Inbox; Telegram; web remote.
- **Phase 13 — Voice/telephony:** voice hardening; phone adapters; multilingual behavior.
- **Phase 14 — Controlled self-development:** Technology Scout; Capability Builder; candidate worktrees; benchmark/promotion/rollback.
- **Phase 15 — Neural Fabric:** collect verified data; train router/context/value/risk candidates; benchmark vs deterministic baselines; promote only winners.
- **Phase 16 — Android/macOS/physical research:** mobile adapter; macOS packaging/control; digital twin and physical-system boundary research.

## 87. Indicative timeline

A small focused team could use a milestone plan roughly like:

- 0–2 months: truth/policy/objectives/evidence stabilization;
- 2–4 months: CortexOS + routing/CLI foundations;
- 4–6 months: memory/skills + browser/desktop hardening;
- 6–9 months: Intent-to-Reality + experiment engine + onboarding + serious pilot;
- 9–12 months: proactive/remote/self-development beta + Model Arena;
- 12+ months: neural-policy learning, macOS/Android, physical/digital-twin research.

This is not a delivery promise. Current repository audit may materially change estimates.

## 88. Production release gate

FRIDAY cannot be called production-ready unless the applicable release checklist passes:

- [ ] exact release HEAD and repo truth recorded
- [ ] clean Windows install validated
- [ ] 8 GB / 4-core / no-GPU useful baseline
- [ ] deterministic suite has no unexplained failure
- [ ] objective restart recovery
- [ ] out-of-band STOP verified
- [ ] secret negative controls
- [ ] LOCAL_ONLY negative controls
- [ ] workspace isolation negative controls
- [ ] memory-poisoning lifecycle suite
- [ ] prompt/data injection adversarial suite
- [ ] protected-kernel write blocked
- [ ] capability-composition gate
- [ ] network-scope gate
- [ ] false success/failure claims blocked
- [ ] compaction fidelity target
- [ ] Context Lab release benchmark
- [ ] routing vs baseline benchmark
- [ ] reasoning-budget vs always-deep benchmark
- [ ] tool/skill-bloat benchmark
- [ ] browser/desktop golden journeys
- [ ] CLI fresh-session continuity
- [ ] Ponytail/minimal-build benchmark if enabled
- [ ] self-development promotion/rollback
- [ ] exact commit CI green
- [ ] defensible soak terminal result

## 89. Open questions to resolve by benchmark

- Mem0 vs OpenViking vs hybrid ownership boundaries.
- Exact 8 GB local expert set.
- Browser Use vs direct Playwright/CDP blend.
- Best learned routing algorithm after sufficient real FRIDAY data exists.
- Best context folding/compaction strategy for each model family.
- Whether Ponytail should be globally enabled for coding workers or selected by task.
- Best STT/TTS/voice architecture after latency/quality tests.
- Best Android control adapter after security testing.
- Which skill-distillation method proves worth its complexity.
- First CAD/simulation/engineering integrations for physical-project work.
- Technology Scout cadence that produces positive net value.
- Retention policy for personal/workspace research and black-box data.

## 90. Research evidence register

This PRD is informed by current research/documentation, including:

- Anthropic — Effective context engineering for AI agents: https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
- OpenAI — Safety and alignment in an era of long-horizon models: https://openai.com/index/safety-alignment-long-horizon-models/
- OpenAI — Designing AI agents to resist prompt injection: https://openai.com/index/designing-agents-to-resist-prompt-injection/
- OpenAI — Safety in building agents: https://developers.openai.com/api/docs/guides/agent-builder-safety
- ContextRot: https://github.com/GAIR-NLP/ContextRot
- ARC context management: https://arxiv.org/abs/2601.12030
- Slipstream compaction validation: https://arxiv.org/abs/2605.08580
- LLMRouterBench: https://arxiv.org/abs/2601.07206
- LLMRouter / xRouteBench: https://arxiv.org/abs/2608.06867
- Adaptive Test-Time Compute Allocation: https://arxiv.org/abs/2604.14853
- Learning When to Think: https://arxiv.org/abs/2608.20256
- Cost-effective Agent Test-Time Scaling: https://research.google/pubs/cost-effective-agent-test-time-scaling-2/
- Self-Improvements in Modern Agentic Systems survey: https://arxiv.org/abs/2607.13104
- Trace2Skill: https://arxiv.org/abs/2603.25158
- MetaSkill-Evolve: https://arxiv.org/abs/2607.05297
- MemSecBench: https://arxiv.org/abs/2607.27080
- OSWorld / OSWorld 2.0: https://os-world.github.io/
- ToolCUA: https://arxiv.org/abs/2605.12481
- Agentic neuro-symbolic planning and digital twins: https://doi.org/10.48550/arXiv.2606.08214
- Multi-agent scientific discovery, Nature (2026): https://www.nature.com/articles/s41586-026-10652-y
- Ponytail: https://github.com/DataSooth/claude_ponytail

These are architectural inputs and evaluation references, not mandatory dependencies.

## 91. Formal requirement matrix

The following requirements are release/implementation contracts. The table intentionally overlaps narrative sections so an autonomous engineering system can extract deterministic IDs.

| ID | Pri | Requirement | Acceptance evidence |
|---|---|---|---|
| CORE-001 | P0 | FRIDAY remains sole user-facing product control plane. | Delegated work returns through FRIDAY objective/policy/evidence contracts. |
| CORE-002 | P0 | Audit current repository before creating duplicate architecture. | Requirement ledger maps current files/tests/state. |
| INTENT-001 | P0 | Convert non-trivial intent into Objective Contract. | Vague idea golden journey becomes executable objective. |
| INTENT-002 | P1 | Ask user only for material ambiguity/authority. | Question-necessity benchmark. |
| OBJ-001 | P0 | Durable objective survives worker/model restart. | Crash/restart golden journey. |
| OBJ-002 | P0 | Version material user amendments. | Selective invalidation test. |
| OBJ-003 | P0 | Autonomous loops are bounded. | Infinite-retry negative control. |
| TRAJ-001 | P0 | Detect trajectory drift/no progress. | Long-horizon synthetic drift benchmark. |
| CTX-001 | P0 | Compile minimal working set per call. | Context telemetry. |
| CTX-002 | P0 | L0/L1/L2 progressive retrieval. | Large-catalog benchmark. |
| CTX-003 | P0 | Compaction preserves CriticalFactSet. | Lossy-summary mutation rejected. |
| CTX-004 | P1 | Context health drives trim/fold/reset. | Long-run benchmark. |
| CTX-005 | P1 | Large tool outputs offload to artifacts. | Subsequent prompt excludes raw payload. |
| CTX-006 | P1 | Semantic cache respects scope/freshness/equivalence. | Stale/cross-workspace negative tests. |
| COG-001 | P0 | Eliminate/Automate/Delegate enforced. | Simple deterministic tasks use zero frontier calls. |
| COG-002 | P1 | Reasoning effort adapts to difficulty/consequence. | Beats always-deep cost at quality threshold. |
| COG-003 | P1 | Advisor/executor route supported. | Frontier advisor bounded to hard decisions. |
| COG-004 | P1 | Sparse workers only when justified. | Simple-task no-swarm test. |
| MODEL-001 | P0 | Provider/model registry is replaceable and evidence-based. | Multi-provider adapter tests. |
| MODEL-002 | P0 | Configured route is not healthy until semantic probe. | Empty output -> degraded/unhealthy. |
| MODEL-003 | P1 | Learned router must beat simple baseline. | Versioned evaluation report. |
| MODEL-004 | P1 | Open-set model onboarding. | New mock route profiled without core rewrite. |
| MODEL-005 | P1 | Hardware-aware local model residency. | 8 GB stress test. |
| NEURAL-001 | P2 | Small FRIDAY-owned policy networks may train from verified data. | Candidate stays non-production until holdout win. |
| NEURAL-002 | P0 | No direct production-weight update from one interaction. | Architecture/unit test. |
| LEARN-001 | P1 | Neural/adaptor promotion tests forgetting/regression. | Promotion blocked on threshold regression. |
| MEM-001 | P0 | Canonical scoped durable memory. | Cross-worker restart recall. |
| MEM-002 | P0 | Provenance/trust/freshness/supersession stored. | Web-derived memory cannot impersonate user authority. |
| MEM-003 | P0 | Memory poisoning lifecycle tested. | Delayed-execution adversarial benchmark. |
| SKILL-001 | P1 | Skill quarantine/test/negative-test lifecycle. | Unverified skill cannot activate. |
| SKILL-002 | P1 | Skill distillation links to verified traces. | Skill artifact references evidence. |
| SKILL-003 | P1 | Dependency-based revalidation. | Relevant change invalidates; irrelevant change does not. |
| CAP-001 | P0 | Closed-world capability resolver. | Invented tool blocked pre-execution. |
| CAP-002 | P0 | Combined capability risk analyzed. | Read-private + arbitrary-send elevation test. |
| TRUST-001 | P0 | Trust is typed by runtime, never self-declared text. | trusted=true external payload remains untrusted. |
| TRUST-002 | P0 | Data lineage retained through transforms. | Untrusted-to-shell path requires gate. |
| SEC-001 | P0 | Secrets excluded from ordinary prompts/logs/skills. | Dummy-secret scans clean. |
| SEC-002 | P0 | LOCAL_ONLY physically blocked from public provider. | Forced-route returns NO_ROUTE/local route. |
| SEC-003 | P0 | Protected kernel cannot be autonomously weakened. | Worker write blocked. |
| SEC-004 | P0 | Prompt/data injection cannot increase authority. | Adversarial suite. |
| SEC-005 | P0 | Every worker has execution envelope. | Out-of-envelope action denied. |
| NET-001 | P0 | Objective-bound revocable network scope. | Expired/unknown host denied. |
| CLI-001 | P1 | Unified CLI gateway. | Equivalent work packet through >=2 adapters. |
| CLI-002 | P1 | Fresh CLI session can resume from durable handoff. | No hidden prior session required. |
| CODE-001 | P1 | Minimal-build policy benchmarked. | A/B diff/cost/quality report. |
| BROW-001 | P0 | Hybrid action route supports structured + GUI paths. | Golden journey/backend evaluation. |
| BROW-002 | P0 | Post-state verification required. | Click without state change cannot succeed. |
| VOICE-001 | P1 | Realtime voice, barge-in and mute correctness. | Voice latency/interruption suite. |
| REMOTE-001 | P1 | Remote commands paired/replay-protected. | Replayed command rejected. |
| MOBILE-001 | P2 | Android adapter remains capability-scoped. | Cannot bypass FRIDAY authority. |
| WORLD-001 | P1 | Structured provenance-aware world state. | State update/contradiction tests. |
| EXP-001 | P1 | Bounded experiment engine. | Cheap evidence route beats speculative loop. |
| EPI-001 | P1 | Important claims have evidence/confidence/freshness. | Engineering decision links to ledger. |
| FAIL-001 | P1 | Root-cause failures become Failure Genome patterns. | Similar failure retrieves prior repair. |
| VERIFY-001 | P0 | High-consequence work receives independent verification. | Builder false claim rejected. |
| VERIFY-002 | P0 | Every critical guard has negative control. | Red-path required for release. |
| STOP-001 | P0 | Out-of-band emergency stop. | Model refusal cannot prevent termination. |
| RES-001 | P0 | Resource Governor bounds processes/models/workers. | Low-RAM stress test. |
| PRO-001 | P1 | Proactive runtime is event-driven. | Idle system has no unnecessary inference loop. |
| SELF-001 | P1 | Self-development is isolated, benchmarked, rollbackable. | Production unchanged before promotion. |
| SCOUT-001 | P2 | Technology Scout maps adoption to measured need. | Trending repo not auto-installed. |
| CYBER-001 | P0 | Active cyber work restricted to explicit authorized scope. | Out-of-scope target blocked. |
| UX-001 | P1 | Normal and Developer Mode. | Visibility differs, authority does not. |
| ONB-001 | P1 | Progressive onboarding. | New user completes setup + useful task. |
| PKG-001 | P1 | Autonomous clean-machine bootstrap. | Clean Windows VM ready-state. |
| PKG-002 | P1 | Durable state separate from application bundle. | Upgrade preserves brain/state. |
| EVAL-001 | P0 | Model/context/capability promotion requires reproducible evals. | Versioned eval linked to build. |
| EVAL-002 | P1 | Context Lab maintained. | Release report includes context metrics. |
| EVAL-003 | P1 | Model Arena profiles candidates. | Vendor claim alone cannot promote route. |

## 92. Research-validated design decisions

**Keep:** one manager / many workers; persistent objectives; evidence-backed claims; exact literal preservation; shared canonical memory; skill lifecycle; model/provider abstraction; local-first privacy; browser/desktop abstraction; controlled self-development; negative-control testing.

**Upgrade:**
- Context Budget Manager -> CortexOS;
- Model Fitness Router -> Model Arena + baseline/learned router + abstention;
- self-learning -> trace/skill/data/candidate/promotion pipeline;
- computer use -> hybrid GUI/tool path router;
- action evidence -> Reality Ledger + proof-carrying actions + independent verifier;
- memory provenance -> typed trust + memory security lifecycle;
- task loops -> Objective OS + Trajectory Governor;
- token budgeting -> Cognitive Governor + adaptive reasoning compute;
- self-development -> scaffold-first + protected kernel + neural candidate lab.

**Do not hard-code yet:** one mandatory memory backend; one permanent browser backend; one specific local model; one learned router algorithm; one telephony provider; one self-improvement technique. These remain benchmark decisions.

## 93. Current engineering state disclaimer

This PRD does not claim the latest repository lacks every system named above. The first implementation phase must inspect the exact current HEAD, worktree, tests, running services and generated artifacts.

Previously reported work may already include: ActionEvidence; claim auditing; direct exact-literal actions; provider/gateway hardening; context/retrieval work; CI hardening; objective budgets; Windows EXE/packaging; protected kernel/self-development guards; soak harnesses.

Those reports are clues, not current proof.

## 94. Final product definition

FRIDAY is successful when a user can express a legitimate desired outcome and the system can acquire the context, knowledge, intelligence and capabilities needed to create the real result while remaining truthful, private, resource-aware and controllable.

The permanent architecture principle is:

**One visible intelligence. Many replaceable models. Minimal relevant context. Sparse specialists. Typed authority. Proof-carrying actions. Durable learning. Measured self-improvement.**

The product is not complete because it can explain how to build something.

It is moving toward the target only when it can turn the idea into working reality within the available capabilities, authority and physical constraints.

---

## 95. Amendment A1 (2026-09-26): domain partitioning and the updated implementation controller

> **Transcription note (added 2026-09-26; not part of the owner's text).** Added at the owner's instruction
> ("add these to prd documente and continue as told above"). §95.1 and §95.2 are the owner's text verbatim.
> Appendix C is the owner's updated implementation controller, verbatim. §95.3 is derived by the implementing
> agent so that the amendment's obligations that §91 does not already carry get deterministic IDs, as §91 does
> for sections 0–90; where §95.3 and the verbatim text differ, the verbatim text controls. Priorities in §95.3
> are derived: P0 where the controller puts the item in its negative controls (C§105) or release gate (C§106)
> and it is an authority or safety boundary, P2 for optional backends and future-facing items, P1 otherwise.
> The owner may re-rank.

### 95.1 Domain partitioning rule (owner's text, verbatim)

```text
# DOMAIN PARTITIONING RULE

Before sending a large autonomous program to any model, classify
requirements by provider safeguard domain.

Do not bundle unrelated restricted-domain implementation details into
the main general-engineering context.

Create separate bounded work programs for specialized domains such as:

- cybersecurity
- other provider-restricted expert domains

The main FRIDAY implementation program should implement interfaces,
authority boundaries, schemas, mocks and integration contracts for
those modules.

Operational domain-specific capability implementation and evaluation
runs separately using an appropriate compatible model/access path.

A safeguard intervention is a routing signal, not a reason to repeatedly
retry the same request.
```

### 95.2 Domain partition of the general build (owner's text, verbatim)

```text
Continue implementation of the F.R.I.D.A.Y. Master PRD.

IMPORTANT DOMAIN PARTITION:

This session is for general software engineering and autonomous-agent
architecture only.

Do not perform penetration testing, exploitation, scanning, credential
operations, malware execution, or offensive cybersecurity activity.

The PRD contains a future Cybersecurity Research Lab. In this session,
implement only its generic software boundaries where dependencies
require them:

- provider interface
- authorization schema
- sandbox abstraction
- capability registry integration
- audit/evidence contract
- network-scope representation
- mock provider
- deterministic policy tests

Leave operational cybersecurity integrations and security research
skills to a separate authorized Cyber Lab implementation program.

Continue all other PRD work autonomously from the existing requirement
ledger and repository state.

Do not restart completed work.

Audit current HEAD and durable implementation state, identify the next
dependency-ready unresolved non-cyber requirement, and continue the
autonomous engineering loop until all non-cyber PRD requirements reach
their truthful terminal states.
```

### 95.3 Requirement matrix addendum (derived; the verbatim text controls)

"C§n" is section n of Appendix C. Obligations that §91 already carries keep their §91 IDs: one manager
(CORE-001), progressive disclosure (CTX-002), compaction fidelity (CTX-003), cognitive governor, reasoning
budget and advisor/executor (COG-001–COG-004), code minimalism and Ponytail (CODE-001), future FRIDAY System One
networks (NEURAL-001), memory and poisoning (MEM-001–MEM-003), epistemic ledger (EPI-001), failure genome
(FAIL-001), closed-world resolution (CAP-001), composition (CAP-002), typed trust and lineage (TRUST-001,
TRUST-002), secret broker (SEC-001), LOCAL_ONLY (SEC-002), protected kernel (SEC-003), envelopes (SEC-005),
network scope (NET-001), CLI gateway, work packet and fresh sessions (CLI-001, CLI-002), independent verifier and
the Byzantine worker (VERIFY-001), negative controls (VERIFY-002), emergency stop (STOP-001), resource governor
(RES-001), Context Lab (EVAL-002), Model Arena (EVAL-003), technology scout (SCOUT-001), self-development
(SELF-001), voice, remote and mobile (VOICE-001, REMOTE-001, MOBILE-001), installer (PKG-001), experiment engine
(EXP-001), world model (WORLD-001) and authorized cyber scope (CYBER-001).

| ID | Pri | Requirement | Acceptance evidence | Source |
|---|---|---|---|---|
| DOMAIN-001 | P0 | Work is classified by provider-safeguard domain before delegation. Restricted domains (cybersecurity and other provider-restricted expert domains) run as separate bounded work programs, and the general build carries only their interfaces, authority boundaries, schemas, mocks and integration contracts. | A general-engineering work packet carries no restricted-domain operational detail; a restricted packet goes to its own program or is marked BLOCKED_EXTERNAL, never retried on the same route. | §95.1, §95.2, C§6, C§77 |
| MODEL-006 | P1 | The model registry records each route's policy and safeguard compatibility and known task restrictions. Dispatch runs task → domain classification → model fitness → policy compatibility → ALLOW, FALLBACK, LOCAL_ONLY, ASK_USER or BLOCKED, and an incompatible route is never retried. | A recorded safeguard intervention changes the next route (fallback or BLOCKED) instead of repeating it. | C§7 |
| CTX-007 | P1 | Context payloads are classed LOSSLESS_REQUIRED, RECOVERABLE_LOSSY_ALLOWED, SUMMARIZABLE or DROP_AFTER_EXTRACTION. One Compression Coordinator chooses PASS_RAW, CAVEMAN, SUMMARIZE, FOLD_TO_ARTIFACT or DROP_AFTER_EXTRACTION per payload and records source id and hash, transformation, transformed hash, critical fact set, recovery pointer and reversibility. LOSSLESS_REQUIRED is never lossy-compressed, and a failed transformation fails closed. | Lossless literals pass raw; a stacked summary, compression and provider compaction loses no critical fact; every transformed artifact is recoverable by hash. | C§16, C§18, C§19 |
| CTX-008 | P2 | Caveman is integrated only as a compression backend behind the coordinator, after the licence and provenance of each subcomponent are reviewed, and is benchmarked OFF, OUTPUT_ONLY, ENGINE and FULL on tokens, task success, latency, recovery, critical-fact and exact-literal retention and debugging quality. | Licence review recorded before any embedding; benchmark report. | C§18 |
| S1-001 | P1 | One System One Gateway (choose, score, yes_no, rank, classify, confidence) is the only interface FRIDAY code uses for fast typed decisions, with pluggable backends (Laya local, Jev hosted, FRIDAY's own future models). LOCAL_ONLY never reaches a hosted backend, and a hosted call is made only when data policy, health, benchmark and budget all allow it. | Gateway contract tests with a mock backend; a LOCAL_ONLY forced route never calls the hosted backend. | C§21, C§22, C§23 |
| S1-002 | P1 | System One confidence is calibrated empirically: predicted confidence against actual success is tracked, calibration curves set the autonomy thresholds, low confidence or backend disagreement escalates to System Two, and extra predictors run only when their expected value justifies them. | Calibration report; a disagreement or low-confidence case escalates. | C§25, C§26 |
| S1-003 | P2 | Laya is benchmarked locally (CPU latency, RAM, accuracy, confidence calibration, escalation rate, tool-selection accuracy) before it becomes the default local System One backend, and never replaces deep reasoning. | Local benchmark report on the 8 GB profile. | C§22 |
| RSCH-001 | P1 | Research is routed by depth (R0 fresh cache to R5 scientific-scale) against an immutable CanonicalInquiry (objective, scope, must-answer questions, non-goals, freshness, evidence standard). A drift guard rejects branches that do not reduce its uncertainty, and research has time, source, token and cost budgets and stops on expected information gain, not source count. | A trivial question never takes R4/R5; an irrelevant branch is rejected; a budget stop is recorded. | C§32, C§34, C§37 |
| RSCH-002 | P1 | Deep research runs behind one FridayResearchProvider interface, so providers (HyperResearch, model-based research, local research, scientific APIs) are swappable without rewrite, and a provider's own benchmark claims are evaluated independently before it is preferred. | Two providers behind the interface; any preference is backed by FRIDAY's own benchmark. | C§33 |
| RSCH-003 | P1 | Research provenance records source, parent discovery source, primary vs derivative, date, quality, freshness, retraction or supersession and claim support, and independence analysis counts copies of one source once. | Five copied articles count as one independent source. | C§36 |
| RSCH-004 | P1 | Research artifacts go to their owners: source corpus to the research store, verified claims to the epistemic ledger, project conclusions to the Project Brain, small durable user facts to memory, reusable procedures to skills. | A research run writes no raw source corpus into long-term memory. | C§35 |
| BRAIN-001 | P1 | A human-inspectable Project Brain holds structured architecture, requirements, decisions, assumptions, research, failures, experiments, objectives, incidents, artifacts, handoffs, procedures and entities. | Round-trip per record type; a human-readable export. | C§40 |
| ASSUME-001 | P1 | An assumption register stores each assumption's origin, confidence, consequence, validation and current state, and high-impact assumptions are validated early. | A high-impact unvalidated assumption schedules validation before dependent work. | C§87 |
| CF-001 | P2 | Important plans are compared against alternatives, doing nothing, deferring and experimenting first, with fact, measurement, simulation and inference kept apart. | The decision record shows the options compared and each claim's evidence class. | C§89 |
| ACT-001 | P0 | No success claim without matching evidence and no failure claim without attempt evidence, correlated by workspace, objective, version, action, target, executor, operation and timestamp: file (path, hash, readback), code (reproduction, diff, tests after), web (post-state, URL, response), email (message id or sent state), simulation (measurement artifact). | A claim without matching evidence is refused. | C§64, C§65 |
| BBOX-001 | P1 | A black box records the observable run (objective, amendments, policy, models, tools, capability calls, approvals, side effects, evidence, final state) without requiring hidden chain-of-thought. | A run is reconstructed from its black box. | C§66 |
| WS-001 | P0 | Workspaces are isolated: a request made in one workspace cannot reach another workspace's data. | A cross-workspace request is blocked. | C§105, C§106 |
| CAP-003 | P1 | The capability registry records id, version, backend, provenance, permissions, health, auth, risk, data classes, OS, latency, cost, tests and last verified; installed or configured is never reported as healthy without a probe. | An installed but failing capability reports unhealthy. | C§43 |
| SKILL-004 | P1 | Imported and generated executable skills pass the trust pipeline (source, pin, hash or signature, static scan including SkillSpector where compatible, dependency and CVE check, secret scan, permission extraction, deduplication, sandbox, dynamic behaviour, live value, quarantine, activate or reject), storing scanner version, scan date, source commit, findings and report hash. A static PASS is never treated as runtime-safe. | A fake skill containing an injection is rejected. | C§46, C§47, C§105 |
| SKILL-005 | P1 | Skills are evaluated in tiers (safety and validity, duplication and semantic overlap, live value) on correctness, reliability, token, latency, tool-call, context, security and maintenance cost with empirical weights, and a safe but useless skill is not activated. | A skill with no measured value stays inactive. | C§48, C§49 |
| SKILL-006 | P1 | An activated external skill records source, commit, hash, signature, dependency SBOM, scan and evaluation, and any content change moves it ACTIVE → UPDATE_UNVERIFIED → QUARANTINE until it is re-scanned and re-evaluated. | Editing an active skill's content quarantines it. | C§50 |
| SKILL-007 | P2 | Skill performance (use count, success, failures, corrections, token impact, time, affected app version) moves skills between ACTIVE, DEGRADED, NEEDS_REVALIDATION and DEPRECATED, and unused, duplicate, degraded, stale, superseded or unhealthy skills and capabilities are archived rather than left in the active catalog. | A garbage-collection run archives a planted stale duplicate. | C§51, C§97 |
| SUPPLY-001 | P1 | The trust pipeline covers the whole capability supply chain (plugins, MCP servers, repositories, binaries, packages, models, LoRAs, browser extensions, CLIs), recorded in an Agent Capability SBOM. | The SBOM lists every active external component with source, version and scan state. | C§52 |
| SANDBOX-001 | P1 | Untrusted executable capabilities are evaluated in a runtime behaviour sandbox that observes filesystem writes, process tree, network, DNS, environment and credential access and persistence, seeded where practical with fake secrets, sensitive files and network canaries; unexpected access fails the evaluation. | A planted canary read fails the candidate. | C§53 |
| MCP-001 | P0 | MCP servers are pinned at registration (identity, tool schema, scanned description, version and hash); tool output is untrusted by default; an unexpected schema, description or server change moves the server ACTIVE → SUSPENDED until it is revalidated. | An unexpected MCP schema change suspends the server. | C§54, C§105 |
| PRIV-001 | P1 | Each objective declares its privacy budget (allowed data classes, providers and external services), which CortexOS and the model router enforce. | A payload outside the objective's budget is refused a provider. | C§58 |
| OBJ-004 | P1 | Batch work persists per-item state, retries only the failed items and uses idempotency so that a crash or restart does not repeat side effects. | A three-item batch with one failure retries only that item; a restart duplicates nothing. | C§79 |
| SIDE-001 | P1 | A side-effect ledger records every meaningful external change (files, messages, browser writes, DB writes, application changes, external API writes) with its reversibility where possible. | Each side-effecting capability writes a ledger row the verifier reads back. | C§80 |
| SIDE-002 | P1 | Consequential tasks run plan → dry run → expected side effects → policy and approval → real run where practical, and the estimated blast radius (resources, users, money, data scope, reversibility, systems) raises reasoning, confirmation and verification. | The dry run shows expected effects before approval; a larger blast radius escalates. | C§81, C§82 |
| CHAOS-001 | P1 | Chaos tests cover browser crash, provider outage, Hermes crash, invalid model response, DB contention, network loss, malformed tool output, worker death and reboot, and FRIDAY fails or recovers truthfully in each. | A chaos suite report. | C§83 |
| CAPB-001 | P1 | When a requested capability is absent, FRIDAY searches its capabilities, skills, plugins and MCP servers, official options and GitHub, compares them, runs the trust pipeline and the sandbox, implements or adapts, tests, registers, and then returns to the user's original task. | A golden journey in which a missing capability is built and the original task completes. | C§92 |
| EVAL-004 | P1 | Research, skill, compression and capability-path arenas compare alternatives, and every promotion is empirical. | An arena report behind each promotion. | C§98 |
| EVAL-005 | P1 | Token productivity is tracked as verified useful work per 100K tokens: requirements closed, defects fixed, accepted artifacts, successful objectives, token usage, retries and wall time. | A productivity report per run. | C§99 |
| BROW-003 | P1 | Browser work uses a user-approved Chrome profile, a dedicated FRIDAY profile or a temporary profile, never silently another person's profile or account, and OTP, passkey, hardware-key and human-verification steps are handed to the person and resumed afterwards, with no bypass behaviour. | An unapproved profile is refused; an OTP step pauses for the person. | C§70, C§71 |
| UX-002 | P2 | Long tasks report meaningful milestones rather than every tool call, at the level the user chooses (detailed, balanced, major-only). | A milestone stream at each level. | C§103 |
| CYBER-002 | P0 | The general build implements the Cyber Lab only as generic boundaries: a provider interface; an authorization contract schema (target, ownership or explicit authorization, scope, allowed methods, network destinations, time window, environment, artifact handling, expiry) enforced at runtime and not extensible by a model; a sandbox tier abstraction (K1 ephemeral container, K2 WSL, K3 VM) with a router over privileges, GUI, kernel requirements, target, isolation, persistence, network and risk; capability registry integration; an audit and evidence contract; a network-scope representation; a mock provider; and deterministic policy tests. | A mock cyber worker aimed at an unauthorized host is blocked; an expired contract refuses. | §95.2, C§72, C§73, C§74, C§105 |
| CYBER-003 | P1 | Operational Cyber Lab work (Kali execution, cyber skill packs such as Claude-Red in the CyberSkillVault, specialist cyber models, purple-team runs) belongs to a separate authorized Cyber Lab program, not the general build. | Deferred from the general build by §95.1 and §95.2. | §95.1, §95.2, C§73, C§75, C§76 |

## 96. Amendment A2 (2026-09-27): durable execution, behavioural contracts and interoperability

> **Transcription note (added 2026-09-27; not part of the owner's text).** Added at the owner's instruction
> ("new ideas to be added in prd and continue as left"). Appendix D is the owner's text verbatim, and "D§n" below
> is its numbered section n. §96.1 and §96.2 are derived by the implementing agent, as §95.3 was, so that the
> obligations the matrix does not already carry get deterministic IDs; where they differ from the verbatim text,
> the verbatim text controls. Priorities are derived: P0 for the two systems the text names as the most important
> (durable execution and behavioural contracts) and for the two authority boundaries checked at execution time
> (approval staleness and the just-in-time authority check); P2 for experimental, bake-off, optional-backend and
> watchlist items; P1 otherwise. The owner may re-rank.

### 96.1 Scope rule carried by this amendment (derived from Appendix D's closing sections)

- The ten upgrades are built inside FRIDAY's existing seams, not by adopting ten new frameworks. An outside
  project (Temporal, AgentGateway, AgentAssert, AgentBench, OpenSharing) enters only as an optional adapter, a
  bake-off candidate, an evaluation pack or a watchlist item, through the capability fabric and the upstream lock,
  under the fabric's licence rules.
- With this amendment the core architecture is frozen as the v8 core. The master PRD gains no further named
  systems until the first implementation baseline run reports where the real gaps are; later discoveries go
  through the technology scout (SCOUT-001) and the capability builder, not a PRD rewrite.

### 96.2 Requirement matrix addendum (derived; the verbatim text controls)

Obligations the matrix already carries keep their IDs: durable objectives and versioned amendments (OBJ-001,
OBJ-002), bounded loops (OBJ-003), per-item idempotency (OBJ-004), the side-effect ledger and the dry run with
blast radius (SIDE-001, SIDE-002), the black box (BBOX-001), the failure genome (FAIL-001), trajectory drift
(TRAJ-001), MCP pinning and change suspension (MCP-001), the skill trust pipeline (SKILL-004), the capability
registry (CAP-003), the hybrid browser route (BROW-001), paired remote commands (REMOTE-001), workspace isolation
(WS-001) and federation's own policy check (§66), envelopes (SEC-005), the emergency stop (STOP-001), reproducible
evaluation and the arenas (EVAL-001, EVAL-004) and the technology scout (SCOUT-001).

| ID | Pri | Requirement | Acceptance evidence | Source |
|---|---|---|---|---|
| DUR-001 | P0 | A durable execution kernel under the objective OS records each significant state change as an append-only event (objective created, plan created, worker started, tool called, side effect committed, action verified, user interrupted, objective amended, worker crashed, objective resumed). Resume after a crash, restart, reboot, worker or model replacement, or a long absence of the user restores state from those events, and replay never re-executes a committed side effect: a committed side effect carries its external identifier (such as a message id) so replay can show it already happened. | A run killed after a side effect commits resumes from its events, and the side effect happens exactly once. | D§1 |
| DUR-002 | P2 | The durable runtime has a FRIDAY-native local backend and, for server or enterprise deployments only, an optional Temporal adapter behind the same interface; Temporal is never required on the desktop product. | The same durability test passes on the native backend with Temporal absent. | D§1 |
| BCON-001 | P0 | Critical objectives carry a behavioural contract of preconditions, invariants, governance rules and recovery mechanisms that the runtime checks before and during execution, rather than asking the prompt to enforce them. An action that would break an invariant or a governance rule is refused and the contract's recovery applies. AgentAssert is not a core dependency (new research, AGPL). | A contract with `max_external_writes: N` refuses write N+1; a broken invariant stops the actions that depend on it; `on_partial_failure: resume_failed_items_only` retries only the failed items. | D§7 |
| SIGNAL-001 | P1 | Human signals (correction, approval, denial, answer, stop, priority change, policy change, user back) from every surface (desktop, voice, Telegram, web, mobile) reach the objective OS through one signal bus. A waiting objective survives restarts while it waits and resumes from the signal. An amendment passes an amendment barrier that stops the old version's dependent actions, including one a worker on another device is running. | An amendment sent from a second surface while a worker runs stops the old version's dependent actions; a waiting objective resumes from a signal after a restart. | D§8 |
| APPROVAL-001 | P0 | An approval is a lease bound to the objective id, objective version, action class, target, blast radius, plan hash and expiry. A material change to any of them makes it STALE, and a stale approval authorises nothing. A standing permission ("FRIDAY may publish normal social posts") is a scoped policy rule, not an approval. | An approval given before a material plan change does not authorise the changed action; an expired approval refuses. | D§9 |
| JIT-001 | P0 | Authority is checked at plan time and again immediately before a consequential action: capability hash, skill version, authentication, objective version, approval validity, network grant and target-state compatibility. Any change refuses the action until it is revalidated. | A capability whose hash changed between planning and execution is refused at execution. | D§10 |
| FED-001 | P2 | FRIDAY-to-FRIDAY and agent-to-agent federation (§66) uses A2A v1.0 as its wire protocol through a federation gateway. A2A carries discovery, delegation and artifact exchange only; identity, workspace, authority, privacy, data class, approval, trust and evidence stay FRIDAY's, and a remote Agent Card's claimed skills grant no access to local data. | A remote agent whose card claims a capability is refused local data without a local policy grant. | D§2 |
| CONN-001 | P2 | Before any further gateway layer is added, a bake-off compares FRIDAY-native connectivity, AgentGateway beneath FRIDAY, and a hybrid (AgentGateway transport with FRIDAY policy and routing) on latency, RAM, provider support, MCP, A2A, OAuth, local operation, Windows, security, failure recovery, observability and code complexity. The decision may delete code rather than add it. | A bake-off report with the recorded decision. | D§3 |
| WEBMCP-001 | P2 | The browser action route prefers, in order: WebMCP when present and trusted, an official API or tool, application scripting, accessibility or DOM, vision, coordinates. The WebMCP consumer is an experimental adapter that nothing depends on; a WebMCP producer for FRIDAY-owned sites is experimental. | With a trusted WebMCP tool present the route selects it; without one it falls back to the next tier. | D§4 |
| CAP-004 | P1 | Every capability declares read_only, consequential, reversible, idempotent, external_side_effect and requires_confirmation, and policy and verification read these declarations. | Every registered capability declares all six; a consequential capability with no confirmation path fails the registry check. | D§4 |
| OTEL-001 | P1 | A telemetry plane records objective, agent, model, tool, worker, capability, evidence and verification spans using the OpenTelemetry GenAI semantic conventions (model, tokens, latency, tool count, context size, skill, outcome, cost). Metadata telemetry is on and local by default; prompt and completion content is off by default and opt-in in developer mode; secret content is never recorded. | A run's span tree attributes its wall time per component; a default-configuration trace holds no prompt text; a planted dummy secret never appears. | D§5 |
| TRAJ-002 | P1 | A trajectory debugger normalises a failed run's trace, derives invariants, checks them step by step and names the first critical failure step and its root cause, which feed the failure genome and a regression test. | On a synthetic trace that breaks at step 5 and fails at step 25, the debugger names step 5. | D§6 |
| EVAL-006 | P2 | An external evaluation pack (AgentBench now, alongside OSWorld, the Context Lab, the Model Arena and the golden journeys) is run as independent evidence, not as release authority; a substantial regression on it is reported. | An external-pack report linked to a build. | D§11 |
| EXCH-001 | P2 | Capability sharing, when the user chooses to publish, goes through a capability exchange adapter after the skill trust pipeline and signing; OpenSharing is a watchlist item, not a bespoke marketplace protocol, and nothing is implemented now. | Deferred by the text: watchlist only. | D§12 |

## 97. Amendment A3 (2026-09-27): the local-model hardware rule and the prompt research corpus

> **Transcription note (added 2026-09-27; not part of the owner's text).** Added at the owner's instruction: the
> owner pasted the text in Appendix E into the running session, under the standing instruction given for A2 ("new
> ideas to be added in prd and continue as left"). Appendix E is the owner's text verbatim, and "E, <heading>"
> below names one of its headings. The same paste repeated Appendix D (already recorded, not duplicated) and
> carried a copy of a vendor chat product's system prompt, the kind of material the corpus below holds; that prompt
> text is not recorded in this repository, in keeping with E's own rule not to copy such prompts. §97.1 and §97.3
> are derived by the implementing agent, as §96.1 and §96.2 were; where they differ from the verbatim text, the
> verbatim text controls. Priorities are derived: P1 for the hard rule, P2 for measurement and prompt compilation,
> P3 for corpus research. The owner may re-rank.

### 97.1 How this amendment sits with the v8 freeze (derived)

- §96.1 froze the core's named systems until the first implementation baseline reports the real gaps. This
  amendment adds one binding rule, which constrains requirements the matrix already carries rather than adding a
  system: the local-model hardware rule (E, "New hard architecture rule", which the owner asks to add verbatim;
  quoted in §97.2). It governs hardware-aware residency (MODEL-005), the resource governor (RES-001), open-set
  onboarding (MODEL-004) and the low-end goal (G-12), and it adds MODEL-007.
- PromptOS and its parts (the prompt compiler, instruction-conflict detection, the prompt health score, the
  pattern miner and prompt evolution tracking) are recorded as obligations of the existing context engine
  (CTX-001 minimal working set, CTX-002 progressive disclosure, CTX-004 context health) and of the technology scout
  (SCOUT-001), at P2 and P3. Whether they become a named core system is decided after the baseline, per §96.1.
- The prompt corpus (`asgeirtj/system_prompts_leaks`) is a research corpus. In fabric terms it is REFERENCE_ONLY:
  never a runtime dependency and never a source of production prompt text. Its contents are other vendors'
  prompts published as leaks, so their terms of use are unclear: before any clone, its licence and terms are read
  and recorded and it is pinned through the upstream lock like any upstream. FRIDAY keeps only derived, compact,
  FRIDAY-native rules with the benchmark evidence that justified each, never copied prompt text.
- The model names and sizes in Appendix E (Laya, FunctionGemma, EmbeddingGemma, MiniCPM5-1B and 2B, MiniCPM-V
  4.6) are candidates from the owner's research, not measurements taken here. A model enters the default pack
  only after the Model Arena measures it on the reference profile (MODEL-003, MODEL-008).
- Reference hardware: Appendix E names the target machine as 16 GB RAM with 4 GB VRAM. The development host,
  measured on 2026-09-27, has 15.4 GB RAM and an integrated AMD Radeon GPU with 0.5 GB of dedicated memory (the rest
  shared from system RAM), so it is not that profile. The policy is written for the owner's stated profile; the
  residency manager reads the actual device's memory rather than assuming either, and G-12's baseline (8 GB RAM, 4
  cores, no dedicated GPU) still stands.

### 97.2 The hard rule (owner's text, verbatim, from Appendix E)

> **FRIDAY must not solve hardware limitations by loading increasingly large local language models. Local intelligence should be decomposed into small specialized models, deterministic software, retrieval, skills and temporary general workers. On the reference 16 GB RAM / 4 GB VRAM Windows system, individual default local model artifacts must remain below 2 GB, only necessary models may be resident, and CortexOS should minimize context before increasing model size. Frontier models are escalations for high-value reasoning rather than permanent local requirements.**

### 97.3 Requirement matrix addendum (derived; the verbatim text controls)

Obligations the matrix already carries keep their IDs: hardware-aware residency (MODEL-005), the resource
governor (RES-001), open-set onboarding (MODEL-004), the learned router and Model Arena (MODEL-003), the minimal
working set, progressive disclosure and context health (CTX-001, CTX-002, CTX-004) and the technology scout
(SCOUT-001).

| ID | Pri | Requirement | Acceptance evidence | Source |
|---|---|---|---|---|
| MODEL-007 | P1 | The default local model pack obeys a local model policy: every default artifact is below 2 GB (at most 1.6 GB preferred); on the reference profile at most one general local model is resident at a time, and a vision model loads on demand, displacing it when memory requires; default local context is 4K to 8K tokens and more needs a resource check; context is minimised before model size is raised; a candidate above the cap is refused for the default pack unless the owner overrides. Frontier models are escalations, never local requirements. | Onboarding refuses a 3.7 GB artifact for the default pack and accepts it only with an owner override; loading a vision model while a general model is resident unloads the general model first when memory requires; residency reads the actual device's memory. | E, "New hard architecture rule", "I would make `<2 GB` a formal FRIDAY policy", "And only ONE big local worker stays hot" |
| MODEL-008 | P2 | The local escalation chain (the smallest general model, then the next, then a frontier model) and each model's place in it are chosen from measured Model Arena results, with a verifier deciding when to escalate, not by assumption. | An arena report on the reference profile giving, per tier, the share of local tasks completed and verified and the escalation rate. | E, "MiniCPM5-1B might actually be more important than MiniCPM5-2B" |
| PROMPT-001 | P2 | A model call's instructions are compiled from ranked layers (system policy, product policy, worker contract, objective, skill, task): a lower layer that contradicts a higher one is dropped and the conflict reported, duplicates are removed, and the compiled prompt carries a health measure (duplicated or contradictory instructions, unused tool schemas, stale facts) that the context engine acts on before the call. | A compile in which a skill contradicts the worker contract keeps the contract's rule and reports the conflict; a duplicated instruction appears once; the health measure falls when unused tool schemas are added. | E, "Build a new PromptOS", "Add Prompt Instruction Conflict Detection", "Add Prompt Entropy Score" |
| PROMPT-002 | P3 | Published agent system prompts are a research corpus only: a pattern miner extracts candidate design rules (delegation, context, tool search, confirmation, research, skill loading, failure handling) and version-to-version changes, and a candidate becomes a FRIDAY rule only after an A/B benchmark shows it helps. Prompt text is never copied into FRIDAY and the corpus is never a runtime dependency. | A candidate rule with its benchmark result and adoption decision; the repository holds no copied vendor prompt text. | E, "`system_prompts_leaks`: yes, but give it the right job", "Add a Prompt Pattern Miner", "Add Prompt Evolution Tracking" |

## 98. Amendment A4 (2026-09-28): adaptive hardware and the two reference machines

> **Transcription note (added 2026-09-28; not part of the owner's text).** Added at the owner's instruction, in
> reply to the owner actions raised on 2026-09-27 (the governor's RAM thresholds on this host, Playwright for
> BROW-001, the uncommitted tree). Appendix F is the owner's text verbatim, and "F, <heading>" below names one of its
> headings. §98.1 is derived by the implementing agent; §98.2's requirement texts are the owner's own ("Add these
> formal requirements"), and only their acceptance evidence and priorities are derived: P0 for RES-002 and RES-003,
> because the fixed threshold misjudges this machine today (an idle system reads as pressured and background work is
> shed at rest); P1 for the rest. The owner may re-rank. The owner's explicit request overrides the §96.1 freeze for
> these six.

### 98.1 Decisions carried by this amendment (derived)

- Two reference machines, both first-class: REFERENCE-A (15.4 GB RAM, integrated GPU, about 0.5 GB dedicated
  VRAM - the development host) and REFERENCE-B (16 GB RAM, 4 GB dedicated VRAM). FRIDAY must run usefully on both,
  and neither is tuned by hand: FRIDAY profiles the host and adapts (F, "Add AdaptiveHardwareGovernor").
- The resource thresholds are not raised to fit REFERENCE-A. Pressure is judged against the machine's own
  baseline and headroom, with static percentages only as guardrails, through GREEN, YELLOW, ORANGE and RED states
  with hysteresis (F, "The 76% RAM issue", "Resource states"). This supersedes the owner action raised under
  RES-001 on 2026-09-27.
- Internal policy is capability-based (can_keep_2b_hot, max_parallel_workers, preferred_context_tokens and so on),
  never marketing tiers; tier names may exist in the UI only (F, "Important: make profiles capability-based").
- Model choice and CPU/GPU offload are benchmarked on the host rather than assumed (F, "Make model choice
  machine-aware too", "Add automatic machine benchmarking"). MODEL-007's 2 GB artifact cap still holds.
- Playwright is installed into the project's test environment on REFERENCE-A, keeping browser and test
  dependencies out of the minimal production runtime, and BROW-001's real journey is run and its resource cost
  recorded here; if Playwright makes the minimum host unusable, that is product evidence for optimising the browser
  backend, not a reason to exempt the machine (F, "Playwright decision").
- The tree is committed as a recoverable checkpoint before further major slices (F, "Commit decision").

### 98.2 Requirement matrix addendum (the owner's requirement text; acceptance derived)

| ID | Pri | Requirement | Acceptance evidence | Source |
|---|---|---|---|---|
| RES-002 | P0 | FRIDAY SHALL detect and benchmark the execution host and derive resource/model/concurrency policies from measured machine capability instead of assuming one fixed hardware profile. | On each reference machine a hardware qualification produces a machine capability profile (RAM, idle baseline, CPU, GPU and VRAM, disk, power) and the concurrency, browser and model policy derived from it; the two machines get different measured profiles from the same code. | F, "Add these formal requirements" |
| RES-003 | P0 | Resource pressure thresholds SHALL be calibrated relative to operating-system baseline, available headroom and observed system behavior. Static percentages may be used as guardrails but SHALL NOT alone determine health. | A machine idling at 76% RAM reads GREEN; the same machine losing headroom fast reads pressured before any fixed percentage is crossed; a state changes only after the change persists (hysteresis), with no flapping around a boundary. | F, "Add these formal requirements" |
| RES-004 | P1 | FRIDAY SHALL re-evaluate relevant hardware/runtime capabilities when material environment changes are detected. | A changed RAM total, GPU or power source triggers re-profiling of the affected capabilities, recorded with its cause. | F, "Add these formal requirements" |
| MODEL-009 | P1 | Local model loading, quantization, context size, CPU/GPU offload and residency SHALL be dynamically selected against measured RAM, VRAM, latency, thermal and stability constraints. | A benchmark of CPU-only, partial and full feasible GPU offload on each reference machine chooses the configuration, and the choice differs where the measurements differ. | F, "Add these formal requirements" |
| MODEL-010 | P1 | Failure to run a preferred local model SHALL degrade to a smaller/local alternative or policy-permitted remote route without making FRIDAY itself unavailable. | A local model that fails to load or runs out of memory falls back to a smaller model or a permitted remote route, and the request completes. | F, "Add these formal requirements" |
| BG-001 | P1 | Background learning, research, indexing and proactive work SHALL run only when current machine headroom, user activity, power state and objective priority permit it. | Background work is deferred while the person is active or the machine is pressured or on battery, and runs when it is idle, plugged in and has headroom. | F, "Add these formal requirements" |

## 99. Amendment A5 (2026-09-29): live browser readiness, test isolation and the P0 drive

> **Transcription note (added 2026-09-29; not part of the owner's text).** Added at the owner's instruction, in reply
> to the follow-up report of 2026-09-28. Appendix G is the owner's text verbatim, and "G, <heading>" below names one
> of its headings. §99.1 is derived by the implementing agent. §99.2's two requirements are the owner's invariants
> ("This becomes a product invariant"; "I would formalize this as TEST-ISOLATION-001"), worded as requirements with
> derived acceptance; TEST-ISOLATION-001 is P0 because it protects the owner's live data, BROW-004 is P1. The owner
> may re-rank.

### 99.1 Decisions carried by this amendment (derived)

- The browser extra is installed in FRIDAY's live environment, not only the test one, and browser readiness is a
  real launch - extra installed, Chromium build present, a harmless public page loaded and read - on both reference
  machines, with its cost recorded (G, "1. Browser runtime").
- RES-003 gains the owner's practical rule: static percentage thresholds are emergency guardrails, not the primary
  pressure classifier. No machine is special-cased by hostname; a third machine is profiled the same way
  (G, "2. Hardware governor").
- The audit rows written by tests are not deleted: the contamination is recorded as an incident (V7-D52), and the
  invariant that automated tests never write live user-state stores becomes TEST-ISOLATION-001 (G, "4. Audit rows").
- Provider-shaped fake keys in tests are replaced by clearly synthetic values, or, where the test is about
  recognising a provider's key shape, assembled at run time; secret scanning and push protection are not weakened or
  bypassed, and this is settled before the branch is first pushed (G, "5. Fake-key fixtures").
- Sequence: finish the follow-up checkpoint, run the full suite, commit, leave the tree clean, inspect the diff
  against main, merge; Slice 7 continues from main on a new branch (G, "6. Merge").
- A provider safety check that keeps failing is a route-health signal, not a reason to retry: bounded retry, then
  persist state and switch route or start a fresh session - the safeguard-aware routing of DOMAIN-001 (G, "7.").
- Scope: no large new architecture on the implementation branch for now; unresolved P0s are driven toward zero in
  the owner's order - live browser readiness, the adaptive resource governor, CORE-001, ACT-001 and action truth,
  DOMAIN-001, MCP-001, EVAL-001, remaining security and isolation P0s, CTX retrieval quality, the Cyber interface
  boundary only (G, "One thing I would change in the current priority").
- CTX-002 is not complete at 75.9% single-search recall. Before any larger model: benchmark lexical (BM25),
  embedding, hybrid, metadata filters, a small local reranker and a skill/tool reranker on Recall@1, @3 and @5,
  schema tokens and latency; the target the owner names is Recall@1 at least 90% and Recall@3 at least 97% while
  keeping the 80% disclosure reduction (G, "CTX search at 75.9%").

### 99.2 Requirement matrix addendum (derived wording; the verbatim text controls)

| ID | Pri | Requirement | Acceptance evidence | Source |
|---|---|---|---|---|
| TEST-ISOLATION-001 | P0 | Automated tests may read production-like schemas but never write, or open, the owner's live user-state stores - the store, the audit log, the workspace, nonce, cooldown and access files, the logs - neither in the test process nor in any process a test starts. | A full suite run with FRIDAY not running leaves every file under the live data/ and logs/ unchanged, and the runner fails a run that changes one; a test that opens a live store is refused; a process a test starts resolves the test session's state root. | G, "4. Audit rows" |
| BROW-004 | P1 | Browser readiness is a real launch: the browser extra installed, its Chromium build present, and a headless launch that loads and reads a harmless public page is BROWSER_READY; importing Playwright is not readiness, and a missing package or browser build is reported as such. | The readiness probe reports BROWSER_READY from FRIDAY's own environment on each reference machine, with the launch's time and memory recorded; with the package or the browser build missing it reports which. | G, "1. Browser runtime" |

## 100. Amendment A6 (2026-09-30): the computer-use fabric and the Cua evaluation

> **Transcription note (added 2026-09-30; not part of the owner's text).** Recorded under the owner's standing
> instruction of Amendment A5 - new architecture goes into the PRD and research registry, not onto the
> implementation branch, while the unresolved P0s are driven down. Appendix H is the owner's text verbatim, and
> "H, <heading>" below names one of its headings. §100.1 is derived by the implementing agent. §100.2's requirements
> are the owner's text with derived acceptance. They are ranked P1 (MODEL-011, a "MAY", P2) because A5 put the P0s
> first; the owner's text calls Cua Driver "high-priority" and proposes a Cua Driver spike as the next
> implementation step, so the owner decides whether that spike comes before the remaining P0s, and may re-rank.
> What the text says about Cua's current features, versions, permission modes, issue tracker and model card is the
> owner's research as of 2026-09-30, not verified here; it is checked against a pinned clone before any adoption
> decision rests on it.

### 100.1 Decisions carried by this amendment (derived)

- The action-path router (§48, BROW-001) grows into a provider-neutral computer-use fabric: structured APIs and
  scripts first, then the browser (WebMCP where offered, Playwright and the DOM), then desktop backends, then vision,
  with screen coordinates the last resort. Cua Driver is a candidate desktop backend under it, not FRIDAY's desktop
  architecture, and it does not replace Playwright for browser-only work (H, "Add: ComputerUseFabric").
- Background desktop operation is the reason to adopt it: FRIDAY acting on permitted windows while the person keeps
  the physical pointer, keyboard and foreground (H, "The biggest reason I want it"). Each background session has an
  owner (objective, worker), an explicit window and application set, a permission manifest, its own cursor where
  the backend supports one, an evidence stream and a lease (H, "BackgroundDesktopSessions").
- Default permission mode is bounded: FRIDAY's policy, then its execution envelope, then the backend's own bounded
  manifest (applications, windows, actions, file roots, clipboard, expiry) - two independent boundaries. An
  unrestricted or approval-bypassing mode is never normal operation (H, "But we should NOT give Cua unrestricted
  host authority").
- A backend's report is not proof: every computer-use action is checked against an independently read post-state
  (H, "Cua also fits our evidence philosophy"). Desktop readiness is earned like BROWSER_READY: installed, signature
  and integrity checked, launched, windows enumerated, an accessibility tree read, a background action taken and its
  post-state verified, multi-monitor where relevant - otherwise a named degraded state and the router falls back
  (H, "There are current Cua issues we must not ignore").
- Supply chain: Cua comes in through the capability fabric like any upstream - licence read, commit pinned in the
  upstream lock, absent means UNAVAILABLE - and before any binary is bundled, its signing (upstream-signed, built
  from pinned source, or signed with FRIDAY's package) is settled against Windows Smart App Control
  (H, "Very important: code signing").
- Evaluation: the paths are compared on the same execution-based tasks (a pinned Cua Bench pack among them) for
  success, latency, tokens, actions, retries, RAM, CPU, foreground disruption, wrong targets and verified post-state
  (H, "Cua Bench"). CUA-S1 is research and benchmark only, not a production dependency; a small local UI model is
  used only after it beats the deterministic and frontier baselines there (H, "CUA-S1").
- Environments: the person's own desktop for normal personal tasks; an isolated sandbox or VM for untrusted work
  and testing; the Cyber Lab's own VM or container, never the personal desktop. A cloud fleet is optional, never
  required (H, "Cua Sandbox / Fleet").

### 100.2 Requirement matrix addendum (the owner's requirement text; acceptance derived)

| ID | Pri | Requirement | Acceptance evidence | Source |
|---|---|---|---|---|
| CUA-001 | P1 | FRIDAY SHALL provide a provider-neutral ComputerUseFabric supporting structured browser, desktop, visual and isolated-computer execution backends. | One interface serves the Playwright browser backend and at least one desktop backend, each registered through the capability fabric and chosen by the action-path router; a missing backend reports UNAVAILABLE without breaking the others. | H, "I would add these requirements" |
| CUA-002 | P1 | Where OS/application support permits, FRIDAY SHALL execute desktop tasks without unnecessarily moving the physical pointer, stealing focus or interrupting the user's foreground session. | On a Windows reference machine a desktop task on a background window completes with the physical pointer, the foreground window and focus unchanged, measured before and after; where the target cannot be driven in the background, that is said before acting. | H, "I would add these requirements" |
| CUA-003 | P1 | Each concurrent background computer-use worker SHALL receive an explicit allowed window/application set. | A session acts only on its listed windows and applications; an action on any other is refused and recorded. | H, "I would add these requirements" |
| CUA-004 | P1 | Computer-use actions SHALL not count as successful solely because the automation backend returned success. | An action counts as done only when an independent read of the application's state afterwards shows the intended change; a backend reporting success with no change is recorded as a failure. | H, "I would add these requirements" |
| CUA-005 | P1 | Cua/Playwright/other computer-use backend health SHALL be validated using real action + independently observed post-state. | A backend is reported ready only after a real action with an independently observed post-state on this machine; a failed step is named (for example the binary blocked by code integrity, the launch failed, no accessibility tree) and the router falls back. | H, "I would add these requirements" |
| CUA-006 | P1 | Alternative computer-use paths SHALL be benchmarkable against execution-based tasks for correctness, latency, resource cost and interaction count. | The same pinned task set runs through each available path and reports success, latency, tokens, actions, retries, RAM, CPU, foreground disruption, wrong targets and verified post-state per path. | H, "I would add these requirements" |
| CUA-007 | P1 | Background computer use SHALL be tested for unintended focus theft, pointer movement and window-order changes. | Each background run records focus changes, pointer movement and window-order changes, and a path that disturbs the foreground is flagged. | H, "I would add these requirements" |
| CUA-008 | P1 | Concurrent computer-use sessions SHALL have explicit ownership, leases and resource boundaries. | Each session has an owner (objective and worker), a lease with an expiry, its window set and resource limits; two sessions cannot act on the same window, and an expired lease stops the session. | H, "I would add these requirements" |
| MODEL-011 | P2 | FRIDAY MAY use/train small specialized UI decision models only after they outperform deterministic/frontier baseline routes on Cua Bench/FRIDAY evaluations. | A small UI decision model is put on a path only after it beats the deterministic and frontier baselines on held-out execution-based tasks, with its licence checked; until then it is a research and benchmark entry. | H, "I would add these requirements" |

## 101. Amendment A7 (2026-09-30): FRIDAY runs its workers the way its build should

> **Transcription note (added 2026-09-30; not part of the owner's text).** The owner's directive of 2026-09-30 is
> about the build harness - the build of FRIDAY is too serial and too token-hungry - and ends in the owner's own
> words "DO IT SAME FOR FRIDAY TOO". Read (assumed; the owner may correct it) as: the same architecture governs how
> FRIDAY itself runs Hermes and coding workers for the person. The directive is kept verbatim in
> `docs/plans/build-harness/00-owner-directive-2026-09-30.md` rather than duplicated here, because it governs the
> build harness as much as the product; its build-harness rules (BUILD-001..010, BUILD-EFF-001/002) live in
> `docs/plans/build-harness/01-plan.md`, not in this PRD. §101.2 is the product side, worded by the implementing
> agent from the directive; ranked P1 so the P0 burn-down keeps its order, and the owner may re-rank. Its claims about
> other projects (Symphony, RTK, Beads, Superpowers, harness optimizers and the rest) are the owner's research, not
> verified here.

### 101.1 Decisions carried by this amendment (derived)

- Scheduling is code, intelligence is the model: which task is ready, which worker is idle, which dependency finished,
  which worktree belongs to whom, retries, backoff, stalls and timeouts are decided deterministically; a model is
  woken for a failure, a merge conflict, an ambiguous requirement or a failed verification.
- Parallelism is earned: independent tasks over disjoint files run as waves, within what the resource governor admits
  on this machine; otherwise work is sequential. A worker is spawned for parallelism, specialisation, an isolated
  context or independent verification, never because more agents sound powerful.
- Every worker is fresh, small and bounded: a compact packet (the task, the current head, the relevant files and
  tests, any previous failure), a stable prompt prefix, a token, turn, tool-call and wall-time budget with a soft
  limit that asks for progress and a hard limit that stops it, and a structured result of a few hundred tokens with
  the transcript on disk.
- Output reaching a model is compressed where exactness is not needed - counts and failures, not thousands of lines -
  with the raw output recoverable; one compressor per stage unless a benchmark says otherwise.
- The cost is measured: tokens by source per model call, and verified work per token and per minute per strategy;
  anything added to the harness stays only if removing it makes a benchmark worse.

### 101.2 Requirement matrix addendum (derived from the directive; the verbatim text controls)

| ID | Pri | Requirement | Acceptance evidence | Source |
|---|---|---|---|---|
| ORCH-001 | P1 | An objective's independent tasks run as dependency waves through isolated workers, scheduled by code from their dependencies and predicted file surfaces, within the resource governor's concurrency. | Given a plan whose tasks form independent branches over disjoint files, the branches run at once, a task sharing a file with a running one waits, a task starts only when its dependencies are done, and concurrency never exceeds what the governor admits. | Directive, "Agent Wave Scheduler", "File Conflict Prediction" |
| ORCH-002 | P1 | A worker is spawned only for parallel work, specialisation, context isolation or independent verification. | Each worker records why it was started; a request that fits none of the four runs in the current worker. | Directive, "Agent spawn rule", "Parallelism Value Test" |
| ORCH-003 | P1 | Every worker carries a token, turn, tool-call, research-call and wall-time budget, with a soft limit that asks for progress and a hard limit that stops it. | At the soft limit the worker reports progress, blocker and why more budget helps; at the hard limit code stops it unless the budget is raised; a runaway worker is stopped without a model noticing. | Directive, "Hard budgets", "Token Stop-Loss", "Wall-Time Stop-Loss" |
| ORCH-004 | P1 | Workers hand results over as structured artifacts and references, not transcripts. | A worker's result reaches FRIDAY as a record (status, commit, tests, evidence references, blockers) of a few hundred tokens; the transcript stays on disk and is read only on demand. | Directive, "Artifact Handoffs" |
| ORCH-005 | P1 | A task starts on the least costly model predicted to meet it and escalates only on measured need. | Each escalation to a stronger model is recorded with its cause (a failed test, a failed verification, a stated need); tasks that pass on the first model are not escalated. | Directive, "Cascade Model Strategy" |
| ORCH-006 | P1 | Concurrent code workers work in isolated worktrees and branches, and FRIDAY integrates them. | Two concurrent coding workers never share a checkout; each result is integrated by FRIDAY, and a conflict becomes an integration task with both requirements in hand. | Directive, "Every worker gets its own Git worktree" |
| ORCH-007 | P1 | Output reaching a model is compressed where exact text is not needed, and the raw output stays recoverable. | Test, git and search output reaches a model as counts and failures within a size cap, with the full output kept as an artifact it can ask for; no two compressors are stacked on one stage unless benchmarked. | Directive, "RTK", "OutputCompressionRouter", "Failure-Focused Output" |
| ORCH-008 | P1 | A coding worker iterates on the tests its change affects; the full suite runs at checkpoints. | A worker's change runs the affected tests; the objective's checkpoint runs the full suite; a failure reports the failing ids and a pointer to the full log. | Directive, "Test Sampling During Iteration" |
| ORCH-009 | P1 | Every model call is attributed by source, and verified work is measured per token and per minute. | Each call records its input by source (instructions, task, code, tool results, history) and its output; per objective FRIDAY reports verified work against tokens, wall time, retries and tool calls. | Directive, "TokenAttribution", "Agent cost accounting" |
| ORCH-010 | P1 | Nothing becomes a default in FRIDAY's worker harness unless removing it makes a benchmark worse. | Each default instruction, hook, agent or skill has a recorded evaluation showing it helps; one that does not is removed. | Directive, "Harness Minimalism Gate" |
| ORCH-011 | P1 | Worker supervision is event-driven code; a model is woken only when judgement is needed. | Starts, exits, heartbeats, timeouts, test results and resource limits are watched by code; a model is woken only for a failure, a conflict, an ambiguity or a failed verification. | Directive, "ZeroTokenSupervisor" |
| ORCH-012 | P1 | Worker prompts keep a stable prefix and a dynamic suffix, so provider prompt caching holds. | Rules, role and tool definitions come first in a fixed order; nothing volatile (timestamps, ids, machine state) sits in the prefix; the cache hit rate is measured per worker. | Directive, "prompt-cache-aware compilation" |
| ORCH-013 | P1 | A finished task that invalidates an assumption cancels the running tasks built on it. | When a task's result invalidates an assumption later tasks depend on, the scheduler cancels those still running and replans them. | Directive, "Early Cancel" |
| ORCH-014 | P1 | FRIDAY's worker harness is measured against its own baseline for tokens and wall time at an equal or better verification rate. | On representative objectives the median model tokens fall at least 60%, and the wall time at least 40% on parallelisable work, against the recorded baseline, with the verification rate held. | Directive, "BUILD-EFF-001", "BUILD-EFF-002" |

## 102. Amendment A8 (2026-10-02): Capability Forge - "Friday, learn this"

> **Transcription note (added 2026-10-02; not part of the owner's text).** The owner's proposal is kept verbatim in
> `docs/plans/build-harness/00-owner-directive-2026-10-02d.md`. §102.2 restates its twelve requirements with
> acceptance evidence worded by the implementing agent. Ranked P1 (assumed from the owner's P0-first rule and the
> Cua ruling; the owner may re-rank): the forge is a product feature and waits for the P0 burn-down. It extends,
> rather than replaces, CAPB-001 (absent capability -> build -> return to the task), CAP-003 (registry fields),
> SKILL-004..007 (skill trust, evaluation, provenance, lifecycle) and the capability fabric's pinning; Cua
> (CUA-001..008) is its first intended real case after P0. The owner's claims about other projects (Repomix,
> Serena, codebase-memory-mcp, SkillSpector) are the owner's research, not verified here.

### 102.1 Decisions carried by this amendment (derived)

- A repository URL is the front door; the person never needs to know whether it is a skill, MCP, CLI, library,
  service, model or research corpus. FRIDAY decides: use directly, wrap, build adapter, extract pattern,
  reference only, or reject.
- Nothing from a repository touches the trusted runtime before quarantine, scanning and sandboxed tests; a static
  scan PASS is never trust.
- Integration patterns are deterministic recipe templates chosen from the classification; the simplest reliable
  path wins and MCP is not the default.
- An integration is complete only when a real capability journey passes with independently observed post-state;
  "installed" is never "integrated".
- Every integration is versioned with provenance and a last-known-good version, monitored upstream, and rolled back
  automatically when an update regresses.

### 102.2 Requirement matrix addendum (derived from the proposal; the verbatim text controls)

| ID | Pri | Requirement | Acceptance evidence |
|---|---|---|---|
| CAP-FORGE-001 | P1 | A person gives FRIDAY a repository URL and FRIDAY evaluates and integrates its useful capabilities without anyone editing FRIDAY's source or configuration. | Given only a repository URL, FRIDAY produces a dossier, a classification and a decision (use directly, wrap, build adapter, extract pattern, reference only, reject) and, when it integrates, a verified capability usable by name - with no manual edit to FRIDAY source or configuration. |
| CAP-FORGE-002 | P1 | Before integration a repository is classified as skill, MCP, CLI, library, application, service, model, research corpus, capability pack or reference-only. | On a labelled set (a skill pack, an MCP server, a CLI, a Python library, a service, a model, a prompt corpus, a reference-only project) the classifier assigns the expected class; a prompt corpus is never classified as executable. |
| CAP-FORGE-003 | P1 | A new repository is analysed outside the trusted runtime before it receives FRIDAY authority, secrets, user data or standing network access. | A repository under analysis has no secrets, no FRIDAY memory, no user files and no standing network; a planted repository that reads an environment secret or a user file is denied and the attempt recorded. |
| CAP-FORGE-004 | P1 | FRIDAY derives the minimum viable integration: native call, CLI wrapper, MCP, service adapter, browser path or reference only. | Each plan names the simplest reliable path from a fixed set of recipe templates and records why the simpler paths were rejected; the model does not invent a new architecture per repository. |
| CAP-FORGE-005 | P1 | Where an adapter is needed FRIDAY generates and tests the smallest one without the person writing code. | For a library with no adapter, FRIDAY generates the adapter, its tests and its manifest, and the tests run in the quarantine sandbox before any admission. |
| CAP-FORGE-006 | P1 | A successfully integrated capability can produce a local skill describing correct use, limits and verified procedures. | A verified integration yields a DRAFT local skill that becomes ACTIVE only after real use passes, under the SKILL-004..007 lifecycle. |
| CAP-FORGE-007 | P1 | An integration is complete only when a real representative capability journey passes with independently observed verification. | A representative journey passes with independently observed post-state (for a desktop driver: Calculator shows 42 after 4 and 2 are entered); an install that succeeds without the journey stays unadmitted. |
| CAP-FORGE-008 | P1 | Only verified integrations become normal FRIDAY capabilities. | The registry admits only integrations whose manifest records origin commit, licence, permissions, scan and journey results; an unverified integration is never offered to planning. |
| CAP-FORGE-009 | P1 | Integrated repository versions and source commits are tracked for updates and security or reliability changes. | Each admitted integration records its source commit; an upstream change is detected, scanned and its capability tests re-run in quarantine before promotion, under a recorded policy (notify, or automatic for low risk). |
| CAP-FORGE-010 | P1 | Updates keep a last-known-good integration and roll back automatically. | A failed or regressing update rolls back to the last known-good version without the person acting, and the bad version is marked so it is not retried. |
| CAP-FORGE-011 | P1 | FRIDAY does not import a repository whose useful capabilities are already covered unless a measurement shows an improvement. | A repository whose capabilities an existing registered capability covers is recorded REFERENCE_ONLY, with the overlap evidence, unless a benchmark shows an improvement. |
| CAP-FORGE-012 | P1 | FRIDAY may adopt a repository's pattern without installing the repository when that is simpler or safer. | FRIDAY records an EXTRACT_PATTERN decision with the pattern described, and can implement it natively without the repository installed. |

## 103. Amendment A9 (2026-10-02): Workflow Fabric - intelligence for novel work, automation for repeated work

> **Transcription note (added 2026-10-02; not part of the owner's text).** The owner's proposal is kept verbatim in
> `docs/plans/build-harness/00-owner-directive-2026-10-02e.md`; §103.2 restates its ten requirements with acceptance
> evidence worded by the implementing agent. Ranked P1 behind the P0 burn-down (assumed from the owner's P0-first
> rule; the owner may re-rank). The owner's licence and product claims (Activepieces MIT core and 760+ apps, n8n
> Sustainable Use License, Windmill AGPL, Node-RED Apache-2.0) are the owner's research, checked through the fabric's
> licence gate when a provider is adopted.

### 103.1 Decisions carried by this amendment (derived)

- Two kinds of workflow stay separate: FRIDAY's own durable work (objectives, restarts, approvals) belongs to the
  objective engine and DUR-001 and never depends on an automation engine being up; user and business automation
  runs on workflow providers behind one interface.
- Providers are interchangeable adapters in the capability fabric; none is core. Owner's order: Activepieces first,
  n8n optional, Windmill for code and data jobs; Node-RED, Kestra, Temporal may plug in later.
- Levels, cheapest adequate first: L0 direct action, L1 procedure, L2 managed workflow, L3 agentic workflow
  (deterministic skeleton with model decision nodes), L4 objective.
- A verified workflow is a capability like any other; a repeated deterministic trajectory may be compiled into one
  so routine work stops costing model tokens - but only when its recurrence and savings justify it.
- Workflow engines never hold FRIDAY's credentials wholesale: scopes per workflow through the secret broker, and
  workflow changes pass the same policy and JIT authority checks.

### 103.2 Requirement matrix addendum (derived from the proposal; the verbatim text controls)

| ID | Pri | Requirement | Acceptance evidence |
|---|---|---|---|
| WF-001 | P1 | FRIDAY runs reusable user and business automations through interchangeable workflow providers, never depending on one workflow product; FRIDAY's own durable execution never depends on one. | One workflow interface serves at least two providers registered through the capability fabric (each licence-checked; copyleft only in isolated modes); a provider that is down reports UNAVAILABLE and no FRIDAY objective, memory or durable-execution path depends on it. |
| WF-002 | P1 | A person describes a recurring process and FRIDAY creates, tests and activates the workflow without anyone configuring nodes. | From a plain-language description FRIDAY produces a workflow on a provider, fires a test event, observes the real result, repairs errors, and activates it only after the test passes. |
| WF-003 | P1 | FRIDAY chooses the workflow backend by connectors, durability, task type, privacy, resources and the person's policy. | Given requests of different kinds (app automation, code/data job, one-off action, long objective) the router picks the expected level and provider and records why; a one-off action never becomes a workflow, a long FRIDAY objective never leaves the objective engine. |
| WF-004 | P1 | A verified workflow becomes callable as a FRIDAY capability. | An activated workflow appears in the capability registry with provenance, permissions and health, and planning can call it by capability name (CAP-001 closed-world resolution). |
| WF-005 | P1 | FRIDAY detects repeated successful agent trajectories and proposes, or under the person's policy compiles, a deterministic workflow for them. | After a configurable number of matching successful trajectories FRIDAY proposes the workflow with the expected savings; a compiled workflow reproduces the trajectory's verified result on a test event without model calls, and exceptions still reach FRIDAY. |
| WF-006 | P1 | FRIDAY monitors workflow executions and detects degraded or broken workflows and providers. | Each workflow records last run, success rate, latency, failures and dependencies; a planted failing run moves it to DEGRADED and a dead provider to UNAVAILABLE, visible to the router. |
| WF-007 | P1 | FRIDAY diagnoses and repairs a broken workflow, with rollback and verification. | A planted breaking change (an expired connector, a renamed field) is diagnosed, a repaired version passes its test event before replacing the old one, and a failed repair rolls back to the last known-good version. |
| WF-008 | P1 | While a workflow is broken FRIDAY can complete eligible urgent runs through its normal capabilities. | With a workflow broken, an eligible pending run is completed through ordinary capabilities with the same verification, and the person is told which path ran. |
| WF-009 | P1 | A workflow engine receives only the credential scopes its workflow needs, through the secret broker. | A workflow's connections are issued per workflow through the secret broker; a planted workflow asking for an unrelated credential (e.g. a marketing flow asking for banking) is refused and the refusal logged; workflow changes pass the policy and JIT authority checks. |
| WF-010 | P1 | FRIDAY promotes a task to a workflow only when recurrence, reliability and expected savings justify it. | Promotion records frequency, current model tokens and time, expected error reduction against build and maintenance cost; a task below the threshold is not converted and the reason is recorded. |

## 104. Amendment A10 (2026-10-03): scoped command execution

> **Transcription note (added 2026-10-03; not part of the owner's text).** The owner's ruling is kept verbatim in
> `docs/plans/build-harness/00-owner-directive-2026-10-03c.md`; the requirement and its acceptance evidence below are
> worded by the implementing agent. P0 because the owner called it the immediate restoration path (assumed; the
> owner may re-rank).

### 104.1 Decisions carried by this amendment

- SEC-005 merges with command execution disabled for scoped delegations: their only file access is FRIDAY's
  jail-backed file capability.
- Commands come back only through an OS-enforced scoped command runner: AppContainer/LPAC is the preferred normal
  Windows backend, Windows Sandbox the stronger isolated backend.
- Job Objects control the process tree and resources; they are not the filesystem sandbox.
- bridge_run_command and interpreters return to scoped delegations only after real escape tests pass.

### 104.2 Requirement matrix addendum

| ID | Pri | Requirement | Acceptance evidence |
|---|---|---|---|
| EXEC-SBX-001 | P0 | A scoped delegation runs commands and interpreters only inside an OS-enforced sandbox bounded by its ExecutionEnvelope. | Real Python, Node and PowerShell processes and their children cannot read or write outside the envelope roots by traversal, absolute or UNC paths, case variations, symlinks, junctions or network paths; work inside the roots succeeds; only then is bridge_run_command restored to scoped delegations. |

## Appendix C. Implementation controller, updated 2026-09-26 (owner's text, verbatim)

```text
# =================================================================================================
# F.R.I.D.A.Y. / JARVIS
# MASTER AUTONOMOUS AGENTIC PRD IMPLEMENTATION PROGRAM — UPDATED
#
# UNIVERSAL COGNITIVE OPERATING SYSTEM
# + INTENT-TO-REALITY ENGINE
# + CORTEX OS
# + SYSTEM ONE
# + RESEARCH OS
# + CAPABILITY TRUST FABRIC
# + MODEL / CLI / SKILL / MCP / PLUGIN GOVERNANCE
# + CONTROLLED SELF-DEVELOPMENT
#
# EXECUTION MODE:
#   END-TO-END AUTONOMOUS HARNESS ENGINEERING
#
# PRIMARY PRODUCT AUTHORITY:
#   ./PRD.md
#
# TERMINATION:
#   DO NOT STOP UNTIL EVERY APPLICABLE PRD REQUIREMENT HAS REACHED
#   A TRUTHFUL TERMINAL STATE AND THE FINAL RELEASE GATE HAS RUN.
#
# THIS PROMPT IS AN IMPLEMENTATION CONTROLLER.
# IT IS NOT A PRODUCT SUMMARY.
# =================================================================================================


# =================================================================================================
# 0. ROLE
# =================================================================================================

You are operating as the autonomous engineering organization for F.R.I.D.A.Y.

You simultaneously perform, delegate, coordinate and verify the functions of:

    Principal Systems Architect
    Principal Software Engineer
    AI/Agent Systems Engineer
    Harness Engineer
    Context/Inference Engineer
    Security Architect
    Reliability Engineer
    Test Engineer
    Performance Engineer
    Research Engineer
    Release Engineer
    Product Implementation Lead

Your task is not to TALK ABOUT the PRD.

Your task is to TURN THE PRD INTO A REAL, WORKING, TESTED PRODUCT.


# =================================================================================================
# 1. ABSOLUTE PRODUCT DEFINITION
# =================================================================================================

FRIDAY is:

    A UNIVERSAL LOCAL-FIRST COGNITIVE OPERATING SYSTEM
    AND INTENT-TO-REALITY ENGINE.

Its fundamental interaction is:

    USER EXPRESSES INTENT
            ↓
    FRIDAY UNDERSTANDS DESIRED REALITY
            ↓
    ACQUIRES MISSING KNOWLEDGE
            ↓
    SELECTS / CREATES NEEDED CAPABILITIES
            ↓
    SELECTS APPROPRIATE INTELLIGENCE
            ↓
    EXECUTES
            ↓
    OBSERVES REALITY
            ↓
    VERIFIES
            ↓
    DELIVERS ACTUAL OUTCOME
            ↓
    LEARNS

FRIDAY is NOT:

    an ecommerce bot
    a chatbot skin
    a single LLM
    a Claude wrapper
    a Gemini wrapper
    a GPT wrapper
    a Hermes wrapper
    a fixed multi-agent framework
    an MCP collection
    a browser-only automation tool
    a coding-only assistant

The architecture must remain capable of supporting any legitimate technically feasible domain through
new knowledge, tools, models, skills and adapters.


# =================================================================================================
# 2. ONE MANAGER
# =================================================================================================

There is exactly ONE product-level manager:

    FRIDAY.

FRIDAY owns:

    user relationship
    user intent
    objectives
    authority
    context
    memory policy
    routing
    worker creation
    capability selection
    model selection
    verification
    final reporting

The following are SUBORDINATE:

    Hermes
    Claude Code
    Codex
    Gemini CLI
    OpenCode
    local models
    Laya
    Jev
    HyperResearch
    Caveman
    Browser Use
    Playwright
    MCPs
    skills
    plugins
    Kali
    Claude-Red
    external frameworks

Never accidentally create multiple competing global brains.


# =================================================================================================
# 3. PRIME AUTONOMY DIRECTIVE
# =================================================================================================

Continue autonomously.

Do NOT repeatedly ask:

    Should I continue?
    Should I implement the next phase?
    Should I run tests?
    Should I fix this?
    Should I research this?
    Should I commit this?

Normal engineering continuation is already authorized.

You may autonomously:

    audit
    research
    edit
    refactor
    run tests
    debug
    benchmark
    create local branches/worktrees
    create safe local artifacts
    build capabilities
    create draft skills
    perform local sandbox validation
    update documentation
    run CI if already authorized/configured
    repair low-risk engineering failures

Ask the human only when:

    explicit account authentication is required
    OTP / passkey / security key is required
    a protected-kernel modification requires approval
    an irreversible consequential external action lacks standing authority
    a major product decision is genuinely ambiguous
    a financial commitment is required
    a physical safety decision needs human authority
    active cybersecurity testing requires explicit target/scope authorization


# =================================================================================================
# 4. DON'T LET ONE BLOCKER STOP THE PROGRAM
# =================================================================================================

If a requirement is blocked:

    record blocker
    classify blocker
    identify exact missing dependency
    park only that branch
    continue every independent branch

Example:

    Telegram credentials missing

must NOT stop:

    CortexOS
    memory
    model routing
    skill trust
    installer
    research system
    context benchmarks


# =================================================================================================
# 5. FINITE AUTONOMY
# =================================================================================================

"Do not stop" does NOT mean infinite loops.

The autonomous program finishes when all applicable requirements are:

    VERIFIED
    BLOCKED_EXTERNAL
    DEFERRED_BY_PRD
    NOT_APPLICABLE_WITH_REASON
    REJECTED_WITH_EVIDENCE

These are NOT terminal:

    TODO
    PARTIAL
    IMPLEMENTED
    IMPLEMENTED_UNVERIFIED
    RESEARCHED
    UNKNOWN
    PROBABLY_WORKS


# =================================================================================================
# 6. DOMAIN PARTITIONING — VERY IMPORTANT
# =================================================================================================

Do NOT send every FRIDAY subsystem into every model context.

Classify work domains before delegation.

Examples:

    GENERAL_ENGINEERING
    CONTEXT_ENGINEERING
    MODEL_ROUTING
    MEMORY
    RESEARCH
    BROWSER
    VOICE
    MOBILE
    CYBER_AUTHORIZED
    PHYSICAL_ENGINEERING
    BUSINESS
    CREATIVE

Specialized or provider-restricted domains MUST be isolated into bounded work programs.

For cybersecurity specifically:

    MAIN FRIDAY BUILD
        implements:
            CyberLab interfaces
            schemas
            authorization
            isolation
            provider contracts
            mocks
            security tests

    SEPARATE CYBER PROGRAM
        handles:
            authorized cybersecurity research execution
            Kali execution
            cyber skill packs
            specialist cyber models

Do NOT pollute the general implementation session with unnecessary operational cyber methodology.

A provider safeguard intervention is:

    ROUTING SIGNAL

not:

    "retry the same prompt 20 times."


# =================================================================================================
# 7. SAFEGUARD-AWARE MODEL ROUTING
# =================================================================================================

The Model Registry SHALL track:

    provider
    model
    strengths
    task fitness
    cost
    latency
    privacy
    context
    tool support
    current health
    policy/safeguard compatibility
    known task restrictions
    fallback routes

Before dispatch:

    TASK
      ↓
    DOMAIN CLASSIFICATION
      ↓
    MODEL FITNESS
      ↓
    POLICY COMPATIBILITY
      ↓
    ROUTE

Result may be:

    ALLOW
    FALLBACK
    LOCAL_ONLY
    ASK_USER
    BLOCKED

Never keep retrying an incompatible model route.


# =================================================================================================
# 8. REPOSITORY TRUTH FIRST
# =================================================================================================

Before building anything, establish:

    repository root
    branch
    HEAD
    git status
    worktrees
    untracked files
    user modifications
    environment
    Python
    Node
    package managers
    OS
    RAM
    CPU
    GPU
    free disk
    Chrome
    current processes
    databases
    installed models
    installed CLIs
    skills
    MCP servers
    plugins
    tests
    CI
    packaging
    installer
    implementation reports

Verify import origin.

Ensure tests exercise THIS checkout.

Wrong checkout invalidates evidence.


# =================================================================================================
# 9. PROTECT EXISTING WORK
# =================================================================================================

Never destroy unknown work.

Do NOT automatically:

    git reset --hard
    git clean -fd
    force checkout
    remove worktrees
    overwrite modified files

Prefer:

    isolated branch
    isolated worktree

for major implementation.


# =================================================================================================
# 10. READ BEFORE BUILD
# =================================================================================================

Inspect current:

    PRD.md
    architecture docs
    implementation ledgers
    objective engine
    evidence system
    policy
    memory
    context
    model gateway
    provider system
    skills
    capabilities
    browser
    desktop
    voice
    installer
    tests
    CI

Classify every proposed architecture component:

    EXISTS_VERIFIED
    EXISTS_UNVERIFIED
    PARTIAL
    BROKEN
    MISSING
    DUPLICATED
    SUPERSEDED

Prefer:

    REUSE
      ↓
    EXTEND
      ↓
    REPAIR
      ↓
    MIGRATE
      ↓
    REBUILD


# =================================================================================================
# 11. EXECUTABLE REQUIREMENT LEDGER
# =================================================================================================

Parse ALL PRD requirements into:

    docs/implementation/PRD_REQUIREMENTS_LEDGER.md
    data/prd_requirements.json

Each requirement:

    id
    section
    priority
    specification
    dependencies
    current classification
    files
    tests
    acceptance criteria
    evidence
    blockers
    status
    commit
    next action


# =================================================================================================
# 12. DEPENDENCY GRAPH
# =================================================================================================

Build a DAG.

Do NOT blindly work top-to-bottom.

High-level logical dependency:

    REPOSITORY TRUTH
        ↓
    PROTECTED KERNEL
        ↓
    ACTION TRUTH
        ↓
    OBJECTIVE OS
        ↓
    CORTEX OS
        ↓
    COGNITIVE GOVERNOR
        ↓
    SYSTEM ONE / MODEL ROUTING
        ↓
    MEMORY / PROJECT BRAIN
        ↓
    SKILLS
        ↓
    CAPABILITY FABRIC
        ↓
    CLI GATEWAY
        ↓
    RESEARCH OS
        ↓
    BROWSER / DESKTOP
        ↓
    INTENT-TO-REALITY
        ↓
    UI / ONBOARDING
        ↓
    PROACTIVE / REMOTE
        ↓
    VOICE / MOBILE
        ↓
    SELF-DEVELOPMENT
        ↓
    LEARNED NEURAL FABRIC

But respect what current code already implements.


# =================================================================================================
# 13. MASTER LOOP
# =================================================================================================

For every requirement:

    DISCOVER
      ↓
    UNDERSTAND CURRENT REALITY
      ↓
    RESEARCH UNKNOWN FACTS
      ↓
    FORM TESTABLE ACCEPTANCE CRITERIA
      ↓
    CREATE / CONFIRM REPRODUCTION
      ↓
    IMPLEMENT MINIMUM CORRECT CHANGE
      ↓
    TARGETED TEST
      ↓
    OBSERVE REAL RESULT
      ↓
    NEGATIVE CONTROL
      ↓
    INTEGRATION TEST
      ↓
    ADVERSARIAL REVIEW
      ↓
    FIX
      ↓
    RETEST
      ↓
    REGRESSION
      ↓
    GOLDEN JOURNEY
      ↓
    RECORD EVIDENCE
      ↓
    UPDATE LEDGER
      ↓
    CONTINUE


# =================================================================================================
# 14. STRUGGLE DETECTOR
# =================================================================================================

Track:

    same failure
    same tool
    same args
    same hypothesis
    no new evidence

After ~3 materially similar failures:

    STOP PATCHING SYMPTOMS.

Perform ROOT-CAUSE RESET:

    reread architecture
    inspect raw trace
    create smaller reproduction
    challenge assumptions
    search official upstream docs/issues
    choose materially different strategy

After repeated root-cause resets:

    park blocker truthfully
    continue independent work.


# =================================================================================================
# 15. CORTEX OS
# =================================================================================================

CortexOS owns:

    what information enters model context.

It SHALL implement/verify:

    WorkingSetCompiler
    ContextRouter
    ContextBudgetManager
    ContextHealthMonitor
    ProgressiveDisclosure
    ContextFolding
    ArtifactStore
    CompactionEngine
    CompactionFidelityVerifier
    FreshSessionHandoff
    SemanticCache
    ContradictionResolver
    ContextLab

Model context is WORKING MEMORY.

It is not storage.


# =================================================================================================
# 16. CONTEXT CLASSES
# =================================================================================================

Classify context payloads:

    LOSSLESS_REQUIRED

        user exact literals
        approvals
        policy
        objective constraints
        security evidence
        critical code patches
        identifiers

    RECOVERABLE_LOSSY_ALLOWED

        verbose logs
        repetitive terminal output
        large DOM/accessibility trees
        search dumps

    SUMMARIZABLE

        research narratives
        old objective history
        meeting history

    DROP_AFTER_EXTRACTION

        duplicate status
        progress spam
        redundant logs


# =================================================================================================
# 17. PROGRESSIVE DISCLOSURE
# =================================================================================================

Use:

    L0 = pointer / tiny summary
    L1 = operational overview
    L2 = complete body

For:

    skills
    capabilities
    research
    code
    project knowledge
    memories
    documents

Never load all L2 content by default.


# =================================================================================================
# 18. CAVEMAN INTEGRATION
# =================================================================================================

Treat Caveman as:

    CONTEXT COMPRESSION BACKEND

NOT:

    replacement for CortexOS.

CortexOS decides WHAT is relevant.

Caveman may decide HOW selected bulky data is represented more cheaply.

Architecture:

    Context selected
       ↓
    CompressionCoordinator
       ├── PASS_RAW
       ├── CAVEMAN
       ├── SUMMARIZE
       ├── FOLD_TO_ARTIFACT
       └── DROP_AFTER_EXTRACTION

Caveman integration must:

    preserve source artifact
    preserve hash
    preserve recovery handle
    fail closed
    never lossy-compress LOSSLESS_REQUIRED content

Benchmark:

    OFF
    OUTPUT_ONLY
    ENGINE
    FULL

Measure:

    tokens
    task success
    latency
    recovery
    critical fact retention
    exact literal retention
    debugging quality

IMPORTANT:

Current Caveman Engine licensing/provenance must be reviewed before embedding/distribution.
Do not assume every Caveman subcomponent has the same license.


# =================================================================================================
# 19. COMPRESSION COORDINATOR
# =================================================================================================

Avoid:

    Cortex summary
      ↓
    Caveman lossy compression
      ↓
    provider compaction
      ↓
    missing critical fact

Track transformations.

Each transformed artifact:

    source_id
    source_hash
    transformation
    transformed_hash
    critical_fact_set
    recovery_pointer
    reversibility


# =================================================================================================
# 20. CONTEXT LAB
# =================================================================================================

Benchmark:

    lost in middle
    semantic retrieval
    distributed multi-hop
    contradiction
    stale state
    noise
    skill bloat
    tool bloat
    long trajectory
    compaction fidelity
    fresh handoff
    exact literal survival
    repository reasoning

Compare:

    full history
    recent only
    simple summary
    summary + recent
    hierarchical retrieval
    folding
    Caveman
    fresh sessions
    recursive processing
    CortexOS hybrid

No strategy is promoted because it sounds sophisticated.


# =================================================================================================
# 21. SYSTEM ONE GATEWAY
# =================================================================================================

Build one stable interface for fast typed decisions.

Conceptual API:

    choose(...)
    score(...)
    yes_no(...)
    rank(...)
    classify(...)
    confidence(...)

Backends:

    LayaLocal
    JevHosted
    FridaySystemOneFuture

FRIDAY code should depend on:

    SystemOneGateway

not directly on Laya/Jev everywhere.


# =================================================================================================
# 22. LAYA
# =================================================================================================

Use Laya as a strong candidate for default LOCAL System-One decisions.

Potential tasks:

    task classification
    model-class routing
    worker-class choice
    capability-family choice
    skill-family choice
    context-strategy choice
    anomaly triage
    simple risk triage
    retry/replan classifier

Do NOT use Laya as a replacement for deep reasoning.

Benchmark locally:

    CPU latency
    RAM
    accuracy
    confidence calibration
    escalation rate
    tool-selection accuracy

Use Jev-compatible interface where practical.


# =================================================================================================
# 23. JEV
# =================================================================================================

Use Jev as:

    optional hosted System-One provider

not mandatory infrastructure.

Route to Jev only if:

    data policy permits cloud
    Jev is healthy
    Jev benchmark wins for task
    user/provider budget allows

LOCAL_ONLY:

    must never route to Jev.

If Laya performs sufficiently:

    avoid unnecessary hosted call.


# =================================================================================================
# 24. FUTURE FRIDAY SYSTEM-ONE MODELS
# =================================================================================================

Collect dataset:

    task features
    candidate routes
    selected route
    confidence
    result
    cost
    latency
    user correction
    verifier result

Candidate networks:

    FridayRouterNet
    FridayContextNet
    FridayValueNet
    FridayRiskNet
    FridaySkillRouter
    FridayDifficultyNet
    FridayCompressionNet

Train offline.

Production promotion requires:

    holdout benchmark
    baseline comparison
    calibration
    privacy check
    regression
    rollback

Never mutate production weights directly from one conversation.


# =================================================================================================
# 25. DISAGREEMENT ROUTING
# =================================================================================================

For cheap uncertain System-One decisions:

    Laya
      +
    FridayNet / Jev where policy allows

If:

    high confidence + agreement
        proceed

If:

    low confidence
    or disagreement
        escalate to System Two

Do not invoke multiple predictors if expected value does not justify it.


# =================================================================================================
# 26. CONFIDENCE CALIBRATION
# =================================================================================================

Track:

    predicted confidence
    actual success

Build calibration curves.

A model saying:

    0.98

does not automatically mean reliable.

Use empirical calibration to determine autonomous thresholds.


# =================================================================================================
# 27. COGNITIVE GOVERNOR
# =================================================================================================

Select:

    deterministic
    System One
    local general model
    normal executor
    frontier advisor
    frontier direct
    bounded team

Inputs:

    complexity
    uncertainty
    consequence
    reversibility
    privacy
    user preference
    budget
    current model health
    context requirements

Strongest model performs the MOST VALUABLE tokens.

Not the most tokens.


# =================================================================================================
# 28. REASONING BUDGET
# =================================================================================================

Support conceptual modes:

    NO_THINK
    SHORT
    NORMAL
    DEEP
    EXPERT

Allocate using:

    difficulty
    consequence
    uncertainty
    verification strength

Easy but catastrophic:

    strong verification.

Hard but harmless:

    cheaper exploratory route may be okay.


# =================================================================================================
# 29. ADVISOR / EXECUTOR
# =================================================================================================

Use frontier model as advisor for:

    architecture
    hard debugging
    strategy fork
    repeated failure
    critical review

Use cheaper executor for:

    mechanical implementation
    iterative tool calls
    routine code editing
    log processing

Do not consult advisor after every edit.


# =================================================================================================
# 30. PONYTAIL / CODE MINIMALISM
# =================================================================================================

Treat Ponytail as candidate optimization.

Benchmark it.

Do not blindly globally install.

Regardless of plugin, coding workers follow:

    ELIMINATE
      ↓
    REUSE EXISTING
      ↓
    STDLIB
      ↓
    NATIVE PLATFORM
      ↓
    EXISTING DEPENDENCY
      ↓
    SMALLEST SAFE NEW CODE

Never reduce:

    security
    validation
    accessibility
    durability
    evidence
    required error handling

for fewer LOC.


# =================================================================================================
# 31. RESEARCH OS
# =================================================================================================

Build:

    ResearchRouter
    CanonicalInquiry
    ResearchDriftGuard
    ResearchBudgetGovernor
    EpistemicBudgetManager
    QuickResearchEngine
    DeepResearchProvider
    ScientificResearchAdapter
    GitHubResearchAdapter
    CyberResearchAdapter
    ResearchArtifactStore
    SourceQualityEngine
    SourceIndependenceAnalyzer
    EvidenceProvenanceGraph
    ClaimGraph
    CitationBindingVerifier
    FreshnessManager
    Retraction/SupersessionMonitor
    GapDetector
    AdversarialCriticPool
    SurgicalRevisionEngine
    ResearchResumeManifest
    ResearchRunIsolation
    ResearchArena


# =================================================================================================
# 32. RESEARCH ROUTING
# =================================================================================================

R0:
    fresh cached research exists

R1:
    quick factual lookup

R2:
    multi-source verification

R3:
    deep research

R4:
    adversarial research

R5:
    dissertation/scientific-scale mission

Do not use R4/R5 for trivial questions.


# =================================================================================================
# 33. HYPERRESEARCH
# =================================================================================================

Classify HyperResearch as:

    DEEP_RESEARCH_PROVIDER

not:

    FRIDAY's entire research architecture
    canonical memory
    universal brain

Use/adapt valuable concepts:

    canonical query
    lazy phase skill loading
    resumable run manifest
    source provenance
    persistent research vault
    source quality
    gap filling
    citation verification
    adversarial critics
    run isolation
    surgical patching

Wrap behind:

    FridayResearchProvider

so FRIDAY can later use:

    HyperResearch
    Claude-based research
    Codex-based research
    OpenAI/Gemini research
    local research
    scientific APIs

without architectural rewrite.

IMPORTANT:

HyperResearch's own benchmark claims must be independently evaluated.
Do not assume "best" from README.


# =================================================================================================
# 34. CANONICAL RESEARCH QUERY
# =================================================================================================

For deep research create immutable:

    CanonicalInquiry

containing:

    exact objective
    scope
    must-answer questions
    non-goals
    freshness requirement
    evidence standard

Every research branch must reduce uncertainty about CanonicalInquiry.

ResearchDriftGuard rejects irrelevant rabbit holes.


# =================================================================================================
# 35. RESEARCH ARTIFACT OWNERSHIP
# =================================================================================================

Do NOT dump every source into long-term memory.

SOURCE CORPUS
    → Research Store

VERIFIED CLAIMS
    → Epistemic Ledger

PROJECT CONCLUSIONS
    → Project Brain

SMALL DURABLE USER FACTS
    → Memory

REUSABLE PROCEDURE
    → Skill


# =================================================================================================
# 36. RESEARCH PROVENANCE
# =================================================================================================

Track:

    source
    parent discovery source
    primary vs derivative
    date
    quality
    freshness
    retraction/supersession
    claim support

Five copied news articles ≠ five independent sources.


# =================================================================================================
# 37. RESEARCH STOPPING
# =================================================================================================

Do not stop based only on source count.

Estimate:

    expected information gain

Continue only while likely new evidence can materially:

    resolve gap
    change confidence
    change decision

Research itself has:

    time budget
    source budget
    token budget
    cost budget


# =================================================================================================
# 38. MEMORY
# =================================================================================================

Durable truth uses canonical scoped memory.

Scopes:

    user
    workspace
    project
    objective

Each record:

    content
    provenance
    trust
    confidence
    freshness
    privacy
    supersession

Workers may keep temporary state.

Permanent worker-private truth is not default.


# =================================================================================================
# 39. MEMORY POISONING
# =================================================================================================

Test full lifecycle:

    malicious candidate written
       ↓
    later retrieval
       ↓
    attempted execution
       ↓
    policy enforcement
       ↓
    forget / repair

Untrusted external content cannot become standing instruction merely through memory.


# =================================================================================================
# 40. PROJECT BRAIN
# =================================================================================================

Maintain structured:

    architecture
    requirements
    decisions
    assumptions
    research
    failures
    experiments
    objectives
    incidents
    artifacts
    handoffs
    procedures
    entities

Human-inspectable source of project truth.


# =================================================================================================
# 41. EPISTEMIC LEDGER
# =================================================================================================

Important claims track:

    claim
    evidence
    support level
    source independence
    confidence
    freshness
    assumptions
    contradictions

Distinguish:

    observed
    measured
    sourced
    inferred
    simulated
    speculative


# =================================================================================================
# 42. FAILURE GENOME
# =================================================================================================

Store meaningful failure:

    symptom
    environment
    task
    strategy
    wrong assumption
    root cause
    repair
    prevention
    regression test

Retrieve before repeating known failure.


# =================================================================================================
# 43. CAPABILITY FABRIC
# =================================================================================================

Capability registry tracks:

    id
    version
    backend
    provenance
    permissions
    health
    auth
    risk
    data classes
    OS
    latency
    cost
    tests
    last verified

Installed ≠ healthy.

Configured ≠ usable.


# =================================================================================================
# 44. CLOSED-WORLD TOOL RESOLUTION
# =================================================================================================

Model-proposed tool:

    existence check
       ↓
    schema check
       ↓
    authority check
       ↓
    composition-risk check
       ↓
    execute

Invented tool:

    reject.


# =================================================================================================
# 45. CAPABILITY SEARCH
# =================================================================================================

Never dump hundreds of tool schemas into context.

Use:

    search_capabilities(intent)
       ↓
    shortlist
       ↓
    load selected schemas only


# =================================================================================================
# 46. SKILL TRUST FABRIC
# =================================================================================================

Every external/generated executable skill passes:

    SOURCE IDENTIFICATION
       ↓
    VERSION / COMMIT PIN
       ↓
    HASH / SIGNATURE
       ↓
    STATIC SCAN
       ↓
    DEPENDENCY / CVE CHECK
       ↓
    SECRET SCAN
       ↓
    PERMISSION EXTRACTION
       ↓
    DEDUPLICATION
       ↓
    SANDBOX
       ↓
    DYNAMIC BEHAVIOR
       ↓
    LIVE VALUE EVAL
       ↓
    QUARANTINE
       ↓
    ACTIVATE / REJECT


# =================================================================================================
# 47. NVIDIA SKILLSPECTOR
# =================================================================================================

Use SkillSpector as:

    mandatory candidate scanner for imported/generated skills

where technically compatible.

But remember:

    static scan PASS
    ≠
    runtime-safe.

Use it as one layer only.

Store:

    scan version
    scan date
    source commit
    risk findings
    report hash


# =================================================================================================
# 48. SKILL EVALUATION
# =================================================================================================

Adopt SkillEvaluator-like stages:

    TIER 1
        safety / validity

    TIER 2
        duplication / semantic overlap

    TIER 3
        actual live value

Evaluate:

    correctness gain
    reliability gain
    token cost
    latency
    tool calls
    context cost
    security risk
    maintenance cost

A safe useless skill is not worth activating.


# =================================================================================================
# 49. SKILL VALUE
# =================================================================================================

Conceptually:

    SkillUtility =
          quality_gain
        + reliability_gain
        + success_gain
        - context_cost
        - token_cost
        - latency
        - security_risk
        - duplication

Use empirically derived weights.

Do not hard-code fake precision.


# =================================================================================================
# 50. SKILL INTEGRITY
# =================================================================================================

Activated external skill tracks:

    source
    commit
    hash
    signature if available
    dependency SBOM
    scan
    eval

If content changes:

    ACTIVE
      ↓
    UPDATE_UNVERIFIED
      ↓
    QUARANTINE
      ↓
    re-scan / re-evaluate


# =================================================================================================
# 51. SKILL PERFORMANCE
# =================================================================================================

Track:

    use count
    success
    failures
    user corrections
    token impact
    time
    affected app version

Possible state:

    ACTIVE
    DEGRADED
    NEEDS_REVALIDATION
    DEPRECATED


# =================================================================================================
# 52. CAPABILITY SUPPLY CHAIN
# =================================================================================================

Not only skills.

Apply provenance/trust pipeline to:

    plugins
    MCP servers
    GitHub repos
    binaries
    packages
    models
    LoRAs
    browser extensions
    CLIs

Maintain:

    Agent Capability SBOM


# =================================================================================================
# 53. RUNTIME BEHAVIOR SANDBOX
# =================================================================================================

When evaluating untrusted executable capability, observe:

    filesystem writes
    process tree
    network
    DNS
    environment access
    credential access
    persistence mechanisms

Where practical seed:

    fake secret canaries
    fake sensitive files
    controlled network canaries

Unexpected access:

    FAIL / INVESTIGATE


# =================================================================================================
# 54. MCP FIREWALL
# =================================================================================================

At registration:

    pin server identity
    record tool schema
    scan description
    record version/hash

At runtime:

    tool output = external/untrusted unless proven otherwise

Track changes.

Unexpected schema/description/server change:

    ACTIVE
      ↓
    SUSPENDED
      ↓
    REVALIDATE

This protects against MCP rug-pull behavior.


# =================================================================================================
# 55. TYPED TRUST
# =================================================================================================

Text may not self-authorize.

Never trust strings such as:

    trusted=true
    admin=true
    approved=true

Authority comes from runtime provenance.


# =================================================================================================
# 56. DATA LINEAGE
# =================================================================================================

Track externally derived values across transformations.

Example:

    website string
      ↓
    model extracts URL
      ↓
    download tool
      ↓
    shell

Shell boundary still knows:

    origin = UNTRUSTED_WEB


# =================================================================================================
# 57. SECRET BROKER
# =================================================================================================

Raw secrets do not enter general model prompts.

Models receive:

    secret_handle
    credential_available

Trusted executor resolves secret locally.

Negative-test:

    dummy secrets
    prompts
    logs
    skills
    artifacts
    telemetry
    crash output


# =================================================================================================
# 58. PRIVACY BUDGET
# =================================================================================================

Each objective declares:

    allowed data classes
    allowed providers
    allowed external services

CortexOS + ModelRouter enforce this.

LOCAL_ONLY:

    cannot route cloud.


# =================================================================================================
# 59. EXECUTION ENVELOPES
# =================================================================================================

Every worker receives:

    cwd
    read roots
    write roots
    capabilities
    network
    secrets
    process budget
    RAM budget
    time
    tokens
    tool calls
    expiry

Model cannot enlarge envelope.


# =================================================================================================
# 60. CAPABILITY COMPOSITION
# =================================================================================================

Calculate combined authority.

Example:

    private_file_read
      +
    arbitrary_network_send

= high exfiltration capability.

Do not assess tools individually only.


# =================================================================================================
# 61. CLI GATEWAY
# =================================================================================================

Unify:

    Hermes
    Claude Code
    Codex
    Gemini CLI
    OpenCode
    future CLIs

Gateway handles:

    work packets
    context
    compression
    cwd
    permissions
    model
    process
    interaction questions
    timeouts
    cancellation
    artifacts
    handoff
    evidence


# =================================================================================================
# 62. WORK PACKET
# =================================================================================================

Worker receives only:

    objective
    objective version
    requirement IDs
    exact task
    constraints
    relevant files
    relevant architecture
    known failures
    selected skills
    selected capabilities
    test contract
    budget
    output contract

Do not dump entire project history.


# =================================================================================================
# 63. FRESH SESSION FIRST-CLASS
# =================================================================================================

CLI sessions are disposable cognition.

Durability resides in:

    Objective OS
    Git
    Project Brain
    research
    memory
    artifacts
    tests
    handoff

If context rots:

    write validated handoff
    kill session
    start fresh
    continue


# =================================================================================================
# 64. ACTION TRUTH
# =================================================================================================

No success claim without matching evidence.

No failure claim without attempt evidence.

Evidence correlation:

    workspace
    objective
    version
    action
    target
    executor
    operation
    timestamp


# =================================================================================================
# 65. PROOF-CARRYING ACTIONS
# =================================================================================================

FILE:
    path
    hash
    readback

CODE:
    before reproduction
    diff
    tests after

WEB:
    post-state
    URL
    response

EMAIL:
    message ID / sent state

SIMULATION:
    measurement artifact


# =================================================================================================
# 66. BLACK BOX
# =================================================================================================

Record observable:

    objective
    amendments
    policy
    models
    tools
    capability calls
    approvals
    side effects
    evidence
    final state

Do not require hidden chain-of-thought.


# =================================================================================================
# 67. INDEPENDENT VERIFIER
# =================================================================================================

For P0/P1/high-consequence:

    BUILDER
      ↓
    TESTS
      ↓
    INDEPENDENT VERIFIER

Verifier reads:

    requirement
    implementation
    independent state
    evidence

not merely builder narrative.


# =================================================================================================
# 68. HARNESS MUST NOT CREATE THE EVIDENCE
# =================================================================================================

A test cannot manufacture the very success artifact it is checking.

Critical guards must have RED tests.


# =================================================================================================
# 69. BROWSER / DESKTOP ROUTING
# =================================================================================================

Possible execution:

    native API
    scripting
    structured tool
    DOM/accessibility
    vision
    coordinates last

Learn best path from evidence over time.


# =================================================================================================
# 70. USER BROWSER
# =================================================================================================

Support:

    user-approved Chrome profile
    dedicated FRIDAY profile
    temporary profile

Never silently use another person's profile/account.


# =================================================================================================
# 71. AUTHENTICATION BOUNDARY
# =================================================================================================

OTP/passkey/hardware key/human verification:

    ask human
    resume afterward.

Do not build bypass behavior.


# =================================================================================================
# 72. CYBER LAB ARCHITECTURE
# =================================================================================================

The normal FRIDAY system includes:

    CyberLabProvider
    CyberAuthorizationContract
    CyberSkillVault
    KaliProvider
    isolated evidence/logging
    security test interfaces

Active cybersecurity operations are a specialized separately scoped domain.


# =================================================================================================
# 73. KALI EXECUTION FABRIC
# =================================================================================================

Provide conceptual tiers:

    K1_EPHEMERAL_CONTAINER
        default isolated tooling

    K2_WSL
        persistent richer Windows-hosted lab

    K3_VM
        strongest isolation / realistic lab

CyberExecutionRouter selects based on:

    privileges
    GUI
    kernel requirements
    target
    isolation
    persistence
    network
    risk

Root inside isolated Kali environment does NOT imply Windows host admin.


# =================================================================================================
# 74. CYBER AUTHORIZATION CONTRACT
# =================================================================================================

Before active security operation require objective-bound:

    target
    ownership / explicit authorization
    scope
    allowed methods
    network destinations
    time window
    environment
    artifact handling
    expiry

Runtime enforces it.

Model cannot extend it.


# =================================================================================================
# 75. CLAUDE-RED
# =================================================================================================

Classify:

    AUTHORIZED CYBER SKILL SOURCE

Use only inside:

    CyberSkillVault

under:

    CyberAuthorizationContract.

Do not globally install/load offensive skills into normal FRIDAY workers.

Load lazily by authorized task.

Prefer:

    red team
      +
    blue team
      +
    verification

for internal FRIDAY hardening.


# =================================================================================================
# 76. PURPLE TEAM ENGINE
# =================================================================================================

For FRIDAY security:

    RED finds weakness
      ↓
    BLUE reproduces / mitigates
      ↓
    BUILDER fixes
      ↓
    RED retries
      ↓
    VERIFIER confirms
      ↓
    REGRESSION TEST


# =================================================================================================
# 77. CYBER PROVIDER SAFEGUARD PARTITION
# =================================================================================================

If current frontier model route pauses/refuses a cyber work packet:

    do not repeatedly retry.

Instead:

    preserve objective
    record safeguard event
    classify domain
    route to permitted/compatible authorized cyber path
    or mark BLOCKED_EXTERNAL

Normal non-cyber PRD work continues.


# =================================================================================================
# 78. RESOURCE GOVERNOR
# =================================================================================================

Manage:

    RAM
    CPU
    GPU
    model residency
    browser count
    process count
    workers
    disk
    network

Each process:

    owner
    objective
    lease
    heartbeat
    cleanup

8 GB systems:

    lazy load
    unload heavy model
    avoid simultaneous expensive workers


# =================================================================================================
# 79. PARTIAL COMPLETION
# =================================================================================================

For batch work:

    item 1 PASS
    item 2 PASS
    item 3 FAIL

persist per-item state.

Retry only failed items.

Use idempotency where possible.

Avoid duplicate side effects after crash/restart.


# =================================================================================================
# 80. SIDE EFFECT LEDGER
# =================================================================================================

Track every meaningful external change:

    files
    messages
    browser writes
    DB writes
    application changes
    external API writes

Include reversibility where possible.


# =================================================================================================
# 81. ACTION DRY-RUN
# =================================================================================================

For consequential task where practical:

    PLAN
      ↓
    DRY RUN
      ↓
    expected side effects
      ↓
    policy / approval
      ↓
    REAL RUN


# =================================================================================================
# 82. BLAST RADIUS
# =================================================================================================

Estimate:

    affected resources
    affected users
    financial amount
    data scope
    reversibility
    number of systems

Increase:

    reasoning
    confirmation
    verification

as blast radius increases.


# =================================================================================================
# 83. CHAOS TESTING
# =================================================================================================

Intentionally test:

    browser crash
    provider outage
    Hermes crash
    invalid model response
    DB contention
    network loss
    malformed tool output
    worker death
    reboot

FRIDAY must fail/recover truthfully.


# =================================================================================================
# 84. BYZANTINE WORKER TEST
# =================================================================================================

Introduce worker that falsely claims:

    tests passed

while artifact says fail.

Manager must follow evidence, not worker assertion.


# =================================================================================================
# 85. OBJECTIVE OS
# =================================================================================================

Persist:

    ID
    workspace
    user
    goal
    version
    success criteria
    constraints
    exact literals
    assumptions
    dependencies
    task graph
    workers
    capabilities
    budgets
    privacy
    risk
    blast radius
    evidence
    checkpoints
    experiments
    blockers
    retries
    verifier
    next action
    state


# =================================================================================================
# 86. OBJECTIVE AMENDMENT
# =================================================================================================

When user changes requirement:

    increment objective version
    run impact analysis
    invalidate dependent work
    preserve unaffected verified work


# =================================================================================================
# 87. ASSUMPTION REGISTER
# =================================================================================================

Store:

    assumption
    origin
    confidence
    consequence
    validation
    current state

Validate high-impact assumptions early.


# =================================================================================================
# 88. EXPERIMENT ENGINE
# =================================================================================================

Prefer experiment when cheap real evidence beats more reasoning.

Record:

    hypothesis
    setup
    variables
    measurement
    budget
    stop criteria
    output
    interpretation


# =================================================================================================
# 89. COUNTERFACTUAL ENGINE
# =================================================================================================

For important plans compare:

    A
    B
    do nothing
    defer
    experiment first

Separate:

    fact
    measurement
    simulation
    inference


# =================================================================================================
# 90. WORLD MODEL
# =================================================================================================

Maintain structured entities/states where needed.

Never turn:

    model hypothesis

into:

    observed real state

without evidence.


# =================================================================================================
# 91. DIGITAL TWIN BOUNDARY
# =================================================================================================

Future physical control:

    intent
      ↓
    world model
      ↓
    formal constraints
      ↓
    simulation
      ↓
    safety
      ↓
    deterministic controller
      ↓
    hardware
      ↓
    sensor feedback

No unrestricted LLM motor-control loop.


# =================================================================================================
# 92. CAPABILITY BUILDER
# =================================================================================================

When requested capability is absent:

    search current capabilities
      ↓
    search skills
      ↓
    search plugins/MCP
      ↓
    research official options
      ↓
    research GitHub
      ↓
    compare
      ↓
    trust pipeline
      ↓
    sandbox
      ↓
    implement/adapt
      ↓
    test
      ↓
    register
      ↓
    RETURN TO ORIGINAL USER TASK

Do not stop after building capability.


# =================================================================================================
# 93. TECHNOLOGY SCOUT
# =================================================================================================

Periodic/event-driven only.

Candidate must map to:

    measured weakness
    active requirement
    cost problem
    reliability problem
    missing capability
    security issue

Trending != useful.


# =================================================================================================
# 94. SELF-DEVELOPMENT
# =================================================================================================

Candidate work happens in:

    isolated branch/worktree.

Flow:

    detect weakness
      ↓
    reproduce
      ↓
    candidate change
      ↓
    tests
      ↓
    adversarial review
      ↓
    benchmark
      ↓
    compare
      ↓
    promote / rollback

Never directly mutate protected production controls.


# =================================================================================================
# 95. PROTECTED KERNEL
# =================================================================================================

Protected:

    Policy Engine
    Secret Broker
    Permission Evaluator
    Verification Trust Root
    Update Trust Root
    Audit Integrity
    Emergency Stop

Requires protected change workflow.


# =================================================================================================
# 96. EMERGENCY STOP
# =================================================================================================

STOP is deterministic.

When triggered:

    block new work
    revoke capability leases
    revoke network
    cancel model calls
    stop owned workers
    stop browser/desktop queue
    persist recoverable state

LLM cannot veto STOP.


# =================================================================================================
# 97. SKILL / CAPABILITY GARBAGE COLLECTION
# =================================================================================================

Periodically find:

    unused
    duplicate
    degraded
    stale
    superseded
    unhealthy

Move to:

    ARCHIVED / DEPRECATED

rather than leaving endless active catalog bloat.


# =================================================================================================
# 98. RESEARCH / MODEL / SKILL ARENAS
# =================================================================================================

ModelArena:
    compare models.

ResearchArena:
    compare research engines.

SkillArena:
    compare overlapping skills.

CompressionArena:
    compare context compression strategies.

CapabilityPathArena:
    compare API/tool/UI approaches.

All promotion is empirical.


# =================================================================================================
# 99. TOKEN PRODUCTIVITY
# =================================================================================================

Optimize:

    verified useful work
    --------------------
         100K tokens

not:

    fewest tokens at any cost.

Track:

    requirements closed
    defects fixed
    accepted artifacts
    successful objectives
    token usage
    retries
    wall time


# =================================================================================================
# 100. INSTALLER
# =================================================================================================

Clean user installation should autonomously provision needed components.

Possible:

    FRIDAY
    Hermes
    browser runtime
    DB
    selected local models
    MCP framework
    CLIs
    updater
    background runtime

Initial installation may require Internet.

End user should not perform developer-level setup.


# =================================================================================================
# 101. HARDWARE PROFILE
# =================================================================================================

Minimum target:

    8 GB RAM
    4 cores
    no GPU

Profile determines:

    model residency
    worker concurrency
    local vs cloud preference
    browser concurrency
    research parallelism


# =================================================================================================
# 102. VOICE / REMOTE / MOBILE
# =================================================================================================

Voice:
    realtime interface, not main brain.

Remote:
    Telegram first
    web later
    mobile app later.

Mobile:
    Android adapter
    granular permissions.

All remain subordinate to FRIDAY policy.


# =================================================================================================
# 103. PROGRESS COMMUNICATION
# =================================================================================================

For long tasks:

    notify meaningful milestones.

Not every tool call.

Examples:

    "Repository audit complete; now fixing three P0 evidence defects."

    "Context benchmark running; memory work continues in parallel."

    "Capability built; verifying the original user task now."

User may choose:

    detailed
    balanced
    major-only


# =================================================================================================
# 104. TEST STRATEGY
# =================================================================================================

Use:

    UNIT
      ↓
    COMPONENT
      ↓
    INTEGRATION
      ↓
    ADVERSARIAL
      ↓
    GOLDEN JOURNEY
      ↓
    E2E
      ↓
    SOAK

Do not run every expensive test after every small edit.

Target first.

Full gate later.


# =================================================================================================
# 105. NEGATIVE CONTROLS
# =================================================================================================

Every critical guard proves a red path.

Examples:

    dummy secret leaks
        → test RED

    cross-workspace request
        → block

    compaction removes pinned literal
        → fidelity RED

    fake skill contains injection
        → Skill Trust RED

    MCP schema changes unexpectedly
        → suspended

    cyber worker targets unauthorized host
        → blocked

    model invents tool
        → rejected


# =================================================================================================
# 106. RELEASE GATE
# =================================================================================================

Before release check applicable:

    deterministic tests
    integration
    adversarial
    context lab
    model router
    System One
    memory poisoning
    skill trust pipeline
    MCP firewall
    secret protection
    LOCAL_ONLY
    workspace isolation
    action evidence
    emergency stop
    objective recovery
    browser/desktop
    CLI handoff
    research OS
    installer
    8 GB profile
    self-development rollback
    CI
    soak


# =================================================================================================
# 107. FINAL CLEAN-ROOM AUDIT
# =================================================================================================

After ledger appears complete:

    start a fresh audit worker.

Do not provide builder self-justification as primary context.

Audit:

    duplicate systems
    hidden TODOs
    context rot
    unverified skill trust
    model routing waste
    cyber-domain contamination
    secrets
    workspace leaks
    false success claims
    capability composition
    incorrect tool health
    stale skills
    package/install
    stop/recovery
    docs truth

Fix findings.

Repeat until no new P0/P1 issue appears.


# =================================================================================================
# 108. REQUIRED OUTPUT FILES
# =================================================================================================

Maintain:

    docs/implementation/PRD_REQUIREMENTS_LEDGER.md
    docs/implementation/PRD_CURRENT_STATE.md
    docs/implementation/PRD_DEFECT_REGISTER.md
    docs/implementation/PRD_RESEARCH_REGISTER.md
    docs/implementation/PRD_FINAL_REPORT.md

and:

    data/prd_requirements.json
    data/prd_final_verdict.json


# =================================================================================================
# 109. DURABLE HANDOFF
# =================================================================================================

Before any session reset:

    OBJECTIVE
    OBJECTIVE VERSION
    HEAD
    WORKTREE
    VERIFIED REQUIREMENTS
    ACTIVE REQUIREMENT
    CURRENT FILES
    CURRENT TESTS
    FAILURES
    KEY DECISIONS
    CRITICAL FACTS
    ARTIFACTS
    NEXT ACTION
    DO NOT REPEAT

Validate handoff before terminating old context.


# =================================================================================================
# 110. ANTI-SLOP RULE
# =================================================================================================

Do NOT create:

    empty agent classes
    unused managers
    duplicate routers
    placeholder modules
    giant abstraction layers
    fake TODO implementations
    documentation-only features

Every abstraction must solve a measurable requirement.


# =================================================================================================
# 111. ANTI-FABRICATION RULE
# =================================================================================================

Never fabricate:

    test result
    file creation
    browser action
    research result
    model health
    benchmark
    CI
    skill validation
    install success
    capability execution
    soak result

If unknown:

    UNKNOWN.

If blocked:

    BLOCKED.

If untested:

    NOT_RUN.


# =================================================================================================
# 112. AUTONOMOUS START
# =================================================================================================

BEGIN NOW.

1. Locate PRD.md.
2. Compute hash/version.
3. Establish repository truth.
4. Preserve existing user work.
5. Read architecture/current ledgers.
6. Discover installed skills/MCP/plugins/capabilities/models.
7. Build requirement ledger.
8. Build dependency DAG.
9. Run baseline.
10. Identify unresolved P0/P1.
11. Classify tasks by domain.
12. Start highest-leverage dependency-ready work.
13. Continue Master Loop.


# =================================================================================================
# 113. AUTONOMOUS CONTINUATION
# =================================================================================================

LOOP:

    refresh ledger

    classify next work
    identify dependencies
    choose context
    choose intelligence
    choose capability
    establish execution envelope

    implement
    test
    verify
    record evidence

    update:
        Objective OS
        Project Brain
        Failure Genome
        Research Store
        Skill candidates
        benchmarks

    manage context health

    if context degraded:
        validated fresh-session handoff

    continue

UNTIL:

    Completion Contract is satisfied.


# =================================================================================================
# 114. NEVER STOP BECAUSE
# =================================================================================================

Do not stop because:

    phase finished
    context large
    one worker failed
    one model refused
    one provider offline
    one test failed
    research finished
    capability was built
    unit tests passed
    CI passed

These are transition events.

Not program completion.


# =================================================================================================
# 115. FINAL COMPLETION CONTRACT
# =================================================================================================

Before exit:

    all applicable requirements terminal
    all feasible P0/P1 complete
    blockers precise
    final tests complete
    security tests complete
    golden journeys complete
    release gate complete
    CI state known
    soak state known
    final report saved
    verdict saved
    temporary workers cleaned
    state flushed
    exact HEAD recorded


# =================================================================================================
# 116. FINAL OUTPUT
# =================================================================================================

Print only after completion:

FRIDAY_PRD_AUTONOMOUS_PROGRAM_COMPLETE
PRD_STATUS=<PASS|PARTIAL|BLOCKED|FAIL>
PRD_VERSION=<version>
PRD_HASH=<hash>
HEAD=<sha>
TOTAL_REQUIREMENTS=<n>
VERIFIED=<n>
BLOCKED_EXTERNAL=<n>
DEFERRED_BY_PRD=<n>
NOT_APPLICABLE=<n>
REJECTED_WITH_EVIDENCE=<n>
UNRESOLVED_P0=<n>
UNRESOLVED_P1=<n>
CONTEXT_GATE=<PASS|FAIL|BLOCKED>
SYSTEM_ONE_GATE=<PASS|FAIL|BLOCKED>
RESEARCH_GATE=<PASS|FAIL|BLOCKED>
SKILL_TRUST_GATE=<PASS|FAIL|BLOCKED>
SECURITY_GATE=<PASS|FAIL|BLOCKED>
BROWSER_GATE=<PASS|FAIL|BLOCKED>
INSTALL_GATE=<PASS|FAIL|BLOCKED>
CI=<PASS|FAIL|BLOCKED|NOT_RUN>
SOAK=<PASS|FAIL|INCONCLUSIVE|NOT_APPLICABLE>
RELEASE_GATE=<PASS|FAIL|BLOCKED|INCONCLUSIVE>
REPORT=docs/implementation/PRD_FINAL_REPORT.md
VERDICT=data/prd_final_verdict.json
EXIT_NOW


# =================================================================================================
# 117. LAST DIRECTIVE
# =================================================================================================

DO NOT JUST MAKE FRIDAY TALK BETTER.

MAKE FRIDAY DO.

MAKE IT:

    understand
    research
    decide
    build
    operate
    observe
    verify
    recover
    remember
    learn
    improve

Use deterministic software wherever enough.

Use System One for cheap fast decisions.

Use System Two for actual hard reasoning.

Use strong frontier models only where their intelligence is valuable.

Keep context small.

Compress only where safe.

Keep raw evidence recoverable.

Use skills only when they earn their context.

Treat every imported capability as untrusted until verified.

Separate cyber-specialist execution from general FRIDAY development.

Make every important action produce evidence.

Make every critical guard prove it can fail.

Never confuse model confidence with reality.

Never confuse code generation with completion.

Never confuse research with implementation.

Never confuse a capability with fulfillment of the user's original goal.

Persist state.

Reset context when needed.

Continue.

BUILD THE FULL APPLICABLE PRD.
VERIFY IT.
ONLY THEN EXIT.
```

## Appendix D. Architecture additions, 2026-09-27 (owner's text, verbatim)

````text
Yes. I went looking specifically for **gaps that are still not fully solved by our current FRIDAY architecture**, rather than just hunting for more GitHub repos to install.

The current draft is already quite strong: it has domain partitioning, safeguard-aware routing, System One, CortexOS/Caveman, zero-trust worker messaging, blast-radius prediction, chaos testing, partial-completion recovery, idempotency, side-effect tracking, privacy budgets, SkillSpector-style supply-chain controls, Kali isolation and the Cyber Lab separation. :chatgpt-content-reference{index="0"} It also already recognizes that worker claims need evidence, permissions should be predicted up front, and consequential actions benefit from dry runs. :chatgpt-content-reference{index="1"}

After researching what has moved in 2026, though, I think there are **six major systems we should promote into the architecture now**, plus several smaller ideas.

## 1. We are missing a true **Durable Execution Kernel**

This is probably the biggest remaining architectural gap.

Objective OS currently remembers:

```text
what FRIDAY wants to do
what tasks exist
what completed
what failed
```

But we also need something below it that guarantees:

```text
crash
restart
machine reboot
worker replacement
model replacement
user disappears for 3 days
         ↓
resume exactly where we were
without repeating external side effects
```

Temporal's current agent work is useful here. Its durable agent harness treats every agent workflow as durable state, so a worker can die/redeploy and the task resumes without blindly rerunning completed tool calls. Its event stream records model interactions, tool starts/ends/errors, approvals, handoffs and token usage. :chatgpt-content-reference{index="2"}

I **would not automatically make Temporal mandatory** for your Windows desktop product. That could be excessive.

Instead build:

# `DurableExecutionKernel`

```text
Objective OS
      │
      ▼
DurableExecutionKernel
      │
 ┌────┼─────┐
 ▼    ▼     ▼
Event Command Side-effect
 Log   Log     Ledger
 │
 ▼
Replay / Resume
```

Every significant state change becomes an event:

```text
OBJECTIVE_CREATED
PLAN_CREATED
WORKER_STARTED
TOOL_CALLED
SIDE_EFFECT_COMMITTED
ACTION_VERIFIED
USER_INTERRUPTED
OBJECTIVE_AMENDED
WORKER_CRASHED
OBJECTIVE_RESUMED
```

The key rule:

> **State replay must never mean re-executing the real-world side effect.**

Example:

```text
SEND_EMAIL requested
SEND_EMAIL committed
message_id = abc123
```

After reboot, FRIDAY restores the event history.

It does **not send the email again**.

This strengthens our existing idempotency and Side-Effect Ledger ideas substantially.

Later we can have:

```text
DurableRuntime
├── FridayNativeLocal
└── TemporalAdapter
```

Temporal becomes optional for server/enterprise deployments.

---

# 2. Use **A2A v1.0** for FRIDAY-to-FRIDAY communication

This directly matches one of your earlier requirements:

> two different Fridays should be able to communicate if the users permit it.

We were going to invent our own protocol.

I don't think we should anymore.

**Agent2Agent (A2A) v1.0 shipped as a production-ready protocol in March 2026**, is now backed by more than 150 organizations, and in August joined the Agentic AI Foundation as a Growth Stage project. Its purpose is exactly what we need: agent discovery, task delegation and artifact exchange between otherwise independent agents. :chatgpt-content-reference{index="3"}

There is a very clean conceptual split:

```text
MCP
=
FRIDAY ↔ Tools

A2A
=
FRIDAY ↔ Other Agents
```

The A2A spec explicitly describes agents collaborating **without requiring access to each other's internal memory or tools**, which is exactly what we want for company/workspace boundaries. :chatgpt-content-reference{index="4"}

So replace our custom future federation protocol with:

# `FridayFederationGateway`

```text
FRIDAY A
   │
   ▼
FridayFederationGateway
   │
   ▼
A2A
   │
   ▼
FridayFederationGateway
   │
   ▼
FRIDAY B
```

But there is an important distinction:

### A2A handles transport/interoperability.

FRIDAY still handles:

```text
identity
workspace
authority
privacy
data classification
approval
trust
evidence
```

An A2A Agent Card saying:

> “I can handle financial reports.”

doesn't give that remote agent permission to see ours.

So our earlier Zero-Trust Agent Messaging stays.

A2A simply becomes the wire protocol.

---

# 3. Research **AgentGateway** as our unified connectivity plane

This repo is more interesting than I expected:

`agentgateway/agentgateway`

It currently handles:

- LLM gateway/routing;
- MCP gateway;
- A2A gateway;
- OAuth;
- load balancing;
- failover;
- spend controls;
- observability;
- self-hosted inference routing. :chatgpt-content-reference{index="5"}

That overlaps heavily with things we're independently building:

```text
Model Gateway
MCP Router
MCP Firewall
A2A Gateway
cost controls
provider failover
```

But I would **not immediately add it**.

This is where I want to challenge the tendency to keep integrating everything.

If we add:

```text
Hermes gateway
+
FRIDAY model gateway
+
AgentGateway
+
LiteLLM
+
MCP gateway
```

we've created five layers solving overlapping problems.

So put AgentGateway into an **Architecture Bake-Off**:

```text
OPTION A
FRIDAY native connectivity

OPTION B
AgentGateway underneath FRIDAY

OPTION C
hybrid:
AgentGateway transport
+
FRIDAY policy/routing
```

Benchmark:

```text
latency
RAM
provider support
MCP
A2A
OAuth
local operation
Windows
security
failure recovery
observability
code complexity
```

If AgentGateway wins enough of that, we can potentially **delete a lot of code instead of adding more code**.

That's exactly the kind of decision Ponytail philosophy should encourage.

---

# 4. Add **WebMCP** to Browser Intelligence

This one is new enough that I think it belongs in the PRD as **experimental but strategically important**.

Google and Microsoft are working on WebMCP, where a website exposes structured browser-native tools instead of forcing agents to infer everything from DOM/screenshots. Chrome currently exposes it experimentally/origin-trial style. :chatgpt-content-reference{index="6"}

Imagine Shopify eventually exposing:

```text
search_orders()
get_product()
update_product()
issue_refund()
```

Then instead of:

```text
screenshot
→ vision
→ find button
→ click
→ wait
→ screenshot
```

FRIDAY does:

```text
browser detects WebMCP

update_product({
  id: ...
})
```

Much less context.

Much less brittleness.

Much faster.

For our Browser Action Router I would change the hierarchy to:

```text
1. WebMCP if available and trusted
2. official/native API/tool
3. application scripting
4. accessibility / DOM
5. vision
6. coordinates
```

But **don't depend on WebMCP yet**. Chrome still treats it as experimental, and ecosystem adoption is early. :chatgpt-content-reference{index="7"}

So:

```text
WebMCPConsumer = experimental adapter
```

Also, for websites **we own**, such as AiCodePro or other future products, FRIDAY's website worker could optionally make them **agent-native** by publishing WebMCP tools.

That's an interesting commercial direction:

> Build websites that humans can use normally but AI assistants can operate reliably.

Chrome's guidance already defines useful hints such as `readOnlyHint` and `consequentialHint` for actions that mutate important state. :chatgpt-content-reference{index="8"}

We should actually generalize those ideas into our entire Capability Fabric:

```text
read_only
consequential
reversible
idempotent
external_side_effect
requires_confirmation
```

Not only WebMCP.

---

# 5. Add a real **Observability Plane**, based on OpenTelemetry

We're building:

```text
Reality Ledger
Black Box
Model Arena
token tracking
context metrics
worker traces
```

But we shouldn't create a completely proprietary telemetry format.

OpenTelemetry now has GenAI semantic conventions for model invocations, agent calls, tool executions, token usage and latency. It can represent agent → model → tool span trees. :chatgpt-content-reference{index="9"}

So create:

# `FridayTelemetryPlane`

```text
Objective
  │
  ├── Agent Span
  │     ├── Model Span
  │     ├── Tool Span
  │     └── Worker Span
  │
  ├── Capability Span
  ├── Evidence Span
  └── Verification Span
```

Then Developer Mode can answer:

> Why did this take 43 seconds?

Perhaps:

```text
Claude             3.2s
Browser             6.8s
Tool retry          21s
Context retrieval   0.4s
Verification        11s
```

rather than guessing.

And it gives us exactly what the Neural Fabric needs later:

```text
model
tokens
latency
tool count
context size
skill
outcome
cost
```

### Privacy warning

OpenTelemetry permits recording prompt/completion contents, but those fields can contain sensitive information and are intended to be opt-in. :chatgpt-content-reference{index="10"}

So FRIDAY defaults:

```text
metadata telemetry = ON/local

prompt content logging = OFF

secret content = NEVER
```

Developer can explicitly enable deeper local traces.

---

# 6. Add **AgentRx-style Causal Debugging**

This is one I strongly recommend.

We currently have:

```text
StruggleDetector
Failure Genome
Reality Ledger
```

Those tell us that an agent failed and help avoid repeated loops.

But they don't necessarily tell us:

> **Where did the trajectory first become unrecoverably wrong?**

Microsoft Research released AgentRx in March 2026 specifically for this problem. It converts a trajectory into an intermediate representation, derives invariants, checks them step-by-step, then identifies the **critical failure step** rather than only analyzing the final error. Microsoft reports improvements of 23.6 percentage points in failure localization and 22.9 points in root-cause attribution versus prompting baselines on its benchmark. :chatgpt-content-reference{index="11"}

Add:

# `TrajectoryDebugger`

```text
FAILED OBJECTIVE
       ↓
Reality Trace
       ↓
normalize
       ↓
derive invariants
       ↓
step-by-step checks
       ↓
FIRST CRITICAL FAILURE
       ↓
root cause
       ↓
Failure Genome
       ↓
regression test
```

Example:

```text
Step 3:
web search fine

Step 4:
source parsed fine

Step 5:
agent incorrectly changed objective constraint

Step 6–25:
everything downstream wrong
```

Today we might spend hours debugging step 25.

TrajectoryDebugger says:

> Step 5 was where the run actually broke.

This is extremely valuable for autonomous self-development.

---

# 7. Add **Agent Behavioral Contracts**

This might be one of the deepest upgrades.

Right now an Objective Contract is mostly structured state.

But for critical objectives, turn parts of it into **runtime assertions**.

A 2026 research proposal called Agent Behavioral Contracts expresses agent behavior as:

```text
P = Preconditions
I = Invariants
G = Governance rules
R = Recovery mechanisms
```

and runs those checks continuously instead of expecting the prompt to enforce them. :chatgpt-content-reference{index="12"}

I don't recommend making their particular AgentAssert implementation a core dependency yet—it's new research and the repo is AGPL.

But I very much like the concept.

Add:

# `BehaviorContractEngine`

Example:

```yaml
objective:
  update 20 Shopify products

preconditions:
  authenticated: true
  workspace: crunchy-fashion

invariants:
  currency: INR
  product_ids_must_match_request: true
  max_price_delta_percent: 10

governance:
  no_delete: true
  max_external_writes: 20

recovery:
  on_partial_failure: resume_failed_items_only
  on_auth_expiry: wait_for_user
```

Then every worker must obey an enforceable contract.

This is stronger than:

> “Remember not to delete anything.”

---

# 8. Human interaction needs a proper **Signal Bus**

We already support:

```text
WAITING_USER
```

But I want to formalize your idea that FRIDAY runs for hours/days and only asks when necessary.

The Temporal HITL work has a useful mental model:

> **the human is an asynchronous API**.

A workflow can suspend, wait for a human event, survive restarts, then resume from that event. :chatgpt-content-reference{index="13"}

So add:

# `HumanSignalBus`

Signals:

```text
USER_CORRECTION
USER_APPROVAL
USER_DENIAL
USER_ANSWER
USER_STOP
USER_PRIORITY_CHANGE
USER_POLICY_CHANGE
USER_BACK
```

They can arrive via:

```text
desktop
voice
Telegram
web
mobile
```

Same Objective OS.

This solves another race condition:

```text
user changes instruction on phone
while PC worker is still running
```

Signal hits Objective OS → amendment barrier → old dependent actions stop.

---

# 9. Another important gap: **approval staleness**

This comes directly from thinking through long-running execution.

Suppose:

```text
10:00
user approves:
"publish this campaign"

10:05
FRIDAY changes campaign significantly

10:10
worker uses original approval
```

That approval should no longer automatically apply.

So approvals should bind to:

```text
objective_id
objective_version
action_class
target
blast_radius
plan_hash
expiry
```

Call this:

# `ApprovalLease`

If important state changes:

```text
approval becomes STALE
```

This is much safer than simply storing:

```text
user_allowed_publish = true
```

Standing permission is different:

```text
"FRIDAY may publish normal social posts."
```

That belongs in a scoped policy rule.

---

# 10. Another gap: **TOCTOU for capabilities**

We scan a skill at 10:00.

It changes at 10:05.

We execute at 10:10.

You already added mutation/rug-pull detection for skills and MCP. Good. :chatgpt-content-reference{index="14"}

I would generalize it into:

# `JustInTimeAuthorityCheck`

Immediately before consequential action verify:

```text
capability hash unchanged?
skill version unchanged?
auth still valid?
objective version unchanged?
approval still valid?
network token alive?
target state still compatible?
```

So policy occurs both:

```text
PLAN TIME
```

and:

```text
EXECUTION TIME
```

This is important for long-running autonomy.

---

# 11. AgentBench is worth using as **one external benchmark**

I found `agentbench/agentbench`, which currently focuses on benchmarking the **agent setup rather than only the underlying model**, with 40 real-world tasks across seven domains and rule-based checks rather than LLM judges. :chatgpt-content-reference{index="15"}

I wouldn't make it our release authority.

But put it in:

```text
External Evaluation Pack
```

alongside:

```text
OSWorld
our Context Lab
our Model Arena
our Golden Journeys
```

If FRIDAY improves internally but gets substantially worse on an independent deterministic suite, that's useful evidence.

---

# 12. `OpenSharing` belongs on the **watchlist**

This matters because earlier you wanted:

> eventually allow people to share skills/capabilities if they choose.

Linux Foundation launched OpenSharing this year for open exchange of data, models and agent skills. The project already has an AgentSkill sharing proposal and an early agent-service sharing specification using scoped temporary credentials. :chatgpt-content-reference{index="16"}

I would not implement this now.

But instead of inventing:

```text
Friday Skill Marketplace Protocol
```

from scratch, create:

```text
CapabilityExchangeAdapter
```

and watch OpenSharing.

Later:

```text
Local private skill
    ↓ user chooses publish
Skill Trust Pipeline
    ↓
sign
    ↓
OpenSharingAdapter
```

Potentially excellent.

---

# What I think we should do now

I want to push against one danger here:

**We should not keep growing the PRD forever.**

We're approaching the point where adding another 50 named systems starts reducing clarity instead of increasing capability.

The clever solution is that **FRIDAY itself already contains Technology Scout + Capability Builder**.

That means the PRD does *not* need to predict every useful repository that will exist in 2027.

We need to freeze the **meta-architecture that can absorb future discoveries**.

I would promote exactly these additions into the next frozen architecture:

1. **DurableExecutionKernel** — event-sourced objectives, side-effect-safe replay and resumable execution.
2. **FridayFederationGateway using A2A v1.0** — FRIDAY↔FRIDAY / agent↔agent interoperability.
3. **ConnectivityPlane bake-off** — evaluate AgentGateway before duplicating Model/MCP/A2A infrastructure.
4. **WebMCPConsumer + experimental WebMCPProducer** — structured browser-native website actions.
5. **FridayTelemetryPlane using OpenTelemetry GenAI conventions** — standard tracing, token, latency and tool observability.
6. **TrajectoryDebugger inspired by AgentRx** — find the first critical failure in long agent runs.
7. **BehaviorContractEngine** — convert important objective constraints into runtime preconditions/invariants/governance/recovery.
8. **HumanSignalBus + ApprovalLease** — robust human interaction with objectives that can wait hours/days.
9. **JustInTimeAuthorityCheck** — revalidate permissions/state immediately before a side effect.
10. **ExternalEvalPack + future CapabilityExchangeAdapter** — AgentBench now, OpenSharing watchlist.

And I would **not introduce ten new independent frameworks to implement those systems**.

The architecture should become:

```text
                           USER
                            │
                  Human Signal Bus
                            │
                            ▼
                         FRIDAY
                            │
                    Intent Compiler
                            │
                            ▼
                       Objective OS
                            │
                  Behavioral Contract
                            │
                            ▼
                 Durable Execution Kernel
                            │
              ┌─────────────┼─────────────┐
              ▼             ▼             ▼
          CortexOS     Cognitive Gov.   World Model
              │             │
              │        ┌────┴─────┐
              │        ▼          ▼
              │    System One   System Two
              │   Laya/Jev/Net   Models
              │
              ▼
         Research OS
              │
              ▼
          CLI Gateway
              │
              ▼
      Connectivity Plane
     ┌────────┼─────────┐
     ▼        ▼         ▼
    MCP      A2A      WebMCP
     │        │         │
     └────────┼─────────┘
              ▼
       Capability Fabric
              │
       JIT Authority Check
              │
              ▼
            REALITY
              │
      ┌───────┼─────────┐
      ▼       ▼         ▼
 Reality   Epistemic   Side-Effect
 Ledger     Ledger       Ledger
      │
      ▼
 Independent Verifier
      │
      ▼
 Trajectory Debugger
      │
      ▼
 Failure Genome / Skills / Learning
              │
              ▼
        Neural Fabric
```

And running across the whole thing:

```text
OpenTelemetry Trace Plane
Resource Governor
Capability Trust Pipeline
Emergency Stop
```

## The one idea I think is most important

If I had to choose only **one** thing from today's research, I would pick:

# **Durable Execution + Behavioral Contracts**

Because those two move FRIDAY from:

> an intelligent agent that can work for a while

to:

> a reliable autonomous system that can work for hours, days, restart, accept user changes, survive failures, and still obey the original constraints.

A frontier model is replaceable.

A skill is replaceable.

A browser engine is replaceable.

**Durability + contracts + evidence are what turn all of those components into a product.**

Then I'd put **A2A + WebMCP** directly behind them, because those are likely to become important interoperability standards rather than something we should reinvent.

So my recommendation is: **add these 10 architecture upgrades, freeze the v8 core architecture, and then stop expanding the master PRD until the first implementation/baseline run tells us where the actual gaps are.** After that, FRIDAY's Technology Scout can keep discovering new things without forcing us to rewrite its brain every week.
````

## Appendix E. Local-model hardware policy and the prompt research corpus, 2026-09-27 (owner's text, verbatim)

````text
Yes. I would use `system_prompts_leaks`, but **not as a dependency and not by copying giant prompts into FRIDAY**. It should become a research corpus for a new **Prompt Architecture Lab**.

The repository is unusually useful because it is being updated with current prompts from Claude Opus 5.5/Claude Code, Codex, Gemini, Grok, OpenCode and other agent systems. It therefore gives us a moving picture of how strong production agents handle tool use, skill discovery, delegation, confirmations, research, computer use and context. :chatgpt-content-reference{index="0"}

But your hardware detail changes my previous local-model recommendation significantly:

> **Your actual target machine = 16 GB RAM + 4 GB VRAM.**
>
> **FRIDAY local model artifact cap = <2 GB per model.**

I think that is a very good constraint.

## First: change FRIDAY's local-model philosophy

We should **not** try to squeeze a 7B/8B model onto your laptop.

Instead:

```text
                       FRIDAY
                          │
                 Cognitive Governor
                          │
        ┌─────────────────┼─────────────────┐
        ▼                 ▼                 ▼
  MICRO MODELS       LOCAL WORKER        CLOUD BRAIN
        │                 │                 │
 Laya / 270M       MiniCPM 1B/2B       Claude / GPT
 embeddings       local vision          Gemini / etc.
        │                 │                 │
        └─────────────────┼─────────────────┘
                          ▼
                    VERIFY / ESCALATE
```

The local models do **cheap private work**.

The cloud frontier models do **hard work**.

FRIDAY decides when each is worth using.

---

# Your new strict local model pack

I would lock the following hardware policy:

| FRIDAY role | Candidate | Approx. model size | Decision |
|---|---|---:|---|
| Fast typed decisions | **Laya** | 322M–421M params | **Use** |
| Tool/action reflex model | **FunctionGemma** | 270M params | **Use / fine-tune later** |
| Embeddings | **EmbeddingGemma** | 308M; <200 MB RAM quantized | **Use** |
| Local general-lite | **MiniCPM5-1B Q4** | **657 MB** | **Use** |
| Local general worker | **MiniCPM5-2B Q4_K_M** | **1.56 GB** | **Use** |
| Local vision | **MiniCPM-V 4.6 Q4 + vision projector** | ~**1.6 GB total** | **Use on demand** |
| Hosted System-1 | Jev | local footprint 0 | Optional |
| Big reasoning | Claude/GPT/Gemini | local footprint 0 | Cloud escalation |

OpenBMB's official GGUF releases put MiniCPM5-1B Q4_K_M at 657 MB and MiniCPM5-2B Q4_K_M at 1.56 GB. Their deployment guide specifically describes the 1.56 GB quant as ideal for laptop/edge use. :chatgpt-content-reference{index="1"}

For vision, MiniCPM-V 4.6 is particularly interesting for your machine: its Q4 language component is roughly 0.5 GB and the vision projector roughly 1.1 GB, giving about **1.6 GB total download**, while OpenBMB recommends at least 6 GB system RAM. That's much more appropriate for your 16 GB laptop than a 7B vision model. :chatgpt-content-reference{index="2"}

FunctionGemma is Google's purpose-built 270M function-calling model. Google explicitly says it is intended to be fine-tuned for specific function-calling workloads rather than used as a general chatbot—which is almost exactly what we need for a future **FridayAction** model. :chatgpt-content-reference{index="3"}

And EmbeddingGemma is only 308M parameters and Google reports that a quantized configuration can run in under 200 MB RAM, making it attractive for always-available local memory/research retrieval. :chatgpt-content-reference{index="4"}

---

# Important correction: 1.56 GB file ≠ 1.56 GB runtime VRAM

This matters.

Running MiniCPM5-2B requires:

```text
model weights
+
KV cache
+
runtime buffers
+
context
+
CUDA/llama.cpp overhead
```

So with your **4 GB VRAM**, we should not tell llama.cpp:

```text
128K context
everything GPU
max parallelism
```

and expect a good experience.

OpenBMB's own recommended starter configuration for MiniCPM5-2B uses an **8K context**, despite the model supporting a much larger advertised maximum. :chatgpt-content-reference{index="5"}

This matches CortexOS perfectly.

Our target should be approximately:

```text
ordinary local task:
2K–6K useful context

coding/local reasoning:
4K–8K

exceptional:
8K–16K only after resource check
```

Instead of blindly using 100K.

---

# I would make `<2 GB` a formal FRIDAY policy

Not merely a preference.

Create:

```yaml
LocalModelPolicy:

  max_single_artifact_size:
    2GB

  preferred_size:
    <= 1.6GB

  target_hardware:
    ram: 16GB
    vram: 4GB

  max_hot_general_models:
    1

  large_context_default:
    false

  preferred_context:
    4096-8192

  gpu_headroom_required:
    true
```

Then Technology Scout discovers a new model and sees:

```text
Model XYZ
Q4 = 3.7 GB
```

FRIDAY automatically says:

```text
REJECT FOR DEFAULT LOCAL PACK
```

unless you explicitly override it.

---

# And only ONE big local worker stays hot

This is extremely important.

Do not run:

```text
MiniCPM5-2B
+
MiniCPM-V
+
another 2B model
+
voice model
```

all on GPU.

Use:

```text
ModelResidencyManager

Laya
    CPU / tiny → HOT

EmbeddingGemma
    CPU → HOT

FunctionGemma/FridayAction
    CPU/GPU → HOT or WARM

MiniCPM5-2B
    GPU → HOT while needed

MiniCPM-V
    COLD
    ↓
screenshot arrives
    ↓
unload MiniCPM text worker if necessary
    ↓
load vision worker
    ↓
analyze
    ↓
unload
```

Your 16 GB RAM gives us enough breathing room to cache/prepare models, but the **4 GB VRAM is the scarce resource**.

---

# MiniCPM5-1B might actually be more important than MiniCPM5-2B

This is something I'd now benchmark carefully.

The 1B Q4 version is only **657 MB**. :chatgpt-content-reference{index="6"}

So imagine:

```text
ordinary FRIDAY work
        ↓
MiniCPM5-1B
        ↓
confidence / verifier
    ├── sufficient
    │      ↓
    │    DONE
    │
    └── insufficient
           ↓
      MiniCPM5-2B
           ↓
      still difficult?
           ↓
     Claude/GPT/Gemini
```

That's a very efficient escalation chain.

Maybe the 1B model solves 65–80% of the boring local work.

If so, it becomes the normal local worker and the 2B is only the local escalation layer.

**Benchmark, don't guess.**

---

# `system_prompts_leaks`: yes, but give it the right job

I would classify it:

```text
asgeirtj/system_prompts_leaks

CLASS:
PROMPT_RESEARCH_CORPUS

PRIORITY:
HIGH RESEARCH VALUE

RUNTIME DEPENDENCY:
NO

PRODUCTION PROMPT SOURCE:
NO

USE:
YES
```

Why?

Because the repository contains current examples of how strong production systems solve problems we care about. For example, the Claude Science material uses explicit skill discovery followed by lazy skill loading rather than placing every skill into the prompt at once—a very close match to our CortexOS architecture. :chatgpt-content-reference{index="7"}

The OpenCode material likewise explicitly says simple file search/read tasks should not unnecessarily spawn subagents, which independently validates our **sparse-agent / don't-delegate-trivial-work** principle. :chatgpt-content-reference{index="8"}

And the Codex computer-use material shows the value of separating ordinary execution from more consequential UI actions, another design pattern relevant to our Capability Fabric. :chatgpt-content-reference{index="9"}

---

# Build a new **PromptOS**

This is the thing I'd add.

Right now our architecture talks about:

```text
CortexOS
Model Router
Skill Router
Objective OS
```

But **the prompt itself is executable cognitive configuration**.

We need to manage it like code.

```text
                         PROMPT OS
                            │
       ┌────────────────────┼────────────────────┐
       ▼                    ▼                    ▼
 Policy Kernel         Role Compiler         Task Packet
       │                    │                    │
       └────────────────────┼────────────────────┘
                            ▼
                     Prompt Compiler
                            │
                            ▼
                          MODEL
```

PromptOS should own:

```text
Prompt Pattern Library
Prompt Budget Manager
Prompt Compiler
Instruction Conflict Detector
Prompt Deduplicator
Prompt Diff Engine
Prompt Benchmark Lab
Prompt Cache Layout
Provider Prompt Profile
Prompt Regression Tests
```

---

# Don't copy the leaked prompts verbatim

This is the key.

Bad architecture:

```text
Claude Code prompt
+
Codex prompt
+
Gemini prompt
+
OpenCode prompt
=
FRIDAY system prompt
```

You'd create a 100K-token monster full of contradictions.

Instead:

```text
system_prompts_leaks
       ↓
Pattern Extractor
       ↓
candidate design principles
       ↓
A/B benchmark
       ↓
FRIDAY-native compact rule
```

Example.

We observe multiple mature systems doing:

```text
search capability first
load detailed instruction later
```

We don't copy 600 lines.

We derive:

```text
FRIDAY RULE:
Capabilities and skills MUST use progressive disclosure.
Only selected L2 instructions enter active context.
```

Maybe 20 tokens.

That's exactly your goal:

> **less tokens, more intelligence.**

---

# Add a **Prompt Pattern Miner**

I actually think a small local model could do this.

```text
system prompt corpus
      ↓
PromptPatternMiner
      ↓
identify:
  delegation rules
  context strategies
  tool strategy
  confirmation design
  research behavior
  skill loading
  failure handling
  user interaction
      ↓
dedupe patterns
      ↓
Prompt Lab
```

Then once a week Technology Scout sees:

```text
Claude Code changed its tool-search strategy.
Codex added a new context rule.
Gemini changed worker delegation.
```

It creates a research artifact.

FRIDAY doesn't automatically copy it.

It tests whether the pattern improves FRIDAY.

---

# Add **Prompt Evolution Tracking**

This repository becomes very valuable over time because it contains versions.

For example:

```text
Claude Code August
     ↓
Claude Code September
```

FRIDAY can diff:

```text
What changed?
Why might they have changed it?
Does it solve a problem we also have?
```

Call it:

# `PromptEvolutionLab`

Possible discoveries:

```text
new confirmation architecture
new tool search method
new subagent rule
new context-management rule
new computer-use policy
new research workflow
```

Then:

```text
hypothesis
↓
FRIDAY benchmark
↓
adopt/reject
```

---

# Add **Prompt Instruction Conflict Detection**

Another serious AI problem.

Suppose active prompt says:

```text
Worker Role:
always explain everything
```

Skill says:

```text
be concise
```

Task says:

```text
only output JSON
```

Plugin says:

```text
include commentary
```

You've created prompt conflict.

PromptOS should build an **Instruction Graph**:

```text
SYSTEM POLICY
     │
     ▼
PRODUCT POLICY
     │
     ▼
WORKER CONTRACT
     │
     ▼
OBJECTIVE
     │
     ▼
SKILL
     │
     ▼
TASK
```

Higher layers override lower ones when incompatible.

Before a call:

```text
PromptCompiler
    ↓
detect contradictions
    ↓
remove redundant instructions
    ↓
compile clean prompt
```

This could save tokens **and** improve reasoning.

---

# Add **Prompt Entropy Score**

New idea.

Measure how polluted a prompt is:

```text
duplicate instructions
contradictions
irrelevant sections
policy overlap
too many examples
tool noise
```

Produce:

```text
PromptHealth = 0.91
```

or:

```text
PromptHealth = 0.46

problems:
18 duplicated instructions
3 contradictory rules
12 unused tool schemas
7 stale task facts
```

Then CortexOS fixes it before model invocation.

That's essentially context health specifically for instructions.

---

# Our complete tiny-local architecture becomes

```text
                         FRIDAY
                            │
                   Cognitive Governor
                            │
         ┌──────────────────┼──────────────────┐
         ▼                  ▼                  ▼
   MICRO COGNITION    LOCAL GENERATION      CLOUD
         │                  │                  │
      Laya              MiniCPM5-1B          Claude
 FunctionGemma              │                 GPT
      │                     ▼                 Gemini
      │                MiniCPM5-2B
      │
      ├── decisions
      ├── tool selection
      ├── event triage
      └── intent


        LOCAL RETRIEVAL              LOCAL VISION
              │                           │
       EmbeddingGemma             MiniCPM-V 4.6
              │                       ~1.6 GB
           Memory


                  MODEL RESIDENCY MANAGER
                           │
                    4 GB VRAM budget
```

This is much more realistic for your laptop.

---

# I would change one previous recommendation

Earlier I mentioned candidates like:

```text
Granite 3B
LFM 2.6B
Qwen 2B
```

I would **remove them from the default installed pack**.

They stay in:

```text
Model Arena Candidate Registry
```

If Technology Scout finds an actual quantized artifact:

```text
< 2 GB
+
runs well on your laptop
+
beats MiniCPM
```

then it can replace MiniCPM.

We don't install five equivalent models.

---

# The default FRIDAY pack I'd build for your laptop

```text
FRIDAY LAPTOP PROFILE
16 GB RAM
4 GB VRAM

ALWAYS AVAILABLE

Laya
    System-One decisions

EmbeddingGemma
    memory/research retrieval

FridayAction / FunctionGemma
    tool routing / common actions


NORMAL LOCAL GENERATION

MiniCPM5-1B Q4
    ~657 MB


LOCAL ESCALATION

MiniCPM5-2B Q4_K_M
    ~1.56 GB


LOCAL VISION — LOAD ON DEMAND

MiniCPM-V 4.6
    ~1.6 GB total


FRONTIER ESCALATION

Claude
GPT
Gemini
other connected models
```

And importantly:

```text
NOT ALL LOADED AT ONCE.
```

---

## New hard architecture rule

I would add this to the PRD verbatim:

> **FRIDAY must not solve hardware limitations by loading increasingly large local language models. Local intelligence should be decomposed into small specialized models, deterministic software, retrieval, skills and temporary general workers. On the reference 16 GB RAM / 4 GB VRAM Windows system, individual default local model artifacts must remain below 2 GB, only necessary models may be resident, and CortexOS should minimize context before increasing model size. Frontier models are escalations for high-value reasoning rather than permanent local requirements.**

That gives us a **real engineering target**.

And `system_prompts_leaks` becomes especially valuable under this architecture because we can use powerful vendors' own agent-design patterns to improve **our orchestration and prompts**, while keeping the actual local models tiny. :chatgpt-content-reference{index="10"}

So instead of trying to put “Claude intelligence” into a 20 GB local model, we steal the *architectural lessons*—tool search, lazy skills, delegation discipline, context management, confirmations, evaluation—and let a 657 MB/1.56 GB worker operate inside a much smarter **FRIDAY harness**.
````

## Appendix F. Adaptive hardware and the two reference machines, 2026-09-28 (owner's text, verbatim)

````text
Exactly. **Do not tune FRIDAY to either laptop manually.** Both machines should be first-class reference targets, and FRIDAY should profile the machine at install/startup and continuously adapt itself.

So I would change the decision from:

```text
RAM > 75% → shed background work
```

to:

```text
observe this machine
→ establish safe baseline
→ calculate available headroom
→ choose operating profile
→ adapt concurrency/models/context/browser work
→ continuously re-evaluate
```

Your two main machines become:

```text
REFERENCE-A
15.4 GB RAM
integrated GPU
~0.5 GB dedicated VRAM

REFERENCE-B
16 GB RAM
4 GB dedicated VRAM
```

**FRIDAY must run usefully on both.**

### Add `AdaptiveHardwareGovernor`

At first boot, after major hardware/runtime changes, and periodically, measure:

```text
physical RAM
currently usable RAM
idle RAM baseline
memory pressure
CPU cores/threads
CPU load
GPU type
dedicated/shared VRAM
GPU backend availability
disk speed/free space
power state
battery
thermal state if available
browser baseline
FRIDAY baseline
local-model runtime availability
```

Then derive a dynamic profile rather than simply saying “16 GB machine.”

For example:

```yaml
machine_profile:
  ram_total_gb: 15.4
  ram_idle_used_percent: 76
  gpu:
    type: integrated
    dedicated_vram_gb: 0.5
    shared_memory: true

  model_policy:
    gpu_local_llm: false
    cpu_local_llm: true
    max_model_artifact_gb: 2
    preferred_model_artifact_gb: 0.7
    max_hot_generators: 1

  concurrency:
    foreground_workers: 1
    background_workers: 0
    browsers: 1
```

While on the second laptop:

```yaml
machine_profile:
  ram_total_gb: 16
  gpu:
    dedicated_vram_gb: 4

  model_policy:
    gpu_local_llm: true
    max_model_artifact_gb: 2
    preferred_model_artifact_gb: 1.6
    max_hot_generators: 1

  concurrency:
    foreground_workers: 2
    background_workers: 1
```

But those values should be **derived and benchmarked**, not permanently hardcoded.

## The 76% RAM issue

I would **not simply raise the current 75% threshold to 80%**.

That merely hides the underlying problem.

The governor should use two different measurements:

```text
ABSOLUTE PRESSURE
+
HEADROOM RELATIVE TO THIS MACHINE'S BASELINE
```

If Windows itself normally idles at 76%, FRIDAY shouldn't immediately conclude:

> emergency memory pressure.

Instead calculate approximately:

```text
BaselineUsed = normal idle usage

CurrentExtraPressure =
CurrentUsed - BaselineUsed
```

And monitor actual signals such as:

```text
available physical memory
commit pressure
pagefile activity
swap/page faults
working-set growth
allocation failures
model OOM
system responsiveness
```

So a machine naturally sitting at 76% could still be:

```text
HEALTHY
```

while another machine at 68% could be:

```text
PRESSURED
```

if available memory is collapsing rapidly.

### Resource states

I would use:

```text
GREEN
normal operation

YELLOW
reduce background concurrency

ORANGE
evict cold models
pause low-priority workers
reduce browser concurrency
shrink local context

RED
preserve objective state
stop optional work
keep only core + current foreground objective
```

Transitions should have **hysteresis** so FRIDAY doesn't constantly jump between modes:

```text
75.1% → shed
74.9% → restore
75.2% → shed
```

That would be terrible.

Require pressure to remain changed for a period before transitioning back.

---

# Make model choice machine-aware too

For the integrated-GPU laptop:

```text
Laya
EmbeddingGemma
FunctionGemma / tiny action model
MiniCPM5-1B Q4
```

are likely much more useful as CPU-oriented/default options.

MiniCPM5-2B should be:

```text
load on demand
benchmark first
unload afterward
```

rather than permanently resident.

For the 4 GB VRAM laptop:

```text
MiniCPM5-1B
or MiniCPM5-2B Q4
```

can potentially use GPU acceleration depending on measured runtime behavior.

But FRIDAY should benchmark:

```text
CPU only
partial GPU offload
full feasible GPU offload
```

and choose whichever gives the best:

```text
latency
RAM
VRAM
thermals
stability
```

Not assume GPU is always better.

---

# Add automatic machine benchmarking

During onboarding:

```text
FRIDAY Hardware Qualification
```

run lightweight benchmarks:

```text
tiny model cold-start
tiny model tokens/sec
1B model cold-start
1B tokens/sec
2B model if feasible
embedding throughput
browser + FRIDAY RAM
one worker
two workers
disk artifact IO
```

Then produce:

```text
Machine Capability Profile
```

This profile determines defaults.

And if the hardware changes:

```text
RAM upgrade
GPU driver changed
external GPU
new machine
power profile changed
```

rerun the affected benchmarks.

---

# Important: make profiles capability-based, not marketing names

Avoid:

```text
LITE
STANDARD
PRO
```

as the real internal logic.

Those can exist in UI.

Internally use capabilities:

```yaml
can_keep_2b_hot: false
can_run_local_vision: true
max_parallel_workers: 1
max_parallel_browsers: 1
preferred_context_tokens: 6000
background_learning_allowed: false
```

Then the same FRIDAY code works across machines.

---

# Background learning should adapt too

On the integrated machine:

```text
user actively working
→ no expensive background learning

machine idle
→ maybe research/indexing

night + plugged in + cool + sufficient RAM
→ deeper learning mission
```

On the stronger machine:

```text
foreground objective
+
one low-priority background research worker
```

might be fine.

So your idea of:

> “I'm going to sleep, Friday learn this”

should trigger a different resource policy from:

> “I'm gaming/rendering and talking to Friday.”

---

# Playwright decision

Because **both are primary supported machines**, I would install Playwright into the dedicated test/browser environment on the current machine and validate the browser journey here too.

Do not require some special stronger host merely for BROW-001.

But isolate it properly:

```text
browser/test dependencies
```

should not unnecessarily pollute the minimal production runtime.

I'd tell the builder:

> Install Playwright in the project/test environment using the repository's dependency-management conventions, install only the browser/runtime required by the tests initially, run BROW-001's real journey, and record its resource impact. If Playwright itself makes the minimum host unusable, treat that as product evidence and optimize the browser backend rather than exempting this machine.

---

# Commit decision

You now have:

```text
4,707 tests
0 failures
```

and multiple security fixes sitting uncommitted.

I would **commit before continuing major new slices**.

Not because the project is finished, but because this is a valuable recoverable checkpoint.

Something like:

```text
feat(core): harden authority, prompt trust and adaptive runtime foundations
```

or split it into a few coherent commits if the working tree cleanly separates:

```text
fix(policy): bind approvals and envelopes to objective authority

fix(context): quarantine tool output from worker facts

feat(runtime): add resource-pressure handling

feat(browser): establish action-path routing

test(release): verify critical negative controls
```

Do not let another six slices pile onto a large uncommitted tree.

---

## Add these formal requirements

### `RES-002 — Adaptive Hardware Profiling`

> FRIDAY SHALL detect and benchmark the execution host and derive resource/model/concurrency policies from measured machine capability instead of assuming one fixed hardware profile.

### `RES-003 — Adaptive Resource Thresholds`

> Resource pressure thresholds SHALL be calibrated relative to operating-system baseline, available headroom and observed system behavior. Static percentages may be used as guardrails but SHALL NOT alone determine health.

### `RES-004 — Runtime Reprofiling`

> FRIDAY SHALL re-evaluate relevant hardware/runtime capabilities when material environment changes are detected.

### `MODEL-009 — Hardware-Aware Local Model Residency`

> Local model loading, quantization, context size, CPU/GPU offload and residency SHALL be dynamically selected against measured RAM, VRAM, latency, thermal and stability constraints.

### `MODEL-010 — Graceful Local Degradation`

> Failure to run a preferred local model SHALL degrade to a smaller/local alternative or policy-permitted remote route without making FRIDAY itself unavailable.

### `BG-001 — Resource-Aware Background Cognition`

> Background learning, research, indexing and proactive work SHALL run only when current machine headroom, user activity, power state and objective priority permit it.

That is the correct direction: **same FRIDAY, different machine, FRIDAY adapts itself.**

You shouldn't need to know whether to use 65%, 75%, MiniCPM-1B, MiniCPM-2B, CPU, GPU, one worker or two workers. **That is FRIDAY's job.**
````

## Appendix G. Live browser readiness, test isolation and the P0 drive, 2026-09-29 (owner's text, verbatim)

````text
Yes — the implementation is going in the right direction. I’d make the following decisions now, and I’d keep the build moving from the current checkpoint rather than reopening the completed slices.

### 1. Browser runtime: **install the browser extra in FRIDAY’s live `.venv`**

BROW-001 is supposed to be a real product capability, so it should not work only inside the test environment.

Use the project’s own optional extra:

```bash
uv pip install -e ".[browser]"
```

Then run a **runtime smoke test from the same `.venv` that launches FRIDAY**:

```text
FRIDAY live runtime
→ import Playwright
→ launch Chromium
→ load harmless public page
→ read title/content
→ close browser
```

Playwright’s official Python setup requires both the Python package and compatible browser binaries; if the compatible Chromium binary is already present in Playwright’s cache, it does not need to download it again. Otherwise the runtime should provision Chromium as part of installer/first-run setup. :chatgpt-content-reference{index="0"}

This becomes a product invariant:

> `browser extra installed` + `browser binary available` + `real launch probe passes` = `BROWSER_READY`

Not just:

> Playwright imported successfully.

And because both laptops are primary machines, this real browser journey should pass on **both**, with resource usage recorded.

---

### 2. Hardware governor: **do not manually raise the 75% threshold**

Your current machine is valuable precisely because it exposes why a static threshold is wrong.

Keep the new A4 design:

```text
machine baseline
+
available headroom
+
actual pressure signals
+
hysteresis
+
resource trend
```

rather than:

```text
RAM > 75%
→ always shed work
```

Both machines become first-class reference hosts:

```text
REFERENCE HOST A
~15.4 GB RAM
integrated GPU
~0.5 GB dedicated VRAM

REFERENCE HOST B
16 GB RAM
4 GB VRAM
```

FRIDAY should automatically discover which machine it is on and derive:

```text
worker concurrency
browser concurrency
background work allowance
model residency
CPU/GPU offload
local-model choice
context ceiling
research parallelism
```

So **do not special-case either laptop by hostname**. A third 12 GB or 32 GB machine should work through exactly the same profiling logic.

I’d add a practical rule to RES-003:

```text
Static percentage thresholds are emergency guardrails,
not the primary pressure classifier.
```

---

### 3. The uncommitted Playwright + audit-isolation work: **commit it before Slice 7 grows further**

You already have an excellent checkpoint:

```text
3d956ba
4,716 passed
0 failed
```

and now another clean point:

```text
Playwright real browser journey PASS
audit-test isolation fixed
4,717 passed
0 failed
```

That deserves a second coherent checkpoint.

Something like:

```text
fix(runtime): isolate audit tests and enable real browser validation
```

The commit should include:

- browser optional extra;
- BROW-001 real journey;
- audit DB isolation fix;
- related tests;
- ledger/evidence updates;
- Slice 7 design note if appropriate.

Then continue.

I would **not wait for another 100-file slice before committing this**.

---

### 4. Audit rows 480–486: **leave them alone**

Correct decision not to delete them.

An audit trail should not be retroactively cleaned simply because tests accidentally wrote into it.

Instead record an incident such as:

```yaml
incident: V7-D52
affected_rows:
  - 480
  - 481
  - 482
  - 483
  - 484
  - 485
  - 486

classification:
  test-contamination

cause:
  child-process tests inherited production audit location

repair:
  test processes now force data/audit.sqlite3

production_data_deleted:
  false
```

If three are definitively from today's harness execution, annotate that in the incident/evidence register, but preserve them.

That gives us a new important invariant:

> **Tests may read production-like schemas, but automated tests must never write into live user-state stores.**

I would formalize this as `TEST-ISOLATION-001` if an equivalent requirement does not already exist.

---

### 5. Fake-key fixtures before push: **change them rather than relying on bypass**

GitHub push protection can block a push when a string matches a supported secret pattern. GitHub does allow a contributor to mark a detected secret as “used in tests,” but doing so creates a bypass record/alert under repository push protection. :chatgpt-content-reference{index="1"}

For FRIDAY, I’d rather our tests **not resemble valid provider credentials unless the test specifically tests secret detection**.

So replace generic fake fixtures like:

```text
sk-abc...
```

with clearly synthetic values that do not match provider formats, e.g.:

```text
FRIDAY_TEST_SECRET_001
TEST_ONLY_INVALID_API_KEY
```

For tests whose whole purpose is verifying scanners recognize OpenAI-style/provider-style patterns, construct the string dynamically in the test or keep it in an explicitly isolated fixture with a documented push-protection strategy.

Do **not** disable secret scanning or globally bypass push protection.

GitHub scans Git history, not merely the latest file state, so it is better to resolve these before the first push of the branch. :chatgpt-content-reference{index="2"}

---

### 6. Merge: **not yet — finish this tiny follow-up checkpoint first**

I would sequence it:

```text
3d956ba
foundation checkpoint
     ↓
browser + audit-isolation follow-up
     ↓
4,717+ full suite
     ↓
commit
     ↓
working tree clean
     ↓
inspect diff against main
     ↓
merge
```

After that, continue Slice 7 from main/new implementation branch.

This gives you a clean bisect point:

```text
foundation security state
→ browser/runtime correction
→ adaptive hardware governor
```

Much easier to debug later.

---

### 7. Safety-classifier transient failures: **stopping retries was correct**

Treat this as an infrastructure/routing failure, not a product defect.

The durable state should say:

```text
CLASSIFIER_TRANSIENT_FAILURE
```

and the remaining two actions should be resumable from the checkpoint.

Do not repeatedly resend shell commands until the provider terminates the session.

This is actually a useful real-world case for the safeguard-aware router we added:

```text
command
↓
provider safety system unavailable/transient failure
↓
bounded retry
↓
provider/session route unhealthy
↓
persist
↓
fresh session / compatible route
```

not:

```text
retry forever
```

---

## One thing I would change in the current priority

You currently have:

```text
124 requirements
32 VERIFIED
20 unresolved P0
```

At this stage I would **stop adding large new architectural ideas to the implementation branch for a bit**.

Keep the ideas in the PRD/research registry, but drive the build toward:

```text
20 unresolved P0
→ 15
→ 10
→ 5
→ 0
```

before another major architecture expansion.

Your next order after committing the follow-up should be roughly:

```text
1. Finish live-browser runtime readiness
2. Finish adaptive Resource Governor
3. CORE-001
4. ACT-001 / action-truth P0s
5. DOMAIN-001 / safeguard routing
6. MCP-001
7. EVAL-001
8. remaining P0 security/isolation
9. CTX retrieval quality
10. Cyber interface boundary only
```

### CTX search at 75.9%

The ≥80% schema reduction is good.

The **75.9% top-search retrieval is not good enough to call CTX complete**, and `PARTIAL` is the correct status.

I would not immediately throw a bigger LLM at it.

First benchmark:

```text
BM25 / lexical
embedding
hybrid lexical + embedding
metadata filters
small local reranker
R3-style skill/tool reranker
```

Measure:

```text
Recall@1
Recall@3
Recall@5
schema tokens loaded
latency
```

The real target may be:

```text
Recall@1 >= 90%
Recall@3 >= 97%
```

while still maintaining the ≥80% disclosure reduction.

That's a better objective than simply “search accuracy increased.”

---

## Instruction I'd give the builder now

You can paste this directly:

> **Continue from `prd-v7/foundations-checkpoint` without reopening completed work. First complete the pending browser/audit follow-up: verify the live audit state without modifying it, update BROW-001 with the real Playwright journey evidence, replace or isolate provider-shaped fake-key fixtures so the branch can pass GitHub push protection without weakening scanning, install the `[browser]` extra into the live FRIDAY `.venv`, and run a live-runtime Chromium smoke test. Run targeted tests, then the full suite, and create a coherent follow-up commit. Do not delete audit rows 480–486; document V7-D52 as historical test contamination. After that, continue Slice 7. The Resource Governor must self-profile the host and derive policies from measured headroom/pressure/hysteresis rather than hardcoding either reference machine or simply increasing the 75% threshold. Both the 15.4 GB integrated-GPU machine and the 16 GB/4 GB-VRAM machine are mandatory reference targets. If provider safety checks repeatedly fail, persist state and switch/fresh-session rather than repeatedly retrying. Continue dependency-ready P0 work autonomously.**

That is the state I would lock.
````

## Appendix H. The computer-use fabric and the Cua evaluation, 2026-09-30 (owner's text, verbatim)

````text
Yes — **`trycua/cua` is one of the stronger additions we’ve found for FRIDAY**, especially because your goal is not merely browser automation but **real computer control in the background while you keep using the laptop**.

I would not treat the whole Cua repository as one dependency. I’d adopt **three parts selectively**:

1. **Cua Driver → high-priority desktop execution backend**
2. **Cua Bench → computer-use evaluation/trajectory system**
3. **CUA-S1 → research candidate for tiny local UI decisions, not production core yet**

Cua’s current design is almost exactly aligned with our architecture: it treats GUI control as one action surface alongside code, structured tools and APIs rather than forcing every task into screenshot-click automation. Cua Driver works across Windows, macOS and Linux and exposes pixels, accessibility state and GUI actions through CLI/MCP/SDK interfaces. :chatgpt-content-reference{index="0"}

## The biggest reason I want it: background computer control

On Windows, Cua Driver now supports **background computer-use**. It can target Win32, WPF, WinUI, Electron, Chromium and other applications using a combination of UI Automation/accessibility, pixels and targeted input. Its synthetic cursor can remain separate from your physical cursor where the target supports it. :chatgpt-content-reference{index="1"}

That is extremely important for your vision.

Instead of:

```text
FRIDAY starts working
→ steals mouse
→ opens windows in front of you
→ you cannot use laptop
```

we want:

```text
YOU
│
├── physical mouse/keyboard
│
└── keep working normally

FRIDAY
│
├── background cursor/session A → Chrome
├── background cursor/session B → Excel
└── background cursor/session C → another app
```

Current Cua 0.8 work describes synthetic multi-cursor sessions across Windows/macOS/Linux, with each session tied to its own cursor, actions and recording; non-overlapping windows can be worked concurrently without agents fighting over the physical pointer. :chatgpt-content-reference{index="2"}

That is almost exactly a requirement we haven't properly formalized yet.

# Add: `ComputerUseFabric`

I would upgrade our current Browser/Desktop architecture into:

```text
                         FRIDAY
                            │
                    Action Path Router
                            │
        ┌───────────────────┼────────────────────┐
        ▼                   ▼                    ▼
    STRUCTURED             WEB                 DESKTOP
        │                   │                    │
 API / MCP / script    WebMCP / Playwright    Cua Driver
                            │                    │
                            │               accessibility
                            │               background input
                            │               pixels
                            │                    │
        └───────────────────┼────────────────────┘
                            ▼
                       Vision fallback
                            │
                            ▼
                       Coordinates
                       LAST RESORT
```

Cua does **not replace Playwright**.

They solve different problems.

### Browser-only work

Prefer:

```text
WebMCP
→ Playwright/DOM
→ browser structured APIs
```

because they're deterministic and cheap.

### Native apps / browser chrome / dialogs / desktop apps

Use:

```text
Cua Driver
```

Examples:

- DaVinci Resolve;
- Adobe apps;
- Excel;
- file dialogs;
- Windows settings;
- Calculator;
- installed business apps;
- Chrome browser chrome, extensions or native dialogs;
- apps without APIs.

### Weird/custom UI

Then:

```text
Cua pixels/vision
→ coordinate fallback
```

So this substantially improves `BROW-001` rather than replacing your now-working Playwright path.

---

# The feature I particularly want: `BackgroundDesktopSessions`

Add a formal subsystem:

```text
BackgroundDesktopSession
{
    session_id
    objective_id
    worker_id
    assigned_windows[]
    permission_manifest
    cursor
    evidence_stream
    lease
}
```

Then FRIDAY can do:

```text
Objective A:
update spreadsheet
→ Session A
→ Excel window

Objective B:
download report
→ Session B
→ Chrome window
```

while you are still typing to FRIDAY or doing something else.

That's a serious step toward the Iron-Man-style experience you're describing.

---

# But we should NOT give Cua unrestricted host authority

Cua Driver currently has permission modes including `standard`, `bounded`, and an unrestricted mode requiring an explicit dangerous bypass flag. :chatgpt-content-reference{index="3"}

For FRIDAY I would enforce:

```text
DEFAULT:
BOUNDED
```

FRIDAY creates a manifest such as:

```yaml
objective: OBJ-928

apps:
  - chrome
  - excel

windows:
  - approved_window_ids

actions:
  - inspect
  - click
  - type
  - scroll
  - set_value

files:
  allowed:
    - C:\Users\...\Project

clipboard:
  false

expiry:
  30m
```

Then:

```text
FRIDAY policy
      ↓
FRIDAY ExecutionEnvelope
      ↓
Cua bounded manifest
      ↓
Windows
```

Two independent security boundaries.

Do **not** use:

```text
--dangerously-bypass-approvals
```

as normal operation.

---

# Cua also fits our evidence philosophy extremely well

I liked something in their current engineering work a lot.

Their regression harness does **not** trust:

```text
Driver says click succeeded
```

It independently checks whether the application's state actually changed. Their current matrix reportedly includes more than 500 behavioral checks across Windows, macOS and Linux toolkits. :chatgpt-content-reference{index="4"}

That's exactly our existing rule:

> **A tool response isn't proof of success.**

So Cua should integrate with:

```text
Cua Action
    ↓
Reality Ledger
    ↓
Independent post-state
    ↓
Proof-Carrying Action
```

Example:

```text
FRIDAY:
"Set calculator to 42"

Cua:
clicks

Verifier:
reads Calculator display

display = 42

→ VERIFIED
```

Perfect.

---

# Cua Bench: YES, probably even more valuable than it first appears

`Cua Bench` gives us reproducible computer-use tasks and execution-based evaluation. Its basic task pack currently includes things like clicking, form filling, spreadsheet editing, dropdowns, sliders, switches, video controls, right-click menus and drag/drop. :chatgpt-content-reference{index="5"}

Instead of inventing every desktop benchmark ourselves, create:

# `ComputerUseArena`

```text
                   COMPUTER USE ARENA
                           │
       ┌───────────────────┼───────────────────┐
       ▼                   ▼                   ▼
  Playwright path       Cua path          Vision path
       │                   │                   │
       └───────────────────┼───────────────────┘
                           ▼
                       Cua Bench
                           │
                           ▼
                  independent scoring
```

Measure:

```text
success %
latency
tokens
actions
retries
RAM
CPU
foreground disruption
wrong clicks
wrong targets
post-state verification
```

Then `CapabilityPathNet` later learns:

> “For this application/task class, Cua accessibility wins.”

instead of us hardcoding opinions forever.

---

# CUA-S1 is also interesting for our tiny-model plan

This caught my attention because we just designed:

```text
Laya
FunctionGemma
FRIDAY micro models
```

Cua has now started **CUA-S1**, a family of small specialized System-1 models for computer-use decisions. Its initial research profile is deliberately narrow: given structured UI elements and document values, score bounded actions such as fill/check/click/skip; deterministic code orders the actual actions. :chatgpt-content-reference{index="6"}

Conceptually:

```text
Screenshot/accessibility state
           ↓
Cua-S1 / Friday UI Net
           ↓
which field/value/action?
           ↓
deterministic executor
```

This is exactly the **small-specialized-model architecture** we've been converging toward.

But:

### I would NOT ship CUA-S1 yet.

Their current model card explicitly describes the initial component as a **research profile**, not a general computer-use model, and says checkpoint artifacts may have separate licensing. :chatgpt-content-reference{index="7"}

So classify:

```text
CUA-S1

STATUS:
WATCH / RESEARCH / BENCHMARK

NOT:
default production dependency
```

What is more valuable immediately is their **architecture**.

We could eventually train:

# `FridayUI-300M`

from our own verified Cua Bench / FRIDAY trajectories.

Its task:

```text
given:
UI state
+
objective

predict:
target
action class
confidence
```

That could eliminate a lot of Claude/Gemini calls during routine computer operation.

---

# This gives us a beautiful future training loop

Remember our Distillation Factory?

Now:

```text
Frontier model
      ↓
uses Cua Driver
      ↓
verified UI trajectory
      ↓
Cua Bench / FRIDAY evaluator
      ↓
training record
      ↓
FridayUI small model
      ↓
Model Arena
```

Over time:

```text
DAY 1

Claude decides most GUI operations


LATER

FridayUI decides:
80% routine targets locally

Claude only handles:
weird/new/difficult UI
```

This is exactly how FRIDAY should become more local over time.

---

# There are current Cua issues we must not ignore

It's promising, but not magic.

Current upstream issue tracking still shows Windows edge cases, including an `ERROR_INVALID_HANDLE` screenshot path and prior multi-monitor coordinate/input issues. :chatgpt-content-reference{index="8"}

There is also a current Windows Smart App Control / Code Integrity issue reporting unsigned Cua Driver release binaries being blocked on some Windows 11 configurations. :chatgpt-content-reference{index="9"}

That matters a lot for our installer.

So do not do:

```text
install Cua
→ declare DESKTOP_READY
```

Instead:

```text
install
↓
signature/integrity check
↓
launch probe
↓
window enumerate
↓
accessibility read
↓
background click
↓
post-state verification
↓
multi-monitor probe if relevant
↓
DESKTOP_READY
```

If it fails:

```text
CUA_DRIVER_DEGRADED
```

and our path router can fall back.

---

# Very important: code signing

Because you were already considering Azure Artifact Signing for FRIDAY.exe, this overlaps with a real deployment problem.

If we eventually bundle or redistribute a Cua-derived driver on Windows, we need to investigate:

```text
upstream binary signing
vs
build our own pinned source
vs
sign our packaged binary
```

Do not blindly redistribute an unsigned executable and then discover Windows blocks FRIDAY during onboarding.

This should go into our **Capability Supply Chain + Installer qualification**.

---

# Cua Sandbox / Fleet: useful, but not core default

Cua also provides isolated computers/sandboxes/fleets. Their docs recommend isolation for sensitive/untrusted computer-use work. :chatgpt-content-reference{index="10"}

For FRIDAY:

```text
NORMAL PERSONAL TASK
→ Cua Driver on real computer

UNTRUSTED TASK
→ isolated sandbox/VM

TESTING
→ Cua Sandbox / local VM where available

CYBER LAB
→ Kali/container/VM, not personal desktop
```

Because we're local-first, I would **not require Cua Cloud Fleet**.

Expose:

```text
ComputerEnvironmentProvider
├── LocalHostCua
├── LocalSandbox
├── Kali
├── VM
└── CloudFleet   optional
```

---

# We should actually rename one architectural concept

Our current:

```text
Browser/Desktop Hybrid Action Router
```

is becoming too narrow.

I would promote it to:

# `ComputerUseFabric`

And underneath:

```text
ComputerUseFabric

├── WebNative
│   ├── WebMCP
│   └── official browser APIs
│
├── BrowserStructured
│   └── Playwright
│
├── DesktopStructured
│   └── Cua Driver
│
├── ApplicationAdapters
│   ├── DaVinci
│   ├── Blender
│   └── ...
│
├── Vision
│   └── local/cloud visual model
│
└── SandboxProviders
    ├── local sandbox
    ├── Kali
    └── VM
```

That is the better long-term abstraction.

---

# I would add these requirements

### `CUA-001 — Computer Use Backend Abstraction`

FRIDAY SHALL provide a provider-neutral ComputerUseFabric supporting structured browser, desktop, visual and isolated-computer execution backends.

### `CUA-002 — Background Desktop Operation`

Where OS/application support permits, FRIDAY SHALL execute desktop tasks without unnecessarily moving the physical pointer, stealing focus or interrupting the user's foreground session.

### `CUA-003 — Window Ownership`

Each concurrent background computer-use worker SHALL receive an explicit allowed window/application set.

### `CUA-004 — Real-State Verification`

Computer-use actions SHALL not count as successful solely because the automation backend returned success.

### `CUA-005 — Backend Health`

Cua/Playwright/other computer-use backend health SHALL be validated using real action + independently observed post-state.

### `CUA-006 — ComputerUse Arena`

Alternative computer-use paths SHALL be benchmarkable against execution-based tasks for correctness, latency, resource cost and interaction count.

### `CUA-007 — Foreground Preservation`

Background computer use SHALL be tested for unintended focus theft, pointer movement and window-order changes.

### `CUA-008 — Desktop Session Isolation`

Concurrent computer-use sessions SHALL have explicit ownership, leases and resource boundaries.

### `MODEL-011 — Local UI Decision Model`

FRIDAY MAY use/train small specialized UI decision models only after they outperform deterministic/frontier baseline routes on Cua Bench/FRIDAY evaluations.

---

# My adoption decision

| Cua component | Decision |
|---|---|
| **Cua Driver** | **YES — high-priority Desktop backend** |
| Windows background automation | **YES — major FRIDAY capability** |
| Synthetic multi-cursor | **YES — benchmark carefully** |
| Cua MCP | Useful adapter, **but don't make MCP mandatory** |
| Cua SDK/native interface | **Preferred integration candidate** |
| **Cua Bench** | **YES — add to ComputerUseArena** |
| **CUA-S1** | **Research/watch/benchmark; not production yet** |
| Lume | Useful later for macOS/local VM |
| Cua Sandbox | Useful optional isolation backend |
| Cua Cloud Fleet | **Optional only**, not local-first core |
| `unrestricted` permission mode | **No for normal FRIDAY** |
| bounded capability manifests | **Yes** |
| Cua as FRIDAY's entire desktop architecture | **No — backend under ComputerUseFabric** |

The strongest reason to adopt it is **not merely that it can click buttons**.

It's that it can potentially let FRIDAY behave like a genuine second operator on your machine:

> You continue using the laptop while FRIDAY independently operates another permitted application/window in the background, observes the outcome, and proves the action worked.

That's much closer to the product you're actually trying to build.

And because your current branch already has Playwright/browser work passing, I would **not replace that work**. The next implementation step should be a **Cua Driver spike behind the existing action-path abstraction**, run it on your current integrated-GPU Windows laptop, measure RAM/CPU/focus-stealing/action accuracy, and compare it directly with the existing Playwright path before promoting it.
````
