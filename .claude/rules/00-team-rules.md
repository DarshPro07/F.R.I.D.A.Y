---
description: "Friday team rules, compact. Replaces the generated rules 01-13 (2026-09-04)."
globs: "*"
alwaysApply: true
---
# Team rules (compact)
- Plan first for work crossing 2+ files or services: a plan under `docs/plans/<slug>/` (the software-factory gates) or `.plans/`; single-file fixes skip it.
- One owner per piece of logic. Services: voice-brain, mcp-tools, fabric, objective-engine. Never reach into another service's internals; extend a documented seam.
- Done means: a test that failed before and passes after, the flow verified end to end (Playwright for UI), no debug prints, no secrets, acceptance criteria checked.
- Unknown business rule or identifier you cannot find with two greps → ask (AskUserQuestion); otherwise state the assumption and proceed.
- Verify before reporting: run it, read it, hit it. Surface blockers at once; never invent a workaround.
- No quick fixes: root cause, correct solution, impact known. Never swallow exceptions, hardcode to pass, disable checks, or weaken tests.
- Match the service's existing style, layout, error handling and test layout.
- Security: all input untrusted; secrets only in env or the broker; outbound HTTP has timeouts and netguard; every endpoint enforces the gate; escape on output; log auth failures. Report a vulnerability the moment you see it.
- Models: Haiku for lookups/monitors, Sonnet for code, Opus only for architecture/review; say why when non-default.
