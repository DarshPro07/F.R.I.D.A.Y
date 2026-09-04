"""
friday/fabric_service.py -- the SIDECAR mode gets a runtime.

`bolt.diy`, `onlook`, `open-lovable`, `postiz`, `maxun`, `anythingllm` and
`open-notebook` are web applications. `SIDECAR` named that shape and supplied
nothing to work with: no base-URL discovery, no auth, no timeout, no retry
policy. An adapter author had to write all of it by hand, differently each
time, and the one existing SIDECAR provider did not even own its process.

Four of those seven are copyleft. A supervised HTTP boundary is the compliant
route the licence invariant already assumes exists, so this closes the same G6
hole `fabric_cli` closed for command-line agents.

Two rules here are not configurable, and both are deliberate:

  loopback only    a third-party web app Friday started must not be reachable
                   from the network. 127.0.0.1, never 0.0.0.0.
  POSTs are never  "generate a site" retried on a timeout is two sites. Only
  retried          idempotent GETs get a second attempt.
"""
from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field

from friday import contracts as c
from friday import fabric_process

#: Body encodings an endpoint may declare.
JSON = "JSON"
FORM = "FORM"
NONE = "NONE"

#: What the caller gets back.
EXPECT_JSON = "JSON"
EXPECT_TEXT = "TEXT"
EXPECT_STATUS = "STATUS"

#: Auth styles. The secret is named, never carried.
AUTH_NONE = "NONE"
AUTH_BEARER = "BEARER"
AUTH_HEADER = "HEADER"

#: How long a service stays up after its last request. Twenty-one always-on
#: sidecars on one Windows box is the wreckage `fabric.py` names in its own
#: docstring; idle shutdown is how this mode avoids recreating it.
IDLE_TTL = 600.0

_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")

MAX_OUTPUT = 200_000


class ServiceError(RuntimeError):
    """A service could not be reached or was misdeclared."""


@dataclass(frozen=True)
class Service:
    """How to start and talk to one HTTP sidecar. Declared by an adapter."""

    spec: fabric_process.Spec
    base_url: str = "http://127.0.0.1:{port}"
    health_path: str = "/health"
    timeout: float = 30.0
    #: Idempotent GETs only; see `_should_retry`.
    retries: int = 1
    auth: str = AUTH_NONE
    #: The secret's NAME. The value is resolved at call time and never stored.
    auth_secret: str = ""
    auth_header: str = "Authorization"
    idle_ttl: float = IDLE_TTL
    start_timeout: float = 90.0


@dataclass(frozen=True)
class Endpoint:
    method: str = "GET"
    path: str = "/"
    body: str = NONE
    expect: str = EXPECT_JSON


#: provider_id -> monotonic time of last use, for the idle reaper.
_LAST_USED: dict[str, float] = {}
_LOCK = threading.Lock()
_REAPER: threading.Thread | None = None
_SERVICES: dict[str, Service] = {}


def spec_for(argv, *, cwd=None, env=None, health_path: str = "",
             port_env: str = "PORT", **kw) -> fabric_process.Spec:
    """A Spec for an HTTP sidecar, with the right readiness gate already set.

    Adapters kept having to know that "ready" for a web app means the port
    answers, not that the process exists - and getting that wrong is precisely
    the bug the supervisor was built for. So the service layer chooses: an
    HTTP probe when the adapter names a health path, a plain TCP accept
    otherwise. An adapter may still pass its own `ready` to override.
    """
    ready = kw.pop("ready", None)
    if ready is None:
        ready = (fabric_process.HttpOk(health_path) if health_path
                 else fabric_process.TcpPort())
    return fabric_process.Spec(
        argv=tuple(argv), cwd=cwd, env=dict(env or {}), ready=ready,
        needs_port=True, port_env=port_env, **kw)


def _fill(text: str, arguments: dict) -> str:
    def swap(match):
        key = match.group(1)
        if key not in arguments:
            raise ServiceError(
                f"no argument {key!r} for placeholder in {text!r}; "
                f"given {sorted(arguments)}")
        return str(arguments[key])
    return _PLACEHOLDER.sub(swap, text)


def _base(service: Service, child) -> str:
    """The child's URL, built from the port the SUPERVISOR allocated.

    There is no port field on the descriptor and no port table: one source, so
    the two cannot disagree. That disagreement is the classic sidecar bug -
    the table says 8080, the child took 8081, and the error says 'connection
    refused' rather than 'you have two sources of truth'.
    """
    url = service.base_url.replace("{port}", str(child.port or 0))
    if not url.startswith("http://127.0.0.1") and not url.startswith("http://localhost"):
        raise ServiceError(
            f"refusing a non-loopback base_url {url!r}; a supervised "
            f"third-party service must not be reachable off this machine")
    return url.rstrip("/")


def _reaper_loop() -> None:
    while True:
        time.sleep(30)
        now = time.monotonic()
        for provider_id, last in list(_LAST_USED.items()):
            service = _SERVICES.get(provider_id)
            if service is None:
                continue
            if now - last > service.idle_ttl:
                fabric_process.stop(provider_id)
                _LAST_USED.pop(provider_id, None)


def _ensure_reaper() -> None:
    global _REAPER
    if _REAPER is None or not _REAPER.is_alive():
        _REAPER = threading.Thread(target=_reaper_loop, daemon=True,
                                   name="fabric-service-reaper")
        _REAPER.start()


