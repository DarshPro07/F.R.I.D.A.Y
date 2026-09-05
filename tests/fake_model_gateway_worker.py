"""
A fake MODEL_GATEWAY worker, for testing the gateway without Hermes.

Speaks the same protocol as `friday/hermes_model_gateway_worker.py`.
Behaviour is scripted through env vars:

    FAKE_GW_FAIL_PROVIDERS="anthropic:QUOTA_EXCEEDED,opencode-free:MODEL_UNAVAILABLE"
        infer on these providers answers with that error code
    FAKE_GW_HANG=1        never answer infer (watchdog tests)
    FAKE_GW_DIE=1         exit abruptly on the first infer (crash tests)
    FAKE_GW_ECHO=1        response is the last user message reversed
"""
import json
import os
import sys
import time


def write(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


PROVIDERS = [
    {"id": "anthropic", "label": "Anthropic", "aliases": [], "authenticated": True,
     "route_kind": "api"},
    {"id": "openai-codex", "label": "ChatGPT or Codex Subscription", "aliases": [],
     "authenticated": True, "route_kind": "subscription"},
    {"id": "opencode-free", "label": "OpenCode Free", "aliases": [],
     "authenticated": True, "route_kind": "free_tier"},
    {"id": "lmstudio", "label": "LM Studio", "aliases": [], "authenticated": False,
     "route_kind": "local"},
]


def main():
    failures = {}
    for item in filter(None, os.getenv("FAKE_GW_FAIL_PROVIDERS", "").split(",")):
        prov, _, code = item.partition(":")
        failures[prov.strip()] = code.strip() or "PROVIDER_ERROR"
    hang = os.getenv("FAKE_GW_HANG") == "1"
    die = os.getenv("FAKE_GW_DIE") == "1"
    echo = os.getenv("FAKE_GW_ECHO") == "1"
    calls = 0
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        req = json.loads(line)
        rid, method, params = req.get("id"), req.get("method"), req.get("params") or {}
        if method == "hello":
            write({"id": rid, "ok": True, "result": {"hermes_home": "FAKE", "pid": os.getpid(),
                                                       "python": sys.executable}})
        elif method == "providers":
            write({"id": rid, "ok": True, "result": {
                "providers": PROVIDERS,
                "main": {"default": "fake-main", "provider": "anthropic"},
                "fallback_providers": []}})
        elif method == "infer":
            calls += 1
            if die:
                sys.exit(3)
            if hang:
                time.sleep(3600)
            provider = params.get("provider") or "anthropic"
            if provider in failures:
                write({"id": rid, "ok": False, "error": {
                    "code": failures[provider],
                    "message": f"fake failure on {provider}"}})
                continue
            msgs = params.get("messages") or []
            last = str(msgs[-1].get("content", "")) if msgs else ""
            text = last[::-1] if echo else "PONG"
            prompt_tokens = sum((len(str(m.get("content", ""))) + 3) // 4 for m in msgs)
            write({"id": rid, "ok": True, "result": {
                "status": "ok", "provider": provider,
                "model": params.get("model") or "fake-main",
                "requested_model": params.get("model") or "",
                "response": text, "finish_reason": "stop", "latency_ms": 7,
                "provider_latency": {}, "entitlement_state": "OK",
                "usage": {"input_tokens": prompt_tokens, "output_tokens": 2,
                          "cached_tokens": 0, "reasoning_tokens": 0}}})
        elif method == "shutdown":
            write({"id": rid, "ok": True, "result": {}})
            return
        else:
            write({"id": rid, "ok": False, "error": {"code": "UNKNOWN_METHOD",
                                                       "message": method}})


if __name__ == "__main__":
    main()
