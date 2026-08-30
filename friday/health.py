"""
Health / status report.

    python -m friday.health          human-readable
    python -m friday.health --json   machine-readable

Answers "why won't it start?" without starting anything expensive: no models
are loaded, no plugins are imported, no LLM is contacted.

Credential VALUES are never read, printed or returned. Only whether a name is
set, and which names are missing.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
from importlib.metadata import PackageNotFoundError, version
from urllib.parse import urlparse

from friday import capabilities, config, providers


def _package_version(name: str) -> str:
    try:
        return version(name)
    except PackageNotFoundError:
        return "not installed"


def _mcp_reachable(url: str, timeout: float = 2.0) -> dict:
    """TCP-connect to the MCP host. Deliberately not an SSE GET, which hangs."""
    parsed = urlparse(url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return {"reachable": True, "host": host, "port": port}
    except OSError as exc:
        return {"reachable": False, "host": host, "port": port, "error": str(exc)}


def serving(url: str = "http://127.0.0.1:8000", *, timeout: float = 1.0) -> bool:
    """
    Is something *answering HTTP* there, rather than merely accepting TCP?

    A listening port is not a running server, and the difference cost a whole
    live gate: after a previous run's server was killed, a connect succeeded
    against a socket nobody was serving, the gate concluded the server was up,
    and every agent session started with "no MCP tools found". The model then
    looped on search_capabilities until the LLM gave out, and the run reported
    thirteen capabilities as unreachable that are all perfectly reachable.

    Any HTTP status will do - a 404 from the ASGI app proves as much as a 200,
    and asks nothing of the routes. Deliberately not GET /sse, which opens a
    stream and hangs by design.
    """
    parsed = urlparse(url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            sock.sendall(f"GET / HTTP/1.1\r\nHost: {host}\r\n"
                         f"Connection: close\r\n\r\n".encode())
            return sock.recv(16).startswith(b"HTTP/")
    except OSError:
        return False


def wait_until_serving(url: str = "http://127.0.0.1:8000", *,
                       deadline_seconds: float = 45.0) -> bool:
    import time

    deadline = time.monotonic() + deadline_seconds
    while time.monotonic() < deadline:
        if serving(url):
            return True
        time.sleep(0.25)
    return False


def report() -> dict:
    stt = os.getenv("STT_PROVIDER", providers.DEFAULT_STT)
    backend = os.getenv("LLM_BACKEND", providers.DEFAULT_LLM_BACKEND)
    role = os.getenv("LLM_ROLE", providers.DEFAULT_ROLE)
    tts = os.getenv("TTS_PROVIDER", providers.DEFAULT_TTS)
    mcp_url = os.getenv("MCP_URL", "http://127.0.0.1:8000").rstrip("/")

    try:
        model = providers.resolve_llm_model(backend, role)
        model_error = None
    except providers.ProviderError as exc:
        model, model_error = None, str(exc)

    def credentials(kind: str, provider: str) -> dict:
        try:
            missing = providers.missing_credentials(kind, provider)
        except providers.ProviderError as exc:
            return {"provider": provider, "error": str(exc)}
        return {
            "provider": provider,
            "configured": not missing,
            "missing": list(missing),  # names only, never values
        }

    problems: list[str] = []
    if model_error:
        problems.append(model_error)
    for kind, provider in (("stt", stt), ("llm", backend), ("tts", tts)):
        info = credentials(kind, provider)
        if info.get("error"):
            problems.append(info["error"])
        elif info["missing"]:
            problems.append(f"{kind} '{provider}' missing: {', '.join(info['missing'])}")

    mcp = _mcp_reachable(mcp_url)
    if not mcp["reachable"]:
        problems.append(f"MCP server unreachable at {mcp_url}")

    dead = config.dead_but_present()
    if dead:
        problems.append(f"dead config still set: {', '.join(dead)}")

    return {
        "versions": {
            "python": sys.version.split()[0],
            "livekit-agents": _package_version("livekit-agents"),
            "livekit-plugins-google": _package_version("livekit-plugins-google"),
            "mcp": _package_version("mcp"),
        },
        "execution_environment": {
            "scope": "agent_runtime",
            "platform": sys.platform,
            "describes": "the machine running this process, not necessarily the user's PC",
        },
        "stt": credentials("stt", stt),
        "llm": {
            **credentials("llm", backend),
            "backend": backend,
            "role": role,
            "model": model,
            "error": model_error,
        },
        "tts": credentials("tts", tts),
        "mcp": {"url": f"{mcp_url}/sse", **mcp},
        "capabilities": {
            "declared": len(capabilities.CAPABILITIES),
            "requiring_edge": [c.id for c in capabilities.requiring_edge()],
        },
        "config": {
            "dead_but_present": list(dead),
            "counts": {
                c: len(config.by_classification(c))
                for c in (config.USED, config.RESERVED, config.DEAD)
            },
        },
        "healthy": not problems,
        "problems": problems,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    data = report()
    if args.json:
        print(json.dumps(data, indent=2))
        return 0 if data["healthy"] else 1

    print(f"FRIDAY health: {'OK' if data['healthy'] else 'PROBLEMS'}")
    print(f"  livekit-agents : {data['versions']['livekit-agents']}")
    print(f"  STT            : {data['stt']['provider']} "
          f"({'configured' if data['stt'].get('configured') else 'NOT configured'})")
    print(f"  LLM            : {data['llm']['backend']} / {data['llm']['role']} "
          f"-> {data['llm']['model']}")
    print(f"  TTS            : {data['tts']['provider']} "
          f"({'configured' if data['tts'].get('configured') else 'NOT configured'})")
    print(f"  MCP            : {data['mcp']['url']} "
          f"({'reachable' if data['mcp']['reachable'] else 'UNREACHABLE'})")
    print(f"  capabilities   : {data['capabilities']['declared']} declared, "
          f"{len(data['capabilities']['requiring_edge'])} require edge")
    for problem in data["problems"]:
        print(f"  ! {problem}")
    return 0 if data["healthy"] else 1


if __name__ == "__main__":
    sys.exit(main())
