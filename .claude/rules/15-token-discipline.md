---
description: "Token discipline for every dispatched agent: fewest agents, cheapest capable model, capped frontmatter, decision-bearing prompts, tight read/report budgets."
globs: "*"
alwaysApply: true
---

# Rule 15: Token Discipline

**When this applies:** every agent dispatched for Friday work, by the human
orchestrator and by `friday-orch`.

Owner's instruction: "they are taking too much token — optimise them so they
do the same task with less token." Sources: the Claude Code subagent and cost
documentation (code.claude.com/docs/en/sub-agents, /costs), the agents-team
rules 06/07/13 and rubric, and what got expensive on 2026-09-03.

## Why this matters

A subagent starts fresh (it does not see the parent's conversation) but it
DOES load CLAUDE.md files, git status, preloaded skills, and the sibling
agent roster on every turn. Combined agent descriptions above 15,000 tokens
trigger a startup warning — keep descriptions short, move detail into the
body (the body loads only when that agent runs). Dispatching agents as
`general-purpose` with every tool inherited (the full tool schema list, every
deferred MCP tool name, all installed agent descriptions) is the expensive
default; a scoped `friday-*` agent with capped frontmatter is the cheap path.

## Rules

1. **Fewest agents.** One builder per disjoint scope; no reader/explorer
   agents; no forks. Follow-ups go to the same agent via `SendMessage` so its
   cache stays warm — never open a new dispatch for a question the last
   agent could answer.
2. **Cheapest capable model.** Haiku for monitors, test runs, log summaries;
   Sonnet for builders; Opus only for architecture-grade decisions, review,
   and the independent verifier.
3. **Capped frontmatter on every `friday-*` agent:** `model`, a minimal
   `tools` allowlist, `disallowedTools: mcp__*`, `maxTurns` (monitor 5,
   engineer 25, qa 30, security 20, tech-lead 15, orch 40), `effort` (monitor
   low, engineers medium, reviewers/orch high), `experimental.cacheTtl: 1h`
   on the builder roles, and a `description` under 30 words.
4. **Decision-bearing prompts.** The dispatch prompt carries the decision,
   not the question: file:line ranges, facts already marked
   `STATICALLY_CONFIRMED` that the agent must not re-derive, the exact test
   files, the design choice, and the report cap. `friday-orch` refuses a
   prompt that is missing any of these.
5. **Read budget.** <= 8 files and <= 400 lines before the first edit;
   skeleton views (`grep -nE '^(def |class )'`) before full ranges; never
   `cat` a file over 200 lines.
6. **Quiet tools.** `pytest -q --tb=line -p no:cacheprovider 2>&1 | tail -15`;
   `git diff --stat`; `head -c`; directory listings at depth <= 2; no
   screenshots unless the task is visual; no `TodoWrite` churn; never
   re-read a file immediately after editing it.
7. **Batch independent commands** in one tool call; one `Edit` per hunk.
8. **One report per agent**, <= 250 words, file:line evidence, written into
   the plan doc.
9. **Roster hygiene.** Install only the agents a repo needs (the VoltAgent
   global install is selective, not the full pack); keep every description
   short; the rest of a pack stays reachable through Friday's
   `roles/search` at runtime, or via the pack's own `install-agents.sh`.
10. **Optional, not enabled:** a `PreToolUse` Bash hook that rewrites
    `pytest` invocations to print only failures and the summary line, as
    documented on the Claude Code costs page.

## Dispatch recipe for the orchestrator

```
Agent(subagent_type="friday-<scope>-engineer", model omitted (frontmatter wins),
      prompt = <scope files> + <facts labelled STATICALLY_CONFIRMED> + <design decision>
               + <exact test files> + <real-test rule> + <report cap 250 words>)
```

Verifier: `friday-tech-lead` (Opus, read-only, `maxTurns` 15). Gate runs:
`friday-monitor` (Haiku, background) over the four pytest chunks, reporting
counts only.
