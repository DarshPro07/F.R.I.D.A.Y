"""
The gateway-backed Completer for the open-set profiler (MODEL-004).

`friday.model_profiling.profile_route` probes a route through a `Completer`,
and until now only mock routes existed. `gateway_completer` is the real one: it
sends each `ProbeRequest` down the model gateway's one-route seam
(`ModelGateway._infer`), which pins a single (provider, model) with no
fallback to another route - a probe that silently landed on a different model
would profile the wrong thing.

Mapping, request side: one user message; tool specs as OpenAI-style
`{"type": "function", "function": spec}`; images as `image_url` data-URI parts
(assumption: the worker takes that shape for vision-capable routes - a route
that does not will refuse or fail the probe, which is the measurement);
`response_schema` is appended to the prompt as text because the worker has no
structured-output parameter.

Reply side: `GatewayUnavailable` (no worker, governor refusal) and any gateway
error other than BAD_REQUEST are `RouteUnreachable` - they say nothing about
the model. BAD_REQUEST is `ProbeRefused`: the route rejected this request shape.
Tool-call arguments arrive as a JSON string; an invalid or non-object value
becomes `{}` (so the probe's nonce check fails honestly) and a nameless call
is dropped.
"""
from __future__ import annotations

import base64
import json

from friday import model_gateway as MG
from friday import model_profiling as MP

_REFUSED_CODE = "BAD_REQUEST"


def _content(request: MP.ProbeRequest) -> str | list[dict]:
    text = request.prompt
    if request.response_schema is not None:
        text += "\n\nReply with JSON matching this schema: " + json.dumps(request.response_schema)
    if not request.images:
        return text
    return [{"type": "text", "text": text}] + [
        {"type": "image_url",
         "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode("ascii")}}
        for png in request.images]


def _tool_calls(raw) -> tuple[MP.ToolCall, ...]:
    calls = []
    for item in raw or ():
        name = item.get("name") if isinstance(item, dict) else None
        if not name:
            continue
        try:
            arguments = json.loads(item.get("arguments") or "{}")
        except (TypeError, ValueError):
            arguments = {}
        calls.append(MP.ToolCall(name, arguments if isinstance(arguments, dict) else {}))
    return tuple(calls)


def gateway_completer(gw, provider: str, model: str, *, timeout_s: float = 30.0,
                      max_output_tokens: int = 256) -> MP.Completer:
    """A `Completer` that asks `model` on `provider` through `gw` and nothing else."""
    def complete(request: MP.ProbeRequest) -> MP.ProbeReply:
        params = {
            "messages": [{"role": "user", "content": _content(request)}],
            "provider": provider, "model": model,
            "max_output_tokens": max_output_tokens,
            "timeout_s": timeout_s,
        }
        if request.tools:
            params["tools"] = [{"type": "function", "function": spec} for spec in request.tools]
        try:
            reply = gw._infer(provider, params, timeout=timeout_s)
        except MG.GatewayUnavailable as exc:
            raise MP.RouteUnreachable(str(exc)) from exc
        if not reply.get("ok"):
            error = reply.get("error") or {}
            message = str(error.get("message") or error.get("code") or "route error")
            if error.get("code") == _REFUSED_CODE:
                raise MP.ProbeRefused(message)
            raise MP.RouteUnreachable(message)
        result = reply.get("result") or {}
        return MP.ProbeReply(text=result.get("response") or "",
                             tool_calls=_tool_calls(result.get("tool_calls")))
    return complete
