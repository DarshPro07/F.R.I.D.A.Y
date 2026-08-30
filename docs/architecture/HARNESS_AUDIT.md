# Friday harness audit

Scored against `awesome-harness-engineering` (CC0-1.0, pinned
`6a146704c167`), using its `templates/HARNESS_CHECKLIST.md`. This is the
`REFERENCE_ONLY` use the audit assigned it: a checklist, not an install.

Each item is PASS / GAP / N/A with evidence from the tree. A GAP is a real
finding; everything else is cited so the claim can be checked.

## Agent instructions (AGENTS.md)

| Item | Verdict | Evidence |
|------|---------|----------|
| Project overview accurate | **GAP** | There is no `AGENTS.md` or `CLAUDE.md` at the repo root. Friday's own behaviour is specified in `agent_friday.py`'s `SYSTEM_PROMPT`, but there is no instruction file for a coding agent working *on* Friday. |
| Repo structure documented | GAP | Same gap. The structure is legible from `friday/` but not written down for an agent. |
| Tool permissions explicit | PASS (elsewhere) | Not in an AGENTS.md, but enforced in code: `friday/policy.py` (`PolicyEngine`), capability `requires_edge`/`permissions`, and the fabric's `risk`/`permissions` fields. |
| Verification gates defined | PASS (elsewhere) | `pyproject.toml` `[tool.pytest.ini_options]`, `scripts/verify_all.py`, `scripts/verify_mcp.py`. Not surfaced in an agent file. |
| No ambiguous instructions | N/A | No agent file to be ambiguous. |

**The one actionable finding of this audit: Friday has no `AGENTS.md`.** An
agent asked to work on Friday has to reverse-engineer the tool-permission
model, the fabric contract and the verification commands from source. All of
those facts exist and are stable; they are just not collected. This is exactly
the "exists because the model can't do something yet" case the checklist's
last section is about — and it is cheap to close.

## Tool design

| Item | Verdict | Evidence |
|------|---------|----------|
| Clear tool names | PASS | 163 capabilities named by verb_noun (`get_world_news`, `hermes_delegate`, `objective_status`). `friday/capabilities.py`. |
| Minimal schemas | PASS | Capability descriptors carry `input_schema`; the CBM/graft adapters pass only allow-listed args (`OPERATIONS` tables). |
| Errors say what to do next | PASS | e.g. `scrapling_parse` on a missing `html`: "Fetch with Friday's own web_fetch so egress stays inside netguard." Fabric `FabricError`s name the fix. |
| Consistent return shape | PASS | `friday/contracts.py` `ActionResult` — same envelope on success and failure. |
| One tool, one thing | PASS | Enforced socially by the capability/group split; `friday/capability_router.py` asserts every tool is core xor grouped. |

## Context delivery

| Item | Verdict | Evidence |
|------|---------|----------|
| Scoped to the task | PASS | The capability router keeps ~24 core tools active and gates the rest behind `search_capabilities`/`use_capability` (`friday/capability_router.py`). The skill packs return names first, one file on demand — never the whole pack. |
| Long-lived state in files | PASS | `friday/store.py` (SQLite), `data/`, objective/run records — not the prompt. |
| Compaction strategy | PASS | `friday/continuity.py`, `skill_ladder.write_recovery_packet`; the harness itself summarises context across windows. |
| No secrets in context | PASS | `friday/browser_capability.py` `redact_secrets()`, `friday/sensitive_domains.py`, NON_NEGOTIABLE 4. The secret broker resolves names, never values. |

## Planning artifacts

| Item | Verdict | Evidence |
|------|---------|----------|
| PLAN for non-trivial tasks | PASS | `friday/planner.py`, `friday/planner_model.py`, objective admission. |
| Milestones have verify commands | PASS | `friday/evaluation.py` records per-attempt pass/fail; `scripts/golden_*.py` are the milestone gates. |
| Scope boundaries written | PARTIAL | Per-objective scope is modelled (`friday/contracts.py`), but there is no repo-level in/out-of-scope doc — related to the AGENTS.md gap. |
| Decisions captured as they happen | PASS | `project_record_decision`, `friday/runcontext.py`, and this docs/ tree (the integration matrix records every reversal with evidence). |

## Permissions & sandbox

| Item | Verdict | Evidence |
|------|---------|----------|
| Minimum permissions | PASS | `friday/policy.py` per-call authorization; capabilities declare `requires_edge`. |
| Destructive ops confirmed | PASS | `friday/confirmation.py` binds a confirmation to the *exact* action ("a confirmation scoped to X cannot authorise Y"); `friday/reversible.py`. |
| Network scoped | PASS | `friday/netguard.py`, `friday/sensitive_domains.py` (banking/auth domains blocked before capture). The Scrapling adapter is parse-only precisely to keep egress inside netguard. |
| Filesystem scoped | PASS | `friday/fsjail.py` (jailed root, reparse-point defences — `tests/test_fsjail_reparse.py`), `friday/sandbox.py`. |

## Verification loop

| Item | Verdict | Evidence |
|------|---------|----------|
| Tests exist for outputs | PASS | 148 `tests/test_*.py`; the fabric alone has ~230 tests. |
| Agent can run verification | PASS | `scripts/verify_all.py`, `scripts/verify_mcp.py`, `scripts/restart_friday.py --check` — all runnable by the agent. |
| Runs on completion | PASS | `friday/evaluation.py` + `friday/honesty.py` gate completion; the Gate discipline records evidence before "done". |
| Eval criteria written first | PASS | `scripts/golden_*.py` are written as the acceptance oracle, not after. |

## Summary

Friday scores strongly on every category the checklist tests **except its
first**: the harness properties are real and enforced in code, but they are
not collected into an `AGENTS.md` for an agent working on the project. That is
the single concrete deliverable this audit surfaces.

### When each harness component should be removed

| Component | Exists because | Can be removed when |
|-----------|----------------|---------------------|
| capability router (active-24 + groups) | a model degrades with 163 tools in context | models reliably select from 163+ tools without accuracy loss |
| `confirmation.py` action binding | a model can re-target a confirmation onto the wrong irreversible action | a model provably never carries a "yes" across to a different action |
| `honesty.py` / `evaluation.py` gates | a model claims done without evidence | a model's self-report of completion is trustworthy unaided |
| skill-pack name-first catalogues | a model can't hold 818 procedures in context | context windows and retrieval make lazy loading unnecessary |

*Reviewed: 2026-08-29 · against awesome-harness-engineering `6a146704c167`*
