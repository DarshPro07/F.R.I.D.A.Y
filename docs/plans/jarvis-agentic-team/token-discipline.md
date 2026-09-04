# Token discipline for subagents (2026-09-03)

Owner's instruction: "they are taking too much token — optimise them so they do the
same task with less token". Applies to every agent dispatched for Friday work, by
the orchestrator (Fable) and by `friday-orch`. Sources: the Claude Code subagent
and cost documentation (code.claude.com/docs/en/sub-agents, /costs), the
agents-team rules 06/07/13 and rubric, and what the four 2026-09-03 builders paid for.

## What the four builders paid for (why it was expensive)
- They were dispatched as `general-purpose` with every tool inherited: the full
  tool schema list, ~300 deferred MCP tool names, the descriptions of all 236
  user-level agents, the global and project CLAUDE.md, git status, and hook output.
  That is the fixed context of every one of their turns.
- No `maxTurns`, no `effort` cap, session-level extended thinking inherited.
- Each read tests and modules with ranges, but verification runs (`pytest`) return
  full output into their context every time.

## Facts from the documentation (verified 2026-09-03)
- A subagent starts fresh: it does NOT see the parent's conversation, but it DOES
  load CLAUDE.md files, git status, preloaded skills and the sibling agent roster.
- Combined agent descriptions above 15,000 tokens trigger a startup warning; the
  advice is "keep descriptions short, move detail into the body" (the body loads
  only when that agent runs).
- Frontmatter levers: `model` (haiku/sonnet/opus/fable/inherit), `effort`
  (low/medium/high/xhigh/max, overrides the session), `tools` allowlist and
  `disallowedTools` denylist (patterns `mcp__*` remove every MCP tool),
  `maxTurns` (v2.1.246+, output marked partial, resumable), `skills` (preload),
  `memory`, `background`, `isolation: worktree`, `experimental.cacheTtl: 1h`.
- Explore/Plan built-ins skip CLAUDE.md and git status to stay cheap; forks inherit
  the whole parent context (never fork from a large session).
- Costs: Haiku for simple subagent tasks; thinking tokens are billed as output and
  the default budget can be tens of thousands per request, so lower `effort` for
  mechanical work; MCP definitions are deferred but names still load; hooks can
  pre-filter verbose output before Claude sees it; specific prompts prevent broad
  scanning; agent teams in plan mode cost ~7x a normal session.

## Rules (enforced through `.claude/rules/15-token-discipline.md` and the agent bodies)
1. Fewest agents: one builder per disjoint scope; no reader/explorer agents; no forks;
   follow-ups go to the same agent via SendMessage (its cache stays warm).
2. Cheapest capable model: Haiku for monitors, test runs, log summaries; Sonnet for
   builders; Opus only for architecture-grade fixes, review and the independent verifier.
3. Frontmatter on every `friday-*` agent: `model`, minimal `tools`, `disallowedTools: mcp__*`,
   `maxTurns` (monitor 5, engineer 25, qa 30, security 20, tech-lead 15, orch 40),
   `effort` (monitor low, engineers medium, reviewers/orch high), `experimental.cacheTtl: 1h`,
   `description` under 30 words.
4. The dispatch prompt carries the decision, not the question: file:line ranges,
   STATICALLY_CONFIRMED facts the agent must not re-derive, the exact test files,
   the design choice, the report cap. `friday-orch` refuses a prompt missing any of these.
5. Read budget: ≤ 8 files and ≤ 400 lines before the first edit; skeleton views
   (`grep -nE '^(def |class )'`) before ranges; never `cat` a file over 200 lines.
6. Quiet tools: `pytest -q --tb=line -p no:cacheprovider 2>&1 | tail -15`;
   `git diff --stat`; `head -c`; directory listings at depth ≤ 2; no screenshots
   unless the task is visual; no TodoWrite churn; never re-read a file after editing it.
7. Batch independent commands in one Bash call; one Edit per hunk.
8. One report per agent, ≤ 250 words, file:line evidence, written into the plan doc.
9. Roster hygiene: install only the agents a repo needs (the VoltAgent global
   install is selective: ~70 of 158); keep every description short; the rest of a
   pack stays reachable through Friday's `roles/search` at runtime.
10. Optional (not enabled): a PreToolUse Bash hook that rewrites `pytest` commands
    to print only failures and the summary line, as documented on the costs page.

## Dispatch recipe for the orchestrator (Fable)
```
Agent(subagent_type="friday-<scope>-engineer", model omitted (frontmatter wins),
      prompt = <scope files> + <facts labelled STATICALLY_CONFIRMED> + <design decision>
               + <exact test files> + <real-test rule> + <report cap 250 words>)
```
Verifier: `friday-tech-lead` (Opus, read-only, `maxTurns` 15). Gate runs: `friday-monitor`
(Haiku, background) with the four pytest chunks, reporting counts only.