def ensure_up(provider_id: str, service: Service):
    """Start the sidecar if it is not already serving. Returns the Child."""
    child = fabric_process.child(provider_id)
    if child is not None and child.state == fabric_process.READY and child.alive():
        return child
    child = fabric_process.spawn(provider_id, service.spec,
                                 timeout=service.start_timeout)
    _SERVICES[provider_id] = service
    _ensure_reaper()
    return child


def _headers(service: Service) -> dict:
    """Auth headers, resolved by name at call time.

    The value never appears in `Service`, never in a log line and never in
    ActionResult evidence - see `_evidence`.
    """
    if service.auth == AUTH_NONE or not service.auth_secret:
        return {}
    from friday.secret_broker import SecretBroker
    value = SecretBroker().resolve_for_process(service.auth_secret)
    if not value:
        raise ServiceError(f"the secret {service.auth_secret!r} is not set")
    if service.auth == AUTH_BEARER:
        return {service.auth_header: f"Bearer {value}"}
    return {service.auth_header: value}


def _should_retry(endpoint: Endpoint) -> bool:
    """Only idempotent GETs. A retried POST is two of whatever it made."""
    return endpoint.method.upper() == "GET"


def _evidence(provider_id: str, endpoint: Endpoint, url: str,
              status: int, elapsed: float) -> str:
    return (f"{endpoint.method.upper()} {url} -> {status} in {elapsed:.2f}s "
            f"against supervised child {provider_id}; the status is the claim, "
            f"not a check of what the service did")


def health(provider_id: str, service: Service) -> dict:
    """A real request, not an assumption.

    A service whose process is alive but whose HTTP surface returns 500 is
    DEGRADED, and that distinction is the whole of G8: presence is not
    function.
    """
    from friday import fabric

    child = fabric_process.child(provider_id)
    if child is None:
        return {"state": fabric.REGISTERED, "detail": "not started"}
    if not child.alive():
        return {"state": fabric.UNAVAILABLE,
                "detail": child.last_error or "child is not running"}
    try:
        import httpx
        url = _base(service, child) + service.health_path
        resp = httpx.get(url, timeout=5.0)
    except Exception as exc:  # noqa: BLE001
        return {"state": fabric.UNAVAILABLE, "detail": f"health probe failed: {exc}"}
    if resp.status_code >= 500:
        return {"state": fabric.DEGRADED,
                "detail": f"process is up but {service.health_path} returned "
                          f"{resp.status_code}"}
    if resp.status_code >= 400:
        return {"state": fabric.DEGRADED,
                "detail": f"{service.health_path} returned {resp.status_code}"}
    return {"state": fabric.READY, "detail": f"{service.health_path} answered OK"}


def request(provider, operation: str, service: Service, endpoints: dict, *,
            run_id: str = "", **arguments) -> c.ActionResult:
    """Call one endpoint on a supervised sidecar, honestly enveloped."""
    tool_id = f"fabric.{provider.id}.{operation}"
    result = c.started(run_id or c.new_run_id(), tool_id)

    endpoint = endpoints.get(operation)
    if endpoint is None:
        return c.failed(result, f"{provider.id} has no endpoint for {operation!r}")

    try:
        import httpx
    except ImportError:
        return c.failed(result, "httpx is not installed; the service mode needs it")

    try:
        child = ensure_up(provider.id, service)
    except Exception as exc:  # noqa: BLE001
        return c.failed(result, f"{provider.id} would not start: {exc}")

    try:
        url = _base(service, child) + _fill(endpoint.path, arguments)
        headers = _headers(service)
    except ServiceError as exc:
        return c.failed(result, str(exc))

    attempts = (service.retries + 1) if _should_retry(endpoint) else 1
    started = time.monotonic()
    last_error = ""
    for attempt in range(attempts):
        try:
            if endpoint.method.upper() == "GET":
                resp = httpx.get(url, headers=headers, params=arguments or None,
                                 timeout=service.timeout)
            elif endpoint.body == FORM:
                resp = httpx.post(url, headers=headers, data=arguments,
                                  timeout=service.timeout)
            elif endpoint.body == NONE:
                resp = httpx.post(url, headers=headers, timeout=service.timeout)
            else:
                resp = httpx.post(url, headers=headers, json=arguments,
                                  timeout=service.timeout)
            break
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)[:200]
            if attempt + 1 >= attempts:
                elapsed = time.monotonic() - started
                return c.failed(
                    result,
                    f"{provider.id}.{operation} did not answer after "
                    f"{elapsed:.1f}s: {last_error}")
            time.sleep(0.5)

    elapsed = time.monotonic() - started
    with _LOCK:
        _LAST_USED[provider.id] = time.monotonic()

    if resp.status_code >= 400:
        return c.failed(
            result,
            f"{provider.id}.{operation} returned {resp.status_code}: "
            f"{resp.text[:400]}")

    if endpoint.expect == EXPECT_STATUS:
        value = resp.status_code
    elif endpoint.expect == EXPECT_TEXT:
        value = resp.text[:MAX_OUTPUT]
    else:
        try:
            value = resp.json()
        except Exception as exc:  # noqa: BLE001
            return c.failed(
                result,
                f"{provider.id}.{operation} promised JSON and gave {exc}; "
                f"first 200 chars: {resp.text[:200]}")

    return c.succeeded(
        result,
        verification=c.Verification(
            method="fabric.service",
            evidence=_evidence(provider.id, endpoint, url,
                               resp.status_code, elapsed),
        ),
        output=value,
    )


def shutdown(provider_id: str) -> None:
    fabric_process.stop(provider_id)
    _LAST_USED.pop(provider_id, None)
    _SERVICES.pop(provider_id, None)
