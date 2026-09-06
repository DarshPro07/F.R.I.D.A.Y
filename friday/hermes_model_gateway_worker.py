"""
The Hermes MODEL_GATEWAY worker - runs INSIDE the Hermes venv, never Friday's.

PRD v3.1 FR-069/070/071/072/073/079: Friday sends a bounded request
envelope; this process asks Hermes's own provider layer to resolve a
configured provider/model, performs ONE stateless provider transaction, and
returns the text plus usage/route metadata. It never builds an AIAgent,
never loads toolsets, skills, subagents or session history, and never
returns a credential.

Protocol: newline-delimited JSON on stdio, one request per line, one reply
per line, `id` echoed. Methods:

    {"id": 1, "method": "hello"}
        -> {"id": 1, "ok": true, "result": {"hermes_home": ..., "pid": ...}}
    {"id": 2, "method": "providers"}
        -> {"id": 2, "ok": true, "result": {"providers": [...], "main": {...}}}
    {"id": 3, "method": "infer", "params": {ModelGatewayRequest}}
        -> {"id": 3, "ok": true, "result": {ModelGatewayResult}}
    {"id": 4, "method": "shutdown"}

The ModelGatewayRequest params understood here are `messages`, `provider`,
`model`, `max_output_tokens`, `temperature`, `timeout_s`, `reasoning`.
Everything else in the PRD envelope (task_class, budgets, allowlists,
privacy) is Friday's business and is decided BEFORE this process sees the
request - it only ever receives the compiled context package.

Only stdlib is imported at module load, so a broken Hermes install reports a
structured error on the first request instead of failing to start.
"""
from __future__ import annotations

import json
import os
import sys
import time
import traceback


def _reply(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, default=str) + "\n")
    sys.stdout.flush()


def _err(rid, code: str, message: str, **extra) -> None:
    _reply({"id": rid, "ok": False,
            "error": {"code": code, "message": message, **extra}})


ENTITLEMENT_MARKERS = (
    ("model is unavailable", "MODEL_UNAVAILABLE"),
    ("insufficient", "INSUFFICIENT_CREDIT"),
    ("quota", "QUOTA_EXCEEDED"),
    ("rate limit", "RATE_LIMITED"),
    ("rate_limit", "RATE_LIMITED"),
    ("429", "RATE_LIMITED"),
    ("401", "AUTH_FAILED"),
    ("403", "AUTH_FAILED"),
    ("unauthorized", "AUTH_FAILED"),
    ("invalid api key", "AUTH_FAILED"),
    ("invalid x-api-key", "AUTH_FAILED"),
    ("authentication", "AUTH_FAILED"),
    ("not logged in", "AUTH_FAILED"),
    ("subscription", "SUBSCRIPTION_REQUIRED"),
    ("no llm provider", "NOT_CONFIGURED"),
    ("not configured", "NOT_CONFIGURED"),
    ("disabled in config", "PROVIDER_DISABLED"),
)


def classify_failure(text: str) -> str:
    """Map a provider/auth exception message to an entitlement state.

    The mapping is deliberately conservative: an unrecognised failure is
    `PROVIDER_ERROR`, never a guessed entitlement. FR-072 requires the
    truthful category, and "we do not know" is a truthful category.
    """
    low = (text or "").lower()
    for needle, state in ENTITLEMENT_MARKERS:
        if needle in low:
            return state
    return "PROVIDER_ERROR"


def _usage_dict(response) -> dict:
    usage = getattr(response, "usage", None)
    if usage is None and isinstance(response, dict):
        usage = response.get("usage")
    out = {"input_tokens": 0, "output_tokens": 0,
           "cached_tokens": 0, "reasoning_tokens": 0}
    if usage is None:
        return out

    def get(obj, *names):
        for name in names:
            if isinstance(obj, dict):
                if obj.get(name) is not None:
                    return obj[name]
            else:
                val = getattr(obj, name, None)
                if val is not None:
                    return val
        return None

    out["input_tokens"] = int(get(usage, "prompt_tokens", "input_tokens") or 0)
    out["output_tokens"] = int(get(usage, "completion_tokens", "output_tokens") or 0)
    details_in = get(usage, "prompt_tokens_details", "input_tokens_details")
    if details_in is not None:
        out["cached_tokens"] = int(get(details_in, "cached_tokens",
                                       "cache_read_input_tokens") or 0)
    if not out["cached_tokens"]:
        out["cached_tokens"] = int(get(usage, "cache_read_input_tokens",
                                       "cached_tokens") or 0)
    details_out = get(usage, "completion_tokens_details",
                      "output_tokens_details")
    if details_out is not None:
        out["reasoning_tokens"] = int(get(details_out, "reasoning_tokens") or 0)
    if not out["reasoning_tokens"]:
        # Thinking models that report no breakdown still bill the thinking
        # in `total_tokens`: gemini-3.6-flash answered a 9-token prompt with
        # 0 completion tokens and total 22 - the missing 13 were reasoning
        # spent inside max_tokens (2026-09-05). Derive it so the caller can
        # see WHERE an empty answer's budget went.
        total = int(get(usage, "total_tokens") or 0)
        hidden = total - out["input_tokens"] - out["output_tokens"]
        if total and hidden > 0:
            out["reasoning_tokens"] = hidden
    return out


