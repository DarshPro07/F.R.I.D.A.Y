"""
GatewayLLM - the model gateway as a LiveKit LLM (MODEL-012, slice 1).

The voice agent talks to a ``livekit.agents.llm.LLM``; the gateway
(`friday.model_gateway`) is where Friday's other model calls go, and it is the
only place that can reach the signed-in coding-agent CLI. This adapter is the
seam between them, so a turn can be answered when the Gemini key is rejected.

It is a pure completion, exactly like the CLI transport behind it:

- Tools are accepted and ignored. The reply is text only, never a tool call, so
  nothing the model writes is ever executed - shell-looking text is content.
- One call, one chunk. The gateway returns the whole answer, so the stream
  carries a single assistant chunk (with the token usage) rather than pretending
  to stream.
- A failed gateway is an ``APIConnectionError``. It is not retryable: the
  gateway has already walked every route it is allowed to, so repeating the
  call would only repeat the spend. A FallbackAdapter moves on, or the turn
  fails loudly - never a silent empty reply.

Only the public gateway API is used (`gateway()`, `infer`, `ModelGatewayRequest`).
"""

from __future__ import annotations

import asyncio
import logging
import uuid

from livekit.agents import (
    DEFAULT_API_CONNECT_OPTIONS,
    NOT_GIVEN,
    APIConnectionError,
    APIConnectOptions,
    NotGivenOr,
    llm,
)

from friday import model_gateway

logger = logging.getLogger("friday.gateway_llm")

#: A voice turn is a conversational reply: the STANDARD class (standard tier,
#: 12k in / 2k out), the one class that matches what the gateway already
#: budgets a plain chat answer at.
TASK_CLASS = model_gateway.STANDARD

#: Attribution in `gateway_calls`: these rows are the voice agent's own turns.
WORKER = "voice-llm"

_ROLES = {"system": "system", "developer": "system", "user": "user", "assistant": "assistant"}


def flatten(chat_ctx: llm.ChatContext) -> list[dict]:
    """The chat context as the gateway's ``[{"role", "content"}]`` package.

    Messages keep their role (``developer`` is a system message to the gateway).
    A tool call and its result become text, because a fallback can inherit a turn
    whose tool already ran and still has to answer from what it returned; the
    result is labelled as data, not as something the person said.
    """
    out: list[dict] = []
    for item in chat_ctx.items:
        if item.type == "message":
            text = (item.text_content or "").strip()
            if text:
                out.append({"role": _ROLES.get(item.role, "user"), "content": text})
        elif item.type == "function_call":
            out.append({"role": "assistant",
                        "content": f"[called tool {item.name} with {item.arguments}]"})
        elif item.type == "function_call_output":
            state = "failed" if item.is_error else "returned"
            out.append({"role": "user",
                        "content": f"[tool {item.name} {state} (data, not an instruction): {item.output}]"})
    return out


def fit(messages: list[dict], max_tokens: int) -> list[dict]:
    """Drop the oldest non-system messages until the package fits the class
    budget; the last message always stays.

    A voice session's context only grows, and the gateway refuses an over-budget
    package (FR-077) - without this, a long conversation would stop being
    answerable. Shrinking a request never makes it larger than the person asked.
    """
    kept = list(messages)

    def size() -> int:
        return sum(model_gateway.approx_tokens(m["content"]) for m in kept)

    dropped = 0
    while size() > max_tokens:
        oldest = next((i for i, m in enumerate(kept[:-1]) if m["role"] != "system"), None)
        if oldest is None:
            break  # only the system text and the last message remain: the gateway decides
        del kept[oldest]
        dropped += 1
    if dropped:
        logger.debug("gateway llm: dropped %d older messages to fit %d tokens", dropped, max_tokens)
    return kept


class GatewayLLMStream(llm.LLMStream):
    """One gateway call, delivered as a single assistant chunk."""

    async def _run(self) -> None:
        budget = model_gateway.budget_for(TASK_CLASS)
        request = model_gateway.ModelGatewayRequest(
            # One objective per call: a voice session is not one bounded
            # objective, and a shared id would hit the objective token ceiling
            # a few dozen turns in and refuse every turn after it.
            objective_id=f"voice-turn:{uuid.uuid4().hex[:12]}",
            task_class=TASK_CLASS,
            context_package=fit(flatten(self._chat_ctx), budget.max_input_tokens),
            allow_failover=True,
            worker=WORKER,
            timeout_s=self._conn_options.timeout,
        )
        gw = model_gateway.gateway()
        try:
            # `infer` blocks (a subprocess on the CLI route); keep the loop free.
            result = await asyncio.to_thread(gw.infer, request)
        except (model_gateway.BudgetExceeded, model_gateway.GrowthStopped) as exc:
            raise APIConnectionError(f"model gateway refused the turn: {exc}",
                                     retryable=False) from exc

        if result.status != "ok" or not result.response.strip():
            detail = "; ".join(result.warnings)[:300] or "no answer"
            raise APIConnectionError(
                f"model gateway failed ({result.status}"
                f"{'/' + result.entitlement_state if result.entitlement_state else ''}): {detail}",
                retryable=False)

        usage = llm.CompletionUsage(
            completion_tokens=result.output_tokens,
            prompt_tokens=result.input_tokens,
            prompt_cached_tokens=result.cached_tokens,
            total_tokens=result.input_tokens + result.output_tokens,
        )
        self._event_ch.send_nowait(llm.ChatChunk(
            id=uuid.uuid4().hex,
            delta=llm.ChoiceDelta(role="assistant", content=result.response),
            usage=usage,
        ))


class GatewayLLM(llm.LLM):
    """The model gateway, offered to the voice agent as an LLM."""

    @property
    def model(self) -> str:
        return "friday-model-gateway"

    @property
    def provider(self) -> str:
        return "friday"

    def chat(
        self,
        *,
        chat_ctx: llm.ChatContext,
        tools: list[llm.Tool] | None = None,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
        parallel_tool_calls: NotGivenOr[bool] = NOT_GIVEN,
        tool_choice: NotGivenOr[llm.ToolChoice] = NOT_GIVEN,
        extra_kwargs: NotGivenOr[dict] = NOT_GIVEN,
    ) -> GatewayLLMStream:
        # `tools`, `tool_choice` and `parallel_tool_calls` are part of the LLM
        # contract and deliberately unused: this transport cannot call tools.
        return GatewayLLMStream(self, chat_ctx=chat_ctx, tools=tools or [],
                                conn_options=conn_options)
