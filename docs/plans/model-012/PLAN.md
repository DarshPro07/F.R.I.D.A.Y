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
- Turn it on/off: `FRIDAY_CLI_MODEL_PROVIDERS` ("claude" by default, "off" to
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

## Out of scope / blockers
- Voice turn: LiveKit AgentSession uses its own Gemini LLM plugin, not
  `model.infer`; it needs a LiveKit LLM adapter and working LiveKit + TTS keys
  (.env holds placeholders). NOT proven; reported as a blocker, not a pass.
- FRIDAY.exe was not rebuilt or driven: the text turn was proven through the same
  `model.infer` tool and process-wide `gateway()` the exe calls.
- Codex: signed in (ChatGPT) but `codex` 0.121.0 here is too old for its default
  model ("gpt-5.5 requires a newer version of Codex"), so a no-tools run could not be
  proven. It has no `--tools ""`; it needs `--disable shell_tool` (and friends) and an
  event-stream check that no command ran. Upgrade Codex, then add a `CliBackend`.
- Antigravity: `antigravity` is the IDE launcher (VS Code fork); its CLI has no
  headless completion command, so there is nothing to put behind `CliBackend`.