def _default_model_for(provider_id: str) -> str:
    """The provider's OWN default model, or "" when Hermes knows none.

    Two Hermes sources, in order: the curated auxiliary default (what Hermes
    itself would use for a cheap call on this provider) and the provider's
    static catalog default. Never the profile's main model: that belongs to
    the main provider, and sending it to another is how OpenAI was asked
    for `claude-opus-5` (live suite, 2026-09-05).
    """
    pid = (provider_id or "").strip()
    if not pid:
        return ""
    try:
        from agent.auxiliary_client import _get_aux_model_for_provider
        picked = _get_aux_model_for_provider(pid) or ""
    except Exception:  # noqa: BLE001 - a private helper; absence is not an error
        picked = ""
    if not picked:
        try:
            from hermes_cli.models import get_default_model_for_provider
            picked = get_default_model_for_provider(pid) or ""
        except Exception:  # noqa: BLE001
            picked = ""
    return str(picked).strip()


#: Provider ids whose transport is a consumer SUBSCRIPTION or OAuth app
#: rather than a metered API key. Entitlement, not billing: a subscription
#: route can be rate-limited by plan and cannot be topped up with credit.
_SUBSCRIPTION_IDS = frozenset({
    "openai-codex", "xai-oauth", "qwen-oauth", "minimax-oauth",
    "opencode-go", "copilot", "copilot-acp", "kimi-coding",
})

#: Ids that are local ONLY when their endpoint actually is. `custom`
#: ("Custom endpoint") is the dangerous one: its base_url is whatever the
#: user configured, and on this machine it points at https://opencode.ai.
#: Friday's `privacy_policy="local_only"` trusts this label, so a wrong
#: answer here sends private context off the machine while promising it did
#: not - the id must never be the whole test.
_LOCAL_IDS = frozenset({"lmstudio", "ollama", "ollama-local", "custom"})

#: Hosts that are genuinely this machine.
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0", "[::1]"})


def _is_local_endpoint(base_url: str) -> bool:
    """Is this URL served by the machine Friday runs on?

    An empty base_url means the provider never told us where it points. That
    is not evidence of locality, so it is not treated as local: for a privacy
    promise the burden of proof runs the other way.
    """
    url = str(base_url or "").strip()
    if not url:
        return False
    from urllib.parse import urlparse
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    if not host:
        return False
    if host in _LOCAL_HOSTS or host.endswith(".local") or host == "host.docker.internal":
        return True
    # A literal loopback address in any notation (127.0.0.0/8, ::1).
    import ipaddress
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _route_kind_for(provider: dict) -> str:
    """Classify one provider's transport from what it actually is.

    PRD A-010: the transport model (API vs subscription vs OAuth app vs
    local) must be a property of the route, not a guess. The id list decides
    the CANDIDATE kind; the endpoint decides whether a `local` claim stands.
    """
    pid = str(provider.get("id") or "")
    if pid in _SUBSCRIPTION_IDS:
        return "subscription"
    if pid in _LOCAL_IDS:
        # Locality is proven by the endpoint, never by the name. A `custom`
        # provider pointed at a public URL is an API route that happens to be
        # user-configured, and calling it local is what leaks private context.
        return "local" if _is_local_endpoint(provider.get("base_url")) else "api"
    if pid == "opencode-free":
        return "free_tier"
    return "api"


