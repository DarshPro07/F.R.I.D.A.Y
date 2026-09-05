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

### Where the suite actually stands

The last full local run is 2026-09-05 (`data/baseline_fix1`, canonical
`scripts/baseline_suite.py`, four chunks, Windows):

```
3,788 passed, 1 skipped, 4 failed  (chunks 0/1/3 exit 0; chunk 2 exit 1)
```

The four failures are the machine entering Modern Standby mid-run
(Kernel-Power 506/507 in the System log during the chunk; `git rev-parse`
"timed out after -668 s"), not the code; they pass in isolation and have
since been made resolver-independent. Regenerate rather than trust this
paragraph, and read the runner's exit codes, not the tail of a pipe.

Remote truth is the GitHub run on the commit you are looking at, never an
older one. The run on `70176b1` (id 33949024905) was RED on both jobs; the
buckets and their root causes are in
`docs/architecture/AUDIT_2026-09-05_TRIAGE.md` (A-016 again). A Linux
compatibility run can be reproduced locally in WSL against a `git archive`
checkout (empty gitlink dirs, like actions/checkout): `D:/wsl/linux_gate.sh`
on this machine.

The prior `27 failed / 7 errors` (2026-08-31) was cleared as follows:
- the 7 errors were one bug — `friday.toolsets.files.ARTIFACTS_DIR` did not
  exist while two test modules and `scripts/golden_live_runtime.py` used it.
  `files_delete` is now implemented in `friday/toolsets/files.py` with the
  Friday-owned-artifact exemption (delete without a nonce) and the
  confirmation-nonce flow for permanent deletion of anything else; it is
  registered as an MCP tool, a `capabilities.CAPABILITIES` entry, a
  `capability_router` group member, and a `policy.py` category.
- `test_phase1f` (9): `spotify.*` transport was ungated (defaulted ASK →
  every call cancelled); mapped to `MEDIA_CONTROL` / `READ_LOCAL_SAFE`.
- `test_response_render` (2): natural TTS rate reset to 1.0
  (`TTS_SPEED`-overridable) and a cross-chunk URL/markdown cleaner added to
  `FridayAgent.tts_node`.
- `test_phase1e` (2) / `test_connector_plane` (1): `mss` and `yaml` were
  missing from `.venv-verify`; copied in from `.venv`.
- `test_continuity_livekit` (1): `LiveKitContinuity` is now wired into the
  live loop (`agent._continuity`, attached in the entrypoint), so a user turn
  is recorded as a durable objective before it is learned from.
- `test_files_recycle` / `test_upstream_lock` were already green.
- `test_phase1b` (2) are `@live` and excluded from this gate.

`test_reachability.py::test_the_number_of_unreachable_things_does_not_grow`
now passes: the wiring above revived two continuity symbols, and the standing
dead code was triaged into `reachability.KNOWN` with an honest verdict each
(`_tool_evidence` DEAD/orphaned; the rest FUTURE — built subsystems not yet on
a live path: history-aware provider fallback, the voice-input mute gate, World
Monitor destination verification, brain ledger replay, runtime narration
arbiter). Do not paper over new ones — wire them or classify them the same way.

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
