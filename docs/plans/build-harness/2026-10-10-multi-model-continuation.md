# Plan: Claude, Codex and OpenCode together, and continuation on a usage limit

**Date:** 2026-10-10
**Status:** In Progress
**Scope:** build harness (`scripts/`), the interactive session's hooks. No product code.

## Objective

Owner, 2026-10-10:

- "bro the usage is taking too much u use opencode and codex model too try"
- "Bro i want both to run toghter ok caude codex and opencode"
- "Bro i want like i do /models and choose the models and use also ok bro currently if usage limit of claude reach i want it to continue seesion by running other models"

The goal is three routes working side by side. When Claude reports a usage limit, the work moves to
another route without a Claude call, keeps its state and does not repeat finished steps.

## Facts (checked 2026-10-10 on this machine)

- **Claude Code 2.1.295**
  - Hooks have a `StopFailure` event (matcher `rate_limit`). Its input carries `session_id`,
    `transcript_path`, `cwd`, `permission_mode`, `error`, `error_details` and
    `last_assistant_message`. Its output is ignored.
  - stream-json emits `{"type":"rate_limit_event","rate_limit_info":{"status":"rejected","resetsAt":<epoch s>}}`.
    Assistant events carry `error:"rate_limit"`.
  - The message texts are "You've hit your limit · resets …" and "usage limit reached".
- **Codex CLI 0.160.0**
  - Signed in with ChatGPT. Its config default is `gpt-5.5`. `~/.codex/models_cache.json` lists
    the models it can pick.
  - Its limit error is `usage_limit_reached`, with the text "You’ve hit your usage limit" (curly
    apostrophe).
  - Headless runs need stdin closed and `-s danger-full-access`, because the Windows
    workspace-write sandbox fails pytest's temp dirs with WinError 5.
- **OpenCode 1.18.35**
  - The owner's global config starts 13 MCP servers, a plugin and a custom orchestrator agent.
    With all of that, a headless run hung for more than 180 s.
  - Headless runs work with these settings: `OPENCODE_PURE`, `OPENCODE_DISABLE_CLAUDE_CODE`,
    `OPENCODE_DISABLE_EXTERNAL_SKILLS`, `OPENCODE_DISABLE_DEFAULT_PLUGINS`, every MCP server
    disabled in `OPENCODE_CONFIG_CONTENT`, `enabled_providers: ["opencode"]`, `small_model` set to
    the same free model, `--agent build` and `--format json`. That run took 16 s and cost $0.
  - Stored credentials also include Nvidia, OpenAI and Cerebras. The harness never uses them, for
    billing and data reasons.
- **OpenCode Zen data policy** (opencode.ai/docs/zen):
  - Zero-retention, no training: `step-5-preview-free`, `longcat-2.5-preview-free`, `space-bunny-free`.
  - Training on prompts: big-pickle, exo, mimo, ling and muse-spark.
  - "do not submit … confidential data": nemotron.

## Decisions

1. **One seam: `scripts/build_providers.py`.** It holds:
   - the route names: `claude`, `codex:<model>`, `opencode:<model>`
   - `eligible()`, the billing and data-policy guard
   - `classify()`, which reads only the error channels, so model text about limits is never a signal
   - one shared health file, `data/build_harness/provider_health.json`, with `mark`, `status`,
     `available` and `route`
   - `catalogue()`, the model list behind `/models`

   The build kernel, its verifier and the session hook all import this module. No second copy.
2. **OpenCode only on the three zero-retention free models.**
   - Assumption: FRIDAY source is confidential.
   - Models that train on prompts need the owner's explicit approval. Until then they are
     refused at `eligible()`.
   - Codex only on its ChatGPT login (checked before every run). No API key, no paid route,
     no token copied between CLIs.
3. **Interactive session: `scripts/session_handoff.py`.**
   - `models`: the numbered catalogue with health and policy.
   - `use <n|route>`: writes a continuation packet and opens that route's own terminal UI in a
     new window, with the packet as its first prompt. OpenCode's UI has `/models` for further
     switching.
   - `on-limit`: the `StopFailure` hook. It marks `claude` out until the reset, then opens the
     first available route once per session.
   - The packet is built without any model call, from:
     - the transcript: verbatim owner messages, the last compaction summary and the last
       assistant text
     - git state, the build loop's state, and pointers to memory and work_state
   - Secrets are redacted from the packet.
4. **Build loop: lanes and failover in `scripts/build_kernel.py`, plus a worker shim `scripts/build_worker.py`.**
   - Codex and OpenCode runs are written as Claude-compatible stream-json (init, result), so
     `finalize`, `has_result` and the ledger work unchanged.
   - A run whose error channel shows a usage limit is not FAILED. It is marked on the provider,
     re-queued without spending an attempt, and continued on the next route from the same worktree.
   - When every route is out, the job parks with the earliest reset time and nothing retries
     before then.
   - The verifier switches to Codex when Claude is out. If that verifier run changes the
     worktree, its verdict is discarded.
5. **Rollout.**
   - The code lands with the roster unchanged (Claude only), so failover is passive.
   - One non-critical requirement then runs on `codex:` by hand.
   - After that, the roster gains the Codex and OpenCode candidates and the lanes run together.
   - The current harness stays the rollback: remove the prefixed roster ids.

## Tests (real, no mocks of the code under test)

- `tests/test_build_providers.py` checks:
  - classification of each CLI's real error shapes
  - that model text mentioning limits is ignored
  - reset parsing
  - health expiry
  - routing past an exhausted provider
  - eligibility refusals
- `tests/test_session_handoff.py` checks:
  - packet building from a fixture transcript, with redaction
  - the hook marking health and launching once
  - the catalogue
- `tests/test_build_worker.py` and the kernel tests use fake CLIs (small scripts that print each
  provider's real event shapes) to prove:
  - quota leads to a re-queue rather than FAILED
  - all routes out leads to a park and no storm
  - a free route never becomes a paid one
  - a continuation keeps the worktree state