def _providers() -> dict:
    from hermes_cli.models import list_available_providers
    from hermes_cli.config import load_config
    providers = list_available_providers()
    cfg = load_config()
    main = dict(cfg.get("model") or {}) if isinstance(cfg, dict) else {}
    # Never forward keys that might be secrets: only identity fields.
    main = {k: v for k, v in main.items()
            if k in ("default", "provider", "base_url", "api_mode")}
    fallbacks = cfg.get("fallback_providers") if isinstance(cfg, dict) else None
    for p in providers:
        pid = p.get("id", "")
        # PRD A-010: the transport model is a property of the ROUTE, derived
        # from what the provider actually is - see `_route_kind_for`.
        p["route_kind"] = _route_kind_for(p)
        # Requirement 10: every provider carries its own default so Friday
        # never has to guess - and an empty one is a fact Friday can refuse
        # on (NO_ROUTE) rather than a blank Hermes fills with the main model.
        p["default_model"] = _default_model_for(pid)
    return {"providers": providers, "main": main,
            "fallback_providers": list(fallbacks or [])}


def _infer(params: dict) -> dict:
    from agent.auxiliary_client import call_llm, extract_content_or_reasoning

    messages = params.get("messages") or []
    if not isinstance(messages, list) or not messages:
        raise ValueError("infer requires a non-empty messages list")
    provider = (params.get("provider") or "").strip() or None
    model = (params.get("model") or "").strip() or None
    max_tokens = int(params.get("max_output_tokens") or 1024)
    temperature = params.get("temperature")
    timeout = float(params.get("timeout_s") or 60.0)
    reasoning = params.get("reasoning")  # e.g. {"effort": "low"} or None

    route: dict = {}
    latency: dict = {}
    started = time.monotonic()
    kwargs = dict(messages=messages, max_tokens=max_tokens,
                  temperature=temperature, timeout=timeout,
                  route_info=route, latency_info=latency)
    if reasoning:
        kwargs["reasoning_config"] = reasoning
    if provider:
        kwargs["provider"] = provider
        if model:
            kwargs["model"] = model
        response = call_llm(**kwargs)
    else:
        # No explicit provider: Hermes's configured main model for this
        # HERMES_HOME. `task` selects the auxiliary config lane; passing
        # the main runtime keeps it on the profile's primary model.
        response = call_llm(task="title_generation", **kwargs)
    elapsed_ms = int((time.monotonic() - started) * 1000)
    text = extract_content_or_reasoning(response) or ""
    finish = None
    try:
        finish = response.choices[0].finish_reason
    except Exception:  # noqa: BLE001 - shape varies by provider
        finish = None
    return {
        "status": "ok",
        "provider": route.get("provider") or provider or "",
        "model": getattr(response, "model", None) or route.get("model") or model or "",
        "requested_model": model or "",
        "response": text,
        "finish_reason": finish,
        "latency_ms": elapsed_ms,
        "provider_latency": latency,
        "usage": _usage_dict(response),
        "entitlement_state": "OK",
    }


def serve() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError:
            _err(None, "BAD_REQUEST", "not JSON")
            continue
        rid = req.get("id")
        method = req.get("method")
        params = req.get("params") or {}
        try:
            if method == "hello":
                _reply({"id": rid, "ok": True, "result": {
                    "hermes_home": os.environ.get("HERMES_HOME", ""),
                    "pid": os.getpid(),
                    "python": sys.executable,
                }})
            elif method == "providers":
                _reply({"id": rid, "ok": True, "result": _providers()})
            elif method == "infer":
                _reply({"id": rid, "ok": True, "result": _infer(params)})
            elif method == "shutdown":
                _reply({"id": rid, "ok": True, "result": {}})
                return
            else:
                _err(rid, "UNKNOWN_METHOD", f"unknown method {method!r}")
        except Exception as exc:  # noqa: BLE001 - every failure is reported, none escape
            message = f"{type(exc).__name__}: {exc}"
            _err(rid, classify_failure(message), message,
                 traceback=traceback.format_exc()[-2000:])


if __name__ == "__main__":
    # The script's own directory is sys.path[0] when launched by path; if
    # that directory contains packages that shadow the stdlib (Friday's
    # `friday/platform/` does), Hermes's imports break. The worker must see
    # only the Hermes install (cwd) and the venv.
    _here = os.path.dirname(os.path.abspath(__file__))
    sys.path[:] = [p for p in sys.path
                   if os.path.abspath(p or os.getcwd()) != _here]
    # Hermes resolves everything relative to its install root; the launcher
    # sets cwd there, and the root must also be importable (`agent`,
    # `hermes_cli` are top-level packages of the checkout, not the venv).
    _root = os.getcwd()
    if _root not in sys.path:
        sys.path.insert(0, _root)
    serve()
