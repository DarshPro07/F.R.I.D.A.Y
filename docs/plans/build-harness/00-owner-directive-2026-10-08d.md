# Owner directive 2026-10-08 (d): ultracode in FRIDAY, and fewer tokens

Given in the session on 2026-10-08, verbatim, in order.

> use haiku and and older models too if needed ok bro ig u are using too much tokens

> Bro i just meant reduce the token bro u justed stopped it bro optimes and efficently the below thing was
> greate bro fast bro

> Bro the thing u stopped below continue it and u also build ok i want the ultracode feature in my friday too
> something like that continue

> use opus too but one nned ok

## How the controller records them

- The model rule for FRIDAY's own multi-agent work: the cheapest capable model per agent. Economy tier (Haiku
  class or older) for lookups, checks and skeptics. Standard tier (Sonnet class) for code and producers. Deep
  tier (Opus class) only where it is needed: a judge, synthesis or architecture stage, and at most one deep
  agent per run.
- Ultracode, as in Claude Code: an opt-in mode in which a substantive task runs as a deterministic multi-agent
  workflow. Recorded as amendment A17 (docs/PRD_V7.md section 111, rows ULTRA-001..007). The design was taken
  from a seams map and two independent proposals, which converged. It is built on the objective engine,
  Hermes delegation, the model gateway, adversarial.py and the budget gates. It is not a second orchestrator.
  It is distinct from A9 (Workflow Fabric), which runs repeated automations on external engines.
- Assumed (the owner may re-rank): P1, behind the running P0 burn-down; off by default; the owner turns it on.
