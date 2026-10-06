# MODEL-012 slice: tool calls through the model gateway

Date: 2026-10-06. Status: In progress. Services: voice-brain, mcp-tools (voice_brain tool seam), model gateway.

## Why

Owner ruling 2026-10-06 ("test all capabilities from end to end"): without a working
`GOOGLE_API_KEY` the control room's typed brain (`friday/voice_brain.py::_cli_reply`)
has no tools. It answers and recalls but cannot act, and models narrate actions they
never took (live: GLM-5.2 via openrouter said "Writing it to my workspace now, sir -
PAPAYA-77 stored." with no tool call).

## Facts (STATICALLY_CONFIRMED, not re-derived)

- Hermes 3729602 `agent.auxiliary_client.call_llm(..., tools=None, ...)` accepts tools.
- `hermes_model_gateway_worker._accepted()` passes an optional kwarg only when `call_llm`
  takes it, and reports the dropped names in `unsupported_arguments`.
- The Gemini turn in `voice_brain.reply` already runs one generic function,
  `use_capability(family, operation, arguments)`, through `_run_capability`, loops at
  most `_MAX_TOOL_ROUNDS`, and gates the answer with `_honest_about_hermes` and
  `_honest_about_acting`.
- `claude -p` runs with no tools; the signed-in CLI routes are always appended last by
  `_cli_candidates`.

## Design

1. `ModelGatewayRequest.tools` (OpenAI function schemas) and `.no_tools_note`;
   `ModelGatewayResult.tool_calls` (id, name, JSON-string arguments) and `.tools_carried`.
2. Worker `_infer` takes `tools`, hands them to `call_llm` when it accepts them, and returns
   `message.tool_calls`. A reply with tool calls and no text is a success, not EMPTY_RESPONSE.
3. Gateway: a route carries tools unless it is a CLI route or its model declares
   `tools=False` (`model_capabilities`). Routes that cannot carry them are ordered after
   those that can. A tool-less route gets earlier tool turns flattened to text and the
   request's `no_tools_note` as a system message, so it answers without pretending to act.
4. `voice_brain._cli_reply` becomes the gateway turn: the same `use_capability` schema in
   OpenAI form (one description, one owner), tool results appended as `role: tool`
   messages, the same round cap, one final tools-withheld turn, and both honesty gates fed
   with what actually ran.

## Tests (each fails before the change)

The gateway, worker and typed-turn (`voice_brain._cli_reply`) tests all live in
`tests/test_gateway_tools.py`, not `tests/test_model_gateway.py`:

- `tests/test_gateway_tools.py`: the worker hands `tools` to `call_llm` and returns the calls;
  empty text + tool calls is ok; a Hermes that takes no `tools` is reported not carried; tools
  reach the worker only for routes that carry them; a tool-less route (a CLI route, or the final
  tools-withheld turn where `request.tools=()`) flattens the tool turns to text and gets the note;
  `_cli_reply` runs `_run_capability` once and the reply reflects it; a claim with no call is
  still refused; a CLI-only route still answers without tools; the round cap ends with one
  tools-withheld turn.
- `tests/test_gateway_llm.py`: the GatewayLLM adapter yields one assistant chunk and never a tool
  call; the typed turn falls back to the CLI route when the Gemini key is a template.
- `tests/live/test_live_providers.py`: one `live` test puts `tools` through the real Hermes and
  asserts the tool call comes back - skipped unless `FRIDAY_LIVE_PROVIDER_TESTS=1`, so not run here.

## Slice-2 patch

`data/build_runs/MODEL-012.slice2-unintegrated.patch` (durable 401 verdicts, CLI cost caps) is
not present in this worktree - neither the patch nor its evidence JSON exists under `data/` - so
it could not be read, let alone applied. This slice does not depend on it; the missing patch is
recorded as an evidence gap, not worked around.
