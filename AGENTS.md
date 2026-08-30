# Working on Friday

Instructions for an agent working **on** this repository. Friday's own
runtime persona lives in `agent_friday.py`'s `SYSTEM_PROMPT` and is a
different thing from this file.

Written 2026-08-29 to close the one finding in
`docs/architecture/HARNESS_AUDIT.md`. Keep it accurate; a stale AGENTS.md is
worse than none.

**This file is read at runtime.** `friday/memory_stack.py` treats every line
here starting with `- ` or `* ` (longer than 12 characters) as a rule and feeds
it to Friday as tier 3 of her memory. So a bullet is a rule, and prose is not.
Do not convert rules to prose (she stops seeing them) and do not bullet a list
of ordinary nouns (it becomes noise in her context). Verify with
`python -c "from friday import memory_stack as m; print(m.rules()['total'])"`.

## What Friday is

Two processes plus a manager/executor split:

- **MCP server** (`server.py`, `uv run friday`) — a FastMCP server on
  `:8000/sse` exposing 164 tools from `friday/tools/` and `friday/toolsets/`.
- **Voice agent** (`agent_friday.py`, `uv run friday_voice`) — a LiveKit
  `AgentSession` (Sarvam STT → Gemini LLM → OpenAI TTS) that connects to the
  MCP server as a tool source. `start.py` runs both.
- **Friday is the manager**; **Hermes** (`friday/hermes_bridge.py`, external
  gateway under `D:\hermes`) is the execution engine for serious coding work.
  Do not replace either (NON_NEGOTIABLES 1, 2).

## Repository layout

```
friday/                 the package
  tools/                MCP tool wrappers (@mcp.tool), thin
  toolsets/             the implementations the wrappers call
  fabric.py             the Capability Fabric: external-provider registry
  fabric_adapters/      one module per external upstream (DESCRIPTOR + start/stop/health/call)
  capability_router.py  active-tool gating: CORE_TOOLS + enable-able GROUPS
  policy.py             per-call authorization (PolicyEngine)
  confirmation.py       binds a confirmation to one exact action
  fsjail.py sandbox.py netguard.py sensitive_domains.py   the trust boundaries
  evaluation.py honesty.py   completion gates
  planner*.py objectives.py continuity.py   the durable-objective engine
  executors/            coding-agent backends (claude_code, worktrees, brokers)
  store.py              SQLite durable state
server.py agent_friday.py main.py start.py   entry points
tests/                  156 test_*.py
scripts/                golden_*.py oracles, verify_*.py, restart_friday.py
third_party/upstream/   pinned clones, audit evidence only (gitlinked, not product source)
docs/integrations/      the upstream integration matrix, lock, license audit (generated)
Friday Stark Demo Main/ the governance build pack (NON_NEGOTIABLES, briefs, sequence)
```

## Tool permissions — allowed, restricted, not-allowed

- **Allowed without asking**: read-only and reversible capabilities. The
  `PolicyEngine` (`friday/policy.py`) decides per call.
- **Restricted**: anything a capability marks `requires_edge` (touches the
  user's device) or a fabric provider marks `risk="restricted"` with a
  `permissions` list (e.g. `security_skills` needs `authorized_scope`).
- **Not allowed / confirm first**: destructive or irreversible actions go
  through `friday/confirmation.py`, which binds the confirmation to the exact
  action. Banking/authenticated-financial content is blocked before capture
  (`friday/sensitive_domains.py`, NON_NEGOTIABLE 5). Secrets never enter model
  context, logs, or memory (NON_NEGOTIABLE 4).

## Verification gates

Run before claiming anything works. Use the isolated verify venv, never the
live `.venv` (the live agent runs from it):

```bash
# fabric + integration suite
.venv-verify/Scripts/python.exe -m pytest tests/ -m "not live and not slow" -q

# the running MCP server matches the tree, and every tool is reachable
.venv/Scripts/python.exe scripts/restart_friday.py --check
.venv/Scripts/python.exe scripts/verify_mcp.py

# the upstream lock has not drifted from the clones
.venv/Scripts/python.exe scripts/upstream_lock.py --check
```

A known pre-existing failure, unrelated to current work:
`test_reachability.py::test_the_number_of_unreachable_things_does_not_grow`
(12 dead symbols) and `test_phase0.py::test_env_example_documents_only_live_variables`
(the public `.env.example` documents Supabase/Deepgram vars `config.py` calls
DEAD). Do not count these as regressions; do not paper over new ones.

## Adding an external upstream

The fabric is the one registry — do not build a second. Write a module in
`friday/fabric_adapters/` exposing `DESCRIPTOR: fabric.Provider` and
`start/stop/health/call`. `Provider.__post_init__` enforces two rules at
import: copyleft (AGPL/GPL) may only use isolated modes (MCP/SIDECAR/SKILL/
REFERENCE_ONLY), and any named upstream must be pinned to a commit. Read the
clone's LICENSE before writing the descriptor, pin the exact SHA, regenerate
the lock (`scripts/upstream_lock.py`), and add adapter tests. An absent
upstream must report `UNAVAILABLE`, never break boot (NON_NEGOTIABLE 15).

## The rules that are not negotiable

`Friday Stark Demo Main/00_governance/NON_NEGOTIABLES.md` is authoritative.
The load-bearing ones: Friday stays the single control layer; Hermes stays
mandatory for serious execution; every upstream is untrusted until pinned;
no plaintext secrets anywhere a model can read; optional-provider failure
never crashes the parent objective; avoid duplicate browsers, memories,
orchestrators and model loops.

## Operational notes (this machine)

- The live agent commits to git on its own and leaves stale
  `.git/index.lock` files; clear a zero-byte lock if no `git.exe` is running.
- Run the agent in `start` (production) mode, not `dev` — dev mode's
  `watchfiles` overflows the Windows 32767-char env-var limit on a large
  change set and crash-loops.
- Never touch `data/ada.sqlite3` (the live database) directly.
