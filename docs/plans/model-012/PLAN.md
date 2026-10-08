# MODEL-012: a signed-in coding-agent CLI as a model-gateway provider

Status: In progress. Date: 2026-10-04. Service: objective-engine (model gateway).
Owner answer (CTX-002, 2026-10-04): "we will try to use claude code or codex and
antigravity instead of api key".

## Why
On this host the Gemini key is rejected (401) and the OpenAI key is invalid, so
FRIDAY.exe has no working brain. Claude Code is signed in (`claude auth status`
-> loggedIn, `claude -p` answered in 1.1 s for $0.001), so it can be the brain.

## Facts measured on this host (claude 2.1.288)
- `claude -p --output-format json --max-turns 1 --tools "" --strict-mcp-config
  --no-session-persistence --disable-slash-commands --setting-sources ""
  --system-prompt <short>` with the prompt on stdin, cwd = scratch dir, returns
  rc 0 and JSON with `result`, `is_error`, `total_cost_usd`, `usage`, `modelUsage`.
- `--tools ""` is the documented way to disable every built-in tool;
  `--max-turns` works but is hidden from `--help`.
- `claude auth status` prints JSON `{"loggedIn": true, ...}` locally, no model call.
- codex 0.x and antigravity are on PATH; their headless modes are separate work.

## Design
`friday/cli_model_provider.py` is a SECOND gateway transport next to the Hermes
worker (Hermes is external and stays untouched). One interface a backend
implements: `locate`, `signed_in`, `complete`, `classify`. `ClaudeCodeBackend`
first; Codex/Antigravity plug into the same interface.

Gateway seam (`friday/model_gateway.py`), nothing else changes shape:
1. `ModelGateway(cli_backends=...)`; the process-wide `gateway()` discovers them,
   a directly built gateway (every existing test) gets none.
2. `providers()` merges one `route_kind: "cli"` entry per backend, `authenticated`
   = on PATH and signed in. A dead Hermes worker no longer hides the CLI route
   (degraded inventory, cached 60 s so a dead worker is not relaunched per call).
3. `_infer` sends a CLI provider to the backend, not the worker; replies keep the
   worker's `{ok, result|error}` shape so the whole failover loop is reused.
   Absent CLI -> `GatewayUnavailable`; signed-out -> `AUTH_FAILED` (durable).
4. `_raw_candidates` always appends CLI routes LAST, after API routes, whether or
   not the tier table had a route (the old `not out` condition hid them).
   `infer` reserves their slots inside `1 + max_failover`, so API failures cannot
   cut the CLI off.
5. Cost: `cost_usd` on `ModelGatewayResult`, `_record`, `gateway_calls`
   (`_ADDED_COLUMNS` migration), summary.
6. `model_capabilities`: CLI models declared with `tools=False`.
7. Semantic probe = `provider_health.probe` over the CLI route: HEALTHY needs a
   visible non-empty answer.

## Safety
Pure completion: no tools, no MCP, no slash commands, no settings/hooks, a
scratch cwd, a hard timeout, `ANTHROPIC_API_KEY` and session variables scrubbed.
The model name is validated before it reaches argv. The reply is returned as a
string only; no code path executes it. Data-class decision treats `claude-cli` as a
cloud backend (not in `privacy.LOCAL_BACKENDS`).

## Proof (each fails before, passes after)
`tests/test_cli_model_provider.py` with a fake CLI executable: success + cost;
absent; signed-out; timeout; shell-looking output stays inert (no second process);
argv has no tools; gateway test where Gemini 401s and the call lands on the CLI
with failover_count >= 1 and cost recorded; probe HEALTHY / empty-answer
UNAVAILABLE; CLI-only when the worker is dead. Then a real text turn.

## Result (2026-10-04)
- Claude Code backend built and proven: 31 tests in `tests/test_cli_model_provider.py`
  against a fake CLI, plus a real run on this host with no Hermes `friday` profile
  and no API key: `model.infer` -> `claude-cli`/`claude-sonnet-5-5`, "12 times 12
  is 144.", `cost_usd` 0.002336 recorded, probe HEALTHY.
- Turn it on/off: `FRIDAY_CLI_MODEL_PROVIDERS` ("claude,codex" by default since slice 3, "off" to
  disable; the test session sets "off" so no test spends the subscription).

## Slice 1: the gateway as the voice agent's LLM (2026-10-05)
`friday/gateway_llm.py` - `GatewayLLM(livekit.agents.llm.LLM)`; `chat()` returns a stream whose
`_run` flattens the chat context to `[{role, content}]`, calls `gateway().infer` in a thread
(STANDARD class, `allow_failover=True`, `worker="voice-llm"`) and sends ONE assistant chunk
with the usage. Tools are ignored; a refused or failed gateway raises a non-retryable
`APIConnectionError` (the gateway already walked its routes).
- One objective id per call: a shared id would hit the STANDARD objective ceiling (120k tokens)
  a few dozen turns into a voice session and refuse every turn after it.
