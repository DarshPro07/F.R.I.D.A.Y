# Friday Stark Demo Main — 21-Upstream Universal Capability Fabric

This pack is the execution specification for integrating all 21 researched upstream repositories into the existing Friday × Hermes system without turning Friday into a fragile mega-merge.

## Core target

- Friday/Jarvis = the only normal user-facing manager, partner, policy owner, and durable objective owner.
- Hermes = mandatory serious agentic execution authority.
- Fable 5 Ultra = current deep reasoning runtime through Hermes (detect dynamically; do not hard-code the product to one model).
- Existing GBrain = durable shared knowledge.
- Existing ConnectorControlPlane = user-facing connector/auth control.
- Upstreams = capabilities behind adapters, MCP, Skills, or isolated sidecars.
- Claude Code = code-level validation.
- Claude Chrome/browser tooling = real UI/LiveKit validation when visual state matters.

## Equal treatment means equal engineering rigor, not equal invocation frequency

Every upstream must receive:
audit → pin → license check → install test → security review → adapter/Skill → health → focused tests → MCP checks where relevant → live Friday journey → crash/restart → rollback.

Friday's router then chooses the **minimum sufficient capability** for each task.

## Start
Give Fable/Hermes:
`03_prompts/MASTER_EXECUTION_PROMPT_FABLE5_ULTRA.md`

The master prompt uses progressive disclosure: it does not load all 21 research briefs into one model context.
