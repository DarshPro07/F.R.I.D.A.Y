# 02 — Product

## User problem
The owner hands Friday real work and then hears nothing until (maybe) "done". He cannot tell
whether it is succeeding, which model is doing it, or why a job stalled on a capped provider.
He wants the Claude Code feel: one main chat (Friday) with sub-chats he can see, each one a
specialist doing bounded work, all sharing what Friday knows, and Friday stepping in only
when genuinely stuck or when a step is his (a login, a credential).

## User
The owner, by voice (LiveKit room or control-room mic), often away from the screen.

## Current behaviour
Progress lines exist only on the browser path and only on tool changes; the room hears
completions. Hermes gets development tasks without acceptance criteria. A capped provider is
retried after a backoff, never switched. Roles are prompt flavours in one context; no
specialist keeps its own experience. Identical failures are retried until attempts run out.

## Desired outcome
- Friday narrates milestones as they happen and a short digest every ~3 minutes while work
  is in flight: what got done, which model and why, what is next; a final summary with
  reasoning. Silent when nothing is running. "What's running?" answers any time.
- A capped provider is detected from its own error, cooled down until its reset, the same job
  moves to the next capable model, and Friday says so in one line.
- Every serious task carries goal, acceptance criteria, known facts, constraints, allowed
  paths, verification and a reporting contract all the way to the worker.
- Specialists (research, engineering, QA, review) run as Hermes profiles with private memory
  on the native kanban; Friday's memory stays the shared canon; Claude specialists hold
  bounded engineering assignments with their own project memory.
- The same failure is never retried blind: a fingerprint forces a new hypothesis, a different
  specialist, a smaller task, or an honest block.
- Friday asks the owner only for a human-only step ("this needs you: can you log in?") or
  when stuck.

## Success evidence
Unit tests that fail before / pass after each slice; a live delegation whose progress digest
is heard on both paths and whose completion carries the route reason; a forced 429 that
re-routes to the next model with a spoken line; acceptance text visible in the Hermes
bundle; a kanban task executed by a named specialist profile; a golden journey with an
injected failure, a changed hypothesis, a repair, independent review and Friday's verifier.

## Failure conditions
"Done" without evidence; a fabricated model switch; a digest that repeats itself; a second
objective engine / task board / memory store; a worker approving its own change; retrying an
identical failure without new evidence; asking "shall I?" for anything but a human-only step.

## Non-goals
Claude Agent Teams (optional, capability-checked, off by default); replacing continuous.py
with an LLM loop; giving specialists the owner's whole personal memory; real-money actions.