- Oldest non-system messages are dropped to fit the class input budget (12k tokens; the system
  prompt alone is ~3.7k): a growing voice context would otherwise become unanswerable.
- A tool call and its result become labelled text, so a fallback that inherits a turn whose
  tool already ran can still answer from the result.
`providers.build_resilient_llm`: when `cli_model_provider.discover_backends()` is non-empty,
GatewayLLM is appended last in the FallbackAdapter; with no API key it is returned alone. Only a
missing-credentials `ProviderError` is covered - a bad role or backend still stops startup.
Assumed `discover_backends()` is the "CLI configured" test (no `configured()` exists); it means
enabled, not installed or signed in, so a missing CLI fails at turn time, not at startup.
Proof: `tests/test_gateway_llm.py` (15), incl. the fake-CLI turn with `cost_usd > 0` recorded.

## Slice 3: Codex backend; opencode ruled out (2026-10-08)
Owner ruling 2026-10-05b: use opencode free models, Codex and Claude Code logins when signed in. The
ledger's condition for each: build it only if a no-tools answer-only mode is verified with a fake and one
live call; otherwise record why not.

Measured on this host (codex-cli 0.160.0 "Logged in using ChatGPT"; opencode 1.18.35):
- **opencode: NOT buildable as pure completion.** `opencode run` has no tools-off flag. The free tier answers
  only a default-shaped request: a custom agent with every tool disabled gets HTTP 403 "free tier can only be
  used from within OpenCode". The config-level `tools {"*": false}`, `permission {"*": "deny"}` and
  `permission {"bash": "deny", ...}` (via OPENCODE_CONFIG_CONTENT) were all ignored: the `bash` tool ran
  `echo hi` and completed in each. A backend here would let the model run shell commands. Not built.
- **Codex: buildable.** `codex exec --json --ephemeral --ignore-user-config --ignore-rules
  --skip-git-repo-check -s read-only -m <model> --disable <tool features> -c web_search=disabled -` with the prompt
  on stdin, cwd = scratch dir. With the tool features disabled the input drops 32,574 -> 11,039 tokens and the
  tool-bait prompt ("run `echo hi`") is answered in text; without them the model tried `exec_command` and only
  the read-only sandbox stopped it. Events are JSONL: `item.completed` {agent_message | reasoning | error |
  command_execution | file_change | mcp_tool_call | web_search | todo_list ...}, `turn.completed.usage`,
  `turn.failed.error.message`, `error.message`.

Design: `CodexBackend(CliBackend)` in `friday/cli_model_provider.py`, id `codex-cli`, model `gpt-5.5`
(the model the person's own Codex config uses), in `BACKENDS` and `DEFAULT_ENABLED` after claude. Two layers:
prevention (read-only sandbox, user config/rules/hooks ignored, tool features disabled) and detection (any
event item other than agent_message / reasoning / error makes the call FAIL and the text is discarded, so
a tool use is never mistaken for an answer). The answer is the last `agent_message`. Cost: ChatGPT sign-in
bills no per-call price, so `cost_usd` is 0.0 and tokens are recorded. `CliBackend.prompt()` is added so a backend
that cannot take `--system-prompt` carries the instruction in the prompt. `model_capabilities` declares
`codex-cli/gpt-5.5` with `tools=False`.

Proof: `tests/test_cli_model_provider.py` against `tests/fake_cli_model.py` (a codex dialect): argv has the read-only
sandbox, the disables and `-`; success; tool item -> failure, answer discarded; shell-looking text inert; signed-out;
absent; timeout; sign-in check; the gateway lands on codex when claude is absent. One `live` test each is
skipped without FRIDAY_LIVE_PROVIDER_TESTS=1.

## Out of scope / blockers
- Voice turn: LiveKit AgentSession uses its own Gemini LLM plugin, not
  `model.infer`; it needs a LiveKit LLM adapter and working LiveKit + TTS keys
  (.env holds placeholders). NOT proven; reported as a blocker, not a pass.
- FRIDAY.exe was not rebuilt or driven: the text turn was proven through the same
  `model.infer` tool and process-wide `gateway()` the exe calls.
- Codex: built in slice 3 (above) once the controller upgraded it to 0.160.0. Proven with a fake
  (40 tests in `tests/test_cli_model_provider.py`) and one real call on this host: `CodexBackend().complete`
  answered "144", `gpt-5.5`, 8,545 input tokens, cost 0.0, 16 s, sign-in read by `codex login status` in 2.6 s.
  The `--disable` list is a denylist that can drift as Codex adds tool features; the second layer (a tool
  event fails the call) and the read-only sandbox are what keep it honest.
- opencode: not built, see slice 3. Revisit only if opencode ships a tools-off switch the free tier accepts.
- Antigravity: `antigravity` is the IDE launcher (VS Code fork); its CLI has no
  headless completion command, so there is nothing to put behind `CliBackend`.
