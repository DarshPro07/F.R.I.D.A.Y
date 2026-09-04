# 02 — Product

## User problem
The owner speaks to Friday all day and expects Jarvis: answer directly, act on a
vague instruction without bouncing a question back, hand real work to Hermes on
the right model at the right depth, and remember afterwards what was done, by
Friday and by every sub-agent. He also needs one master prompt to test all of it,
and he wants the people who work on Friday (the Claude Code team) and the roles
Friday can play (Hermes side) to come from the same two packs.

## User
One person: the owner (solo founder), by voice, often while doing something else.

## Current behaviour
The 2026-09-02 transcript failures are fixed and live. What still fails him:
1. Hermes finishes a job; the next turn and the next Hermes bundle have no memory of it.
2. The control room shows "340 tasks open · 1.71M tokens" as if it were the live
   objective's cost; it is every run since August.
3. One objective portion can spend seven times its token budget before anything notices.
4. The two agent packs he pointed at are in neither his Claude Code roster nor Friday's organisation.
5. Work on Friday itself runs in one context at a time; no model-routed team.

## Desired outcome
- "What did Hermes do?" is answerable from shared memory; the next delegation knows the last result.
- The header tells the truth about the current objective's tasks and tokens.
- A portion stops at its budget and checkpoints; the run degrades honestly.
- Friday can play VoltAgent and agents-team roles (people ops, QA, security, ...) through the `roles` family, invisibly.
- The owner's Claude Code has both packs installed, and this repo has a linted team:
  orchestrator on Opus, specialists on Sonnet, monitor on Haiku, Fable orchestrating.
- One master prompt covers every spoken phase and every automated gate.

## Success evidence
Unit tests that fail before and pass after each fix; the Playwright suite green
on the changed tree; a Claude-in-Chrome pass through the real brain on a
face-bypass instance; `lint.py` grades on every generated agent; the master
prompt runnable by the owner on both paths.

## Failure conditions
Any "done" without a log line; a fabricated product, order or result; a test
weakened to pass; the live DB touched; a hook or agent that silently changes
session behaviour without being listed in the report.

## User journeys
1. Delegate → completion spoken → "what did you just finish?" answered from memory.
2. Speak an objective → a portion hits its budget → Friday says so and checkpoints.
3. "Act as our head of people and draft the onboarding plan" → one playbook read, answer in role.
4. Working on the repo: the orchestrator agent routes a change to a Sonnet
   specialist, the Haiku monitor runs the gate, an Opus reviewer signs off.

## Non-goals
Real-money commerce writes; removing OpenAI TTS; a second orchestrator (crewai
and openhands stay REFERENCE_ONLY); restarting the owner's live processes
without his go; external CLI worker teams (no tmux here).
