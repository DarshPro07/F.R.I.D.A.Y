"""
Gateway-backed Completer (MODEL-004): the adapter that lets the open-set
profiler (`friday.model_profiling`) probe a real route through the model
gateway's one-route `_infer` seam, with a scripted gateway standing in for it.

One test per mapping: request shape out, reply shape back, and each failure
class (unavailable / refused / other error) onto the profiler's exceptions.
"""
from __future__ import annotations

import base64
import json
import re

import pytest

from friday import gateway_completer as GC
from friday import model_capabilities as MC
from friday import model_gateway as MG
from friday import model_profiling as MP
from friday import route_benchmarks as RB

BACKEND, MODEL = "scripted", "scripted-1"
_NONCE = re.compile(r"\b[0-9a-f]{8}\b")


class ScriptedGateway:
    """Records each `_infer` call and answers with the next scripted reply (or raises it)."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def _infer(self, provider, params, *, timeout):
        self.calls.append((provider, params, timeout))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


@pytest.fixture(autouse=True)
def store(monkeypatch, tmp_path):
    monkeypatch.setenv("FRIDAY_MODEL_PROFILES", str(tmp_path / "model_profiles.json"))
    monkeypatch.setattr(RB, "_STORE", RB.BenchmarkStore())


def _ok(response="", tool_calls=None):
    result = {"response": response}
    if tool_calls is not None:
        result["tool_calls"] = tool_calls
    return {"ok": True, "result": result}


def _complete(gw, request, **kw):
    return GC.gateway_completer(gw, "prov", "m1", **kw)(request)


def test_a_text_probe_is_one_user_message_pinned_to_the_route():
    gw = ScriptedGateway(_ok("abc12345"))
    reply = _complete(gw, MP.ProbeRequest(prompt="say abc12345"),
                      timeout_s=12.0, max_output_tokens=64)

    assert reply == MP.ProbeReply(text="abc12345")
    (provider, params, timeout), = gw.calls
    assert (provider, timeout) == ("prov", 12.0)
    assert params["messages"] == [{"role": "user", "content": "say abc12345"}]
    assert (params["provider"], params["model"]) == ("prov", "m1")
    assert (params["max_output_tokens"], params["timeout_s"]) == (64, 12.0)
    assert "tools" not in params


def test_tool_specs_are_offered_as_function_tools():
    gw = ScriptedGateway(_ok())
    spec = {"name": "probe_echo", "description": "echo", "parameters": {"type": "object"}}
    _complete(gw, MP.ProbeRequest(prompt="p", tools=(spec,)))
    assert gw.calls[0][1]["tools"] == [{"type": "function", "function": spec}]


def test_images_ride_as_data_uri_image_parts():
    png = MP.solid_png((255, 0, 0))
    gw = ScriptedGateway(_ok("red"))
    _complete(gw, MP.ProbeRequest(prompt="colour?", images=(png, png)))

    content = gw.calls[0][1]["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "colour?"}
    images = content[1:]
    assert len(images) == 2
    assert all(p["type"] == "image_url"
               and p["image_url"]["url"] == "data:image/png;base64," + base64.b64encode(png).decode()
               for p in images)


def test_a_response_schema_is_appended_to_the_prompt_text():
    gw = ScriptedGateway(_ok("{}"))
    schema = {"type": "object", "properties": {"answer": {"type": "string"}}}
    _complete(gw, MP.ProbeRequest(prompt="give json", response_schema=schema))

    params = gw.calls[0][1]
    content = params["messages"][0]["content"]
    assert content.startswith("give json")
    assert json.dumps(schema) in content
    assert "response_schema" not in params


def test_tool_calls_are_parsed_from_their_json_arguments():
    gw = ScriptedGateway(_ok("", [
        {"id": "1", "name": "probe_echo", "arguments": '{"value": "abc12345"}'},
        {"id": "2", "name": "bad_json", "arguments": "{not json"},
        {"id": "3", "name": "not_a_dict", "arguments": "[1, 2]"},
        {"id": "4", "name": "", "arguments": "{}"},
    ]))
    reply = _complete(gw, MP.ProbeRequest(prompt="p"))

    assert reply.tool_calls == (
        MP.ToolCall("probe_echo", {"value": "abc12345"}),
        MP.ToolCall("bad_json", {}),
        MP.ToolCall("not_a_dict", {}),
    )


def test_a_reply_without_tool_calls_or_response_is_empty_not_an_error():
    assert _complete(ScriptedGateway(_ok()), MP.ProbeRequest(prompt="p")) == MP.ProbeReply()
    assert _complete(ScriptedGateway({"ok": True, "result": {}}),
                     MP.ProbeRequest(prompt="p")) == MP.ProbeReply()


def test_an_unavailable_gateway_is_an_unreachable_route():
    gw = ScriptedGateway(MG.GatewayUnavailable("worker did not start"))
    with pytest.raises(MP.RouteUnreachable, match="worker did not start"):
        _complete(gw, MP.ProbeRequest(prompt="p"))


def test_a_bad_request_error_is_a_refusal():
    gw = ScriptedGateway({"ok": False, "error": {"code": "BAD_REQUEST", "message": "no tools"}})
    with pytest.raises(MP.ProbeRefused, match="no tools"):
        _complete(gw, MP.ProbeRequest(prompt="p"))


@pytest.mark.parametrize("code", ["UNKNOWN_METHOD", "AUTH", "RATE_LIMIT", ""])
def test_any_other_error_is_an_unreachable_route(code):
    gw = ScriptedGateway({"ok": False, "error": {"code": code, "message": "boom"}})
    with pytest.raises(MP.RouteUnreachable, match="boom"):
        _complete(gw, MP.ProbeRequest(prompt="p"))


class CapableGateway:
    """Answers the way a model that can do everything would, reading the wire shapes."""

    def _infer(self, provider, params, *, timeout):
        content = params["messages"][0]["content"]
        if isinstance(content, list):                       # vision probe
            raw = base64.b64decode(content[1]["image_url"]["url"].split(",", 1)[1])
            for colour, rgb in MP.PROBE_COLOURS.items():
                if raw == MP.solid_png(rgb):
                    return _ok(colour)
            return _ok("unknown")
        nonce = _NONCE.search(content).group()
        if "tools" in params:
            name = params["tools"][0]["function"]["name"]
            return _ok("", [{"id": "c", "name": name, "arguments": json.dumps({"value": nonce})}])
        if '"answer"' in content and "properties" in content:
            return _ok(json.dumps({"answer": nonce}))
        return _ok(nonce)


def test_a_route_reached_only_through_the_gateway_seam_is_profiled_and_stored():
    """The acceptance: an undeclared route is onboarded through the adapter, no core rewrite."""
    with pytest.raises(MC.CapabilityError):
        MC.get(BACKEND, MODEL)

    profile = MP.profile_route(BACKEND, MODEL,
                               GC.gateway_completer(CapableGateway(), "prov", MODEL))

    assert profile.status == "profiled"
    caps = MC.get(BACKEND, MODEL)
    assert (caps.tools, caps.vision, caps.structured_output) == (True, True, True)
